"""书架视图：拟物木书架 + 圆角书脊封面 + 悬停放大（Canvas 自绘，纹理程序化生成）。"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path

from PIL import ImageTk

from core.library import Book
from ui import theme as T

BASE_COVER_W, BASE_COVER_H = 104, 150


class ShelfView(tk.Canvas):
    def __init__(self, master, data_dir: Path, on_open, on_context, scale: float = 1.0,
                 **kw):
        super().__init__(master, bg=T.BG, highlightthickness=0, **kw)
        self.data_dir = Path(data_dir)
        self.on_open = on_open
        self.on_context = on_context
        self.s = max(1.0, scale)

        self.cover_w = int(BASE_COVER_W * self.s)
        self.cover_h = int(BASE_COVER_H * self.s)
        self.cell_w = self.cover_w + int(14 * self.s)
        self.cell_h = self.cover_h + int(58 * self.s)

        self.books: list[Book] = []
        self._covers: dict[tuple[str, bool], ImageTk.PhotoImage] = {}
        self._bg: tuple[tuple[int, int], ImageTk.PhotoImage] | None = None
        self._planks: dict[int, ImageTk.PhotoImage] = {}
        self._cells: list[tuple[int, int, int, int, Book, int]] = []
        self._plank_rects: dict[int, tuple[int, int, int, int]] = {}  # key -> (x, y, w, h)
        self._plank_seq = 0
        self._hover_bid: str | None = None
        self._tip: tk.Toplevel | None = None
        self._render_job = None

        self.bind("<Configure>", self._on_configure)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Double-Button-1>", self._on_double)
        self.bind("<Button-3>", self._on_right)
        self.bind("<MouseWheel>", self._on_wheel)

    # ---------------- 对外 ----------------
    def set_books(self, books: list[Book]):
        self.books = books
        self.redraw()

    def invalidate_thumb(self, bid: str):
        for key in [k for k in self._covers if k[0] == bid]:
            self._covers.pop(key, None)

    def redraw(self):
        if self._render_job:
            self.after_cancel(self._render_job)
            self._render_job = None
        self._hide_tip()
        self.delete("all")
        self._cells = []
        self._plank_rects = {}
        self._plank_seq = 0
        width = max(self.winfo_width(), 400)
        self._draw_background(width, max(self.winfo_height(), 600))

        y = self._draw_header(width)
        if not self.books:
            self.create_text(width // 2, y + 60, text="书架空空如也",
                             fill=T.MUTED, font=T.tk_font(16, serif=True, scale=self.s))
            self._finish(y + 160)
            return
        for title, group in self._sections():
            if not group:
                continue
            y = self._draw_section(width, y, title, group)
        self._finish(y + 24)

    # ---------------- 背景与结构 ----------------
    def _draw_background(self, width, height):
        key = (width, height)
        if not self._bg or self._bg[0] != key:
            img = T.wood_background(width, height)
            self._bg = (key, ImageTk.PhotoImage(img))
        self.create_image(0, 0, image=self._bg[1], anchor="nw", tags="bg")
        self.tag_lower("bg")

    def _draw_header(self, width) -> int:
        """顶部楣板：黄铜名牌。"""
        h = int(40 * self.s)
        self.create_rectangle(0, 0, width, h, fill=T.BG_DEEP, outline="")
        self.create_rectangle(0, h, width, h + 3, fill=T.PLANK_EDGE, outline="")
        self.create_text(int(20 * self.s), h / 2, anchor="w",
                         text="PY 书斋", fill=T.BRASS,
                         font=T.tk_font(15, serif=True, scale=self.s))
        self.create_text(width - int(20 * self.s), h / 2, anchor="e",
                         text=f"藏书 {len(self.books)} 册", fill=T.MUTED,
                         font=T.tk_font(10, scale=self.s))
        return h + int(26 * self.s)

    def _draw_section(self, width, y, title, books) -> int:
        # 分区标题 + 右侧细装饰线
        import tkinter.font as tkfont_mod
        pad = int(26 * self.s)
        self.create_text(pad, y, anchor="w", text=title, fill=T.BRASS_DIM,
                         font=T.tk_font(13, serif=True, scale=self.s))
        f = tkfont_mod.Font(font=T.tk_font(13, serif=True, scale=self.s))
        x0 = pad + f.measure(title) + int(10 * self.s)
        self.create_line(x0, y, width - pad, y, fill="#4A3B28")
        y += int(22 * self.s)

        per_row = max((width - 2 * pad) // self.cell_w, 1)
        n_rows = (len(books) + per_row - 1) // per_row
        # 每行书本 + 各自的木质层板（真实书架结构）
        row_h = self.cover_h + int(46 * self.s)   # 封面 + 书名行
        for r in range(n_rows):
            chunk = books[r * per_row: (r + 1) * per_row]
            plank_key = None
            for i, book in enumerate(chunk):
                _, plank_key = self._draw_book(pad + i * self.cell_w, y, book)
            y += row_h
            y = self._draw_plank(width, y, plank_key)
            y += int(16 * self.s)
        return y + int(20 * self.s)

    def _draw_plank(self, width, y, key=None):
        """画木质层板。key 给定时复用编号（局部重绘用），否则新建。"""
        pad = int(16 * self.s)
        pw = width - 2 * pad
        ph = int(11 * self.s)
        if key is None:
            self._plank_seq += 1
            key = self._plank_seq
        self._plank_rects[key] = (pad, y - int(5 * self.s), pw, ph)
        self._create_plank(key)
        return y + ph + int(6 * self.s)

    def _create_plank(self, key):
        x, y, pw, ph = self._plank_rects[key]
        if pw not in self._planks:
            self._planks[pw] = ImageTk.PhotoImage(T.plank_texture(pw, ph))
        self.create_image(x, y, image=self._planks[pw], anchor="nw",
                          tags=(f"plank:{key}", "plank"))
        return key

    def _draw_book(self, x, y, book: Book, plank_key=None):
        """画一本书的可见元素（封面/角标/书名），全部打 b:{id} 标签；
        返回所在行的层板 key（局部重绘时需一并重画层板以保持遮挡关系）。"""
        tag = f"b:{book.id}"
        cover_w, cover_h = self.cover_w, self.cover_h
        cx = x + (self.cell_w - cover_w) // 2
        hovered = book.id == self._hover_bid
        if hovered:
            # 悬停：整体上提放大，营造"抽出一本书"的手感
            grow = int(4 * self.s)
            cover_w += grow * 2
            cover_h += grow * 2
            cx -= grow
            y -= grow
        photo = self._cover_image(book, cover_w, cover_h, hovered)
        self.create_image(cx, y, image=photo, anchor="nw", tags=(tag,))

        # 状态角标
        if book.progress > 0 and book.pages:
            pct = min(round(book.progress * 100 / book.pages), 99)
            bw, bh = int(48 * self.s), int(18 * self.s)
            self.create_rectangle(cx, y, cx + bw, y + bh, fill=T.GOLD,
                                  outline="#412402", tags=(tag,))
            self.create_text(cx + bw / 2, y + bh / 2, text=f"在读{pct}%",
                             fill="#412402", font=T.tk_font(8, scale=self.s),
                             tags=(tag,))
        elif not book.readable:
            bw, bh = int(62 * self.s), int(18 * self.s)
            self.create_rectangle(cx, y, cx + bw, y + bh, fill="#4B4B4B",
                                  outline="#222", tags=(tag,))
            self.create_text(cx + bw / 2, y + bh / 2, text="文件异常",
                             fill="#E0E0E0", font=T.tk_font(8, scale=self.s),
                             tags=(tag,))
        if book.favorite:
            r = int(7 * self.s)
            self.create_oval(cx + cover_w - 2 * r - 4, y + 4,
                             cx + cover_w - 4, y + 2 * r + 4, fill=T.GOLD,
                             outline="#412402", tags=(tag,))

        # 书名（两行内，超出省略）
        label = self._wrap_title(book.title, 2)
        self.create_text(cx + cover_w / 2, y + cover_h + int(16 * self.s),
                         text=label, fill=T.BRASS if hovered else T.BRASS_DIM,
                         font=T.tk_font(9, scale=self.s),
                         width=cover_w + int(10 * self.s), justify="center",
                         tags=(tag,))

        # 命中区域（含标题行）
        self.create_rectangle(x, y, x + self.cell_w, y + self.cell_h,
                              fill="", outline="", tags=("cell", tag))
        self._cells.append((x, y, self.cell_w, self.cell_h, book, plank_key))
        return tag, plank_key

    def _repaint_book(self, bid):
        """局部重绘一本书：删除其全部绘制项后原位重画，
        并重画其层板（层板须盖住封面下缘，保持"立于层板"的遮挡关系）。
        替代 hover 变化时的全量 redraw——书库规模大时避免整架重绘。"""
        if not bid:
            return
        for x, y, cw, ch, book, plank_key in self._cells:
            if book.id == bid:
                self.delete(f"b:{bid}")
                # 临时移除该 cell 再重画（_draw_book 会重新注册）
                self._cells.remove((x, y, cw, ch, book, plank_key))
                self._draw_book(x, y, book, plank_key)
                if plank_key is not None:
                    self.delete(f"plank:{plank_key}")
                    self._create_plank(plank_key)
                return

    def _cover_image(self, book: Book, w: int, h: int, hover: bool) -> ImageTk.PhotoImage:
        key = (f"{book.id}@{w}x{h}", hover)
        cached = self._covers.get(key)
        if cached is not None:
            return cached
        png = self.data_dir / "covers" / f"{book.id}.png"
        src = png if (book.cover_ok and png.exists()) else None
        img = T.render_cover(src, book.title, w, h, hover=hover,
                             radius=max(int(6 * self.s), 3))
        photo = ImageTk.PhotoImage(img)
        if len(self._covers) > 220:
            self._covers.pop(next(iter(self._covers)))
        self._covers[key] = photo
        return photo

    def _finish(self, total_h):
        self.configure(scrollregion=(0, 0, self.winfo_width(), total_h))

    # ---------------- 分组 ----------------
    def _sections(self):
        opened = [b for b in self.books if b.last_opened > 0]
        recent = sorted(opened, key=lambda b: b.last_opened, reverse=True)[:10]
        favs = [b for b in self.books if b.favorite]
        return [("最近阅读", recent), ("收藏 ★", favs), ("全部书籍", self.books)]

    # ---------------- 交互 ----------------
    def _book_at(self, x, y) -> Book | None:
        for cx, cy, cw, ch, book, _plank in self._cells:
            if cx <= x <= cx + cw and cy <= y <= cy + ch:
                return book
        return None

    def _on_configure(self, _e):
        if self._render_job:
            self.after_cancel(self._render_job)
        self._render_job = self.after(140, self.redraw)

    def _on_motion(self, e):
        book = self._book_at(self.canvasx(e.x), self.canvasy(e.y))
        new_id = book.id if book else None
        if new_id != self._hover_bid:
            old_id = self._hover_bid
            self._hover_bid = new_id
            # 局部重绘：仅旧、新两本书及其层板，不再全量 redraw
            self._repaint_book(old_id)
            self._repaint_book(new_id)
        if book:
            self._show_tip(book, e.x_root, e.y_root)
        else:
            self._hide_tip()
        self.configure(cursor="hand2" if book else "")

    def _on_leave(self, _e):
        # 悬浮提示（Toplevel）弹出/销毁会让 Tk 发出伪 Leave 事件
        # （真实指针仍在画布内）——校验指针位置，避免 hover 被误清除
        try:
            px = self.winfo_pointerx() - self.winfo_rootx()
            py = self.winfo_pointery() - self.winfo_rooty()
            if 0 <= px < self.winfo_width() and 0 <= py < self.winfo_height():
                return   # 指针仍在画布内：伪 Leave，忽略
        except tk.TclError:
            pass
        if self._hover_bid:
            old_id = self._hover_bid
            self._hover_bid = None
            self._repaint_book(old_id)
        self._hide_tip()

    def _show_tip(self, book: Book, rx, ry):
        self._hide_tip()
        self._tip = tk.Toplevel(self)
        self._tip.wm_overrideredirect(True)
        frm = tk.Frame(self._tip, bg=T.BAR, padx=10, pady=6,
                       highlightthickness=1, highlightbackground="#6B4E2E")
        frm.pack()
        tk.Label(frm, text=book.title, bg=T.BAR, fg=T.BRASS,
                 font=T.tk_font(10, bold=True, scale=self.s),
                 wraplength=int(320 * self.s), justify="left").pack(anchor="w")
        bits = [f"{book.pages} 页" if book.pages else "页数未知"]
        if book.favorite:
            bits.append("★ 已收藏")
        if book.last_opened:
            bits.append(f"读到第 {book.progress} 页")
        if book.bookmarks:
            bits.append(f"{len(book.bookmarks)} 个书签")
        if book.highlights:
            bits.append(f"{len(book.highlights)} 处高亮")
        tk.Label(frm, text=" · ".join(bits), bg=T.BAR, fg=T.MUTED,
                 font=T.tk_font(9, scale=self.s)).pack(anchor="w")
        self._tip.wm_geometry(f"+{rx + 16}+{ry + 16}")

    def _hide_tip(self):
        if self._tip:
            self._tip.destroy()
            self._tip = None

    def _on_double(self, e):
        book = self._book_at(self.canvasx(e.x), self.canvasy(e.y))
        if book:
            self._hide_tip()
            self.on_open(book)

    def _on_right(self, e):
        book = self._book_at(self.canvasx(e.x), self.canvasy(e.y))
        if book:
            self.on_context(book, e)

    def _on_wheel(self, e):
        self.yview_scroll(-1 * int(e.delta / 120), "units")

    # ---------------- 工具 ----------------
    def _wrap_title(self, s: str, lines: int) -> str:
        """按每行可容纳的字符数截断为 lines 行，超出用省略号。"""
        per = max(int(self.cover_w / (7.2 * self.s)), 6)
        total = per * lines
        s = s.replace("\n", " ")
        return s if len(s) <= total else s[: total - 1] + "…"
