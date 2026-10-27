import json
from pathlib import Path
import struct
import sys
import tkinter as tk
from tkinter import filedialog, ttk
from urllib.parse import unquote
from xml.sax.saxutils import escape, quoteattr
import zlib

from .models import AppError, MAX_GRAPH_NODES
from .layout import FlowFrame
from .writers import atomic_target


def xml_fragment(store, root):
    count, characters = 0, 0

    def visit(n):
        nonlocal count, characters
        count += 1
        if count > 500:
            raise AppError("FRAGMENT_LIMIT: XML断片は500ノード以内です。")
        if n["kind"] == "element":
            name = "e:" + n["local"] if n["uri"] else n["local"]
            opening = "<" + name + (" xmlns:e=" + quoteattr(n["uri"]) if n["uri"] else "")
            for index, (key, value) in enumerate(json.loads(n["attrs"]).items()):
                if key.startswith("{"):
                    uri, local = key[1:].split("}", 1)
                    if unquote(uri) == "http://www.w3.org/XML/1998/namespace":
                        opening += f" xml:{local}=" + quoteattr(value)
                    else:
                        opening += f" xmlns:a{index}=" + quoteattr(unquote(uri)) + f" a{index}:{local}=" + quoteattr(value)
                else:
                    opening += " " + key + "=" + quoteattr(value)
            result = opening + ">"
            after = 0
            while batch := store.children(n["file"], n["id"], after):
                for child in batch:
                    result += visit(child)
                after = batch[-1]["id"]
            result += "</" + name + ">"
        elif n["kind"] == "comment":
            result = "<!--" + n["text"] + "-->"
        elif n["kind"] == "pi":
            result = "<?" + n["name"] + " " + n["text"] + "?>"
        else:
            result = escape(n["text"])
        characters += len(result)
        if characters > 65536:
            raise AppError("FRAGMENT_LIMIT: XML断片が64Ki文字を超えました。")
        return result

    return visit(root)


