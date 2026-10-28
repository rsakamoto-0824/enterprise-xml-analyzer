"""Export outermost XML records with separately defined, linked headers."""
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
SOURCE_HEADERS = ["元ファイル", "ファイルID", "データID"]
VALUE_COLUMNS = EXCEL_COLUMNS - len(SOURCE_HEADERS)
LONG_HEADERS = SOURCE_HEADERS + ["要素番号", "ノード", "項目名", "値", "種類", "値分割番号", "表示分割番号", "要素パス"]
PREVIEW_ROWS = 20
PREVIEW_COLUMNS = 30
MAX_HEADER_RANGES = 256
HEADER_HEADERS = ["ヘッダーID"] + SOURCE_HEADERS[:2] + LONG_HEADERS[3:]
LINK_HEADERS = ["データID", "ヘッダーID", "元ファイル", "ファイルID", "適用範囲"]


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
    selector: str
    end: int
    record: int
    is_record: bool
    column: str

    @property
    def key(self):
        return self.column + f"/#part={self.part}"

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
            has_children = db.execute("SELECT 1 FROM nodes WHERE file=? AND kind='element' AND depth=1 LIMIT 1", (file["id"],)).fetchone() is not None
            record, record_depth = 0, 1 if has_children else 0
            with closing(db.execute("SELECT id,depth,path,sibling,attrs,end_id FROM nodes "
                                    "WHERE file=? AND kind='element' ORDER BY id", (file["id"],))) as cursor:
                for element in cursor:
                    task.check()
                    del parts[element["depth"]:]
                    parent = "/" + "/".join(parts)
                    name = element["path"].rsplit("/", 1)[-1] + f'[{element["sibling"]}]'
                    parts.append(name)
                    path = "/" + "/".join(parts)
                    is_record = element["depth"] == record_depth
                    if is_record:
                        record = element["id"]
                    column = "/" + "/".join([parts[record_depth].rsplit("[", 1)[0], *parts[record_depth + 1:]]) if record else path
                    extra = (element["path"], element["end_id"], record, is_record)
                    text = direct_text_fragments(db, file["id"], element["id"])
                    for part, value in enumerate(fragments_or_empty(text, task), 1):
                        yield Item(file, element["id"], parent, name, path, "要素", part, value, *extra, column)
                    for attribute, text in json.loads(element["attrs"]).items():
                        for part, value in enumerate(fragments_or_empty((text,), task), 1):
                            yield Item(file, element["id"], path, "@" + attribute,
                                       path + "/@" + attribute, "属性", part, value, *extra, column + "/@" + attribute)
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


def header_ranges(paths):
    if isinstance(paths, str):
        paths = paths.splitlines()
    paths = tuple(dict.fromkeys(path.strip() for path in paths if path.strip()))
    if len(paths) > MAX_HEADER_RANGES:
        raise AppError("HEADER_LIMIT: ヘッダー範囲は256個まで指定できます。親要素の指定も利用してください。")
    for path in paths:
        if (not path.startswith('/') or path == '/' or path.endswith('/') or '//' in path
                or any(part in ('.', '..', '*') or part.startswith('@') or '[' in part or ']' in part for part in path[1:].split('/'))):
            raise AppError("HEADER_PATH: ヘッダー範囲は要素の絶対パスを1行ずつ指定してください。例：/root/header。番号・属性・ワイルドカードは指定しません。")
    return paths


def data_id(file, element):
    return f'{file}:D:{element}'


@dataclass
class TablePlan:
    db: object
    files: list
    columns: int
    values: int
    ranges: tuple
    records: int
    header_groups: int
    header_values: int
    common_values: int
    data_values: int


