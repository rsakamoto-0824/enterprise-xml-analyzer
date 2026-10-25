import sys


def enable_dpi():
    if sys.platform == "win32":
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (OSError, AttributeError):
            ctypes.windll.user32.SetProcessDPIAware()


def install_file_drop(root, callback):
    if sys.platform != "win32":
        return lambda: None
    import ctypes
    from ctypes import wintypes

    user32, shell32 = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("shell32", use_last_error=True)
    result_type = ctypes.c_ssize_t
    callback_type = ctypes.WINFUNCTYPE(result_type, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallWindowProcW.restype = result_type
    shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
    shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    shell32.DragQueryFileW.restype = wintypes.UINT
    shell32.DragFinish.argtypes = [wintypes.HANDLE]
    root.update_idletasks()
    hwnd = user32.GetAncestor(root.winfo_id(), 2)
    old = None

    def dispatch(window, message, wparam, lparam):
        if message == 0x0233:
            paths = []
            try:
                count = shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)
                for index in range(min(count, 17)):
                    size = shell32.DragQueryFileW(wparam, index, None, 0)
                    buffer = ctypes.create_unicode_buffer(size + 1)
                    shell32.DragQueryFileW(wparam, index, buffer, size + 1)
                    paths.append(buffer.value)
            finally:
                shell32.DragFinish(wparam)
            root.after_idle(lambda: callback(paths))
            return 0
        return user32.CallWindowProcW(old, window, message, wparam, lparam)

    handler = callback_type(dispatch)
    ctypes.set_last_error(0)
    old = user32.SetWindowLongPtrW(hwnd, -4, ctypes.cast(handler, ctypes.c_void_p))
    if not old and ctypes.get_last_error():
        return lambda: None
    shell32.DragAcceptFiles(hwnd, True)

    def uninstall():
        # Keep handler alive until the original callback is restored.
        _ = handler
        shell32.DragAcceptFiles(hwnd, False)
        user32.SetWindowLongPtrW(hwnd, -4, old)

    return uninstall
