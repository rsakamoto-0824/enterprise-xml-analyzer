"""Synthetic-only performance checks; each case uses and removes its own temporary folder."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import platform
import sys
import tempfile
import time
import tkinter as tk

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xml_analyzer.comparison import compare, search
from xml_analyzer.extraction import extract
from xml_analyzer.models import Column, DiffOptions, Template
from xml_analyzer.ml_export import save_export
from xml_analyzer.parser import parse_file
from xml_analyzer.simple_export import all_element_rows, simple_template
from xml_analyzer.storage import Store
from xml_analyzer.table_export import LONG, WIDE, save_table
from xml_analyzer.visualization import Graph
from xml_analyzer.writers import write_csv, write_xlsx


def peak_memory():
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        class Memory(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [(name, ctypes.c_size_t) for name in ("peak", "working", "paged_peak", "paged", "nonpaged_peak", "nonpaged", "pagefile", "pagefile_peak")]
        info = Memory()
        info.cb = ctypes.sizeof(info)
        kernel, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(info), info.cb):
            return None
        return info.peak
    import resource
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def generate(path, scale, changed=False):
    with path.open("wb") as file:
        file.write(b"<Root>")
        elements, group = 1, 0
        if scale == "table":
            for index in range(20000):
                file.write(f'<Item id="{index:06d}"><Value>{index}</Value></Item>'.encode())
            elements += 40000
        elif scale == "ml":
            for index in range(100000):
                file.write(f'<Item id="{index:06d}"><temperature>{20 + index % 100 / 10}</temperature><class>{index % 3}</class></Item>'.encode())
            elements += 300000
        elif scale == "million":
            for _ in range(1000):
                file.write(b"<Group>" + b"<Item/>" * 1000 + b"</Group>")
                elements += 1001
        else:
            target = int(scale) * 1048576
            padding = "x" * 4000
            while file.tell() < target:
                file.write(f'<Group id="{group}">'.encode())
                elements += 1
                for item in range(100):
                    value = "y" + padding[1:] if changed and group == 2 and item == 7 else padding
                    file.write(f'<Item id="{item}">{value}</Item>'.encode())
                    elements += 1
                file.write(b"</Group>")
                group += 1
        file.write(b"</Root>")
    return elements


def main():
    if sys.version_info[:3] != (3, 14, 6):
        raise SystemExit("Use CPython 3.14.6")
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="1,10,100,500,million,ml,table")
    parser.add_argument("--output", type=Path, default=ROOT / "output/python-performance.json")
    args = parser.parse_args()
    cases = args.cases.split(",")
    if any(c not in ("1", "10", "100", "500", "million", "ml", "table") for c in cases):
        raise SystemExit("Invalid case")
    report = {"python": platform.python_version(), "platform": platform.platform(), "architecture": platform.machine(),
              "memory_scope": "Whole process lifetime peak RSS/working set, including SQLite and Tk; cumulative across cases", "results": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for scale in cases:
        with tempfile.TemporaryDirectory(prefix="xml-python-perf-") as directory:
            base = Path(directory)
            source = base / "synthetic-a.xml"
            expected = generate(source, scale)
            store = Store(base / "work.sqlite")
            started = time.monotonic()
            result = parse_file(store, source)
            parse_seconds = time.monotonic() - started
            assert result["info"]["elements"] == expected
            root = tk.Tk()
            root.withdraw()
            try:
                graph = Graph(root, store, lambda n: None, lambda e: None)
                started = time.monotonic()
                graph.load(store.node(result["id"], 1))
                root.update_idletasks()
                graph_seconds = time.monotonic() - started
                assert len(graph.points) <= 500
            finally:
                root.destroy()
            started = time.monotonic()
            found = search(store, result["id"], "name", "Group", exact=True)
            search_seconds = time.monotonic() - started
            row = {"case": scale, "bytes": source.stat().st_size, "elements": expected,
                   "parse_seconds": parse_seconds, "commit_wait_seconds": result["info"]["database_seconds"],
                   "graph_seconds": graph_seconds, "search_seconds": search_seconds, "search_hits": found["count"],
                   "database_bytes": sum(p.stat().st_size for p in base.glob("work.sqlite*"))}
            if scale == "100":
                other = base / "synthetic-b.xml"
                generate(other, scale, changed=True)
                b = parse_file(store, other)
                started = time.monotonic()
                diff = compare(store, result["id"], b["id"], DiffOptions(trim=False, comments=True, processing_instructions=True))
                row["compare_seconds"] = time.monotonic() - started
                assert diff["counts"]["MODIFIED"] == 1
                row["comparison_counts"] = diff["counts"]
                template = Template(record_path="/Root/Group/Item", columns=[Column("@id", "id"), Column(".", "text")])
                for format, writer in (("xlsx", write_xlsx), ("csv", write_csv)):
                    started = time.monotonic()
                    saved = writer(base / ("result." + format), extract(store, [result["id"]], template), template)
                    row[format + "_seconds"] = time.monotonic() - started
                    row[format + "_rows"] = saved["rows"]
                    row[format + "_bytes"] = saved["bytes"]
            if scale in ("100", "million"):
                started = time.monotonic()
                all_rows = all_element_rows(store)
                saved = write_xlsx(base / "all-elements.xlsx", all_rows, simple_template())
                row["simple_export_seconds"] = time.monotonic() - started
                # The 100MiB comparison case has loaded both synthetic XMLs.
                expected_rows = expected * (2 if scale == "100" else 1)
                assert saved["rows"] == expected_rows
                row["simple_export_rows"] = saved["rows"]
                row["simple_export_bytes"] = saved["bytes"]
            if scale == "ml":
                started = time.monotonic()
                saved = save_export(store, base / "learning.xlsx", target="class")
                row["ml_seconds"] = time.monotonic() - started
                assert saved["records"] == 100000 and saved["features"] == 3
                row["ml_records"] = saved["records"]
                row["ml_features"] = saved["features"]
                row["ml_bytes"] = saved["bytes"]
            if scale == "table":
                for orientation, key in ((WIDE, "table_wide"), (LONG, "table_long")):
                    started = time.monotonic()
                    saved = save_table(store, base / (key + ".xlsx"), orientation)
                    row[key + "_seconds"] = time.monotonic() - started
                    assert saved["elements"] == 40001 and saved["values"] == 60001
                    assert saved["data_rows"] == (1 if orientation == WIDE else 60001)
                    if orientation == WIDE:
                        assert saved["columns"] == 60001 and saved["sheets"] == 7
                    row[key + "_rows"] = saved["data_rows"]
                    row[key + "_bytes"] = saved["bytes"]
            started = time.monotonic()
            store.clear()
            row["cleanup_seconds"] = time.monotonic() - started
            assert not store.files()
            row["peak_memory_bytes"] = peak_memory()
            report["results"].append(row)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
