"""目录扫描、书名清洗、封面生成（PDF 首页 / 程序化书脊占位）。"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont

SUPPORTED = {".pdf", ".epub", ".txt", ".md"}
SKIP_NAMES = {"desktop.ini", "thumbs.db"}

# 书名噪声清洗规则（针对 Z-Library / Anna's Archive 等下载命名）
_NOISE_PATTERNS = [
    (re.compile(r"(?i)\(?\s*z[\s-]?library\s*\)?"), ""),
    (re.compile(r"(?i)z-lib\.org"), ""),
    (re.compile(r"(?i)anna'?s\s+archive"), ""),
    (re.compile(r"(?i)zhelper-search"), ""),
    (re.compile(r"(?i)libgenrs?[\w-]*"), ""),
    (re.compile(r"\b[0-9a-f]{32}\b"), ""),          # 内容哈希
    (re.compile(r"\b97[89][-\d]{9,17}\b"), ""),      # ISBN
    (re.compile(r"\(\s*\)"), ""),
    (re.compile(r"（\s*）"), ""),
]

# 拟物配色（书脊占位用）
SPINE_COLORS = [
    (153, 53, 86), (15, 110, 86), (24, 95, 165), (163, 45, 45),
    (83, 74, 183), (59, 109, 17), (133, 79, 11), (60, 52, 137),
    (218, 90, 48), (29, 158, 117),
]


def clean_title(filename: str) -> str:
    s = filename
    for suf in SUPPORTED:
        if s.lower().endswith(suf):
            s = s[: -len(suf)]
            break
    # " -- 作者 -- 年份 -- 出版社 -- ..." 长尾（Anna's Archive 命名），截断
    if " -- " in s:
        s = s.split(" -- ")[0]
    for pat, repl in _NOISE_PATTERNS:
        s = pat.sub(repl, s)
    s = re.sub(r"\s{2,}", " ", s).strip(" -_—·.")
    return s or filename


def scan_files(lib_dir: str) -> list[Path]:
    root = Path(lib_dir)
    if not root.is_dir():
        return []
    out = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.name.lower() in SKIP_NAMES or p.name.startswith("~$"):
            continue
        if p.suffix.lower() in SUPPORTED:
            out.append(p)
    out.sort(key=lambda p: p.name.lower())
    return out


def probe_pdf(path: Path) -> tuple[int, bool]:
    """返回 (页数, 可读)。"""
    try:
        doc = pymupdf.open(str(path))
        n = doc.page_count
        doc.close()
        return n, True
    except Exception:
        return 0, False


def cover_path(data_dir: Path, bid: str) -> Path:
    return Path(data_dir) / "covers" / f"{bid}.png"


def _font(size: int):
    for name in ("msyh.ttc", "simhei.ttf", "simsun.ttc", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def generate_cover(path: Path, out_png: Path, title: str, ext: str,
                   max_h: int = 420) -> bool:
    """PDF 取第 1 页渲染；其它格式生成纯色书脊占位封面。"""
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    try:
        if ext == ".pdf":
            doc = pymupdf.open(str(path))
            page = doc[0]
            zoom = max_h / max(page.rect.height, 1)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            doc.close()
        else:
            seed = int(hashlib.md5(title.encode("utf-8")).hexdigest()[:8], 16)
            color = SPINE_COLORS[seed % len(SPINE_COLORS)]
            w, h = int(max_h * 0.72), max_h
            img = Image.new("RGB", (w, h), color)
            d = ImageDraw.Draw(img)
            # 左侧书脊高光
            d.rectangle([0, 0, 14, h], fill=tuple(min(c + 28, 255) for c in color))
            d.rectangle([w - 4, 0, w, h], fill=tuple(max(c - 40, 0) for c in color))
            # 书名竖排（自动换行的水平文字，居中）
            f = _font(26)
            lines, line = [], ""
            for ch in title:
                if d.textlength(line + ch, font=f) > w - 60:
                    lines.append(line)
                    line = ch
                else:
                    line += ch
            if line:
                lines.append(line)
            lines = lines[:12]
            total = len(lines) * 38
            y = (h - total) // 2 + 10
            for ln in lines:
                tw = d.textlength(ln, font=f)
                d.text(((w - tw) / 2, y), ln, font=f,
                       fill=(245, 240, 225))
                y += 38
        img.save(out_png, "PNG")
        return True
    except Exception:
        return False
