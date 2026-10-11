"""多书籍目录 + 记忆重绑测试。

覆盖：多目录汇总、失联保留、内容指纹去重、移除目录不丢记忆、
指纹重绑（L1 完全匹配 / L2 size+title 降级）、歧义消解。
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import scanner                                  # noqa: E402
from core.library import Book, Bookmark, Highlight, Library, book_id_for  # noqa: E402
from core.storage import Storage, _norm_dir               # noqa: E402

FIXED_NS = 1_600_000_000_000_000_000


def _mk_file(path: Path, data: bytes = b"hello", ns: int = FIXED_NS) -> Path:
    """建文件并固定 mtime/atime，保证 fingerprint(mtime_ns:size) 可复现。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, ns=(ns, ns))
    return path


def _info(path: Path, pages: int = 0, readable: bool = True) -> tuple:
    st = path.stat()
    fp = f"{st.st_mtime_ns}:{st.st_size}"
    return (str(path), scanner.clean_title(path.name),
            path.suffix.lower(), pages, readable, fp)


def _infos(paths, pages: int = 0) -> list[tuple]:
    return [_info(p, pages=pages) for p in paths]


def test_reconcile_adds_and_marks_missing(tmp_path):
    """扫描 A 建书；再扫描 B → A 失联(present=False,未删)、B 新增。"""
    a, b = tmp_path / "A", tmp_path / "B"
    pa = _mk_file(a / "甲.pdf")
    pb = _mk_file(b / "乙.pdf", data=b"worldly")   # 与 pa 内容/大小不同，避免误重绑
    lib = Library(tmp_path / "data")

    lib.reconcile_scan(_infos([pa], pages=10))
    id_a = book_id_for(pa)
    assert id_a in lib.books

    stats = lib.reconcile_scan(_infos([pb], pages=20))
    assert stats["added"] == 1
    assert id_a in lib.books                    # 未删除
    assert lib.books[id_a].present is False     # 失联隐藏
    assert lib.books[book_id_for(pb)].present is True


def test_rebind_on_move_keeps_memory(tmp_path):
    """文件移动后按指纹重绑：书签/高亮/进度完整迁移到新 id（核心防丢）。"""
    a = tmp_path / "A"
    p1 = _mk_file(a / "书.pdf")
    lib = Library(tmp_path / "data")
    lib.reconcile_scan(_infos([p1], pages=100))
    bid1 = book_id_for(p1)

    b = lib.books[bid1]
    b.bookmarks = [Bookmark(3, "n")]
    b.highlights = [Highlight(5, (0.0, 0.0, 1.0, 1.0), "h")]
    b.progress = 42
    lib.save()

    p2 = a / "改名后.pdf"
    os.rename(p1, p2)                           # 同盘移动
    os.utime(p2, ns=(FIXED_NS, FIXED_NS))       # 固定 mtime，确保指纹不变
    bid2 = book_id_for(p2)

    stats = lib.reconcile_scan(_infos([p2], pages=100))
    assert stats["rebound"] == 1
    assert bid1 not in lib.books and bid2 in lib.books
    lib.save()

    # 重新加载：记忆已随新 id 落库
    lib2 = Library(tmp_path / "data")
    assert bid2 in lib2.books and bid1 not in lib2.books
    got = lib2.books[bid2]
    assert got.progress == 42
    assert [bm.page for bm in got.bookmarks] == [3]
    assert got.highlights[0].rect == (0.0, 0.0, 1.0, 1.0)


