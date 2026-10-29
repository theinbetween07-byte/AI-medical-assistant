"""运行配置与资源路径解析。

打包为单文件 exe 后，内置资源被解压到 sys._MEIPASS；
同时允许 exe 同目录下的 templates/ 、terms/ 覆盖内置资源，
这样新增报告模板无需重新打包（对齐《技术设计》2.3 声明式模板）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundled_root() -> Path:
    """内置资源根目录（打包后在 _MEIPASS）。"""
    if _frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return PROJECT_DIR


def external_root() -> Path:
    """exe 同目录：允许用户放置自定义模板与术语库。"""
    if _frozen():
        return Path(sys.executable).parent
    return PROJECT_DIR


def _root_candidates() -> tuple[Path, ...]:
    """资源查找的根目录顺序：exe/工程目录优先，其次仓库根（data/terms 所在）。"""
    roots = [external_root()]
    if not _frozen():
        roots.append(external_root().parent)
    return tuple(dict.fromkeys(roots))


def _resolve_dir(name: str, *fallbacks: str) -> Path:
    """解析资源目录。

    依次尝试 name 与 fallbacks（相对候选根目录），
    例如术语库默认在仓库 data/terms，同时兼容旧布局 report-engine/terms。
    """
    override = os.getenv(f"MSHK_{name.upper()}_DIR")
    if override:
        return Path(override).expanduser().resolve()
    for root in _root_candidates():
        for rel in (name,) + fallbacks:
            ext = root / rel
            if ext.is_dir() and any(ext.iterdir()):
                return ext
    for rel in (name,) + fallbacks:
        bundled = bundled_root() / rel
        if bundled.is_dir():
            return bundled
    return bundled_root() / name


class Settings:
    """进程级配置，全部可用环境变量覆盖（便于宿主程序接管）。"""

    def __init__(self) -> None:
        self.host: str = os.getenv("MSHK_HOST", "127.0.0.1")
        self.port: int = int(os.getenv("MSHK_PORT", "8790"))
        self.open_browser: bool = _env_bool("MSHK_OPEN_BROWSER", False)
        self.data_dir: Path = Path(
            os.getenv("MSHK_DATA_DIR", str(external_root() / "data"))
        )
        self.templates_dir: Path = _resolve_dir("templates")
        # 术语库/抽取规则：仓库里位于 data/terms，兼容旧布局 report-engine/terms
        self.terms_dir: Path = _resolve_dir("terms", os.path.join("data", "terms"))
        # 允许浏览器前端跨源调用；默认放开本机来源，避免网页版集成时被 CORS 拦住
        self.cors_origins: list[str] = [
            o.strip()
            for o in os.getenv("MSHK_CORS_ORIGINS", "*").split(",")
            if o.strip()
        ]
        # 外部 LLM：默认关闭，符合 NFR-04 / NFR-06
        self.llm_enabled: bool = _env_bool("MSHK_LLM_ENABLED", False)
        self.llm_allow_external: bool = _env_bool("MSHK_LLM_ALLOW_EXTERNAL", False)
        self.llm_base_url: str = os.getenv("MSHK_LLM_BASE_URL", "")
        self.llm_model: str = os.getenv("MSHK_LLM_MODEL", "")
        self.llm_api_key: str = os.getenv("MSHK_LLM_API_KEY", "")
        self.llm_timeout_s: float = float(os.getenv("MSHK_LLM_TIMEOUT_S", "45"))
        self.llm_max_calls_per_report: int = int(
            os.getenv("MSHK_LLM_MAX_CALLS_PER_REPORT", "2")
        )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "mshk.db"

    @property
    def export_dir(self) -> Path:
        return self.data_dir / "exports"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.export_dir, self.templates_dir, self.terms_dir):
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError:
                # 只读挂载或权限不足时不应导致进程崩溃
                pass


settings = Settings()
