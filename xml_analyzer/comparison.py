from collections import defaultdict, deque
from dataclasses import asdict
import json
import time
import uuid

from .models import AppError, DIFF_TYPES, DiffOptions, MAX_CANDIDATES, Task


def match_siblings(left, right, key, mode, ignore_order):
    used_left, used_right = set(), set()
    result = []
    positions_left = {n["id"]: i for i, n in enumerate(left)}
    positions_right = {n["id"]: i for i, n in enumerate(right)}

    def identity(n):
        return n["kind"], n["uri"], n["local"]

    def group(nodes, selector):
        groups = defaultdict(list)
        for n in nodes:
            value = selector(n)
            if value is not None:
                groups[value].append(n)
        return groups

    def identifier(n):
        attrs = json.loads(n["attrs"])
        return identity(n) + (attrs[key],) if key and key in attrs else None

    def bind(a, b, confidence):
        used_left.add(a["id"])
        used_right.add(b["id"])
        moved = confidence == "key" and not ignore_order and positions_left[a["id"]] != positions_right[b["id"]]
        result.append((a, b, confidence, moved))

    keyed_left, keyed_right = group(left, identifier), group(right, identifier)
    for value, items in keyed_left.items():
        others = keyed_right.get(value, [])
        for a, b in zip(items, others):
            bind(a, b, "key" if len(items) == len(others) == 1 else "ambiguous")
    hash_name = "shape_hash" if mode == "structure" else "full_hash"
    remaining_left = [n for n in left if n["id"] not in used_left and identifier(n) is None]
    remaining_right = [n for n in right if n["id"] not in used_right and identifier(n) is None]
    hashes_left = group(remaining_left, lambda n: identity(n) + (n[hash_name],))
    hashes_right = group(remaining_right, lambda n: identity(n) + (n[hash_name],))
    for value, items in hashes_left.items():
        others = hashes_right.get(value, [])
        if len(items) == len(others) == 1:
            bind(items[0], others[0], "hash")
    names_left = group([n for n in remaining_left if n["id"] not in used_left], identity)
    names_right = group([n for n in remaining_right if n["id"] not in used_right], identity)
    for value, items in names_left.items():
        others = names_right.get(value, [])
        for a, b in zip(items, others):
            confidence = "unique" if len(items) == len(others) == 1 else "order" if a[hash_name] == b[hash_name] else "ambiguous"
            bind(a, b, confidence)
    result.extend((n, None, "unique", False) for n in left if n["id"] not in used_left)
    result.extend((None, n, "unique", False) for n in right if n["id"] not in used_right)
    return result


