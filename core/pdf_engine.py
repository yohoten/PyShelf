"""PyMuPDF 封装：页面渲染、目录、高亮叠加、烛光模式。"""
from __future__ import annotations

from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageEnhance

HIGHLIGHT_RGBA = (239, 159, 39, 78)     # 琥珀色高亮
CANDLE_TINT = (120, 88, 40)             # 烛光模式的暖色滤镜


class PDFEngine:
    def __init__(self, path: Path):
        self.path = str(path)
        self.doc = pymupdf.open(self.path)

    @property
    def page_count(self) -> int:
        return self.doc.page_count

    def close(self):
        try:
            self.doc.close()
        except Exception:
            pass

    def get_toc(self) -> list[tuple[int, str, int]]:
        """[(level, title, 1-based page), ...]"""
        try:
            return [tuple(t) for t in self.doc.get_toc()]
        except Exception:
            return []

    def render(self, page_index: int, zoom: float, highlights=(),
               candle: bool = False) -> Image.Image:
        """渲染一页为 PIL Image。highlights 为该页的 (x0,y0,x1,y1) PDF 坐标矩形。"""
        page = self.doc[page_index]
        m = pymupdf.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=m, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

        if highlights:
            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            d = ImageDraw.Draw(overlay)
            for rect in highlights:
                x0, y0, x1, y1 = [c * zoom for c in rect]
                d.rectangle([x0, y0, x1, y1], fill=HIGHLIGHT_RGBA,
                            outline=(185, 117, 23, 200), width=1)
            img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

        if candle:
            tint = Image.new("RGB", img.size, CANDLE_TINT)
            img = Image.blend(img, tint, 0.28)
            img = ImageEnhance.Brightness(img).enhance(0.88)

        return img

    def page_text(self, page_index: int) -> str:
        try:
            return self.doc[page_index].get_text()
        except Exception:
            return ""
