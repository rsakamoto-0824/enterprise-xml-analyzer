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
