from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import quote
import uuid
from xml.parsers import expat

from .models import AppError, CHUNK_BYTES, MAX_DEPTH, MAX_TOKEN_CHARS, MAX_XML_BUFFER_BYTES, Task

NS_SEPARATOR = "\x1f"


def expanded(uri, local):
    return "{" + quote(uri, safe="") + "}" + local if uri else local


def split_name(name):
    parts = name.split(NS_SEPARATOR)
    if len(parts) == 1:
        return "", parts[0], ""
    return parts[0], parts[1], parts[2] if len(parts) > 2 else ""


def feed(hasher, value):
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    hasher.update(len(raw).to_bytes(8, "big"))
    hasher.update(raw)


@dataclass
class Frame:
    id: int
    path: str
    children: int = 0
    elements: int = 1
    siblings: dict = field(default_factory=dict)
    full: object = field(default_factory=hashlib.sha256)
    shape: object = field(default_factory=hashlib.sha256)


def detect_encoding(header):
    if header.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        raise AppError("ENCODING: UTF-32には対応していません。UTF-8/UTF-16を使用してください。")
    if header.startswith(b"\xff\xfe") or header.startswith(b"<\x00"):
        codec = "utf-16-le"
    elif header.startswith(b"\xfe\xff") or header.startswith(b"\x00<"):
        codec = "utf-16-be"
    else:
        codec = "utf-8-sig"
    text = header.decode(codec, errors="ignore")
    match = re.search(r'^\ufeff?\s*<\?xml\s+[^?]*encoding\s*=\s*[\'"]([^\'"]+)', text, re.I)
    if match:
        declared = match[1].lower().replace("_", "-")
        if declared not in ("utf-8", "utf-16", "utf-16le", "utf-16be", "utf-16-le", "utf-16-be"):
            raise AppError("ENCODING: UTF-8/UTF-16以外の宣言は使用できません。")
        if codec.startswith("utf-16") != declared.startswith("utf-16"):
            raise AppError("ENCODING: XML宣言と文字エンコーディングが一致しません。")
    return codec


