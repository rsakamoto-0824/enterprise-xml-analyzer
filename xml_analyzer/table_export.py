"""Export the complete XML as either an item list or a file-by-item matrix."""
from contextlib import closing, contextmanager
from dataclasses import dataclass
from itertools import zip_longest
import json
import sqlite3
import tempfile
from pathlib import Path

from .extraction import OutputRow
from .models import AppError, Column, Task, Template
from .simple_export import cell_chunks, direct_text_fragments
from .writers import column_letter, write_xlsx

WIDE = "横持ち"
LONG = "縦持ち"
EXCEL_COLUMNS = 16384
SOURCE_HEADERS = ["元ファイル", "ファイルID"]
VALUE_COLUMNS = EXCEL_COLUMNS - len(SOURCE_HEADERS)
LONG_HEADERS = SOURCE_HEADERS + ["要素番号", "ノード", "項目名", "値", "種類", "値分割番号", "表示分割番号", "要素パス"]
PREVIEW_ROWS = 20
PREVIEW_COLUMNS = 30


@dataclass(slots=True)
class Item:
    file: dict
    element: int
    node: str
    name: str
    path: str
    kind: str
    part: int
    value: str

    @property
    def key(self):
        return self.path + f"/#part={self.part}"

def fragments_or_empty(fragments, task):
    found = False
    for value in cell_chunks(fragments, task):
        found = True
        yield value
    if not found:
        yield ""


def items(store, task):
    files = store.files()
    if not files:
        raise AppError("FILES_EMPTY: 先にXMLファイルを読み込んでください。")
    count = 0
    total = sum(file["info"]["elements"] for file in files)
    with store.connect() as db:
        for file in files:
            parts = []
            with closing(db.execute("SELECT id,depth,path,sibling,attrs FROM nodes "
                                    "WHERE file=? AND kind='element' ORDER BY id", (file["id"],))) as cursor:
                for element in cursor:
                    task.check()
                    del parts[element["depth"]:]
                    parent = "/" + "/".join(parts)
                    name = element["path"].rsplit("/", 1)[-1] + f'[{element["sibling"]}]'
                    parts.append(name)
                    path = "/" + "/".join(parts)
                    text = direct_text_fragments(db, file["id"], element["id"])
                    for part, value in enumerate(fragments_or_empty(text, task), 1):
                        yield Item(file, element["id"], parent, name, path, "要素", part, value)
                    for attribute, text in json.loads(element["attrs"]).items():
                        for part, value in enumerate(fragments_or_empty((text,), task), 1):
                            yield Item(file, element["id"], path, "@" + attribute,
                                       path + "/@" + attribute, "属性", part, value)
                    count += 1
                    if count % 100 == 0:
                        task.progress("すべての要素・属性を整理", count, total)


def split_fields(values, task):
    streams = [fragments_or_empty((value,), task) for value in values]
    try:
        yield from zip_longest(*streams, fillvalue="")
    finally:
        for stream in streams:
            stream.close()


def long_rows(store, task, counts):
    for item in items(store, task):
        counts["values"] += 1
        for part, cells in enumerate(split_fields([item.node, item.name, item.value, item.path], task), 1):
            node, name, value, path = cells
            counts["data_rows"] += 1
            yield OutputRow("全要素_縦持ち", LONG_HEADERS,
                            [item.file["name"], item.file["id"], item.element, node, name, value,
                             item.kind, item.part, part, path])


@dataclass
class TablePlan:
    db: object
    files: list
    columns: int
    values: int


