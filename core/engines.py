"""文档引擎抽象：DocumentEngine 协议 + 引擎工厂。

协议面向 ReaderView 所需的最小能力集：
    page_count / page_size / get_toc / render / page_text / close

已实现引擎：
- PDFEngine   （.pdf，PyMuPDF，见 core/pdf_engine.py）
- TextEngine  （.txt / .md，纯 Python + Pillow，按行分页）

后续 EPUB 等新格式：实现本协议后在 open_engine() 注册扩展名即可。
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

CANDLE_TINT = (120, 88, 40)             # 烛光模式的暖色滤镜（与 PDFEngine 一致）

# 文本引擎的虚拟页面：A4 比例，96 DPI 基准
TEXT_PAGE_W, TEXT_PAGE_H = 794.0, 1123.0
LINES_PER_PAGE = 42
TAB = "    "


@runtime_checkable
class DocumentEngine(Protocol):
    """阅读器与具体文档格式之间的最小契约。"""

    @property
    def page_count(self) -> int: ...

    def close(self) -> None: ...

    def get_toc(self) -> list[tuple[int, str, int]]:
        """[(level, title, 1-based page), ...]"""

    def page_size(self, page_index: int) -> tuple[float, float]:
        """页面 (宽, 高)，用于适配缩放。"""

    def render(self, page_index: int, zoom: float, highlights=(),
               candle: bool = False) -> Image.Image: ...

    def page_text(self, page_index: int) -> str: ...


class TextEngine:
    """.txt / .md 引擎：按行分页，Pillow 排版到纸张底色上。

    highlights 矩形坐标系无意义（非固定版式），忽略之。
    """

    def __init__(self, path: Path):
        self.path = str(path)
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
        self.lines = raw.replace("\t", TAB).splitlines() or [""]
        self.pages: list[list[str]] = [
            self.lines[i:i + LINES_PER_PAGE]
            for i in range(0, len(self.lines), LINES_PER_PAGE)
        ] or [[self.lines[0]]]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def close(self):
        pass

    def get_toc(self) -> list[tuple[int, str, int]]:
        """Markdown 标题（#/##/…）作为目录；纯文本返回空。"""
        toc = []
        for i, ln in enumerate(self.lines):
            s = ln.lstrip()
            if s.startswith("#") and s.lstrip("#").strip():
                level = min(len(s) - len(s.lstrip("#")), 3)
                page = i // LINES_PER_PAGE + 1
                toc.append((level + 1, s.lstrip("#").strip(), page))
        return toc

    def page_size(self, page_index: int) -> tuple[float, float]:
        return TEXT_PAGE_W, TEXT_PAGE_H

    def _font(self, zoom: float):
        size = max(int(24 * zoom), 9)
        for name in ("msyh.ttc", "simhei.ttf", "simsun.ttc", "arial.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    def render(self, page_index: int, zoom: float, highlights=(),
               candle: bool = False) -> Image.Image:
        w, h = int(TEXT_PAGE_W * zoom), int(TEXT_PAGE_H * zoom)
        img = Image.new("RGB", (w, h), (246, 240, 228))   # 纸张色
        d = ImageDraw.Draw(img)
        f = self._font(zoom)
        x = int(64 * zoom)
        y = int(56 * zoom)
        step = int(24 * zoom)
        for ln in self.pages[page_index]:
            d.text((x, y), ln[:200], font=f, fill=(52, 44, 36))
            y += step
            if y > h - int(40 * zoom):
                break
        # 折痕装饰（右侧细暗线，延续拟物纸张感）
        d.line([(w - 1, 0), (w - 1, h)], fill=(214, 204, 186), width=1)

        if candle:
            tint = Image.new("RGB", img.size, CANDLE_TINT)
            img = Image.blend(img, tint, 0.28)
            img = ImageEnhance.Brightness(img).enhance(0.88)
        return img

    def page_text(self, page_index: int) -> str:
        return "\n".join(self.pages[page_index])


def open_engine(path: str | Path) -> DocumentEngine:
    """按扩展名分派引擎。不支持的扩展名抛 ValueError。"""
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        from core.pdf_engine import PDFEngine
        return PDFEngine(path)
    if ext in (".txt", ".md"):
        return TextEngine(path)
    # EPUB：可选依赖，装了 ebooklib 才可用
    if ext == ".epub":
        try:
            from core.epub_engine import EPUBEngine   # noqa: F401（占位）
        except ImportError:
            pass
    raise ValueError(f"暂不支持的格式：{ext}")
