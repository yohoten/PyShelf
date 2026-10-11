"""SQLite 存储层：books / bookmarks / highlights / notes / reading_sessions。

设计要点：
- WAL 模式 + 事务：写入原子，进程被杀不会损坏库（对比 JSON 全量重写）。
- 全量 upsert 走单事务：500 本规模下毫秒级完成，无需细粒度脏标记。
- 封面 PNG 仍在文件系统，库内只存 book.id（封面路径由 id 推导）。
- 首次运行自动从 library.json 迁移，原 JSON 保留不动作回滚备份。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path

from core.models import Book, Bookmark, Highlight, book_id_for, _norm_dir

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 3

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
    fingerprint  TEXT    NOT NULL DEFAULT '',
    present      INTEGER NOT NULL DEFAULT 1,
    title_custom INTEGER NOT NULL DEFAULT 0
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
        present=bool(row["present"]),
        title_custom=bool(row["title_custom"]),
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
        # 旧库升级：纯新增补列（fingerprint / present / title_custom），零丢失
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(books)")}
        if "fingerprint" not in cols:
            self.conn.execute(
                "ALTER TABLE books ADD COLUMN fingerprint TEXT NOT NULL DEFAULT ''")
        if "present" not in cols:
            self.conn.execute(
                "ALTER TABLE books ADD COLUMN present INTEGER NOT NULL DEFAULT 1")
        if "title_custom" not in cols:
            # D-6：旧库默认 0（未自定义）→ 向后兼容，重扫仍按文件名刷新标题
            self.conn.execute(
                "ALTER TABLE books ADD COLUMN title_custom INTEGER NOT NULL DEFAULT 0")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_books_fingerprint ON books(fingerprint)")
        self.conn.commit()
        self._migrate_dirs()          # v1 -> v2：library_dir -> library_dirs
        try:
            self.normalize_paths()    # F-1：对齐历史 id/path + 回填指纹（幂等）
        except Exception:             # D-2：病态旧库异常不阻断启动
            logger.exception("normalize_paths 失败：跳过历史对齐（不阻断启动）")
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
        """兼容旧接口：写单值 library_dir。"""
        self._set_meta("library_dir", lib_dir)

    def get_library_dirs(self) -> list[str]:
        """返回规范化后的书籍目录列表（去重、保序）。

        优先读 library_dirs(JSON 数组)；缺失/为空则回退 [library_dir]；再空返回 []。
        """
        raw = self.get_meta("library_dirs", "")
        dirs: list[str] = []
        if raw:
            try:
                v = json.loads(raw)
                if isinstance(v, list):
                    dirs = [str(x) for x in v
                            if isinstance(x, (str, bytes)) and str(x).strip()]
            except json.JSONDecodeError:
                dirs = []
        if not dirs:
            legacy = self.get_meta("library_dir", "")
            if legacy:
                dirs = [legacy]
        out, seen = [], set()
        for d in dirs:
            nd = _norm_dir(d)
            if nd and nd not in seen:
                seen.add(nd)
                out.append(nd)
        return out

    def set_library_dirs(self, dirs: list[str]):
        """持久化目录列表；顺带把首项写回 library_dir 兼容旧代码。

        同时保存原始录入串 `library_dirs_raw`（保留大小写，供 UI 展示，F-11），
        规范化值 `library_dirs` 仅用于扫描/匹配。
        """
        out, seen, raw_out = [], set(), []
        for d in dirs:
            nd = _norm_dir(d)
            if nd and nd not in seen:
                seen.add(nd)
                out.append(nd)
                raw_out.append(str(d))
        self._set_meta("library_dirs", json.dumps(out, ensure_ascii=False))
        self._set_meta("library_dirs_raw", json.dumps(raw_out, ensure_ascii=False))
        self._set_meta("library_dir", out[0] if out else "")

    def get_library_dirs_raw(self) -> list[str]:
        """与 get_library_dirs() 逐项对齐的原始展示串（保留录入大小写）。"""
        norm = self.get_library_dirs()
        raw = self.get_meta("library_dirs_raw", "")
        display: list[str] = []
        if raw:
            try:
                v = json.loads(raw)
                if isinstance(v, list):
                    display = [str(x) for x in v if isinstance(x, (str, bytes))]
            except json.JSONDecodeError:
                display = []
        if len(display) != len(norm):
            return list(norm)          # 长度不齐（旧库/损坏）→ 回退规范值
        return display

    def _migrate_dirs(self):
        """v1 -> v2：把旧的单值 library_dir 升为 library_dirs 数组。幂等。"""
        if self.get_meta("library_dirs", ""):
            return
        legacy = self.get_meta("library_dir", "")
        if legacy:
            self.set_library_dirs([legacy])

    # ---------------- 历史库记忆对齐（F-1，幂等 + 事务） ----------------
    @staticmethod
    def _fingerprint_for(path: str) -> str:
        """回填指纹 mtime_ns:size；文件不可达 → ""（跳过，下次启动再试）。"""
        try:
            st = os.stat(path)
            return f"{st.st_mtime_ns}:{st.st_size}"
        except OSError:
            return ""

    def _reassign_children(self, old_id: str, new_id: str):
        """把旧 book_id 的**全部**子表外键改到新 id（bookmarks 同页冲突去重）。"""
        if old_id == new_id:
            return
        c = self.conn
        c.execute("UPDATE OR IGNORE bookmarks SET book_id=? WHERE book_id=?",
                  (new_id, old_id))
        c.execute("DELETE FROM bookmarks WHERE book_id=?", (old_id,))
        c.execute("UPDATE highlights SET book_id=? WHERE book_id=?",
                  (new_id, old_id))
        c.execute("UPDATE notes SET book_id=? WHERE book_id=?", (new_id, old_id))
        c.execute("UPDATE reading_sessions SET book_id=? WHERE book_id=?",
                  (new_id, old_id))
        c.execute("DELETE FROM books_fts WHERE book_id=?", (old_id,))

    def normalize_paths(self):
        """F-1：把历史 books 的 id/path 对齐到规范形式并回填 fingerprint，
        同步迁移其 bookmarks/highlights/notes/reading_sessions 外键，避免 id 漂移
        使既有记忆被 present=0 永久隐藏。幂等 + 单事务 + 向后兼容（v1 旧库）。

        D-2：病态旧库触发异常时仅记录日志并返回，绝不阻断启动；`with self.conn`
        失败自动回滚，即便中断也不留半保存状态（事务一致性保持）。
        """
        try:
            self._normalize_paths_impl()
        except Exception:
            logger.exception("normalize_paths 失败：跳过历史对齐（不阻断启动）")

    def _normalize_paths_impl(self):
        rows = list(self.conn.execute(
            "SELECT id, path, fingerprint FROM books"))
        plans = []
        for r in rows:
            old_id, path, fp = r["id"], r["path"], r["fingerprint"]
            if not path:
                continue
            np = _norm_dir(path)
            new_id = book_id_for(Path(np))
            new_fp = fp or self._fingerprint_for(path)
            if new_id == old_id and np == path and new_fp == fp:
                continue
            plans.append((old_id, new_id, np, new_fp))
        if not plans:
            return
        # 迁移会改主键（PK UPDATE 等价 DELETE+INSERT），暂关外键级联，
        # 避免 CASCADE 误删子表；子表外键已显式迁移，故关闭期间无孤儿风险。
        self.conn.execute("PRAGMA foreign_keys=OFF")
        try:
            with self.conn:
                for old_id, new_id, np, new_fp in plans:
                    if new_id != old_id:
                        # 仅当目标 id 是**另一行**时才视为冲突需合并
                        exists = self.conn.execute(
                            "SELECT 1 FROM books WHERE id=?", (new_id,)).fetchone()
                        self._reassign_children(old_id, new_id)
                        if exists:                   # 目标 id 已被占用：合并后删旧行
                            self.conn.execute(
                                "DELETE FROM books WHERE id=?", (old_id,))
                            continue
                    # 仅路径/指纹需对齐（id 不变），或 id 变更且目标空：直接更新
                    self.conn.execute(
                        "UPDATE books SET id=?, path=?, fingerprint=? "
                        "WHERE id=?", (new_id, np, new_fp, old_id))
                # D-1：_reassign_children 已删旧 id 的 FTS 行，这里按新 id 重建，
                # 避免「迁移完成 ~ 首次 save」之间书名搜索缺行。删除后重插 → 幂等。
                for nid in {p[1] for p in plans}:
                    row = self.conn.execute(
                        "SELECT title FROM books WHERE id=?", (nid,)).fetchone()
                    self.conn.execute(
                        "DELETE FROM books_fts WHERE book_id=?", (nid,))
                    if row and row["title"]:
                        self.conn.execute(
                            "INSERT INTO books_fts(title, book_id) VALUES(?,?)",
                            (row["title"], nid))
        finally:
            self.conn.execute("PRAGMA foreign_keys=ON")

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
    def _save_book_nolock(self, book: Book):
        """单本 upsert（不含事务封装，供 flush 复用）。"""
        c = self.conn
        c.execute(
            """INSERT INTO books(id, path, title, ext, pages, favorite,
                   progress, last_opened, cover_ok, readable, fingerprint,
                   present, title_custom)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   path=excluded.path, title=excluded.title, ext=excluded.ext,
                   pages=excluded.pages, favorite=excluded.favorite,
                   progress=excluded.progress, last_opened=excluded.last_opened,
                   cover_ok=excluded.cover_ok, readable=excluded.readable,
                   fingerprint=excluded.fingerprint, present=excluded.present,
                   title_custom=excluded.title_custom""",
            (book.id, book.path, book.title, book.ext, book.pages,
             int(book.favorite), book.progress, book.last_opened,
             int(book.cover_ok), int(book.readable), book.fingerprint,
             int(book.present), int(book.title_custom)))
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

    def _delete_book_nolock(self, book_id: str):
        self.conn.execute("DELETE FROM books_fts WHERE book_id=?", (book_id,))
        self.conn.execute("DELETE FROM books WHERE id=?", (book_id,))

    def _reassign_aux_nolock(self, old_id: str, new_id: str):
        """重绑专用：迁移 save_book 不重写、却以 book_id 外键存储的辅助记忆表。
        reading_sessions / notes 若不迁移，删除旧行的 ON DELETE CASCADE 会连
        阅读时长等记忆一并清掉（F-2）。"""
        if old_id == new_id:
            return
        self.conn.execute(
            "UPDATE reading_sessions SET book_id=? WHERE book_id=?",
            (new_id, old_id))
        self.conn.execute(
            "UPDATE notes SET book_id=? WHERE book_id=?", (new_id, old_id))

    def save_book(self, book: Book):
        with self.conn:
            self._save_book_nolock(book)

    def save_books(self, books: dict[str, Book]):
        """全量 upsert 单事务原子提交（F-6）：任一本失败则整体回滚。"""
        with self.conn:
            for b in books.values():
                self._save_book_nolock(b)

    def flush(self, books: dict[str, Book], rebinds: dict | None = None,
              deletes: list | None = None):
        """单事务原子提交：全量 upsert + 重绑辅助记忆迁移(F-2) + 清理旧行(F-7)。

        顺序保证外键有效：先写新 id 的 books 行，再把 reading_sessions/notes
        迁到新 id，最后删除旧行（CASCADE 只清已迁移走的残留）。任何一步失败整体
        回滚，杜绝半保存不一致（F-6）。
        """
        rebinds = rebinds or {}
        deletes = deletes or []
        with self.conn:
            for b in books.values():
                self._save_book_nolock(b)
            for old_id, new_id in rebinds.items():
                self._reassign_aux_nolock(old_id, new_id)
            for oid in deletes:
                self._delete_book_nolock(oid)

    def delete_book(self, book_id: str):
        with self.conn:
            self._delete_book_nolock(book_id)

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
        """累计阅读分钟数；since 给定则仅统计与 [since, now] 的重叠区间。

        D-3：原 since 分支的 WHERE 量纲可疑（把时刻当长度参与运算），修正为与
        minutes_today 一致的「区间裁剪」：每段会话取 MAX(started, since) 与
        MIN(ended, now) 的重叠，投影到 since 之前的会话不计入。
        """
        now = time.time()
        if since is None:
            row = self.conn.execute(
                "SELECT TOTAL(COALESCE(ended, ?) - started) FROM reading_sessions",
                (now,)).fetchone()
        else:
            row = self.conn.execute(
                "SELECT TOTAL(MIN(COALESCE(ended, ?), ?) - MAX(started, ?)) "
                "FROM reading_sessions WHERE COALESCE(ended, ?) > ?",
                (now, now, since, now, since)).fetchone()
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
                # 同页重复书签去重，避免 (book_id,page) 主键冲突整条失败
                seen_pages, bookmarks = set(), []
                for bm in item.get("bookmarks", []):
                    pg = bm.get("page")
                    if pg in seen_pages:
                        continue
                    seen_pages.add(pg)
                    bookmarks.append(Bookmark(pg, bm.get("note", "")))
                b = Book(
                    id=item.get("id") or book_id_for(Path(path)),
                    path=path, title=item["title"],
                    ext=item.get("ext", ".pdf"), pages=item.get("pages", 0),
                    favorite=item.get("favorite", False),
                    progress=item.get("progress", 0),
                    last_opened=item.get("last_opened", 0.0),
                    cover_ok=item.get("cover_ok", False),
                    readable=item.get("readable", True),
                    fingerprint=item.get("fingerprint", ""),
                    present=item.get("present", True),
                    title_custom=item.get("title_custom", False),
                    bookmarks=bookmarks,
                    highlights=[Highlight(h["page"], tuple(h["rect"]),
                                          h.get("note", ""))
                                for h in item.get("highlights", [])],
                )
                self.save_book(b)
                count += 1
            except (KeyError, TypeError, ValueError, sqlite3.IntegrityError):
                continue        # 单条失败不阻断整体迁移（F-10）
        return count

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
