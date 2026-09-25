"""阅读视图：高清渲染（超采样）、翻页、缩放、书签、拖框高亮、烛光模式。"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog

from PIL import Image, ImageTk

from core.library import Book
from core.pdf_engine import PDFEngine
from ui import theme as T
from ui.sidebar import Sidebar

ZOOM_MIN, ZOOM_MAX = 0.5, 3.0
MAX_PIXELS = 26_000_000   # 超采样上限，避免超大页吃满内存


class ReaderView(tk.Frame):
    def __init__(self, master, app, **kw):
        super().__init__(master, bg=T.BG_DEEP, **kw)
        self.app = app
        self.s = getattr(app, "scale", 1.0)
        self.engine: PDFEngine | None = None
        self.book: Book | None = None
        self.page = 0
        self.zoom = 1.0
        self.candle = False
        self._cache = {}
        self._page_bbox = None
        self._drag_start = None
        self._drag_rect_id = None
        self._render_job = None

        self._build_toolbar()
        body = tk.Frame(self, bg=T.BG_DEEP)
        body.pack(fill="both", expand=True)
        self.sidebar = Sidebar(body, self, scale=self.s)
        self.sidebar.pack(side="left", fill="y")
        self.canvas = tk.Canvas(body, bg=T.BG_DEEP, highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)

        self.canvas.bind("<Configure>", lambda e: self._render(debounce=True))
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        for seq in ("<Left>", "<Prior>", "<Key-w>"):
            self.bind(seq, lambda e: self.prev_page())
        for seq in ("<Right>", "<Next>", "<Key-s>"):
            self.bind(seq, lambda e: self.next_page())
        self.bind("<Home>", lambda e: self.goto(0))
        self.bind("<End>", lambda e: self.goto(self.page_count - 1))
        self.bind("<Escape>", lambda e: self._go_back())

    # ---------------- 工具栏 ----------------
    def _btn(self, parent, text, cmd, primary=True):
        base = T.BTN if primary else T.BAR
        b = tk.Button(parent, text=text, command=cmd, bg=base, fg=T.BRASS,
                      activebackground=T.BTN_HOVER, activeforeground=T.BRASS,
                      relief="flat", padx=int(10 * self.s),
                      pady=int(2 * self.s), font=T.tk_font(10, scale=self.s))
        b.bind("<Enter>", lambda e: b.configure(bg=T.BTN_HOVER))
        b.bind("<Leave>", lambda e: b.configure(bg=base))
        return b

    def _build_toolbar(self):
        bar = tk.Frame(self, bg=T.BAR)
        bar.pack(fill="x")
        self._btn(bar, "◀ 书架", self._go_back, primary=False).pack(
            side="left", padx=(10, 4), pady=6)
        self.title_lbl = tk.Label(bar, text="", bg=T.BAR, fg=T.BRASS,
                                  font=T.tk_font(12, serif=True, scale=self.s),
                                  anchor="w")
        self.title_lbl.pack(side="left", fill="x", expand=True, padx=8)
        self._btn(bar, "◀", self.prev_page).pack(side="right", padx=2)
        self.page_lbl = tk.Label(bar, text="0 / 0", bg=T.BAR, fg=T.MUTED,
                                 font=T.tk_font(10, scale=self.s),
                                 width=int(9 * self.s / max(self.s, 0.5)))
        self.page_lbl.pack(side="right", padx=2)
        self._btn(bar, "▶", self.next_page).pack(side="right", padx=2)
        self._btn(bar, "＋", lambda: self._zoom_by(1.2)).pack(side="right", padx=2)
        self._btn(bar, "－", lambda: self._zoom_by(1 / 1.2)).pack(side="right", padx=2)
        self._btn(bar, "适配页宽", self._fit_and_render).pack(side="right", padx=2)
        self._btn(bar, "跳页", self._goto_dialog).pack(side="right", padx=2)
        self._btn(bar, "加书签", self.add_bookmark).pack(side="right", padx=2)
        self.candle_btn = self._btn(bar, "烛光", self.toggle_candle)
        self.candle_btn.pack(side="right", padx=2)

    # ---------------- 生命周期 ----------------
    @property
    def page_count(self) -> int:
        return self.engine.page_count if self.engine else 0

    def open_book(self, book: Book) -> bool:
        try:
            eng = PDFEngine(book.path)
        except Exception as e:
            messagebox.showerror("无法打开", f"{book.title}\n\n{e}", parent=self)
            return False
        if eng.page_count <= 0:
            eng.close()
            messagebox.showerror("无法打开",
                                 f"{book.title}\n\n文档没有可显示的页面。", parent=self)
            return False
        if self.engine:
            self.engine.close()
        self.engine = eng
        self.book = book
        self._cache.clear()
        self.page = min(max(book.progress - 1, 0), max(eng.page_count - 1, 0))
        self.candle = False
        self.candle_btn.configure(bg=T.BTN, fg=T.BRASS)
        self.title_lbl.configure(text=book.title)
        self.sidebar.refresh(book, eng.get_toc(), self.page)
        self.after(120, self._fit_and_render)
        self.focus_set()
        return True

    def close(self):
        if self.engine:
            self.engine.close()
            self.engine = None
        self._cache.clear()

    def _go_back(self):
        self.app.close_reader()

    # ---------------- 渲染 ----------------
    def _fit_zoom(self):
        if not self.engine:
            return
        page = self.engine.doc[self.page]
        avail_w = max(self.canvas.winfo_width() - int(56 * self.s), 200)
        avail_h = max(self.canvas.winfo_height() - int(56 * self.s), 200)
        self.zoom = max(min(avail_w / max(page.rect.width, 1),
                            avail_h / max(page.rect.height, 1), ZOOM_MAX), ZOOM_MIN)

    def _fit_and_render(self):
        if not self.engine:
            return
        self._fit_zoom()
        self._cache.clear()
        self._render()

    def _zoom_by(self, factor: float):
        self.zoom = max(ZOOM_MIN, min(ZOOM_MAX, self.zoom * factor))
        self._cache.clear()
        self._render()

    def _render(self, debounce: bool = False):
        if debounce:
            if self._render_job:
                self.after_cancel(self._render_job)
            self._render_job = self.after(90, self._render_now)
        else:
            self._render_now()

    def _render_now(self):
        self._render_job = None
        if not self.engine:
            return
        key = (self.page, round(self.zoom, 3), self.candle)
        photo = self._cache.get(key)
        if photo is None:
            page = self.engine.doc[self.page]
            # 超采样：以 2 倍分辨率渲染再高质量缩小，文字/线条更锐利
            ss = 2.0
            if (page.rect.width * self.zoom * ss
                    * page.rect.height * self.zoom * ss) > MAX_PIXELS:
                ss = 1.0
            rects = [h.rect for h in self.book.highlights
                     if h.page - 1 == self.page] if self.book else []
            img = self.engine.render(self.page, self.zoom * ss, rects, self.candle)
            if ss != 1.0:
                img = img.resize((max(int(img.width / ss), 1),
                                  max(int(img.height / ss), 1)), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            if len(self._cache) > 10:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = photo

        self.canvas.delete("all")
        cw = max(self.canvas.winfo_width(), 100)
        ch = max(self.canvas.winfo_height(), 100)
        x0 = (cw - photo.width()) // 2
        y0 = max((ch - photo.height()) // 2, int(10 * self.s))
        # 柔和投影（叠加两层点阵阴影，替代生硬黑边）
        for off, stipple, col in ((int(7 * self.s), "gray25", "#000000"),
                                  (int(4 * self.s), "gray50", "#000000"),
                                  (2, "gray75", "#1A1512")):
            self.canvas.create_rectangle(x0 + off, y0 + off,
                                         x0 + photo.width() + off,
                                         y0 + photo.height() + off,
                                         fill=col, outline="", stipple=stipple)
        self.canvas.create_image(x0, y0, image=photo, anchor="nw")
        if self.book and any(bm.page == self.page + 1 for bm in self.book.bookmarks):
            r = int(24 * self.s)
            self.canvas.create_polygon(
                x0 + photo.width() - int(1.6 * r), y0,
                x0 + photo.width() - int(0.5 * r), y0,
                x0 + photo.width() - int(0.5 * r), y0 + int(1.5 * r),
                x0 + photo.width() - int(1.05 * r), y0 + int(1.1 * r),
                x0 + photo.width() - int(1.6 * r), y0 + int(1.5 * r),
                fill=T.RED, outline="")
        self._photo = photo
        self._page_bbox = (x0, y0, photo.width(), photo.height())
        self.page_lbl.configure(text=f"{self.page + 1} / {self.page_count}")
        self.app.on_reader_page_change(self.book, self.page + 1)

    # ---------------- 翻页 ----------------
    def prev_page(self):
        if self.page > 0:
            self.goto(self.page - 1)

    def next_page(self):
        if self.page < self.page_count - 1:
            self.goto(self.page + 1)

    def goto(self, page: int):
        if not self.engine:
            return
        page = max(0, min(page, self.page_count - 1))
        if page == self.page:
            return
        self.page = page
        self._render()
        self.focus_set()

    def _goto_dialog(self):
        if not self.engine:
            return
        ans = simpledialog.askinteger(
            "跳页", f"页码 (1-{self.page_count})：", parent=self,
            minvalue=1, maxvalue=max(self.page_count, 1))
        if ans:
            self.goto(ans - 1)

    def _on_wheel(self, e):
        if e.delta < 0:
            self.next_page()
        else:
            self.prev_page()

    # ---------------- 书签 / 高亮 ----------------
    def add_bookmark(self):
        if not self.book:
            return
        note = simpledialog.askstring(
            "书签", f"在第 {self.page + 1} 页加书签，备注（可空）：", parent=self)
        if note is None:
            return
        self.app.library.add_bookmark(self.book, self.page + 1, note.strip())
        self.app.save_library()
        self._refresh_sidebar()
        self._render_now()

    def remove_bookmark(self, index: int):
        if not self.book:
            return
        bms = sorted(self.book.bookmarks, key=lambda b: b.page)
        if index < len(bms):
            self.book.bookmarks.remove(bms[index])
        self.app.save_library()
        self._refresh_sidebar()
        self._render_now()

    def remove_highlight(self, index: int):
        if not self.book:
            return
        self.app.library.remove_highlight(self.book, index)
        self._cache.clear()
        self.app.save_library()
        self._refresh_sidebar()
        self._render_now()

    def toggle_candle(self):
        self.candle = not self.candle
        self.candle_btn.configure(
            bg=T.GOLD if self.candle else T.BTN,
            fg="#412402" if self.candle else T.BRASS)
        self._render()

    def _refresh_sidebar(self):
        if self.engine and self.book:
            self.sidebar.refresh(self.book, self.engine.get_toc(), self.page)

    # ---------------- 拖框高亮 ----------------
    def _on_press(self, e):
        self._drag_start = (e.x, e.y)

    def _on_drag(self, e):
        if self._drag_start is None:
            return
        if self._drag_rect_id:
            self.canvas.delete(self._drag_rect_id)
        x0, y0 = self._drag_start
        self._drag_rect_id = self.canvas.create_rectangle(
            x0, y0, e.x, e.y, outline=T.GOLD, dash=(4, 3), width=2)

    def _on_release(self, e):
        if self._drag_start is None:
            return
        if self._drag_rect_id:
            self.canvas.delete(self._drag_rect_id)
            self._drag_rect_id = None
        x0, y0 = self._drag_start
        self._drag_start = None
        if not (self.book and self.engine and self._page_bbox):
            return
        if abs(e.x - x0) < 10 or abs(e.y - y0) < 10:
            return
        px, py, pw, ph = self._page_bbox
        sx0, sy0 = max(min(x0, e.x) - px, 0), max(min(y0, e.y) - py, 0)
        sx1, sy1 = min(max(x0, e.x) - px, pw), min(max(y0, e.y) - py, ph)
        z = self.zoom
        rect = (sx0 / z, sy0 / z, sx1 / z, sy1 / z)
        note = simpledialog.askstring(
            "高亮", f"在第 {self.page + 1} 页添加高亮，备注（可空）：", parent=self)
        if note is None:
            return
        self.app.library.add_highlight(self.book, self.page + 1, rect, note.strip())
        self._cache.clear()
        self.app.save_library()
        self._refresh_sidebar()
        self._render_now()