@contextmanager
def prepare_table(store, task, header_paths=()):
    ranges = header_ranges(header_paths)
    files = store.files()
    if not files:
        raise AppError("FILES_EMPTY: 先にXMLファイルを読み込んでください。")
    # Both layouts use a disk-backed plan so headers can be linked without retaining all records.
    with tempfile.TemporaryDirectory(prefix="xml-all-table-", dir=store.path.parent) as directory:
        with closing(sqlite3.connect(Path(directory) / 'table.sqlite')) as db:
            db.row_factory = sqlite3.Row
            db.executescript('''PRAGMA cache_size=-8192;
                PRAGMA journal_mode=OFF;
                PRAGMA synchronous=OFF;
                CREATE TABLE records(seq INTEGER PRIMARY KEY,id TEXT UNIQUE,file TEXT,element INTEGER,path TEXT);
                CREATE INDEX records_file ON records(file,element);
                CREATE TABLE fields(id INTEGER PRIMARY KEY,key TEXT UNIQUE,node TEXT,name TEXT,
                    path TEXT,kind TEXT,part INTEGER,present INTEGER);
                CREATE TABLE vals(record TEXT,field INTEGER,seq INTEGER,element INTEGER,node TEXT,name TEXT,path TEXT,value TEXT,
                    PRIMARY KEY(record,field)) WITHOUT ROWID;
                CREATE INDEX vals_seq ON vals(seq);
                CREATE TABLE header_groups(id TEXT PRIMARY KEY,file TEXT,element INTEGER,scope INTEGER);
                CREATE INDEX header_scope ON header_groups(file,scope);
                CREATE TABLE header_items(group_id TEXT,file TEXT,element INTEGER,node TEXT,name TEXT,path TEXT,kind TEXT,part INTEGER,value TEXT);
                CREATE TABLE common(file TEXT,element INTEGER,node TEXT,name TEXT,path TEXT,kind TEXT,part INTEGER,value TEXT);''')
            matched, selected = set(), set(ranges)
            active, current_file, end, values, headers, common = None, None, 0, 0, 0, 0
            with db:
                for item in items(store, task):
                    if item.file['id'] != current_file:
                        current_file, active = item.file['id'], None
                    if active and item.element > end:
                        active = None
                    if item.selector in selected:
                        matched.add(item.selector)
                        if not active:
                            active, end = f'{current_file}:H:{item.element}', item.end
                            # A selected entire outermost record is a file-level header; a header inside it belongs only to that record.
                            scope = item.record if item.record != item.element else 0
                            db.execute('INSERT INTO header_groups VALUES(?,?,?,?)', (active, current_file, item.element, scope))
                    metadata = (current_file, item.element, item.node, item.name, item.path, item.kind, item.part, item.value)
                    values += 1
                    if active:
                        db.execute('INSERT INTO header_items VALUES(?,?,?,?,?,?,?,?,?)', (active, *metadata))
                        headers += 1
                    elif not item.record:
                        db.execute('INSERT INTO common VALUES(?,?,?,?,?,?,?,?)', metadata)
                        common += 1
                    else:
                        record = data_id(current_file, item.record)
                        if item.is_record:
                            db.execute('INSERT OR IGNORE INTO records(id,file,element,path) VALUES(?,?,?,?)',
                                       (record, current_file, item.record, item.path))
                        node, _, name = item.column.rpartition('/')
                        field = db.execute('''INSERT INTO fields(key,node,name,path,kind,part,present)
                            VALUES(?,?,?,?,?,?,1) ON CONFLICT(key) DO UPDATE SET present=present+1 RETURNING id''',
                            (item.key, node or '/', name, item.column, item.kind, item.part)).fetchone()[0]
                        db.execute('INSERT INTO vals VALUES(?,?,?,?,?,?,?,?)',
                                   (record, field, values, item.element, item.node, item.name, item.path, item.value))
            if missing := selected - matched:
                raise AppError('HEADER_NOT_FOUND: 見つからないヘッダー範囲があります：' + sorted(missing)[0][:120])
            columns = db.execute('SELECT count(*) FROM fields').fetchone()[0]
            records = db.execute('SELECT count(*) FROM records').fetchone()[0]
            groups = db.execute('SELECT count(*) FROM header_groups').fetchone()[0]
            task.check()
            yield TablePlan(db, files, columns, values, ranges, records, groups, headers, common, values - headers - common)


def field_title(field):
    title = field['path'] + (f'（続き{field["part"]}）' if field['part'] > 1 else '')
    return title if len(title) <= 128 else f'項目_{field["id"]:07d}'


class TableCells:
    def __init__(self, plan, record, start, count, task):
        self.plan, self.record, self.start, self.count, self.task = plan, record, start, count, task

    def __len__(self):
        return len(SOURCE_HEADERS) + self.count

    def __iter__(self):
        file = next(file for file in self.plan.files if file['id'] == self.record['file'])
        yield file['name']
        yield file['id']
        yield self.record['id']
        with closing(self.plan.db.execute('SELECT field,value FROM vals WHERE record=? AND field>=? AND field<? ORDER BY field',
                                          (self.record['id'], self.start, self.start + self.count))) as cursor:
            value = next(cursor, None)
            for field in range(self.start, self.start + self.count):
                self.task.check()
                if value is not None and value['field'] == field:
                    yield value['value']
                    value = next(cursor, None)
                else:
                    yield None


