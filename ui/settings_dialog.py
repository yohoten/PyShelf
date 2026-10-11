"""设置中心：外观 / 字体 / 阅读 / 性能 / 书源。

- 大多数项保存后**立即生效**（封面尺寸、分区、提示、阅读选项、线程数）；
- 主题、字体、字号缩放涉及界面重建，标注为「重启后生效」。
"""
from __future__ import annotations

from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QSpinBox, QTabWidget,
                             QVBoxLayout, QWidget)

from ui import theme as T

THEME_ITEMS = [("深色 · 烛光书房", "dark"), ("浅色 · 午后书房", "light")]
COVER_ITEMS = [("小", "small"), ("中（默认）", "medium"), ("大", "large")]
ZOOM_ITEMS = [("适配整页（默认）", "fit_page"),
              ("适配页宽", "fit_width"),
              ("原始 100%", "100%")]
SS_ITEMS = [("2x 更清晰（默认）", 2), ("1x 更省内存", 1)]


class SettingsDialog(QDialog):
    """返回 `result_settings`（保存后的完整设置字典），取消则为 None。"""

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.s = getattr(app, "scale", 1.0)
        self.result_settings: dict | None = None
        self._cfg = dict(app.settings)

        self.setWindowTitle("设置")
        self.setModal(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        tabs = QTabWidget()
        tabs.addTab(self._tab_appearance(), " 外观 ")
        tabs.addTab(self._tab_font(), " 字体 ")
        tabs.addTab(self._tab_reading(), " 阅读 ")
        tabs.addTab(self._tab_perf(), " 性能 ")
        tabs.addTab(self._tab_sources(), " 书源 ")
        root.addWidget(tabs)

        tip = QLabel("提示：主题、字体与字号缩放将在**重启后**生效；其余项保存后立即生效。")
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color:{T.MUTED};")
        root.addWidget(tip)

        box = QDialogButtonBox()
        reset = QPushButton("恢复默认")
        box.addButton(reset, QDialogButtonBox.ResetRole)
        cancel = box.addButton("取消", QDialogButtonBox.RejectRole)
        save = box.addButton("保存并应用", QDialogButtonBox.AcceptRole)
        save.setProperty("role", "primary")
        reset.clicked.connect(self._reset)
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._save)
        root.addWidget(box)

    # ---------------- 各选项卡 ----------------
    def _combo(self, items) -> QComboBox:
        cb = QComboBox()
        for label, value in items:
            cb.addItem(label, value)
        return cb

    def _group(self, title):
        g = QGroupBox(title)
        f = QFormLayout(g)
        f.setContentsMargins(12, 14, 12, 10)
        f.setSpacing(9)
        return g, f

    def _select(self, cb: QComboBox, value):
        i = cb.findData(value)
        if i >= 0:
            cb.setCurrentIndex(i)

    def _tab_appearance(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 12, 10, 10)

        g1, f1 = self._group("主题")
        self.cb_theme = self._combo(THEME_ITEMS)
        self._select(self.cb_theme, self._cfg.get("theme", "dark"))
        f1.addRow("配色主题", self.cb_theme)
        lay.addWidget(g1)

        g2, f2 = self._group("书架")
        self.cb_cover = self._combo(COVER_ITEMS)
        self._select(self.cb_cover, self._cfg.get("cover_size", "medium"))
        f2.addRow("封面尺寸", self.cb_cover)
        self.chk_recent = QCheckBox("显示「最近阅读」分区")
        self.chk_recent.setChecked(bool(self._cfg.get("show_recent", True)))
        self.chk_fav = QCheckBox("显示「收藏」分区")
        self.chk_fav.setChecked(bool(self._cfg.get("show_favorites", True)))
        self.chk_tip = QCheckBox("悬停显示书籍信息提示")
        self.chk_tip.setChecked(bool(self._cfg.get("show_tooltips", True)))
        f2.addRow(self.chk_recent)
        f2.addRow(self.chk_fav)
        f2.addRow(self.chk_tip)
        lay.addWidget(g2)
        lay.addStretch(1)
        return w

    def _tab_font(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 12, 10, 10)

        g, f = self._group("字体（重启后生效）")
        self.cb_ui_font = self._combo([(x, x) for x in T.UI_FONT_CHOICES])
        self._select(self.cb_ui_font, self._cfg.get("ui_font", "Microsoft YaHei"))
        f.addRow("界面字体", self.cb_ui_font)
        self.cb_title_font = self._combo([(x, x) for x in T.TITLE_FONT_CHOICES])
        self._select(self.cb_title_font, self._cfg.get("title_font", "KaiTi"))
        f.addRow("标题字体", self.cb_title_font)
        self.sp_scale = QSpinBox()
        self.sp_scale.setRange(60, 200)
        self.sp_scale.setSingleStep(5)
        self.sp_scale.setSuffix(" %")
        self.sp_scale.setValue(int(round(float(self._cfg.get("font_scale", 1.0)) * 100)))
        f.addRow("字号缩放", self.sp_scale)
        lay.addWidget(g)

        preview = QLabel("预览：Py 书斋 · 拟物复古书架阅读器 0123")
        preview.setFont(T.font(13, scale=self.s))
        self.sp_scale.valueChanged.connect(
            lambda v: preview.setStyleSheet(
                f"font-family:'{self.cb_ui_font.currentData()}';"
                f"font-size:{max(int(13 * v / 100), 8)}px;color:{T.BRASS};"))
        self.cb_ui_font.currentIndexChanged.connect(
            lambda _i: preview.setStyleSheet(
                f"font-family:'{self.cb_ui_font.currentData()}';"
                f"font-size:{max(int(13 * self.sp_scale.value() / 100), 8)}px;"
                f"color:{T.BRASS};"))
        lay.addWidget(preview)
        lay.addStretch(1)
        return w

    def _tab_reading(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 12, 10, 10)

        g, f = self._group("阅读器")
        self.cb_zoom = self._combo(ZOOM_ITEMS)
        self._select(self.cb_zoom, self._cfg.get("default_zoom", "fit_page"))
        f.addRow("打开时缩放", self.cb_zoom)
        self.chk_candle = QCheckBox("打开书籍即启用烛光模式")
        self.chk_candle.setChecked(bool(self._cfg.get("candle_default", False)))
        self.chk_wheel = QCheckBox("滚轮翻页（按住 Ctrl 滚轮缩放）")
        self.chk_wheel.setChecked(bool(self._cfg.get("wheel_turn", True)))
        f.addRow(self.chk_candle)
        f.addRow(self.chk_wheel)
        lay.addWidget(g)
        lay.addStretch(1)
        return w

    def _tab_perf(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 12, 10, 10)

        g, f = self._group("渲染")
        self.cb_ss = self._combo(SS_ITEMS)
        self._select(self.cb_ss, int(self._cfg.get("supersample", 2)))
        f.addRow("页面渲染质量", self.cb_ss)
        self.sp_threads = QSpinBox()
        self.sp_threads.setRange(1, 8)
        self.sp_threads.setValue(int(self._cfg.get("render_threads", 2)))
        f.addRow("后台渲染线程数", self.sp_threads)
        lay.addWidget(g)

        note = QLabel("封面与页面位图在后台线程合成，主线程只负责显示；"
                      "书库很大时可适当提高线程数。")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{T.MUTED};")
        lay.addWidget(note)
        lay.addStretch(1)
        return w

    def _tab_sources(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 12, 10, 10)

        g, f = self._group("书源")
        self.ed_token = QLineEdit(self._cfg.get("github_token", ""))
        self.ed_token.setEchoMode(QLineEdit.Password)
        self.ed_token.setPlaceholderText("可选，用于提升 GitHub API 限额")
        f.addRow("GitHub Token", self.ed_token)
        lay.addWidget(g)

        row = QHBoxLayout()
        row.addWidget(T.make_button("管理书籍目录…", self.app._manage_dirs,
                                    scale=self.s))
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)
        return w

    # ---------------- 动作 ----------------
    def _defaults(self) -> dict:
        from app import DEFAULT_SETTINGS
        return DEFAULT_SETTINGS

    def _reset(self):
        d = self._defaults()
        self._select(self.cb_theme, d["theme"])
        self._select(self.cb_cover, d["cover_size"])
        self.chk_recent.setChecked(d["show_recent"])
        self.chk_fav.setChecked(d["show_favorites"])
        self.chk_tip.setChecked(d["show_tooltips"])
        self._select(self.cb_ui_font, d["ui_font"])
        self._select(self.cb_title_font, d["title_font"])
        self.sp_scale.setValue(int(d["font_scale"] * 100))
        self._select(self.cb_zoom, d["default_zoom"])
        self.chk_candle.setChecked(d["candle_default"])
        self.chk_wheel.setChecked(d["wheel_turn"])
        self._select(self.cb_ss, d["supersample"])
        self.sp_threads.setValue(d["render_threads"])
        self.ed_token.setText(d["github_token"])

    def _save(self):
        cfg = dict(self._cfg)
        cfg.update({
            "theme": self.cb_theme.currentData(),
            "cover_size": self.cb_cover.currentData(),
            "show_recent": self.chk_recent.isChecked(),
            "show_favorites": self.chk_fav.isChecked(),
            "show_tooltips": self.chk_tip.isChecked(),
            "ui_font": self.cb_ui_font.currentData(),
            "title_font": self.cb_title_font.currentData(),
            "font_scale": round(self.sp_scale.value() / 100.0, 2),
            "default_zoom": self.cb_zoom.currentData(),
            "candle_default": self.chk_candle.isChecked(),
            "wheel_turn": self.chk_wheel.isChecked(),
            "supersample": int(self.cb_ss.currentData()),
            "render_threads": int(self.sp_threads.value()),
            "github_token": self.ed_token.text().strip(),
        })
        self.result_settings = cfg
        self.accept()
