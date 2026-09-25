"""下载器：后台线程下载书籍到本地书库，支持备用通道与进度回报。"""
from __future__ import annotations

import os
import queue
import threading
import urllib.parse
import urllib.request
from pathlib import Path

UA = {"User-Agent": "PyShelf/1.1 (+desktop reader)"}
_OPENER_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _open(url: str, timeout: int = 30):
    """优先走系统代理；被代理拦截（空 406/403/451/502）或网络失败时直连重试。"""
    req = urllib.request.Request(url, headers=UA)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code in (403, 406, 451, 502) and "127.0.0.1" in str(
                urllib.request.getproxies()):
            return _OPENER_NO_PROXY.open(req, timeout=timeout)
        raise
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return _OPENER_NO_PROXY.open(req, timeout=timeout)


def safe_name(name: str) -> str:
    name = urllib.parse.unquote(name)
    for ch in '\\/:*?"<>|':
        name = name.replace(ch, "_")
    return name.strip()[:120] or "网络书籍"


class Downloader:
    """把 RemoteBook 下载到目标目录；进度通过 queue 汇报给 UI。"""

    def __init__(self, q: queue.Queue):
        self.q = q
        self._lock = threading.Lock()

    def start(self, title: str, url: str, fmt: str,
              target_dir: Path, fallbacks: list[tuple[str, str]] | None = None):
        t = threading.Thread(target=self._run,
                             args=(title, url, fmt, Path(target_dir), fallbacks or []),
                             daemon=True)
        t.start()

    def _run(self, title, url, fmt, target_dir, fallbacks):
        target_dir.mkdir(parents=True, exist_ok=True)
        ext = f".{fmt.lower()}" if fmt and fmt.lower() in (
            "pdf", "epub", "txt", "md") else Path(
            urllib.parse.urlparse(url).path).suffix or ".bin"
        if not title.lower().endswith(ext):
            fname = safe_name(title) + ext
        else:
            fname = safe_name(title)
        dest = target_dir / fname
        i = 1
        while dest.exists():
            dest = target_dir / f"{Path(fname).stem}_{i}{Path(fname).suffix}"
            i += 1

        candidates = [(title, url)] + list(fallbacks)
        last_err = None
        for label, u in candidates:
            try:
                self._fetch(u, dest)
                self.q.put(("dl_done", str(dest), title))
                return
            except Exception as e:
                last_err = e
        self.q.put(("dl_fail", title, str(last_err)))

    def _fetch(self, url: str, dest: Path):
        tmp = dest.with_suffix(dest.suffix + ".part")
        with _open(url) as r:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            with open(tmp, "wb") as f:
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    pct = int(got * 100 / total) if total else 0
                    self.q.put(("dl_progress", dest.name, pct,
                                f"{got/1048576:.1f}MB"))
        if tmp.stat().st_size < 1024:
            tmp.unlink(missing_ok=True)
            raise IOError("文件过小，可能不是有效书籍文件")
        os.replace(tmp, dest)
