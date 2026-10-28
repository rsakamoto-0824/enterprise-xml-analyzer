from dataclasses import replace
from datetime import datetime
import gc
import json
from pathlib import Path
import queue
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import VERSION
from .layout import FlowFrame, ScrollablePage
from .ml_export import preview_export, save_export
from .comparison import compare, search
from .extraction import candidate_columns, difference_rows, extract, preview
from .models import (AppError, Cancelled, Column, DIFF_COLORS, DIFF_LABELS, DIFF_TYPES,
                     DiffOptions, MAX_VISIBLE_NODES, PAGE_SIZE, Task, Template)
from .parser import parse_file
from .simple_export import all_element_rows, simple_template
from .storage import Store
from .table_export import LONG, WIDE, preview_table, save_table
from .visualization import Graph, xml_fragment
from .windows import install_file_drop
from .writers import atomic_target, write_csv, write_xlsx


def display(value):
    return "" if value is None else value.isoformat() if hasattr(value, "isoformat") else str(value)


def make_table(parent, columns, widths=None, height=12):
    frame = ttk.Frame(parent)
    frame.pack(fill="both", expand=True, pady=5)
    tree = ttk.Treeview(frame, columns=columns, show="headings", height=height)
    for index, name in enumerate(columns):
        tree.heading(name, text=name)
        tree.column(name, width=(widths or {}).get(name, 160), minwidth=60, stretch=True)
    vertical = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    tree.grid(row=0, column=0, sticky="nsew")
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)
    return tree


def clear_tree(tree):
    items = tree.get_children()
    if items:
        tree.delete(*items)


