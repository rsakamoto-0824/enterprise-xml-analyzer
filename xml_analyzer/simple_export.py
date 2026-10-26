"""Export every parsed element without applying the configurable extraction template."""
from itertools import zip_longest

from .extraction import OutputRow
from .models import AppError, Column, MAX_CELL_UNITS, Task, Template

SIMPLE_HEADERS = ["元ファイル", "ファイルID", "要素番号", "親要素番号", "階層", "要素名",
                  "名前空間", "要素パス", "属性(JSON)", "テキスト", "分割番号"]
SIMPLE_SHEET = "全要素"
CELL_BYTES = MAX_CELL_UNITS * 2


def simple_template():
    return Template(sheet_name=SIMPLE_SHEET, source_file=False,
                    columns=[Column(".", name) for name in SIMPLE_HEADERS])


def cell_chunks(fragments, task):
    """Join text fragments into bounded cells without splitting a UTF-16 surrogate pair."""
    pending = bytearray()
    try:
        for text in fragments:
            task.check()
            raw = text.encode("utf-16-le")
            start = 0
            while start < len(raw):
                task.check()
                end = min(len(raw), start + CELL_BYTES - len(pending))
                if end < len(raw) and 0xD800 <= int.from_bytes(raw[end - 2:end], "little") <= 0xDBFF:
                    end -= 2
                if end == start:
                    yield pending.decode("utf-16-le")
                    pending.clear()
                    continue
                pending.extend(raw[start:end])
                start = end
                if len(pending) == CELL_BYTES:
                    yield pending.decode("utf-16-le")
                    pending.clear()
        if pending:
            yield pending.decode("utf-16-le")
    finally:
        close = getattr(fragments, "close", None)
        if close:
            close()


def direct_text_fragments(db, file_id, element_id):
    cursor = db.execute("SELECT text FROM nodes WHERE file=? AND parent=? "
                        "AND kind IN ('text','cdata') ORDER BY id", (file_id, element_id))
    try:
        for row in cursor:
            yield row[0]
    finally:
        cursor.close()


def all_element_rows(store, task=None):
    task = task or Task()
    files = store.files()
    if not files:
        raise AppError("FILES_EMPTY: 先にXMLファイルを読み込んでください。")
    total = sum(file["info"]["elements"] for file in files)
    count = 0
    with store.connect() as db:
        for file in files:
            path_parts = []
            cursor = db.execute("SELECT id,parent,depth,name,uri,sibling,attrs FROM nodes "
                                "WHERE file=? AND kind='element' ORDER BY id", (file["id"],))
            try:
                for element in cursor:
                    task.check()
                    depth = element["depth"]
                    del path_parts[depth:]
                    path_parts.append(f'{element["name"]}[{element["sibling"]}]')
                    fixed = [file["name"], file["id"], element["id"], element["parent"], depth]
                    values = [element["name"], element["uri"], "/" + "/".join(path_parts), element["attrs"]]
                    streams = [cell_chunks((value,), task) for value in values]
                    streams.append(cell_chunks(direct_text_fragments(db, file["id"], element["id"]), task))
                    try:
                        for part, cells in enumerate(zip_longest(*streams, fillvalue=""), 1):
                            task.check()
                            yield OutputRow(SIMPLE_SHEET, SIMPLE_HEADERS, fixed + list(cells) + [part])
                    finally:
                        for stream in streams:
                            stream.close()
                    count += 1
                    if count % 100 == 0:
                        task.progress("全要素を抽出", count, total)
            finally:
                cursor.close()