def test_rebind_ambiguous_picks_last_opened(tmp_path):
    """两条同指纹失联记录：迁移 last_opened 大者；另一条仍失联保留。"""
    lib = Library(tmp_path / "data")
    p1 = _mk_file(tmp_path / "A" / "one.pdf")
    p2 = _mk_file(tmp_path / "B" / "two.pdf")
    fp = _info(p1)[5]
    b1 = Book(id=book_id_for(p1), path=str(p1), title="one", ext=".pdf",
              fingerprint=fp, last_opened=1.0, present=True)
    b2 = Book(id=book_id_for(p2), path=str(p2), title="two", ext=".pdf",
              fingerprint=fp, last_opened=9.0, present=True)
    b1.bookmarks = [Bookmark(1, "old1")]
    b2.bookmarks = [Bookmark(2, "old2")]
    lib.books = {b1.id: b1, b2.id: b2}
    lib.save()

    p3 = _mk_file(tmp_path / "C" / "moved.pdf")
    new_id = book_id_for(p3)
    stats = lib.reconcile_scan([_info(p3)])
    assert stats["rebound"] == 1
    assert new_id in lib.books
    assert lib.books[new_id].bookmarks[0].note == "old2"   # last_opened 大者胜出
    assert lib.books[b1.id].present is False               # 另一条仍失联保留


def test_rebind_degraded_size_title(tmp_path):
    """mtime 变（指纹变）后仍能按 size+clean_title 重绑（L2 降级）。"""
    lib = Library(tmp_path / "data")
    old = tmp_path / "A" / "老书.pdf"
    _mk_file(old)
    lib.books = {book_id_for(old): Book(
        id=book_id_for(old), path=str(old), title="老书", ext=".pdf",
        fingerprint="111:5", present=True)}
    lib.save()

    new = _mk_file(tmp_path / "B" / "新位置.pdf")   # size=5，mtime 不同
    st = new.stat()
    fp_new = f"{st.st_mtime_ns}:{st.st_size}"
    info = (str(new), "老书", ".pdf", 8, True, fp_new)   # 标题相同
    stats = lib.reconcile_scan([info])
    assert stats["rebound"] == 1
    got = lib.books[book_id_for(new)]
    assert got.title == "老书" and got.pages == 8


def test_remove_dir_keeps_memory(tmp_path):
    """移除目录只改配置：该目录书籍失联隐藏，书签等记忆仍在库。"""
    a, b = tmp_path / "A", tmp_path / "B"
    pa = _mk_file(a / "甲.pdf")
    pb = _mk_file(b / "乙.pdf", data=b"world")
    lib = Library(tmp_path / "data")
    assert lib.add_dir(str(a)) and lib.add_dir(str(b))
    lib.reconcile_scan(_infos([pa, pb], pages=10))

    ida = book_id_for(pa)
    lib.books[ida].bookmarks = [Bookmark(7, "keep")]
    lib.save()

    assert lib.remove_dir(str(a))
    assert _norm_dir(str(a)) not in lib.library_dirs

    # 重扫（仅剩 B）→ A 的书失联隐藏但不删、记忆仍在
    lib.reconcile_scan(_infos([pb], pages=10))
    assert ida in lib.books and lib.books[ida].present is False
    lib.save()

    lib2 = Library(tmp_path / "data")
    assert ida in lib2.books
    assert [bm.page for bm in lib2.books[ida].bookmarks] == [7]


def test_scan_dedupe_overlapping_dirs(tmp_path):
    """同一物理文件在重叠/嵌套目录被扫两次 → 仅 1 条（F-9：按 (指纹,路径) 去重）。"""
    a = tmp_path / "A"
    f = _mk_file(a / "sub" / "x.txt", data=b"same-bytes")
    infos = scanner.collect_infos([str(a), str(a / "sub")], {})
    assert len([i for i in infos if i[0] == str(f)]) == 1


def test_scan_distinct_files_not_merged(tmp_path):
    """不同文件即使 mtime/size 相同也不合并（F-9，避免不同文件误并致失联）。"""
    a, b = tmp_path / "A", tmp_path / "B"
    _mk_file(a / "x.txt", data=b"same-bytes")
    _mk_file(b / "y.txt", data=b"same-bytes")
    infos = scanner.collect_infos([str(a), str(b)], {})
    assert len(infos) == 2


