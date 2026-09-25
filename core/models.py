"""数据模型（独立于存储层，避免 library ↔ storage 循环导入）。"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path


def book_id_for(path: Path) -> str:
    return hashlib.md5(str(path).encode("utf-8")).hexdigest()


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
    fingerprint: str = ""      # "mtime_ns:size"，扫描增量化用（未变更则跳过探测）
