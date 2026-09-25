"""数据层门面：书库、收藏、书签、高亮、阅读历史、阅读会话。

v1.2 起底层由 library.json 迁移到 SQLite（core/storage.py）：
- Library 对外 API 保持不变，app.py 无感切换；
- 首次启动自动从旧 library.json 导入，原文件保留作回滚备份；
- save() 为单事务全量 upsert，SQLite 下毫秒级完成且原子安全。

数据类定义在 core/models.py（避免与 storage 循环导入），此处 re-export。
"""
from __future__ import annotations

import time
from pathlib import Path

from core.models import Book, Bookmark, Highlight, book_id_for   # noqa: F401
from core.storage import Storage


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
        self.library_dir: str = ""
        self._session_id: int | None = None

        self._load()

    # ---------------- 持久化 ----------------
    def _load(self):
        # 旧版 JSON 存在且库为空 → 一次性迁移
        if not self._db_has_books() and self.json_path.exists():
            self.store.migrate_from_json(self.json_path)
        self.library_dir = self.store.get_meta("library_dir", "")
        self.books = self.store.load_all()

    def _db_has_books(self) -> bool:
        return bool(self.store.conn.execute(
            "SELECT 1 FROM books LIMIT 1").fetchone())

    def save(self):
        self.store.save_books(self.books)

    def save_book(self, book: Book):
        self.store.save_book(book)

    def delete(self, book_id: str):
        self.books.pop(book_id, None)
        self.store.delete_book(book_id)

    # ---------------- 条目管理 ----------------
    def upsert(self, path: Path, title: str, ext: str, pages: int, readable: bool) -> Book:
        bid = book_id_for(path)
        book = self.books.get(bid)
        if book is None:
            book = Book(id=bid, path=str(path), title=title, ext=ext)
            self.books[bid] = book
        # 文件可能被替换过：刷新元数据
        book.title = title
        book.ext = ext
        if pages:
            book.pages = pages
        book.readable = readable
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
        return [self.books[i] for i in ids if i in self.books]

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
