"""拟物主题：配色、DPI 缩放、字体、程序化木纹纹理、圆角带书脊/阴影的封面渲染。"""
from __future__ import annotations

import math
import random
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------- 配色（深色书房 + 黄铜 + 琥珀） ----------------
BG = "#241E18"
BG_DEEP = "#1A1512"
PANEL = "#2E2620"
BAR = "#3E3125"
BAR_HOVER = "#4B3C2C"
BTN = "#5C4327"
BTN_HOVER = "#7A5A38"
PLANK_TOP = "#9A7548"
PLANK_BODY = "#6B4E2E"
PLANK_EDGE = "#3F2D19"
BRASS = "#E8D9B0"
BRASS_DIM = "#C8B896"
MUTED = "#8A8478"
GOLD = "#EF9F27"
RED = "#A32D2D"
PAPER = "#F2E9D8"

SPINE_COLORS = ["#993556", "#0F6E56", "#185FA5", "#A32D2D", "#534AB7",
                "#3B6D11", "#854F0B", "#3C3489", "#D85A30", "#1D9E75"]


# ---------------- DPI / 字体 ----------------
def dpi_scale(root) -> float:
    try:
        return max(1.0, min(3.0, root.winfo_fpixels("1i") / 96.0))
    except Exception:
        return 1.0


@lru_cache(maxsize=64)
def _font(size: int, bold: bool = False):
    for name in (("msyhbd.ttc", "msyh.ttc") if bold else ("msyh.ttc",)):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.truetype("simhei.ttf", size)
    except OSError:
        return ImageFont.load_default()


def tk_font(size: int, serif: bool = False, bold: bool = False,
            scale: float = 1.0) -> tuple:
    fam = "KaiTi" if serif else "Microsoft YaHei"
    px = max(int(round(size * scale)), 8)
    return (fam, px, "bold") if bold else (fam, px)


# ---------------- 程序化纹理 ----------------
def _noise(w: int, h: int, cell: int, seed: int) -> Image.Image:
    rnd = random.Random(seed)
    sw, sh = max(w // cell, 2), max(h // cell, 2)
    small = Image.frombytes("L", (sw, sh),
                            bytes(rnd.randrange(256) for _ in range(sw * sh)))
    return small.resize((w, h), Image.BICUBIC).filter(
        ImageFilter.GaussianBlur(radius=cell * 0.7))


def wood_background(w: int, h: int, seed: int = 11) -> Image.Image:
    """深色木纹背景：纵向明暗渐变 + 年轮曲线 + 细颗粒 + 暗角。"""
    w, h = max(w, 2), max(h, 2)
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    top, bottom = (44, 36, 27), (24, 20, 15)
    for y in range(h):
        t = y / max(h - 1, 1)
        d.line([(0, y), (w, y)],
               fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    # 年轮：缓慢起伏的深浅木纹
    rnd = random.Random(seed)
    for i in range(0, h + 40, 6):
        base_y = i + rnd.randint(-2, 2)
        amp = rnd.uniform(1.5, 4.5)
        phase = rnd.uniform(0, math.tau)
        dark = rnd.random() < 0.5
        col = (30, 24, 17) if dark else (58, 47, 34)
        pts = [(x, base_y + amp * math.sin(phase + x / rnd.uniform(160, 420)))
               for x in range(0, w + 12, 12)]
        d.line(pts, fill=col, width=1)
    img = img.filter(ImageFilter.GaussianBlur(radius=0.5))
    # 颗粒
    grain = _noise(w, h, 3, seed + 5)
    img = Image.blend(img, Image.merge("RGB", (grain, grain, grain)), 0.045)
    # 暗角
    vig = Image.new("L", (w, h), 0)
    ImageDraw.Draw(vig).ellipse([-w * 0.25, -h * 0.35, w * 1.25, h * 1.35], fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(w * 0.08))
    dark = Image.new("RGB", (w, h), (12, 10, 8))
    return Image.composite(img, dark, vig)


def plank_texture(w: int, h: int, seed: int = 21) -> Image.Image:
    """木质层板：顶部高光 + 主体木色 + 底边暗线 + 木纹刻痕 + 圆角端头（含下方阴影）。"""
    pad = 6
    W, H = max(w, 8), h + pad
    img = Image.new("RGB", (W, H), (18, 14, 10))
    d = ImageDraw.Draw(img)
    rnd = random.Random(seed)
    for y in range(h):
        t = y / max(h - 1, 1)
        if t < 0.22:
            c = (154, 117, 72)
        elif t < 0.55:
            k = (t - 0.22) / 0.33
            c = tuple(int(154 + (107 - 154) * k) for _ in range(3))
        else:
            k = (t - 0.55) / 0.45
            c = tuple(int(107 + (74 - 107) * k) for _ in range(3))
        d.line([(0, y), (W, y)], fill=c)
    for x in range(-20, W + 20, rnd.randint(34, 60)):
        col = (72, 52, 30) if rnd.random() < 0.6 else (124, 92, 56)
        d.line([(x, 3), (x + rnd.randint(8, 22), h - 2)], fill=col, width=1)
    # 底部厚度暗线
    d.line([(0, h - 1), (W, h - 1)], fill=(48, 34, 19))
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, W - 1, h + pad - 3],
                                          radius=3, fill=255)
    shadow = mask.filter(ImageFilter.GaussianBlur(2.4)).point(lambda v: int(v * 0.45))
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.paste(Image.new("RGB", (W, H), (0, 0, 0)), (0, 0), shadow)
    out.paste(img, (0, 0), mask)
    return out


