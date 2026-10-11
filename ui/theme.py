"""拟物主题：配色、DPI、字体、程序化木纹纹理、圆角带书脊/阴影的封面渲染。

PyQt5 版：配色与 PIL 纹理/封面渲染逻辑与 tk 版一致（纯 PIL，跨框架复用）；
表述层（QFont/QSS/QPixmap）为 Qt 实现，并支持用户字体与字号缩放配置。
"""
from __future__ import annotations

import math
import random
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from PyQt5.QtGui import QFont, QImage, QPixmap

# ---------------- 配色（双主题：烛光书房·深 / 午后书房·浅） ----------------
_DARK = {
    "BG": "#241E18", "BG_DEEP": "#1A1512", "PANEL": "#2E2620",
    "BAR": "#3E3125", "BAR_HOVER": "#4B3C2C",
    "BTN": "#5C4327", "BTN_HOVER": "#7A5A38",
    "PLANK_TOP": "#9A7548", "PLANK_BODY": "#6B4E2E", "PLANK_EDGE": "#3F2D19",
    "BRASS": "#E8D9B0", "BRASS_DIM": "#C8B896", "MUTED": "#8A8478",
    "GOLD": "#EF9F27", "RED": "#A32D2D", "PAPER": "#F2E9D8",
    # 木纹背景渐变端点与暗角
    "WOOD_TOP": (44, 36, 27), "WOOD_BOTTOM": (24, 20, 15),
    "WOOD_RING_DARK": (30, 24, 17), "WOOD_RING_LIGHT": (58, 47, 34),
    "WOOD_VIGNETTE": (12, 10, 8),
}

_LIGHT = {
    "BG": "#EFE6D4", "BG_DEEP": "#E5DAC4", "PANEL": "#F6EFDF",
    "BAR": "#DCCFB4", "BAR_HOVER": "#CEBE9F",
    "BTN": "#B69C75", "BTN_HOVER": "#C8AD82",
    "PLANK_TOP": "#C09359", "PLANK_BODY": "#8F6E42", "PLANK_EDGE": "#5C4426",
    "BRASS": "#4A3820", "BRASS_DIM": "#6B5A3E", "MUTED": "#8A7A60",
    "GOLD": "#B8760F", "RED": "#A32D2D", "PAPER": "#FBF6EA",
    "WOOD_TOP": (222, 202, 168), "WOOD_BOTTOM": (192, 170, 136),
    "WOOD_RING_DARK": (168, 146, 112), "WOOD_RING_LIGHT": (232, 214, 182),
    "WOOD_VIGNETTE": (150, 132, 104),
}

THEMES = {"dark": _DARK, "light": _LIGHT}
_current_theme = "dark"


def use_theme(name: str):
    """切换主题（需在创建任何控件之前调用；运行期切换需重启生效）。"""
    global _current_theme
    palette = THEMES.get(name, _DARK)
    _current_theme = "dark" if palette is _DARK else name
    globals().update({k: v for k, v in palette.items() if not k.startswith("_")})


def current_theme() -> str:
    return _current_theme


def is_dark() -> bool:
    return _current_theme == "dark"


use_theme("dark")

SPINE_COLORS = ["#993556", "#0F6E56", "#185FA5", "#A32D2D", "#534AB7",
                "#3B6D11", "#854F0B", "#3C3489", "#D85A30", "#1D9E75"]

# 可选字体（设置项下拉用）
UI_FONT_CHOICES = ["Microsoft YaHei", "SimHei", "SimSun", "KaiTi",
                   "Consolas", "Segoe UI", "Arial"]
TITLE_FONT_CHOICES = ["KaiTi", "SimSun", "Microsoft YaHei", "SimHei"]


# ---------------- DPI / 字体（Qt，可配置） ----------------
def dpi_scale() -> float:
    """按主屏逻辑 DPI 求缩放系数（96 DPI = 1.0），夹在 [1.0, 3.0]。"""
    try:
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            scr = app.primaryScreen()
            if scr is not None:
                return max(1.0, min(3.0, scr.logicalDotsPerInch() / 96.0))
    except Exception:
        pass
    return 1.0


