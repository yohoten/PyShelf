"""书籍目录管理对话框：添加/移除多个书籍目录。

设计要点：
- 所有已添加目录汇总到同一书架（多目录扫描后按内容指纹去重）；
- 「移除所选」仅修改目录配置，**不删除**任何书籍记录与阅读记忆
  （书签/高亮/进度），重新添加该目录即可恢复；
- 风格复用 ui.theme 配色与按钮工厂。
"""
from __future__ import annotations

import os
from pathlib import Path

from PyQt5.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel,
                             QListWidget, QMessageBox, QVBoxLayout)

from ui import theme as T


class DirManagerDialog(QDialog):
    """目录管理：列出已添加目录（含每目录在架书籍数），支持添加/移除。"""

    def __init__(self, app, parent=None):
        super().__init__(parent if parent is not None else app)
        self.app = app
        self.setWindowTitle("管理书籍目录")
        self.setModal(True)

        lay = QVBoxLayout(self)
        head = QLabel("已添加的书籍目录（可添加多个，书籍汇总到同一书架）")
        head.setFont(T.font(11, serif=True, scale=app.scale))
        head.setStyleSheet(f"color:{T.BRASS};")
        lay.addWidget(head)

        self.lb = QListWidget()
        self.lb.setFont(T.font(10, scale=app.scale))
        self.lb.setMinimumSize(int(580 * app.scale), int(260 * app.scale))
        self.lb.itemDoubleClicked.connect(lambda *_: self._open_dir())
        lay.addWidget(self.lb)

        bar = QHBoxLayout()
        bar.addWidget(T.make_button("添加目录", self._add, primary=True,
                                    scale=app.scale))
        bar.addWidget(T.make_button("移除所选", self._remove, scale=app.scale))
        bar.addStretch(1)
        bar.addWidget(T.make_button("关闭", self.accept, scale=app.scale))
        lay.addLayout(bar)

        self._refresh()

    # ---------------- 内部 ----------------
    def _refresh(self):
        self.lb.clear()
        dirs = self.app.library.library_dirs          # 规范化：用于计数/操作
        raw = self.app.library.library_dirs_raw       # 原始录入：用于展示（F-11）
        for i, d in enumerate(dirs):
            display = raw[i] if i < len(raw) else d   # 保留用户录入大小写
            n = self.app.library.count_books_under(d)
            state = "" if Path(d).is_dir() else "   ·   ⚠ 目录不可访问"
            self.lb.addItem(f"{display}   ·   {n} 本{state}")

    def _add(self):
        d = QFileDialog.getExistingDirectory(self, "添加书籍目录",
                                             str(Path.home()))
        if not d:
            return
        if not Path(d).is_dir():
            QMessageBox.critical(self, "无效目录", "目录不存在或不可访问。")
            return
        if self.app.library.add_dir(d):
            self._refresh()
            self.app.start_scan(self.app.library.library_dirs)   # 触发重扫
        else:
            QMessageBox.information(self, "已存在", "该目录已在列表中。")

    def _remove(self):
        i = self.lb.currentRow()
        if i < 0:
            return
        dirs = self.app.library.library_dirs
        if i >= len(dirs):
            return
        d = dirs[i]
        if QMessageBox.question(
                self, "移除目录",
                f"从书架移除目录：\n{d}\n\n"
                "书籍与阅读记录（书签/高亮/进度）将保留，"
                "重新添加该目录即可恢复。") == QMessageBox.Yes:
            self.app.library.remove_dir(d)     # 只改配置，不删记忆
            self._refresh()
            self.app.start_scan(self.app.library.library_dirs)

    def _open_dir(self):
        i = self.lb.currentRow()
        dirs = self.app.library.library_dirs
        if i < 0 or i >= len(dirs):
            return
        try:
            os.startfile(dirs[i])  # noqa: S606
        except Exception:
            QMessageBox.critical(self, "失败", "无法打开该目录。")