@contextmanager
def prepare_table(store, task):
    files = store.files()
    if not files:
        raise AppError("FILES_EMPTY: 先にXMLファイルを読み込んでください。")
    # A disk-backed pivot keeps wide XMLs out of Python's in-memory dictionaries.
    with tempfile.TemporaryDirectory(prefix="xml-all-table-", dir=store.path.parent) as directory:
        with closing(sqlite3.connect(Path(directory) / "table.sqlite")) as db:
            db.row_factory = sqlite3.Row
            db.executescript("""PRAGMA cache_size=-8192;
                PRAGMA journal_mode=OFF;
                PRAGMA synchronous=OFF;
                CREATE TABLE fields(id INTEGER PRIMARY KEY,key TEXT UNIQUE,node TEXT,name TEXT,
                    path TEXT,kind TEXT,part INTEGER,present INTEGER);
                CREATE TABLE vals(file TEXT,field INTEGER,value TEXT,PRIMARY KEY(file,field)) WITHOUT ROWID;""")
            values = 0
            with db:
                for item in items(store, task):
                    field = db.execute("""INSERT INTO fields(key,node,name,path,kind,part,present)
                        VALUES(?,?,?,?,?,?,1) ON CONFLICT(key) DO UPDATE SET present=present+1 RETURNING id""",
                        (item.key, item.node, item.name, item.path, item.kind, item.part)).fetchone()[0]
                    db.execute("INSERT INTO vals VALUES(?,?,?)", (item.file["id"], field, item.value))
                    values += 1
            columns = db.execute("SELECT count(*) FROM fields").fetchone()[0]
            if (columns + VALUE_COLUMNS - 1) // VALUE_COLUMNS + (columns + 1048574) // 1048575 + 2 > 512:
                raise AppError("SHEET_LIMIT: 全項目の保存に必要なシートが512枚を超えます。XMLを分けて読み込んでください。")
            task.check()
            yield TablePlan(db, files, columns, values)


def field_title(field):
    title = field["path"] + (f'（続き{field["part"]}）' if field["part"] > 1 else "")
    return title if len(title) <= 128 else f'項目_{field["id"]:07d}'


class TableCells:
    def __init__(self, plan, file, start, count, task):
        self.plan, self.file, self.start, self.count, self.task = plan, file, start, count, task

    def __len__(self):
        return len(SOURCE_HEADERS) + self.count

    def __iter__(self):
        yield self.file["name"]
        yield self.file["id"]
        with closing(self.plan.db.execute("SELECT field,value FROM vals WHERE file=? AND field>=? AND field<? ORDER BY field",
                                          (self.file["id"], self.start, self.start + self.count))) as cursor:
            value = next(cursor, None)
            for field in range(self.start, self.start + self.count):
                self.task.check()
                if value is not None and value["field"] == field:
                    yield value["value"]
                    value = next(cursor, None)
                else:
                    yield None


def wide_rows(plan, task):
    for start in range(1, plan.columns + 1, VALUE_COLUMNS):
        task.check()
        count = min(VALUE_COLUMNS, plan.columns - start + 1)
        sheet = f"全要素_横持ち_{(start - 1) // VALUE_COLUMNS + 1}"
        fields = plan.db.execute("SELECT * FROM fields WHERE id>=? AND id<? ORDER BY id", (start, start + count))
        headers = SOURCE_HEADERS + [field_title(field) for field in fields]
        for file in plan.files:
            yield OutputRow(sheet, headers, TableCells(plan, file, start, count, task))
    headers = ["項目番号", "シート", "Excel列", "列名", "ノード", "項目名", "要素パス", "種類", "値分割番号", "存在XML数", "表示分割番号"]
    for field in plan.db.execute("SELECT * FROM fields ORDER BY id"):
        task.check()
        index = field["id"] - 1
        fixed = [field["id"], f"全要素_横持ち_{index // VALUE_COLUMNS + 1}",
                 column_letter(index % VALUE_COLUMNS + len(SOURCE_HEADERS) + 1), field_title(field)]
        for part, cells in enumerate(split_fields([field["node"], field["name"], field["path"]], task), 1):
            yield OutputRow("項目一覧", headers, fixed + list(cells) + [field["kind"], field["part"], field["present"], part])


