from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from itertools import product
import json
import math
import re

from .models import AppError, Column, MAX_CELL_UNITS, MAX_EXPANSION_ROWS, Task


@dataclass
class OutputRow:
    sheet: str
    headers: list[str]
    cells: list


def values_for(store, record, path, filename, task):
    task.check()
    meta = {"$name": record["name"], "$path": None, "$namespace": record["uri"],
            "$file": filename, "$depth": str(record["depth"])}
    if path == "$path":
        return [store.instance_path(record)]
    if path in meta:
        return [meta[path]]
    if path == "$attributes":
        return [f"{k}={v}" for k, v in json.loads(record["attrs"]).items()]
    if path.startswith("$level:"):
        try:
            depth = int(path[7:])
        except ValueError:
            raise AppError("PATH_INVALID: 階層番号が不正です。") from None
        n = record
        while n and n["depth"] > depth:
            n = store.node(n["file"], n["parent"])
        return [n["name"]] if n and n["depth"] == depth else []
    if path.startswith("../"):
        parent = store.node(record["file"], record["parent"])
        return values_for(store, parent, path[3:], filename, task) if parent else []
    if path.startswith("/") or "[" in path or "//" in path:
        raise AppError("PATH_INVALID: 列には相対パス・@属性・../親を指定してください。")
    current = [record]
    for part in (p for p in path.split("/") if p not in ("", ".")):
        task.check()
        if part == "text()":
            break
        if part.startswith("@"):
            return [json.loads(n["attrs"])[part[1:]] for n in current if part[1:] in json.loads(n["attrs"])]
        following = []
        for parent in current:
            after = 0
            while batch := store.children(parent["file"], parent["id"], after):
                task.check()
                following.extend(n for n in batch if n["kind"] == "element" and (part == "*" or n["path"].rsplit("/", 1)[-1] == part))
                if len(following) > MAX_EXPANSION_ROWS:
                    raise AppError("EXPANSION_LIMIT: 1レコードの繰り返し値が1万件を超えました。")
                after = batch[-1]["id"]
        current = following
    return [store.direct_text(n) for n in current]


def convert(value, column):
    if value is None:
        if column.missing == "error":
            raise AppError("MISSING_VALUE: 必須の抽出項目が見つかりません。")
        return None
    if len(value.encode("utf-16-le")) // 2 > MAX_CELL_UNITS:
        raise AppError("CELL_LIMIT: 1セル32767文字を超えました。抽出項目を絞ってください。")
    if column.type == "string":
        return value
    if column.type == "boolean":
        if value.casefold() in ("true", "1"):
            return True
        if value.casefold() in ("false", "0"):
            return False
        raise AppError("TYPE_BOOLEAN: 真偽値はtrue/false/1/0で指定してください。")
    if column.type == "number":
        try:
            number = Decimal(value.strip())
            if not number.is_finite() or len(number.normalize().as_tuple().digits) > 15 or abs(number.adjusted()) > 307:
                raise ValueError()
            return int(number) if number == number.to_integral_value() and abs(number) < 10**15 else float(number)
        except (InvalidOperation, ValueError, OverflowError):
            raise AppError("TYPE_NUMBER: 数値に変換できない値、またはExcelの精度範囲外です。文字列型を使用してください。") from None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value):
            raise ValueError()
        return datetime.fromisoformat(value)
    except ValueError:
        raise AppError("TYPE_DATE: 日付はYYYY-MM-DDまたはUTCのISO形式で指定してください。") from None


def filter_record(store, record, template, filename, task):
    outcomes = []
    for f in template.filters:
        values = values_for(store, record, f.get("path", ""), filename, task)
        op, value = f["op"], f.get("value", "")
        if op == "exists":
            result = bool(values)
        elif op == "empty":
            result = not values or all(not v.strip() for v in values)
        elif op == "range":
            try:
                lower, upper = Decimal(value), Decimal(f.get("max", ""))
                if not lower.is_finite() or not upper.is_finite() or lower > upper:
                    raise InvalidOperation()
            except InvalidOperation:
                raise AppError("FILTER_RANGE: 数値範囲の下限・上限が不正です。") from None
            result = False
            for v in values:
                try:
                    n = Decimal(v)
                    result |= n.is_finite() and lower <= n <= upper
                except InvalidOperation:
                    pass
        else:
            result = any(v == value if op == "equals" else value in v for v in values)
        outcomes.append(result)
    return not outcomes or (all(outcomes) if template.logic == "AND" else any(outcomes))


