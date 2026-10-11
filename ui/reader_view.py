"""阅读视图：高清渲染（超采样）、翻页、缩放、书签、拖框高亮、烛光模式。

渲染放到后台线程（RenderService），主线程只做贴图，翻页/缩放不再卡顿；
引擎访问由互斥锁串行化（PyMuPDF 文档非线程安全），并丢弃过期渲染结果。
"""
from __future__ import annotations

import threading

from PIL import Image
from PyQt5.QtCore import QPoint, Qt, QTimer
from PyQt5.QtGui import (QColor, QFontMetrics, QKeySequence, QPainter, QPen,
                         QPixmap, QPolygon)
from PyQt5.QtWidgets import (QHBoxLayout, QInputDialog, QLabel, QMessageBox,
                             QShortcut, QVBoxLayout, QWidget)

from core.engines import DocumentEngine, open_engine
from core.library import Book
from ui import theme as T
from ui.sidebar import Sidebar

ZOOM_MIN, ZOOM_MAX = 0.5, 3.0
MAX_PIXELS = 26_000_000   # 超采样上限，避免超大页吃满内存


class _PageCanvas(QWidget):
    """页面画布：只负责绘制当前页位图、书签丝带与拖框，事件转交 ReaderView。"""

    def __init__(self, reader, parent=None):
        super().__init__(parent)
        self.reader = reader
        self._pm = None
        self._x0 = self._y0 = 0
        self._drag_start: tuple[int, int] | None = None
        self._drag_cur: tuple[int, int] | None = None
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(False)

    def set_page(self, pm, x0: int, y0: int):
        self._pm = pm
        self._x0, self._y0 = x0, y0
        self.update()

    def page_bbox(self):
        if self._pm is None:
            return None
        return (self._x0, self._y0, self._pm.width(), self._pm.height())

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(T.BG_DEEP))
        if self._pm is None:
            p.setPen(QColor(T.MUTED))
            p.setFont(T.font(11, scale=self.reader.s))
            p.drawText(self.rect(), Qt.AlignCenter, "正在渲染页面…")
            p.end()
            return
        s = self.reader.s
        pm = self._pm
        x0, y0 = self._x0, self._y0
        # 柔和投影（多层半透明黑，替代生硬黑边）
        for off, alpha in ((int(7 * s), 70), (int(4 * s), 120), (2, 150)):
            p.fillRect(x0 + off, y0 + off, pm.width(), pm.height(),
                       QColor(0, 0, 0, alpha))
        p.drawPixmap(x0, y0, pm)
        # 书签红丝带
        if self.reader._page_has_bookmark():
            r = int(24 * s)
            w = pm.width()
            poly = QPolygon([
                QPoint(x0 + w - int(1.6 * r), y0),
                QPoint(x0 + w - int(0.5 * r), y0),
                QPoint(x0 + w - int(0.5 * r), y0 + int(1.5 * r)),
                QPoint(x0 + w - int(1.05 * r), y0 + int(1.1 * r)),
                QPoint(x0 + w - int(1.6 * r), y0 + int(1.5 * r)),
            ])
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T.RED))
            p.drawPolygon(poly)
        # 拖框
        if self._drag_start and self._drag_cur:
            x1, y1 = self._drag_start
            x2, y2 = self._drag_cur
            p.setPen(QPen(QColor(T.GOLD), 2, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))
        p.end()

    def _in_page(self, x, y) -> bool:
        bb = self.page_bbox()
        if not bb:
            return False
        px, py, pw, ph = bb
        return px <= x <= px + pw and py <= y <= py + ph

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.reader._render(debounce=True)   # 尺寸变化：重新居中/渲染当前页

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._in_page(e.x(), e.y()):
            self._drag_start = (e.x(), e.y())
            self._drag_cur = None

    def mouseMoveEvent(self, e):
        if self._drag_start is not None:
            self._drag_cur = (e.x(), e.y())
            self.update()

    def mouseReleaseEvent(self, e):
        if self._drag_start is None:
            return
        x0, y0 = self._drag_start
        self._drag_start = None
        self._drag_cur = None
        self.update()
        if abs(e.x() - x0) < 10 or abs(e.y() - y0) < 10:
            return
        self.reader.add_drag_highlight((x0, y0), (e.x(), e.y()))

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.reader._zoom_by(1.2 if e.angleDelta().y() > 0 else 1 / 1.2)
        elif self.reader.wheel_turn:
            if e.angleDelta().y() < 0:
                self.reader.next_page()
            else:
                self.reader.prev_page()


