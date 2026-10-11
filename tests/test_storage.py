"""SQLite 存储层测试：CRUD、JSON 迁移、会话统计、FTS 搜索。"""
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.library import Book, Bookmark, Library, book_id_for   # noqa: E402
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
    # library_dir 现为规范化值（Windows normcase），大小写不敏感比较
    assert os.path.normcase(lib.library_dir) == os.path.normcase(str(tmp_path))
    assert len(lib.books) == 1
    b = next(iter(lib.books.values()))
    assert b.title == "旧书" and b.favorite and b.progress == 3
    assert b.bookmarks[0].page == 2 and b.highlights[0].rect == (0, 0, 1, 1)
    # 原 JSON 保留（回滚备份）
    assert json_path.exists()


def test_json_migration_tolerates_bad_items(tmp_path):
    """F-10：同页重复书签去重、缺字段条目跳过，单条失败不阻断整体迁移。"""
    p = tmp_path / "a.pdf"
    p.write_bytes(b"x")
    lib_json = {
        "library_dir": str(tmp_path),
        "books": [
            {"path": str(p), "title": "A", "ext": ".pdf", "pages": 3,
             "bookmarks": [{"page": 1}, {"page": 1, "note": "重复页"},
                           {"page": 2}],
             "highlights": []},
            {"title": "缺 path 字段"},               # KeyError → 跳过
        ],
    }
    jp = tmp_path / "library.json"
    jp.write_text(json.dumps(lib_json, ensure_ascii=False), encoding="utf-8")

    lib = Library(tmp_path)
    assert len(lib.books) == 1                       # 坏条目未阻断迁移
    b = next(iter(lib.books.values()))
    assert [bm.page for bm in b.bookmarks] == [1, 2]  # 同页去重后保留


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
    # F-1 迁移会把历史 id 对齐到规范形式（旧 'x' 不再保留），但记录与列不丢
    assert len(books) == 1
    assert next(iter(books.values())).fingerprint == ""   # 路径不可达 → 跳过回填


def test_library_dirs_roundtrip(tmp_path):
    """多目录持久化：get/set_library_dirs 往返一致，library_dir 兼容取首个。"""
    st = Storage(tmp_path / "t.db")
    a, b = str(tmp_path / "A"), str(tmp_path / "B")
    st.set_library_dirs([a, b])
    got = st.get_library_dirs()
    assert len(got) == 2
    assert got == [os.path.normcase(os.path.normpath(os.path.abspath(a))),
                   os.path.normcase(os.path.normpath(os.path.abspath(b)))]
    assert st.get_meta("library_dir") == got[0]


def test_library_dirs_legacy_fallback(tmp_path):
    """旧库仅有 library_dir 键：回退读回单元素，且 __init__ 迁移写入 library_dirs。"""
    db = tmp_path / "t.db"
    st = Storage(db)
    st.set_library_dir(str(tmp_path / "Legacy"))
    st.close()

    st2 = Storage(db)                       # __init__ 触发 _migrate_dirs
    dirs = st2.get_library_dirs()
    assert len(dirs) == 1
    assert st2.get_meta("library_dirs")     # 已迁移写入 JSON 键


def test_dirs_dedupe_normcase(tmp_path):
    """同一目录的不同大小写/尾分隔符写法去重为一条。"""
    st = Storage(tmp_path / "t.db")
    st.set_library_dirs([r"C:\A", "c:\\a\\"])
    assert len(st.get_library_dirs()) == 1


def test_present_column_upgrade(tmp_path):
    """旧库（无 present 列）打开时自动补列，默认 1（在架）。"""
    import sqlite3
    db = tmp_path / "old.db"
    c = sqlite3.connect(str(db))
    c.executescript("""
        CREATE TABLE books (
            id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
            ext TEXT NOT NULL DEFAULT '.pdf', pages INTEGER NOT NULL DEFAULT 0,
            favorite INTEGER NOT NULL DEFAULT 0, progress INTEGER NOT NULL DEFAULT 0,
            last_opened REAL NOT NULL DEFAULT 0, cover_ok INTEGER NOT NULL DEFAULT 0,
            readable INTEGER NOT NULL DEFAULT 1,
            fingerprint TEXT NOT NULL DEFAULT '');
    """)
    c.execute("INSERT INTO books(id, path, title) VALUES('x', 'p', '旧记录')")
    c.commit()
    c.close()
    st = Storage(db)
    books = st.load_all()
    assert len(books) == 1
    assert next(iter(books.values())).present is True