# 用户可配置字体（由 app 在创建任何控件之前通过 configure_fonts 注入）
_ui_font = "Microsoft YaHei"
_title_font = "KaiTi"
_font_scale = 1.0


def configure_fonts(ui_font: str | None = None, title_font: str | None = None,
                    font_scale: float | None = None):
    """设置界面字体 / 标题字体 / 全局字号缩放（需在创建控件前调用）。"""
    global _ui_font, _title_font, _font_scale
    if ui_font:
        _ui_font = str(ui_font)
    if title_font:
        _title_font = str(title_font)
    if font_scale:
        _font_scale = max(0.6, min(float(font_scale), 2.0))
    _qfont.cache_clear()


def font_scale() -> float:
    return _font_scale


@lru_cache(maxsize=512)
def _qfont(fam: str, bold: bool, px: int) -> QFont:
    f = QFont(fam)
    f.setPixelSize(max(int(px), 8))
    f.setBold(bool(bold))
    return f


def font(size: int, serif: bool = False, bold: bool = False,
         scale: float = 1.0) -> QFont:
    """返回按 DPI 与用户字号缩放后的 QFont（像素号）。"""
    fam = _title_font if serif else _ui_font
    px = max(int(round(size * max(scale, 0.1) * _font_scale)), 8)
    return _qfont(fam, bool(bold), px)


def pil_to_pixmap(img: Image.Image) -> QPixmap:
    """PIL 图像 → QPixmap（走 ARGB32，保证透明通道正确；copy 避免缓冲被回收）。"""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    data = img.tobytes("raw", "BGRA")
    qimg = QImage(data, img.width, img.height, img.width * 4,
                  QImage.Format_ARGB32)
    return QPixmap.fromImage(qimg.copy())


