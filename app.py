"""Py 书斋 —— 拟物复古书架阅读器 + 多书源检索（PyQt5 + PyMuPDF）。"""
from __future__ import annotations

import ctypes
import json
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import QByteArray, Qt, QTimer
from PyQt5.QtGui import QIcon, QKeySequence
from PyQt5.QtWidgets import (QApplication, QDialog, QFileDialog, QHBoxLayout,
                             QInputDialog, QLabel, QLineEdit, QMainWindow,
                             QMenu, QMessageBox, QScrollArea, QShortcut,
                             QStackedWidget, QVBoxLayout, QWidget)

from core import scanner
from core.downloader import Downloader
from core.library import Book, Library
from core.models import _norm_dir
from ui import theme as T
from ui.dir_manager import DirManagerDialog
from ui.reader_view import ReaderView
from ui.render import RenderService
from ui.settings_dialog import SettingsDialog
from ui.shelf_view import ShelfView
from ui.source_view import SourceView

def _resolve_app_dir() -> Path:
    """定位「应用目录」：源码运行时为脚本所在目录；PyInstaller 单文件打包后
    `__file__` 指向临时解包目录（`sys._MEIPASS`），该目录每次启动重新解包、
    退出即清理 —— 若仍以其为基准，`data/` 会落在临时目录，导致迁移来的阅读数据
    （`data/library.db` / `data/library.json`）无法被识别，本次阅读记录与设置
    也在退出时一并丢失。故冻结态一律以可执行文件所在目录为基准，
    使 `data/` 与 exe 同目录、可随目录整体迁移。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = _resolve_app_dir()
DATA_DIR = APP_DIR / "data"
ICON_PATH = APP_DIR / "app.ico"
CRASH_LOG = DATA_DIR / "crash.log"
FILTERS = ("全部", "收藏", "历史")

# 设置默认值（与 data/settings.json 合并；缺失键自动补齐）
DEFAULT_SETTINGS = {
    "theme": "dark",
    "ui_font": "Microsoft YaHei",
    "title_font": "KaiTi",
    "font_scale": 1.0,
    "cover_size": "medium",
    "show_recent": True,
    "show_favorites": True,
    "show_tooltips": True,
    "default_zoom": "fit_page",
    "candle_default": False,
    "wheel_turn": True,
    "supersample": 2,
    "render_threads": 2,
    "github_token": "",
    "custom_sources": [],
    "filter_mode": "全部",
    "geometry": "",
}


def write_crash(exc, val, tb, tag: str = ""):
    """pythonw 无控制台：未捕获异常默认无声消失；统一落到 data/crash.log。"""
    try:
        import traceback
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n==== {datetime.now().isoformat(timespec='seconds')}"
                    f"{tag} ====\n")
            traceback.print_exception(exc, val, tb, file=f)
    except Exception:
        pass


def install_crash_log():
    def _hook(tp, val, tb):
        write_crash(tp, val, tb)
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


class PyShelfApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Py 书斋 · 拟物书架")
        self.resize(1240, 820)
        self.setMinimumSize(900, 600)
        self.icon_ok = ICON_PATH.exists()
        if self.icon_ok:
            self.setWindowIcon(QIcon(str(ICON_PATH)))

        self.scale = T.dpi_scale()
        self.settings = {**DEFAULT_SETTINGS, **self._load_settings()}
        # 主题与字体需在创建任何控件之前生效
        T.use_theme(self.settings.get("theme", "dark"))
        T.configure_fonts(self.settings.get("ui_font"),
                          self.settings.get("title_font"),
                          self.settings.get("font_scale"))

        self.library = Library(DATA_DIR)
        self.q: queue.Queue = queue.Queue()
        self.downloader = Downloader(self.q)
        # 后台渲染线程池：书架封面（线程数可调）+ 阅读页（独立单线程，优先响应）
        self.render = RenderService(self.settings.get("render_threads", 2), self)
        self.reader_render = RenderService(1, self)
        self.filter_mode = self.settings.get("filter_mode", "全部")
        if self.filter_mode not in FILTERS:
            self.filter_mode = "全部"
        self.search_text = ""
        self._covers_dirty = False
        self._scan_epoch = 0            # 扫描代次：仅最新结果被采纳（F-4）
        self._progress_ts = 0.0         # D-4：上次进度落库时间戳
        self._progress_pages = 0        # D-4：自上次落库累计翻页数

        self._root = QWidget()
        self._root_layout = QVBoxLayout(self._root)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(0)
        self.setCentralWidget(self._root)

        self._build_topbar()
        self._build_body()

        # 记忆上次窗口大小/位置
        geo = self.settings.get("geometry", "")
        if geo:
            try:
                self.restoreGeometry(QByteArray.fromBase64(geo.encode("ascii")))
            except Exception:
                pass

        # 全局样式表（主题生效后应用）
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(T.global_qss())

        # 后台事件泵
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(400)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()

        # Esc：书源界面返回书架 / 阅读界面返回书架
        esc = QShortcut(QKeySequence("Esc"), self)
        esc.setContext(Qt.WindowShortcut)
        esc.activated.connect(self._on_escape)

        if self.library.library_dirs:
            QTimer.singleShot(120, lambda: self.start_scan(self.library.library_dirs))
        else:
            QTimer.singleShot(200, self._first_run)

    # ---------------- 设置 ----------------
    def _load_settings(self) -> dict:
        p = DATA_DIR / "settings.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {**DEFAULT_SETTINGS, "custom_sources": []}

    def save_settings(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "settings.json").write_text(
            json.dumps(self.settings, ensure_ascii=False, indent=1),
            encoding="utf-8")

    # ---------------- UI ----------------
    def _build_topbar(self):
        self.topbar = QWidget()
        self.topbar.setStyleSheet(f"background:{T.BAR};")
        bar = QHBoxLayout(self.topbar)
        bar.setContentsMargins(16, 8, 12, 8)
        bar.setSpacing(4)

        title = QLabel("Py 书斋")
        title.setFont(T.font(16, serif=True, scale=self.scale))
        title.setStyleSheet(f"color:{T.BRASS};background:transparent;")
        bar.addWidget(title)
        bar.addSpacing(8)

        self.search_entry = QLineEdit()
        self.search_entry.setPlaceholderText("搜索书名…")
        self.search_entry.setFont(T.font(10, scale=self.scale))
        self.search_entry.setFixedWidth(int(200 * self.scale))
        self.search_entry.textChanged.connect(self._on_search)
        bar.addWidget(self.search_entry)

        self.filter_btns = {}
        for name in FILTERS:
            b = T.make_button(name, lambda n=name: self._set_filter(n),
                              primary=(name == self.filter_mode),
                              scale=self.scale)
            bar.addWidget(b)
            self.filter_btns[name] = b

        bar.addSpacing(8)
        bar.addWidget(T.make_button("书源检索", self.open_sources, primary=True,
                                    scale=self.scale))
        self.src_toggle_btn = T.make_button("◂ 返回书架", self.close_sources,
                                            primary=True, scale=self.scale)
        self.src_toggle_btn.setVisible(False)
        bar.addWidget(self.src_toggle_btn)
        bar.addStretch(1)

        bar.addWidget(self._vsep())
        self.status_lbl = QLabel("")
        self.status_lbl.setFont(T.font(9, scale=self.scale))
        self.status_lbl.setStyleSheet(f"color:{T.MUTED};background:transparent;")
        bar.addWidget(self.status_lbl)
        bar.addWidget(self._vsep())
        bar.addWidget(T.make_button("管理书籍目录", self._manage_dirs,
                                    dim=True, scale=self.scale))
        self.theme_btn = T.make_button(self._theme_label(), self._toggle_theme,
                                       dim=True, scale=self.scale)
        bar.addWidget(self.theme_btn)
        bar.addWidget(T.make_button("⚙ 设置", self.open_settings,
                                    scale=self.scale, role="tag"))

        self._root_layout.addWidget(self.topbar)

    @staticmethod
    def _vsep():
        """顶栏分组之间的竖分隔线。"""
        w = QWidget()
        w.setFixedWidth(1)
        w.setStyleSheet(f"background:{T.PLANK_EDGE};")
        return w

    def _build_body(self):
        self.stack = QStackedWidget()

        self.shelf_scroll = QScrollArea()
        self.shelf_scroll.setWidgetResizable(True)
        self.shelf_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 竖滚动条常显：避免「内容变高→滚动条出现→视口变窄→重排」的来回抖动
        self.shelf_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.shelf = ShelfView(
            DATA_DIR, on_open=self.open_reader, on_context=self._book_menu,
            scale=self.scale,
            cover_size=self.settings.get("cover_size", "medium"),
            show_recent=self.settings.get("show_recent", True),
            show_favorites=self.settings.get("show_favorites", True),
            show_tooltips=self.settings.get("show_tooltips", True),
            render=self.render)
        self.shelf_scroll.setWidget(self.shelf)

        self.reader = ReaderView(self)
        self.source_view = SourceView(self)

        self.stack.addWidget(self.shelf_scroll)   # index 0
        self.stack.addWidget(self.reader)         # index 1
        self.stack.addWidget(self.source_view)    # index 2
        self._root_layout.addWidget(self.stack, 1)

    # ---------------- 主题 ----------------
    def _theme_label(self) -> str:
        return "☾ 午后书房" if T.current_theme() == "dark" else "☀ 烛光书房"

    def _toggle_theme(self):
        new = "light" if T.current_theme() == "dark" else "dark"
        self.settings["theme"] = new
        self.save_settings()
        QMessageBox.information(
            self, "主题已切换",
            f"已选择「{'午后书房' if new == 'light' else '烛光书房'}」主题。\n"
            "重启应用后生效（阅读进度与设置不受影响）。")

    def _set_status(self, text: str):
        self.status_lbl.setText(text)

    # ---------------- 设置中心 ----------------
    def open_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec_() != QDialog.Accepted or not dlg.result_settings:
            return
        old = dict(self.settings)
        # 原字典原地更新（保持与 SourceView 等持有的引用一致）
        self.settings.clear()
        self.settings.update(dlg.result_settings)
        self.save_settings()
        self._apply_settings(old, self.settings)

    def _apply_settings(self, old: dict, new: dict):
        """即时生效项立即应用；主题/字体类改动提示重启。"""
        self.shelf.set_cover_size(new.get("cover_size", "medium"))
        self.shelf.set_sections(new.get("show_recent"),
                                new.get("show_favorites"))
        self.shelf.set_tooltips(bool(new.get("show_tooltips", True)))
        self.reader.apply_options(
            wheel_turn=new.get("wheel_turn", True),
            default_zoom=new.get("default_zoom", "fit_page"),
            candle_default=new.get("candle_default", False))
        self.render.set_threads(new.get("render_threads", 2))
        self.theme_btn.setText(self._theme_label())

        restart = []
        if old.get("theme") != new.get("theme"):
            restart.append("主题")
        if (old.get("ui_font") != new.get("ui_font")
                or old.get("title_font") != new.get("title_font")
                or abs(float(old.get("font_scale", 1.0))
                       - float(new.get("font_scale", 1.0))) > 1e-6):
            restart.append("字体 / 字号")
        if restart:
            QMessageBox.information(
                self, "设置已保存",
                "设置已保存。以下更改将在重启应用后生效：\n  · "
                + "\n  · ".join(restart))

    # ---------------- 书库 ----------------
    def _first_run(self):
        QMessageBox.information(
            self, "欢迎使用 Py 书斋",
            "首次使用：请选择书籍目录（可添加多个，递归扫描 "
            "PDF / EPUB / TXT / MD，所有目录汇总到同一书架）。\n\n"
            "双击封面开始阅读 · 右键管理收藏 · 阅读中拖框即高亮 · "
            "「书源检索」可搜索 GitHub 开源书库与 arXiv 论文。")
        self._pick_dir()

    def _pick_dir(self):
        """首次运行/快捷入口：添加一个书籍目录（可重复添加多个）。"""
        start = (self.library.library_dirs[0]
                 if self.library.library_dirs else str(Path.home()))
        d = QFileDialog.getExistingDirectory(self, "添加书籍目录", start)
        if not d:
            return
        if not Path(d).is_dir():
            QMessageBox.critical(self, "无效目录", "目录不存在或不可访问。")
            return
        if self.library.add_dir(d):
            self.start_scan(self.library.library_dirs)
        else:
            QMessageBox.information(self, "已存在", "该目录已在列表中。")

    def _manage_dirs(self):
        """打开目录管理对话框（添加/移除多个书籍目录）。"""
        DirManagerDialog(self).exec_()

    def _accept_epoch(self, epoch: int) -> bool:
        """仅最新扫描代次的结果有效；过期结果一律丢弃（F-4）。"""
        return epoch == self._scan_epoch

    def start_scan(self, dirs: list[str]):
        self._scan_epoch += 1
        epoch = self._scan_epoch
        self.library.library_dirs = list(dirs)
        self.library.library_dir = dirs[0] if dirs else ""   # 兼容取首个
        self.library.save()
        self._set_status("扫描中…")
        # 快照在主线程构建（避免与 UI/worker 读写竞态）：规范路径 -> (指纹, 页数)
        snapshot = {_norm_dir(b.path): (b.fingerprint, b.pages)
                    for b in self.library.books.values()
                    if b.readable and b.pages > 0 and b.fingerprint}
        threading.Thread(target=self._scan_worker,
                         args=(epoch, list(dirs), snapshot),
                         daemon=True).start()

    def _scan_worker(self, epoch: int, dirs: list[str], snapshot: dict):
        """后台线程**只做纯 IO**（磁盘扫描 + PDF 探测），绝不遍历/改动
        `self.library.books`；字典增删一律回主线程，杜绝
        `dictionary changed size during iteration`。结果带 epoch，供主线程判新旧。"""
        infos = scanner.collect_infos(dirs, snapshot)
        self.q.put(("scanned", epoch, infos))

    def _start_covers(self, epoch: int):
        """主线程基于**最新库状态**构建封面任务后交后台纯 IO 线程渲染。"""
        todo = [(b.id, b.path, b.title, b.ext)
                for b in self.library.books.values()
                if b.present and not (b.cover_ok
                                      and scanner.cover_path(DATA_DIR, b.id).exists())]
        if not todo:
            self.q.put(("done", epoch, 0))
            return
        threading.Thread(target=self._cover_worker, args=(epoch, todo),
                         daemon=True).start()

    def _cover_worker(self, epoch: int, todo: list):
        """仅对在架(present)且缺封面的书渲染（免为失联文件做无用功）；
        扫描代次已过期则立即放弃，结果回主线程更新。"""
        n = 0
        for bid, path, title, ext in todo:
            if not self._accept_epoch(epoch):
                return
            ok = scanner.generate_cover(Path(path),
                                        scanner.cover_path(DATA_DIR, bid),
                                        title, ext)
            self.q.put(("cover", epoch, bid, ok))
            n += 1
        self.q.put(("done", epoch, n))

    # ---------------- 后台事件 ----------------
    def _handle_msg(self, msg):
        """处理单条后台消息。任何一条失败都不允许拖垮轮询循环。"""
        kind = msg[0]
        if kind == "scanned":
            epoch, infos = msg[1], msg[2]
            if not self._accept_epoch(epoch):
                return                                   # 过期扫描结果丢弃（F-4）
            stats = self.library.reconcile_scan(infos)   # 重绑 / 新增 / 标记失联
            self.library.save()                          # 落库 + 迁移记忆 + 清理旧行
            self.refresh_shelf()
            self._set_status(f"书库 {stats['present']} 本 · 失联 {stats['missing']} 本 "
                             "· 封面生成中…")
            self._start_covers(epoch)                    # 主线程构建封面任务
        elif kind == "cover":
            epoch, bid, ok = msg[1], msg[2], msg[3]
            if not self._accept_epoch(epoch):
                return
            b = self.library.books.get(bid)
            if b is not None:
                b.cover_ok = bool(ok)
            self.shelf.invalidate_thumb(bid)
            self._covers_dirty = True
        elif kind == "done":
            epoch = msg[1]
            if not self._accept_epoch(epoch):
                return
            self.library.save()
            self.refresh_shelf()
            self._set_status(self._status_text())
        elif kind == "src_results":
            self.source_view.show_results(msg[1], msg[2])
        elif kind == "src_error":
            self.source_view.show_error(msg[1], msg[2])
        elif kind == "dl_progress":
            self.source_view.set_status(
                f"下载中 {msg[1]} · {msg[2]}% · {msg[3]}")
            self.source_view.set_progress(msg[2])
        elif kind == "dl_done":
            self.source_view.set_status(
                f"下载完成：{Path(msg[1]).name} · 已加入书库")
            self.source_view.finish_progress()
            self._set_status("新增书籍，重新扫描书库…")
            self.start_scan(self.library.library_dirs)
        elif kind == "dl_fail":
            self.source_view.set_status(f"下载失败：{msg[1]}")
            self.source_view.finish_progress()
            QTimer.singleShot(50, lambda: QMessageBox.critical(
                self, "下载失败", f"{msg[1]}\n\n{msg[2]}"))

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                try:
                    self._handle_msg(msg)
                except Exception:
                    # 单条消息失败不终止循环（否则后台事件泵永久停摆）
                    write_crash(*sys.exc_info(), tag="[消息]")
        except queue.Empty:
            pass
        if self._covers_dirty:
            self._covers_dirty = False
            self.shelf.redraw()

    # ---------------- 阅读统计 ----------------
    def _status_text(self) -> str:
        n = sum(1 for b in self.library.books.values() if b.present)
        today = self.library.minutes_today()
        total = self.library.hours_total()
        if today >= 60:
            today_s = f"{today / 60:.1f} 小时"
        elif today >= 1:
            today_s = f"{today:.0f} 分钟"
        else:
            today_s = "不足 1 分钟"
        return f"书库 {n} 本 · 今日阅读 {today_s} · 累计 {total:.1f} 小时"

    # ---------------- 书架 ----------------
    def _visible_books(self) -> list[Book]:
        # 失联书（present=False）不显示，但记录与记忆保留在库
        books = [b for b in self.library.books.values() if b.present]
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
            b.setProperty("role", "primary" if n == name else "")
            T.refresh_widget(b)
        self.refresh_shelf()

    def _on_search(self, _text=""):
        self.search_text = self.search_entry.text().strip()
        self.refresh_shelf()

    def _book_menu(self, book: Book, global_pos):
        m = QMenu(self)
        m.addAction("★ 取消收藏" if book.favorite else "☆ 加入收藏",
                    lambda: self._toggle_fav(book))
        m.addSeparator()
        m.addAction("从上次位置继续", lambda: self.open_reader(book))
        m.addAction("打开所在文件夹", lambda: self._open_folder(book))
        m.addAction("重命名显示标题", lambda: self._rename(book))
        m.addSeparator()
        m.addAction("从书架移除记录（不删文件）", lambda: self._remove(book))
        m.exec_(global_pos)

    def _toggle_fav(self, book: Book):
        self.library.toggle_favorite(book)
        self.library.save()
        self.refresh_shelf()

    def _open_folder(self, book: Book):
        try:
            import os
            os.startfile(str(Path(book.path).parent))  # noqa: S606
        except Exception:
            QMessageBox.critical(self, "失败", "无法打开文件夹。")

    def _rename(self, book: Book):
        ans, ok = QInputDialog.getText(self, "重命名", "显示标题：",
                                       text=book.title)
        if ok and ans.strip():
            book.title = ans.strip()
            book.title_custom = True      # D-6：标记自定义，重扫不覆盖
            self.library.save()
            self.shelf.invalidate_thumb(book.id)
            self.refresh_shelf()

    def _remove(self, book: Book):
        if QMessageBox.question(
                self, "移除",
                f"从书架移除「{book.title}」的记录？\n"
                "（不会删除原文件，仅清除阅读数据）") == QMessageBox.Yes:
            self.library.delete(book.id)   # 落库删除并级联清记忆（F-5）
            self.shelf.invalidate_thumb(book.id)
            self.refresh_shelf()

    # ---------------- 阅读器 ----------------
    def open_reader(self, book: Book):
        if not book.readable:
            QMessageBox.warning(self, "无法读取", f"「{book.title}」无法被解析。")
            return
        if not self.reader.open_book(book):
            return          # 打开失败：留在书架
        self.src_toggle_btn.setVisible(False)
        self.stack.setCurrentWidget(self.reader)
        self.library.touch_history(book, book.progress or 1)
        self.library.save()
        self.library.begin_session(book)   # 阅读时长统计
        self._progress_ts = time.time()    # D-4：重置节流计时
        self._progress_pages = 0

    def on_reader_page_change(self, book: Book, page: int):
        """翻页回调：更新内存进度并对**进度落库做节流**（D-4）。

        每累计 ≥10 页 或 距上次落库 ≥5 秒才写一次（主线程判断，避免频繁 IO 卡顿），
        使阅读中崩溃/强杀至多丢失约 10 页或 5 秒内的进度；退出/关闭时仍兜底全量
        save()。仅调用 save_book(book) 单本 upsert，写入量与影响面最小。
        """
        if not book:
            return
        self.library.touch_history(book, page)
        self._progress_pages += 1
        now = time.time()
        if self._progress_pages >= 10 or (now - self._progress_ts) >= 5.0:
            self.library.save_book(book)
            self._progress_pages = 0
            self._progress_ts = now

    def close_reader(self):
        self.library.end_session(self.reader.page + 1)
        self.library.save()
        self.reader.close()
        self.stack.setCurrentWidget(self.shelf_scroll)
        self.refresh_shelf()
        self._set_status(self._status_text())

    # ---------------- 书源 ----------------
    def open_sources(self):
        self.stack.setCurrentWidget(self.source_view)
        self.src_toggle_btn.setVisible(True)
        self.source_view.query.setFocus()

    def close_sources(self):
        self.src_toggle_btn.setVisible(False)
        self.stack.setCurrentWidget(self.shelf_scroll)
        self.refresh_shelf()

    def _on_escape(self):
        cur = self.stack.currentWidget()
        if cur is self.reader:
            self.close_reader()
        elif cur is self.source_view:
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
        self.close()

    def closeEvent(self, event):
        if self.reader.engine and self.reader.book:
            self.library.touch_history(self.reader.book, self.reader.page + 1)
        self.library.end_session(self.reader.page + 1)
        self.library.save()
        # 记忆窗口几何与筛选模式
        try:
            self.settings["geometry"] = bytes(
                self.saveGeometry().toBase64()).decode("ascii")
        except Exception:
            pass
        self.settings["filter_mode"] = self.filter_mode
        self.save_settings()
        self.reader.close()
        for svc in (getattr(self, "render", None),
                    getattr(self, "reader_render", None)):
            try:
                if svc is not None:
                    svc.shutdown()
            except Exception:
                pass
        super().closeEvent(event)


def main():
    install_crash_log()
    set_app_user_model_id()
    # 高 DPI：必须在 QApplication 创建之前声明
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    win = PyShelfApp()
    win.show()

    if "--smoke" in sys.argv:
        QTimer.singleShot(9000, win.close)
    if "--shot" in sys.argv or "--shotsrc" in sys.argv:
        # 自截窗口图像后退出（视觉回归用）
        if "--shotsrc" in sys.argv:
            QTimer.singleShot(1200, win.open_sources)

        def _shot():
            win.grab().save(str(APP_DIR / "_shot.png"))
            win.close()

        QTimer.singleShot(7000, _shot)

    rc = app.exec_()
    # 兜底：确保后台渲染任务在主循环退出后不再回投信号
    for svc in (getattr(win, "render", None),
                getattr(win, "reader_render", None)):
        try:
            if svc is not None:
                svc.shutdown()
        except Exception:
            pass
    sys.exit(rc)


if __name__ == "__main__":
    main()
