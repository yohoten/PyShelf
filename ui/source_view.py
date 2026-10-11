"""书源视图：选择书源 → 搜索 → 结果列表（仓库可进入浏览）→ 加入书库下载。"""
from __future__ import annotations

import json
import threading

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout,
                             QInputDialog, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QMessageBox, QProgressBar,
                             QTableWidget, QTableWidgetItem, QTextEdit,
                             QVBoxLayout, QWidget)

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


class SourceView(QWidget):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.s = app.scale
        self.settings = app.settings
        self.sources = S.load_sources(self.settings)
        self.current = self.sources[0] if self.sources else None
        self.results: list[S.RemoteBook] = []
        self.busy = False
        self._stack = []
        self._build()
        self._refresh_sources()

    # ---------------- UI ----------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 顶栏
        head = QWidget()
        head.setStyleSheet(f"background:{T.BAR};")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(10, 6, 10, 6)
        hl.setSpacing(6)
        hl.addWidget(T.make_button("◂ 返回书架", self.app.close_sources,
                                   scale=self.s))
        hl.addWidget(self._title_label("书源检索"))
        self.query = QLineEdit()
        self.query.setFont(T.font(10, scale=self.s))
        self.query.setMinimumWidth(int(220 * self.s))
        self.query.returnPressed.connect(self.search)
        hl.addWidget(self.query)
        hl.addWidget(T.make_button("搜索", self.search, primary=True,
                                   scale=self.s))
        hl.addWidget(T.make_button("加入书库", self.download_selected,
                                   primary=True, scale=self.s))
        hl.addWidget(T.make_button("↻ 刷新列表", self.go_up, scale=self.s))
        hl.addStretch(1)
        hl.addWidget(T.make_button("GitHub Token", self.set_token,
                                   scale=self.s))
        hl.addWidget(T.make_button("关闭", self.app.close_sources, scale=self.s))
        root.addWidget(head)

        # 主体
        body = QWidget()
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)

        left = QWidget()
        left.setFixedWidth(int(210 * self.s))
        left.setStyleSheet(f"background:{T.PANEL};")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(8, 8, 8, 8)
        lbl = QLabel("书源")
        lbl.setFont(T.font(10, scale=self.s))
        lbl.setStyleSheet(f"color:{T.BRASS_DIM};background:transparent;")
        ll.addWidget(lbl)
        self.hint_lbl = QLabel("")
        self.hint_lbl.setWordWrap(True)
        self.hint_lbl.setFont(T.font(9, scale=self.s))
        self.hint_lbl.setStyleSheet(f"color:{T.MUTED};background:transparent;")
        ll.addWidget(self.hint_lbl)
        self.src_list = QListWidget()
        self.src_list.setFont(T.font(10, scale=self.s))
        self.src_list.setStyleSheet(
            f"QListWidget{{background:{T.PANEL};}}")
        self.src_list.currentRowChanged.connect(self._on_source_pick)
        ll.addWidget(self.src_list, 1)
        lbar = QHBoxLayout()
        lbar.addWidget(T.make_button("新增自定义", self.add_custom_source,
                                     scale=self.s))
        lbar.addWidget(T.make_button("删除", self.del_custom_source,
                                     scale=self.s))
        lbar.addStretch(1)
        ll.addLayout(lbar)
        bl.addWidget(left)

        # 右侧结果
        cols = ["标题", "作者 / 仓库", "格式", "大小 / 日期", "说明"]
        self.table = QTableWidget(0, len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setFont(T.font(10, scale=self.s))
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(int(26 * self.s))
        for i, w in enumerate((320, 170, 70, 100, 240)):
            self.table.setColumnWidth(i, int(w * self.s))
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(lambda *_: self._activate())
        bl.addWidget(self.table, 1)
        root.addWidget(body, 1)

        # 状态 + 进度
        self.status = QLabel("选择左侧书源，输入关键词后点「搜索」")
        self.status.setFont(T.font(9, scale=self.s))
        self.status.setStyleSheet(
            f"color:{T.MUTED};background:{T.PANEL};padding:4px 8px;")
        root.addWidget(self.status)
        self.pbar = QProgressBar()
        self.pbar.setRange(0, 100)
        self.pbar.setTextVisible(False)
        self.pbar.setVisible(False)
        root.addWidget(self.pbar)

    def _title_label(self, text):
        lbl = QLabel(text)
        lbl.setFont(T.font(13, serif=True, scale=self.s))
        lbl.setStyleSheet(f"color:{T.BRASS};background:transparent;")
        return lbl

    def set_status(self, text: str):
        self.status.setText(text)

    def set_progress(self, value: int):
        if not self.pbar.isVisible():
            self.pbar.setVisible(True)
        self.pbar.setValue(value)

    # ---------------- 书源列表 ----------------
    def _refresh_sources(self):
        self.src_list.clear()
        for src in self.sources:
            prefix = "  " if not isinstance(src, S.CustomSource) else "  ◆ "
            self.src_list.addItem(QListWidgetItem(prefix + src.name))
        if self.sources:
            self.src_list.setCurrentRow(0)
        if self.settings.get("github_token"):
            self.set_status("已配置 GitHub Token，API 限额已提升")

    def _on_source_pick(self, row: int):
        if row < 0 or row >= len(self.sources):
            return
        self.current = self.sources[row]
        self.hint_lbl.setText(getattr(self.current, "hint", ""))

    def add_custom_source(self):
        dlg = _SpecDialog(self, self.s)
        if dlg.exec_() != QDialog.Accepted or not dlg.result:
            return
        specs = self.settings.setdefault("custom_sources", [])
        specs.append(dlg.result)
        self.app.save_settings()
        self.sources = S.load_sources(self.settings)
        self._refresh_sources()
        self.set_status(f"已添加自定义书源：{dlg.result.get('name')}")

    def del_custom_source(self):
        if not isinstance(self.current, S.CustomSource):
            QMessageBox.information(self, "提示", "内置书源不可删除。")
            return
        specs = self.settings.get("custom_sources", [])
        specs = [s for s in specs if s.get("name") != self.current.name]
        self.settings["custom_sources"] = specs
        self.app.save_settings()
        self.sources = S.load_sources(self.settings)
        self._refresh_sources()

    def set_token(self):
        tok, ok = QInputDialog.getText(
            self, "GitHub Token",
            "填入 GitHub Personal Access Token"
            "（可选，仅用于提升 API 限额，留空则清除）：")
        if not ok:
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
            self.set_status("上一个检索还在进行中，请稍候…")
            return
        q = self.query.text().strip()
        if not q:
            self.set_status("请输入关键词")
            return
        self.busy = True
        self.set_status(f"正在 {self.current.name} 检索「{q}」…")
        threading.Thread(target=self._search_worker, args=(self.current, q),
                         daemon=True).start()

    def _search_worker(self, src, q):
        try:
            res = src.search(q)
            self.app.q.put(("src_results", src.name, res))
        except Exception as e:
            self.app.q.put(("src_error", src.name, f"{type(e).__name__}: {e}"))

    def show_results(self, src_name: str, res: list):
        self.busy = False
        self.results = res
        self.table.setRowCount(0)
        self.table.setRowCount(len(res))
        for i, b in enumerate(res):
            for col, val in enumerate((b.title, b.author, b.fmt, b.size, b.note)):
                self.table.setItem(i, col, QTableWidgetItem(str(val or "")))
        self.set_status(
            f"{src_name}：命中 {len(res)} 条 · "
            f"双击仓库行可进入浏览文件，选中后点「加入书库」下载")

    def show_error(self, src_name: str, msg: str):
        self.busy = False
        self.set_status(f"{src_name} 出错：{msg}")
        QMessageBox.critical(self, "书源错误", f"{src_name}\n\n{msg}")

    # ---------------- 交互 ----------------
    def _selected(self) -> list:
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        return [self.results[r] for r in rows if 0 <= r < len(self.results)]

    def _activate(self):
        sel = self._selected()
        if not sel:
            return
        if sel[0].kind == "repo":
            self.browse_repo(sel[0])
        else:
            self.download_selected()

    def browse_repo(self, book):
        if not isinstance(self.current, S.GitHubSource):
            return
        self.set_status(f"正在读取仓库 {book.repo} 的文件列表…")
        self.busy = True
        self._stack.append(("repo", book.repo))

        def worker():
            try:
                files = self.current.list_files(book.repo, book.ref)
                self.app.q.put(("src_results",
                                f"{book.repo} 内的书籍文件", files))
            except Exception as e:
                self.app.q.put(("src_error", book.repo,
                                f"{type(e).__name__}: {e}"))

        threading.Thread(target=worker, daemon=True).start()

    def go_up(self):
        """刷新当前检索结果（在仓库文件列表内时退回仓库搜索结果）。"""
        if self._stack:
            self._stack.pop()
        self.search()

    def finish_progress(self):
        self.pbar.setValue(0)
        self.pbar.setVisible(False)

    def download_selected(self):
        sel = [b for b in self._selected() if b.kind == "file" and b.url]
        if not sel:
            QMessageBox.information(self, "提示",
                                    "请选中至少一个可下载的文件条目\n"
                                    "（仓库条目请先双击进入）")
            return
        self.pbar.setVisible(True)
        self.pbar.setValue(0)
        self.app.start_downloads(sel)
        self.set_status(f"已加入下载队列：{len(sel)} 个文件")


class _SpecDialog(QDialog):
    """自定义书源规则编辑对话框。"""

    def __init__(self, master, scale=1.0):
        super().__init__(master)
        self.result = None
        self.s = scale
        self.setWindowTitle("新增自定义书源")
        self.setModal(True)
        lay = QVBoxLayout(self)
        tip = QLabel("填写 JSON 规则（字段说明见 README「书源」章节）")
        tip.setFont(T.font(10, scale=scale))
        tip.setStyleSheet(f"color:{T.BRASS_DIM};")
        lay.addWidget(tip)
        self.txt = QTextEdit()
        self.txt.setFont(T.font(10, scale=scale))
        self.txt.setPlainText(json.dumps(CUSTOM_SPEC_TEMPLATE,
                                         ensure_ascii=False, indent=2))
        self.txt.setMinimumSize(int(560 * scale), int(320 * scale))
        lay.addWidget(self.txt)
        bar = QHBoxLayout()
        bar.addStretch(1)
        bar.addWidget(T.make_button("取消", self.reject, scale=scale))
        bar.addWidget(T.make_button("保存", self._save, primary=True,
                                    scale=scale))
        lay.addLayout(bar)

    def _save(self):
        raw = self.txt.toPlainText().strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            QMessageBox.critical(self, "JSON 格式错误", str(e))
            return
        if not data.get("name") or not data.get("search_url"):
            QMessageBox.critical(self, "缺少字段", "至少需要 name 与 search_url")
            return
        if "{q}" not in data["search_url"]:
            QMessageBox.critical(self, "缺少占位符",
                                 "search_url 必须包含 {q} 作为关键词占位符")
            return
        self.result = data
        self.accept()
