"""阅读侧栏：目录 / 书签 / 高亮 三个标签页（拟物配色 + DPI 字体）。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from core.library import Book
from ui import theme as T


class Sidebar(tk.Frame):
    def __init__(self, master, reader, scale: float = 1.0, **kw):
        super().__init__(master, bg=T.PANEL, width=int(248 * scale), **kw)
        self.reader = reader
        self.s = scale
        self.book: Book | None = None
        self._iid_page: dict[str, int] = {}

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TNotebook", background=T.PANEL, borderwidth=0)
        style.configure("TNotebook.Tab", background=T.BAR, foreground=T.MUTED,
                        padding=(int(11 * scale), int(5 * scale)), borderwidth=0,
                        font=T.tk_font(10, scale=scale))
        style.map("TNotebook.Tab", background=[("selected", T.BTN)],
                  foreground=[("selected", T.BRASS)])
        style.configure("Treeview", background=T.BG_DEEP, foreground=T.BRASS_DIM,
                        fieldbackground=T.BG_DEEP, borderwidth=0,
                        rowheight=int(24 * scale), font=T.tk_font(10, scale=scale))
        style.map("Treeview", background=[("selected", T.BTN)],
                  foreground=[("selected", T.BRASS)])

        self.nb = ttk.Notebook(self)

        # ---- 目录 ----
        self.toc_frame = tk.Frame(self.nb, bg=T.BG_DEEP)
        self.toc_tree = ttk.Treeview(self.toc_frame, show="tree", selectmode="browse")
        vsb = ttk.Scrollbar(self.toc_frame, orient="vertical",
                            command=self.toc_tree.yview)
        self.toc_tree.configure(yscrollcommand=vsb.set)
        self.toc_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.toc_tree.bind("<<TreeviewSelect>>", self._on_toc_select)

        # ---- 书签 ----
        self.bm_frame = tk.Frame(self.nb, bg=T.BG_DEEP)
        self.bm_list = self._listbox(self.bm_frame, T.BRASS_DIM)
        self.bm_list.bind("<Double-Button-1>", self._on_bm_jump)
        bar = tk.Frame(self.bm_frame, bg=T.BG_DEEP)
        bar.pack(fill="x", padx=4, pady=(0, 4))
        self._btn(bar, "删除所选", self._del_bookmark).pack(side="right")

        # ---- 高亮 ----
        self.hl_frame = tk.Frame(self.nb, bg=T.BG_DEEP)
        self.hl_list = self._listbox(self.hl_frame, "#D9A03A")
        self.hl_list.bind("<Double-Button-1>", self._on_hl_jump)
        bar2 = tk.Frame(self.hl_frame, bg=T.BG_DEEP)
        bar2.pack(fill="x", padx=4, pady=(0, 4))
        self._btn(bar2, "删除所选", self._del_highlight).pack(side="right")

        self.nb.add(self.toc_frame, text=" 目录 ")
        self.nb.add(self.bm_frame, text=" 书签 ")
        self.nb.add(self.hl_frame, text=" 高亮 ")
        self.nb.pack(fill="both", expand=True)
        self.pack_propagate(False)

    def _listbox(self, parent, fg):
        lb = tk.Listbox(parent, bg=T.BG_DEEP, fg=fg, selectbackground=T.BTN,
                        selectforeground=T.BRASS, relief="flat",
                        highlightthickness=0, activestyle="none",
                        font=T.tk_font(10, scale=self.s))
        lb.pack(fill="both", expand=True, padx=4, pady=4)
        return lb

    def _btn(self, parent, text, cmd):
        b = tk.Button(parent, text=text, command=cmd, bg=T.BTN, fg=T.BRASS,
                      activebackground=T.BTN_HOVER, activeforeground=T.BRASS,
                      relief="flat", padx=int(9 * self.s),
                      pady=int(2 * self.s), font=T.tk_font(9, scale=self.s))
        b.bind("<Enter>", lambda e: b.configure(bg=T.BTN_HOVER))
        b.bind("<Leave>", lambda e: b.configure(bg=T.BTN))
        return b

    # ---------------- 刷新 ----------------
    def refresh(self, book: Book, toc=(), current_page: int = 0):
        self.book = book
        self.toc_tree.delete(*self.toc_tree.get_children())
        self._iid_page = {}
        for level, title, page in toc:
            title = (title or "").strip() or f"第 {page} 页"
            iid = self.toc_tree.insert("", "end",
                                       text="    " * max(level - 1, 0) + title,
                                       open=False)
            self._iid_page[iid] = page
        self.bm_list.delete(0, "end")
        for bm in sorted(book.bookmarks, key=lambda b: b.page):
            self.bm_list.insert("end", f"P.{bm.page}" + (f"  {bm.note}" if bm.note else ""))
        self.hl_list.delete(0, "end")
        for i, h in enumerate(book.highlights):
            self.hl_list.insert("end", f"P.{h.page}  #{i + 1}"
                                + (f"  {h.note}" if h.note else ""))

    # ---------------- 事件 ----------------
    def _on_toc_select(self, _e=None):
        sel = self.toc_tree.selection()
        if sel and sel[0] in self._iid_page:
            page = self._iid_page[sel[0]]
            if page >= 1:
                self.reader.goto(page - 1)

    def _on_bm_jump(self, _e=None):
        sel = self.bm_list.curselection()
        if sel and self.book:
            bms = sorted(self.book.bookmarks, key=lambda b: b.page)
            if sel[0] < len(bms):
                self.reader.goto(bms[sel[0]].page - 1)

    def _on_hl_jump(self, _e=None):
        sel = self.hl_list.curselection()
        if sel and self.book and sel[0] < len(self.book.highlights):
            self.reader.goto(self.book.highlights[sel[0]].page - 1)

    def _del_bookmark(self):
        sel = self.bm_list.curselection()
        if sel and self.book and self.reader:
            self.reader.remove_bookmark(sel[0])

    def _del_highlight(self):
        sel = self.hl_list.curselection()
        if sel and self.book and self.reader:
            self.reader.remove_highlight(sel[0])
