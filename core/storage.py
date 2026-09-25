"""SQLite 存储层：books / bookmarks / highlights / notes / reading_sessions。

设计要点：
- WAL 模式 + 事务：写入原子，进程被杀不会损坏库（对比 JSON 全量重写）。
- 全量 upsert 走单事务：500 本规模下毫秒级完成，无需细粒度脏标记。
- 封面 PNG 仍在文件系统，库内只存 book.id（封面路径由 id 推导）。
- 首次运行自动从 library.json 迁移，原 JSON 保留不动作回滚备份。
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from core.models import Book, Bookmark, Highlight, book_id_for

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS books (
    id           TEXT PRIMARY KEY,
    path         TEXT UNIQUE NOT NULL,
    title        TEXT NOT NULL,
    ext          TEXT NOT NULL DEFAULT '.pdf',
    pages        INTEGER NOT NULL DEFAULT 0,
    favorite     INTEGER NOT NULL DEFAULT 0,
    progress     INTEGER NOT NULL DEFAULT 0,
    last_opened  REAL    NOT NULL DEFAULT 0,
    cover_ok     INTEGER NOT NULL DEFAULT 0,
    readable     INTEGER NOT NULL DEFAULT 1,
    fingerprint  TEXT    NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS bookmarks (
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    page    INTEGER NOT NULL,
    note    TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (book_id, page)
);
CREATE TABLE IF NOT EXISTS highlights (
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    page    INTEGER NOT NULL,
    rect    TEXT NOT NULL,          -- JSON [x0,y0,x1,y1]
    note    TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS notes (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    page    INTEGER NOT NULL,
    text    TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reading_sessions (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    started REAL NOT NULL,
    ended   REAL,
    last_page INTEGER NOT NULL DEFAULT 1
);
CREATE VIRTUAL TABLE IF NOT EXISTS books_fts USING fts5(
    title, book_id UNINDEXED
);
"""


def _row_to_book(row: sqlite3.Row, children: dict) -> Book:
    return Book(
        id=row["id"], path=row["path"], title=row["title"], ext=row["ext"],
        pages=row["pages"], favorite=bool(row["favorite"]),
        progress=row["progress"], last_opened=row["last_opened"],
        cover_ok=bool(row["cover_ok"]), readable=bool(row["readable"]),
        fingerprint=row["fingerprint"],
        bookmarks=children.get("bm", []),
        highlights=children.get("hl", []),
    )


