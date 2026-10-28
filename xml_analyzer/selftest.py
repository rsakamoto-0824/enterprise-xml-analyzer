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
from unittest.mock import patch

from .comparison import compare
from .extraction import extract
from .gui import Application
from .models import Column, DiffOptions, Template
from .parser import parse_file
from .writers import write_xlsx
from .visualization import save_windows_canvas_png


def self_test(screenshot_dir=None):
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
            simple_workbook = base / "all-elements.xlsx"
            with patch("xml_analyzer.gui.filedialog.asksaveasfilename", return_value=str(simple_workbook)):
                app.export_all_elements()
            deadline = time.monotonic() + 30
            while app.task:
                root.update()
                if time.monotonic() > deadline:
                    raise AssertionError("Simple export did not complete")
                time.sleep(0.01)
            assert app.last_result["rows"] == 10
            with zipfile.ZipFile(simple_workbook) as archive:
                text = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
                assert '<f>' not in text and '001' in text and '全要素' in archive.read('xl/workbook.xml').decode()
            app.preview_ml()
            deadline = time.monotonic() + 30
            while app.task:
                root.update()
                assert time.monotonic() < deadline, "ML preview timed out"
                time.sleep(0.01)
            assert app.ml_record_path.get() == "/r/n"
            assert len(app.ml_preview.get_children()) == 4
            app.ml_target.set("v")
            learning_workbook = base / "learning.xlsx"
            with patch("xml_analyzer.gui.filedialog.asksaveasfilename", return_value=str(learning_workbook)):
                app.ml_export_button.invoke()
            deadline = time.monotonic() + 30
            while app.task:
                root.update()
                assert time.monotonic() < deadline, "ML save timed out"
                time.sleep(0.01)
            assert app.last_result["records"] == 4 and app.last_result["features"] == 2
            with zipfile.ZipFile(learning_workbook) as archive:
                assert archive.read("xl/workbook.xml").decode().count('<sheet ') == 4
                assert '<f>' not in archive.read("xl/worksheets/sheet1.xml").decode()
            app.header_paths.insert("1.0", "/r/n/v")
            for orientation, rows in (("縦持ち", 8), ("横持ち", 4)):
                app.table_orientation.set(orientation)
                app.preview_all_table()
                deadline = time.monotonic() + 30
                while app.task:
                    root.update()
                    assert time.monotonic() < deadline, "Whole table preview timed out"
                    time.sleep(0.01)
                assert len(app.table_preview.get_children()) == rows
                with patch("xml_analyzer.gui.filedialog.asksaveasfilename", return_value=str(base / (orientation + ".xlsx"))):
                    app.simple_export_button.invoke()
                deadline = time.monotonic() + 30
                while app.task:
                    root.update()
                    assert time.monotonic() < deadline, "Whole table save timed out"
                    time.sleep(0.01)
                assert app.last_result["elements"] == 10 and app.last_result["values"] == 14
                assert app.last_result["data_rows"] == rows
                if orientation == "横持ち":
                    assert app.last_result["columns"] == 2
                assert app.last_result["header_groups"] == 4 and app.last_result["header_values"] == 4
            app.set_file(ids[0])
            root.update()
            assert len(app.graph.points) > 0
            svg = base / "graph.svg"
            app.graph.export_svg(svg)
            assert '<svg' in svg.read_text(encoding="utf-8")
            screenshots = []
            if sys.platform == "win32" and screenshot_dir:
                root.deiconify()
                app.jump_to_node(app.store.node(ids[0], 2))
                app.tabs.select(app.pages["構造ビュー"])
                root.update()
                for name, widget in (("python-selftest-window.png", root), ("python-selftest-graph.png", app.graph.canvas)):
                    if widget is app.graph.canvas:
                        app.tabs.select(app.pages["グラフ"])
                        root.update()
                    destination = Path(screenshot_dir) / name
                    save_windows_canvas_png(widget, destination)
                    assert destination.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
                    screenshots.append(name)
                app.tabs.select(app.pages["Excel出力"])
                app.pages["Excel出力"].canvas.yview_moveto(0)
                root.update()
                name = "python-selftest-simple.png"
                save_windows_canvas_png(root, Path(screenshot_dir) / name)
                screenshots.append(name)
                root.geometry("640x480+0+0")
                root.update()
                assert app.navigation.winfo_ismapped()
                app.pages["Excel出力"].reveal(app.simple_export_button)
                root.update()
                name = "python-selftest-resize.png"
                save_windows_canvas_png(root, Path(screenshot_dir) / name)
                screenshots.append(name)
            info = {"status": "PASS", "python": platform.python_version(), "platform": sys.platform,
                    "architecture": platform.machine(), "tk": root.tk.call("info", "patchlevel"),
                    "frozen": bool(getattr(sys, "frozen", False)), "files": 2,
                    "modified": result["counts"]["MODIFIED"], "xlsx_rows": exported["rows"], "all_element_rows": 10,
                    "ml_records": 4, "ml_features": 2, "ml_sheets": 4, "minimum_window": "640x480",
                    "table_elements": 10, "table_values": 14, "table_wide_rows": 4, "table_wide_columns": 2, "table_long_rows": 8, "table_header_groups": 4, "table_header_values": 4,
                    "gui": "Tk application, background import, tree and canvas", "network": "blocked", "screenshots": screenshots}
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