def compare(store, a, b, options=None, task=None):
    options, task = options or DiffOptions(), task or Task(timeout=600)
    options.validate()
    ready = {f["id"] for f in store.files()}
    if a not in ready or b not in ready:
        raise AppError("COMPARE_PAIR: 解析済みXMLを指定してください。")
    comparison_id = uuid.uuid4().hex
    info = {"id": comparison_id, "a": a, "b": b, "options": asdict(options),
            "counts": dict.fromkeys(DIFF_TYPES, 0), "rows": 0, "status": "COMPARING"}
    started = time.monotonic()

    def accepted(n):
        if n["kind"] == "comment" and not options.comments:
            return False
        if n["kind"] == "pi" and not options.processing_instructions:
            return False
        if n["kind"] in ("text", "cdata"):
            return options.mode != "structure" and (not options.trim or bool(n["text"].strip()))
        return True

    def signature(n):
        attrs = json.loads(n["attrs"])
        values = sorted(attrs) if options.mode == "structure" else sorted(attrs.items())
        text = "" if options.mode == "structure" else n["text"].strip() if options.trim else n["text"]
        return json.dumps([n["kind"], n["uri"], n["local"], values, text], ensure_ascii=False)

    hash_usable = (options.mode == "structure" and not options.comments and not options.processing_instructions
                   or options.mode == "full" and not options.trim and options.comments and options.processing_instructions)
    hash_usable = hash_usable and not options.ignore_order and not options.verify_hashes
    hash_name = "shape_hash" if options.mode == "structure" else "full_hash"
    try:
        with store.session(), store.connect() as db:
            db.execute("INSERT INTO comparisons VALUES(?,?,?,?)", (comparison_id, a, b, json.dumps(info)))
            db.commit()
            queue = deque([(0, 0, 0, 0, False)])
            while queue:
                task.check()
                pa, pb, after_a, after_b, already_paged = queue.popleft()
                batch_a = store.children(a, pa, after_a, MAX_CANDIDATES + 1)
                batch_b = store.children(b, pb, after_b, MAX_CANDIDATES + 1)
                paged = already_paged or max(len(batch_a), len(batch_b)) > MAX_CANDIDATES
                extra = max(len(batch_a), len(batch_b)) > MAX_CANDIDATES
                batch_a, batch_b = batch_a[:MAX_CANDIDATES], batch_b[:MAX_CANDIDATES]
                if extra:
                    queue.append((pa, pb, batch_a[-1]["id"] if batch_a else 9223372036854775806,
                                  batch_b[-1]["id"] if batch_b else 9223372036854775806, True))
                left, right = [n for n in batch_a if accepted(n)], [n for n in batch_b if accepted(n)]
                positions_a = {n["id"]: i for i, n in enumerate(left)}
                positions_b = {n["id"]: i for i, n in enumerate(right)}
                for x, y, confidence, moved in match_siblings(left, right, "" if paged else options.key, options.mode, options.ignore_order):
                    task.check()
                    n = y or x
                    old, new, attr, covered, skip = "", "", "", 1, False
                    if x is None or y is None:
                        kind = "ADDED" if x is None else "REMOVED"
                        covered = n["elements"] if options.mode == "structure" else n["end_id"] - n["id"] + 1
                        details = "サブツリー単位。CoveredNodesは省略範囲の件数です。"
                    elif paged or confidence == "ambiguous":
                        kind = "AMBIGUOUS"
                        details = "兄弟候補の上限による暫定対応" if paged else "識別キーの重複、または一意に対応しない繰り返し要素"
                    elif hash_usable and not moved and x[hash_name] == y[hash_name]:
                        kind, skip = "UNCHANGED", True
                        covered = n["elements"] if options.mode == "structure" else n["end_id"] - n["id"] + 1
                        details = "SHA-256一致で省略。厳密照合を有効にすると直接比較します。"
                    else:
                        old, new = signature(x), signature(y)
                        reordered = options.mode != "value" and not options.ignore_order and positions_a[x["id"]] != positions_b[y["id"]]
                        kind = "MOVED" if moved and options.mode != "value" else "MODIFIED" if old != new or reordered else "UNCHANGED"
                        details = "同じ親の一意キーで順序変更を確認" if kind == "MOVED" else "対応ノードの属性・直接値・順序を比較"
                        aa, ab = json.loads(x["attrs"]), json.loads(y["attrs"])
                        attr = ", ".join(k for k in sorted(aa.keys() | ab.keys()) if (k in aa) != (k in ab) or options.mode != "structure" and aa.get(k) != ab.get(k))
                    info["rows"] += 1
                    info["counts"][kind] += covered
                    db.execute("INSERT INTO differences VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (comparison_id, info["rows"], kind, x["id"] if x else None, y["id"] if y else None,
                                n["path"], n["name"], attr, old[:4000], new[:4000], confidence, details, covered))
                    if x and y and x["kind"] == y["kind"] == "element" and not skip:
                        queue.append((x["id"], y["id"], 0, 0, False))
                    if len(queue) > 100000:
                        raise AppError("CANDIDATE_LIMIT: 比較の待ち行列が上限に達しました。")
                    if info["rows"] % 500 == 0:
                        db.commit()
                        task.progress("XML比較", info["rows"], 0)
            info["seconds"] = time.monotonic() - started
            info["status"] = "COMPLETED"
            db.execute("UPDATE comparisons SET info=? WHERE id=?", (json.dumps(info), comparison_id))
        return info
    except Exception:
        with store.connect() as db:
            db.execute("DELETE FROM comparisons WHERE id=?", (comparison_id,))
        raise


def search(store, file, target, query, exact=False, case_sensitive=False, task=None):
    task = task or Task(timeout=600)
    if not query:
        raise AppError("SEARCH_EMPTY: 検索文字列を入力してください。")
    needle = query if case_sensitive else query.casefold()
    results, count = [], 0
    with store.session():
        for index, n in enumerate(store.nodes(file)):
            task.check()
            attrs = json.loads(n["attrs"])
            values = {"name": [n["name"], n["local"]], "attribute-name": list(attrs),
                      "attribute-value": list(attrs.values()), "text": [n["text"]] if n["kind"] in ("text", "cdata") else [], "path": [n["path"]]}.get(target)
            if values is None:
                raise AppError("SEARCH_TARGET: 検索対象が不正です。")
            values = values if case_sensitive else [v.casefold() for v in values]
            if any(v == needle if exact else needle in v for v in values):
                count += 1
                if len(results) < 10000:
                    results.append({"id": n["id"], "name": n["name"], "path": n["path"]})
            if index % 1000 == 0:
                task.progress("XML検索", index, 0)
    return {"count": count, "results": results}
