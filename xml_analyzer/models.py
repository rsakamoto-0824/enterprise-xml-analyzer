from dataclasses import asdict, dataclass, field
import json
import re
import threading
import time

CHUNK_BYTES = 65536
PAGE_SIZE = 250
MAX_DEPTH = 256
MAX_TOKEN_CHARS = 1048576
MAX_XML_BUFFER_BYTES = MAX_TOKEN_CHARS * 4
MAX_VISIBLE_NODES = 10000
MAX_GRAPH_NODES = 500
MAX_CANDIDATES = 4096
MAX_PREVIEW_ROWS = 100
MAX_CELL_UNITS = 32767
MAX_SHEET_ROWS = 1048575
MAX_EXPORT_ROWS = 2000000
MAX_EXPANSION_ROWS = 10000
DIFF_TYPES = ("ADDED", "REMOVED", "MODIFIED", "MOVED", "UNCHANGED", "AMBIGUOUS")
DIFF_LABELS = dict(zip(DIFF_TYPES, ("追加", "削除", "変更", "移動", "変更なし", "曖昧")))
DIFF_COLORS = dict(zip(DIFF_TYPES, ("#167350", "#b83232", "#b26909", "#2869b0", "#64748b", "#824ab0")))


class AppError(Exception):
    """Only messages suitable for display to the user belong in this exception."""


class Cancelled(AppError):
    def __init__(self):
        super().__init__("CANCELLED: 処理を中断しました。")


class Task:
    def __init__(self, progress=None, timeout=1800):
        self.cancel = threading.Event()
        self.progress = progress or (lambda *args: None)
        self.started = time.monotonic()
        self.timeout = timeout

    def check(self):
        if self.cancel.is_set():
            raise Cancelled()
        if time.monotonic() - self.started > self.timeout:
            raise AppError("TIME_LIMIT: 処理時間の上限に達しました。対象を小さくしてください。")


@dataclass
class Column:
    path: str
    name: str
    type: str = "string"
    repeat: str = "join"
    missing: str = "blank"
    enabled: bool = True


@dataclass
class Template:
    version: int = 2
    record_path: str = ""
    layout: str = "records"
    columns: list[Column] = field(default_factory=list)
    filters: list[dict] = field(default_factory=list)
    logic: str = "AND"
    delimiter: str = " | "
    source_file: bool = True
    grouping: str = "merged"
    sheet_name: str = "抽出結果"
    header: bool = True
    auto_filter: bool = True
    freeze: bool = True
    sheet_rows: int = MAX_SHEET_ROWS
    column_width: float = 24

    def to_json(self):
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, text):
        if len(text.encode("utf-8")) > 262144:
            raise AppError("TEMPLATE_LIMIT: 設定JSONは256KiB以内にしてください。")
        try:
            raw = json.loads(text)
            if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] not in (1, 2):
                raise ValueError()
            if raw["version"] == 1:
                aliases = {"recordPath": "record_path", "sourceFile": "source_file", "sheetName": "sheet_name", "autoFilter": "auto_filter", "sheetRows": "sheet_rows", "columnWidth": "column_width"}
                raw = {aliases.get(k, k): v for k, v in raw.items() if k not in ("format", "outputName")}
                raw["version"] = 2
            raw["columns"] = [Column(**c) for c in raw["columns"]]
            result = cls(**raw)
            result.validate()
            return result
        except (ValueError, TypeError, KeyError, AttributeError):
            raise AppError("TEMPLATE_INVALID: 設定JSONの形式・バージョンを確認してください。") from None

    def validate(self):
        fail = AppError("TEMPLATE_INVALID: 出力列・型・条件・書式を確認してください。")
        if (self.layout not in ("records", "nodes", "hierarchy") or self.logic not in ("AND", "OR")
                or self.grouping not in ("merged", "sheets", "files") or not self.columns
                or len(self.columns) > 256 or not any(c.enabled for c in self.columns)
                or not isinstance(self.record_path, str) or len(self.record_path) > 4096
                or not isinstance(self.delimiter, str) or len(self.delimiter) > 32
                or not isinstance(self.sheet_name, str) or not self.sheet_name.strip()
                or len(self.sheet_name) > 31 or re.search(r"[\\/*?:\[\]]", self.sheet_name)
                or type(self.sheet_rows) is not int or not 1 <= self.sheet_rows <= MAX_SHEET_ROWS
                or type(self.column_width) not in (int, float) or not 1 <= self.column_width <= 100
                or len(self.filters) > 50):
            raise fail
        for value in (self.header, self.auto_filter, self.freeze, self.source_file):
            if type(value) is not bool:
                raise fail
        for c in self.columns:
            if (not isinstance(c.path, str) or len(c.path) > 4096 or not isinstance(c.name, str)
                    or not c.name.strip() or len(c.name) > 128 or c.type not in ("string", "number", "date", "boolean")
                    or c.repeat not in ("first", "join", "rows", "sheet") or c.missing not in ("blank", "error")
                    or type(c.enabled) is not bool):
                raise fail
        for f in self.filters:
            if (not isinstance(f, dict) or f.get("op") not in ("equals", "contains", "range", "empty", "exists")
                    or not all(isinstance(f.get(k, ""), str) and len(f.get(k, "")) <= 4096 for k in ("path", "value", "max"))):
                raise fail
        return self


@dataclass
class DiffOptions:
    mode: str = "full"
    key: str = "id"
    trim: bool = True
    comments: bool = False
    processing_instructions: bool = False
    ignore_order: bool = False
    verify_hashes: bool = False

    def validate(self):
        if self.mode not in ("structure", "value", "full") or not isinstance(self.key, str) or len(self.key) > 4096:
            raise AppError("COMPARE_OPTIONS: 比較設定が不正です。")
