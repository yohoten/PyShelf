"""书源视图：选择书源 → 搜索 → 结果列表（仓库可进入浏览）→ 加入书库下载。"""
from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

from core import sources as S
from ui import theme as T

CUSTOM_SPEC_TEMPLATE = {
    "name": "我的自定义书源",
    "hint": "http(s) 接口，返回 JSON",
    "base_url": "https://example.com",
    "search_url": "https://example.com/api/search?q={q}",
    "results_path": "data.list",
    "title": "name",
    "author": "author",
    "format": "format",
    "download_url": "download",
    "size": "size",
    "note": "",
}


class SourceView(tk.Frame):
    def __init__(self, master, app, **kw):
        super().__init__(master, bg=T.BG, **kw)
        self.app = app
        self.s = app.scale
        self.settings = app.settings
        self.sources = S.load_sources(self.settings)
        self.current = self.sources[0] if self.sources else None
        self.results: list[S.RemoteBook] = []
        self.busy = False

        self._build()
        self._refresh_sources()

    # ---------------- UI ----------------
    def _build(self):
        head = tk.Frame(self, bg=T.BAR)
        head.pack(fill="x")
        self._btn(head, "◂ 返回书架", self.app.close_sources).pack(
            side="left", padx=(10, 4), pady=8)
        tk.Label(head, text="书源检索", bg=T.BAR, fg=T.BRASS,
                 font=T.tk_font(13, serif=True, scale=self.s)).pack(
            side="left", padx=(6, 10), pady=8)
        self.query = tk.Entry(head, bg=T.BG, fg=T.BRASS, relief="flat",
                              insertbackground=T.BRASS,
                              font=T.tk_font(10, scale=self.s), width=34)
        self.query.pack(side="left", ipady=int(4 * self.s), padx=4, pady=8)
        self.query.bind("<Return>", lambda e: self.search())
        self._btn(head, "搜索", self.search, primary=True).pack(side="left", padx=6)
        self._btn(head, "加入书库", self.download_selected,
                  primary=True).pack(side="left", padx=2)
        self._btn(head, "↻ 刷新列表", self.go_up).pack(side="left", padx=2)
        self._btn(head, "关闭", self.app.close_sources).pack(side="right", padx=10)
        self._btn(head, "GitHub Token", self.set_token).pack(side="right", padx=2)

        body = tk.Frame(self, bg=T.BG)
        body.pack(fill="both", expand=True)

        # 左侧书源列表
        left = tk.Frame(body, bg=T.PANEL, width=int(210 * self.s))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        tk.Label(left, text="书源", bg=T.PANEL, fg=T.BRASS_DIM,
                 font=T.tk_font(10, scale=self.s)).pack(anchor="w", padx=10,
                                                        pady=(10, 4))
        self.hint_lbl = tk.Label(left, text="", bg=T.PANEL, fg=T.MUTED,
                                 font=T.tk_font(9, scale=self.s),
                                 wraplength=int(190 * self.s), justify="left")
        self.hint_lbl.pack(fill="x", padx=10, pady=(0, 8))
        self.src_list = tk.Listbox(left, bg=T.PANEL, fg=T.BRASS_DIM,
                                   selectbackground=T.BTN,
                                   selectforeground=T.BRASS, relief="flat",
                                   highlightthickness=0, activestyle="none",
                                   font=T.tk_font(10, scale=self.s))
        self.src_list.pack(fill="both", expand=True, padx=6, pady=4)
        self.src_list.bind("<<ListboxSelect>>", self._on_source_pick)
        bar = tk.Frame(left, bg=T.PANEL)
        bar.pack(fill="x", padx=6, pady=(0, 8))
        self._btn(bar, "新增自定义", self.add_custom_source).pack(side="left")
        self._btn(bar, "删除", self.del_custom_source).pack(side="left", padx=4)

        # 右侧结果
        right = tk.Frame(body, bg=T.BG)
        right.pack(side="left", fill="both", expand=True)
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Src.Treeview", background=T.BG_DEEP, foreground=T.BRASS_DIM,
                        fieldbackground=T.BG_DEEP, rowheight=int(26 * self.s),
                        borderwidth=0, font=T.tk_font(10, scale=self.s))
        style.configure("Src.Treeview.Heading", background=T.BAR,
                        foreground=T.BRASS, font=T.tk_font(10, scale=self.s))
        style.map("Src.Treeview", background=[("selected", T.BTN)],
                  foreground=[("selected", T.BRASS)])
        cols = ("title", "author", "fmt", "size", "note")
        self.tree = ttk.Treeview(right, columns=cols, show="headings",
                                 style="Src.Treeview", selectmode="extended")
        for c, txt, w in (("title", "标题", 320), ("author", "作者 / 仓库", 170),
                          ("fmt", "格式", 70), ("size", "大小 / 日期", 100),
                          ("note", "说明", 240)):
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=int(w * self.s),
                             anchor="w" if c in ("title", "note", "author") else "center")
        vsb = ttk.Scrollbar(right, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Double-Button-1>", lambda e: self._activate())
        self.tree.bind("<Return>", lambda e: self._activate())

        self.status = tk.Label(self, text="选择左侧书源，输入关键词后点「搜索」",
                               bg=T.PANEL, fg=T.MUTED, anchor="w",
                               font=T.tk_font(9, scale=self.s))
        self.status.pack(fill="x")
        self.pbar = ttk.Progressbar(self, mode="determinate", maximum=100)

    def _btn(self, parent, text, cmd, primary=False):
        b = tk.Button(parent, text=text, command=cmd,
                      bg=T.BTN if primary else T.BAR, fg=T.BRASS,
                      activebackground=T.BTN_HOVER, activeforeground=T.BRASS,
                      relief="flat", padx=int(10 * self.s), pady=int(2 * self.s),
                      font=T.tk_font(10, scale=self.s))
        b.bind("<Enter>", lambda e: b.configure(bg=T.BTN_HOVER))
        b.bind("<Leave>", lambda e: b.configure(
            bg=T.BTN if primary else T.BAR))
        return b

    # ---------------- 书源列表 ----------------
    def _refresh_sources(self):
        self.src_list.delete(0, "end")
        for i, src in enumerate(self.sources):
            self.src_list.insert("end", ("  " if not isinstance(src, S.CustomSource)
                                         else "  ◆ ") + src.name)
        if self.sources:
            self.src_list.selection_clear(0, "end")
            self.src_list.selection_set(0)
            self._on_source_pick()
        if self.settings.get("github_token"):
            self.status.configure(text="已配置 GitHub Token，API 限额已提升")

    def _on_source_pick(self, _e=None):
        sel = self.src_list.curselection()
        if not sel:
            return
        self.current = self.sources[sel[0]]
        self.hint_lbl.configure(text=getattr(self.current, "hint", ""))

    def add_custom_source(self):
        dlg = _SpecDialog(self, self.s)
        self.wait_window(dlg)
        if not dlg.result:
            return
        specs = self.settings.setdefault("custom_sources", [])
        specs.append(dlg.result)
        self.app.save_settings()
        self.sources = S.load_sources(self.settings)
        self._refresh_sources()
        self.status.configure(text=f"已添加自定义书源：{dlg.result.get('name')}")

    def del_custom_source(self):
        if not isinstance(self.current, S.CustomSource):
            messagebox.showinfo("提示", "内置书源不可删除。", parent=self)
            return
        specs = self.settings.get("custom_sources", [])
        specs = [s for s in specs if s.get("name") != self.current.name]
        self.settings["custom_sources"] = specs
        self.app.save_settings()
        self.sources = S.load_sources(self.settings)
        self._refresh_sources()

    def set_token(self):
        tok = simpledialog.askstring("GitHub Token",
                                     "填入 GitHub Personal Access Token"
                                     "（可选，仅用于提升 API 限额，留空则清除）：",
                                     parent=self, show="*")
        if tok is None:
            return
        self.settings["github_token"] = tok.strip()
        self.app.save_settings()
        self.sources = S.load_sources(self.settings)
        self._refresh_sources()

    # ---------------- 搜索 ----------------
    def search(self):
        if not self.current:
            return
        if self.busy:
            # 忙时必须给出反馈，静默返回会让用户以为按钮失灵
            self.status.configure(text="上一个检索还在进行中，请稍候…")
            return
        q = self.query.get().strip()
        if not q:
            self.status.configure(text="请输入关键词")
            return
        self.busy = True
        self.status.configure(text=f"正在 {self.current.name} 检索「{q}」…")
        threading.Thread(target=self._search_worker, args=(self.current, q),
                         daemon=True).start()

    def _search_worker(self, src, q):
        try:
            res = src.search(q)
            self.app.q.put(("src_results", src.name, res))
        except Exception as e:
            self.app.q.put(("src_error", src.name, f"{type(e).__name__}: {e}"))

    def show_results(self, src_name: str, res: list[S.RemoteBook]):
        self.busy = False
        self.tree.delete(*self.tree.get_children())
        self.results = res
        for i, b in enumerate(res):
            self.tree.insert("", "end", iid=str(i),
                             values=(b.title, b.author, b.fmt, b.size, b.note))
        self.status.configure(
            text=f"{src_name}：命中 {len(res)} 条 · "
                 f"双击仓库行可进入浏览文件，选中后点「加入书库」下载")

    def show_error(self, src_name: str, msg: str):
        self.busy = False
        self.status.configure(text=f"{src_name} 出错：{msg}")
        messagebox.showerror("书源错误", f"{src_name}\n\n{msg}", parent=self)

    # ---------------- 交互 ----------------
    def _selected(self) -> list[S.RemoteBook]:
        return [self.results[int(i)] for i in self.tree.selection()
                if i.isdigit() and int(i) < len(self.results)]

    def _activate(self):
        """双击：仓库 → 列出文件；文件 → 下载。"""
        sel = self._selected()
        if not sel:
            return
        if sel[0].kind == "repo":
            self.browse_repo(sel[0])
        else:
            self.download_selected()

    def browse_repo(self, book: S.RemoteBook):
        if not isinstance(self.current, S.GitHubSource):
            return
        self.status.configure(text=f"正在读取仓库 {book.repo} 的文件列表…")
        self.busy = True
        self._stack = getattr(self, "_stack", [])
        self._stack.append(("repo", book.repo))

        def worker():
            try:
                files = self.current.list_files(book.repo, book.ref)
                self.app.q.put(("src_results", f"{book.repo} 内的书籍文件", files))
            except Exception as e:
                self.app.q.put(("src_error", book.repo, f"{type(e).__name__}: {e}"))

        threading.Thread(target=worker, daemon=True).start()

    def go_up(self):
        """刷新当前检索结果（在仓库文件列表内时退回仓库搜索结果）。"""
        stack = getattr(self, "_stack", None)
        if stack:
            stack.pop()
        self.search()

    def finish_progress(self):
        """下载结束：进度条归零并收起。"""
        self.pbar.configure(value=0)
        self.pbar.pack_forget()

    def download_selected(self):
        sel = [b for b in self._selected() if b.kind == "file" and b.url]
        if not sel:
            messagebox.showinfo("提示", "请选中至少一个可下载的文件条目\n"
                                "（仓库条目请先双击进入）", parent=self)
            return
        self.pbar.pack(fill="x")
        self.app.start_downloads(sel)
        self.status.configure(text=f"已加入下载队列：{len(sel)} 个文件")


class _SpecDialog(tk.Toplevel):
    """自定义书源规则编辑对话框。"""

    def __init__(self, master, scale=1.0):
        super().__init__(master, bg=T.PANEL)
        self.title("新增自定义书源")
        self.result = None
        self.s = scale
        self.configure(padx=16, pady=12)
        self.transient(master)
        self.grab_set()

        tk.Label(self, text="填写 JSON 规则（字段说明见 README「书源」章节）",
                 bg=T.PANEL, fg=T.BRASS_DIM,
                 font=T.tk_font(10, scale=scale)).pack(anchor="w")
        self.txt = tk.Text(self, width=68, height=15, bg=T.BG_DEEP, fg=T.BRASS,
                           insertbackground=T.BRASS, relief="flat",
                           font=("Consolas", max(int(10 * scale), 8)))
        self.txt.pack(pady=8)
        self.txt.insert("1.0", json.dumps(CUSTOM_SPEC_TEMPLATE,
                                          ensure_ascii=False, indent=2))
        bar = tk.Frame(self, bg=T.PANEL)
        bar.pack(fill="x")
        tk.Button(bar, text="保存", command=self._save, bg=T.BTN, fg=T.BRASS,
                  relief="flat", padx=14, pady=3,
                  font=T.tk_font(10, scale=scale)).pack(side="right")
        tk.Button(bar, text="取消", command=self.destroy, bg=T.BAR, fg=T.BRASS_DIM,
                  relief="flat", padx=14, pady=3,
                  font=T.tk_font(10, scale=scale)).pack(side="right", padx=8)

    def _save(self):
        raw = self.txt.get("1.0", "end").strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            messagebox.showerror("JSON 格式错误", str(e), parent=self)
            return
        if not data.get("name") or not data.get("search_url"):
            messagebox.showerror("缺少字段", "至少需要 name 与 search_url", parent=self)
            return
        if "{q}" not in data["search_url"]:
            messagebox.showerror("缺少占位符",
                                 "search_url 必须包含 {q} 作为关键词占位符", parent=self)
            return
        self.result = data
        self.destroy()
