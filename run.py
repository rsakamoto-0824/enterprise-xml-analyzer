"""Source and frozen entry point. Check the interpreter before importing application code."""
import json
from pathlib import Path
import sys

REQUIRED_PYTHON = (3, 14, 6)


def show_startup_error(message):
    if sys.stderr is not None:
        print(message, file=sys.stderr)
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror("Enterprise XML Analyzer", message)
        root.destroy()
    except Exception:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "Enterprise XML Analyzer", 0x10)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if sys.version_info[:3] != REQUIRED_PYTHON:
        show_startup_error("Python 3.14.6が必要です。Windows配布版のEnterpriseXmlAnalyzer.exeを使用するか、Python 3.14.6の完全版（Tcl/Tk付き）で起動してください。")
        return 2
    try:
        if len(argv) == 2 and argv[0] == "--self-test":
            from xml_analyzer.selftest import self_test
            result = self_test()
            Path(argv[1]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            return 0
        if argv:
            show_startup_error("起動引数が不正です。引数なしで起動してください。")
            return 2
        from xml_analyzer.windows import enable_dpi
        enable_dpi()
        import tkinter
        from xml_analyzer.gui import Application
        root = tkinter.Tk()
        Application(root)
        root.mainloop()
        return 0
    except Exception as error:
        if len(argv) == 2 and argv[0] == "--self-test":
            Path(argv[1]).write_text(json.dumps({"status": "FAIL", "error_type": type(error).__name__}), encoding="utf-8")
            return 1
        show_startup_error("STARTUP_ERROR: 起動できませんでした。Windows配布フォルダをすべて展開し直してください。ソース版の場合はPython 3.14.6とTcl/Tkの導入状態を確認してください。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
