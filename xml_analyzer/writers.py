from contextlib import contextmanager
import csv
from datetime import date, datetime, timezone
import os
from pathlib import Path
import re
import tempfile
from xml.sax.saxutils import escape
import zipfile

from .models import AppError, MAX_CELL_UNITS, MAX_EXPORT_ROWS, Task

XML_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


@contextmanager
def atomic_target(target):
    target = Path(target)
    temporary = tempfile.NamedTemporaryFile(prefix=".xml-analyzer-", suffix=".part", dir=target.parent, delete=False)
    temporary.close()
    try:
        yield Path(temporary.name)
        os.replace(temporary.name, target)
    finally:
        Path(temporary.name).unlink(missing_ok=True)


def column_letter(index):
    value = ""
    while index:
        index, digit = divmod(index - 1, 26)
        value = chr(65 + digit) + value
    return value


def excel_string(value):
    if len(value.encode("utf-16-le")) // 2 > MAX_CELL_UNITS:
        raise AppError("CELL_LIMIT: 1セル32767文字を超えました。")
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value):
        raise AppError("CELL_CONTROL: XMLで許可されない制御文字が含まれています。")
    value = re.sub(r"_x[0-9a-fA-F]{4}_", lambda m: "_x005F_" + m[0][1:], value)
    return escape(value).replace("\r", "&#13;")


def cell_xml(value, position, header=False):
    style = 1 if header else 0
    if value is None:
        return f'<c r="{position}" s="{style}"/>'
    if isinstance(value, bool):
        return f'<c r="{position}" s="{style}" t="b"><v>{int(value)}</v></c>'
    if isinstance(value, (date, datetime)):
        moment = value if isinstance(value, datetime) else datetime.combine(value, datetime.min.time())
        if moment.tzinfo:
            moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
        serial = (moment - datetime(1899, 12, 30)).total_seconds() / 86400
        return f'<c r="{position}" s="2"><v>{serial:.12g}</v></c>'
    if isinstance(value, (int, float)):
        return f'<c r="{position}" s="{style}"><v>{value}</v></c>'
    return f'<c r="{position}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{excel_string(str(value))}</t></is></c>'


