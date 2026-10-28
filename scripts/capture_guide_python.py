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
            app.preview_ml()
            wait()
            assert app.ml_record_path.get() == "/catalog/item"
            app.ml_target.set("price")
            target = Path(folder) / "learning.xlsx"
            with patch("xml_analyzer.gui.filedialog.asksaveasfilename", return_value=str(target)):
                app.export_ml()
            wait()
            assert app.last_result["records"] == 6 and app.last_result["features"] == 4
            app.tabs.select(app.pages["Excel出力"])
            root.update()
            app.pages["Excel出力"].reveal(app.ml_export_button)
            capture("14-learning-export", "Excel出力")
            for orientation in ("縦持ち", "横持ち"):
                app.table_orientation.set(orientation)
                app.preview_all_table()
                wait()
                target = Path(folder) / (orientation + ".xlsx")
                with patch("xml_analyzer.gui.filedialog.asksaveasfilename", return_value=str(target)):
                    app.simple_export_button.invoke()
                wait()
                assert app.last_result["elements"] == 26 and app.last_result["values"] == 32
                assert app.last_result["data_rows"] == (30 if orientation == "縦持ち" else 6)
                if orientation == "横持ち":
                    assert app.last_result["columns"] == 5
                app.pages["Excel出力"].reveal(app.table_preview)
                capture("13-long-export" if orientation == "縦持ち" else "11-simple-export", "Excel出力")
            root.geometry("800x600+0+0")
            root.update()
            app.pages["Excel出力"].reveal(app.simple_export_button)
            capture("12-resize", "Excel出力")
            root.geometry(f"{width}x{height}+0+0")
            app.pages["Excel出力"].canvas.yview_moveto(0)
            root.update()
        before = next(f["id"] for f in app.files if f["name"] == "sample-before.xml")
        app.set_file(before)
        item = next(n for n in app.store.nodes(before, path="/catalog/item"))
        app.jump_to_node(item)
        app.detail.yview_moveto(1)
        capture("03-tree", "構造ビュー")
        app.search_text.set("price")
        app.search_exact.set(True)
        app.start_search()
        wait()
        assert len(app.search_results.get_children()) == 3
        app.pages["構造ビュー"].canvas.yview_moveto(1)
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
        app.pages["XML比較"].canvas.yview_moveto(1)
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
        app.pages["Excel出力"].reveal(app.export_scroll)
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
        app.clear_workspace()
        wait()
        app.import_files([str(SAMPLES / 'sample-with-headers.xml')])
        wait()
        app.header_paths.insert('1.0', '/root/header\n/root/record/header')
        root.update()
        app.tabs.select(app.pages['Excel出力'])
        app.pages['Excel出力'].canvas.yview_moveto(0)
        root.update()
        capture('15-header-ranges', 'Excel出力')
        for orientation, name in (('横持ち', '16-header-wide'), ('縦持ち', '19-header-long')):
            app.table_orientation.set(orientation)
            app.table_view.set('中身のデータ')
            app.preview_all_table()
            wait()
            assert app.last_result['data_records'] == 2 and app.last_result['header_groups'] == 3
            app.pages['Excel出力'].reveal(app.table_preview)
            capture(name, 'Excel出力')
        for view, name in (('ヘッダー情報', '17-header-info'), ('紐づけ', '18-header-links')):
            app.table_view.set(view)
            app.table_view.event_generate('<<ComboboxSelected>>')
            root.update()
            assert len(app.table_preview.get_children()) == (7 if view == 'ヘッダー情報' else 4)
            capture(name, 'Excel出力')
        with tempfile.TemporaryDirectory(prefix='xml-guide-headers-') as folder:
            with patch('xml_analyzer.gui.filedialog.asksaveasfilename', return_value=str(Path(folder) / 'linked.xlsx')):
                app.export_table()
            wait()
            assert app.last_result['header_groups'] == 3 and app.last_result['values'] == 17
        (OUTPUT / "capture.json").write_text(json.dumps({
            "platform": platform.platform(), "python": platform.python_version(),
            "tk": root.tk.call("info", "patchlevel"), "screenshots": captured,
            "sample_rows": 3, "all_element_rows": 26, "ml_records": 6, "ml_features": 4,
            "table_elements": 26, "table_values": 32, "table_wide_rows": 6, "table_wide_columns": 5, "table_long_rows": 30,
            "header_sample_records": 2, "header_sample_groups": 3, "header_sample_values": 17, "header_sample_links": 4,
            "resize": "800x600", "comparison_counts": counts,
            "note": "Unedited real Tk application captures; synthetic samples only. Save chooser substituted, actual XLSX writer executed."
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        if app.task:
            app.cancel()
            wait()
        app.finish_close()


if __name__ == "__main__":
    main()