def wide_rows(plan, task):
    for start in range(1, plan.columns + 1, VALUE_COLUMNS):
        task.check()
        count = min(VALUE_COLUMNS, plan.columns - start + 1)
        sheet = f'全要素_横持ち_{(start - 1) // VALUE_COLUMNS + 1}'
        fields = plan.db.execute('SELECT * FROM fields WHERE id>=? AND id<? ORDER BY id', (start, start + count))
        headers = SOURCE_HEADERS + [field_title(field) for field in fields]
        for record in plan.db.execute('SELECT * FROM records ORDER BY seq'):
            yield OutputRow(sheet, headers, TableCells(plan, record, start, count, task))
    headers = ['項目番号', 'シート', 'Excel列', '列名', 'ノード', '項目名', '要素パス', '種類', '値分割番号', '存在データ数', '表示分割番号']
    for field in plan.db.execute('SELECT * FROM fields ORDER BY id'):
        task.check()
        index = field['id'] - 1
        fixed = [field['id'], f'全要素_横持ち_{index // VALUE_COLUMNS + 1}',
                 column_letter(index % VALUE_COLUMNS + len(SOURCE_HEADERS) + 1), field_title(field)]
        for part, cells in enumerate(split_fields([field['node'], field['name'], field['path']], task), 1):
            yield OutputRow('項目一覧', headers, fixed + list(cells) + [field['kind'], field['part'], field['present'], part])


def item_rows(plan, task, kind='data'):
    if kind == 'data':
        sql = '''SELECT v.*,f.kind,f.part,r.file FROM vals v JOIN fields f ON f.id=v.field
                 JOIN records r ON r.id=v.record ORDER BY v.seq'''
        sheet, headers = '全要素_縦持ち', LONG_HEADERS
    elif kind == 'header':
        sql = 'SELECT * FROM header_items ORDER BY rowid'
        sheet, headers = 'ヘッダー', HEADER_HEADERS
    else:
        sql = 'SELECT * FROM common ORDER BY rowid'
        sheet, headers = '共通データ', LONG_HEADERS
    names = {file['id']: file['name'] for file in plan.files}
    with closing(plan.db.execute(sql)) as cursor:
        for item in cursor:
            task.check()
            if kind == 'header':
                fixed = [item['group_id'], names[item['file']], item['file'], item['element']]
            else:
                fixed = [names[item['file']], item['file'], item['record'] if kind == 'data' else '', item['element']]
            for part, cells in enumerate(split_fields([item['node'], item['name'], item['value'], item['path']], task), 1):
                node, name, value, path = cells
                yield OutputRow(sheet, headers, fixed + [node, name, value, item['kind'], item['part'], part, path])


def link_rows(plan, task):
    names = {file['id']: file['name'] for file in plan.files}
    sql = '''SELECT r.seq AS record_seq,r.id,r.file,h.id AS header,h.scope,h.element AS header_element FROM records r
             JOIN header_groups h ON h.file=r.file AND h.scope=0
             UNION ALL
             SELECT r.seq,r.id,r.file,h.id AS header,h.scope,h.element FROM records r
             JOIN header_groups h ON h.file=r.file AND h.scope=r.element WHERE h.scope!=0
             ORDER BY record_seq,header_element'''
    with closing(plan.db.execute(sql)) as cursor:
        for row in cursor:
            task.check()
            yield OutputRow('紐づけ', LINK_HEADERS, [row['id'], row['header'], names[row['file']], row['file'],
                                                   '共通' if row['scope'] == 0 else 'ノード内'])


def data_index_rows(plan, task):
    names = {file['id']: file['name'] for file in plan.files}
    for row in plan.db.execute('SELECT * FROM records ORDER BY seq'):
        task.check()
        for part, (path,) in enumerate(split_fields([row['path']], task), 1):
            yield OutputRow('データ一覧', ['データID', '元ファイル', 'ファイルID', '要素番号', 'ノード', '表示分割番号'],
                            [row['id'], names[row['file']], row['file'], row['element'], path, part])


