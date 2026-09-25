@echo off
rem PyShelf 打包脚本：生成 dist/PyShelf.exe（需先 pip install pyinstaller pymupdf pillow）
cd /d %~dp0
python -m pip install --upgrade pyinstaller pymupdf pillow
python -m PyInstaller PyShelf.spec --noconfirm
echo.
echo 打包完成：dist/PyShelf.exe
pause
