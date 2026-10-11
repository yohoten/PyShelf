#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Py 书斋（PyShelf）一键打包脚本。

用途
----
在 Windows 下把项目打包为免安装的单文件 `dist/PyShelf.exe`。脚本会自动完成：

  1. 环境自检   —— Python 版本、目标源文件、图标、spec 是否齐备；
  2. 依赖自检   —— 检查 PyQt5 / PyMuPDF / Pillow / PyInstaller 是否可导入；
  3. UPX 解析   —— 定位 upx.exe 并通过 `--upx-dir` 交给 PyInstaller；
  4. 可选测试   —— 传 `--test` 时先跑 `python -m pytest tests/`（失败即中止打包）；
  5. 清理旧产物 —— 默认清理 `build/` 中间目录（可用 `--no-clean` 关闭）；
  6. 调用打包   —— `python -m PyInstaller PyShelf.spec --noconfirm [--clean]`
                   `[--upx-dir <DIR>]`，输出实时透传并落盘 `build/pyinstaller.log`；
  7. 结果校验   —— 确认 `dist/PyShelf.exe` 生成、打印体积与 UPX 生效统计。

用法
----
    python build.py                                  # 标准打包（默认使用 H:\\UPX）
    python build.py --upx-dir "D:\\Tools\\upx"        # 指定 UPX 目录
    python build.py --no-upx                         # 关闭 UPX 压缩
    python build.py --test                           # 打包前先跑单元测试
    python build.py --no-clean                       # 保留 build/ 增量加速
    python build.py --skip-deps                      # 跳过依赖检查
    python build.py -h                               # 查看全部参数

UPX 说明
--------
PyInstaller 的压缩需**「可用 + 开启」双条件**才生效（内部：
`upx = CONF['upx_available'] and kwargs.get('upx')`）：

- 可用 —— 本脚本找到 `upx.exe` 后传入 `--upx-dir`（未找到则告警并跳过，不报错）；
- 开启 —— [`PyShelf.spec`](PyShelf.spec) 的 `EXE(upx=True)`（已置 True）。

压缩对象是 PyInstaller **收集到的二进制**（Qt/Python 的 DLL、`.pyd`），
bootloader 本体不压缩；PyInstaller 会自动豁免 CFG 保护的二进制与 Qt 插件
（`upx_exclude` 亦可追加）。UPX 结果带缓存，重复打包时命中缓存无需重压。
注意：加壳后杀毒软件误报率上升，对外发布前建议实测。

退出码
------
0 成功；1 环境自检失败；2 依赖缺失；3 测试失败；4 PyInstaller 失败；
5 产物校验失败；6 `--upx-dir` 指定路径下无 upx.exe。
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
SPEC_FILE = APP_DIR / "PyShelf.spec"
ENTRY_FILE = APP_DIR / "app.py"
ICON_FILE = APP_DIR / "app.ico"
BUILD_DIR = APP_DIR / "build"
DIST_DIR = APP_DIR / "dist"
BUILD_LOG = BUILD_DIR / "pyinstaller.log"
EXE_NAME = "PyShelf.exe"

# 本机 UPX 安装位置；可用 --upx-dir 覆盖，或 --no-upx 关闭
UPX_DEFAULT_DIR = Path(r"H:\UPX")
UPX_EXE_NAME = "upx.exe"

# PyInstaller 打包时预期可导入的运行时依赖（模块名 -> pip 安装名）
RUNTIME_DEPS = {
    "PyQt5": "PyQt5",
    "fitz": "PyMuPDF",       # import 名为 fitz
    "PIL": "Pillow",
}
BUILD_TOOL = "PyInstaller"
BUILD_TOOL_PIP = "pyinstaller"

MIN_PYTHON = (3, 8)


# --------------------------------------------------------------------------- #
# 日志
# --------------------------------------------------------------------------- #
def _stamp() -> str:
    return time.strftime("%H:%M:%S")


def log(msg: str = "") -> None:
    _echo(f"[{_stamp()}] {msg}\n")


def step(msg: str) -> None:
    _echo(f"\n[{_stamp()}] === {msg} ===\n")


def warn(msg: str) -> None:
    _echo(f"[{_stamp()}] [警告] {msg}\n")


def fail(msg: str) -> None:
    _echo(f"[{_stamp()}] [错误] {msg}\n")


def _echo(text: str) -> None:
    """输出到控制台；遇到无法用当前代码页编码的字符时降级替换，绝不中断构建。"""
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        sys.stdout.buffer.write(text.encode(enc, "replace"))
        sys.stdout.buffer.flush()


