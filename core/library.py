"""数据模型与持久化：书库、收藏、书签、高亮、阅读历史。"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path


def book_id_for(path: Path) -> str:
    return hashlib.md5(str(path).encode("utf-8")).hexdigest()


@dataclass
class Bookmark:
    page: int
    note: str = ""


@dataclass
class Highlight:
    page: int
    rect: tuple  # (x0, y0, x1, y1) in PDF page coordinates (points)
    note: str = ""


@dataclass
class Book:
    id: str
    path: str
    title: str
    ext: str
    pages: int = 0
    favorite: bool = False
    progress: int = 0          # 已读到的页（1-based；0 = 未读）
    last_opened: float = 0.0
    bookmarks: list = field(default_factory=list)   # list[Bookmark]
    highlights: list = field(default_factory=list)  # list[Highlight]
    cover_ok: bool = False     # 封面缓存是否已生成
    readable: bool = True      # PyMuPDF 是否能打开


class Library:
    """library.json 读写 + 书籍条目管理。"""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.covers_dir = self.data_dir / "covers"
        self.json_path = self.data_dir / "library.json"
        self.books: dict[str, Book] = {}
        self.library_dir: str = ""
        self._load()

    # ---------- 持久化 ----------
    def _load(self):
        if not self.json_path.exists():
            return
        try:
            raw = json.loads(self.json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        self.library_dir = raw.get("library_dir", "")
        for item in raw.get("books", []):
            try:
                b = Book(
                    id=item["id"], path=item["path"], title=item["title"],
                    ext=item.get("ext", ".pdf"), pages=item.get("pages", 0),
                    favorite=item.get("favorite", False),
                    progress=item.get("progress", 0),
                    last_opened=item.get("last_opened", 0.0),
                    cover_ok=item.get("cover_ok", False),
                    readable=item.get("readable", True),
                    bookmarks=[Bookmark(**bm) for bm in item.get("bookmarks", [])],
                    highlights=[Highlight(h["page"], tuple(h["rect"]), h.get("note", ""))
                                for h in item.get("highlights", [])],
                )
                self.books[b.id] = b
            except (KeyError, TypeError, ValueError):
                continue

    def save(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.covers_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "library_dir": self.library_dir,
            "books": [
                {
                    "id": b.id, "path": b.path, "title": b.title, "ext": b.ext,
                    "pages": b.pages, "favorite": b.favorite, "progress": b.progress,
                    "last_opened": b.last_opened, "cover_ok": b.cover_ok,
                    "readable": b.readable,
                    "bookmarks": [{"page": bm.page, "note": bm.note} for bm in b.bookmarks],
                    "highlights": [{"page": h.page, "rect": list(h.rect), "note": h.note}
                                   for h in b.highlights],
                }
                for b in self.books.values()
            ],
        }
        tmp = self.json_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.json_path)

    # ---------- 条目管理 ----------
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

    # ---------- 查询 ----------
    def existing_paths(self) -> set[str]:
        return {b.path for b in self.books.values()}
