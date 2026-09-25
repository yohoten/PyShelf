@echo off
setlocal
chcp 936 >nul
title Py 书斋
cd /d "%~dp0"

rem =====================================================
rem  Py 书斋启动脚本
rem  用法:  launcher.bat          正常启动（无控制台）
rem         launcher.bat debug    控制台运行，报错可见，可接 --smoke 等参数
rem =====================================================

if not exist "app.py" (
    echo [错误] 未找到 app.py，请将本脚本放在程序目录内运行。
    pause
    exit /b 1
)

rem ---------- 探测解释器（优先本机已装 PyMuPDF 的 Python 3.12） ----------
set "PYW="
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
    "%ProgramFiles%\Python312\pythonw.exe"
    "%ProgramFiles%\Python313\pythonw.exe"
    "%ProgramFiles%\Python311\pythonw.exe"
) do (
    if not defined PYW if exist %%P set "PYW=%%~P"
)
if not defined PYW (
    for /f "delims=" %%P in ('where pythonw.exe 2^>nul') do (
        if not defined PYW set "PYW=%%P"
    )
)
if not defined PYW (
    for /f "delims=" %%P in ('where pyw.exe 2^>nul') do (
        if not defined PYW set "PYW=%%P"
    )
)
if not defined PYW (
    echo [错误] 未找到 pythonw.exe。
    echo        请安装 Python 3.11 或更高版本（安装时勾选 tcl/tk），
    echo        或运行 launcher.bat debug 查看详细信息。
    pause
    exit /b 1
)

if /i "%~1"=="debug" goto debug

rem ---------- 正常启动（pythonw 无控制台；异常会记录到 data\crash.log） ----------
start "Py 书斋" "%PYW%" "%~dp0app.py"
exit /b 0

:debug
set "PYD=%PYW:pythonw.exe=python.exe%"
if exist "%PYD%" (set "RUN=%PYD%") else set "RUN=%PYW%"
echo [调试] 解释器: %RUN%
echo [调试] 启动 app.py，关闭窗口或按 Ctrl+C 结束……
echo.
"%RUN%" app.py %2 %3 %4
echo.
echo [调试] 程序已退出，退出码 %errorlevel%。
echo [调试] 若为异常退出，详见 data\crash.log
pause
exit /b 0
