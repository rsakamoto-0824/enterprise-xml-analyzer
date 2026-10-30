import sys
from queue import Empty, SimpleQueue

WM_DROPFILES = 0x0233
WM_NCDESTROY = 0x0082
GA_ROOT = 2
DROP_POLL_MS = 50
MAX_DROP_FILES = 17


def enable_dpi():
    if sys.platform == "win32":
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (OSError, AttributeError):
            ctypes.windll.user32.SetProcessDPIAware()


class _WindowsDropAPI:
    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.shell = ctypes.WinDLL('shell32', use_last_error=True)
        self.controls = ctypes.WinDLL('comctl32', use_last_error=True)
        self.handler_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                              wintypes.WPARAM, wintypes.LPARAM, ctypes.c_size_t, ctypes.c_size_t)
        self.root_window = self.user.GetAncestor
        self.root_window.argtypes = [wintypes.HWND, wintypes.UINT]
        self.root_window.restype = wintypes.HWND
        self.install = self.controls.SetWindowSubclass
        self.install.argtypes = [wintypes.HWND, self.handler_type, ctypes.c_size_t, ctypes.c_size_t]
        self.install.restype = wintypes.BOOL
        self.remove = self.controls.RemoveWindowSubclass
        self.remove.argtypes = [wintypes.HWND, self.handler_type, ctypes.c_size_t]
        self.remove.restype = wintypes.BOOL
        self.forward = self.controls.DefSubclassProc
        self.forward.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.forward.restype = ctypes.c_ssize_t
        self.accept = self.shell.DragAcceptFiles
        self.accept.argtypes = [wintypes.HWND, wintypes.BOOL]
        self.accept.restype = None
        self.shell.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
        self.shell.DragQueryFileW.restype = wintypes.UINT
        self.finish = self.shell.DragFinish
        self.finish.argtypes = [wintypes.HANDLE]
        self.finish.restype = None

    def read_paths(self, handle):
        paths = []
        count = self.shell.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
        for index in range(min(count, MAX_DROP_FILES)):
            size = self.shell.DragQueryFileW(handle, index, None, 0)
            buffer = self.ctypes.create_unicode_buffer(size + 1)
            if self.shell.DragQueryFileW(handle, index, buffer, size + 1) != size:
                raise OSError('Cannot read dropped file name')
            if buffer.value:
                paths.append(buffer.value)
        return paths


class _FileDrop:
    def __init__(self, root, callback, api, window):
        self.root, self.callback, self.api, self.window = root, callback, api, window
        self.pending = SimpleQueue()
        self.closed, self.installed, self.cleaned, self.timer = False, False, False, None
        self.identifier = id(self)
        self.handler = api.handler_type(self.dispatch)
        if not api.install(window, self.handler, self.identifier, 0):
            raise OSError('Cannot install file drop handler')
        self.installed = True
        if not hasattr(root, '_xml_file_drops'):
            root._xml_file_drops = {}
        # Native callbacks must remain alive even if the caller discards the cleanup function.
        root._xml_file_drops[self.identifier] = self
        try:
            api.accept(window, True)
            self.timer = root.after(DROP_POLL_MS, self.drain)
        except Exception:
            self.close()
            raise

    def dispatch(self, window, message, wparam, lparam, identifier, reference):
        # Tcl calls here can corrupt _tkinter's thread state during native message dispatch.
        # Even scheduling root.after/after_idle belongs in the normal Tk event loop.
        try:
            if message == WM_DROPFILES:
                try:
                    if not self.closed:
                        paths = self.api.read_paths(wparam)
                        if paths:
                            self.pending.put((paths, None))
                finally:
                    self.api.finish(wparam)
                return 0
            if message == WM_NCDESTROY:
                self.closed = True
                self.api.remove(window, self.handler, self.identifier)
                self.installed = False
            return self.api.forward(window, message, wparam, lparam)
        except BaseException as error:
            # Never let a Python exception cross the Windows callback boundary.
            if not self.closed:
                self.pending.put((None, error))
            return 0

    def drain(self):
        self.timer = None
        if self.closed:
            return
        try:
            paths, error = self.pending.get_nowait()
            if error is not None:
                raise error
            self.callback(paths)
        except Empty:
            pass
        except BaseException as error:
            self.root.report_callback_exception(type(error), error, error.__traceback__)
        finally:
            if not self.closed:
                self.timer = self.root.after(DROP_POLL_MS, self.drain)

    def close(self):
        if self.cleaned:
            return
        self.closed = True
        if self.timer is not None:
            try:
                self.root.after_cancel(self.timer)
            except Exception:
                pass
            self.timer = None
        was_installed = self.installed
        if was_installed:
            if not self.api.remove(self.window, self.handler, self.identifier):
                return
            self.installed = False
        handlers = self.root._xml_file_drops
        handlers.pop(self.identifier, None)
        self.cleaned = True
        if was_installed and not any(item.installed and item.window == self.window for item in handlers.values()):
            self.api.accept(self.window, False)
        while not self.pending.empty():
            self.pending.get_nowait()


def install_file_drop(root, callback):
    if sys.platform != 'win32':
        return lambda: None
    from .models import AppError
    try:
        api = _WindowsDropAPI()
        root.update_idletasks()
        window = api.root_window(root.winfo_id(), GA_ROOT)
        if not window:
            raise OSError('Missing window handle')
        return _FileDrop(root, callback, api, window).close
    except OSError:
        error = AppError('DROP_SETUP: ドロップを初期化できませんでした。「XMLファイルを選択」を利用してください。')
        root.report_callback_exception(type(error), error, None)
        return lambda: None