def extract(store, file_ids, template, task=None):
    template.validate()
    task = task or Task()
    files = {f["id"]: f for f in store.files()}
    columns = [c for c in template.columns if c.enabled]
    if not file_ids or any(f not in files for f in file_ids):
        raise AppError("FILES_EMPTY: 解析済みの出力対象XMLを選択してください。")
    headers = (["SourceFile"] if template.source_file else []) + [c.name for c in columns]
    child_sheets = any(c.repeat == "sheet" for c in columns)
    if child_sheets:
        headers.insert(0, "RecordPath")
    with store.session():
        records_seen = 0
        for file_id in file_ids:
            filename = files[file_id]["name"]
            sheet = filename if template.grouping == "sheets" else template.sheet_name
            for record in store.nodes(file_id, template.record_path if template.layout == "records" else None):
                task.check()
                records_seen += 1
                if records_seen % 100 == 0:
                    task.progress("項目抽出", records_seen, 0)
                if not filter_record(store, record, template, filename, task):
                    continue
                choices, separate = [], []
                for c in columns:
                    values = values_for(store, record, c.path, filename, task)
                    if c.repeat == "sheet":
                        separate.append((c, values))
                        choices.append([None])
                    elif c.repeat == "rows":
                        choices.append([convert(v, c) for v in values] or [convert(None, c)])
                    else:
                        selected = values[0] if c.repeat == "first" and values else template.delimiter.join(values) if values else None
                        choices.append([convert(selected, c)])
                if math.prod(len(v) for v in choices) > MAX_EXPANSION_ROWS:
                    raise AppError("EXPANSION_LIMIT: 行展開の組み合わせが1レコード1万行を超えました。")
                prefix = [filename] if template.source_file else []
                record_path = store.instance_path(record) if child_sheets else ""
                for cells in product(*choices):
                    yield OutputRow(sheet, headers, ([record_path] if child_sheets else []) + prefix + list(cells))
                for c, values in separate:
                    for value in values:
                        yield OutputRow(sheet + "-" + c.name, ["RecordPath"] + (["SourceFile"] if template.source_file else []) + [c.name],
                                        [record_path] + prefix + [convert(value, c)])


def preview(store, file_ids, template, task=None):
    task = task or Task()
    rows, more = [], False
    generator = extract(store, file_ids, template, task)
    try:
        for row in generator:
            if len(rows) == 100:
                more = True
                break
            rows.append(row)
    finally:
        generator.close()
    return rows, more


def candidate_columns(store, record, task=None):
    task = task or Task()
    paths = {}
    with store.session():
        for index, n in enumerate(store.nodes(record["file"], start=record["id"] - 1, end=record["end_id"])):
            if index >= 2000 or len(paths) >= 250:
                break
            task.check()
            if n["kind"] != "element":
                continue
            prefix = n["path"][len(record["path"]):].lstrip("/")
            for attribute in json.loads(n["attrs"]):
                paths.setdefault((prefix + "/" if prefix else "") + "@" + attribute, attribute)
            if store.direct_text(n):
                paths.setdefault(prefix or ".", n["local"] if prefix else "テキスト")
    return [Column(path, name) for path, name in paths.items()][:256]


def difference_rows(store, comparison, kind="", task=None):
    task = task or Task()
    with store.connect() as db:
        row = db.execute("SELECT * FROM comparisons WHERE id=?", (comparison,)).fetchone()
        if not row or json.loads(row["info"])["status"] != "COMPLETED":
            raise AppError("COMPARISON_MISSING: 先に比較を実行してください。")
        files = {f["id"]: f["name"] for f in store.files()}
        a, b = files[row["a"]], files[row["b"]]
    headers = ["DiffType", "ElementPath", "ElementName", "AttributeName", "OldValue", "NewValue", "SourceFileA", "SourceFileB", "Confidence", "Details", "CoveredNodes"]
    offset = 0
    while batch := store.differences(comparison, kind, offset):
        for r in batch:
            task.check()
            yield OutputRow("XML差分", headers, [r["type"], r["path"], r["name"], r["attribute"], r["old"], r["new"], a, b, r["confidence"], r["details"], r["covered"]])
        offset += len(batch)
