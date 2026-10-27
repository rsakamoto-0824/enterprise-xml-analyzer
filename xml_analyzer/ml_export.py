"""Build a typed observation-by-feature matrix from repeated XML records."""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import json
import re

from .extraction import OutputRow
from .models import AppError, Column, MAX_CELL_UNITS, Task, Template
from .writers import write_xlsx

MAX_ML_COLUMNS = 1024
MAX_RECORD_UNITS = 4 * 1024 * 1024
ML_SHEET = "学習データ"
NUMBER = re.compile(r"[+-]?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")


def numeric_value(value):
    text = value.strip()
    if not NUMBER.fullmatch(text):
        return None
    try:
        number = Decimal(text)
        if not number.is_finite() or len(number.normalize().as_tuple().digits) > 15 or abs(number.adjusted()) > 307:
            return None
        return int(number) if number == number.to_integral_value() and abs(number) < 10**15 else float(number)
    except (InvalidOperation, ValueError, OverflowError):
        return None


def is_identifier(key):
    name = key[-1][0].rsplit("}", 1)[-1].lstrip("@")
    return bool(re.search(r"(?:^|[_-])(?:id|code|serial|identifier)$", name, re.I)
                or re.search(r"(?:Id|ID|Code|Serial|Identifier)$", name))


def check_cell(value):
    if value is not None and len(value.encode("utf-16-le")) // 2 > MAX_CELL_UNITS:
        raise AppError("ML_CELL_LIMIT: 学習データの1セル上限を超えました。1行の単位を細かくするか、全要素一覧を使ってください。")


@dataclass
class Feature:
    key: tuple
    name: str = ""
    numeric: bool = True
    boolean: bool = True
    count: int = 0
    first: str | None = None
    varies: bool = False

    def observe(self, value):
        if value is None or not value.strip():
            return
        self.count += 1
        self.numeric &= numeric_value(value) is not None
        self.boolean &= value.strip().casefold() in ("true", "false")
        if self.first is None:
            self.first = value
        else:
            self.varies |= self.first != value

    @property
    def type(self):
        if not self.count or is_identifier(self.key):
            return "string"
        return "boolean" if self.boolean else "number" if self.numeric else "string"

    def convert(self, value):
        if value is None or not value.strip():
            return None
        if self.type == "number":
            return numeric_value(value)
        if self.type == "boolean":
            return value.strip().casefold() == "true"
        return value


@dataclass
class MLPlan:
    record_path: str
    files: list
    features: list[Feature] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    repeated: set = field(default_factory=set)
    candidates: list = field(default_factory=list)

    @property
    def rows(self):
        return sum(self.counts.values())

    @property
    def headers(self):
        return [feature.name for feature in self.features]


def record_candidates(store, task):
    task.check()
    with store.connect() as db:
        rows = db.execute("""WITH groups AS (
            SELECT file,path,min(depth) AS depth,count(*) AS records FROM nodes
            WHERE kind='element' GROUP BY file,path)
            SELECT path,min(depth) AS depth,sum(records) AS records FROM groups
            GROUP BY path HAVING max(records)>1 AND count(*)=?
            ORDER BY depth,records DESC,path LIMIT 100""", (len(store.files()),)).fetchall()
    task.check()
    return [row["path"] for row in rows]


def records(store, files, path, task):
    for file in files:
        seen = False
        for record in store.nodes(file["id"], path or None):
            task.check()
            if record["kind"] != "element":
                continue
            seen = True
            yield file, record
            if not path:
                break
        if not seen:
            raise AppError("ML_RECORD_MISSING: 指定した1行の単位が見つからないXMLがあります。全ファイルで共通する単位を選択してください。")


def flatten_record(store, record, task, repeated):
    values, stack = {}, []
    units = 0

    def put(key, value):
        nonlocal units
        check_cell(value)
        values[key] = value
        units += len((value or "").encode("utf-16-le")) // 2
        if len(values) > MAX_ML_COLUMNS or units > MAX_RECORD_UNITS:
            raise AppError("ML_RECORD_LIMIT: 1件の項目数・文字量が上限を超えました。より細かい繰り返し要素を1行の単位にしてください。")

    def finish():
        context = stack.pop()
        node, key, text = context
        if text or node["elements"] == 1:
            text_key = key if key and node["elements"] == 1 else key + (("#text", 0),)
            put(text_key, text if text.strip() else None)

    for node in store.nodes(record["file"], start=record["id"] - 1, end=record["end_id"]):
        task.check()
        while stack and stack[-1][0]["end_id"] < node["id"]:
            finish()
        if node["kind"] == "element":
            key = () if node["id"] == record["id"] else stack[-1][1] + ((node["path"].rsplit("/", 1)[-1], node["sibling"]),)
            if key and node["sibling"] > 1:
                repeated.add(tuple(part[0] for part in key))
            for name, value in json.loads(node["attrs"]).items():
                put(key + (("@" + name, 0),), value)
            stack.append([node, key, ""])
        elif node["kind"] in ("text", "cdata") and stack:
            if not node["text"].strip() and stack[-1][0]["elements"] > 1:
                continue
            value = stack[-1][2] + node["text"]
            check_cell(value)
            stack[-1][2] = value
    while stack:
        finish()
    return values


def feature_path(key, repeated):
    parts, prefix = [], []
    for name, index in key:
        prefix.append(name)
        parts.append(name + (f"[{index}]" if tuple(prefix) in repeated else ""))
    return "/".join(parts)