def test_upgrade_aligns_legacy_memory(tmp_path):
    """F-1：旧库（非规范 id、原始 path、fingerprint 为空）打开后自动对齐 id/path、
    回填指纹并迁移 bookmarks/reading_sessions 外键——既有记忆仍可被新扫描 id 命中。"""
    db = tmp_path / "data"
    f = _mk_file(tmp_path / "老书.pdf", data=b"legacy-bytes")
    new_id = book_id_for(f)

    st = Storage(db / "library.db")
    with st.conn:
        st.conn.execute(
            "INSERT INTO books(id, path, title, ext, pages, progress, "
            "last_opened, fingerprint, present) VALUES(?,?,?,?,?,?,?,?,?)",
            ("legacyid", str(f), "老书", ".pdf", 100, 37, 5.0, "", 1))
        st.conn.execute(
            "INSERT INTO bookmarks(book_id, page, note) VALUES(?,?,?)",
            ("legacyid", 9, "旧书签"))
        st.conn.execute(
            "INSERT INTO reading_sessions(book_id, started, ended, last_page) "
            "VALUES(?,?,?,?)", ("legacyid", 1000.0, 1060.0, 9))
    st.close()

    lib = Library(db)                 # 打开即触发 F-1 迁移
    assert new_id in lib.books and "legacyid" not in lib.books
    got = lib.books[new_id]
    assert got.progress == 37
    assert got.fingerprint                            # 文件可达 → 已回填指纹
    assert _norm_dir(got.path) == got.path            # 路径已规范化
    assert [bm.page for bm in got.bookmarks] == [9]   # 书签随 id 迁移
    row = lib.store.conn.execute(
        "SELECT book_id FROM reading_sessions").fetchone()
    assert row is not None and row[0] == new_id       # 会话外键已迁移，无孤儿
    lib.close()

    lib2 = Library(db)                # 幂等：二次打开不再迁移，记忆仍在
    assert list(lib2.books) == [new_id]
    assert [bm.page for bm in lib2.books[new_id].bookmarks] == [9]
    lib2.close()


def test_rebind_keeps_reading_sessions(tmp_path):
    """F-2：重绑换 id 时 reading_sessions 随记忆迁移，不被 ON DELETE CASCADE 误删。"""
    a = tmp_path / "A"
    p1 = _mk_file(a / "s.pdf")
    lib = Library(tmp_path / "data")
    lib.reconcile_scan(_infos([p1], pages=10))
    bid1 = book_id_for(p1)
    lib.save()
    sid = lib.store.begin_session(bid1)
    lib.store.end_session(sid, 3)
    assert lib.store.reading_minutes() > 0

    p2 = a / "s2.pdf"
    os.rename(p1, p2)
    os.utime(p2, ns=(FIXED_NS, FIXED_NS))             # 保 mtime → 指纹不变 → L1 重绑
    bid2 = book_id_for(p2)
    lib.reconcile_scan(_infos([p2], pages=10))
    lib.save()

    row = lib.store.conn.execute(
        "SELECT book_id FROM reading_sessions WHERE id=?", (sid,)).fetchone()
    assert row is not None and row[0] == bid2
    assert lib.store.reading_minutes() > 0
    lib.close()


def test_library_delete_persists(tmp_path):
    """F-5：Library.delete 落库删除，重启后不复活。"""
    a = tmp_path / "A"
    p = _mk_file(a / "x.pdf")
    lib = Library(tmp_path / "data")
    lib.reconcile_scan(_infos([p], pages=5))
    bid = book_id_for(p)
    lib.save()
    lib.delete(bid)
    lib2 = Library(tmp_path / "data")
    assert bid not in lib2.books
    lib2.close()


def test_scan_epoch_discards_stale():
    """F-4：仅最新扫描代次结果被采纳，过期 epoch 被丢弃。"""
    from app import PyShelfApp
    # PyQt5(SIP) 派生类不经 __init__ 构造：须用类自身 __new__（不建窗口）
    obj = PyShelfApp.__new__(PyShelfApp)
    obj._scan_epoch = 3
    assert obj._accept_epoch(3) is True
    assert obj._accept_epoch(2) is False


