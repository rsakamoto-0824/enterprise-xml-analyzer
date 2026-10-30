from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


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
            from xml_analyzer.selftest import post_windows_drop

            base = Path(sys.argv[1])
            paths = [base / name for name in ('日本語 😀 space.xml', 'braces {A}.xml', 'third.xml')]
            for path in paths:
                path.write_text('<r><n>value</n></r>', encoding='utf-8')
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
                    root.quit()
                    return
                root.after(20, check)

            root.after(50, lambda: post_windows_drop(root, paths[:1]))
            root.after(20, check)
            root.mainloop()
            assert not failures, failures
            assert stage == 3 and len(app.files) == 4, (stage, len(app.files))
            app.finish_close()
            print('POSTED_DROP_PASS', flush=True)
        ''')
        with tempfile.TemporaryDirectory(prefix='xml-posted-drop-') as directory:
            script = Path(directory) / 'driver.py'
            script.write_text(driver, encoding='utf-8')
            result = subprocess.run([sys.executable, '-X', 'faulthandler', '-c', driver, directory],
                                    cwd=Path(__file__).resolve().parent.parent, capture_output=True,
                                    text=True, encoding='utf-8', errors='replace', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('POSTED_DROP_PASS', result.stdout)