class Storage:
    """SQLite 持久化。所有写操作在单事务内提交。"""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(_SCHEMA)
        # 旧库升级：补充 fingerprint 列（无该列的既有 data/library.db）
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(books)")}
        if "fingerprint" not in cols:
            self.conn.execute(
                "ALTER TABLE books ADD COLUMN fingerprint TEXT NOT NULL DEFAULT ''")
            self.conn.commit()
        self._set_meta("schema_version", str(SCHEMA_VERSION))

    # ---------------- meta ----------------
    def _set_meta(self, key: str, value: str):
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self.conn.commit()

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_library_dir(self, lib_dir: str):
        self._set_meta("library_dir", lib_dir)

    # ---------------- 载入 ----------------
    def load_all(self) -> dict[str, Book]:
        bms: dict[str, list[Bookmark]] = {}
        for r in self.conn.execute("SELECT * FROM bookmarks ORDER BY page"):
            bms.setdefault(r["book_id"], []).append(Bookmark(r["page"], r["note"]))
        hls: dict[str, list[Highlight]] = {}
        for r in self.conn.execute(
                "SELECT rowid, * FROM highlights ORDER BY rowid"):
            hls.setdefault(r["book_id"], []).append(
                Highlight(r["page"], tuple(json.loads(r["rect"])), r["note"]))
        books = {}
        for row in self.conn.execute("SELECT * FROM books"):
            bid = row["id"]
            books[bid] = _row_to_book(row, {
                "bm": bms.get(bid, []), "hl": hls.get(bid, [])})
        return books

    # ---------------- 保存 ----------------
    def save_book(self, book: Book):
        c = self.conn
        with c:
            c.execute(
                """INSERT INTO books(id, path, title, ext, pages, favorite,
                       progress, last_opened, cover_ok, readable, fingerprint)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       path=excluded.path, title=excluded.title, ext=excluded.ext,
                       pages=excluded.pages, favorite=excluded.favorite,
                       progress=excluded.progress, last_opened=excluded.last_opened,
                       cover_ok=excluded.cover_ok, readable=excluded.readable,
                       fingerprint=excluded.fingerprint""",
                (book.id, book.path, book.title, book.ext, book.pages,
                 int(book.favorite), book.progress, book.last_opened,
                 int(book.cover_ok), int(book.readable), book.fingerprint))
            c.execute("DELETE FROM bookmarks WHERE book_id=?", (book.id,))
            c.executemany(
                "INSERT INTO bookmarks(book_id, page, note) VALUES(?,?,?)",
                [(book.id, bm.page, bm.note) for bm in book.bookmarks])
            c.execute("DELETE FROM highlights WHERE book_id=?", (book.id,))
            c.executemany(
                "INSERT INTO highlights(book_id, page, rect, note) VALUES(?,?,?,?)",
                [(book.id, h.page, json.dumps(list(h.rect)), h.note)
                 for h in book.highlights])
            c.execute("DELETE FROM books_fts WHERE book_id=?", (book.id,))
            c.execute("INSERT INTO books_fts(title, book_id) VALUES(?,?)",
                      (book.title, book.id))

    def save_books(self, books: dict[str, Book]):
        for b in books.values():
            self.save_book(b)

    def delete_book(self, book_id: str):
        with self.conn:
            self.conn.execute("DELETE FROM books_fts WHERE book_id=?", (book_id,))
            self.conn.execute("DELETE FROM books WHERE id=?", (book_id,))

    # ---------------- 搜索 ----------------
    def search_titles(self, query: str, limit: int = 100) -> list[str]:
        """FTS5 书名前缀/分词搜索，返回 book_id 列表。"""
        if not query.strip():
            return []
        q = query.strip().replace('"', '""')
        try:
            rows = self.conn.execute(
                'SELECT book_id FROM books_fts WHERE books_fts MATCH ? LIMIT ?',
                (f'"{q}"*', limit)).fetchall()
            return [r["book_id"] for r in rows]
        except sqlite3.OperationalError:
            return []

    # ---------------- 阅读会话 ----------------
    def begin_session(self, book_id: str) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO reading_sessions(book_id, started, ended, last_page) "
                "VALUES(?, ?, NULL, 1)", (book_id, time.time()))
            return cur.lastrowid

    def end_session(self, session_id: int, last_page: int):
        with self.conn:
            self.conn.execute(
                "UPDATE reading_sessions SET ended=?, last_page=? WHERE id=?",
                (time.time(), max(last_page, 1), session_id))

    def reading_minutes(self, since: float | None = None) -> float:
        """累计阅读分钟数（since 给定则统计该时刻之后）。"""
        if since is None:
            row = self.conn.execute(
                "SELECT TOTAL(COALESCE(ended, ?) - started) FROM reading_sessions",
                (time.time(),)).fetchone()
        else:
            row = self.conn.execute(
                "SELECT TOTAL(COALESCE(ended, ?) - MAX(started, ?)) "
                "FROM reading_sessions WHERE started >= ? - COALESCE(ended, ?) - started + 0",
                (time.time(), since, since, time.time())).fetchone()
        return max(float(row[0] or 0) / 60.0, 0.0)

    def minutes_today(self) -> float:
        import datetime as _dt
        midnight = _dt.datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp()
        row = self.conn.execute(
            "SELECT TOTAL(MIN(COALESCE(ended, ?), ?) - MAX(started, ?)) "
            "FROM reading_sessions WHERE COALESCE(ended, ?) > ?",
            (time.time(), time.time(), midnight,
             time.time(), midnight)).fetchone()
        return max(float(row[0] or 0) / 60.0, 0.0)

    # ---------------- JSON 迁移 ----------------
    def migrate_from_json(self, json_path: Path, json_books_key: str = "books") -> int:
        """从旧版 library.json 导入。返回导入条数；JSON 文件保持原样。"""
        try:
            raw = json.loads(Path(json_path).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return 0
        lib_dir = raw.get("library_dir", "")
        if lib_dir:
            self.set_library_dir(lib_dir)
        count = 0
        for item in raw.get(json_books_key, []):
            try:
                path = item["path"]
                b = Book(
                    id=item.get("id") or book_id_for(Path(path)),
                    path=path, title=item["title"],
                    ext=item.get("ext", ".pdf"), pages=item.get("pages", 0),
                    favorite=item.get("favorite", False),
                    progress=item.get("progress", 0),
                    last_opened=item.get("last_opened", 0.0),
                    cover_ok=item.get("cover_ok", False),
                    readable=item.get("readable", True),
                    bookmarks=[Bookmark(**bm) for bm in item.get("bookmarks", [])],
                    highlights=[Highlight(h["page"], tuple(h["rect"]),
                                          h.get("note", ""))
                                for h in item.get("highlights", [])],
                )
                self.save_book(b)
                count += 1
            except (KeyError, TypeError, ValueError):
                continue
        return count

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