# --------------------------------------------------------------------------- #
# 环境检查
# --------------------------------------------------------------------------- #
def check_python() -> bool:
    if sys.version_info < MIN_PYTHON:
        fail(f"Python 版本过低：需要 >= "
             f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}，当前为 "
             f"{sys.version_info.major}.{sys.version_info.minor}。")
        return False
    log(f"Python {sys.version.split()[0]}  ({sys.executable})")
    return True


def check_files() -> bool:
    ok = True
    for f in (SPEC_FILE, ENTRY_FILE, ICON_FILE):
        if f.exists():
            log(f"找到 {f.name}")
        else:
            fail(f"缺少必需文件：{f}")
            ok = False
    return ok


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def check_deps(skip: bool) -> bool:
    if skip:
        log("已跳过依赖检查（--skip-deps）")
        return True

    missing: list[str] = []
    for mod, pip_name in RUNTIME_DEPS.items():
        if _module_available(mod):
            log(f"依赖就绪：{pip_name}")
        else:
            warn(f"缺少依赖：{pip_name}（import {mod} 失败）")
            missing.append(pip_name)

    if not _module_available(BUILD_TOOL):
        warn(f"缺少打包工具：{BUILD_TOOL_PIP}")
        missing.append(BUILD_TOOL_PIP)

    if missing:
        fail("以下依赖缺失，请先安装：")
        _echo("        pip install " + " ".join(missing) + "\n")
        return False
    return True


# --------------------------------------------------------------------------- #
# UPX
# --------------------------------------------------------------------------- #
def upx_version(upx_dir: Path) -> str:
    """读取 upx -V 首行版本号（探测可用性，失败返回空串）。"""
    try:
        out = subprocess.run(
            [str(upx_dir / UPX_EXE_NAME), "-V"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in (out.stdout or "").splitlines():
        line = line.strip()
        if line.lower().startswith("upx"):
            return line
    return ""


def resolve_upx(spec_dir: str | None, disabled: bool) -> tuple[Path | None, bool]:
    """定位 UPX 目录，返回 (目录或 None, 是否「显式指定却不可用」)。

    入参 `spec_dir` 可写目录（`H:\\UPX`）或直接指向 `upx.exe`。
    未显式指定且默认目录不存在时仅告警——CI 等无 UPX 环境仍能正常出包
    （spec 里 `upx=True` 在 UPX 不可用时会被 PyInstaller 自动忽略）。
    """
    if disabled:
        log("已按 --no-upx 关闭 UPX 压缩")
        return None, False

    explicit = bool(spec_dir)
    cand = Path(spec_dir) if explicit else UPX_DEFAULT_DIR
    if cand.is_file() and cand.name.lower() == UPX_EXE_NAME:
        cand = cand.parent
    if not (cand / UPX_EXE_NAME).is_file():
        if explicit:
            return None, True
        warn(f"未找到 UPX：{cand / UPX_EXE_NAME}")
        warn("本次打包不做 UPX 压缩；如需启用请用 --upx-dir 指定目录。")
        return None, False

    ver = upx_version(cand)
    if ver:
        log(f"UPX 就绪：{ver}  ({cand / UPX_EXE_NAME})")
    else:
        log(f"UPX 就绪：{cand / UPX_EXE_NAME}")
    return cand, False


# --------------------------------------------------------------------------- #
# 构建步骤
# --------------------------------------------------------------------------- #
def run_tests() -> bool:
    step("运行单元测试")
    cmd = [sys.executable, "-m", "pytest", "tests/", "-v"]
    log("执行：" + " ".join(cmd))
    try:
        rc = subprocess.call(cmd, cwd=str(APP_DIR))
    except FileNotFoundError:
        fail("未找到 pytest，请先执行：pip install pytest")
        return False
    if rc != 0:
        fail(f"测试失败（退出码 {rc}），已中止打包。")
        return False
    log("测试通过。")
    return True


def clean_outputs(no_clean: bool) -> None:
    """清理中间产物。dist/ 始终清理以避免旧 exe 造成误判。"""
    step("清理旧产物")
    if no_clean:
        log("保留 build/（--no-clean）；仅清理 dist/")
    elif BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR, ignore_errors=True)
        log("已删除 build/")
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR, ignore_errors=True)
        log("已删除 dist/")