def information_rows(files, orientation):
    for file in files:
        yield OutputRow("出典", ["元ファイル", "ファイルID", "要素数"],
                        [file["name"], file["id"], file["info"]["elements"]])
    notes = [
        ("形式", orientation), ("対象", "読み込み済みの全XML・全階層の要素と属性。選択中のノード・抽出条件では絞り込みません。"),
        ("横持ち", "1XMLファイル＝1行。ノード/項目を列に展開し、16382項目ごとにシートを分けます。"),
        ("縦持ち", "要素の直接テキストと各属性を、ノード・項目名・値の行として出力します。"),
        ("値", "XML値はすべて文字列です。先頭ゼロ・空白・大きな数・数式に似た文字列も保持します。"),
        ("要素", "親要素・空要素にも値の欄を用意します。直接テキストだけを連結し、子の値はその子の項目に出力します。"),
        ("繰り返し", "すべての要素に[1]、[2]等の同名兄弟番号を付け、全出現分を別の項目として保持します。"),
        ("名前空間", "{URI}要素名を使用し、同じ名前でもURIの異なる要素・属性を区別します。URIは内部のエスケープ表記です。"),
        ("長い値", "32767 UTF-16単位で分割し、値分割番号の順に連結できます。横持ちでは（続き2）等の列へ出力します。"),
        ("長い項目名", "横持ちの長い列名は項目番号に短縮します。完全なノード・項目名・パスは項目一覧に出力します。"),
        ("表示分割", "長いノード・項目名・パスは表示分割番号順に連結します。縦持ちではファイルID・要素番号・種類・値分割番号も対応に使います。"),
        ("制限", "全シート合計200万データ行、512シートまで。上限・中断・失敗時は保存を中止して既存ファイルを保持します。")]
    for name, value in notes:
        yield OutputRow("出力情報", ["項目", "内容"], [name, value])


def save_table(store, target, orientation=WIDE, task=None):
    task = task or Task()
    if orientation not in (LONG, WIDE):
        raise AppError("TABLE_LAYOUT: 縦持ちか横持ちを選択してください。")
    files = store.files()
    counts = {"values": 0, "data_rows": 0}
    template = Template(source_file=False, columns=[Column(".", "全要素")])
    if orientation == WIDE:
        with prepare_table(store, task) as plan:
            def rows():
                yield from wide_rows(plan, task)
                yield from information_rows(files, orientation)
            result = write_xlsx(target, rows(), template, task)
            counts.update(values=plan.values, columns=plan.columns, data_rows=len(files))
    else:
        def rows():
            yield from long_rows(store, task, counts)
            yield from information_rows(files, orientation)
        result = write_xlsx(target, rows(), template, task)
    return result | counts | {"orientation": orientation, "files": len(files),
                              "elements": sum(file["info"]["elements"] for file in files)}


def preview_table(store, orientation=WIDE, task=None):
    task = task or Task()
    files = store.files()
    if orientation == WIDE:
        with prepare_table(store, task) as plan:
            count = min(PREVIEW_COLUMNS - len(SOURCE_HEADERS), plan.columns)
            fields = plan.db.execute("SELECT * FROM fields ORDER BY id LIMIT ?", (count,)).fetchall()
            headers = SOURCE_HEADERS + [field_title(field) for field in fields]
            rows = [list(TableCells(plan, file, 1, count, task)) for file in plan.files[:PREVIEW_ROWS]]
            columns = plan.columns
    elif orientation == LONG:
        generator = long_rows(store, task, {"values": 0, "data_rows": 0})
        rows = []
        try:
            for row in generator:
                rows.append(row.cells)
                if len(rows) >= PREVIEW_ROWS:
                    break
        finally:
            generator.close()
        headers, columns = LONG_HEADERS, None
    else:
        raise AppError("TABLE_LAYOUT: 縦持ちか横持ちを選択してください。")
    return {"headers": headers, "rows": rows, "columns": columns, "orientation": orientation,
            "files": len(files), "elements": sum(file["info"]["elements"] for file in files)}