# ---------------- 全局样式表（QSS） ----------------
def global_qss() -> str:
    """按当前配色生成 App 级 QSS，统一各控件外观（拟物暖色 + 细节打磨）。"""
    base = max(int(round(13 * _font_scale)), 9)
    return f"""
    QWidget {{
        background: {BG};
        color: {BRASS};
        font-family: '{_ui_font}';
        font-size: {base}px;
    }}
    QMainWindow, QDialog {{ background: {BG}; }}
    QLabel {{ background: transparent; }}
    QPushButton {{
        background: {BAR};
        color: {BRASS};
        border: 1px solid {PLANK_EDGE};
        border-radius: 5px;
        padding: 5px 12px;
    }}
    QPushButton:hover {{ background: {BAR_HOVER}; border-color: {BTN_HOVER}; }}
    QPushButton:pressed {{ background: {BTN_HOVER}; }}
    QPushButton:focus {{ border-color: {GOLD}; }}
    QPushButton[role="primary"] {{
        background: {BTN}; border-color: {BTN_HOVER}; font-weight: 600;
    }}
    QPushButton[role="primary"]:hover {{ background: {BTN_HOVER}; }}
    QPushButton[role="dim"] {{ color: {MUTED}; border-color: {BG_DEEP}; }}
    QPushButton[role="dim"]:hover {{ color: {BRASS}; }}
    QPushButton[role="gold"] {{
        background: {GOLD}; color: #412402; border-color: {GOLD};
    }}
    QPushButton[role="tag"] {{
        background: {BG_DEEP}; color: {MUTED}; border: 1px solid {PLANK_EDGE};
        padding: 3px 10px;
    }}
    QPushButton[role="tag"]:hover {{ color: {BRASS}; }}
    QPushButton[role="tag"]:checked {{
        background: {BTN}; color: {BRASS}; border-color: {BTN_HOVER};
    }}
    QPushButton:disabled {{
        color: {MUTED}; background: {BAR}; border-color: {BG_DEEP};
    }}
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
        background: {BG_DEEP};
        color: {BRASS};
        border: 1px solid {PLANK_EDGE};
        border-radius: 5px;
        padding: 4px 7px;
        selection-background-color: {BTN};
        selection-color: {BRASS};
    }}
    QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QTextEdit:focus {{
        border-color: {GOLD};
    }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox QAbstractItemView {{
        background: {BAR}; color: {BRASS}; border: 1px solid {PLANK_EDGE};
        selection-background-color: {BTN}; selection-color: {BRASS};
    }}
    QCheckBox {{ spacing: 7px; background: transparent; }}
    QCheckBox::indicator {{
        width: 15px; height: 15px; border-radius: 3px;
        border: 1px solid {PLANK_EDGE}; background: {BG_DEEP};
    }}
    QCheckBox::indicator:checked {{ background: {BTN}; border-color: {GOLD}; }}
    QListWidget, QTreeWidget, QTableWidget {{
        background: {BG_DEEP};
        color: {BRASS_DIM};
        border: 1px solid {PLANK_EDGE};
        border-radius: 6px;
        outline: none;
    }}
    QListWidget::item {{ padding: 4px 7px; border-radius: 4px; }}
    QListWidget::item:hover {{ background: {BAR}; color: {BRASS}; }}
    QListWidget::item:selected, QTreeWidget::item:selected,
    QTableWidget::item:selected {{ background: {BTN}; color: {BRASS}; }}
    QTreeWidget::item {{ padding: 3px 4px; }}
    QTreeWidget::item:hover {{ background: {BAR}; }}
    QHeaderView::section {{
        background: {BAR};
        color: {BRASS};
        border: none;
        border-right: 1px solid {PLANK_EDGE};
        padding: 6px 8px;
        font-weight: 600;
    }}
    QTableWidget {{
        gridline-color: {PLANK_EDGE};
        alternate-background-color: {PANEL};
    }}
    QTabWidget::pane {{
        border: 1px solid {PLANK_EDGE}; border-radius: 6px;
        background: {PANEL}; top: -1px;
    }}
    QTabBar::tab {{
        background: {BG_DEEP};
        color: {MUTED};
        padding: 6px 13px;
        margin-right: 2px;
        border: 1px solid {PLANK_EDGE};
        border-bottom: none;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
    }}
    QTabBar::tab:hover {{ color: {BRASS}; }}
    QTabBar::tab:selected {{
        background: {PANEL}; color: {BRASS}; border-color: {BTN_HOVER};
    }}
    QScrollArea {{ border: none; }}
    QScrollBar:vertical {{
        background: {BG_DEEP}; width: 12px; margin: 0; border-radius: 6px;
    }}
    QScrollBar::handle:vertical {{
        background: {PLANK_BODY}; min-height: 30px; border-radius: 5px; margin: 2px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {PLANK_TOP}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
    }}
    QScrollBar:horizontal {{
        background: {BG_DEEP}; height: 12px; margin: 0; border-radius: 6px;
    }}
    QScrollBar::handle:horizontal {{
        background: {PLANK_BODY}; min-width: 30px; border-radius: 5px; margin: 2px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {PLANK_TOP}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: transparent;
    }}
    QProgressBar {{
        background: {BG_DEEP}; border: 1px solid {PLANK_EDGE}; border-radius: 5px;
        height: 10px; text-align: center; color: {MUTED};
    }}
    QProgressBar::chunk {{ background: {GOLD}; border-radius: 4px; }}
    QMenu {{
        background: {BAR}; color: {BRASS_DIM}; border: 1px solid {PLANK_EDGE};
        border-radius: 6px; padding: 4px;
    }}
    QMenu::item {{ padding: 6px 24px 6px 14px; border-radius: 4px; }}
    QMenu::item:selected {{ background: {BTN}; color: {BRASS}; }}
    QMenu::separator {{ height: 1px; background: {PLANK_EDGE}; margin: 4px 8px; }}
    QToolTip {{
        background: {BAR}; color: {BRASS}; border: 1px solid {PLANK_EDGE};
        padding: 6px 10px; border-radius: 5px;
    }}
    QSplitter::handle {{ background: {PLANK_EDGE}; }}
    QGroupBox {{
        border: 1px solid {PLANK_EDGE}; border-radius: 6px; margin-top: 14px;
        padding: 14px 10px 8px 10px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin; left: 12px; padding: 0 5px; color: {BRASS_DIM};
    }}
    """