def plan_export(store, record_path=None, task=None):
    task = task or Task()
    files = store.files()
    if not files:
        raise AppError("FILES_EMPTY: 先にXMLファイルを読み込んでください。")
    candidates = record_candidates(store, task)
    path = (candidates[0] if candidates else "") if record_path is None else record_path
    if not isinstance(path, str) or len(path) > 4096 or (path and not path.startswith("/")):
        raise AppError("ML_RECORD_PATH: 1行の単位に要素の絶対パスを指定してください。")
    plan = MLPlan(path, files, candidates=candidates)
    found = {}
    with store.session():
        for file, record in records(store, files, path, task):
            values = flatten_record(store, record, task, plan.repeated)
            plan.counts[file["id"]] = plan.counts.get(file["id"], 0) + 1
            for key, value in values.items():
                if key not in found:
                    if len(found) >= MAX_ML_COLUMNS:
                        raise AppError("ML_COLUMN_LIMIT: 学習データが1024列を超えます。1行の単位を細かくしてください。")
                    found[key] = Feature(key)
                found[key].observe(value)
            if plan.rows % 100 == 0:
                task.progress("学習データの列・型を確認", plan.rows, 0)
    if not found:
        raise AppError("ML_COLUMNS_EMPTY: 値や属性のある項目がありません。別の1行の単位を選択してください。")
    names = set()
    for index, feature in enumerate(found.values(), 1):
        path_name = feature_path(feature.key, plan.repeated)
        check_cell(path_name)
        name = path_name if len(path_name) <= 128 else f"項目_{index:04d}"
        while name in names:
            name = f"項目_{index:04d}_" + name[:110]
        feature.name = name
        names.add(name)
    plan.features = list(found.values())
    return plan


def matrix_rows(store, plan, task=None):
    task = task or Task()
    headers = plan.headers
    with store.session():
        for _, record in records(store, plan.files, plan.record_path, task):
            values = flatten_record(store, record, task, set())
            yield OutputRow(ML_SHEET, headers, [feature.convert(values.get(feature.key)) for feature in plan.features])


def preview_export(store, record_path=None, task=None):
    task = task or Task()
    plan = plan_export(store, record_path, task)
    generator = matrix_rows(store, plan, task)
    rows = []
    try:
        for row in generator:
            rows.append(row)
            if len(rows) == 20:
                break
    finally:
        generator.close()
    return plan, rows


def workbook_rows(store, plan, target, task):
    if target and target not in plan.headers:
        raise AppError("ML_TARGET: 目的変数が出力項目にありません。プレビュー後に選び直してください。")
    yield from matrix_rows(store, plan, task)
    offset = 1
    for file in plan.files:
        count = plan.counts[file["id"]]
        yield OutputRow("出典", ["ファイル名", "ファイルID", "データ開始番号", "データ終了番号", "件数", "1行の単位"],
                        [file["name"], file["id"], offset, offset + count - 1, count, plan.record_path or "XMLファイル単位"])
        offset += count
    for feature in plan.features:
        role = "目的変数" if feature.name == target else "ID候補（使用を判断）" if is_identifier(feature.key) else "説明変数候補"
        note = "全件欠損" if not feature.count else "定数列候補" if not feature.varies else ""
        yield OutputRow("項目定義", ["項目", "型", "役割", "欠損数", "非欠損数", "XML相対パス", "例（先頭80文字）", "注意"],
                        [feature.name, feature.type, role, plan.rows - feature.count, feature.count,
                         feature_path(feature.key, plan.repeated), (feature.first or "")[:80], note])
    notes = [
        ("データ形式", "1件1行・1項目1列。読み込み済みの全XMLが対象。XML内の順序を保持。"),
        ("1行の単位", plan.record_path or "XMLファイル単位"),
        ("データ件数", plan.rows), ("項目数", len(plan.features)), ("目的変数", target or "未指定（利用者が指定してください）"),
        ("欠損", "欠落・空文字・空白だけの値は空欄。ゼロ埋め・行削除・補完を自動実行しません。"),
        ("型", "全件の値から型を決定。先頭ゼロ・ID候補・15桁を超える数値は文字列として保持。"),
        ("繰り返し", "子要素の繰り返しは[1]、[2]等の別列。直積による行の増加や平均集計は行いません。"),
        ("出典", "学習データの各シートを順に連結したデータ連番。並べ替え前の行順と対応。"),
        ("学習前の確認", "目的変数、ID、定数列、欠損を確認し、同一対象・時系列のデータ漏洩に配慮して学習用と評価用を分割してください。"),
        ("前処理", "標準化・カテゴリ符号化・欠損補完は学習データだけに適合させます。このアプリは学習を実行しません。"),
        ("セル上限", "学習データは1行を分割しません。1セル32767 UTF-16単位超過時は保存を中止。全要素一覧は別操作。")]
    for name, value in notes:
        yield OutputRow("出力情報", ["項目", "内容"], [name, value])


def save_export(store, target_path, record_path=None, target="", task=None):
    task = task or Task()
    plan = plan_export(store, record_path, task)
    template = Template(sheet_name=ML_SHEET, source_file=False, columns=[Column(".", "学習データ")])
    result = write_xlsx(target_path, workbook_rows(store, plan, target, task), template, task)
    return result | {"records": plan.rows, "features": len(plan.features), "record_path": plan.record_path}
