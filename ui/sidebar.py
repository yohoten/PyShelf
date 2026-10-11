"""阅读侧栏：目录 / 书签 / 高亮 三个标签页（QTabWidget + QTreeWidget/QListWidget）。"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QHBoxLayout, QListWidget, QTabWidget,
                             QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from core.library import Book
from ui import theme as T


class Sidebar(QWidget):
    def __init__(self, reader, scale: float = 1.0, parent=None):
        super().__init__(parent)
        self.reader = reader
        self.s = scale
        self.book: Book | None = None
        self.setFixedWidth(int(248 * scale))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.nb = QTabWidget()
        self.nb.setFont(T.font(10, scale=scale))
        lay.addWidget(self.nb)

        # ---- 目录 ----
        self.toc_tree = QTreeWidget()
        self.toc_tree.setHeaderHidden(True)
        self.toc_tree.setFont(T.font(10, scale=scale))
        self.toc_tree.itemClicked.connect(self._on_toc_click)
        self.nb.addTab(self.toc_tree, " 目录 ")

        # ---- 书签 ----
        bm = QWidget()
        bl = QVBoxLayout(bm)
        bl.setContentsMargins(4, 4, 4, 4)
        self.bm_list = QListWidget()
        self.bm_list.setFont(T.font(10, scale=scale))
        self.bm_list.itemDoubleClicked.connect(self._on_bm_jump)
        bl.addWidget(self.bm_list)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(T.make_button("删除所选", self._del_bookmark, scale=scale))
        bl.addLayout(row)
        self.nb.addTab(bm, " 书签 ")

        # ---- 高亮 ----
        hl = QWidget()
        hll = QVBoxLayout(hl)
        hll.setContentsMargins(4, 4, 4, 4)
        self.hl_list = QListWidget()
        self.hl_list.setFont(T.font(10, scale=scale))
        self.hl_list.setStyleSheet("QListWidget { color: #D9A03A; }")
        self.hl_list.itemDoubleClicked.connect(self._on_hl_jump)
        hll.addWidget(self.hl_list)
        row2 = QHBoxLayout()
        row2.addStretch(1)
        row2.addWidget(T.make_button("删除所选", self._del_highlight, scale=scale))
        hll.addLayout(row2)
        self.nb.addTab(hl, " 高亮 ")

    # ---------------- 刷新 ----------------
    def refresh(self, book: Book, toc=(), current_page: int = 0):
        self.book = book
        self.toc_tree.clear()
        for level, title, page in toc:
            title = (title or "").strip() or f"第 {page} 页"
            item = QTreeWidgetItem(["    " * max(level - 1, 0) + title])
            item.setData(0, Qt.UserRole, page)
            self.toc_tree.addTopLevelItem(item)

        self.bm_list.clear()
        for bm in sorted(book.bookmarks, key=lambda b: b.page):
            self.bm_list.addItem(
                f"P.{bm.page}" + (f"  {bm.note}" if bm.note else ""))

        self.hl_list.clear()
        for i, h in enumerate(book.highlights):
            self.hl_list.addItem(
                f"P.{h.page}  #{i + 1}" + (f"  {h.note}" if h.note else ""))
            item = self.hl_list.item(self.hl_list.count() - 1)
            item.setForeground(QColor("#D9A03A"))

    # ---------------- 事件 ----------------
    def _on_toc_click(self, item, _col=0):
        page = item.data(0, Qt.UserRole)
        if page and page >= 1:
            self.reader.goto(page - 1)

    def _on_bm_jump(self, _item=None):
        if not self.book:
            return
        row = self.bm_list.currentRow()
        bms = sorted(self.book.bookmarks, key=lambda b: b.page)
        if 0 <= row < len(bms):
            self.reader.goto(bms[row].page - 1)

    def _on_hl_jump(self, _item=None):
        if not self.book:
            return
        row = self.hl_list.currentRow()
        if 0 <= row < len(self.book.highlights):
            self.reader.goto(self.book.highlights[row].page - 1)

    def _del_bookmark(self):
        row = self.bm_list.currentRow()
        if row >= 0 and self.book and self.reader:
            self.reader.remove_bookmark(row)

    def _del_highlight(self):
        row = self.hl_list.currentRow()
        if row >= 0 and self.book and self.reader:
            self.reader.remove_highlight(row)
