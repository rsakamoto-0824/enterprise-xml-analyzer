from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import threading

from .models import AppError, PAGE_SIZE

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);
INSERT OR IGNORE INTO metadata VALUES('schema','2');
INSERT OR IGNORE INTO metadata VALUES('application','enterprise-xml-analyzer-python');
CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,name TEXT,size INTEGER,status TEXT,info TEXT);
CREATE TABLE IF NOT EXISTS nodes(
 file TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,id INTEGER NOT NULL,parent INTEGER,
 kind TEXT,name TEXT,uri TEXT,local TEXT,prefix TEXT,depth INTEGER,sibling INTEGER,
 position INTEGER,path TEXT,text TEXT,attrs TEXT,children INTEGER DEFAULT 0,end_id INTEGER,
 full_hash TEXT,shape_hash TEXT,elements INTEGER DEFAULT 0,
 PRIMARY KEY(file,id));
CREATE INDEX IF NOT EXISTS node_parent ON nodes(file,parent,id);
CREATE INDEX IF NOT EXISTS node_path ON nodes(file,path,id);
CREATE TABLE IF NOT EXISTS comparisons(id TEXT PRIMARY KEY,a TEXT REFERENCES files(id) ON DELETE CASCADE,
 b TEXT REFERENCES files(id) ON DELETE CASCADE,info TEXT);
CREATE TABLE IF NOT EXISTS differences(comparison TEXT REFERENCES comparisons(id) ON DELETE CASCADE,
 seq INTEGER,type TEXT,a INTEGER,b INTEGER,path TEXT,name TEXT,attribute TEXT,old TEXT,new TEXT,
 confidence TEXT,details TEXT,covered INTEGER,PRIMARY KEY(comparison,seq));
CREATE INDEX IF NOT EXISTS diff_type ON differences(comparison,type,seq);
"""


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self._local = threading.local()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        cached = getattr(self._local, "db", None)
        if cached is not None:
            yield cached
            return
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("PRAGMA cache_size=-8192")
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def session(self):
        """Reuse one connection within an engine task; never share it between threads."""
        with self.connect() as db:
            previous = getattr(self._local, "db", None)
            self._local.db = db
            try:
                yield self
            finally:
                self._local.db = previous

    def files(self):
        with self.connect() as db:
            return [dict(r) | {"info": json.loads(r["info"])} for r in db.execute("SELECT * FROM files WHERE status='READY' ORDER BY rowid")]

    def node(self, file, node_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM nodes WHERE file=? AND id=?", (file, node_id)).fetchone()
            return dict(row) if row else None

    def children(self, file, parent=0, after=0, limit=PAGE_SIZE):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM nodes WHERE file=? AND parent=? AND id>? ORDER BY id LIMIT ?", (file, parent, after, limit))]

    def nodes(self, file, path=None, start=0, end=9223372036854775807):
        with self.connect() as db:
            sql = "SELECT * FROM nodes WHERE file=? AND id>? AND id<=?"
            args = [file, start, end]
            if path is not None:
                sql += " AND path=? AND kind='element'"
                args.append(path)
            for row in db.execute(sql + " ORDER BY id", args):
                yield dict(row)

    def direct_text(self, n):
        if n["kind"] != "element":
            return n["text"]
        with self.connect() as db:
            return "".join(r[0] for r in db.execute("SELECT text FROM nodes WHERE file=? AND parent=? AND kind IN ('text','cdata') ORDER BY id", (n["file"], n["id"])))

    def instance_path(self, n):
        parts = []
        while n:
            parts.append(f'{n["name"]}[{n["sibling"]}]')
            n = self.node(n["file"], n["parent"]) if n["parent"] else None
        return "/" + "/".join(reversed(parts))

    def delete_file(self, file):
        with self.connect() as db:
            db.execute("DELETE FROM files WHERE id=?", (file,))

    def clear(self):
        with self.connect() as db:
            db.execute("DELETE FROM files")
        with self.connect() as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db.execute("VACUUM")

    def save_workspace(self, target):
        with self.connect() as source:
            with closing(sqlite3.connect(target)) as dest:
                source.backup(dest)

    def load_workspace(self, source):
        # Open the source read-only and validate before replacing the temporary work database.
        try:
            uri = Path(source).resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as src:
                if src.execute("SELECT value FROM metadata WHERE key='schema'").fetchone() != ("2",):
                    raise ValueError()
                if src.execute("SELECT value FROM metadata WHERE key='application'").fetchone() != ("enterprise-xml-analyzer-python",):
                    raise ValueError()
                if src.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view') LIMIT 1").fetchone():
                    raise ValueError()
                if src.execute("SELECT count(*) FROM files").fetchone()[0] > 16:
                    raise ValueError()
                if src.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise ValueError()
                for table in ("files", "nodes", "comparisons", "differences"):
                    src.execute(f"SELECT * FROM {table} LIMIT 0")
                src.execute("SELECT file,id,parent,kind,name,uri,local,prefix,depth,sibling,position,path,text,attrs,children,end_id,full_hash,shape_hash,elements FROM nodes LIMIT 0")
                with self.connect() as dest:
                    src.backup(dest)
            with self.connect() as db:
                db.execute("DELETE FROM files WHERE status!='READY'")
        except (sqlite3.Error, ValueError, OSError):
            raise AppError("WORKSPACE_INVALID: 対応する正常な作業ファイルを選択してください。") from None

    def differences(self, comparison, kind="", offset=0, limit=1000):
        with self.connect() as db:
            sql = "SELECT * FROM differences WHERE comparison=?"
            args = [comparison]
            if kind:
                sql += " AND type=?"
                args.append(kind)
            args += [limit, offset]
            return [dict(r) for r in db.execute(sql + " ORDER BY seq LIMIT ? OFFSET ?", args)]
