"""书名清洗规则测试（针对 Z-Library / Anna's Archive 下载命名）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.scanner import clean_title   # noqa: E402


def test_plain_name():
    assert clean_title("流畅的Python.pdf") == "流畅的Python"


def test_zlibrary_suffix():
    assert "z-library" not in clean_title("机器学习 (z-library).pdf").lower()
    assert "zlibrary" not in clean_title("算法导论 zlibrary.epub").lower()


def test_annas_archive_longtail():
    raw = "深入理解计算机系统 -- Randal E. Bryant -- 2016 -- 机械工业出版社 -- 32位md5hash.pdf"
    out = clean_title(raw)
    assert out == "深入理解计算机系统"


def test_isbn_stripped():
    out = clean_title("Clean Code 978-7-115-21687-8.pdf")
    assert "978" not in out
    assert out.startswith("Clean Code")


def test_md5_hash_stripped():
    out = clean_title("数据库系统概念 0123456789abcdef0123456789abcdef.pdf")
    assert "0123456789abcdef" not in out


def test_empty_fallback():
    # 全部被清洗掉时回退原文件名
    out = clean_title("（ ）.pdf")
    assert out == "（ ）.pdf"


def test_md_supported():
    assert clean_title("学习笔记.md") == "学习笔记"