def make_button(text: str, on_click=None, primary: bool = False,
                dim: bool = False, parent=None, scale: float = 1.0,
                role: str | None = None, checkable: bool = False):
    """按主题生成带 hover 效果的 QPushButton（通过 role 属性驱动 QSS）。"""
    from PyQt5.QtWidgets import QPushButton
    b = QPushButton(text, parent)
    r = role or ("primary" if primary else ("dim" if dim else ""))
    if r:
        b.setProperty("role", r)
    if checkable:
        b.setCheckable(True)
    b.setFont(font(10, scale=scale))
    b.setCursor(_pointing_hand())
    if on_click is not None:
        b.clicked.connect(on_click)
    return b


def _pointing_hand():
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QCursor
    return QCursor(Qt.PointingHandCursor)


def refresh_widget(w):
    """属性（如 role）变更后重刷 QSS。"""
    try:
        w.style().unpolish(w)
        w.style().polish(w)
        w.update()
    except Exception:
        pass


# ---------------- 程序化纹理（纯 PIL） ----------------
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


def _noise(w: int, h: int, cell: int, seed: int) -> Image.Image:
    rnd = random.Random(seed)
    sw, sh = max(w // cell, 2), max(h // cell, 2)
    small = Image.frombytes("L", (sw, sh),
                            bytes(rnd.randrange(256) for _ in range(sw * sh)))
    return small.resize((w, h), Image.BICUBIC).filter(
        ImageFilter.GaussianBlur(radius=cell * 0.7))


def wood_background(w: int, h: int, seed: int = 11) -> Image.Image:
    """木纹背景：纵向明暗渐变 + 年轮曲线 + 细颗粒 + 暗角（颜色随主题）。"""
    w, h = max(w, 2), max(h, 2)
    # 纵向渐变：先在 1×256 上生成再放大，避免逐行（h 次）绘图调用
    top, bottom = WOOD_TOP, WOOD_BOTTOM
    col = Image.new("RGB", (1, 256))
    cpx = col.load()
    for i in range(256):
        t = i / 255.0
        cpx[0, i] = tuple(int(top[k] + (bottom[k] - top[k]) * t)
                          for k in range(3))
    img = col.resize((w, h), Image.BILINEAR)
    d = ImageDraw.Draw(img)
    # 年轮：缓慢起伏的深浅木纹
    rnd = random.Random(seed)
    ring_step = max(6, h // 900)
    for i in range(0, h + 40, ring_step):
        base_y = i + rnd.randint(-2, 2)
        amp = rnd.uniform(1.5, 4.5)
        phase = rnd.uniform(0, math.tau)
        dark = rnd.random() < 0.5
        col_ring = WOOD_RING_DARK if dark else WOOD_RING_LIGHT
        pts = [(x, base_y + amp * math.sin(phase + x / rnd.uniform(160, 420)))
               for x in range(0, w + 12, 12)]
        d.line(pts, fill=col_ring, width=1)
    img = img.filter(ImageFilter.GaussianBlur(radius=0.5))
    # 颗粒
    grain = _noise(w, h, 3, seed + 5)
    img = Image.blend(img, Image.merge("RGB", (grain, grain, grain)), 0.045)
    # 暗角
    vig = Image.new("L", (w, h), 0)
    ImageDraw.Draw(vig).ellipse([-w * 0.25, -h * 0.35, w * 1.25, h * 1.35], fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(w * 0.08))
    dark = Image.new("RGB", (w, h), WOOD_VIGNETTE)
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


# ---------------- 封面渲染（纯 PIL） ----------------
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
