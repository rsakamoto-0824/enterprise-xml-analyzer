"""Capture genuine Windows app screens using only synthetic training XML."""
import json
from pathlib import Path
import platform
import sys
import tempfile
import time
import tkinter as tk
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xml_analyzer.gui import Application
from xml_analyzer.visualization import save_windows_canvas_png

OUTPUT = ROOT / "artifacts/python-guide"
SAMPLES = ROOT / "docs/python-user-guide/samples"
TIMEOUT_SECONDS = 30


def main():
    if sys.platform != "win32" or sys.version_info[:3] != (3, 14, 6):
        raise SystemExit("Capture requires Windows and Python 3.14.6.")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    app = Application(root)
    width, height = min(1280, root.winfo_screenwidth() - 40), min(900, root.winfo_screenheight() - 80)
    root.geometry(f"{width}x{height}+0+0")
    captured = []

    def wait():
        deadline = time.monotonic() + TIMEOUT_SECONDS
        while app.task:
            root.update()
            if time.monotonic() > deadline:
                raise AssertionError("Guide operation timed out")
            time.sleep(0.01)
        root.update()
        if isinstance(app.last_result, Exception):
            raise app.last_result

    def capture(name, tab):
        app.tabs.select(app.pages[tab])
        root.update()
        root.lift()
        root.update()
        save_windows_canvas_png(root, OUTPUT / (name + ".png"))
        captured.append(name + ".png")

    try:
        capture("01-start", "XML読込")
        app.import_files([str(SAMPLES / name) for name in ("sample-before.xml", "sample-after.xml")])
        wait()
        capture("02-loaded", "XML読込")
        with tempfile.TemporaryDirectory(prefix="xml-guide-simple-") as folder:
            target = Path(folder) / "all-elements.xlsx"
            with patch("xml_analyzer.gui.filedialog.asksaveasfilename", return_value=str(target)):
                app.export_all_elements()
            wait()
            assert app.last_result["rows"] == 26
            capture("11-simple-export", "Excel出力")
        before = next(f["id"] for f in app.files if f["name"] == "sample-before.xml")
        app.set_file(before)
        item = next(n for n in app.store.nodes(before, path="/catalog/item"))
        app.jump_to_node(item)
        capture("03-tree", "構造ビュー")
        app.search_text.set("price")
        app.search_exact.set(True)
        app.start_search()
        wait()
        assert len(app.search_results.get_children()) == 3
        capture("04-search", "構造ビュー")
        app.jump_to_node(item)
        app.focus_graph()
        capture("05-graph", "グラフ")
        app.start_compare()
        wait()
        app.diff_filter.set("変更")
        app.show_differences(reset=True)
        first = app.differences.get_children()[0]
        app.differences.selection_set(first)
        app.show_diff_detail()
        capture("06-compare", "XML比較")
        counts = app.comparison["counts"]
        app.jump_to_node(item)
        app.use_record()
        wait()
        app.export_files.selection_clear(0, "end")
        app.export_files.selection_set(0)
        for index, column in enumerate(app.template.columns):
            app.column_table.selection_set(str(index))
            app.edit_column()
            app.column_fields["name"].set({"@id": "商品ID", "name": "商品名", "price": "単価", "stock": "在庫数"}[column.path])
            app.column_fields["type"].set("number" if column.path in ("price", "stock") else "string")
            app.apply_column()
        app.export_scroll.yview_moveto(0)
        capture("07-columns", "Excel出力")
        app.start_preview()
        wait()
        assert "先頭3行" in app.preview_text.get("1.0", "end")
        app.export_scroll.yview_moveto(1)
        capture("08-preview", "Excel出力")
        with tempfile.TemporaryDirectory(prefix="xml-guide-output-") as folder:
            target = Path(folder) / "sample-export.xlsx"
            # Only the chooser is substituted; generation uses the real UI action and writer.
            with patch("xml_analyzer.gui.filedialog.asksaveasfilename", return_value=str(target)):
                app.export_output("xlsx")
            wait()
            assert target.is_file()
            capture("09-saved", "Excel出力")
        capture("10-settings", "設定")
        (OUTPUT / "capture.json").write_text(json.dumps({
            "platform": platform.platform(), "python": platform.python_version(),
            "tk": root.tk.call("info", "patchlevel"), "screenshots": captured,
            "sample_rows": 3, "simple_export_rows": 26, "comparison_counts": counts,
            "note": "Unedited real Tk application captures; synthetic samples only. Save chooser substituted, actual XLSX writer executed."
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        if app.task:
            app.cancel()
            wait()
        app.finish_close()


if __name__ == "__main__":
    main()
