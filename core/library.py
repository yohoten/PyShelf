"""数据层门面：书库、收藏、书签、高亮、阅读历史、阅读会话。

v1.2 起底层由 library.json 迁移到 SQLite（core/storage.py）：
- Library 对外 API 保持不变，app.py 无感切换；
- 首次启动自动从旧 library.json 导入，原文件保留作回滚备份；
- save() 为单事务全量 upsert，SQLite 下毫秒级完成且原子安全。

数据类定义在 core/models.py（避免与 storage 循环导入），此处 re-export。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from core.models import Book, Bookmark, Highlight, book_id_for   # noqa: F401
from core.storage import Storage, _norm_dir


class Library:
    """SQLite 读写 + 书籍条目管理。"""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.covers_dir = self.data_dir / "covers"
        self.covers_dir.mkdir(parents=True, exist_ok=True)
        self.json_path = self.data_dir / "library.json"   # 旧版文件，仅作迁移源
        self.db_path = self.data_dir / "library.db"

        self.store = Storage(self.db_path)
        self.books: dict[str, Book] = {}
        self.library_dirs: list[str] = []        # 规范化（扫描/匹配用）
        self.library_dirs_raw: list[str] = []    # 原始录入（UI 展示，保留大小写）
        self.library_dir: str = ""       # 兼容单值：= 首个目录
        self._pending_delete: dict[str, str] = {}   # 旧id -> 新id（重绑遗留）
        self._session_id: int | None = None

        self._load()

    # ---------------- 持久化 ----------------
    def _load(self):
        # 旧版 JSON 存在且库为空 → 一次性迁移
        if not self._db_has_books() and self.json_path.exists():
            self.store.migrate_from_json(self.json_path)
        try:
            self.store.normalize_paths()  # F-1：对齐历史 id/path + 回填指纹（幂等）
        except Exception:                 # D-2：病态旧库异常不阻断启动
            pass
        self.library_dirs = self.store.get_library_dirs()
        self.library_dirs_raw = self.store.get_library_dirs_raw()
        if len(self.library_dirs_raw) != len(self.library_dirs):
            self.library_dirs_raw = list(self.library_dirs)
        self.library_dir = self.library_dirs[0] if self.library_dirs else ""
        self.books = self.store.load_all()
        self._pending_delete = {}

    def _db_has_books(self) -> bool:
        return bool(self.store.conn.execute(
            "SELECT 1 FROM books LIMIT 1").fetchone())

    def save(self):
        """单事务原子提交：全量 upsert + 重绑辅助记忆迁移 + 清理旧行（F-2/F-6/F-7）。

        重绑遗留旧 id：只有「已不再出现在内存 books 中」的旧 id 才迁移记忆并删除，
        与 books.keys() 求差集，避免误删被新书复用的同 id 行。
        """
        rebinds, deletes = {}, []
        for old_id, new_id in self._pending_delete.items():
            if old_id in self.books:
                continue            # 旧 id 已被复用 → 不迁移也不删除
            rebinds[old_id] = new_id
            deletes.append(old_id)
        self.store.flush(self.books, rebinds, deletes)
        self._pending_delete = {}

    def save_book(self, book: Book):
        self.store.save_book(book)

    def delete(self, book_id: str):
        """从书库移除记录并落库（级联清 bookmarks/highlights/sessions/FTS）。"""
        self.books.pop(book_id, None)
        self.store.delete_book(book_id)
        # D-5：同步清理封面缓存，避免同名 id 复用时显示陈旧封面
        try:
            (self.covers_dir / f"{book_id}.png").unlink(missing_ok=True)
        except OSError:
            pass

    # ---------------- 多目录管理 ----------------
    def _sync_dirs(self):
        """按原始展示串重新规范化落库并回读，保持 raw 与 normalized 逐项对齐。"""
        self.store.set_library_dirs(self.library_dirs_raw or self.library_dirs)
        self.library_dirs = self.store.get_library_dirs()
        self.library_dirs_raw = self.store.get_library_dirs_raw()
        self.library_dir = self.library_dirs[0] if self.library_dirs else ""

    def add_dir(self, path: str) -> bool:
        """规范化加入目录；已存在返回 False。仅持久化，不触发扫描。

        原始串另存 `library_dirs_raw`，UI 展示保留录入大小写（F-11）。
        """
        nd = _norm_dir(path)
        if not nd or nd in self.library_dirs:
            return False
        self.library_dirs_raw.append(str(path))
        self._sync_dirs()
        return True

    def remove_dir(self, path: str) -> bool:
        """仅从配置移除目录，不删除任何书籍记录与阅读记忆。"""
        nd = _norm_dir(path)
        if nd not in self.library_dirs:
            return False
        self.library_dirs_raw = [r for r in self.library_dirs_raw
                                 if _norm_dir(r) != nd]
        self._sync_dirs()
        return True

    def count_books_under(self, path: str) -> int:
        """该目录下当前在架(present)书籍数，供 UI 展示。"""
        pre = _norm_dir(path) + os.sep
        return sum(1 for b in self.books.values()
                   if b.present and _norm_dir(b.path).startswith(pre))

    # ---------------- 扫描对账 / 记忆重绑 ----------------
    def reconcile_scan(self, infos: list[tuple]) -> dict:
        """把一次扫描结果并入书库：常规刷新 / 指纹重绑 / 新增 / 标记失联。

        不变式：**永不对外 DELETE 失联记录**，只置 present=False；重绑时把旧
        book_id 的记忆整体搬到新 id（save() 时迁移辅助表并清理遗留旧行）。
        路径一律按 `_norm_dir` 规范化比较，杜绝大小写/写法差异造成的误失联。
        返回 {"present","missing","added","rebound"}。
        """
        scanned = {_norm_dir(info[0]) for info in infos}
        claimed: set[str] = set()
        added = rebound = 0
        for path_s, title, ext, pages, readable, fp in infos:
            np = _norm_dir(path_s)
            bid = book_id_for(Path(np))
            book = self.books.get(bid)
            if book is not None:
                # 现役：常规刷新元数据与指纹（路径归一化对齐，避免存储写法漂移）
                book.path = np
                if not book.title_custom:     # D-6：用户自定义标题不被文件名覆盖
                    book.title = title
                book.ext = ext
                if pages:
                    book.pages = pages
                book.readable = readable
                book.fingerprint = fp
                book.present = True
                continue
            old_id = self._find_rebind(fp, title, pages, scanned, claimed)
            if old_id:
                old = self.books.pop(old_id)      # 记忆整体搬到新 id
                old.id = bid
                old.path = np
                if not old.title_custom:          # D-6：自定义标题随记忆保留
                    old.title = title
                old.ext = ext
                if pages:
                    old.pages = pages
                old.readable = readable
                old.fingerprint = fp
                old.present = True
                self.books[bid] = old
                claimed.add(old_id)
                self._pending_delete[old_id] = bid   # 供 save() 迁移记忆并清理
                rebound += 1
            else:
                self.books[bid] = Book(
                    id=bid, path=np, title=title, ext=ext, pages=pages,
                    readable=readable, fingerprint=fp, present=True)
                added += 1
        # 失联标记：本次扫描未命中的记录隐藏但保留（含其记忆）
        missing = 0
        for b in self.books.values():
            if b.id in claimed:
                continue
            if _norm_dir(b.path) in scanned:
                continue
            b.present = False
            missing += 1
        present = sum(1 for b in self.books.values() if b.present)
        return {"present": present, "missing": missing,
                "added": added, "rebound": rebound}

    def _find_rebind(self, fp: str, title: str, pages: int, scanned: set,
                     claimed: set) -> str | None:
        """在「失联且未被占用」的候选中查找应重绑的旧 book_id。

        L1：fingerprint（mtime_ns:size）完全相等（文件移动/改路径）；
        L2：size 相等 且 clean_title 相等 且 pages 一致（文件被复制/触碰）；
        歧义：last_opened 大者优先 → path 字典序，保证确定性；无匹配返回 None。
        """
        def orphan(b: Book) -> bool:
            return b.id not in claimed and _norm_dir(b.path) not in scanned

        cands = [b for b in self.books.values()
                 if orphan(b) and fp and b.fingerprint == fp]
        if not cands and fp and ":" in fp:
            size = fp.split(":")[-1]
            cands = [b for b in self.books.values()
                     if orphan(b) and b.title == title and b.fingerprint
                     and b.fingerprint.split(":")[-1] == size
                     and (b.pages == 0 or pages == 0 or b.pages == pages)]
        if not cands:
            return None
        # 歧义消解：最近打开优先 → 路径稳定排序（确定性）
        cands.sort(key=lambda b: (-b.last_opened, b.path))
        return cands[0].id

    # ---------------- 条目管理 ----------------
    def upsert(self, path: Path, title: str, ext: str, pages: int, readable: bool,
               fingerprint: str = "") -> Book:
        bid = book_id_for(path)
        book = self.books.get(bid)
        if book is None:
            book = Book(id=bid, path=str(path), title=title, ext=ext)
            self.books[bid] = book
        # 文件可能被替换过：刷新元数据（D-6：自定义标题不被文件名覆盖）
        if not book.title_custom:
            book.title = title
        book.ext = ext
        if pages:
            book.pages = pages
        book.readable = readable
        book.fingerprint = fingerprint
        book.present = True
        return book

    def touch_history(self, book: Book, page: int):
        book.progress = max(page, 1)
        book.last_opened = time.time()

    def toggle_favorite(self, book: Book):
        book.favorite = not book.favorite

    def add_bookmark(self, book: Book, page: int, note: str = ""):
        if not any(bm.page == page for bm in book.bookmarks):
            book.bookmarks.append(Bookmark(page, note))

    def remove_bookmark(self, book: Book, index: int):
        if 0 <= index < len(book.bookmarks):
            book.bookmarks.pop(index)

    def add_highlight(self, book: Book, page: int, rect: tuple, note: str = ""):
        book.highlights.append(Highlight(page, rect, note))

    def remove_highlight(self, book: Book, index: int):
        if 0 <= index < len(book.highlights):
            book.highlights.pop(index)

    # ---------------- 查询 ----------------
    def existing_paths(self) -> set[str]:
        return {b.path for b in self.books.values()}

    def search_titles(self, query: str) -> list[Book]:
        """FTS5 书名搜索（中文按前缀匹配），返回按相关度排序的 Book 列表。"""
        ids = self.store.search_titles(query)
        return [self.books[i] for i in ids
                if i in self.books and self.books[i].present]

    # ---------------- 阅读会话（时长统计） ----------------
    def begin_session(self, book: Book):
        if book:
            self._session_id = self.store.begin_session(book.id)

    def end_session(self, last_page: int = 1):
        if self._session_id is not None:
            self.store.end_session(self._session_id, last_page)
            self._session_id = None

    def minutes_today(self) -> float:
        return self.store.minutes_today()

    def hours_total(self) -> float:
        return self.store.reading_minutes() / 60.0

    def close(self):
        self.store.close()
