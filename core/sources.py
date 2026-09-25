"""书源引擎：GitHub 开源书库 / arXiv / Gutenberg 公版书 / URL 直链 / 自定义 JSON 书源。

设计原则
- 只内置可合法公开获取的资源站点（开源仓库、预印本、公版书、用户自有直链）。
- 自定义书源为通用规则引擎（搜索 URL 模板 + 字段路径映射），由用户自行配置并
  对其所选站点的内容合法性负责。
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

UA = {"User-Agent": "PyShelf/1.1 (+desktop reader)"}
BOOK_EXTS = {".pdf", ".epub", ".txt", ".md", ".mobi", ".azw3", ".djvu"}
SKIP_FILENAMES = {"readme", "license", "changelog", "contributing", "notice"}


@dataclass
class RemoteBook:
    title: str
    source: str
    fmt: str = ""
    author: str = ""
    size: str = ""
    url: str = ""            # 可下载地址；kind='repo' 时为空
    kind: str = "file"       # file | repo
    repo: str = ""           # GitHub 仓库 full_name
    ref: str = ""            # 默认分支
    path: str = ""           # 仓库内路径
    note: str = ""
    extras: list = field(default_factory=list)  # 额外可下载候选 (label, url)


class SourceError(Exception):
    pass


_OPENER_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http(url: str, token: str = "", timeout: int = 20) -> bytes:
    headers = dict(UA)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code in (403, 406, 451, 502) and "127.0.0.1" in str(
                urllib.request.getproxies()):
            # 疑似本地代理拦截（空 406 等）：绕过系统代理直连重试
            with _OPENER_NO_PROXY.open(req, timeout=timeout) as r:
                return r.read()
        raise
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        # 网络层失败：绕过系统代理直连重试
        with _OPENER_NO_PROXY.open(req, timeout=timeout) as r:
            return r.read()


def _http_json(url: str, token: str = "", timeout: int = 20):
    return json.loads(_http(url, token, timeout).decode("utf-8", "replace"))


# --------------------------------------------------------------------------- #
# 1. GitHub 开源书库
# --------------------------------------------------------------------------- #
class GitHubSource:
    name = "GitHub 开源书库"
    hint = "搜索 GitHub 上的开源书籍仓库，进入仓库后可挑选 PDF/EPUB/TXT 文件下载"

    def __init__(self, token: str = ""):
        self.token = token

    def search(self, query: str) -> list[RemoteBook]:
        q = urllib.parse.quote(f"{query} in:name,description,readme")
        url = (f"https://api.github.com/search/repositories?q={q}"
               f"&per_page=20")
        data = _http_json(url, self.token)
        out = []
        for item in data.get("items", []):
            out.append(RemoteBook(
                title=item["full_name"],
                source=self.name,
                fmt="仓库",
                author=item.get("owner", {}).get("login", ""),
                size=f"★{item.get('stargazers_count', 0)}",
                kind="repo",
                repo=item["full_name"],
                ref=item.get("default_branch", "main"),
                note=(item.get("description") or "")[:60],
            ))
        return out

    def list_files(self, repo: str, ref: str) -> list[RemoteBook]:
        url = f"https://api.github.com/repos/{repo}/git/trees/{ref}?recursive=1"
        data = _http_json(url, self.token)
        out = []
        for node in data.get("tree", []):
            if node.get("type") != "blob":
                continue
            p = node["path"]
            ext = Path(p).suffix.lower()
            if ext not in BOOK_EXTS:
                continue
            if Path(p).stem.lower() in SKIP_FILENAMES:
                continue
            size_kb = node.get("size", 0) / 1024
            out.append(RemoteBook(
                title=Path(p).stem,
                source=self.name, fmt=ext.lstrip(".").upper(),
                author=repo.split("/")[0],
                size=(f"{size_kb/1024:.1f} MB" if size_kb > 1024
                      else f"{size_kb:.0f} KB"),
                url=f"https://cdn.jsdelivr.net/gh/{repo}@{ref}/{urllib.parse.quote(p)}",
                repo=repo, ref=ref, path=p,
                note=p,
                extras=[("raw 备用通道",
                         f"https://raw.githubusercontent.com/{repo}/{ref}/"
                         f"{urllib.parse.quote(p)}")],
            ))
        out.sort(key=lambda b: (-_num(b.size), b.title))
        return out


def _num(size_str: str) -> float:
    m = re.match(r"([\d.]+)", size_str or "")
    if not m:
        return 0
    v = float(m.group(1))
    return v * 1024 if "MB" in size_str else v


# --------------------------------------------------------------------------- #
# 2. arXiv 预印本
# --------------------------------------------------------------------------- #
class ArxivSource:
    name = "arXiv 论文"
    hint = "检索 arXiv 预印本论文，直接下载 PDF（官方要求请求间隔 ≥3 秒）"

    def search(self, query: str) -> list[RemoteBook]:
        q = urllib.parse.quote_plus(query)   # 空格转 +，保留 all: 前缀不编码
        url = (f"https://export.arxiv.org/api/query?search_query=all:{q}"
               f"&start=0&max_results=20&sortBy=relevance")
        raw = None
        for i, wait in enumerate((0, 5, 12)):   # 406 = 被限流，退避重试
            if wait:
                time.sleep(wait)
            try:
                raw = _http(url, timeout=30).decode("utf-8", "replace")
                break
            except urllib.error.HTTPError as e:
                if e.code == 406 and i < 2:
                    continue
                raise SourceError(
                    "arXiv 暂时限流（HTTP 406），请约 1 分钟后重试") from e
        ns = {"a": "http://www.w3.org/2005/Atom"}
        out = []
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as e:
            raise SourceError(f"arXiv 返回解析失败：{e}")
        for entry in root.findall("a:entry", ns):
            title = " ".join((entry.findtext("a:title", "", ns) or "").split())
            authors = [a.findtext("a:name", "", ns)
                       for a in entry.findall("a:author", ns)]
            pdf, alt = "", []
            for link in entry.findall("a:link", ns):
                if link.get("title") == "pdf":
                    pdf = link.get("href", "")
                elif link.get("rel") == "alternate":
                    alt.append(("摘要页", link.get("href", "")))
            published = (entry.findtext("a:published", "", ns) or "")[:10]
            out.append(RemoteBook(
                title=title or "(无标题)", source=self.name, fmt="PDF",
                author=", ".join(authors[:3]) + ("等" if len(authors) > 3 else ""),
                size=published, url=pdf, note=published, extras=alt))
        return out


# --------------------------------------------------------------------------- #
# 3. Gutenberg 公版书（按书号，官方直连，不做搜索页爬取）
# --------------------------------------------------------------------------- #
class GutenbergSource:
    name = "Gutenberg 公版书"
    hint = "输入 Gutenberg 书号（如 1342）或书籍页链接，下载公版 TXT/EPUB"

    def search(self, query: str) -> list[RemoteBook]:
        m = re.search(r"(\d{2,7})", query or "")
        if not m:
            raise SourceError("请输入数字书号，例如 1342（《傲慢与偏见》）")
        bid = m.group(1)
        txt = f"https://www.gutenberg.org/cache/epub/{bid}/pg{bid}.txt"
        epub = f"https://www.gutenberg.org/ebooks/{bid}.epub3.images"
        return [RemoteBook(
            title=f"Gutenberg #{bid}", source=self.name, fmt="TXT",
            author="Project Gutenberg", size="", url=txt, note=f"书号 {bid}",
            extras=[("EPUB 版本", epub),
                    ("书籍页", f"https://www.gutenberg.org/ebooks/{bid}")])]


# --------------------------------------------------------------------------- #
# 4. URL 直链导入
# --------------------------------------------------------------------------- #
class DirectURLSource:
    name = "URL 直链导入"
    hint = "粘贴 PDF/EPUB/TXT 的 http(s) 直链直接入库"

    def search(self, query: str) -> list[RemoteBook]:
        url = (query or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            raise SourceError("请输入完整的 http(s) 直链")
        name = Path(urllib.parse.urlparse(url).path).name or "网络文件"
        ext = Path(name).suffix.lower().lstrip(".") or "未知"
        return [RemoteBook(title=Path(name).stem or name, source=self.name,
                            fmt=ext.upper(), author="", url=url, note=url)]


# --------------------------------------------------------------------------- #
# 5. 自定义 JSON 书源（规则引擎）
# --------------------------------------------------------------------------- #
class CustomSource:
    hint = "自建规则：搜索 URL 模板 + 结果字段路径映射"

    def __init__(self, spec: dict):
        self.spec = spec
        self.name = spec.get("name") or "自定义书源"

    def search(self, query: str) -> list[RemoteBook]:
        spec = self.spec
        url_tpl = spec.get("search_url", "")
        if "{q}" not in url_tpl:
            raise SourceError("search_url 必须包含 {q} 占位符")
        url = url_tpl.replace("{q}", urllib.parse.quote(query or ""))
        data = _http_json(url, spec.get("token", ""))
        items = _dig(data, spec.get("results_path", ""))
        if not isinstance(items, list):
            raise SourceError(f"results_path 未取到数组：{spec.get('results_path')!r}")
        out = []
        for it in items[:30]:
            def f(key):
                v = _dig(it, spec.get(key, "")) if spec.get(key) else None
                return "" if v is None else str(v)
            dl = f("download_url")
            if dl and not dl.lower().startswith("http"):
                base = spec.get("base_url", "")
                dl = urllib.parse.urljoin(base, dl)
            out.append(RemoteBook(
                title=f("title") or "(无标题)", source=self.name,
                author=f("author"), fmt=(f("format") or "").upper(),
                size=f("size"), url=dl, note=spec.get("note", "")))
        return out


def _dig(obj, path: str):
    """支持 'a.b.0.c' 形式路径；空路径返回原对象。"""
    if not path:
        return obj
    cur = obj
    for part in path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #
def load_sources(settings: dict) -> list:
    gh_token = settings.get("github_token", "")
    sources = [GitHubSource(gh_token), ArxivSource(),
               GutenbergSource(), DirectURLSource()]
    for spec in settings.get("custom_sources", []):
        try:
            sources.append(CustomSource(spec))
        except Exception:
            continue
    return sources
