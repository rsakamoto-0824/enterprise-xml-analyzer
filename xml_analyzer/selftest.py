from dataclasses import asdict
import json
from pathlib import Path
import platform
import socket
import sys
import tempfile
import time
import tkinter as tk
import zipfile

from .comparison import compare
from .extraction import extract
from .gui import Application
from .models import Column, DiffOptions, Template
from .parser import parse_file
from .writers import write_xlsx


def self_test():
    # Deny network access in the frozen smoke test as well as in source tests.
    original_connect = socket.socket.connect
    def deny(*args, **kwargs):
        raise AssertionError("Network access is not allowed")
    socket.socket.connect = deny
    root = None
    app = None
    try:
        with tempfile.TemporaryDirectory(prefix="xml-analyzer-selftest-") as directory:
            base = Path(directory)
            root = tk.Tk()
            root.withdraw()
            app = Application(root, work_dir=directory)
            a, b = base / "日本語 A.xml", base / "日本語 B.xml"
            a.write_text('<r><n id="001"><v>りんご</v></n><n id="002"><v>=1+1</v></n></r>', encoding="utf-8")
            b.write_text('<r><n id="001"><v>みかん</v></n><n id="002"><v>=1+1</v></n></r>', encoding="utf-8")
            app.import_files([str(a), str(b)])
            deadline = time.monotonic() + 30
            while app.task:
                root.update()
                if time.monotonic() > deadline:
                    raise AssertionError("GUI import did not complete")
                time.sleep(0.01)
            assert len(app.store.files()) == 2
            assert len(app.file_table.get_children()) == 2
            ids = [f["id"] for f in app.store.files()]
            result = compare(app.store, *ids, DiffOptions())
            assert result["counts"]["MODIFIED"] == 1
            template = Template(record_path="/r/n", columns=[Column("@id", "ID"), Column("v", "値")])
            workbook = base / "result.xlsx"
            exported = write_xlsx(workbook, extract(app.store, ids, template), template)
            assert exported["rows"] == 4
            with zipfile.ZipFile(workbook) as archive:
                text = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
                assert '<f>' not in text and '001' in text and 'りんご' in text
            app.set_file(ids[0])
            root.update()
            assert len(app.graph.points) > 0
            svg = base / "graph.svg"
            app.graph.export_svg(svg)
            assert '<svg' in svg.read_text(encoding="utf-8")
            info = {"status": "PASS", "python": platform.python_version(), "platform": sys.platform,
                    "architecture": platform.machine(), "tk": root.tk.call("info", "patchlevel"),
                    "frozen": bool(getattr(sys, "frozen", False)), "files": 2,
                    "modified": result["counts"]["MODIFIED"], "xlsx_rows": exported["rows"],
                    "gui": "Tk application, background import, tree and canvas", "network": "blocked"}
            app.finish_close()
            app, root = None, None
            return info
    finally:
        socket.socket.connect = original_connect
        if app:
            if app.task:
                app.task.cancel.set()
                app.worker.join(timeout=30)
            app.finish_close()
        elif root:
            root.destroy()