def write_xlsx(target, rows, template, task=None):
    template.validate()
    task = task or Task()
    total = 0
    with atomic_target(target) as output, tempfile.TemporaryDirectory(prefix="xml-xlsx-", dir=Path(target).parent) as temporary:
        sheets, active, names = [], {}, set()

        def create(key, headers):
            if not headers or len(headers) > 16384:
                raise AppError("COLUMN_LIMIT: Excelの列数上限を超えました。")
            if len(sheets) >= 512:
                raise AppError("SHEET_LIMIT: シート数の安全上限512に達しました。")
            base = re.sub(r"[\\/*?:\[\]\x00-\x1f]", "_", key).strip("'") or "結果"
            name, suffix = base[:31], 1
            while name.casefold() in names:
                suffix += 1
                name = base[:25] + f"-{suffix}"
            names.add(name.casefold())
            path = Path(temporary) / f"sheet{len(sheets) + 1}.xml"
            state = {"name": name, "path": path, "rows": 0, "headers": headers, "handle": None}
            with path.open("w", encoding="utf-8", newline="") as f:
                f.write(f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{XML_NS}">')
                f.write('<sheetViews><sheetView workbookViewId="0">')
                if template.freeze and template.header:
                    f.write('<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>')
                f.write('</sheetView></sheetViews>')
                f.write(f'<cols><col min="1" max="{len(headers)}" width="{template.column_width}" customWidth="1"/></cols><sheetData>')
                if template.header:
                    f.write('<row r="1" ht="24" customHeight="1">' + "".join(cell_xml(v, column_letter(i + 1) + "1", True) for i, v in enumerate(headers)) + '</row>')
            sheets.append(state)
            active[key] = state
            return state

        # Keep only a small number of append handles open, even with hundreds of child sheets.
        handles = {}
        try:
            for row in rows:
                task.check()
                total += 1
                if total > MAX_EXPORT_ROWS:
                    raise AppError("EXPORT_LIMIT: 出力の安全上限200万行に達しました。対象を分割してください。")
                state = active.get(row.sheet)
                if state is None or state["rows"] >= template.sheet_rows:
                    state = create(row.sheet, row.headers)
                if state["headers"] != row.headers or len(row.cells) != len(row.headers):
                    raise AppError("COLUMN_MISMATCH: 同一シートの列定義が一致しません。")
                key = state["name"]
                if key not in handles:
                    if len(handles) >= 16:
                        oldest = next(iter(handles))
                        handles.pop(oldest).close()
                    handles[key] = state["path"].open("a", encoding="utf-8", newline="")
                state["rows"] += 1
                number = state["rows"] + int(template.header)
                handle = handles[key]
                handle.write(f'<row r="{number}">')
                for index, value in enumerate(row.cells, 1):
                    handle.write(cell_xml(value, column_letter(index) + str(number)))
                handle.write('</row>')
                if total % 500 == 0:
                    task.progress("Excel生成", total, 0)
        finally:
            for handle in handles.values():
                handle.close()
            close = getattr(rows, "close", None)
            if close:
                close()
        if not sheets:
            headers = (["SourceFile"] if template.source_file else []) + [c.name for c in template.columns if c.enabled]
            if any(c.enabled and c.repeat == "sheet" for c in template.columns):
                headers.insert(0, "RecordPath")
            create(template.sheet_name, headers)
        for state in sheets:
            with state["path"].open("a", encoding="utf-8", newline="") as f:
                f.write('</sheetData>')
                if template.header and template.auto_filter:
                    f.write(f'<autoFilter ref="A1:{column_letter(len(state["headers"]))}{state["rows"] + 1}"/>')
                f.write('<pageSetup orientation="landscape" paperSize="9"/></worksheet>')
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            types = '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            workbook = f'<workbook xmlns="{XML_NS}" xmlns:r="{REL_NS}"><bookViews><workbookView/></bookViews><sheets>'
            relations = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            for index, state in enumerate(sheets, 1):
                task.check()
                archive.write(state["path"], f"xl/worksheets/sheet{index}.xml")
                types += f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                workbook += f'<sheet name="{escape(state["name"], {chr(34): "&quot;"})}" sheetId="{index}" r:id="rId{index}"/>'
                relations += f'<Relationship Id="rId{index}" Type="{REL_NS}/worksheet" Target="worksheets/sheet{index}.xml"/>'
            relations += f'<Relationship Id="styles" Type="{REL_NS}/styles" Target="styles.xml"/></Relationships>'
            archive.writestr("[Content_Types].xml", types + '</Types>')
            archive.writestr("xl/workbook.xml", workbook + '</sheets></workbook>')
            archive.writestr("xl/_rels/workbook.xml.rels", relations)
            archive.writestr("_rels/.rels", f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="workbook" Type="{REL_NS}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
            archive.writestr("xl/styles.xml", STYLES)
        task.check()
    return {"rows": total, "sheets": len(sheets), "bytes": Path(target).stat().st_size}


def csv_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if value is None:
        return ""
    if isinstance(value, str) and re.match(r"^[\s\x00-\x1f]*[=+\-@]", value):
        return "'" + value
    return str(value)


def write_csv(target, rows, template, task=None):
    task = task or Task()
    template.validate()
    if any(c.enabled and c.repeat == "sheet" for c in template.columns):
        raise AppError("CSV_SHEETS: 繰り返しの別シート出力はXLSXを使用してください。")
    count, headers = 0, None
    try:
        with atomic_target(target) as output, output.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
            for row in rows:
                task.check()
                if headers is None:
                    headers = row.headers
                    if template.header:
                        writer.writerow(headers)
                if headers != row.headers:
                    raise AppError("CSV_COLUMNS: CSV統合時の列定義が一致しません。")
                writer.writerow(csv_value(v) for v in row.cells)
                count += 1
                if count > MAX_EXPORT_ROWS:
                    raise AppError("EXPORT_LIMIT: 出力の安全上限200万行に達しました。")
                if count % 500 == 0:
                    task.progress("CSV保存", count, 0)
    finally:
        close = getattr(rows, "close", None)
        if close:
            close()
    return {"rows": count, "bytes": Path(target).stat().st_size}


STYLES = f'''<styleSheet xmlns="{XML_NS}">
<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm:ss"/></numFmts>
<fonts count="2"><font><sz val="11"/><name val="Arial"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Arial"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF244B62"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFill="1" applyFont="1"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''