def information_rows(plan, orientation, task):
    for file in plan.files:
        records = plan.db.execute('SELECT count(*) FROM records WHERE file=?', (file['id'],)).fetchone()[0]
        groups = plan.db.execute('SELECT count(*) FROM header_groups WHERE file=?', (file['id'],)).fetchone()[0]
        yield OutputRow('出典', ['元ファイル', 'ファイルID', '要素数', 'データ件数', 'ヘッダー件数'],
                        [file['name'], file['id'], file['info']['elements'], records, groups])
    for index, path in enumerate(plan.ranges, 1):
        for part, (text,) in enumerate(split_fields([path], task), 1):
            yield OutputRow('ヘッダー定義', ['定義番号', '要素パス', '表示分割番号'], [index, text, part])
    notes = [
        ('形式', orientation), ('対象', '全XML・全階層の要素と属性。ヘッダーに指定した範囲はヘッダーシートへ分離します。'),
        ('横持ちの1行', '文書ルート直下の最も外側のデータ要素。ヘッダー範囲の要素は除外します。子要素のない文書ではルートが1行です。'),
        ('横持ちの列', '外側のデータ要素名を起点に、配下の項目を列にします。外側の出現番号は列から除き、内部の繰り返し番号は保持します。'),
        ('ヘッダー', '指定した要素の配下全体を、ヘッダーID付きの一覧へ保存します。重なる範囲は外側の範囲に統合します。'),
        ('紐づけ', 'データシートのデータIDとヘッダーシートのヘッダーIDを、紐づけシートの同名列で結合します。複数範囲は複数行で対応します。'),
        ('適用範囲', '文書ルートまたはその直下のヘッダーは同じXMLの全データに適用。データ要素の内側のヘッダーはそのデータだけに適用します。'),
        ('データ一覧', 'データIDと元XMLの実際のノード位置を記録します。IDはファイルIDと要素番号から生成し、同名ファイルも区別します。'),
        ('共通データ', '文書ルート自身の直接テキスト・属性は共通データに保存し、ファイルIDで同じXMLの全データに対応します。'),
        ('データ件数', str(plan.records)), ('ヘッダー件数', str(plan.header_groups)),
        ('値', 'XMLとして解析後の文字列を保持。先頭ゼロ・空白・大きな数・数式に似た文字列を変換しません。'),
        ('親・空要素', '親も空要素も保持し、子の値は子の項目に保存。親は直接テキストだけを連結します。'),
        ('名前空間', 'URI展開名で区別します。接頭辞が異なっても同じURIなら対応し、URIは内部のエスケープ表記です。'),
        ('長い値・パス', '32767 UTF-16単位で分割。値分割番号・表示分割番号順に連結できます。長い列名は項目一覧を参照します。'),
        ('制限', f'横持ちは{VALUE_COLUMNS}項目ごとに別シート。全シート合計200万データ行・512シートまで。超過・中断・失敗時は既存保存先を保持します。')]
    for name, value in notes:
        yield OutputRow('出力情報', ['項目', '内容'], [name, value])


def save_table(store, target, orientation=WIDE, task=None, header_paths=()):
    task = task or Task()
    if orientation not in (LONG, WIDE):
        raise AppError('TABLE_LAYOUT: 縦持ちか横持ちを選択してください。')
    template = Template(source_file=False, columns=[Column('.', '全要素')])
    with prepare_table(store, task, header_paths) as plan:
        counts = {'data_rows': 0}
        def rows():
            if orientation == WIDE:
                yield from wide_rows(plan, task)
                counts['data_rows'] = plan.records
            else:
                for row in item_rows(plan, task):
                    counts['data_rows'] += 1
                    yield row
            yield from item_rows(plan, task, 'header')
            yield from item_rows(plan, task, 'common')
            yield from data_index_rows(plan, task)
            yield from link_rows(plan, task)
            yield from information_rows(plan, orientation, task)
        result = write_xlsx(target, rows(), template, task)
        return result | counts | plan_summary(plan, orientation)


def plan_summary(plan, orientation):
    return {'orientation': orientation, 'files': len(plan.files), 'elements': sum(file['info']['elements'] for file in plan.files),
            'values': plan.values, 'columns': plan.columns, 'data_records': plan.records,
            'header_groups': plan.header_groups, 'header_values': plan.header_values,
            'common_values': plan.common_values, 'data_values': plan.data_values}


def first_rows(generator, limit=PREVIEW_ROWS):
    try:
        rows = []
        for row in generator:
            rows.append(row.cells)
            if len(rows) >= limit:
                break
        return rows
    finally:
        generator.close()


def preview_table(store, orientation=WIDE, task=None, header_paths=()):
    task = task or Task()
    if orientation not in (LONG, WIDE):
        raise AppError('TABLE_LAYOUT: 縦持ちか横持ちを選択してください。')
    with prepare_table(store, task, header_paths) as plan:
        if orientation == WIDE:
            count = min(PREVIEW_COLUMNS - len(SOURCE_HEADERS), plan.columns)
            fields = plan.db.execute('SELECT * FROM fields ORDER BY id LIMIT ?', (count,))
            headers = SOURCE_HEADERS + [field_title(field) for field in fields]
            rows = [list(TableCells(plan, record, 1, count, task))
                    for record in plan.db.execute('SELECT * FROM records ORDER BY seq LIMIT ?', (PREVIEW_ROWS,))]
        else:
            headers, rows = LONG_HEADERS, first_rows(item_rows(plan, task))
        previews = {'中身のデータ': {'headers': headers, 'rows': rows},
                    'ヘッダー情報': {'headers': HEADER_HEADERS, 'rows': first_rows(item_rows(plan, task, 'header'))},
                    '紐づけ': {'headers': LINK_HEADERS, 'rows': first_rows(link_rows(plan, task))},
                    '共通データ': {'headers': LONG_HEADERS, 'rows': first_rows(item_rows(plan, task, 'common'))}}
        return previews['中身のデータ'] | plan_summary(plan, orientation) | {'previews': previews}
