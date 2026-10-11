"""数据模型（独立于存储层，避免 library ↔ storage 循环导入）。"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

# 说明：路径规范化（normcase）会让「同一文件的不同录入写法」收敛为同一形式，
# 是「id 不漂移 / 记忆不丢失」的基石（F-1）。放在 models 便于 scanner 轻量复用。

def _norm_dir(p) -> str:
    """规范化路径用于匹配/去重/身份键（Windows 大小写不敏感 → normcase）。"""
    if not p:
        return ""
    return os.path.normcase(os.path.normpath(os.path.abspath(str(p))))


def book_id_for(path) -> str:
    """由**规范化路径**派生稳定 id：大小写/相对写法差异不再造成 id 漂移。"""
    return hashlib.md5(_norm_dir(path).encode("utf-8")).hexdigest()


@dataclass
class Bookmark:
    page: int
    note: str = ""


@dataclass
class Highlight:
    page: int
    rect: tuple  # (x0, y0, x1, y1) in PDF page coordinates (points)
    note: str = ""


@dataclass
class Book:
    id: str
    path: str
    title: str
    ext: str
    pages: int = 0
    favorite: bool = False
    progress: int = 0          # 已读到的页（1-based；0 = 未读）
    last_opened: float = 0.0
    bookmarks: list = field(default_factory=list)   # list[Bookmark]
    highlights: list = field(default_factory=list)  # list[Highlight]
    cover_ok: bool = False     # 封面缓存是否已生成
    readable: bool = True      # 引擎是否能打开
    fingerprint: str = ""      # "mtime_ns:size"，内容身份键（增量探测 + 记忆重绑）
    present: bool = True       # 本次扫描是否在磁盘命中；False = 失联(隐藏但保留记忆)
    title_custom: bool = False  # 标题是否被用户自定义；True 时重扫不回退为文件名
