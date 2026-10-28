"""Small native layout helpers: wrapping toolbars and scrollable tab contents."""
import tkinter as tk
from tkinter import ttk


class FlowFrame(ttk.Frame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, width=1, height=34, **kwargs)
        self.pack_propagate(False)
        self.pending = None
        self.bind("<Configure>", self.schedule)
        self.bind("<Map>", self.schedule)
        self.schedule()

    def destroy(self):
        if self.pending is not None:
            self.after_cancel(self.pending)
        super().destroy()

    def schedule(self, event=None):
        if self.pending is None:
            self.pending = self.after_idle(self.reflow)

    def reflow(self):
        self.pending = None
        width = max(1, self.winfo_width())
        children = self.winfo_children()
        for child in children:
            if child.winfo_manager() == "pack":
                child.pack_forget()
        x, y, row_height = 0, 0, 0
        for child in children:
            child_width = min(width, child.winfo_reqwidth())
            child_height = child.winfo_reqheight()
            if x and x + child_width > width:
                x, y, row_height = 0, y + row_height + 5, 0
            child.place(x=x, y=y, width=child_width, height=child_height)
            x += child_width + 6
            row_height = max(row_height, child_height)
        self.configure(height=max(1, y + row_height + 3))


class ScrollablePage(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        vertical = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vertical.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.body = ttk.Frame(self.canvas, padding=10)
        self.window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.pending = None
        self.canvas.bind("<Configure>", self.schedule)
        self.body.bind("<Configure>", self.schedule)

    def schedule(self, event=None):
        if self.pending is None:
            self.pending = self.after_idle(self.resize)

    def destroy(self):
        if self.pending is not None:
            self.after_cancel(self.pending)
        super().destroy()

    def resize(self):
        self.pending = None
        width = max(1, self.canvas.winfo_width())
        height = max(self.canvas.winfo_height(), self.body.winfo_reqheight())
        self.canvas.itemconfigure(self.window, width=width, height=height)
        self.canvas.configure(scrollregion=(0, 0, width, height))

    def reveal(self, widget):
        self.update_idletasks()
        self.resize()
        top = widget.winfo_rooty() - self.body.winfo_rooty()
        total = max(1, self.body.winfo_height())
        self.canvas.yview_moveto(max(0, (top - 8) / total))
        self.update_idletasks()