def parse_file(store, path, task=None, chunk_bytes=CHUNK_BYTES):
    task = task or Task()
    path = Path(path)
    file_id = uuid.uuid4().hex
    started = time.monotonic()
    info = {"elements": 0, "attributes": 0, "nodes": 0, "max_depth": 0, "namespaces": {},
            "names": {}, "depths": {}, "children": {}, "parse_seconds": 0, "database_seconds": 0}
    parser = expat.ParserCreate(namespace_separator=NS_SEPARATOR)
    parser.namespace_prefixes = True
    parser.buffer_text = False
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    stack = []
    top = Frame(0, "", elements=0)
    text_parts = []
    text_size = 0
    in_cdata = False
    pending_namespaces = []
    db_seconds = 0

    def check_text(value):
        if len(value) > MAX_TOKEN_CHARS:
            raise AppError("TOKEN_LIMIT: 単一のテキスト・属性群等が1Mi文字を超えました。")

    def count(mapping, key):
        mapping[key] = mapping.get(key, 0) + 1
        if len(mapping) > 10000:
            raise AppError("VARIETY_LIMIT: 名前または統計の種類数が1万件を超えました。")

    try:
        with path.open("rb") as source, store.connect() as db:
            size = path.stat().st_size
            info["encoding"] = detect_encoding(source.read(512))
            source.seek(0)
            db.execute("INSERT INTO files VALUES(?,?,?,?,?)", (file_id, path.name, size, "PARSING", "{}"))
            db.commit()

            def create(kind, name, uri="", local="", prefix="", text="", attrs=None):
                parent = stack[-1] if stack else top
                identity = expanded(uri, local or name)
                count(parent.siblings, identity)
                parent.children += 1
                node_id = info["nodes"] + 1
                info["nodes"] = node_id
                depth = len(stack)
                if depth > MAX_DEPTH:
                    raise AppError("DEPTH_LIMIT: 深さ256を超えるXMLには対応していません。")
                node_path = parent.path + "/" + identity
                db.execute("INSERT INTO nodes(file,id,parent,kind,name,uri,local,prefix,depth,sibling,position,path,text,attrs,end_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (file_id, node_id, parent.id, kind, name, uri, local or name, prefix, depth,
                            parent.siblings[identity], parent.children, node_path, text,
                            json.dumps(attrs or {}, ensure_ascii=False), node_id))
                return node_id, node_path

            def leaf(kind, text, name=None):
                if not text and kind != "pi":
                    return
                check_text(text)
                node_id, _ = create(kind, name or "#" + kind, text=text)
                full = hashlib.sha256()
                shape = hashlib.sha256()
                feed(full, [kind, name, text])
                feed(shape, [kind, name])
                db.execute("UPDATE nodes SET full_hash=?,shape_hash=? WHERE file=? AND id=?",
                           (full.hexdigest(), shape.hexdigest(), file_id, node_id))
                if stack:
                    feed(stack[-1].full, [kind, full.hexdigest()])

            def flush_text():
                nonlocal text_size
                if text_parts:
                    leaf("cdata" if in_cdata else "text", "".join(text_parts))
                    text_parts.clear()
                    text_size = 0

            def chars(value):
                nonlocal text_size
                if not stack:
                    return
                text_size += len(value)
                if text_size > MAX_TOKEN_CHARS:
                    raise AppError("TOKEN_LIMIT: 単一テキストが1Mi文字を超えました。")
                text_parts.append(value)

            def start(name, attributes):
                flush_text()
                task.check()
                total = len(name) + sum(len(k) + len(v) for k, v in attributes.items())
                if total > MAX_TOKEN_CHARS:
                    raise AppError("TOKEN_LIMIT: タグと属性の合計が1Mi文字を超えました。")
                uri, local, prefix = split_name(name)
                attrs = {expanded(*split_name(k)[:2]): v for k, v in attributes.items()}
                display = prefix + ":" + local if prefix else local
                node_id, node_path = create("element", display, uri, local, prefix, attrs=attrs)
                frame = Frame(node_id, node_path)
                feed(frame.full, [uri, local, sorted(attrs.items())])
                feed(frame.shape, [uri, local, sorted(attrs)])
                stack.append(frame)
                info["elements"] += 1
                info["attributes"] += len(attrs)
                info["max_depth"] = max(info["max_depth"], len(stack) - 1)
                count(info["namespaces"], uri or "(なし)")
                count(info["names"], expanded(uri, local))
                count(info["depths"], str(len(stack) - 1))
                for namespace in pending_namespaces + [split_name(k)[0] for k in attributes]:
                    if namespace:
                        info["namespaces"].setdefault(namespace, 0)
                pending_namespaces.clear()

            def end(name):
                flush_text()
                f = stack.pop()
                full, shape = f.full.hexdigest(), f.shape.hexdigest()
                db.execute("UPDATE nodes SET children=?,end_id=?,full_hash=?,shape_hash=?,elements=? WHERE file=? AND id=?",
                           (f.children, info["nodes"], full, shape, f.elements, file_id, f.id))
                count(info["children"], str(f.children))
                if stack:
                    stack[-1].elements += f.elements
                    feed(stack[-1].full, ["element", full])
                    feed(stack[-1].shape, shape)

            def cdata(value):
                nonlocal in_cdata
                flush_text()
                in_cdata = value

            def comment(value):
                flush_text()
                leaf("comment", value)

            def pi(target, value):
                flush_text()
                leaf("pi", value, target)

            def forbidden(*args):
                raise AppError("DTD_FORBIDDEN: DTD・独自エンティティは使用できません。")

            parser.StartElementHandler = start
            parser.EndElementHandler = end
            parser.CharacterDataHandler = chars
            parser.StartCdataSectionHandler = lambda: cdata(True)
            parser.EndCdataSectionHandler = lambda: cdata(False)
            parser.CommentHandler = comment
            parser.ProcessingInstructionHandler = pi
            parser.StartNamespaceDeclHandler = lambda prefix, uri: pending_namespaces.append(uri)
            parser.StartDoctypeDeclHandler = forbidden
            parser.EntityDeclHandler = forbidden
            parser.ExternalEntityRefHandler = forbidden
            parser.XmlDeclHandler = lambda version, encoding, standalone: (
                None if not encoding or encoding.lower().replace("_", "-") in
                ("utf-8", "utf-16", "utf-16le", "utf-16be", "utf-16-le", "utf-16-be") else forbidden())
            consumed = 0
            while chunk := source.read(chunk_bytes):
                task.check()
                parser.Parse(chunk, False)
                consumed += len(chunk)
                if consumed - parser.CurrentByteIndex > MAX_XML_BUFFER_BYTES:
                    raise AppError("TOKEN_LIMIT: 未完了のXMLトークンが4MiBを超えました。")
                before = time.monotonic()
                db.commit()
                db_seconds += time.monotonic() - before
                task.progress("XML解析", consumed, size)
            parser.Parse(b"", True)
            flush_text()
            task.check()
            info["database_seconds"] = db_seconds
            info["parse_seconds"] = time.monotonic() - started
            db.execute("UPDATE files SET status='READY',info=? WHERE id=?", (json.dumps(info, ensure_ascii=False), file_id))
        return {"id": file_id, "name": path.name, "size": size, "info": info}
    except Exception as error:
        store.delete_file(file_id)
        if isinstance(error, AppError):
            raise
        if isinstance(error, expat.ExpatError):
            raise AppError(f"XML_SYNTAX: XML構文エラー（{error.lineno}行・{error.offset}列）。") from None
        if isinstance(error, sqlite3.Error):
            raise AppError("STORAGE_ERROR: 作業領域の空き容量と書き込み権限を確認してください。") from None
        if isinstance(error, OSError):
            raise AppError("FILE_ERROR: XMLの読み取り権限・パス・空き容量を確認してください。") from None
        raise
