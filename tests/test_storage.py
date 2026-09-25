"""SQLite 存储层测试：CRUD、JSON 迁移、会话统计、FTS 搜索。"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.library import Book, Library, book_id_for   # noqa: E402
from core.storage import Storage   # noqa: E402


def _mk_book(tmp_path, title="测试书", fav=False) -> Book:
    tmp_path = Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / f"{title}.pdf"
    p.write_bytes(b"fake")
    return Book(id=book_id_for(p), path=str(p), title=title, ext=".pdf",
                pages=100, favorite=fav, progress=42)


def test_roundtrip(tmp_path):
    st = Storage(tmp_path / "t.db")
    b = _mk_book(tmp_path)
    from core.library import Bookmark, Highlight
    b.bookmarks = [Bookmark(10, "笔记"), Bookmark(20)]
    b.highlights = [Highlight(5, (1.0, 2.0, 3.0, 4.0), "重点")]
    st.save_book(b)
    books = st.load_all()
    assert len(books) == 1
    got = books[b.id]
    assert got.title == "测试书" and got.progress == 42
    assert [bm.page for bm in got.bookmarks] == [10, 20]
    assert got.highlights[0].rect == (1.0, 2.0, 3.0, 4.0)


def test_update_and_delete(tmp_path):
    st = Storage(tmp_path / "t.db")
    b = _mk_book(tmp_path)
    st.save_book(b)
    b.favorite = True
    b.title = "改名后"
    st.save_book(b)
    got = st.load_all()[b.id]
    assert got.favorite and got.title == "改名后"
    st.delete_book(b.id)
    assert st.load_all() == {}


def test_json_migration(tmp_path):
    # 构造旧版 library.json
    p = tmp_path / "旧书.pdf"
    lib_json = {
        "library_dir": str(tmp_path),
        "books": [{
            "id": book_id_for(p), "path": str(p), "title": "旧书",
            "ext": ".pdf", "pages": 12, "favorite": True, "progress": 3,
            "last_opened": 1000.0, "cover_ok": True, "readable": True,
            "bookmarks": [{"page": 2, "note": "n"}],
            "highlights": [{"page": 1, "rect": [0, 0, 1, 1], "note": ""}],
        }],
    }
    json_path = tmp_path / "library.json"
    json_path.write_text(json.dumps(lib_json, ensure_ascii=False), encoding="utf-8")

    lib = Library(tmp_path)
    assert lib.library_dir == str(tmp_path)
    assert len(lib.books) == 1
    b = next(iter(lib.books.values()))
    assert b.title == "旧书" and b.favorite and b.progress == 3
    assert b.bookmarks[0].page == 2 and b.highlights[0].rect == (0, 0, 1, 1)
    # 原 JSON 保留（回滚备份）
    assert json_path.exists()


def test_library_uses_db_not_json(tmp_path):
    # 第二次打开：直接从 SQLite 读，即使 JSON 已删除
    b = _mk_book(tmp_path, title="持久书")
    lib = Library(tmp_path)
    lib.books[b.id] = b
    lib.save()
    (tmp_path / "library.json").unlink(missing_ok=True)
    lib2 = Library(tmp_path)
    assert b.id in lib2.books and lib2.books[b.id].title == "持久书"


def test_sessions_and_stats(tmp_path):
    st = Storage(tmp_path / "t.db")
    b = _mk_book(tmp_path)
    st.save_book(b)
    sid = st.begin_session(b.id)
    time.sleep(0.05)
    st.end_session(sid, 10)
    assert st.reading_minutes() > 0
    assert st.minutes_today() > 0


def test_fts_search(tmp_path):
    st = Storage(tmp_path / "t.db")
    for t in ("Python 编程", "机器学习入门", "Python 数据分析"):
        st.save_book(_mk_book(tmp_path / t, title=t))
    hits = st.search_titles("Python")
    assert len(hits) == 2
    assert st.search_titles("") == []
    # 特殊字符不炸
    st.search_titles('"奇奇怪怪的引号')


def test_fingerprint_roundtrip(tmp_path):
    """扫描增量化依赖的指纹字段可持久化。"""
    st = Storage(tmp_path / "t.db")
    b = _mk_book(tmp_path)
    b.fingerprint = "123456789:987654"
    st.save_book(b)
    got = st.load_all()[b.id]
    assert got.fingerprint == "123456789:987654"


def test_old_db_upgrade_adds_fingerprint(tmp_path):
    """旧版 library.db（无 fingerprint 列）打开时自动 ALTER 升级。"""
    import sqlite3
    db = tmp_path / "old.db"
    c = sqlite3.connect(str(db))
    c.executescript("""
        CREATE TABLE books (
            id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
            ext TEXT NOT NULL DEFAULT '.pdf', pages INTEGER NOT NULL DEFAULT 0,
            favorite INTEGER NOT NULL DEFAULT 0, progress INTEGER NOT NULL DEFAULT 0,
            last_opened REAL NOT NULL DEFAULT 0, cover_ok INTEGER NOT NULL DEFAULT 0,
            readable INTEGER NOT NULL DEFAULT 1);
    """)
    c.execute("INSERT INTO books(id, path, title) VALUES('x', 'p', '旧记录')")
    c.commit()
    c.close()
    st = Storage(db)   # 应自动补列而不报错
    books = st.load_all()
    assert "x" in books and books["x"].fingerprint == ""
