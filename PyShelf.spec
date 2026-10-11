# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：python -m PyInstaller PyShelf.spec --noconfirm

产物：dist/PyShelf.exe（单文件，约 40MB）
说明：
- PyQt5 / PyMuPDF / Pillow 均可被 PyInstaller 自动收集（PyQt5 需 pip 安装）；
- data/ 目录不打包（首次运行自动创建；用户数据与 exe 分离）；
- app.ico 由 spec 直接嵌入。
"""
import sys

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=["pymupdf", "PyQt5", "PyQt5.QtCore", "PyQt5.QtGui",
                   "PyQt5.QtWidgets", "PyQt5.sip"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tests", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PyShelf",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX 需「可用 + 开启」双条件才生效（PyInstaller 内部：
    # upx = CONF['upx_available'] and kwargs.get('upx')）：
    #   · 可用 —— 由 build.py 传入 --upx-dir H:\UPX（或 UPX 在 PATH 中）决定；
    #   · 开启 —— 即此处 upx=True。若 UPX 不可用则自动跳过，不会报错。
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                 # pythonw 语义：无控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="app.ico" if sys.platform == "win32" else None,
)