class ReaderView(QWidget):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.s = getattr(app, "scale", 1.0)
        self.engine: DocumentEngine | None = None
        self.book: Book | None = None
        self.page = 0
        self.zoom = 1.0
        self.candle = False
        self._cache: dict = {}
        self._full_title = ""
        self._engine_lock = threading.Lock()
        self._render_busy = False
        self._pending_key = None
        self._queued = None
        self._need_initial_fit = False
        # 阅读页用独立线程池：避免排队在书架封面任务之后（引擎访问本就串行）
        self.render = (getattr(app, "reader_render", None)
                       or getattr(app, "render", None))
        if self.render is not None:
            self.render.ready.connect(self._on_render_ready)

        st = getattr(app, "settings", {}) or {}
        self.wheel_turn = bool(st.get("wheel_turn", True))
        self.default_zoom = st.get("default_zoom", "fit_page")
        self.candle_default = bool(st.get("candle_default", False))

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(90)
        self._render_timer.timeout.connect(self._render_now)

        # 看门狗：渲染任务异常丢失时避免「一直渲染中」的流水线卡死
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.setInterval(8000)
        self._watchdog.timeout.connect(self._on_render_timeout)

        self._build_toolbar()
        body = QWidget()
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        self.sidebar = Sidebar(self, scale=self.s)
        self.canvas = _PageCanvas(self)
        bl.addWidget(self.sidebar)
        bl.addWidget(self.canvas, 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.toolbar)
        root.addWidget(body, 1)

        self.setFocusPolicy(Qt.StrongFocus)
        self._install_shortcuts()

    # ---------------- 工具栏 ----------------
    def _build_toolbar(self):
        self.toolbar = QWidget()
        self.toolbar.setStyleSheet(f"background:{T.BAR};")
        bar = QHBoxLayout(self.toolbar)
        bar.setContentsMargins(8, 5, 8, 5)
        bar.setSpacing(4)

        bar.addWidget(T.make_button("◀ 书架", self._go_back, scale=self.s))
        self.title_lbl = QLabel("")
        self.title_lbl.setFont(T.font(12, serif=True, scale=self.s))
        self.title_lbl.setStyleSheet(f"color:{T.BRASS};background:transparent;")
        bar.addWidget(self.title_lbl, 1)

        self.candle_btn = T.make_button("烛光", self.toggle_candle,
                                        scale=self.s, role="tag",
                                        checkable=True)
        bar.addWidget(self.candle_btn)
        bar.addWidget(T.make_button("加书签", self.add_bookmark, scale=self.s))
        bar.addWidget(T.make_button("跳页", self._goto_dialog, scale=self.s))
        bar.addWidget(T.make_button("适配页宽", self._fit_width, scale=self.s))
        bar.addWidget(T.make_button("适配整页", self._fit_page, scale=self.s))
        bar.addWidget(T.make_button("－", lambda: self._zoom_by(1 / 1.2),
                                    scale=self.s))
        bar.addWidget(T.make_button("＋", lambda: self._zoom_by(1.2),
                                    scale=self.s))
        self.page_lbl = QLabel("0 / 0")
        self.page_lbl.setFont(T.font(10, scale=self.s))
        self.page_lbl.setStyleSheet(f"color:{T.MUTED};background:transparent;")
        self.page_lbl.setMinimumWidth(int(96 * self.s))
        self.page_lbl.setAlignment(Qt.AlignCenter)
        bar.addWidget(self.page_lbl)
        bar.addWidget(T.make_button("◀", self.prev_page, scale=self.s))
        bar.addWidget(T.make_button("▶", self.next_page, scale=self.s))

    def _install_shortcuts(self):
        def sc(seq, fn):
            s = QShortcut(QKeySequence(seq), self)
            s.setContext(Qt.WidgetWithChildrenShortcut)
            s.activated.connect(fn)
        for seq in ("Left", "PgUp", "W"):
            sc(seq, self.prev_page)
        for seq in ("Right", "PgDown", "S"):
            sc(seq, self.next_page)
        sc("Home", lambda: self.goto(0))
        sc("End", lambda: self.goto(self.page_count - 1))
        for seq in ("+", "="):
            sc(seq, lambda: self._zoom_by(1.2))
        sc("-", lambda: self._zoom_by(1 / 1.2))
        sc("F", self._toggle_favorite)
        # Esc 由主窗口统一处理（返回书架），此处不重复绑定以免快捷键歧义

    # ---------------- 设置即时应用 ----------------
    def apply_options(self, wheel_turn=None, default_zoom=None,
                      candle_default=None):
        if wheel_turn is not None:
            self.wheel_turn = bool(wheel_turn)
        if default_zoom is not None:
            self.default_zoom = default_zoom
        if candle_default is not None:
            self.candle_default = bool(candle_default)

    # ---------------- 生命周期 ----------------
    @property
    def page_count(self) -> int:
        return self.engine.page_count if self.engine else 0

    def open_book(self, book: Book) -> bool:
        try:
            eng = open_engine(book.path)
        except Exception as e:
            QMessageBox.critical(self, "无法打开", f"{book.title}\n\n{e}")
            return False
        if eng.page_count <= 0:
            eng.close()
            QMessageBox.critical(self, "无法打开",
                                 f"{book.title}\n\n文档没有可显示的页面。")
            return False
        self.close()
        self.engine = eng
        self.book = book
        self._cache.clear()
        self._render_busy = False
        self._pending_key = None
        self._queued = None
        self.page = min(max(book.progress - 1, 0), max(eng.page_count - 1, 0))
        self.candle = self.candle_default
        if self.candle:
            self.candle_btn.setStyleSheet(
                f"QPushButton{{background:{T.GOLD};color:#412402;}}")
        self.candle_btn.setChecked(self.candle)
        self._full_title = book.title or ""
        self._apply_title()
        self.sidebar.refresh(book, eng.get_toc(), self.page)
        # 首次自适应缩放：等控件拿到真实尺寸再算（事件驱动 + 兜底定时器）
        self._need_initial_fit = True
        QTimer.singleShot(0, self._maybe_initial_fit)
        QTimer.singleShot(600, lambda: self._maybe_initial_fit(force=True))
        self.setFocus()
        return True

    def close(self):
        with self._engine_lock:
            if self.engine:
                self.engine.close()
                self.engine = None
        self._cache.clear()
        self._pending_key = None
        self._queued = None
        self._need_initial_fit = False

    def _go_back(self):
        self.app.close_reader()

    # ---------------- 标题（超宽截断） ----------------
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_title()
        if self._need_initial_fit:
            self._maybe_initial_fit()

    def showEvent(self, e):
        super().showEvent(e)
        self._maybe_initial_fit()

    def _apply_title(self):
        t = self._full_title
        if not t:
            return
        fm = QFontMetrics(self.title_lbl.font())
        max_w = max(self.title_lbl.width() - 8, 80)
        self.title_lbl.setText(fm.elidedText(t, Qt.ElideRight, max_w))

    # ---------------- 缩放 ----------------
    def _supersample(self) -> float:
        try:
            return 2.0 if float(self.app.settings.get("supersample", 2)) >= 2 else 1.0
        except Exception:
            return 2.0

    def _avail(self):
        return (max(self.canvas.width() - int(56 * self.s), 200),
                max(self.canvas.height() - int(56 * self.s), 200))

    def _fit_page(self):
        self._fit_zoom("fit_page")

    def _fit_width(self):
        self._fit_zoom("fit_width")

    def _fit_zoom(self, mode: str):
        if not self.engine:
            return
        with self._engine_lock:
            pw, ph = self.engine.page_size(self.page)
        aw, ah = self._avail()
        if mode == "fit_width":
            z = aw / max(pw, 1)
        elif mode == "100%":
            z = 1.0
        else:
            z = min(aw / max(pw, 1), ah / max(ph, 1))
        self.zoom = max(min(z, ZOOM_MAX), ZOOM_MIN)
        self._cache.clear()
        self._render_now()

    def _maybe_initial_fit(self, force: bool = False):
        """控件尺寸就绪后执行一次「打开时缩放」。

        需等阅读区真正铺开（避开尚未完成布局、尺寸过小的中间态）；
        force=True 为兜底：小窗口或尺寸迟迟不就绪时也要完成首次适配。
        """
        if not self._need_initial_fit or not self.engine:
            return
        if not force and (self.canvas.width() < 700
                          or self.canvas.height() < 500):
            return
        self._need_initial_fit = False
        self._initial_zoom()

    def _initial_zoom(self):
        mode = self.default_zoom if self.default_zoom in (
            "fit_page", "fit_width", "100%") else "fit_page"
        self._fit_zoom(mode)

    def _fit_and_render(self):
        self._fit_zoom("fit_page")

    def _zoom_by(self, factor: float):
        self.zoom = max(ZOOM_MIN, min(ZOOM_MAX, self.zoom * factor))
        self._cache.clear()
        self._render_now()

    # ---------------- 渲染（后台线程） ----------------
    def _render(self, debounce: bool = False):
        if debounce:
            self._render_timer.start()
        else:
            self._render_now()

    def _page_key(self):
        return ("page", self.page, round(self.zoom, 3), self.candle,
                self._supersample())

    def _render_worker(self, key):
        """后台线程：渲染页面并按超采样倍率缩小，返回 PIL 图像。"""
        _, page, zoom, candle, ss = key
        with self._engine_lock:
            eng = self.engine
            book = self.book
            if eng is None:
                return None
            pw, ph = eng.page_size(page)
            use_ss = ss
            if (pw * zoom * use_ss * ph * zoom * use_ss) > MAX_PIXELS:
                use_ss = 1.0
            rects = [h.rect for h in book.highlights
                     if h.page - 1 == page] if book else []
            img = eng.render(page, zoom * use_ss, rects, candle)
        if use_ss != 1.0:
            img = img.resize((max(int(img.width / use_ss), 1),
                              max(int(img.height / use_ss), 1)), Image.LANCZOS)
        return img

    def _render_now(self):
        self._render_timer.stop()
        if self._need_initial_fit:
            self._maybe_initial_fit()
        if not self.engine:
            return
        key = self._page_key()
        pm = self._cache.get(key)
        if pm is not None:
            self._display(pm)
            return
        if self.render is None:                   # 兜底：同步渲染
            img = self._render_worker(key)
            if img is not None:
                pm = T.pil_to_pixmap(img)
                self._cache[key] = pm
                self._display(pm)
            return
        self.page_lbl.setText(f"{self.page + 1} / {self.page_count} · 渲染中…")
        self._request_render(key)

    def _request_render(self, key):
        if self._render_busy:
            self._queued = key
            return
        self._render_busy = True
        self._pending_key = key
        self._watchdog.start()
        self.render.submit(key, lambda k=key: self._render_worker(k))

    def _on_render_timeout(self):
        """兜底：任务丢失时不至于让渲染流水线永久卡死。"""
        self._render_busy = False
        if self.engine:
            self._render_now()

    def _on_render_ready(self, key, qimg):
        if not isinstance(key, tuple) or not key or key[0] != "page":
            return
        self._watchdog.stop()
        self._render_busy = False
        if qimg is not None:
            if len(self._cache) > 12:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = QPixmap.fromImage(qimg)
        want = self._page_key() if self.engine else None
        if want == key and qimg is not None:
            self._display(self._cache[key])
        elif want is not None and want not in self._cache:
            self._request_render(want)            # 有更新的待渲染页
        elif want is None:
            self.page_lbl.setText("0 / 0")

    def _display(self, pm):
        cw = max(self.canvas.width(), 100)
        ch = max(self.canvas.height(), 100)
        x0 = (cw - pm.width()) // 2
        y0 = max((ch - pm.height()) // 2, int(10 * self.s))
        self.canvas.set_page(pm, x0, y0)
        self.page_lbl.setText(f"{self.page + 1} / {self.page_count}")
        self.app.on_reader_page_change(self.book, self.page + 1)

    def _page_has_bookmark(self) -> bool:
        return bool(self.book and any(
            bm.page == self.page + 1 for bm in self.book.bookmarks))

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
        self._render_now()
        self.setFocus()

    def _goto_dialog(self):
        if not self.engine:
            return
        n = max(self.page_count, 1)
        val, ok = QInputDialog.getInt(self, "跳页", f"页码 (1-{n})：",
                                      self.page + 1, 1, n)
        if ok:
            self.goto(val - 1)

    def _toggle_favorite(self):
        if self.book:
            self.app.library.toggle_favorite(self.book)
            self.app.save_library()

    # ---------------- 书签 / 高亮 ----------------
    def add_bookmark(self):
        if not self.book:
            return
        note, ok = QInputDialog.getText(
            self, "书签", f"在第 {self.page + 1} 页加书签，备注（可空）：")
        if not ok:
            return
        self.app.library.add_bookmark(self.book, self.page + 1, note.strip())
        self.app.save_library()
        self._refresh_sidebar()
        self.canvas.update()

    def remove_bookmark(self, index: int):
        if not self.book:
            return
        bms = sorted(self.book.bookmarks, key=lambda b: b.page)
        if index < len(bms):
            self.book.bookmarks.remove(bms[index])
        self.app.save_library()
        self._refresh_sidebar()
        self.canvas.update()

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
        self.candle_btn.setChecked(self.candle)
        if self.candle:
            self.candle_btn.setStyleSheet(
                f"QPushButton{{background:{T.GOLD};color:#412402;}}")
        else:
            self.candle_btn.setStyleSheet("")
        self._cache.clear()
        self._render_now()

    def _refresh_sidebar(self):
        if self.engine and self.book:
            with self._engine_lock:
                toc = self.engine.get_toc()
            self.sidebar.refresh(self.book, toc, self.page)

    # ---------------- 拖框高亮 ----------------
    def add_drag_highlight(self, start, end):
        if not (self.book and self.engine):
            return
        bb = self.canvas.page_bbox()
        if not bb:
            return
        px, py, pw, ph = bb
        x0, y0 = start
        x1, y1 = end
        sx0 = max(min(x0, x1) - px, 0)
        sy0 = max(min(y0, y1) - py, 0)
        sx1 = min(max(x0, x1) - px, pw)
        sy1 = min(max(y0, y1) - py, ph)
        z = self.zoom
        rect = (sx0 / z, sy0 / z, sx1 / z, sy1 / z)
        note, ok = QInputDialog.getText(
            self, "高亮", f"在第 {self.page + 1} 页添加高亮，备注（可空）：")
        if not ok:
            return
        self.app.library.add_highlight(self.book, self.page + 1, rect,
                                       note.strip())
        self._cache.clear()
        self.app.save_library()
        self._refresh_sidebar()
        self._render_now()