class Application:
    def __init__(self, root, work_dir=None):
        self.root = root
        self.temporary = tempfile.TemporaryDirectory(prefix="enterprise-xml-python-", dir=work_dir)
        self.store = Store(Path(self.temporary.name) / "workspace.sqlite")
        self.files, self.file_labels = [], {}
        self.selected = None
        self.task = None
        self.worker = None
        self.closing = False
        self.messages = queue.Queue()
        self.logs = []
        self.template = Template()
        self.tree_nodes, self.more_nodes = {}, {}
        self.comparison = None
        self.diff_offset = 0
        self.diff_rows = {}
        self.last_result = None
        self.status = tk.StringVar(value="XMLファイルを選択、またはウインドウへドロップしてください。")
        self.current_file = tk.StringVar()
        self.root.title(f"Enterprise XML Analyzer {VERSION} — Python 3.14.6")
        width = min(1320, max(640, self.root.winfo_screenwidth() - 60))
        height = min(860, max(480, self.root.winfo_screenheight() - 100))
        self.root.geometry(f"{width}x{height}+20+20")
        self.root.minsize(640, 480)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.report_callback_exception = lambda kind, value, trace: self.report(value)
        self._style()
        header = ttk.Frame(root, padding=(12, 8))
        header.pack(fill="x")
        header.columnconfigure(1, weight=1)
        self.title_label = ttk.Label(header, text="Enterprise XML Analyzer", style="Title.TLabel")
        self.title_label.grid(row=0, column=0, sticky="w", padx=(0, 10))
        ttk.Label(header, text="ローカルXMLワークスペース", style="Muted.TLabel").grid(row=1, column=0, sticky="w")
        self.file_picker = ttk.Combobox(header, textvariable=self.current_file, state="readonly", width=24)
        self.file_picker.grid(row=0, column=1, rowspan=2, sticky="ew")
        self.file_picker.bind("<<ComboboxSelected>>", lambda e: self.select_file())
        self.navigation = ttk.Frame(header)
        self.navigation.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        ttk.Label(self.navigation, text="画面").pack(side="left", padx=(0, 6))
        self.page_choice = ttk.Combobox(self.navigation, state="readonly", width=24)
        self.page_choice.pack(side="left", fill="x", expand=True)
        self.page_choice.bind("<<ComboboxSelected>>", lambda e: self.tabs.select(self.pages[self.page_choice.get()]))
        self.summary = ttk.Label(root, text="0 ファイル ｜ XMLはこのPC内で処理します。", padding=(18, 0))
        self.summary.pack(anchor="w")
        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill="both", expand=True, padx=14, pady=12)
        self.pages, self.contents = {}, {}
        for name in ("XML読込", "構造ビュー", "グラフ", "XML比較", "Excel出力", "統計", "設定"):
            page = ScrollablePage(self.tabs)
            self.tabs.add(page, text=name)
            self.pages[name] = page
            self.contents[name] = page.body
        self.page_choice.configure(values=tuple(self.pages))
        self.page_choice.set("XML読込")
        self.tabs.bind("<<NotebookTabChanged>>", self.tab_changed)
        self._load_page()
        self._tree_page()
        self.graph = Graph(self.contents["グラフ"], self.store, self.jump_to_node, self.report)
        self.graph.pack(fill="both", expand=True)
        self._compare_page()
        self._export_page()
        self.statistics = tk.Text(self.contents["統計"], height=12, wrap="word", font=("Yu Gothic UI", 11))
        self.statistics.pack(fill="both", expand=True)
        self._settings_page()
        footer = ttk.Frame(root, padding=(16, 6))
        footer.pack(fill="x")
        self.status_label = ttk.Label(footer, textvariable=self.status, wraplength=760)
        self.status_label.pack(side="left", fill="x", expand=True)
        self.cancel_button = ttk.Button(footer, text="中断", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="right")
        self.progress = ttk.Progressbar(root, mode="determinate")
        self.progress.pack(fill="x", padx=16, pady=(0, 8))
        ttk.Label(root, text="Python 3.14.6 ｜ 追加パッケージ不要 ｜ 外部通信なし ｜ 保存は明示操作のみ", style="Muted.TLabel", padding=(16, 0, 16, 8)).pack(anchor="w")
        # Reserve status/cancel space before giving the notebook the remaining height.
        self.tabs.pack_forget()
        footer.pack_configure(side="bottom")
        self.progress.pack_configure(side="bottom")
        self.tabs.pack(fill="both", expand=True, padx=14, pady=12)
        self.resize_pending = None
        self.root.bind("<Configure>", self.schedule_resize, add="+")
        self.root.after_idle(self.resize_layout)
        self.uninstall_drop = install_file_drop(root, self.import_files)
        self.poll_id = root.after(50, self.poll)

    def tab_changed(self, event=None):
        selected = self.tabs.select()
        for name, page in self.pages.items():
            if str(page) == selected:
                self.page_choice.set(name)
                break

    def schedule_resize(self, event):
        if event.widget is self.root and self.resize_pending is None:
            self.resize_pending = self.root.after_idle(self.resize_layout)

    def resize_layout(self):
        self.resize_pending = None
        width = self.root.winfo_width()
        compact = width < 900
        if compact:
            self.navigation.grid()
        else:
            self.navigation.grid_remove()
        self.tabs.configure(style="Compact.TNotebook" if compact else "TNotebook")
        self.status_label.configure(wraplength=max(200, width - 140))
        self.title_label.configure(font=("Yu Gothic UI", 15 if compact else 19, "bold"))
        def wrap(widget):
            for child in widget.winfo_children():
                if isinstance(child, ttk.Label) and int(child.cget("wraplength") or 0) > 0:
                    child.configure(wraplength=max(200, width - 80))
                if isinstance(child, FlowFrame):
                    child.schedule()
                wrap(child)
        for page in self.pages.values():
            wrap(page)
            page.schedule()

    def _style(self):
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
        style.layout("Compact.TNotebook.Tab", [])
        style.configure(".", font=("Yu Gothic UI", 10))
        style.configure("Treeview", rowheight=28)
        style.configure("Treeview.Heading", font=("Yu Gothic UI", 10, "bold"))
        style.configure("Title.TLabel", font=("Yu Gothic UI", 19, "bold"), foreground="#173a52")
        style.configure("Muted.TLabel", foreground="#52677b")
        style.configure("TNotebook.Tab", padding=(14, 8))

    def report(self, error):
        message = str(error) if isinstance(error, AppError) else "PROCESS_ERROR: 操作に失敗しました。ファイル権限・空き容量を確認してください。"
        self.status.set(message)
        self.last_result = error

    def run(self, label, function, done=None):
        if self.task:
            self.status.set("処理中です。完了または中断後に操作してください。")
            return False
        # Reclaim closed Tk widget cycles on the UI thread before starting a worker.
        gc.collect()
        self.task = Task(progress=lambda text, count, total: self.messages.put(("progress", (text, count, total))))
        self.lock_controls(True)
        task = self.task
        self.status.set(label + "…")
        self.cancel_button.configure(state="normal")
        self.last_result = None

        def work():
            try:
                value = function(task)
                self.messages.put(("done", (label, value, done, task.started)))
            except Exception as error:
                self.messages.put(("error", (label, error, task.started)))

        self.worker = threading.Thread(target=work, name="xml-operation", daemon=False)
        self.worker.start()
        return True

    def poll(self):
        try:
            self.poll_messages()
        except Exception as error:
            self.report(error)
            self.poll_id = self.root.after(100, self.poll)

    def poll_messages(self):
        latest_progress = None
        for _ in range(500):
            try:
                event, value = self.messages.get_nowait()
            except queue.Empty:
                break
            if event == "progress":
                latest_progress = value
                continue
            label = value[0]
            self.task = None
            self.lock_controls(False)
            self.cancel_button.configure(state="disabled")
            self.progress["value"] = 0
            latest_progress = None
            if event == "done":
                _, result, callback, started = value
                self.last_result = result
                self.status.set(label + " · 完了")
                if callback and not self.closing:
                    try:
                        callback(result)
                    except Exception as error:
                        self.report(error)
                outcome = "COMPLETED"
            else:
                _, error, started = value
                self.report(error)
                outcome = "CANCELLED" if isinstance(error, Cancelled) else "ERROR"
            self.logs.append({"time": datetime.now().astimezone().isoformat(), "event": label,
                              "seconds": time.monotonic() - started, "result": outcome,
                              "file_ids": [f["id"] for f in self.store.files()]})
            self.logs = self.logs[-1000:]
            if not self.closing:
                self.refresh_files()
        if latest_progress and self.task:
            label, count, total = latest_progress
            self.status.set(f"{label}… {count:,}" + (f" / {total:,}" if total else ""))
            self.progress["value"] = count * 100 / total if total else (count % 1000) / 10
        if self.closing and self.task is None:
            self.finish_close()
            return
        self.poll_id = self.root.after(50, self.poll)

    def cancel(self):
        if self.task:
            self.task.cancel.set()
            self.status.set("中断処理中…未完了データを削除しています。")

    def lock_controls(self, busy):
        if busy:
            self.saved_states = []
            def visit(widget):
                for child in widget.winfo_children():
                    if isinstance(child, (ttk.Button, ttk.Entry, ttk.Combobox, ttk.Checkbutton)) and child is not self.cancel_button:
                        self.saved_states.append((child, child.state()))
                        child.state(["disabled"])
                    elif isinstance(child, tk.Listbox):
                        self.saved_states.append((child, str(child.cget("state"))))
                        child.configure(state="disabled")
                    visit(child)
            visit(self.root)
        else:
            for widget, state in getattr(self, "saved_states", []):
                if not widget.winfo_exists():
                    continue
                if isinstance(widget, tk.Listbox):
                    widget.configure(state=state)
                else:
                    widget.state(["!disabled", "!readonly", *state])
            self.saved_states = []

    def _load_page(self):
        page = self.contents["XML読込"]
        ttk.Label(page, text="XMLの構造を、見えるかたちに。", font=("Yu Gothic UI", 18, "bold")).pack(anchor="w", pady=10)
        ttk.Label(page, text="UTF-8 / UTF-16対応。複数ファイル・UNC共有のファイルも選択できます。Windowsではここへドロップできます。", wraplength=1000).pack(anchor="w", pady=5)
        bar = FlowFrame(page)
        bar.pack(fill="x", pady=12)
        ttk.Button(bar, text="XMLファイルを選択", command=self.pick_files).pack(side="left", padx=3)
        ttk.Button(bar, text="選択ファイルを削除", command=self.delete_selected).pack(side="left", padx=3)
        ttk.Button(bar, text="全要素をExcel保存", command=self.export_table).pack(side="left", padx=(18, 3))
        self.file_table = make_table(page, ["ファイル", "サイズ", "要素数", "深さ", "文字コード", "解析秒"], {"ファイル": 400})
        self.file_table.bind("<<TreeviewSelect>>", self.select_from_table)

    def pick_files(self):
        paths = filedialog.askopenfilenames(title="XMLファイルを選択", filetypes=[("XMLファイル", "*.xml"), ("すべてのファイル", "*.*")])
        if paths:
            self.import_files(paths)

    def import_files(self, paths):
        if len(paths) > 16 or len(paths) + len(self.store.files()) > 16:
            self.report(AppError("QUEUE_LIMIT: 作業中のXMLは合計16ファイルまでです。"))
            return
        paths = tuple(paths)

        def action(task):
            results = []
            for path in paths:
                task.check()
                results.append(parse_file(self.store, path, task))
            return results

        def done(results):
            self.refresh_files()
            if results:
                self.set_file(results[-1]["id"])

        self.run("XML読込", action, done)

    def refresh_files(self):
        selected = self.file_id()
        previous_ids = [file["id"] for file in self.files]
        self.files = self.store.files()
        if previous_ids != [file["id"] for file in self.files]:
            self.reset_ml_preview()
            self.reset_table_preview()
        self.file_labels = {f'{f["name"]}  [{f["id"][:6]}]': f["id"] for f in self.files}
        labels = tuple(self.file_labels)
        for widget in (self.file_picker, self.compare_a, self.compare_b):
            widget.configure(values=labels)
            if widget.get() not in labels:
                widget.set(labels[0] if labels else "")
        if len(labels) > 1 and self.compare_a.get() == self.compare_b.get():
            self.compare_b.set(labels[1])
        if selected and selected in self.file_labels.values():
            self.current_file.set(next(k for k, v in self.file_labels.items() if v == selected))
        clear_tree(self.file_table)
        for f in self.files:
            info = f["info"]
            self.file_table.insert("", "end", iid=f["id"], values=(f["name"], f'{f["size"] / 1048576:.2f} MiB', f'{info["elements"]:,}', info["max_depth"], info["encoding"], f'{info["parse_seconds"]:.2f}'))
        self.summary.configure(text=f'{len(self.files)} ファイル ｜ {sum(f["info"]["elements"] for f in self.files):,} 要素 ｜ 作業領域 {self.store.path.stat().st_size / 1048576:.2f} MiB')
        old_selections = set(self.export_files.curselection())
        self.export_files.delete(0, "end")
        for index, label in enumerate(labels):
            self.export_files.insert("end", label)
            if not old_selections or index in old_selections:
                self.export_files.selection_set(index)
        self.update_statistics()

    def file_id(self):
        return self.file_labels.get(self.current_file.get())

    def set_file(self, file_id):
        self.current_file.set(next((k for k, v in self.file_labels.items() if v == file_id), ""))
        self.select_file()

    def select_from_table(self, event=None):
        if self.task:
            return
        selection = self.file_table.selection()
        if selection:
            self.set_file(selection[0])

    def select_file(self):
        if self.task:
            return
        file_id = self.file_id()
        self.selected = None
        clear_tree(self.tree)
        self.tree_nodes.clear()
        self.more_nodes.clear()
        self.set_detail("")
        self.graph.load(None)
        if file_id:
            self.add_children("", file_id, 0)
            roots = self.tree.get_children()
            if roots:
                self.tree.item(roots[0], open=True)
                self.expand_tree(roots[0])
            root = next((n for n in self.store.children(file_id) if n["kind"] == "element"), None)
            if root:
                self.graph.load(root)

    def delete_selected(self):
        file_id = self.file_id()
        if file_id:
            self.run("ファイル削除", lambda task: self.store.delete_file(file_id), lambda _: self.after_delete())

    def after_delete(self):
        self.comparison = None
        self.diff_rows.clear()
        clear_tree(self.differences)
        self.refresh_files()
        self.select_file()
        self.preview_text.delete("1.0", "end")

    def _tree_page(self):
        page = self.contents["構造ビュー"]
        bar = FlowFrame(page)
        bar.pack(fill="x")
        self.search_text = tk.StringVar()
        ttk.Entry(bar, textvariable=self.search_text, width=30).pack(side="left", padx=3)
        self.search_target = ttk.Combobox(bar, values=("要素名", "属性名", "属性値", "テキスト", "パス"), state="readonly", width=10)
        self.search_target.set("要素名")
        self.search_target.pack(side="left", padx=3)
        self.search_exact, self.search_case = tk.BooleanVar(), tk.BooleanVar()
        ttk.Checkbutton(bar, text="完全一致", variable=self.search_exact).pack(side="left")
        ttk.Checkbutton(bar, text="大文字小文字を区別", variable=self.search_case).pack(side="left")
        ttk.Button(bar, text="検索", command=self.start_search).pack(side="left", padx=5)
        panes = ttk.Panedwindow(page, orient="horizontal")
        panes.pack(fill="both", expand=True, pady=8)
        left, right = ttk.Frame(panes), ttk.Frame(panes)
        panes.add(left, weight=3)
        panes.add(right, weight=2)
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        horizontal = ttk.Scrollbar(left, orient="horizontal", command=self.tree.xview)
        horizontal.pack(side="bottom", fill="x")
        self.tree.configure(xscrollcommand=horizontal.set)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewOpen>>", lambda e: self.expand_tree(self.tree.focus()))
        self.tree.bind("<<TreeviewSelect>>", self.tree_selected)
        self.tree.bind("<Double-1>", self.activate_more)
        ttk.Button(right, text="この要素を出力の1行にする", command=self.use_record).pack(fill="x", pady=3)
        ttk.Button(right, text="選択サブツリーをグラフ表示", command=self.focus_graph).pack(fill="x", pady=3)
        ttk.Button(right, text="XML断片を表示", command=self.show_fragment).pack(fill="x", pady=3)
        self.detail = tk.Text(right, height=10, width=30, wrap="word", font=("Yu Gothic UI", 10))
        self.detail.pack(fill="both", expand=True, pady=5)
        self.search_results = make_table(page, ["検索結果", "パス"], {"パス": 750}, height=3)
        self.search_results.bind("<<TreeviewSelect>>", self.search_selected)
        self.search_results.master.pack_configure(side="bottom", fill="x", expand=False)
        panes.pack_forget()
        panes.pack(fill="both", expand=True, pady=8)

    def add_children(self, parent_item, file_id, parent_id, after=0):
        batch = self.store.children(file_id, parent_id, after, PAGE_SIZE + 1)
        if len(self.tree_nodes) + len(batch) > MAX_VISIBLE_NODES:
            self.report(AppError("TREE_LIMIT: 表示上限1万ノードです。ファイルを選び直すか検索してください。"))
            return
        for n in batch[:PAGE_SIZE]:
            iid = f'n{n["id"]}'
            if self.tree.exists(iid):
                continue
            self.tree.insert(parent_item, "end", iid=iid, text=n["name"] + (f'  子 {n["children"]}' if n["children"] else ""))
            self.tree_nodes[iid] = n["id"]
            if n["children"]:
                self.tree.insert(iid, "end", iid=iid + "-pending", text="読み込み…")
        if len(batch) > PAGE_SIZE:
            iid = f'more-{parent_id}-{batch[PAGE_SIZE - 1]["id"]}'
            self.tree.insert(parent_item, "end", iid=iid, text="次の250件を表示（ダブルクリック）…")
            self.more_nodes[iid] = (parent_item, file_id, parent_id, batch[PAGE_SIZE - 1]["id"])

    def expand_tree(self, iid):
        pending = iid + "-pending"
        if self.tree.exists(pending):
            self.tree.delete(pending)
            self.add_children(iid, self.file_id(), self.tree_nodes[iid])

    def activate_more(self, event=None):
        iid = self.tree.focus()
        if iid in self.more_nodes:
            args = self.more_nodes.pop(iid)
            self.tree.delete(iid)
            self.add_children(*args)

    def tree_selected(self, event=None):
        if self.task:
            return
        iid = self.tree.focus()
        if iid in self.tree_nodes:
            self.selected = self.store.node(self.file_id(), self.tree_nodes[iid])
            n = self.selected
            attrs = json.dumps(json.loads(n["attrs"]), ensure_ascii=False, indent=2)
            text = self.store.direct_text(n)
            self.set_detail(f'{n["name"]}\n\nインスタンスパス\n{self.store.instance_path(n)}\n\n構造パス\n{n["path"]}\n\nNamespace: {n["uri"] or "なし"}\n深さ: {n["depth"]} / 子ノード: {n["children"]}\n\n属性\n{attrs[:65536]}\n\nテキスト（先頭64Ki文字）\n{text[:65536]}')

    def set_detail(self, text):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("end", text)
        self.detail.configure(state="disabled")

    def jump_to_node(self, n):
        if self.task:
            return
        self.set_file(n["file"])
        ancestors, current = [], n
        while current:
            ancestors.append(current)
            current = self.store.node(n["file"], current["parent"]) if current["parent"] else None
        parent = ""
        for item in reversed(ancestors):
            iid = f'n{item["id"]}'
            if not self.tree.exists(iid):
                self.tree.insert(parent, "end", iid=iid, text=item["name"])
                self.tree_nodes[iid] = item["id"]
                if item["children"]:
                    self.tree.insert(iid, "end", iid=iid + "-pending", text="読み込み…")
            self.tree.item(iid, open=True)
            self.expand_tree(iid)
            parent = iid
        self.tree.focus(parent)
        self.tree.selection_set(parent)
        self.tree.see(parent)
        self.tree_selected()
        self.tabs.select(self.pages["構造ビュー"])

    def focus_graph(self):
        if self.selected:
            self.graph.load(self.selected)
            self.tabs.select(self.pages["グラフ"])

    def show_fragment(self):
        if self.selected:
            try:
                self.set_detail("解析結果から再構成したXML断片です。元の字句表現とは異なります。\n\n" + xml_fragment(self.store, self.selected))
            except AppError as error:
                self.report(error)

    def start_search(self):
        if not self.file_id():
            return
        targets = {"要素名": "name", "属性名": "attribute-name", "属性値": "attribute-value", "テキスト": "text", "パス": "path"}
        file_id, target, query = self.file_id(), targets[self.search_target.get()], self.search_text.get()
        exact, case = self.search_exact.get(), self.search_case.get()
        self.search_file = file_id
        self.run("XML検索", lambda task: search(self.store, file_id, target, query, exact, case, task), self.show_search)

    def show_search(self, result):
        clear_tree(self.search_results)
        for row in result["results"][:1000]:
            self.search_results.insert("", "end", iid=str(row["id"]), values=(row["name"], row["path"]))
        self.status.set(f'検索 {result["count"]:,}件（画面は先頭1000件・保持上限1万件）')

    def search_selected(self, event=None):
        items = self.search_results.selection()
        if items:
            n = self.store.node(self.search_file, int(items[0]))
            if n:
                self.jump_to_node(n)

    def _compare_page(self):
        page = self.contents["XML比較"]
        bar = FlowFrame(page)
        bar.pack(fill="x")
        ttk.Label(bar, text="XML A").pack(side="left")
        self.compare_a = ttk.Combobox(bar, state="readonly", width=30)
        self.compare_a.pack(side="left", padx=5)
        ttk.Label(bar, text="XML B").pack(side="left")
        self.compare_b = ttk.Combobox(bar, state="readonly", width=30)
        self.compare_b.pack(side="left", padx=5)
        self.compare_mode = ttk.Combobox(bar, state="readonly", values=("完全比較", "構造比較", "値比較"), width=12)
        self.compare_mode.set("完全比較")
        self.compare_mode.pack(side="left", padx=5)
        ttk.Label(bar, text="キー属性").pack(side="left")
        self.compare_key = tk.StringVar(value="id")
        ttk.Entry(bar, textvariable=self.compare_key, width=14).pack(side="left", padx=4)
        options = FlowFrame(page)
        options.pack(fill="x", pady=8)
        self.compare_flags = {}
        for key, label, default in (("trim", "空白を除いて比較", True), ("comments", "コメントを含む", False),
                                    ("processing_instructions", "PIを含む", False), ("ignore_order", "順序を無視", False),
                                    ("verify_hashes", "ハッシュ一致でも厳密照合", False)):
            variable = tk.BooleanVar(value=default)
            self.compare_flags[key] = variable
            ttk.Checkbutton(options, text=label, variable=variable).pack(side="left", padx=4)
        actions = FlowFrame(page)
        actions.pack(fill="x")
        ttk.Button(actions, text="比較を開始", command=self.start_compare).pack(side="left", padx=3)
        self.diff_filter = ttk.Combobox(actions, values=("すべて", *DIFF_LABELS.values()), state="readonly", width=12)
        self.diff_filter.set("すべて")
        self.diff_filter.pack(side="left", padx=5)
        self.diff_filter.bind("<<ComboboxSelected>>", lambda e: self.show_differences(reset=True))
        ttk.Button(actions, text="前の1000件", command=lambda: self.diff_page(-1)).pack(side="left", padx=3)
        ttk.Button(actions, text="次の1000件", command=lambda: self.diff_page(1)).pack(side="left", padx=3)
        ttk.Button(actions, text="差分XLSX保存", command=self.export_differences).pack(side="left", padx=3)
        ttk.Button(actions, text="Aの要素へ", command=lambda: self.jump_difference("a")).pack(side="left", padx=3)
        ttk.Button(actions, text="Bの要素へ", command=lambda: self.jump_difference("b")).pack(side="left", padx=3)
        ttk.Button(actions, text="左右グラフ", command=self.compare_graphs).pack(side="left", padx=3)
        self.diff_summary = ttk.Label(page, text="異なる2つのXMLを選んで比較してください。")
        self.diff_summary.pack(anchor="w", pady=8)
        self.differences = make_table(page, ["差分", "XML A", "XML B", "根拠", "省略範囲件数"], {"XML A": 350, "XML B": 350}, height=8)
        for kind, color in DIFF_COLORS.items():
            self.differences.tag_configure(kind, foreground=color)
        self.differences.bind("<<TreeviewSelect>>", self.show_diff_detail)
        self.diff_detail = tk.Text(page, height=5, wrap="word", font=("Yu Gothic UI", 10))
        self.diff_detail.pack(side="bottom", fill="x", pady=5)
        self.differences.master.pack_forget()
        self.differences.master.pack(fill="both", expand=True, pady=5)

    def start_compare(self):
        a, b = self.file_labels.get(self.compare_a.get()), self.file_labels.get(self.compare_b.get())
        if not a or not b or a == b:
            self.report(AppError("COMPARE_PAIR: 異なる2つのXMLを選択してください。"))
            return
        options = DiffOptions(mode={"完全比較": "full", "構造比較": "structure", "値比較": "value"}[self.compare_mode.get()],
                              key=self.compare_key.get(), **{k: v.get() for k, v in self.compare_flags.items()})
        self.run("XML比較", lambda task: compare(self.store, a, b, options, task), self.compared)

    def compared(self, result):
        self.comparison = result
        self.diff_offset = 0
        self.show_differences()
        self.update_statistics()

    def diff_kind(self):
        return next((k for k, v in DIFF_LABELS.items() if v == self.diff_filter.get()), "")

    def show_differences(self, reset=False):
        if self.task or not self.comparison:
            return
        if reset:
            self.diff_offset = 0
        rows = self.store.differences(self.comparison["id"], self.diff_kind(), self.diff_offset)
        clear_tree(self.differences)
        self.diff_rows = {str(r["seq"]): r for r in rows}
        for r in rows:
            self.differences.insert("", "end", iid=str(r["seq"]), tags=(r["type"],),
                                    values=(DIFF_LABELS[r["type"]], r["path"] if r["a"] else "— 存在しません", r["path"] if r["b"] else "— 存在しません", r["confidence"], r["covered"]))
        counts = "  /  ".join(f'{DIFF_LABELS[k]} {v:,}' for k, v in self.comparison["counts"].items())
        self.diff_summary.configure(text=f'{counts}  ｜ {self.comparison["seconds"]:.2f}秒 ｜ {self.diff_offset + 1}件目から表示')

    def diff_page(self, direction):
        self.diff_offset = max(0, self.diff_offset + direction * 1000)
        self.show_differences()

    def selected_difference(self):
        selected = self.differences.selection()
        return self.diff_rows.get(selected[0]) if selected else None

    def show_diff_detail(self, event=None):
        row = self.selected_difference()
        if row:
            self.diff_detail.delete("1.0", "end")
            self.diff_detail.insert("end", f'{row["details"]}\n属性: {row["attribute"]}\nXML A（先頭4000文字）: {row["old"]}\nXML B（先頭4000文字）: {row["new"]}')

    def jump_difference(self, side):
        row = self.selected_difference()
        if row and row[side] and self.comparison:
            n = self.store.node(self.comparison[side], row[side])
            if n:
                self.jump_to_node(n)

    def compare_graphs(self):
        row = self.selected_difference()
        if not row or not self.comparison:
            return
        window = tk.Toplevel(self.root)
        window.title("左右グラフ比較 — " + DIFF_LABELS[row["type"]])
        window.geometry("1200x700")
        panes = ttk.Panedwindow(window, orient="horizontal")
        panes.pack(fill="both", expand=True)
        for side in ("a", "b"):
            panel = ttk.Frame(panes)
            panes.add(panel, weight=1)
            ttk.Label(panel, text="XML " + side.upper()).pack(anchor="w")
            n = self.store.node(self.comparison[side], row[side]) if row[side] else None
            if n:
                graph = Graph(panel, self.store, self.jump_to_node, self.report)
                graph.pack(fill="both", expand=True)
                graph.load(n)
            else:
                ttk.Label(panel, text="対応するノードがありません。").pack(pady=50)

    def export_differences(self):
        if not self.comparison or self.task:
            return
        target = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excelブック", "*.xlsx")], initialfile="xml-differences.xlsx")
        if not target:
            return
        template = Template(columns=[Column("", "差分")], source_file=False)
        comparison, kind = self.comparison["id"], self.diff_kind()
        self.run("差分Excel保存", lambda task: write_xlsx(target, difference_rows(self.store, comparison, kind, task), template, task))

    def _export_page(self):
        container = self.contents["Excel出力"]
        whole = ttk.LabelFrame(container, text="簡単出力モード（すべての要素・属性）", padding=8)
        whole.pack(fill="x", pady=(0, 10))
        ttk.Label(whole, text="読み込み済みの全XML・全階層を出力します。親・空要素・繰り返し値も含み、以下の機械学習設定や抽出条件では絞り込みません。",
                  wraplength=900).pack(anchor="w")
        actions = FlowFrame(whole)
        actions.pack(fill="x", pady=5)
        ttk.Label(actions, text="表の形式").pack(side="left", padx=3)
        self.table_orientation = ttk.Combobox(actions, state="readonly", values=(WIDE, LONG), width=9)
        self.table_orientation.set(WIDE)
        self.table_orientation.pack(side="left", padx=3)
        self.table_orientation.bind("<<ComboboxSelected>>", lambda _: self.reset_table_preview())
        ttk.Button(actions, text="全要素プレビュー", command=self.preview_all_table).pack(side="left", padx=3)
        self.simple_export_button = ttk.Button(actions, text="全要素をExcel保存", command=self.export_table)
        self.simple_export_button.pack(side="left", padx=3)
        ttk.Label(whole, text="横持ち：1XML＝1行、ノード/項目名＝列。縦持ち：ノード・項目名・値を行で表示。値は文字列として保持します。",
                  wraplength=900).pack(anchor="w")
        self.table_summary = ttk.Label(whole, text="XMLを読み込んで、全要素プレビューを実行してください。", wraplength=900)
        self.table_summary.pack(anchor="w")
        self.table_preview = make_table(whole, ["プレビュー"], height=4)
        simple = ttk.LabelFrame(container, text="機械学習向け出力（1行の単位を指定・回帰／分類向け）", padding=8)
        simple.pack(fill="x", pady=(0, 10))
        ttk.Label(simple, text="読み込み済みの全XMLから、1件＝1行・1項目＝1列の表を作成します。数値・真偽値・文字列を全件で判定し、欠損は空欄にします。",
                  wraplength=900).pack(anchor="w")
        self.ml_auto = "自動（繰り返し要素を候補から選択）"
        self.ml_file_unit = "XMLファイル単位（1ファイル＝1行）"
        self.ml_record_path = tk.StringVar(value=self.ml_auto)
        ttk.Label(simple, text="1行の単位（候補を選択、または要素の絶対パスを入力）").pack(anchor="w", pady=(6, 0))
        self.ml_record_choice = ttk.Combobox(simple, textvariable=self.ml_record_path,
                                           values=(self.ml_auto, self.ml_file_unit))
        self.ml_record_choice.pack(fill="x")
        ttk.Label(simple, text="目的変数（任意・予測したい項目。候補はプレビュー後に表示）").pack(anchor="w", pady=(5, 0))
        self.ml_target = ttk.Combobox(simple, values=("未指定",))
        self.ml_target.set("未指定")
        self.ml_target.pack(fill="x")
        actions = FlowFrame(simple)
        actions.pack(fill="x", pady=5)
        ttk.Button(actions, text="構造を確認・プレビュー", command=self.preview_ml).pack(side="left")
        self.ml_export_button = ttk.Button(actions, text="学習データをExcel保存", command=self.export_ml)
        self.ml_export_button.pack(side="left")
        self.ml_summary = ttk.Label(simple, text="自動候補は全XMLで共通する最も浅い繰り返し要素です。保存前に1行の意味を確認してください。", wraplength=900)
        self.ml_summary.pack(anchor="w")
        self.ml_preview = make_table(simple, ["プレビュー"], height=4)
        ttk.Label(simple, text="学習データ／出典／項目定義／出力情報の4種類のシートを保存。ID候補・定数列は項目定義で確認できます。", wraplength=900).pack(anchor="w")
        self.all_elements_button = ttk.Button(container, text="従来の要素一覧を保存（属性をJSONで表示）", command=self.export_all_elements)
        self.all_elements_button.pack(anchor="w", pady=(0, 6))
        ttk.Label(container, text="詳細出力：対象XML・列・条件を指定する場合は、以下を設定します。",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 4))
        horizontal = ttk.Scrollbar(container, orient="horizontal")
        horizontal.pack(side="bottom", fill="x")
        scroll = ttk.Scrollbar(container, orient="vertical")
        scroll.pack(side="right", fill="y")
        self.export_scroll = tk.Canvas(container, highlightthickness=0, yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.export_scroll.pack(fill="both", expand=True)
        scroll.configure(command=self.export_scroll.yview)
        horizontal.configure(command=self.export_scroll.xview)
        page = ttk.Frame(self.export_scroll)
        window = self.export_scroll.create_window((0, 0), window=page, anchor="nw")
        self.export_scroll.bind("<Configure>", lambda e: self.export_scroll.itemconfigure(window, width=max(e.width, page.winfo_reqwidth())))
        page.bind("<Configure>", lambda e: (self.export_scroll.itemconfigure(window, width=max(self.export_scroll.winfo_width(), page.winfo_reqwidth())), self.export_scroll.configure(scrollregion=self.export_scroll.bbox("all"))))
        upper = ttk.Frame(page)
        upper.pack(fill="x")
        selection = ttk.LabelFrame(upper, text="対象XML（複数選択可）")
        selection.pack(side="left", fill="y", padx=(0, 12))
        self.export_files = tk.Listbox(selection, height=4, width=35, selectmode="extended", exportselection=False)
        self.export_files.pack(fill="both", expand=True)
        settings = ttk.Frame(upper)
        settings.pack(side="left", fill="both", expand=True)
        self.record_path = tk.StringVar()
        ttk.Label(settings, text="1行の基準となる要素パス").grid(row=0, column=0, sticky="w")
        ttk.Entry(settings, textvariable=self.record_path, width=55).grid(row=0, column=1, columnspan=3, sticky="ew", padx=4)
        self.export_layout = ttk.Combobox(settings, state="readonly", values=("レコード単位", "ノード単位", "階層を列展開"), width=16)
        self.export_layout.set("レコード単位")
        self.export_layout.grid(row=1, column=0, pady=5)
        self.export_group = ttk.Combobox(settings, state="readonly", values=("1シートに統合", "XMLごとに別シート", "XMLごとに別ファイル"), width=21)
        self.export_group.set("1シートに統合")
        self.export_group.grid(row=1, column=1, padx=4)
        ttk.Button(settings, text="階層列を追加", command=self.add_hierarchy_columns).grid(row=1, column=2)
        ttk.Button(settings, text="メタ情報を追加", command=self.add_metadata).grid(row=1, column=3)
        settings.columnconfigure(1, weight=1)
        self.column_table = make_table(page, ["出力", "列名", "相対パス", "型", "繰り返し", "欠損"], {"相対パス": 330}, height=6)
        self.column_table.bind("<<TreeviewSelect>>", self.edit_column)
        editor = FlowFrame(page)
        editor.pack(fill="x", pady=4)
        self.column_fields = {}
        for name, key, width in (("列名", "name", 18), ("相対パス", "path", 28)):
            ttk.Label(editor, text=name).pack(side="left", padx=3)
            value = tk.StringVar()
            ttk.Entry(editor, textvariable=value, width=width).pack(side="left")
            self.column_fields[key] = value
        for key, choices in (("type", ("string", "number", "date", "boolean")), ("repeat", ("join", "first", "rows", "sheet")), ("missing", ("blank", "error"))):
            field = ttk.Combobox(editor, values=choices, state="readonly", width=9)
            field.set(choices[0])
            field.pack(side="left", padx=3)
            self.column_fields[key] = field
        ttk.Button(editor, text="列へ反映", command=self.apply_column).pack(side="left", padx=3)
        bar = FlowFrame(page)
        bar.pack(fill="x", pady=3)
        for text, action in (("出力ON/OFF", self.toggle_column), ("上へ", lambda: self.move_column(-1)),
                             ("下へ", lambda: self.move_column(1)), ("列を追加", self.add_column), ("列を削除", self.remove_column)):
            ttk.Button(bar, text=text, command=action).pack(side="left", padx=3)
        ttk.Label(bar, text="文字列型を標準にし、先頭ゼロ・大きなIDを保持します。", style="Muted.TLabel").pack(side="left", padx=10)
        filters_box = ttk.LabelFrame(page, text="抽出条件（全行共通）")
        filters_box.pack(fill="x", pady=5)
        filters = FlowFrame(filters_box)
        filters.pack(fill="x", pady=5)
        self.filter_path, self.filter_value, self.filter_max = tk.StringVar(), tk.StringVar(), tk.StringVar()
        ttk.Entry(filters, textvariable=self.filter_path, width=23).pack(side="left", padx=4)
        self.filter_op = ttk.Combobox(filters, values=("equals", "contains", "range", "empty", "exists"), state="readonly", width=10)
        self.filter_op.set("equals")
        self.filter_op.pack(side="left", padx=4)
        ttk.Entry(filters, textvariable=self.filter_value, width=18).pack(side="left")
        ttk.Label(filters, text="～").pack(side="left")
        ttk.Entry(filters, textvariable=self.filter_max, width=15).pack(side="left")
        self.filter_logic = ttk.Combobox(filters, values=("AND", "OR"), state="readonly", width=6)
        self.filter_logic.set("AND")
        self.filter_logic.pack(side="left", padx=5)
        ttk.Button(filters, text="条件追加", command=self.add_filter).pack(side="left", padx=3)
        ttk.Button(filters, text="条件をクリア", command=self.clear_filters).pack(side="left", padx=3)
        self.filter_notice = ttk.Label(page, text="条件なし", wraplength=1150)
        self.filter_notice.pack(anchor="w")
        formats = FlowFrame(page)
        formats.pack(fill="x", pady=4)
        self.sheet_name = tk.StringVar(value=self.template.sheet_name)
        self.sheet_rows = tk.StringVar(value=str(self.template.sheet_rows))
        self.column_width = tk.StringVar(value="24")
        self.delimiter = tk.StringVar(value=" | ")
        for text, variable, width in (("シート名", self.sheet_name, 12), ("分割行数", self.sheet_rows, 9), ("列幅", self.column_width, 4), ("結合区切り", self.delimiter, 6)):
            ttk.Label(formats, text=text).pack(side="left", padx=3)
            ttk.Entry(formats, textvariable=variable, width=width).pack(side="left")
        self.export_flags = {}
        for key, text in (("source_file", "SourceFile"), ("header", "ヘッダー"), ("auto_filter", "フィルター"), ("freeze", "固定行")):
            var = tk.BooleanVar(value=True)
            self.export_flags[key] = var
            ttk.Checkbutton(formats, text=text, variable=var).pack(side="left", padx=3)
        actions = FlowFrame(page)
        actions.pack(fill="x", pady=4)
        for text, action in (("プレビュー100行", self.start_preview), ("XLSX保存", lambda: self.export_output("xlsx")),
                             ("CSV保存", lambda: self.export_output("csv")), ("設定JSON保存", self.save_template), ("設定JSON読込", self.load_template)):
            ttk.Button(actions, text=text, command=action).pack(side="left", padx=3)
        self.preview_text = tk.Text(page, height=5, wrap="none", font=("Yu Gothic UI", 10))
        self.preview_text.pack(fill="both", expand=True)

    def render_columns(self, selected=None):
        clear_tree(self.column_table)
        for i, c in enumerate(self.template.columns):
            self.column_table.insert("", "end", iid=str(i), values=("☑" if c.enabled else "☐", c.name, c.path, c.type, c.repeat, c.missing))
        if selected is not None and self.column_table.exists(str(selected)):
            self.column_table.selection_set(str(selected))

    def column_index(self):
        selected = self.column_table.selection()
        return int(selected[0]) if selected else None

    def edit_column(self, event=None):
        index = self.column_index()
        if index is not None:
            column = self.template.columns[index]
            for key, widget in self.column_fields.items():
                widget.set(getattr(column, key))

    def apply_column(self):
        index = self.column_index()
        if index is not None and not self.task:
            values = {k: w.get() for k, w in self.column_fields.items()}
            self.template.columns[index] = replace(self.template.columns[index], **values)
            self.render_columns(index)

    def toggle_column(self):
        index = self.column_index()
        if index is not None and not self.task:
            self.template.columns[index].enabled = not self.template.columns[index].enabled
            self.render_columns(index)

    def move_column(self, direction):
        index = self.column_index()
        if index is not None and 0 <= index + direction < len(self.template.columns) and not self.task:
            columns = self.template.columns
            columns[index], columns[index + direction] = columns[index + direction], columns[index]
            self.render_columns(index + direction)

    def add_column(self):
        if not self.task and len(self.template.columns) < 256:
            self.template.columns.append(Column(".", "新しい列"))
            self.render_columns(len(self.template.columns) - 1)

    def remove_column(self):
        index = self.column_index()
        if index is not None and not self.task:
            self.template.columns.pop(index)
            self.render_columns()

    def use_record(self):
        if self.task or not self.selected or self.selected["kind"] != "element":
            return
        n = dict(self.selected)

        def done(columns):
            self.template.record_path = n["path"]
            self.template.columns = columns or [Column("$name", "要素名")]
            self.record_path.set(n["path"])
            self.render_columns(0)
            self.tabs.select(self.pages["Excel出力"])

        self.run("出力候補を作成", lambda task: candidate_columns(self.store, n, task), done)

    def add_metadata(self):
        if not self.task:
            for path, name in (("$name", "要素名"), ("$path", "パス"), ("$namespace", "Namespace"), ("$depth", "深さ"), ("$attributes", "属性一覧"), ("../$name", "親要素")):
                if not any(c.path == path for c in self.template.columns):
                    self.template.columns.append(Column(path, name))
            self.render_columns()

    def add_hierarchy_columns(self):
        if not self.task:
            depth = max((f["info"]["max_depth"] for f in self.files), default=0)
            for index in range(min(depth + 1, 32)):
                path = f"$level:{index}"
                if not any(c.path == path for c in self.template.columns):
                    self.template.columns.append(Column(path, f"階層{index}"))
            self.render_columns()

    def add_filter(self):
        if not self.task and len(self.template.filters) < 50:
            self.template.filters.append({"path": self.filter_path.get(), "op": self.filter_op.get(), "value": self.filter_value.get(), "max": self.filter_max.get()})
            self.show_filter_notice()

    def clear_filters(self):
        if not self.task:
            self.template.filters.clear()
            self.show_filter_notice()

    def show_filter_notice(self):
        self.filter_notice.configure(text=" / ".join(f'{f["path"]} {f["op"]}' for f in self.template.filters) or "条件なし")

    def export_payload(self):
        try:
            self.template.record_path = self.record_path.get()
            self.template.layout = {"レコード単位": "records", "ノード単位": "nodes", "階層を列展開": "hierarchy"}[self.export_layout.get()]
            self.template.grouping = {"1シートに統合": "merged", "XMLごとに別シート": "sheets", "XMLごとに別ファイル": "files"}[self.export_group.get()]
            self.template.sheet_name = self.sheet_name.get()
            self.template.sheet_rows = int(self.sheet_rows.get())
            self.template.column_width = float(self.column_width.get())
            self.template.delimiter = self.delimiter.get()
            self.template.logic = self.filter_logic.get()
            for key, variable in self.export_flags.items():
                setattr(self.template, key, variable.get())
            template = Template.from_json(self.template.to_json())
            ids = [self.file_labels[self.export_files.get(i)] for i in self.export_files.curselection()]
            if not ids:
                raise AppError("FILES_EMPTY: 出力対象XMLを選択してください。")
            return ids, template
        except (ValueError, KeyError):
            raise AppError("EXPORT_SETTINGS: 列・シート行数・列幅・対象XMLを確認してください。") from None

    def start_preview(self):
        try:
            ids, template = self.export_payload()
            self.run("抽出プレビュー", lambda task: preview(self.store, ids, template, task), self.show_preview)
        except AppError as error:
            self.report(error)

    def show_preview(self, result):
        rows, more = result
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("end", f'先頭{len(rows)}行' + ("（続きあり）" if more else "") + "。総行数は出力完了時に確定します。\n")
        previous = None
        for row in rows:
            if previous != row.sheet:
                self.preview_text.insert("end", "\n[" + row.sheet + "]\n" + "\t".join(row.headers) + "\n")
                previous = row.sheet
            self.preview_text.insert("end", "\t".join(display(v) for v in row.cells) + "\n")
        if not rows:
            self.preview_text.insert("end", "該当するレコードがありません。基準パスと条件を確認してください。")
        elif all(all(v is None or v == "" for v in row.cells[int(self.template.source_file):]) for row in rows):
            self.preview_text.insert("end", "\n警告: プレビューで抽出項目の値が見つかりません。設定したパスを確認してください。")

    def reset_table_preview(self):
        clear_tree(self.table_preview)
        self.table_summary.configure(text="形式を選び、全要素プレビューを実行してください。")

    def preview_all_table(self):
        orientation = self.table_orientation.get()
        self.run("全要素確認", lambda task: preview_table(self.store, orientation, task), self.show_table_preview)

    def show_table_preview(self, result):
        if result["orientation"] != self.table_orientation.get():
            return
        clear_tree(self.table_preview)
        columns = tuple(f"c{i}" for i in range(len(result["headers"])))
        self.table_preview.configure(columns=columns)
        for column, name in zip(columns, result["headers"]):
            self.table_preview.heading(column, text=name)
            self.table_preview.column(column, width=180, minwidth=60, stretch=False)
        for row in result["rows"]:
            self.table_preview.insert("", "end", values=[display(value)[:80].replace("\n", "↵") for value in row])
        count = f'・{result["columns"]:,}項目' if result["columns"] is not None else ""
        self.table_summary.configure(text=f'{result["orientation"]}：{result["files"]:,}ファイル・{result["elements"]:,}要素{count}。プレビューは先頭20行・30列・各80文字まで。保存時はすべて出力します。')

    def export_table(self):
        if self.task:
            return
        if not self.store.files():
            self.report(AppError("FILES_EMPTY: 先にXMLファイルを読み込んでください。"))
            return
        orientation = self.table_orientation.get()
        target = filedialog.asksaveasfilename(title=f"簡単出力：全要素を{orientation}でExcel保存", defaultextension=".xlsx",
                                            filetypes=[("Excelブック", "*.xlsx")],
                                            initialfile="xml-all-wide.xlsx" if orientation == WIDE else "xml-all-long.xlsx")
        if not target:
            return
        if Path(target).suffix.lower() != ".xlsx":
            target += ".xlsx"
        def done(result):
            self.status.set(f'全要素・{result["orientation"]}保存 · 完了（{result["files"]:,}ファイル・{result["elements"]:,}要素・{result["values"]:,}値）')
        self.run("全要素保存", lambda task: save_table(self.store, target, orientation, task), done)

    def ml_path(self):
        value = self.ml_record_path.get().strip()
        return None if value in ("", self.ml_auto) else "" if value == self.ml_file_unit else value

    def reset_ml_preview(self):
        self.ml_record_path.set(self.ml_auto)
        self.ml_record_choice.configure(values=(self.ml_auto, self.ml_file_unit))
        self.ml_target.set("未指定")
        self.ml_target.configure(values=("未指定",))
        clear_tree(self.ml_preview)
        self.ml_summary.configure(text="XMLを読み込んで、構造を確認・プレビューしてください。")

    def preview_ml(self):
        path = self.ml_path()
        self.run("学習データ確認", lambda task: preview_export(self.store, path, task), self.show_ml_preview)

    def show_ml_preview(self, result):
        plan, rows = result
        self.ml_record_choice.configure(values=(self.ml_auto, self.ml_file_unit, *plan.candidates))
        self.ml_record_path.set(plan.record_path or self.ml_file_unit)
        self.ml_target.configure(values=("未指定", *plan.headers))
        if self.ml_target.get() not in ("未指定", *plan.headers):
            self.ml_target.set("未指定")
        clear_tree(self.ml_preview)
        # Keep the native preview responsive even when a record has hundreds of features.
        columns = tuple(f"c{i}" for i in range(min(30, len(plan.headers))))
        self.ml_preview.configure(columns=columns)
        for column, name in zip(columns, plan.headers):
            self.ml_preview.heading(column, text=name)
            self.ml_preview.column(column, width=150, minwidth=60, stretch=False)
        for row in rows:
            self.ml_preview.insert("", "end", values=[display(value)[:80] for value in row.cells[:30]])
        self.ml_summary.configure(text=f"{len(plan.files):,}ファイル・{plan.rows:,}件・{len(plan.features):,}項目。プレビューは先頭20件・30項目・各80文字まで。保存時は全件・全項目を出力します。")

    def export_ml(self):
        if self.task:
            return
        if not self.store.files():
            self.report(AppError("FILES_EMPTY: 先にXMLファイルを読み込んでください。"))
            return
        path = self.ml_path()
        target_column = self.ml_target.get().strip()
        if target_column == "未指定":
            target_column = ""
        target = filedialog.asksaveasfilename(title="機械学習向け出力：学習データをExcel保存", defaultextension=".xlsx",
                                            filetypes=[("Excelブック", "*.xlsx")], initialfile="xml-learning-data.xlsx")
        if not target:
            return
        if Path(target).suffix.lower() != ".xlsx":
            target += ".xlsx"
        def done(result):
            self.ml_record_path.set(result["record_path"] or self.ml_file_unit)
            self.status.set(f'学習データ保存 · 完了（{result["records"]:,}件・{result["features"]:,}項目）')
        self.run("学習データ保存", lambda task: save_export(self.store, target, path, target_column, task), done)

    def export_all_elements(self):
        if self.task:
            return
        files = self.store.files()
        if not files:
            self.report(AppError("FILES_EMPTY: 先にXMLファイルを読み込んでください。"))
            return
        target = filedialog.asksaveasfilename(title="全要素一覧：全XMLの全要素をExcel保存",
                                            defaultextension=".xlsx", filetypes=[("Excelブック", "*.xlsx")],
                                            initialfile="xml-all-elements.xlsx")
        if not target:
            return
        if Path(target).suffix.lower() != ".xlsx":
            target += ".xlsx"
        elements = sum(file["info"]["elements"] for file in files)

        def done(result):
            self.status.set(f'全要素一覧 · 完了（{len(files):,}ファイル・{elements:,}要素・{result["rows"]:,}行）')

        self.run("全要素一覧", lambda task: write_xlsx(target, all_element_rows(self.store, task), simple_template(), task), done)

    def export_output(self, format):
        if self.task:
            return
        try:
            ids, template = self.export_payload()
            if format == "csv" and any(c.enabled and c.repeat == "sheet" for c in template.columns):
                raise AppError("CSV_SHEETS: 別シート出力にはXLSXを使用してください。")
            target = filedialog.asksaveasfilename(defaultextension="." + format, filetypes=[("Excelブック" if format == "xlsx" else "CSV", "*." + format)], initialfile="xml-export." + format)
            if not target:
                return
            if Path(target).suffix.lower() != "." + format:
                target += "." + format
            targets = [Path(target)]
            groups = [ids]
            if template.grouping == "files":
                groups = [[file] for file in ids]
                targets = [Path(target).with_stem(Path(target).stem + f"-{i + 1}") for i in range(len(ids))]
                if any(t.exists() for t in targets) and not messagebox.askyesno("出力ファイル", "連番の出力先に既存ファイルがあります。上書きしますか？"):
                    return

            def action(task):
                writer = write_xlsx if format == "xlsx" else write_csv
                return [writer(dest, extract(self.store, group, template, task), template, task) for dest, group in zip(targets, groups)]

            self.run("XLSX保存" if format == "xlsx" else "CSV保存", action)
        except AppError as error:
            self.report(error)

    def save_template(self):
        if self.task:
            return
        try:
            _, template = self.export_payload()
            path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("設定JSON", "*.json")], initialfile="xml-export-template.json")
            if path:
                with atomic_target(path) as target:
                    target.write_text(template.to_json(), encoding="utf-8")
                self.status.set("設定JSONを保存しました。XML本体は含まれません。")
        except (AppError, OSError) as error:
            self.report(error)

    def load_template(self):
        if self.task:
            return
        path = filedialog.askopenfilename(filetypes=[("設定JSON", "*.json")])
        if path:
            try:
                if Path(path).stat().st_size > 262144:
                    raise AppError("TEMPLATE_LIMIT: 設定JSONは256KiB以内にしてください。")
                self.apply_template(Template.from_json(Path(path).read_text(encoding="utf-8-sig")))
                self.status.set("設定を読み込みました。異なるXMLへの適用時はプレビューで値を確認してください。")
            except (AppError, OSError, UnicodeError) as error:
                self.report(error)

    def apply_template(self, template):
        self.template = template
        self.record_path.set(template.record_path)
        self.sheet_name.set(template.sheet_name)
        self.sheet_rows.set(str(template.sheet_rows))
        self.column_width.set(str(template.column_width))
        self.delimiter.set(template.delimiter)
        self.filter_logic.set(template.logic)
        self.export_layout.set({"records": "レコード単位", "nodes": "ノード単位", "hierarchy": "階層を列展開"}[template.layout])
        self.export_group.set({"merged": "1シートに統合", "sheets": "XMLごとに別シート", "files": "XMLごとに別ファイル"}[template.grouping])
        for key, variable in self.export_flags.items():
            variable.set(getattr(template, key))
        self.render_columns()
        self.show_filter_notice()

    def update_statistics(self):
        self.statistics.delete("1.0", "end")
        for f in self.files:
            info = f["info"]
            text = f'{f["name"]}\n要素 {info["elements"]:,} / 属性 {info["attributes"]:,} / 文書ノード {info["nodes"]:,} / 最大深さ {info["max_depth"]}\n解析全体 {info["parse_seconds"]:.3f} 秒\n'
            for title, key in (("Namespace別", "namespaces"), ("要素名別・上位30", "names"), ("深さ別", "depths"), ("子ノード数の分布", "children")):
                pairs = sorted(info[key].items(), key=lambda p: p[1], reverse=True)[:30]
                largest = max((v for _, v in pairs), default=1) or 1
                text += "\n" + title + "\n" + "\n".join(f'{name}: {value:,} {"█" * max(1, round(value / largest * 24))}' for name, value in pairs) + "\n"
            self.statistics.insert("end", text + "\n")
        if self.comparison:
            counts = self.comparison["counts"]
            changed = sum(v for k, v in counts.items() if k != "UNCHANGED")
            total = sum(counts.values())
            self.statistics.insert("end", "\n比較結果\n" + "\n".join(f'{DIFF_LABELS[k]}: {v:,}' for k, v in counts.items()) + f'\n差分率 = (追加+削除+変更+移動+曖昧) / 全分類の合計 = {changed / total * 100 if total else 0:.2f}%\nサブツリー省略範囲の件数を含み、構造比較では要素数、完全・値比較では文書ノード数を使用します。\n')

    def _settings_page(self):
        page = self.contents["設定"]
        ttk.Label(page, text="保存とプライバシー", font=("Yu Gothic UI", 16, "bold")).pack(anchor="w", pady=10)
        ttk.Label(page, text="解析結果は一時フォルダ内のSQLiteへ保存します。通常終了時に削除します。\n作業を続けたい場合だけ、作業ファイルを明示的に保存してください。作業ファイルにはXMLの解析値が含まれます。\n異常終了時に一時フォルダが残る場合があります。共有PCではOSの利用者アカウントを分けてください。", wraplength=1000, justify="left").pack(anchor="w", pady=10)
        for text, command in (("作業ファイルを保存", self.save_workspace), ("作業ファイルを開く", self.open_workspace),
                              ("すべての作業データと設定を削除", self.clear_workspace), ("技術ログをJSON保存", self.save_log)):
            ttk.Button(page, text=text, command=command).pack(anchor="w", pady=6)
        ttk.Label(page, text="実行環境: Python 3.14.6 / Tkinter / SQLite / Expat / 標準ライブラリのみ\n安全上限: 深さ256・単一テキスト1Mi文字・未完了トークン4MiB・グラフ500ノード・ツリー1万ノード\n出力: 200万行まで逐次生成、Excelは1シート1048575データ行・1セル32767文字で制限\nCSVでは数式に見える文字列の先頭にアポストロフィを加えるため、値が変化します。", wraplength=1050, justify="left", style="Muted.TLabel").pack(anchor="w", pady=20)

    def save_workspace(self):
        if self.task:
            return
        path = filedialog.asksaveasfilename(defaultextension=".xmlwork", filetypes=[("XML作業ファイル", "*.xmlwork")], initialfile="workspace.xmlwork")
        if path:
            def action(task):
                with atomic_target(path) as output:
                    self.store.save_workspace(output)
            self.run("作業保存", action)

    def open_workspace(self):
        if self.task:
            return
        path = filedialog.askopenfilename(filetypes=[("XML作業ファイル", "*.xmlwork")])
        if path:
            self.run("作業読込", lambda task: self.store.load_workspace(path), lambda _: self.after_delete())

    def clear_workspace(self):
        if not self.task:
            def done(_):
                for child in self.root.winfo_children():
                    if isinstance(child, tk.Toplevel):
                        child.destroy()
                self.apply_template(Template())
                self.reset_ml_preview()
                self.reset_table_preview()
                for variable in (self.search_text, self.filter_path, self.filter_value, self.filter_max,
                                 self.column_fields["path"], self.column_fields["name"]):
                    variable.set("")
                for key, value in (("type", "string"), ("repeat", "join"), ("missing", "blank")):
                    self.column_fields[key].set(value)
                self.search_target.set("要素名")
                self.search_exact.set(False)
                self.search_case.set(False)
                self.filter_op.set("equals")
                self.diff_filter.set("すべて")
                self.compare_key.set("id")
                self.compare_mode.set("完全比較")
                for key, variable in self.compare_flags.items():
                    variable.set(key == "trim")
                self.after_delete()
                clear_tree(self.search_results)
                self.diff_detail.delete("1.0", "end")
                self.diff_summary.configure(text="比較結果はありません。")
                self.graph.load(None)
                self.logs.clear()
            self.run("全作業データ削除", lambda task: self.store.clear(), done)

    def save_log(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")], initialfile="xml-analyzer-log.json")
        if path:
            with atomic_target(path) as output:
                output.write_text(json.dumps(self.logs, ensure_ascii=False, indent=2), encoding="utf-8")

    def close(self):
        if self.task:
            if not messagebox.askyesno("処理中です", "処理を中断して終了しますか？"):
                return
            self.closing = True
            self.cancel()
        else:
            self.finish_close()

    def finish_close(self):
        if self.worker and self.worker.is_alive():
            self.root.after(50, self.finish_close)
            return
        self.root.after_cancel(self.poll_id)
        if self.resize_pending is not None:
            self.root.after_cancel(self.resize_pending)
        self.uninstall_drop()
        self.root.destroy()
        self.temporary.cleanup()