class Graph(ttk.Frame):
    def __init__(self, master, store, on_select, report):
        super().__init__(master)
        self.store, self.on_select, self.report = store, on_select, report
        self.root_node = None
        self.collapsed = set()
        self.horizontal = True
        self.points, self.edges = [], []
        bar = FlowFrame(self)
        bar.pack(fill="x", pady=6)
        for text, action in (("縦／横", self.toggle_direction), ("全体を表示", self.reset),
                             ("拡大", lambda: self.zoom(1.2)), ("縮小", lambda: self.zoom(1 / 1.2)),
                             ("SVG保存", self.save_svg), ("PNG保存", self.save_png)):
            ttk.Button(bar, text=text, command=action).pack(side="left", padx=3)
        self.notice = ttk.Label(self, text="ノードを選択すると詳細へ移動。ダブルクリックで開閉。ドラッグで移動。", wraplength=900)
        self.notice.pack(anchor="w", pady=4)
        box = ttk.Frame(self)
        box.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(box, background="#f7fafc", highlightthickness=0)
        horizontal = ttk.Scrollbar(box, orient="horizontal", command=self.canvas.xview)
        vertical = ttk.Scrollbar(box, command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        self.canvas.bind("<ButtonPress-1>", lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind("<B1-Motion>", lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        self.canvas.bind("<MouseWheel>", lambda e: self.zoom(1.15 if e.delta > 0 else 1 / 1.15))

    def load(self, node):
        self.document_root = node
        self.root_node = node
        self.collapsed.clear()
        self.render()

    def reset(self):
        self.root_node = getattr(self, "document_root", None)
        self.collapsed.clear()
        self.render()

    def toggle_direction(self):
        self.horizontal = not self.horizontal
        self.render()

    def zoom(self, factor):
        bounds = self.canvas.bbox("all")
        if not bounds or (factor < 1 and bounds[2] - bounds[0] < 150) or (factor > 1 and bounds[2] - bounds[0] > 100000):
            return
        self.canvas.scale("all", 0, 0, factor, factor)
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def render(self):
        self.canvas.delete("all")
        self.points, self.edges = [], []
        if not self.root_node:
            return
        nodes = [{"node": self.root_node, "level": 0, "children": []}]
        for item in nodes:
            n = item["node"]
            kids = []
            if item["level"] < 4 and n["id"] not in self.collapsed:
                after = 0
                while len(nodes) + len(kids) < MAX_GRAPH_NODES:
                    batch = self.store.children(n["file"], n["id"], after, min(250, MAX_GRAPH_NODES - len(nodes) - len(kids)))
                    if not batch:
                        break
                    kids.extend({"node": child, "level": item["level"] + 1, "children": []}
                                for child in batch if child["kind"] == "element")
                    after = batch[-1]["id"]
            item["children"] = kids
            item["omitted"] = n["children"] - len(kids)
            nodes.extend(kids)
        cursor = 0

        def place(item):
            nonlocal cursor
            for child in item["children"]:
                place(child)
            across = sum(c["across"] for c in item["children"]) / len(item["children"]) if item["children"] else cursor * 54 + 40
            if not item["children"]:
                cursor += 1
            item["across"] = across
            item["x"], item["y"] = (item["level"] * 230 + 40, across) if self.horizontal else (across * 3.2, item["level"] * 110 + 40)

        place(nodes[0])
        for item in nodes:
            for child in item["children"]:
                edge = (item["x"], item["y"], child["x"], child["y"])
                self.edges.append(edge)
                self.canvas.create_line(*edge, fill="#b3c4d3", width=2)
        for item in nodes:
            x, y, n = item["x"], item["y"], item["node"]
            label = n["name"][:35]
            omitted = f'+{item["omitted"]} 子ノード省略' if item["omitted"] else ""
            circle = self.canvas.create_oval(x - 6, y - 6, x + 6, y + 6, fill="#178477", outline="white", width=2)
            text = self.canvas.create_text(x + 13, y - 2, anchor="w", text=label, fill="#213b52", font=("Yu Gothic UI", 10))
            if omitted:
                self.canvas.create_text(x + 13, y + 17, anchor="w", text=omitted, fill="#906229", font=("Yu Gothic UI", 9))
            for tag in (circle, text):
                self.canvas.tag_bind(tag, "<ButtonRelease-1>", lambda e, node=n: self.on_select(node))
                self.canvas.tag_bind(tag, "<Double-Button-1>", lambda e, node=n: self.toggle_node(node))
            self.points.append((x, y, label, omitted))
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.notice.configure(text=f"表示 {len(nodes):,} ノード（上限500・深さ4）。クリックで詳細、ダブルクリックで開閉、ドラッグで移動。")

    def toggle_node(self, n):
        if n["id"] in self.collapsed:
            self.collapsed.remove(n["id"])
        else:
            self.collapsed.add(n["id"])
        self.render()

    def save_svg(self):
        if not self.points:
            return
        target = filedialog.asksaveasfilename(defaultextension=".svg", filetypes=[("SVG画像", "*.svg")], initialfile="xml-graph.svg")
        if target:
            self.export_svg(target)

    def export_svg(self, target):
        width = max((p[0] for p in self.points), default=100) + 280
        height = max((p[1] for p in self.points), default=100) + 60
        lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="#f7fafc"/>']
        lines += [f'<line x1="{a}" y1="{b}" x2="{c}" y2="{d}" stroke="#b3c4d3"/>' for a, b, c, d in self.edges]
        for x, y, label, omitted in self.points:
            lines.append(f'<circle cx="{x}" cy="{y}" r="6" fill="#178477"/><text x="{x + 13}" y="{y}" font-size="13" font-family="Yu Gothic UI,sans-serif">{escape(label)}</text>')
            if omitted:
                lines.append(f'<text x="{x + 13}" y="{y + 18}" font-size="11">{escape(omitted)}</text>')
        lines.append('</svg>')
        with atomic_target(target) as output:
            output.write_text("".join(lines), encoding="utf-8")

    def save_png(self):
        if sys.platform != "win32":
            self.report(AppError("PNG_PLATFORM: PNG保存はWindows版で使用できます。他のOSではSVG保存を使用してください。"))
            return
        target = filedialog.asksaveasfilename(defaultextension=".png", filetypes=[("PNG画像", "*.png")], initialfile="xml-graph.png")
        if target:
            try:
                save_windows_canvas_png(self.canvas, target)
            except (OSError, AppError):
                self.report(AppError("PNG_ERROR: 表示中グラフの画像保存に失敗しました。"))


def save_windows_canvas_png(canvas, target):
    """Capture the visible canvas using Windows GDI; no imaging package is required."""
    import ctypes
    from ctypes import wintypes

    user32, gdi32 = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("gdi32", use_last_error=True)
    for name in ("GetDC",):
        getattr(user32, name).restype = wintypes.HDC
    for name in ("CreateCompatibleDC", "CreateCompatibleBitmap", "SelectObject"):
        getattr(gdi32, name).restype = wintypes.HANDLE
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    canvas.update_idletasks()
    hwnd = canvas.winfo_id()
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    width, height = rect.right, rect.bottom
    if not 1 <= width <= 8000 or not 1 <= height <= 8000:
        raise AppError("PNG_SIZE: グラフ表示サイズが保存上限を超えています。")
    source = user32.GetDC(hwnd)
    dest = gdi32.CreateCompatibleDC(source)
    bitmap = gdi32.CreateCompatibleBitmap(source, width, height)
    old = gdi32.SelectObject(dest, bitmap)
    try:
        if not gdi32.BitBlt(dest, 0, 0, width, height, source, 0, 0, 0x00CC0020):
            raise OSError()
        gdi32.SelectObject(dest, old)
        header = struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        info = ctypes.create_string_buffer(header + bytes(16))
        pixels = ctypes.create_string_buffer(width * height * 4)
        if not gdi32.GetDIBits(dest, bitmap, 0, height, pixels, info, 0):
            raise OSError()
        raw = bytearray()
        data = pixels.raw
        for y in range(height):
            raw.append(0)
            row = data[y * width * 4:(y + 1) * width * 4]
            for x in range(0, len(row), 4):
                raw.extend((row[x + 2], row[x + 1], row[x]))

        def chunk(kind, content):
            return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content))

        with atomic_target(target) as output:
            output.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    finally:
        gdi32.SelectObject(dest, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(dest)
        user32.ReleaseDC(hwnd, source)