def test_present_roundtrip(tmp_path):
    """present 字段可持久化（失联书写 0 后读回 False）。"""
    st = Storage(tmp_path / "t.db")
    b = _mk_book(tmp_path)
    b.present = False
    st.save_book(b)
    assert st.load_all()[b.id].present is False


def test_save_books_single_transaction_rollback(tmp_path):
    """F-6：save_books 单事务原子提交——任一本失败则整体回滚，无半保存。"""
    st = Storage(tmp_path / "t.db")
    good = _mk_book(tmp_path, title="good")
    bad = _mk_book(tmp_path, title="bad")
    bad.bookmarks = [Bookmark(1), Bookmark(1)]     # 同页重复 → (book_id,page) 冲突
    try:
        st.save_books({good.id: good, bad.id: bad})
    except Exception:
        pass
    assert st.load_all() == {}                       # 先写的 good 也被回滚


def test_title_custom_roundtrip(tmp_path):
    """D-6：title_custom 标志可持久化（自定义标题不被误判）。"""
    st = Storage(tmp_path / "t.db")
    b = _mk_book(tmp_path)
    b.title = "自定义标题"
    b.title_custom = True
    st.save_book(b)
    got = st.load_all()[b.id]
    assert got.title == "自定义标题" and got.title_custom is True


def test_old_db_upgrade_adds_title_custom(tmp_path):
    """D-6：旧库（无 title_custom 列）自动补列，默认 0（未自定义），零丢失。"""
    db = tmp_path / "old.db"
    c = sqlite3.connect(str(db))
    c.executescript("""
        CREATE TABLE books (
            id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
            ext TEXT NOT NULL DEFAULT '.pdf', pages INTEGER NOT NULL DEFAULT 0,
            favorite INTEGER NOT NULL DEFAULT 0, progress INTEGER NOT NULL DEFAULT 0,
            last_opened REAL NOT NULL DEFAULT 0, cover_ok INTEGER NOT NULL DEFAULT 0,
            readable INTEGER NOT NULL DEFAULT 1,
            fingerprint TEXT NOT NULL DEFAULT '',
            present INTEGER NOT NULL DEFAULT 1);
    """)
    c.execute("INSERT INTO books(id, path, title) VALUES('x', 'p', '旧记录')")
    c.commit()
    c.close()
    st = Storage(db)                     # 触发 ALTER 补列
    cols = {r[1] for r in st.conn.execute("PRAGMA table_info(books)")}
    assert "title_custom" in cols
    assert next(iter(st.load_all().values())).title_custom is False


def test_normalize_paths_failure_does_not_block_startup(tmp_path, monkeypatch):
    """D-2：normalize_paths 内部异常被吞掉，不阻断 Storage/Library 启动。"""
    from core import storage as storage_mod

    def boom(self):
        raise sqlite3.OperationalError("模拟病态旧库")

    monkeypatch.setattr(storage_mod.Storage, "_normalize_paths_impl", boom)
    st = Storage(tmp_path / "bad.db")    # 不应抛出
    assert st.load_all() == {}
    st.close()


def test_reading_minutes_since_window(tmp_path):
    """D-3：reading_minutes(since) 仅统计与 [since, now] 的重叠区间。"""
    st = Storage(tmp_path / "t.db")
    b = _mk_book(tmp_path)
    st.save_book(b)
    now = time.time()
    with st.conn:
        st.conn.execute(                 # 早于 since 的会话（应被排除）
            "INSERT INTO reading_sessions(book_id, started, ended, last_page) "
            "VALUES(?,?,?,?)", (b.id, now - 1000, now - 900, 1))
        st.conn.execute(                 # 完全落在 since 之后的会话
            "INSERT INTO reading_sessions(book_id, started, ended, last_page) "
            "VALUES(?,?,?,?)", (b.id, now - 50, now - 10, 1))
    mins = st.reading_minutes(now - 200)
    assert abs(mins - 40 / 60.0) < 0.03          # 仅约 40 秒
    assert st.reading_minutes() > 2.0            # 全量含两段（约 140s）
