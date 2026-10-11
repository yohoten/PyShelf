"""书架视图：拟物木书架 + 圆角书脊封面 + 悬停放大（QWidget 自绘，纹理程序化生成）。

性能要点：
- 整架绘制一次到缓存 QPixmap(`_base`)，paintEvent 仅贴图；
- 悬停仅在该缓存之上叠加绘制「被悬停的那本书」；
- 封面合成交给后台线程（RenderService）：先用纯色占位，就绪后只**局部重绘**
  该书的所有副本（`_repaint_book_in_base`），不整架重建 —— 大书库也顺滑。
"""
from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt, QRect, QTimer
from PyQt5.QtGui import QColor, QFontMetrics, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QToolTip, QWidget

from core.library import Book
from ui import theme as T

BASE_COVER_W, BASE_COVER_H = 104, 150
COVER_SIZE_FACTOR = {"small": 0.82, "medium": 1.0, "large": 1.22}


class ShelfView(QWidget):
    def __init__(self, data_dir: Path, on_open, on_context, scale: float = 1.0,
                 cover_size: str = "medium", show_recent: bool = True,
                 show_favorites: bool = True, show_tooltips: bool = True,
                 render=None, parent=None):
        super().__init__(parent)
        self.data_dir = Path(data_dir)
        self.on_open = on_open
        self.on_context = on_context
        self.s = max(1.0, scale)
        self.cover_factor = COVER_SIZE_FACTOR.get(cover_size, 1.0)
        self.show_recent = show_recent
        self.show_favorites = show_favorites
        self.show_tooltips = show_tooltips
        self.render = render
        if self.render is not None:
            self.render.ready.connect(self._on_render_ready)

        self.books: list[Book] = []
        self._covers: dict[tuple, QPixmap] = {}
        self._cell_cache: dict[tuple, QPixmap] = {}
        self._planks: dict[int, QPixmap] = {}
        self._pending: set = set()
        self._base: QPixmap | None = None
        self._bg_pixmaps: dict[int, QPixmap] = {}
        self._cells: list[tuple[int, int, int, int, Book]] = []
        self._hover_bid: str | None = None
        self._last_width = 0

        self._render_job = QTimer(self)
        self._render_job.setSingleShot(True)
        self._render_job.setInterval(140)
        self._render_job.timeout.connect(self.redraw)

        self._apply_cover_metrics()
        self.setMouseTracking(True)
        self.setMinimumWidth(400)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    def _apply_cover_metrics(self):
        f = self.cover_factor
        self.cover_w = max(int(BASE_COVER_W * self.s * f), 40)
        self.cover_h = max(int(BASE_COVER_H * self.s * f), 56)
        self.cell_w = self.cover_w + int(14 * self.s)
        self.cell_h = self.cover_h + int(58 * self.s)

    # ---------------- 对外 ----------------
    def set_books(self, books: list[Book]):
        self.books = books
        self.redraw()

    def invalidate_thumb(self, bid: str):
        """封面文件已更新：清缓存并丢弃在途结果（避免旧占位覆盖新封面）。"""
        for cache in (self._covers, self._cell_cache):
            for key in [k for k in cache if k[0] == bid]:
                cache.pop(key, None)
        for key in [k for k in self._pending if k[1] == bid]:
            self._pending.discard(key)

    def set_cover_size(self, name: str):
        f = COVER_SIZE_FACTOR.get(name, 1.0)
        if abs(f - self.cover_factor) < 1e-6:
            return
        self.cover_factor = f
        self._apply_cover_metrics()
        self._covers.clear()
        self._cell_cache.clear()
        self._pending.clear()
        self._bg_pixmaps.clear()
        self.redraw()

    def set_sections(self, recent: bool | None = None,
                     favorites: bool | None = None):
        changed = False
        if recent is not None and recent != self.show_recent:
            self.show_recent = recent
            changed = True
        if favorites is not None and favorites != self.show_favorites:
            self.show_favorites = favorites
            changed = True
        if changed:
            self.redraw()

    def set_tooltips(self, on: bool):
        self.show_tooltips = bool(on)
        if not on:
            QToolTip.hideText()

    # ---------------- 布局计算 ----------------
    def _sections(self):
        out = []
        if self.show_recent:
            opened = [b for b in self.books if b.last_opened > 0]
            out.append(("最近阅读",
                        sorted(opened, key=lambda b: b.last_opened,
                               reverse=True)[:10]))
        if self.show_favorites:
            out.append(("收藏 ★", [b for b in self.books if b.favorite]))
        out.append(("全部书籍", self.books))
        return out

    def _compute(self, width):
        s = self.s
        pad = int(26 * s)
        per_row = max((width - 2 * pad) // self.cell_w, 1)
        header_h = int(40 * s) + int(26 * s)
        row_h = self.cover_h + int(46 * s)
        row_block = row_h + int(11 * s) + int(6 * s) + int(16 * s)
        sections = []
        total = header_h
        for title, group in self._sections():
            if not group:
                continue
            rows = [group[i:i + per_row] for i in range(0, len(group), per_row)]
            sections.append((title, rows))
            total += int(22 * s) + len(rows) * row_block + int(20 * s)
        total += int(24 * s)
        return pad, header_h, row_h, max(total, 1), sections

    # ---------------- 重绘（构建缓存底板） ----------------
    def redraw(self):
        # 无在途任务却仍标记待渲染 → 说明任务被丢弃，清理以便重试
        if (self.render is not None and self.render.inflight == 0
                and self._pending):
            self._pending.clear()
        width = max(self.width(), 400)
        pad, header_h, row_h, total, sections = self._compute(width)
        self._base = QPixmap(width, total)
        self._base.fill(QColor(T.BG))
        p = QPainter(self._base)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.drawPixmap(0, 0, self._bg_for(width, total))
        self._cells = []

        self._paint_header(p, width, int(40 * self.s))
        y = header_h
        if not self.books:
            p.setPen(QColor(T.MUTED))
            p.setFont(T.font(16, serif=True, scale=self.s))
            p.drawText(QRect(0, y, width, int(60 * self.s)),
                       Qt.AlignHCenter | Qt.AlignTop, "书架空空如也")
        else:
            for title, rows in sections:
                y = self._paint_section(p, width, y, title, rows, pad, row_h)
        p.end()

        self._last_width = width
        self.setMinimumHeight(total)
        self.update()

    def _bg_for(self, width: int, total: int) -> QPixmap:
        """木纹背景：按宽度缓存（生成含大半径模糊，代价高）。

        以半分辨率生成再平滑放大，兼顾观感与速度；仅在宽度变化或内容
        更高时才重建，避免每次重绘都重算（这是书架重绘的主要开销）。
        """
        cached = self._bg_pixmaps.get(width)
        if cached is not None and cached.height() >= total:
            return cached
        h = max(total, self.height(), 600)
        hw, hh = max(width // 2, 2), max(h // 2, 2)
        pm = T.pil_to_pixmap(T.wood_background(hw, hh)).scaled(
            width, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        self._bg_pixmaps[width] = pm
        if len(self._bg_pixmaps) > 3:
            self._bg_pixmaps.pop(next(iter(self._bg_pixmaps)))
        return pm

    def _paint_header(self, p: QPainter, width: int, h: int):
        p.fillRect(0, 0, width, h, QColor(T.BG_DEEP))
        # 黄铜色下沿 + 细高光，做出楣板厚度感
        p.fillRect(0, h - 1, width, 1, QColor(T.PLANK_TOP))
        p.fillRect(0, h, width, 3, QColor(T.PLANK_EDGE))
        p.setPen(QColor(T.BRASS))
        p.setFont(T.font(15, serif=True, scale=self.s))
        p.drawText(QRect(int(20 * self.s), 0, width - int(40 * self.s), h),
                   Qt.AlignVCenter | Qt.AlignLeft, "PY 书斋")
        p.setPen(QColor(T.MUTED))
        p.setFont(T.font(10, scale=self.s))
        p.drawText(QRect(0, 0, width - int(20 * self.s), h),
                   Qt.AlignVCenter | Qt.AlignRight, f"藏书 {len(self.books)} 册")

    def _paint_section(self, p, width, y, title, rows, pad, row_h):
        s = self.s
        p.setPen(QColor(T.BRASS_DIM))
        p.setFont(T.font(13, serif=True, scale=s))
        fm = QFontMetrics(p.font())
        p.drawText(pad, y + fm.ascent(), title)
        x0 = pad + fm.horizontalAdvance(title) + int(10 * s)
        p.setPen(QColor("#4A3B28"))
        p.drawLine(x0, y, width - pad, y)
        y += int(22 * s)

        for chunk in rows:
            for i, book in enumerate(chunk):
                self._draw_book(p, pad + i * self.cell_w, y, book, False,
                                self.cover_w, self.cover_h)
            y += row_h
            y = self._paint_plank(p, width, y)
            y += int(16 * s)
        return y + int(20 * s)

    def _paint_plank(self, p, width, y):
        s = self.s
        pad = int(16 * s)
        pw = max(width - 2 * pad, 8)
        ph = int(11 * s)
        if pw not in self._planks:
            self._planks[pw] = T.pil_to_pixmap(T.plank_texture(pw, ph))
        p.drawPixmap(pad, y - int(5 * s), self._planks[pw])
        return y + ph + int(6 * s)

    def _draw_book(self, p: QPainter, x, y, book: Book, hovered: bool,
                   cover_w, cover_h):
        s = self.s
        cx = x + (self.cell_w - cover_w) // 2
        base_y = y
        if hovered:
            grow = int(4 * s)
            cover_w += grow * 2
            cover_h += grow * 2
            cx -= grow
            y -= grow
        p.drawPixmap(cx, y, self._cell_pixmap(book, hovered, cover_w, cover_h))
        # 命中区域（含标题行）；悬停覆盖层不重复登记
        if not hovered:
            self._cells.append((x, base_y, self.cell_w, self.cell_h, book))

    def _cell_pixmap(self, book: Book, hover: bool, cover_w, cover_h) -> QPixmap:
        """把「封面 + 状态角标 + 书名」渲染成一张缓存位图。

        同一本书常在「最近阅读 / 收藏 / 全部书籍」多个分区重复出现，
        缓存后整架重绘仅需贴图，避免对同一本书重复做文字排版（数倍提速）。
        """
        s = self.s
        key = (book.id, hover, cover_w, cover_h, book.title, book.progress,
               book.pages, book.favorite, book.readable, book.cover_ok)
        cached = self._cell_cache.get(key)
        if cached is not None:
            return cached

        pad = 10                       # render_cover 预留的投影留白
        cw = cover_w + pad
        ch = cover_h + pad + int(30 * s)
        pm = QPixmap(cw, ch)
        pm.fill(Qt.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.Antialiasing, True)
        q.setRenderHint(QPainter.SmoothPixmapTransform, True)
        q.drawPixmap(0, 0, self._cover_pixmap(book, cover_w, cover_h, hover))

        # 状态角标（封面左上角）
        if book.progress > 0 and book.pages:
            pct = min(round(book.progress * 100 / book.pages), 99)
            bw, bh = int(48 * s), int(18 * s)
            q.fillRect(0, 0, bw, bh, QColor(T.GOLD))
            q.setPen(QColor("#412402"))
            q.setFont(T.font(8, scale=s))
            q.drawText(QRect(0, 0, bw, bh), Qt.AlignCenter, f"在读{pct}%")
        elif not book.readable:
            bw, bh = int(62 * s), int(18 * s)
            q.fillRect(0, 0, bw, bh, QColor("#4B4B4B"))
            q.setPen(QColor("#E0E0E0"))
            q.setFont(T.font(8, scale=s))
            q.drawText(QRect(0, 0, bw, bh), Qt.AlignCenter, "文件异常")

        if book.favorite:
            r = int(7 * s)
            q.setPen(QPen(QColor("#412402")))
            q.setBrush(QColor(T.GOLD))
            q.drawEllipse(cover_w - 2 * r - 4, 4, 2 * r, 2 * r)
            q.setBrush(Qt.NoBrush)

        # 书名（两行内，超出省略）
        label = self._wrap_title(book.title, 2)
        q.setPen(QColor(T.BRASS if hover else T.BRASS_DIM))
        q.setFont(T.font(9, scale=s))
        q.drawText(QRect(0, cover_h + int(6 * s), cw, int(30 * s)),
                   Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, label)
        q.end()

        if len(self._cell_cache) > 500:
            self._cell_cache.pop(next(iter(self._cell_cache)))
        self._cell_cache[key] = pm
        return pm

    # ---------------- 封面（后台合成） ----------------
    def _cover_pixmap(self, book: Book, w: int, h: int, hover: bool) -> QPixmap:
        key = (book.id, w, h, hover)
        cached = self._covers.get(key)
        if cached is not None:
            return cached
        if self.render is not None:
            if key not in self._pending:
                self._pending.add(key)
                png = self.data_dir / "covers" / f"{book.id}.png"
                src = png if (book.cover_ok and png.exists()) else None
                title = book.title
                radius = max(int(6 * self.s), 3)
                self.render.submit(
                    ("cover", key),
                    lambda src=src, title=title, w=w, h=h, hover=hover,
                    radius=radius: T.render_cover(src, title, w, h, hover=hover,
                                                  radius=radius))
            pm = self._placeholder_cover(book, w, h)
        else:                                    # 无渲染服务时同步兜底
            png = self.data_dir / "covers" / f"{book.id}.png"
            src = png if (book.cover_ok and png.exists()) else None
            pm = T.pil_to_pixmap(T.render_cover(
                src, book.title, w, h, hover=hover, radius=max(int(6 * self.s), 3)))
        self._covers[key] = pm
        return pm

    def _placeholder_cover(self, book: Book, w: int, h: int) -> QPixmap:
        """占位封面：纯色书脊 + 高光，开销极小（后台合成完成后被替换）。"""
        m = 10
        pm = QPixmap(w + m, h + m)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        col = QColor(T.SPINE_COLORS[abs(hash(book.title)) % len(T.SPINE_COLORS)])
        p.fillRect(0, 0, w, h, col)
        p.fillRect(0, 0, max(int(w * 0.09), 4), h, col.lighter(132))
        p.setPen(QPen(QColor("#3A2C1C")))
        p.drawRect(0, 0, w - 1, h - 1)
        p.end()
        return pm

    def _on_render_ready(self, key, qimg):
        """后台封面就绪：命中则替换缓存并只重绘该书的所有副本。"""
        if not isinstance(key, tuple) or not key or key[0] != "cover":
            return
        ckey = key[1]
        if ckey not in self._pending:
            return                       # 已被失效/过期：丢弃
        self._pending.discard(ckey)
        if qimg is None or self._base is None:
            return
        if ckey[1] != self.cover_w or ckey[2] != self.cover_h:
            return                       # 封面尺寸已变更：丢弃旧结果
        self._covers[ckey] = QPixmap.fromImage(qimg)
        bid = ckey[0]
        for k in [k for k in self._cell_cache if k[0] == bid]:
            self._cell_cache.pop(k, None)
        if ckey[3]:                      # 悬停变体：仅刷新，不写入底板
            self.update()
            return
        self._repaint_book_in_base(bid)

    def _repaint_book_in_base(self, bid: str):
        """局部重绘：把某本书的所有分区副本按新封面重画到底板并仅刷新该区域。"""
        dirty = QRect()
        p = QPainter(self._base)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        for x, y, _cw, _ch, book in self._cells:
            if book.id != bid:
                continue
            cell = self._cell_pixmap(book, False, self.cover_w, self.cover_h)
            p.drawPixmap(x + (self.cell_w - self.cover_w) // 2, y, cell)
            dirty = dirty.united(QRect(x, y, self.cell_w, self.cell_h))
        p.end()
        if not dirty.isNull():
            self.update(dirty)

    # ---------------- 绘制与交互 ----------------
    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(T.BG))
        if self._base is not None:
            p.drawPixmap(0, 0, self._base)
        if self._hover_bid:
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            for x, y, _w, _h, book in self._cells:
                if book.id == self._hover_bid:
                    self._draw_book(p, x, y, book, True,
                                    self.cover_w, self.cover_h)
        p.end()

    def _book_at(self, x, y) -> Book | None:
        for cx, cy, cw, ch, book in self._cells:
            if cx <= x <= cx + cw and cy <= y <= cy + ch:
                return book
        return None

    def mouseMoveEvent(self, e):
        book = self._book_at(e.x(), e.y())
        bid = book.id if book else None
        if bid != self._hover_bid:
            self._hover_bid = bid
            self.update()
        if book:
            self.setCursor(Qt.PointingHandCursor)
            if self.show_tooltips:
                QToolTip.showText(e.globalPos(), self._tip_html(book), self)
        else:
            self.setCursor(Qt.ArrowCursor)
            if self.show_tooltips:
                QToolTip.hideText()

    def leaveEvent(self, _e):
        if self._hover_bid is not None:
            self._hover_bid = None
            self.update()
        QToolTip.hideText()

    def mouseDoubleClickEvent(self, e):
        book = self._book_at(e.x(), e.y())
        if book:
            self.on_open(book)

    def contextMenuEvent(self, e):
        book = self._book_at(e.pos().x(), e.pos().y())
        if book:
            self.on_context(book, e.globalPos())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.width() != self._last_width:
            self._render_job.start()

    # ---------------- 工具 ----------------
    def _tip_html(self, book: Book) -> str:
        bits = [f"{book.pages} 页" if book.pages else "页数未知"]
        if book.favorite:
            bits.append("★ 已收藏")
        if book.last_opened:
            bits.append(f"读到第 {book.progress} 页")
        if book.bookmarks:
            bits.append(f"{len(book.bookmarks)} 个书签")
        if book.highlights:
            bits.append(f"{len(book.highlights)} 处高亮")
        title = (book.title or "").replace("&", "&").replace("<", "<")
        return (f"<b style='color:{T.BRASS}'>{title}</b><br>"
                f"<span style='color:{T.MUTED}'>"
                f"{' · '.join(bits)}</span>")

    def _wrap_title(self, s: str, lines: int) -> str:
        per = max(int(self.cover_w / (7.2 * max(self.s, 0.5))), 6)
        total = per * lines
        s = (s or "").replace("\n", " ")
        return s if len(s) <= total else s[: total - 1] + "…"