def test_custom_title_survives_rescan(tmp_path):
    """D-6：用户自定义标题在重扫/重启后保留，不被文件名静默覆盖。"""
    a = tmp_path / "A"
    p = _mk_file(a / "原名.pdf")
    lib = Library(tmp_path / "data")
    lib.reconcile_scan(_infos([p], pages=10))
    bid = book_id_for(p)
    book = lib.books[bid]
    book.title = "我的自定义名"
    book.title_custom = True
    lib.save()

    # 重扫（文件名未变）：自定义标题保留
    lib.reconcile_scan(_infos([p], pages=10))
    assert lib.books[bid].title == "我的自定义名"
    lib.save()

    # 重启后再扫：仍保留，且标志位持久化
    lib2 = Library(tmp_path / "data")
    assert lib2.books[bid].title == "我的自定义名"
    assert lib2.books[bid].title_custom is True
    lib2.reconcile_scan(_infos([p], pages=10))
    assert lib2.books[bid].title == "我的自定义名"
    lib2.close()


def test_delete_cleans_cover(tmp_path):
    """D-5：Library.delete 一并清理封面 PNG，避免陈旧封面残留。"""
    a = tmp_path / "A"
    p = _mk_file(a / "x.pdf")
    lib = Library(tmp_path / "data")
    lib.reconcile_scan(_infos([p], pages=5))
    bid = book_id_for(p)
    lib.save()
    cover = lib.covers_dir / f"{bid}.png"
    cover.write_bytes(b"png")
    assert cover.exists()

    lib.delete(bid)
    assert not cover.exists()               # 封面文件被清理
    assert bid not in lib.books
    lib.close()


def test_migration_rebuilds_fts(tmp_path):
    """D-1：F-1 迁移改 id 后按新 id 重建 books_fts，迁移到首次 save 之间可搜到。"""
    db = tmp_path / "data"
    f = _mk_file(tmp_path / "legacy.pdf", data=b"legacy-bytes")
    new_id = book_id_for(f)
    st = Storage(db / "library.db")
    with st.conn:                            # 直接插旧行（非规范 id、无 FTS）
        st.conn.execute(
            "INSERT INTO books(id, path, title, ext, pages, fingerprint, present) "
            "VALUES(?,?,?,?,?,?,?)",
            ("legacyid", str(f), "LegacyBook", ".pdf", 10, "", 1))
    st.close()

    st2 = Storage(db / "library.db")         # __init__ 触发 id 对齐迁移
    row = st2.conn.execute(
        "SELECT book_id FROM books_fts WHERE book_id=?", (new_id,)).fetchone()
    assert row is not None                   # 新 id 的 FTS 行已重建
    assert new_id in st2.search_titles("LegacyBook")   # 迁移后无需 save 即可搜到
    st2.close()


def test_progress_throttle_persists(tmp_path):
    """D-4：翻页累计到阈值才落库（崩溃兜底进度写入）。"""
    from app import PyShelfApp
    a = tmp_path / "A"
    p = _mk_file(a / "book.pdf")
    lib = Library(tmp_path / "data")
    lib.reconcile_scan(_infos([p], pages=100))
    bid = book_id_for(p)
    lib.save()
    book = lib.books[bid]

    obj = PyShelfApp.__new__(PyShelfApp)     # 不初始化窗口，仅验证节流逻辑
    obj.library = lib
    obj._progress_ts = time.time()
    obj._progress_pages = 0

    for pg in range(2, 11):                  # 翻 9 页：未达阈值/未超时 → 不落库
        obj.on_reader_page_change(book, pg)
    row = lib.store.conn.execute(
        "SELECT progress FROM books WHERE id=?", (bid,)).fetchone()
    assert row["progress"] == 0              # 仍是初始进度（未写盘）

    obj.on_reader_page_change(book, 11)      # 第 10 页触发节流落库
    row = lib.store.conn.execute(
        "SELECT progress FROM books WHERE id=?", (bid,)).fetchone()
    assert row["progress"] == 11
    lib.close()