# ---------------- 封面渲染 ----------------
def _fit_crop(img: Image.Image, w: int, h: int) -> Image.Image:
    """按目标比例裁切（优先保留顶部，符合书籍封面构图）并高质量缩放。"""
    tr = w / h
    iw, ih = img.size
    if iw / ih > tr:
        nw = max(int(ih * tr), 1)
        left = (iw - nw) // 2
        img = img.crop((left, 0, left + nw, ih))
    else:
        nh = max(int(iw / tr), 1)
        img = img.crop((0, 0, iw, nh))
    return img.resize((w, h), Image.LANCZOS)


def render_cover(src: Path | None, title: str, w: int, h: int,
                 hover: bool = False, radius: int = 6) -> Image.Image:
    """生成带书脊高光、圆角、柔和投影的封面（RGBA）。src 为空则生成拟物占位封面。"""
    pad = 10
    W, H = w + pad, h + pad
    base = Image.new("RGB", (w, h), (60, 48, 34))
    if src and Path(src).exists():
        try:
            base = _fit_crop(Image.open(src).convert("RGB"), w, h)
        except Exception:
            pass
    else:
        base = _placeholder(title, w, h)

    # 书脊：左侧竖向高光渐变 + 折痕线
    grad = Image.new("L", (w, h), 0)
    gd = ImageDraw.Draw(grad)
    spine_w = max(int(w * 0.09), 4)
    for x in range(spine_w):
        gd.line([(x, 0), (x, h)], fill=int(70 * (1 - x / spine_w)))
    base = Image.composite(Image.new("RGB", (w, h), (255, 246, 224)), base,
                           grad.point(lambda v: v))
    d = ImageDraw.Draw(base)
    d.line([(spine_w, 0), (spine_w, h)], fill=(40, 30, 20), width=1)
    # 右侧暗边
    for x in range(max(w - 5, 0), w):
        d.line([(x, 0), (x, h)], fill=(30, 22, 14))
    # 顶部柔光
    hl = Image.new("L", (w, h), 0)
    ImageDraw.Draw(hl).rectangle([0, 0, w, max(int(h * 0.06), 2)], fill=38)
    base = Image.composite(Image.new("RGB", (w, h), (255, 250, 235)), base,
                           hl.filter(ImageFilter.GaussianBlur(3)))

    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1],
                                           radius=radius, fill=255)
    shadow = mask.filter(ImageFilter.GaussianBlur(3.2)).point(lambda v: int(v * 0.5))
    if hover:
        shadow = mask.filter(ImageFilter.GaussianBlur(4.2)).point(
            lambda v: int(v * 0.62))

    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.paste(Image.new("RGB", (w, h), (0, 0, 0)), (2, 5), shadow)
    out.paste(base, (0, 0), mask)
    if hover:
        ImageDraw.Draw(out).rounded_rectangle(
            [0, 0, w - 1, h - 1], radius=radius, outline=GOLD, width=2)
    else:
        ImageDraw.Draw(out).rounded_rectangle(
            [0, 0, w - 1, h - 1], radius=radius,
            outline=(58, 44, 28), width=1)
    return out


def _placeholder(title: str, w: int, h: int) -> Image.Image:
    seed = abs(hash(title)) % len(SPINE_COLORS)
    col = SPINE_COLORS[seed]
    rgb = tuple(int(col[i:i + 2], 16) for i in (1, 3, 5))
    img = Image.new("RGB", (w, h), rgb)
    d = ImageDraw.Draw(img)
    # 纵向明暗 + 书名（按像素宽度自动换行）
    d.rectangle([0, int(h * 0.62), w, h],
                fill=tuple(max(c - 26, 0) for c in rgb))
    f = _font(max(int(w * 0.13), 9))
    lines, cur = [], ""
    for ch in title:
        if d.textlength(cur + ch, font=f) > w - 18:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    lines = lines[:9]
    y = int(h * 0.12)
    for ln in lines:
        d.text(((w - d.textlength(ln, font=f)) / 2, y), ln, font=f,
               fill=(246, 241, 227))
        y += int(f.size * 1.45)
    d.text((w / 2 - d.textlength("未获取封面", font=_font(8)) / 2, h - 16),
           "未获取封面", font=_font(8), fill=(240, 230, 205))
    return img
