"""引擎层测试：工厂分派、TextEngine 分页/目录/渲染、PDFEngine 冒烟。"""
import sys
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.engines import TextEngine, open_engine   # noqa: E402
from core.pdf_engine import PDFEngine   # noqa: E402


def test_factory_dispatch(tmp_path):
    txt = tmp_path / "note.txt"
    txt.write_text("hello", encoding="utf-8")
    assert isinstance(open_engine(txt), TextEngine)
    with pytest.raises(ValueError):
        open_engine(tmp_path / "x.mobi")


def _make_pdf(tmp_path) -> Path:
    p = tmp_path / "sample.pdf"
    doc = pymupdf.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((72, 96), f"Page {i + 1}")
    doc.save(str(p))
    doc.close()
    return p


def test_pdf_engine(tmp_path):
    p = _make_pdf(tmp_path)
    eng = PDFEngine(p)
    assert eng.page_count == 3
    w, h = eng.page_size(0)
    assert w > 0 and h > 0
    img = eng.render(0, zoom=1.0)
    assert isinstance(img, Image.Image) and img.width > 0
    assert "Page 1" in eng.page_text(0)
    eng.close()


def test_factory_pdf(tmp_path):
    eng = open_engine(_make_pdf(tmp_path))
    assert isinstance(eng, PDFEngine)
    assert eng.page_count == 3
    eng.close()


def test_text_engine_paging(tmp_path):
    p = tmp_path / "long.txt"
    lines = [f"line {i}" for i in range(100)]
    p.write_text("\n".join(lines), encoding="utf-8")
    eng = TextEngine(p)
    from core.engines import LINES_PER_PAGE
    assert eng.page_count == (100 + LINES_PER_PAGE - 1) // LINES_PER_PAGE
    assert "line 0" in eng.page_text(0)
    assert "line 99" in eng.page_text(eng.page_count - 1)
    img = eng.render(0, zoom=1.0)
    assert isinstance(img, Image.Image)


def test_text_engine_toc_markdown(tmp_path):
    p = tmp_path / "note.md"
    p.write_text("# 第一章\n正文\n\n## 小节 A\n正文\n\n# 第二章\n正文",
                 encoding="utf-8")
    eng = TextEngine(p)
    toc = eng.get_toc()
    titles = [t for _, t, _ in toc]
    assert "第一章" in titles and "小节 A" in titles and "第二章" in titles
    assert all(p <= eng.page_count for _, _, p in toc)


def test_text_engine_empty(tmp_path):
    p = tmp_path / "empty.txt"
    p.write_text("", encoding="utf-8")
    eng = TextEngine(p)
    assert eng.page_count == 1
    eng.render(0, zoom=1.0)
