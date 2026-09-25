"""Py 书斋 —— 拟物复古书架阅读器 + 多书源检索（tkinter + PyMuPDF）。"""
from __future__ import annotations

import ctypes
import json
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog

from core import scanner
from core.downloader import Downloader
from core.library import Book, Library
from ui import theme as T
from ui.reader_view import ReaderView
from ui.shelf_view import ShelfView
from ui.source_view import SourceView

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
ICON_PATH = APP_DIR / "app.ico"
CRASH_LOG = DATA_DIR / "crash.log"
FILTERS = ("全部", "收藏", "历史")


def install_crash_log():
    """pythonw 无控制台，未捕获异常默认无声消失；统一落到 data/crash.log。"""
    def _hook(tp, val, tb):
        try:
            import traceback
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(CRASH_LOG, "a", encoding="utf-8") as f:
                f.write(f"\n==== {datetime.now().isoformat(timespec='seconds')} ====\n")
                traceback.print_exception(tp, val, tb, file=f)
        except Exception:
            pass
        sys.__excepthook__(tp, val, tb)
    sys.excepthook = _hook


def set_app_user_model_id():
    """必须在创建任何窗口之前调用：任务栏默认按 exe 分组，pythonw 会显示
    Python 图标；声明独立 AppUserModelID 后任务栏才认 app.ico。"""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "PyShelf.Reader.1")
    except Exception:
        pass


def apply_window_icon(window) -> bool:
    """给窗口与后续所有对话框设置 app.ico（含标题栏/任务栏/Alt-Tab）。"""
    if not ICON_PATH.exists():
        return False
    try:
        window.iconbitmap(default=str(ICON_PATH))
        return True
    except tk.TclError:
        return False


