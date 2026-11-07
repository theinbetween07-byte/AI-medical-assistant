"""静态资源版本号：按文件内容哈希生成。

历史问题（2026-11-07 修复）：
    index.html 里原先写死 `?v=20261106`，而静态资源的响应只有 ETag/Last-Modified、
    没有 Cache-Control。浏览器于是按 Last-Modified 做启发式缓存（新鲜期约为文件年龄的
    1/10，上限 24h）。重新打包或重启服务后文件已更新，但 URL 没变，浏览器继续使用
    磁盘里的旧 app.js / app.css，与新 index.html 的 DOM 结构混搭，表现为「界面元素
    丢失」（新元素由新脚本创建，旧脚本创建不出来）。

    现在版本号由内容哈希决定：只要 static 下的文件变了，URL 就变，浏览器必然重新下载；
    首页本身返回 no-store，保证 HTML 与资源版本永远同代。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# 参与版本计算的资源；新增静态资源时加到这里即可。
TRACKED = ("app.css", "app-extra.css", "app.js")

_cache_key: tuple | None = None
_cache_value = ""


def build_version(static_dir: Path) -> str:
    """返回 12 位内容哈希；文件未修改时复用上次计算结果。"""
    global _cache_key, _cache_value

    paths = [static_dir / name for name in TRACKED]
    key = tuple(
        (p.name, p.stat().st_mtime_ns, p.stat().st_size) if p.is_file() else (p.name, 0, -1)
        for p in paths
    )
    if key == _cache_key:
        return _cache_value

    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes() if path.is_file() else b"")
    _cache_value = digest.hexdigest()[:12]
    _cache_key = key
    return _cache_value