def build(clean_flag: bool, upx_dir: Path | None) -> tuple[bool, int, int]:
    """调用 PyInstaller；输出实时透传并落盘 build/pyinstaller.log。

    返回 (是否成功, UPX 处理的二进制数, UPX 失败数)：命中数取自日志中的
    `Executing: ...\\upx.exe ...` 行——UPX 只作用于收集到的 DLL/.pyd，
    bootloader 本体不压缩，故该数字即压缩覆盖面；失败数取自
    `Failed to upx ...` 行，用于识别个别 DLL 不被 UPX 支持的情形。
    """
    step("调用 PyInstaller 打包")
    cmd = [sys.executable, "-m", "PyInstaller", str(SPEC_FILE), "--noconfirm"]
    if clean_flag:
        cmd.append("--clean")
    if upx_dir is not None:
        cmd += ["--upx-dir", str(upx_dir)]
    log("执行：" + " ".join(cmd))

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    # 子进程日志强制 UTF-8，避免管道下按 GBK 输出导致中文路径乱码
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    upx_hits = 0
    upx_fails = 0

    with open(BUILD_LOG, "w", encoding="utf-8", errors="replace") as logf:
        proc = subprocess.Popen(
            cmd, cwd=str(APP_DIR), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        assert proc.stdout is not None
        for line in proc.stdout:
            _echo(line)
            logf.write(line)
            low = line.lower()
            if "executing:" in low and "upx" in low:
                upx_hits += 1
            elif "failed to upx" in low:
                upx_fails += 1
        rc = proc.wait()

    if rc != 0:
        fail(f"PyInstaller 退出码 {rc}，打包失败（完整日志：{BUILD_LOG}）。")
        return False, upx_hits, upx_fails
    return True, upx_hits, upx_fails


def verify_output(upx_dir: Path | None, upx_hits: int, upx_fails: int) -> bool:
    step("校验打包结果")
    exe = DIST_DIR / EXE_NAME
    if not exe.exists():
        fail(f"未找到产物：{exe}")
        return False
    size_mb = exe.stat().st_size / (1024 * 1024)
    log(f"产物就绪：{exe}")
    log(f"体积：{size_mb:.1f} MB")

    if upx_dir is not None:
        if upx_hits:
            log(f"UPX 压缩：{upx_dir} —— 已压缩 {upx_hits} 个二进制")
        else:
            warn("已启用 UPX 但日志中未见调用记录（可能全部二进制命中 "
                 "CFG 保护 / Qt 插件 / upx_exclude 豁免规则）。")
        if upx_fails:
            warn(f"其中 {upx_fails} 个二进制 UPX 处理失败（不影响出包，"
                 f"可对照 {BUILD_LOG} 中的 'Failed to upx' 记录确认）。")
        log(f"完整日志：{BUILD_LOG}")
    else:
        log("UPX 压缩：未启用（--no-upx 或未找到 upx.exe）")

    log("提示：运行数据会生成在 exe 同目录的 data/ 下，与程序本体分离。")
    return True


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Py 书斋（PyShelf）一键打包脚本：生成 dist/PyShelf.exe 单文件。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--upx-dir", default=None, metavar="DIR",
                   help=f"UPX 所在目录（默认：{UPX_DEFAULT_DIR}）")
    p.add_argument("--no-upx", action="store_true",
                   help="关闭 UPX 压缩（默认开启）")
    p.add_argument("--test", action="store_true",
                   help="打包前先运行 pytest 单元测试（失败则中止）")
    p.add_argument("--no-clean", action="store_true",
                   help="保留 build/ 中间目录以增量加速（默认会清理）")
    p.add_argument("--skip-deps", action="store_true",
                   help="跳过运行时依赖检查")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    log("Py 书斋 · 打包脚本启动")

    step("环境自检")
    if not (check_python() and check_files()):
        return 1

    step("依赖检查")
    if not check_deps(args.skip_deps):
        return 2

    step("UPX 压缩")
    upx_dir, explicit_bad = resolve_upx(args.upx_dir, args.no_upx)
    if explicit_bad:
        fail(f"--upx-dir 下没有 {UPX_EXE_NAME}：{args.upx_dir}")
        return 6
    if upx_dir is not None:
        log("spec 中 upx=True，PyInstaller 将压缩收集到的 DLL/.pyd。")

    if args.test:
        if not run_tests():
            return 3

    clean_outputs(args.no_clean)

    ok, upx_hits, upx_fails = build(
        clean_flag=not args.no_clean, upx_dir=upx_dir)
    if not ok:
        return 4

    if not verify_output(upx_dir, upx_hits, upx_fails):
        return 5

    log("\n打包完成 ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
