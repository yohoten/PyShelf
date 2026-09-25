# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：python -m PyInstaller PyShelf.spec --noconfirm

产物：dist/PyShelf.exe（单文件，约 40MB）
说明：
- tkinter / PyMuPDF / Pillow 均可被 PyInstaller 自动收集；
- data/ 目录不打包（首次运行自动创建；用户数据与 exe 分离）；
- app.ico 由 spec 直接嵌入。
"""
import sys

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=["pymupdf", "PIL._tkinter_finder"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tests"],
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
    upx=False,
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