def enable_dpi_awareness():
    """Windows 高 DPI：避免系统位图拉伸导致的模糊封面与文字。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class PyShelfApp(tk.Tk):
    def report_callback_exception(self, exc, val, tb):
        """Tk 回调异常：pythonw 下默认写 stderr（不存在的句柄）而彻底静默，
        这是「按钮点了没反应」类问题的温床 —— 必须落盘到 crash.log。"""
        try:
            import traceback
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(CRASH_LOG, "a", encoding="utf-8") as f:
                f.write(f"\n==== {datetime.now().isoformat(timespec='seconds')} "
                        f"[Tk 回调] ====\n")
                traceback.print_exception(exc, val, tb, file=f)
        except Exception:
            pass

    def __init__(self):
        enable_dpi_awareness()
        super().__init__()
        self.title("Py 书斋 · 拟物书架")
        self.geometry("1240x820")
        self.minsize(900, 600)
        self.icon_ok = apply_window_icon(self)
        self.configure(bg=T.BG)

        self.scale = T.dpi_scale(self)
        self.library = Library(DATA_DIR)
        self.settings = self._load_settings()
        self.q: queue.Queue = queue.Queue()
        self.downloader = Downloader(self.q)
        self.filter_mode = "全部"
        self.search_text = ""
        self._covers_dirty = False

        self._build_topbar()
        self._build_body()
        self._poll()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # Esc 在书源界面 = 返回书架（阅读界面由 reader 自行处理 Esc）
        self.bind("<Escape>", self._on_escape)

        if self.library.library_dir:
            self.start_scan(self.library.library_dir)
        else:
            self.after(200, self._first_run)

    # ---------------- 设置 ----------------
    def _load_settings(self) -> dict:
        p = DATA_DIR / "settings.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"github_token": "", "custom_sources": []}

    def save_settings(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "settings.json").write_text(
            json.dumps(self.settings, ensure_ascii=False, indent=1),
            encoding="utf-8")

    # ---------------- UI ----------------
    def _mk_btn(self, parent, text, cmd, primary=False, dim=False):
        base = T.BTN if primary else T.BAR
        b = tk.Button(parent, text=text, command=cmd, bg=base,
                      fg=T.MUTED if dim else T.BRASS,
                      activebackground=T.BTN_HOVER, activeforeground=T.BRASS,
                      relief="flat", padx=int(11 * self.scale),
                      pady=int(2 * self.scale),
                      font=T.tk_font(10, scale=self.scale))
        glow = T.BTN_HOVER if not primary else "#8A6842"
        b.bind("<Enter>", lambda e: b.configure(bg=glow))
        b.bind("<Leave>", lambda e: b.configure(bg=base))
        return b

    def _build_topbar(self):
        bar = tk.Frame(self, bg=T.BAR)
        bar.pack(fill="x")
        tk.Label(bar, text="Py 书斋", bg=T.BAR, fg=T.BRASS,
                 font=T.tk_font(16, serif=True, scale=self.scale)).pack(
            side="left", padx=(16, 10), pady=8)

        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(
            bar, textvariable=self.search_var, bg=T.BG, fg=T.BRASS,
            insertbackground=T.BRASS, relief="flat", width=24,
            font=T.tk_font(10, scale=self.scale))
        self.search_entry.pack(side="left", padx=4, pady=8,
                               ipady=int(4 * self.scale))
        self.search_entry.insert(0, "搜索书名…")
        self.search_entry.bind("<FocusIn>", self._search_focus_in)
        self.search_entry.bind("<KeyRelease>", self._on_search)

        self.filter_btns = {}
        for name in FILTERS:
            b = self._mk_btn(bar, name, lambda n=name: self._set_filter(n),
                             primary=(name == "全部"))
            b.pack(side="left", padx=3)
            self.filter_btns[name] = b

        self._mk_btn(bar, "书源检索", self.open_sources, primary=True).pack(
            side="left", padx=(12, 3))
        self.src_toggle_btn = self._mk_btn(bar, "◂ 返回书架",
                                           self.close_sources, primary=True)
        self.src_toggle_btn.pack_forget()   # 仅书源界面显示
        self._mk_btn(bar, "选择书库目录", self._pick_dir, dim=True).pack(
            side="right", padx=10)

        self.status_lbl = tk.Label(bar, text="", bg=T.BAR, fg=T.MUTED,
                                   font=T.tk_font(9, scale=self.scale))
        self.status_lbl.pack(side="right", padx=8)

    def _build_body(self):
        self.holder = tk.Frame(self, bg=T.BG)
        self.holder.pack(fill="both", expand=True)

        self.shelf_holder = tk.Frame(self.holder, bg=T.BG)
        self.shelf_holder.pack(fill="both", expand=True)
        vsb = tk.Scrollbar(self.shelf_holder, orient="vertical")
        vsb.pack(side="right", fill="y")
        self.shelf = ShelfView(self.shelf_holder, DATA_DIR,
                               on_open=self.open_reader,
                               on_context=self._book_menu,
                               scale=self.scale)
        self.shelf.pack(side="left", fill="both", expand=True)
        vsb.configure(command=self.shelf.yview)
        self.shelf.configure(yscrollcommand=vsb.set)

        self.reader = ReaderView(self.holder, app=self)
        self.source_view = SourceView(self.holder, app=self)

    # ---------------- 书库 ----------------
    def _first_run(self):
        messagebox.showinfo(
            "欢迎使用 Py 书斋",
            "首次使用：请选择您的书籍目录（递归扫描 PDF / EPUB / TXT / MD）。\n\n"
            "双击封面开始阅读 · 右键管理收藏 · 阅读中拖框即高亮 · "
            "「书源检索」可搜索 GitHub 开源书库与 arXiv 论文。",
            parent=self)
        self._pick_dir()

    def _pick_dir(self):
        d = filedialog.askdirectory(
            title="选择书库目录", parent=self,
            initialdir=self.library.library_dir or str(Path.home()))
        if d:
            self.start_scan(d)

    def start_scan(self, lib_dir: str):
        self.library.library_dir = lib_dir
        self.library.save()
        self.status_lbl.configure(text="扫描中…")
        threading.Thread(target=self._scan_worker, args=(lib_dir,),
                         daemon=True).start()

    def _scan_worker(self, lib_dir: str):
        """后台线程只做慢 IO（磁盘扫描 + PDF 探测 + 封面渲染）；
        书库字典的增删一律回到主线程执行，避免与 UI 读取发生竞态。"""
        files = scanner.scan_files(lib_dir)
        infos = []
        for p in files:
            pages, readable = 0, True
            if p.suffix.lower() == ".pdf":
                pages, readable = scanner.probe_pdf(p)
            infos.append((str(p), scanner.clean_title(p.name),
                          p.suffix.lower(), pages, readable))
        self.q.put(("scanned", infos))

        todo = [b for b in list(self.library.books.values())
                if not (b.cover_ok and scanner.cover_path(DATA_DIR, b.id).exists())]
        for b in todo:
            ok = scanner.generate_cover(Path(b.path),
                                        scanner.cover_path(DATA_DIR, b.id),
                                        b.title, b.ext)
            b.cover_ok = ok
            self.q.put(("cover", b.id))
        self.q.put(("done", len(todo)))

    # ---------------- 后台事件 ----------------
    def _handle_msg(self, msg):
        """处理单条后台消息。任何一条失败都不允许拖垮轮询循环。"""
        kind = msg[0]
        if kind == "scanned":
            infos = msg[1]
            existing = set()
            for path_s, title, ext, pages, readable in infos:
                self.library.upsert(Path(path_s), title, ext, pages, readable)
                existing.add(path_s)
            for bid in [b.id for b in self.library.books.values()
                        if b.path not in existing]:
                del self.library.books[bid]
            self.refresh_shelf()
            self.status_lbl.configure(text=f"书库 {len(infos)} 本 · 封面生成中…")
        elif kind == "cover":
            self.shelf.invalidate_thumb(msg[1])
            self._covers_dirty = True
        elif kind == "done":
            self.library.save()
            self.refresh_shelf()
            self.status_lbl.configure(
                text=f"书库 {len(self.library.books)} 本 · 就绪")
        elif kind == "src_results":
            self.source_view.show_results(msg[1], msg[2])
        elif kind == "src_error":
            self.source_view.show_error(msg[1], msg[2])
        elif kind == "dl_progress":
            self.source_view.status.configure(
                text=f"下载中 {msg[1]} · {msg[2]}% · {msg[3]}")
            self.source_view.pbar.configure(value=msg[2])
        elif kind == "dl_done":
            self.source_view.status.configure(
                text=f"下载完成：{Path(msg[1]).name} · 已加入书库")
            self.source_view.finish_progress()
            self.status_lbl.configure(text="新增书籍，重新扫描书库…")
            self.start_scan(self.library.library_dir)
        elif kind == "dl_fail":
            self.source_view.status.configure(text=f"下载失败：{msg[1]}")
            self.source_view.finish_progress()
            self.after(50, lambda: messagebox.showerror(
                "下载失败", f"{msg[1]}\n\n{msg[2]}", parent=self))

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                try:
                    self._handle_msg(msg)
                except Exception:
                    # 单条消息失败不终止循环（否则后台事件泵永久停摆）
                    self.report_callback_exception(*sys.exc_info())
        except queue.Empty:
            pass
        if self._covers_dirty:
            self._covers_dirty = False
            self.shelf.redraw()
        self.after(400, self._poll)

    # ---------------- 书架 ----------------
    def _visible_books(self) -> list[Book]:
        books = list(self.library.books.values())
        if self.filter_mode == "收藏":
            books = [b for b in books if b.favorite]
        elif self.filter_mode == "历史":
            books = [b for b in books if b.last_opened]
            books.sort(key=lambda b: b.last_opened, reverse=True)
        else:
            books.sort(key=lambda b: (not b.favorite, b.title.lower()))
        if self.search_text:
            books = [b for b in books
                     if self.search_text.lower() in b.title.lower()]
        return books

    def refresh_shelf(self):
        self.shelf.set_books(self._visible_books())

    def _set_filter(self, name):
        self.filter_mode = name
        for n, b in self.filter_btns.items():
            b.configure(bg=T.BTN if n == name else T.BAR)
        self.refresh_shelf()

    def _search_focus_in(self, _e):
        if self.search_var.get() == "搜索书名…":
            self.search_var.set("")

    def _on_search(self, _e):
        self.search_text = self.search_var.get().strip()
        if self.search_text == "搜索书名…":
            self.search_text = ""
        self.refresh_shelf()

    def _book_menu(self, book: Book, event):
        m = tk.Menu(self, tearoff=0, bg=T.BAR, fg=T.BRASS_DIM,
                    activebackground=T.BTN, activeforeground=T.BRASS)
        m.add_command(label="★ 取消收藏" if book.favorite else "☆ 加入收藏",
                      command=lambda: self._toggle_fav(book))
        m.add_separator()
        m.add_command(label="从上次位置继续", command=lambda: self.open_reader(book))
        m.add_command(label="打开所在文件夹", command=lambda: self._open_folder(book))
        m.add_command(label="重命名显示标题", command=lambda: self._rename(book))
        m.add_separator()
        m.add_command(label="从书架移除记录（不删文件）",
                      command=lambda: self._remove(book))
        m.tk_popup(event.x_root, event.y_root)

    def _toggle_fav(self, book: Book):
        self.library.toggle_favorite(book)
        self.library.save()
        self.refresh_shelf()

    def _open_folder(self, book: Book):
        try:
            import os
            os.startfile(str(Path(book.path).parent))  # noqa: S606
        except Exception:
            messagebox.showerror("失败", "无法打开文件夹。", parent=self)

    def _rename(self, book: Book):
        ans = simpledialog.askstring("重命名", "显示标题：",
                                     initialvalue=book.title, parent=self)
        if ans and ans.strip():
            book.title = ans.strip()
            self.library.save()
            self.shelf.invalidate_thumb(book.id)
            self.refresh_shelf()

    def _remove(self, book: Book):
        if messagebox.askyesno("移除", f"从书架移除「{book.title}」的记录？\n"
                                "（不会删除原文件，仅清除阅读数据）", parent=self):
            self.library.books.pop(book.id, None)
            self.library.save()
            self.refresh_shelf()

    # ---------------- 阅读器 ----------------
    def open_reader(self, book: Book):
        if not book.readable:
            messagebox.showwarning("无法读取", f"「{book.title}」无法被解析。",
                                   parent=self)
            return
        if not self.reader.open_book(book):
            return          # 打开失败：留在书架
        self.shelf_holder.pack_forget()
        self.source_view.pack_forget()
        self.src_toggle_btn.pack_forget()
        self.reader.pack(fill="both", expand=True)
        self.library.touch_history(book, book.progress or 1)
        self.library.save()

    def on_reader_page_change(self, book: Book, page: int):
        if book:
            self.library.touch_history(book, page)

    def close_reader(self):
        self.library.save()
        self.reader.close()
        self.reader.pack_forget()
        self.shelf_holder.pack(fill="both", expand=True)
        self.refresh_shelf()

    # ---------------- 书源 ----------------
    def open_sources(self):
        self.shelf_holder.pack_forget()
        self.reader.pack_forget()
        self.source_view.pack(fill="both", expand=True)
        # 顶栏出现「返回书架」按钮
        self.src_toggle_btn.pack(side="left", padx=3)
        self.source_view.query.focus_set()

    def close_sources(self):
        self.source_view.pack_forget()
        self.src_toggle_btn.pack_forget()
        self.shelf_holder.pack(fill="both", expand=True)
        self.refresh_shelf()

    def _on_escape(self, _e):
        if self.source_view.winfo_ismapped():
            self.close_sources()

    def start_downloads(self, books: list):
        target = Path(self.library.library_dir or DATA_DIR) / "_网络下载"
        for b in books:
            self.downloader.start(b.title, b.url, b.fmt, target,
                                  fallbacks=b.extras)

    def save_library(self):
        self.library.save()

    # ---------------- 退出 ----------------
    def _on_close(self):
        if self.reader.engine and self.reader.book:
            self.library.touch_history(self.reader.book, self.reader.page + 1)
        self.library.save()
        self.save_settings()
        self.reader.close()
        self.destroy()


def main():
    install_crash_log()
    set_app_user_model_id()
    app = PyShelfApp()
    if "--smoke" in sys.argv:
        app.after(9000, app._on_close)
    if "--shot" in sys.argv or "--shotsrc" in sys.argv:
        # 自截窗口图像后退出（视觉回归用）
        if "--shotsrc" in sys.argv:
            app.after(1200, app.open_sources)
        def _shot():
            app.update_idletasks()
            from PIL import ImageGrab
            x, y = app.winfo_rootx(), app.winfo_rooty()
            w, h = app.winfo_width(), app.winfo_height()
            ImageGrab.grab(bbox=(x, y - 32, x + w, y + h)).save(APP_DIR / "_shot.png")
            app._on_close()
        app.after(7000, _shot)
    app.mainloop()


if __name__ == "__main__":
    main()
