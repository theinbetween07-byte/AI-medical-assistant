"""可選擴展模塊（目前是 CapsWriter 引擎）的發現、安裝狀態與清單。

約定
----
- 清單：``app/models/ext/extensions.json``（隨基礎包分發，構建腳本會寫入真實下載地址與校驗值）
- 安裝目錄：``app/models/ext/<dir>``，要求存在 ``start_server.exe`` 與 ``config_server.py``
- 未安裝時：``/asr/models`` 返回 ``installed=false`` 與安裝指引，界面據此彈出下載說明
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from loguru import logger

from app.config import EXTENSIONS_MANIFEST, EXTENSIONS_ROOT

_DEFAULT_INSTALL_STEPS = [
    "下载扩展包的**全部**分卷（.7z.001、.002 …）与说明文件",
    "解压 .7z.001 得到 app 文件夹",
    "把 app 文件夹合并到 MedScribe 根目录（提示时选择“合并/替换”）",
    "重启 MedScribe，在工具条「模型」中选择该模型",
]


def _read_manifest() -> list[dict]:
    if not EXTENSIONS_MANIFEST.exists():
        return []
    try:
        data = json.loads(EXTENSIONS_MANIFEST.read_text(encoding="utf-8"))
    except Exception as exc:  # 清單損壞不應拖垮服務
        logger.warning(f"擴展清單讀取失敗：{exc}")
        return []
    entries = data.get("extensions") if isinstance(data, dict) else data
    return [e for e in (entries or []) if isinstance(e, dict) and e.get("id")]


def extension_path(entry: dict) -> Path:
    return EXTENSIONS_ROOT / str(entry.get("dir") or entry["id"])


def is_installed(entry: dict) -> bool:
    root = extension_path(entry)
    if entry.get("kind") == "caps":
        return (root / "start_server.exe").exists() and (root / "config_server.py").exists()
    return root.exists()


def _install_info(entry: dict) -> dict:
    steps = entry.get("steps") or _DEFAULT_INSTALL_STEPS
    return {
        "url": entry.get("url", ""),
        "size_mb": entry.get("size_mb", 0),
        "sha256": entry.get("sha256", ""),
        "steps": steps,
        "note": entry.get(
            "note",
            "扩展包完全离线，安装后不再联网；卸载只需删除 app/models/ext/<目录> 后重启。",
        ),
    }


def model_entries() -> list[dict]:
    """Whisper 基礎模型 + 擴展模型，供 /asr/models 返回。"""
    from app.config import LOCAL_MODELS, SUPPORTED_MODELS

    models: list[dict] = []
    for model_id in SUPPORTED_MODELS:
        models.append(
            {
                "id": model_id,
                "label": f"Whisper {model_id}",
                "kind": "whisper",
                "local": model_id in LOCAL_MODELS,
                "installed": model_id in LOCAL_MODELS,
            }
        )
    for entry in _read_manifest():
        installed = is_installed(entry)
        models.append(
            {
                "id": entry["id"],
                "label": entry.get("name") or entry["id"],
                "kind": entry.get("kind", "caps"),
                "local": installed,
                "installed": installed,
                "install": _install_info(entry),
            }
        )
    return models


def find_extension(model_id: str) -> Optional[dict]:
    for entry in _read_manifest():
        if entry["id"] == model_id:
            return entry
    return None


def require_installed(model_id: str) -> dict:
    """切換擴展模型前的校驗；未安裝時拋出帶安裝指引的 FileNotFoundError。"""
    entry = find_extension(model_id)
    if entry is None:
        raise ValueError(f"未知的模型：{model_id}")
    if not is_installed(entry):
        info = _install_info(entry)
        lines = [f"扩展模块「{entry.get('name', model_id)}」尚未安装。", f"下载地址：{info['url']}"]
        lines += [f"{i}. {s}" for i, s in enumerate(info["steps"], 1)]
        raise FileNotFoundError("\n".join(lines))
    return entry
