from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import Mock

from xml_analyzer.windows import _FileDrop, WM_DROPFILES, WM_NCDESTROY


class FileDropQueueTests(unittest.TestCase):
    def setUp(self):
        self.native = False
        self.callbacks = {}
        self.errors = []
        self.root = Mock()
        self.root._xml_file_drops = {}
        def after(delay, callback):
            self.assertFalse(self.native, 'Tk scheduling inside a native callback')
            identifier = str(len(self.callbacks))
            self.callbacks[identifier] = callback
            return identifier
        self.root.after.side_effect = after
        self.root.report_callback_exception.side_effect = lambda *args: self.errors.append(args[1])
        self.api = Mock()
        self.api.handler_type.side_effect = lambda callback: callback
        self.api.install.return_value = True
        self.api.remove.return_value = True
        self.api.forward.return_value = 17
        self.api.read_paths.return_value = ['日本語 😀 space.xml', 'braces {A}.xml']
        self.received = []
        self.bridge = _FileDrop(self.root, self.received.extend, self.api, 1 << 40)
        self.addCleanup(self.bridge.close)

    def dispatch(self, message=WM_DROPFILES):
        self.native = True
        try:
            return self.bridge.dispatch(1 << 40, message, 23, 0, self.bridge.identifier, 0)
        finally:
            self.native = False

    def test_native_callback_releases_handle_and_never_calls_tk_or_import(self):
        self.root.after.reset_mock()
        self.assertEqual(self.dispatch(), 0)
        self.assertEqual(self.received, [])
        self.root.after.assert_not_called()
        self.root.report_callback_exception.assert_not_called()
        self.api.finish.assert_called_once_with(23)
        self.bridge.drain()
        self.assertEqual(self.received, ['日本語 😀 space.xml', 'braces {A}.xml'])

    def test_native_and_import_errors_are_reported_only_from_tk_timer(self):
        error = OSError('drop query failed')
        self.api.read_paths.side_effect = error
        self.assertEqual(self.dispatch(), 0)
        self.assertEqual(self.errors, [])
        self.api.finish.assert_called_once_with(23)
        self.bridge.drain()
        self.assertEqual(self.errors, [error])
        self.api.read_paths.side_effect = None
        self.bridge.callback = Mock(side_effect=ValueError('bad callback'))
        self.dispatch()
        self.bridge.drain()
        self.assertIsInstance(self.errors[-1], ValueError)
        self.assertIsNotNone(self.bridge.timer)

    def test_close_discards_pending_work_and_is_idempotent(self):
        self.dispatch()
        self.bridge.close()
        self.bridge.close()
        self.bridge.drain()
        self.assertEqual(self.received, [])
        self.api.remove.assert_called_once()
        self.root.after_cancel.assert_called_once()
        self.assertEqual(self.root._xml_file_drops, {})

    def test_subclass_cleanup_preserves_another_installed_drop_handler(self):
        second = _FileDrop(self.root, self.received.extend, self.api, 1 << 40)
        self.api.accept.reset_mock()
        self.bridge.close()
        self.api.accept.assert_not_called()
        second.close()
        self.api.accept.assert_called_once_with(1 << 40, False)

    def test_native_destroy_forwards_without_tk_and_stops_pending_callbacks(self):
        self.dispatch()
        self.assertEqual(self.dispatch(WM_NCDESTROY), 17)
        self.api.remove.assert_called_once()
        self.bridge.drain()
        self.assertEqual(self.received, [])
        self.bridge.close()
        self.assertEqual(self.root._xml_file_drops, {})


@unittest.skipUnless(sys.platform == 'win32', 'native posted drops require Windows')
class PostedDropTests(unittest.TestCase):
    def test_posted_drop_survives_mainloop_resize_and_repeated_imports(self):
        # A fatal native callback cannot be caught by unittest in the same process.
        driver = textwrap.dedent('''
            import gc
            from pathlib import Path
            import sys
            import time
            import tkinter as tk
            from xml_analyzer.gui import Application
            from xml_analyzer.models import AppError
            from xml_analyzer.selftest import post_windows_drop

            base = Path(sys.argv[1])
            paths = [base / name for name in ('日本語 😀 space.xml', 'braces {A}.xml', 'third.xml')]
            for path in paths:
                path.write_text('<r><n>value</n></r>', encoding='utf-8')
            broken = base / 'broken.xml'
            broken.write_text('<r>', encoding='utf-8')
            root = tk.Tk()
            app = Application(root, work_dir=base)
            failures = []
            root.report_callback_exception = lambda kind, value, trace: (failures.append(kind.__name__), root.quit())
            deadline = time.monotonic() + 15
            stage = 0

            def check():
                global stage
                if time.monotonic() > deadline:
                    failures.append('timeout')
                    root.quit()
                    return
                if not app.task and len(app.files) == 1 and stage == 0:
                    stage = 1
                    gc.collect()
                    root.geometry('900x650')
                    post_windows_drop(root, paths[1:])
                elif not app.task and len(app.files) == 3 and stage == 1:
                    stage = 2
                    root.withdraw()
                    root.deiconify()
                    gc.collect()
                    post_windows_drop(root, paths[:1])
                elif not app.task and len(app.files) == 4 and stage == 2:
                    stage = 3
                    post_windows_drop(root, [broken])
                elif not app.task and isinstance(app.last_result, AppError) and stage == 3:
                    assert len(app.files) == 4
                    stage = 4
                    post_windows_drop(root, [paths[0]] * 17)
                elif stage == 4 and 'QUEUE_LIMIT' in app.status.get():
                    assert len(app.files) == 4
                    stage = 5
                    post_windows_drop(root, paths[:1])
                elif not app.task and len(app.files) == 5 and stage == 5:
                    stage = 6
                    root.quit()
                    return
                root.after(20, check)

            root.after(50, lambda: post_windows_drop(root, paths[:1]))
            root.after(20, check)
            root.mainloop()
            assert not failures, failures
            assert stage == 6 and len(app.files) == 5, (stage, len(app.files))
            app.finish_close()
            print('POSTED_DROP_PASS', flush=True)
        ''')
        with tempfile.TemporaryDirectory(prefix='xml-posted-drop-') as directory:
            result = subprocess.run([sys.executable, '-X', 'faulthandler', '-c', driver, directory],
                                    cwd=Path(__file__).resolve().parent.parent, capture_output=True,
                                    text=True, encoding='utf-8', errors='replace', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('POSTED_DROP_PASS', result.stdout)
