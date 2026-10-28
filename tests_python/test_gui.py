import ctypes
import json
from pathlib import Path
import struct
import sqlite3
import sys
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
import zipfile

from xml_analyzer.gui import Application
from xml_analyzer.models import Column, Template
from xml_analyzer.windows import install_file_drop


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="xml-gui-test-")
        self.base = Path(self.temporary.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = Application(self.root, work_dir=self.temporary.name)
        self.root.update()

    def tearDown(self):
        if self.app.task:
            self.app.cancel()
            self.wait()
        work = self.app.temporary.name
        self.app.finish_close()
        self.assertFalse(Path(work).exists())
        self.temporary.cleanup()

    def wait(self):
        deadline = time.monotonic() + 30
        while self.app.task:
            self.root.update()
            if time.monotonic() > deadline:
                self.fail("GUI worker timed out")
            time.sleep(0.01)
        self.root.update()

    def load(self, content='<r><n id="001"><v>日本語</v><v>B</v></n></r>', name="日本語.xml"):
        path = self.base / name
        path.write_text(content, encoding="utf-8")
        self.app.import_files([str(path)])
        self.wait()
        self.assertIsInstance(self.app.last_result, list)
        return self.app.files[-1]["id"]

    def test_tree_record_columns_preview_template_and_native_save(self):
        file_id = self.load()
        n = self.app.store.node(file_id, 2)
        self.app.jump_to_node(n)
        self.assertEqual(self.app.selected["id"], 2)
        self.assertIn('001', self.app.detail.get("1.0", "end"))
        self.app.use_record()
        self.wait()
        self.assertEqual(self.app.record_path.get(), "/r/n")
        self.assertEqual([c.path for c in self.app.template.columns], ["@id", "v"])
        self.app.column_table.selection_set("1")
        self.app.move_column(-1)
        self.assertEqual(self.app.template.columns[0].path, "v")
        self.app.start_preview()
        self.wait()
        self.assertIn("日本語 | B", self.app.preview_text.get("1.0", "end"))
        template_path = self.base / "template.json"
        with patch("xml_analyzer.gui.filedialog.asksaveasfilename", return_value=str(template_path)):
            self.app.save_template()
        self.assertEqual(json.loads(template_path.read_text(encoding="utf-8"))["version"], 2)
        self.app.apply_template(Template(columns=[Column(".", "changed")]))
        with patch("xml_analyzer.gui.filedialog.askopenfilename", return_value=str(template_path)):
            self.app.load_template()
        self.assertEqual(self.app.template.columns[0].path, "v")
        target = self.base / "出力.xlsx"
        with patch("xml_analyzer.gui.filedialog.asksaveasfilename", return_value=str(target)):
            self.app.export_output("xlsx")
        self.wait()
        self.assertTrue(target.is_file())

    def test_simple_export_uses_every_file_and_ignores_detailed_settings(self):
        self.load('<r><n id="001">=1+1</n><empty/></r>', 'a.xml')
        self.load('<root><value>日本語</value></root>', 'b.xml')
        self.app.template = Template(record_path="/does-not-exist", columns=[], filters=[{"path": ".", "op": "equals", "value": "no-match"}])
        original = self.app.template.to_json()
        self.app.export_files.selection_clear(0, "end")
        self.app.export_files.selection_set(1)
        target = self.base / "全要素"
        with patch("xml_analyzer.gui.filedialog.asksaveasfilename", return_value=str(target)):
            self.app.all_elements_button.invoke()
        self.assertIsNotNone(self.app.task)
        self.wait()
        self.assertEqual(self.app.template.to_json(), original)
        self.assertIn("2ファイル・5要素・5行", self.app.status.get())
        with zipfile.ZipFile(target.with_suffix('.xlsx')) as z:
            tree = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
            self.assertEqual(len(tree.findall('.//{*}row')), 6)
            self.assertEqual(tree.findall('.//{*}f'), [])
            text = z.read("xl/worksheets/sheet1.xml").decode()
            for value in ('a.xml', 'b.xml', '001', '=1+1', '日本語', 'empty'):
                self.assertIn(value, text)

    def test_simple_export_requires_xml_and_allows_save_dialog_cancel(self):
        with patch("xml_analyzer.gui.filedialog.asksaveasfilename") as choose:
            self.app.export_all_elements()
            choose.assert_not_called()
        self.assertIn("FILES_EMPTY", self.app.status.get())
        self.load()
        with patch("xml_analyzer.gui.filedialog.asksaveasfilename", return_value=""):
            self.app.export_all_elements()
        self.assertIsNone(self.app.task)

    def test_paging_tree_graph_svg_and_search_jump(self):
        file_id = self.load('<r>' + '<n>value</n>' * 600 + '</r>')
        self.assertLessEqual(len(self.app.tree_nodes), 251)
        self.assertEqual(len(self.app.more_nodes), 1)
        marker = next(iter(self.app.more_nodes))
        self.app.tree.focus(marker)
        self.app.activate_more()
        self.assertEqual(len(self.app.tree_nodes), 501)
        self.assertLessEqual(len(self.app.graph.points), 500)
        svg = self.base / "graph.svg"
        self.app.graph.export_svg(svg)
        self.assertIn('<svg', svg.read_text(encoding="utf-8"))
        self.app.search_text.set("n")
        self.app.search_exact.set(True)
        self.app.start_search()
        self.wait()
        self.assertIn("600", self.app.status.get())
        iid = self.app.search_results.get_children()[0]
        self.app.search_results.selection_set(iid)
        self.app.search_selected()
        self.assertEqual(self.app.selected["name"], "n")

    def test_compare_filter_jump_and_clear_releases_previews(self):
        self.load('<r><n id="001">old</n></r>', "a.xml")
        self.load('<r><n id="001">new</n></r>', "b.xml")
        labels = list(self.app.file_labels)
        self.app.compare_a.set(labels[0])
        self.app.compare_b.set(labels[1])
        self.app.start_compare()
        self.wait()
        self.assertEqual(self.app.comparison["counts"]["MODIFIED"], 1)
        self.app.diff_filter.set("変更")
        self.app.show_differences(reset=True)
        self.assertEqual(len(self.app.differences.get_children()), 1)
        self.app.differences.selection_set(self.app.differences.get_children()[0])
        self.app.jump_difference("b")
        self.assertEqual(self.app.selected["text"], "new")
        self.app.search_text.set("PRIVATE_QUERY")
        self.app.filter_value.set("PRIVATE_FILTER")
        self.app.clear_workspace()
        self.wait()
        self.assertEqual(self.app.files, [])
        self.assertEqual(self.app.template.columns, [])
        self.assertIsNone(self.app.graph.root_node)
        self.assertIsNone(self.app.graph.document_root)
        self.assertEqual(self.app.detail.get("1.0", "end").strip(), "")
        self.assertEqual(self.app.preview_text.get("1.0", "end").strip(), "")
        self.assertEqual(self.app.search_text.get(), "")
        self.assertEqual(self.app.filter_value.get(), "")

    def test_worker_disables_controls_and_cancel_cleans_database(self):
        path = self.base / "cancel.xml"
        path.write_text("<r>" + "<n/>" * 100000 + "</r>")
        self.app.import_files([str(path)])
        self.assertIn("disabled", self.app.file_picker.state())
        self.app.cancel()
        self.wait()
        self.assertEqual(self.app.files, [])
        self.assertNotIn("disabled", self.app.file_picker.state())
        self.assertIn("CANCELLED", self.app.status.get())

    def test_storage_failure_does_not_stop_the_gui_event_pump(self):
        self.root.after_cancel(self.app.poll_id)
        self.app.messages.put(("done", ("test", None, None, time.monotonic())))
        with patch.object(self.app.store, "files", side_effect=sqlite3.OperationalError("disk I/O error")):
            self.app.poll()
        self.assertIn("PROCESS_ERROR", self.app.status.get())
        self.assertIsNotNone(self.app.poll_id)
        self.root.update()

    def test_export_actions_remain_reachable_on_a_small_screen(self):
        self.root.geometry("1000x650+0+0")
        self.root.deiconify()
        self.app.tabs.select(self.app.pages["Excel出力"])
        self.root.update()
        self.app.pages["Excel出力"].reveal(self.app.export_scroll)
        self.app.export_scroll.yview_moveto(1)
        self.root.update()
        def find(widget):
            for child in widget.winfo_children():
                if isinstance(child, ttk.Button) and child.cget("text") == "XLSX保存":
                    return child
                result = find(child)
                if result:
                    return result
        from tkinter import ttk
        button = find(self.app.pages["Excel出力"])
        self.assertIsNotNone(button)
        top = self.app.export_scroll.winfo_rooty()
        self.assertGreaterEqual(button.winfo_rooty(), top)
        self.assertLess(button.winfo_rooty() + button.winfo_height(), top + self.app.export_scroll.winfo_height())
        self.assertLessEqual(self.app.cancel_button.winfo_rooty() + self.app.cancel_button.winfo_height(), self.root.winfo_rooty() + self.root.winfo_height())
        self.app.pages["Excel出力"].reveal(self.app.simple_export_button)
        self.assertGreater(self.app.simple_export_button.winfo_rooty(), self.app.pages["Excel出力"].canvas.winfo_rooty())

    def test_ml_preview_override_target_and_save_ignores_detailed_settings(self):
        self.load('<r><n id="001"><x>1</x><y>A</y></n><n id="002"><x>2</x><y>B</y></n></r>')
        self.load('<r><n id="003"><x>3</x><y>A</y></n></r>', 'b.xml')
        self.app.template = Template(record_path="/wrong", columns=[])
        self.app.preview_ml()
        self.wait()
        self.assertEqual(self.app.ml_record_path.get(), "/r/n")
        self.assertIn("/r/n", self.app.ml_record_choice.cget("values"))
        self.assertEqual(len(self.app.ml_preview.get_children()), 3)
        self.assertIn("y", self.app.ml_target.cget("values"))
        self.app.ml_target.set("y")
        target = self.base / "学習データ.xlsx"
        with patch("xml_analyzer.gui.filedialog.asksaveasfilename", return_value=str(target)):
            self.app.ml_export_button.invoke()
        self.wait()
        self.assertEqual(self.app.last_result["records"], 3)
        self.assertIn("3件・3項目", self.app.status.get())
        self.assertTrue(target.exists())
        self.app.ml_record_path.set(self.app.ml_file_unit)
        self.app.preview_ml()
        self.wait()
        self.assertEqual(len(self.app.ml_preview.get_children()), 2)
        self.assertEqual(self.app.ml_target.get(), "未指定")
        self.app.clear_workspace()
        self.wait()
        self.assertEqual(self.app.ml_target.get(), "未指定")
        self.assertEqual(self.app.ml_preview.get_children(), ())

    def test_all_table_choice_preserves_outside_values_and_ignores_selection(self):
        from xml_analyzer.table_export import LONG, WIDE
        self.load('<r><n id="001"><x>20</x><x>30</x></n><outside> A </outside></r>')
        self.load('<different><empty/></different>', 'b.xml')
        self.app.template = Template(record_path="/wrong", columns=[])
        original = self.app.template.to_json()
        self.app.export_files.selection_clear(0, 'end')
        self.app.ml_record_path.set('/r/n')
        for orientation, rows in ((WIDE, 3), (LONG, 6)):
            self.app.table_orientation.set(orientation)
            self.app.table_orientation.event_generate('<<ComboboxSelected>>')
            self.assertEqual(self.app.table_preview.get_children(), ())
            self.app.preview_all_table()
            self.wait()
            self.assertEqual(len(self.app.table_preview.get_children()), rows)
            target = self.base / orientation
            with patch('xml_analyzer.gui.filedialog.asksaveasfilename', return_value=str(target)):
                self.app.simple_export_button.invoke()
            self.wait()
            self.assertEqual(self.app.last_result['elements'], 7)
            self.assertEqual(self.app.last_result['values'], 8)
            self.assertEqual(self.app.last_result['data_rows'], rows)
            self.assertEqual(self.app.template.to_json(), original)
            with zipfile.ZipFile(target.with_suffix('.xlsx')) as archive:
                text = archive.read('xl/worksheets/sheet1.xml').decode()
                for value in ('001', '20', '30', ' A ', 'outside', 'empty'):
                    self.assertIn(value, text)
                self.assertNotIn('<f>', text)
        self.app.clear_workspace()
        self.wait()
        self.assertEqual(self.app.table_preview.get_children(), ())

    def test_header_ranges_preview_links_settings_and_clear(self):
        file = self.load('<r><h><id>001</id></h><n><header>local</header><v>20</v></n><n><v>30</v></n></r>')
        header = next(self.app.store.nodes(file, path='/r/h'))
        self.app.jump_to_node(header)
        self.app.add_header_range()
        self.app.header_paths.insert('end', '\n/r/n/header')
        self.root.update()
        self.app.preview_all_table()
        self.assertEqual(self.app.header_paths.cget('state'), 'disabled')
        self.wait()
        self.assertEqual(self.app.header_paths.cget('state'), 'normal')
        self.assertEqual(self.app.last_result['data_records'], 2)
        self.assertEqual(self.app.last_result['header_groups'], 2)
        self.assertEqual(len(self.app.table_preview.get_children()), 2)
        for view, rows in (('ヘッダー情報', 3), ('紐づけ', 3), ('中身のデータ', 2), ('共通データ', 1)):
            self.app.table_view.set(view)
            self.app.table_view.event_generate('<<ComboboxSelected>>')
            self.assertEqual(len(self.app.table_preview.get_children()), rows)
        settings = self.base / 'simple.json'
        with patch('xml_analyzer.gui.filedialog.asksaveasfilename', return_value=str(settings)):
            self.app.save_table_settings()
        content = json.loads(settings.read_text())
        self.assertEqual(content['header_paths'], ['/r/h', '/r/n/header'])
        self.assertNotIn('001', settings.read_text())
        self.app.clear_header_ranges()
        with patch('xml_analyzer.gui.filedialog.askopenfilename', return_value=str(settings)):
            self.app.load_table_settings()
        self.assertEqual(self.app.header_paths.get('1.0', 'end').strip(), '/r/h\n/r/n/header')
        settings.write_text('{"type":"wrong"}')
        original = self.app.header_paths.get('1.0', 'end')
        with patch('xml_analyzer.gui.filedialog.askopenfilename', return_value=str(settings)):
            self.app.load_table_settings()
        self.assertEqual(self.app.header_paths.get('1.0', 'end'), original)
        self.assertIn('TABLE_SETTINGS', self.app.status.get())
        for orientation in ('横持ち', '縦持ち'):
            self.app.table_orientation.set(orientation)
            target = self.base / (orientation + '.xlsx')
            with patch('xml_analyzer.gui.filedialog.asksaveasfilename', return_value=str(target)):
                self.app.export_table()
            self.wait()
            self.assertEqual(self.app.last_result['header_groups'], 2)
            self.assertEqual(self.app.last_result['data_records'], 2)
            with zipfile.ZipFile(target) as archive:
                text = archive.read('xl/workbook.xml').decode()
                self.assertIn('ヘッダー', text)
                self.assertIn('紐づけ', text)
        self.app.clear_workspace()
        self.wait()
        self.assertEqual(self.app.header_paths.get('1.0', 'end').strip(), '')
        self.assertIsNone(self.app.table_preview_result)

    def test_resize_all_pages_and_wrapped_buttons_keep_state(self):
        from tkinter import ttk
        from xml_analyzer.layout import FlowFrame
        self.load()
        self.app.ml_target.set("remember-me")
        self.root.deiconify()
        def visit(widget):
            yield widget
            for child in widget.winfo_children():
                yield from visit(child)
        for width, height in ((640, 480), (800, 600), (1000, 650), (1440, 1000), (640, 480)):
            self.root.geometry(f"{width}x{height}+0+0")
            self.root.update()
            self.assertEqual(bool(self.app.navigation.winfo_ismapped()), width < 900)
            for name, page in self.app.pages.items():
                self.app.page_choice.set(name)
                self.app.page_choice.event_generate("<<ComboboxSelected>>")
                self.root.update()
                self.assertEqual(self.app.tabs.select(), str(page))
                self.assertLessEqual(page.winfo_width(), self.root.winfo_width())
                self.assertGreater(page.canvas.winfo_height(), 30)
                for bar in (w for w in visit(page) if isinstance(w, FlowFrame)):
                    for child in bar.winfo_children():
                        self.assertGreaterEqual(child.winfo_x(), 0)
                        self.assertLessEqual(child.winfo_x() + child.winfo_width(), bar.winfo_width() + 1)
                        self.assertLessEqual(child.winfo_y() + child.winfo_height(), bar.winfo_height())
            self.assertLessEqual(self.app.cancel_button.winfo_rooty() + self.app.cancel_button.winfo_height(), self.root.winfo_rooty() + self.root.winfo_height())
            self.assertEqual(self.app.ml_target.get(), "remember-me")

    @unittest.skipUnless(sys.platform == "win32", "native WM_DROPFILES is Windows only")
    def test_windows_native_drop_unicode_paths(self):
        from ctypes import wintypes
        received = []
        remove = install_file_drop(self.root, received.extend)
        self.addCleanup(remove)
        paths = [str(self.base / "日本語 space.xml"), str(self.base / "long name.xml")]
        encoded = ("\0".join(paths) + "\0\0").encode("utf-16-le")
        payload = struct.pack("<IiiII", 20, 0, 0, 0, 1) + encoded
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        user = ctypes.WinDLL("user32", use_last_error=True)
        kernel.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel.GlobalLock.restype = ctypes.c_void_p
        kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user.GetAncestor.restype = wintypes.HWND
        user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user.SendMessageW.restype = ctypes.c_ssize_t
        handle = kernel.GlobalAlloc(0x0042, len(payload))
        address = kernel.GlobalLock(handle)
        ctypes.memmove(address, payload, len(payload))
        kernel.GlobalUnlock(handle)
        user.SendMessageW(user.GetAncestor(self.root.winfo_id(), 2), 0x0233, handle, 0)
        self.root.update()
        self.assertEqual(received, paths)
        remove()
        self.doCleanups()


if __name__ == "__main__":
    unittest.main()
