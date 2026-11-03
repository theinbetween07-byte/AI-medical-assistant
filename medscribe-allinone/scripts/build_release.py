"""构建 GitHub Release 资产：基础包（Whisper small）与 CapsWriter Qwen3 扩展包。

产物（默认 outputs/release/）：
    MedScribe-base-v1.0.0.7z.001 …  基础包分卷（含运行时、small 模型、界面）
    MedScribe-ext-caps-qwen3-v1.0.0.7z.001 …  Qwen3-ASR 扩展分卷
    SHA256SUMS.txt
    extensions.json（同步写入基础包 app/models/ext/）

用法：
    python scripts/build_release.py            # 全量构建
    python scripts/build_release.py --keep     # 保留中间目录便于检查
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # medscribe-allinone/
REPO = ROOT.parent
APP = ROOT / "app"
RUNTIME = ROOT / "runtime" / "python"
BUILD = REPO / ".deepworks" / "tmp" / "release_build"
OUT = REPO / "outputs" / "release"
CAPS_SRC = REPO / ".deepworks" / "tmp" / "caps" / "app" / "CapsWriter-Offline"
FFMPEG = Path(r"C:\Users\LegacYinJadeware\.deepworks\tools\ffmpeg.exe")
SEVEN_ZIP = Path(r"C:\SetupSoftware\B-AOMEI Backupper\AOMEI Backupper\6.20.0\7z.exe")

VERSION = "1.0.0"
REPO_SLUG = "theinbetween07-byte/AI-medical-assistant"
EXT_DIR_NAME = "caps-qwen3"

IGNORE_DIRS = {"__pycache__", ".pytest_cache", "logs", "data"}
IGNORE_FILES = {".DS_Store", "Thumbs.db"}


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def _ignore(directory: str, names: list[str]) -> set[str]:
    dropped = {n for n in names if n in IGNORE_DIRS or n in IGNORE_FILES}
    dropped |= {n for n in names if n.endswith((".pyc", ".pyo", ".log", ".db", ".db-wal", ".db-shm"))}
    return dropped


def link_tree(src: Path, dst: Path, ignore_dirs: set[str] | None = None, skip_pyc: bool = False) -> None:
    """优先硬链接（同卷瞬时、省空间），失败回退复制。

    ``skip_pyc`` 只用于便携运行时：PyInstaller 的 onedir 包（扩展模块）把标准库
    放成 ``.pyc``，缺了就跑不起来，因此扩展包必须原样保留。
    """
    if not src.exists():
        raise FileNotFoundError(src)
    for path in src.rglob("*"):
        rel = path.relative_to(src)
        skip = ignore_dirs if ignore_dirs is not None else IGNORE_DIRS
        if any(part in skip for part in rel.parts) or path.name in IGNORE_FILES:
            continue
        if path.name.endswith((".log", ".db", ".db-wal", ".db-shm")):
            continue
        if skip_pyc and path.name.endswith((".pyc", ".pyo")):
            continue
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            continue
        try:
            os.link(path, target)
        except OSError:
            shutil.copy2(path, target)


BASE_README = """# MedScribe HK · 离线基础包

本包已包含：Windows 运行时（Python 3.12）、整合界面、报告引擎、Whisper **small** 模型、
ffmpeg。**完全离线**：不下载模型、不上传音频。

## 快速开始

1. 把全部 `MedScribe-base-v{ver}.7z.00*` 分卷放在同一个文件夹，用 7-Zip 解压 **.001**
   （会自动合并其余分卷），得到 `MedScribe` 文件夹。
2. 双击 `MedScribe` 里的 **MedScribe.bat**。
3. 等待控制台显示三段服务就绪（首次 10–30 秒），浏览器会自动打开
   <http://127.0.0.1:8100>。

> 运行时请保持黑色控制台窗口开着；关闭窗口或双击 **停止.bat** 即可停止全部服务。

## 系统要求

| 项目 | 要求 |
| --- | --- |
| 系统 | Windows 10 / 11，64 位 |
| 内存 | 4 GB 起（small 常驻约 1 GB） |
| 磁盘 | 约 1 GB（解压后 908 MB） |
| 显卡 | 不需要；默认 CPU int8 |
| 网络 | 安装与使用都不需要（扩展包需一次性下载） |

## 内置模型

| 模型 | 说明 |
| --- | --- |
| Whisper small | 默认，CPU int8；中文门诊字错率（CER）13.76%–20.11% |

工具条「模型」下拉里的 **Qwen3-ASR** 属于可选扩展，未安装时点击会弹出下载地址与
安装步骤，不会尝试联网下载。

## 目录

```
MedScribe/
  MedScribe.bat      启动（三段服务 + 打开浏览器）
  停止.bat           停止全部服务
  runtime/           便携 Python 3.12
  app/backend        ASR 服务（18000）
  app/report-engine  报告引擎（8790）
  app/integration    整合层与网页界面（8100）
  app/models         Whisper small 与扩展清单
  bin/ffmpeg.exe     音频解码
```

## 停止与排错

- 端口被占用：关闭占用 8100 / 8790 / 18000 的程序后重试。
- 首次转写较慢：small 模型加载约 2–8 秒（24 核测试机 1.5 秒），转写耗时随音频长度增加。
- 日志：`app/integration/logs/`（asr.log / engine.log / integration.log）。
- 完全卸载：删除整个 MedScribe 文件夹（不写注册表、不写用户目录）。

## 隐私

音频转码、ASR、模板抽取全部在本机完成；除导出文件外不产生外部请求。
"""

EXT_README = """# Qwen3-ASR 扩展（CapsWriter-Offline）

这是 MedScribe HK 的可选识别引擎，适合病理长术语等高精度场景。
病理语料实测错误率 **3.64%**（内置 Whisper small 为 9.09%）。

## 安装（三步）

1. 下载**全部分卷** `MedScribe-ext-caps-qwen3-v{ver}.7z.001`、`.002` … 放到同一文件夹。
2. 用 7-Zip 解压 **.001**（会自动合并其余分卷），得到 `MedScribe-ext` 文件夹。
3. 进入 `MedScribe-ext`，把其中的 `app` 整个复制到 `MedScribe` 根目录
   （提示时选择“合并/替换”），然后重新双击 `MedScribe.bat`。

安装完成后，工具条「模型」中选择 **Qwen3-ASR** 即可。默认 CPU 推理，首次转写需加载
约 1.4 GB 模型（20–60 秒），之后常驻内存；测试机 90 秒病理音频耗时约 35 秒。

## 卸载

删除 `MedScribe/app/models/ext/caps-qwen3` 文件夹，重启后下拉中会重新显示“未安装”。

## 说明

- 完全离线：模型与 CapsWriter 运行时都在本包内，不联网下载。
- 占用：解压后约 2.9 GB 磁盘；运行时常驻约 3–4 GB 内存。
- 默认 CPU 推理，兼容无独显的机器。若本机有 Vulkan 显卡，可把
  `app/models/ext/caps-qwen3/config_server.py` 里的 `llm_use_gpu` 改为 `True` 提速
  （改完重启 MedScribe）。
- 使用端口 6016（仅本机回环）。若被其它程序占用，请先关闭占用者。
- 输出无精确时间戳，界面按段落均分估算片段位置。
"""


def write_text(path: Path, text: str, encoding: str = "utf-8-sig") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=encoding)


def build_base(dest: Path) -> None:
    log("组装基础包目录…")
    app = dest / "app"
    link_tree(APP / "backend", app / "backend")
    link_tree(APP / "integration", app / "integration")
    link_tree(APP / "report-engine", app / "report-engine")
    # 只带 small；medium 等不再随包分发
    link_tree(APP / "models" / "faster-whisper-small", app / "models" / "faster-whisper-small")
    (app / "models" / "ext").mkdir(parents=True, exist_ok=True)
    shutil.copy2(APP / "models" / "ext" / "extensions.json", app / "models" / "ext" / "extensions.json")

    # 运行时不带 CUDA 库（默认 CPU；可省约 2 GB）
    runtime_dst = dest / "runtime" / "python"
    link_tree(RUNTIME, runtime_dst, {"__pycache__", ".pytest_cache"}, skip_pyc=True)
    nvidia = runtime_dst / "Lib" / "site-packages" / "nvidia"
    if nvidia.exists():
        shutil.rmtree(nvidia, ignore_errors=True)

    (dest / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy2(FFMPEG, dest / "bin" / "ffmpeg.exe")

    write_text(dest / "MedScribe.bat", BAT_START, "gbk")
    write_text(dest / "停止.bat", BAT_STOP, "gbk")
    write_text(dest / "使用教程.md", BASE_README.format(ver=VERSION))
    write_text(dest / "README.md", BASE_README.format(ver=VERSION))


BAT_START = r"""@echo off
chcp 65001 >nul
title MedScribe HK
cd /d "%~dp0app\integration"
set "MSHK_PYTHON=%~dp0runtime\python\python.exe"
set "MSHK_ASR_DIR=%~dp0app\backend"
set "MSHK_ENGINE_DIR=%~dp0app\report-engine"
set "FFMPEG_PATH=%~dp0bin\ffmpeg.exe"
set "WHISPER_MODEL=small"
set "WHISPER_DEVICE=cpu"
set "WHISPER_COMPUTE_TYPE=int8"
if not exist "%MSHK_PYTHON%" (
  echo [错误] 未找到 runtime\python\python.exe，请确认已完整解压全部分卷。
  pause
  exit /b 1
)
echo 正在启动 MedScribe，首次启动约 10-30 秒...
"%MSHK_PYTHON%" scripts\start_all.py --open
echo.
echo 服务已退出或启动失败，请查看 app\integration\logs\ 下的日志。
pause
"""

BAT_STOP = r"""@echo off
chcp 65001 >nul
cd /d "%~dp0app\integration"
"%~dp0runtime\python\python.exe" scripts\stop_all.py
pause
"""


def build_extension(dest: Path) -> None:
    log("组装 Qwen3-ASR 扩展包…")
    caps = dest / "app" / "models" / "ext" / EXT_DIR_NAME
    caps.mkdir(parents=True, exist_ok=True)

    for name in ("assets", "core", "internal", "LLM", "docs"):
        src = CAPS_SRC / name
        if src.exists():
            link_tree(src, caps / name)

    for name in ("start_server.exe", "start_client.exe", "LICENSE", "readme.md", "hot-server.txt", "hot.txt", "hot-rule.txt"):
        src = CAPS_SRC / name
        if src.exists():
            shutil.copy2(src, caps / name)

    models = caps / "models"
    for name in ("Qwen3-ASR", "Qwen3-ForcedAligner", "Punct-CT-Transformer"):
        src = CAPS_SRC / "models" / name
        if not src.exists():
            raise FileNotFoundError(f"缺少扩展模型目录：{src}")
        link_tree(src, models / name)

    server_cfg = (CAPS_SRC / "config_server.py").read_text(encoding="utf-8")
    server_cfg = re.sub(r"model_type\s*=\s*'[^']*'", "model_type = 'qwen_asr'", server_cfg)
    server_cfg = re.sub(r"enable_tray\s*=\s*\w+", "enable_tray = False", server_cfg)
    server_cfg = re.sub(r"log_level\s*=\s*'[^']*'", "log_level = 'WARNING'", server_cfg)
    # 擴展默認走 CPU，避免無 Vulkan 顯卡的機器啟動失敗；有顯卡可自行改回 True
    server_cfg = re.sub(r"llm_use_gpu\s*=\s*True", "llm_use_gpu = False", server_cfg)
    write_text(caps / "config_server.py", server_cfg)

    client_cfg = (CAPS_SRC / "config_client.py").read_text(encoding="utf-8")
    client_cfg = re.sub(r"enable_tray\s*=\s*\w+", "enable_tray = False", client_cfg)
    client_cfg = re.sub(r"llm_enabled\s*=\s*\w+", "llm_enabled = False", client_cfg)
    client_cfg = re.sub(r"file_save_srt\s*=\s*\w+", "file_save_srt = False", client_cfg)
    client_cfg = re.sub(r"file_save_json\s*=\s*\w+", "file_save_json = False", client_cfg)
    write_text(caps / "config_client.py", client_cfg)

    write_text(dest / "安装说明.md", EXT_README.format(ver=VERSION))


def seven_zip(archive: Path, source_dir: Path) -> list[Path]:
    if not SEVEN_ZIP.exists():
        raise FileNotFoundError(f"未找到 7-Zip：{SEVEN_ZIP}")
    archive.parent.mkdir(parents=True, exist_ok=True)
    # 清掉同名前綴的舊分卷，否則 7z 會因卷已存在而退出
    for old in archive.parent.glob(archive.name + ".*"):
        old.unlink()
    cmd = [str(SEVEN_ZIP), "a", "-t7z", "-mx=1", "-v190m", "-y", str(archive), str(source_dir)]
    log(f"压缩 {archive.name}（分卷 190 MB）…")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    return sorted(archive.parent.glob(archive.name + ".*"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="保留中间目录")
    parser.add_argument("--only", choices=["base", "ext"], help="只重建其中一个包")
    args = parser.parse_args()

    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True)
    OUT.mkdir(parents=True, exist_ok=True)

    base_dir = BUILD / "MedScribe"
    ext_dir = BUILD / "MedScribe-ext"
    ext_volumes: list[Path] = []
    base_volumes: list[Path] = []

    if args.only != "base":
        build_extension(ext_dir)
        ext_volumes = seven_zip(OUT / f"MedScribe-ext-caps-qwen3-v{VERSION}.7z", ext_dir)
    if args.only != "ext":
        build_base(base_dir)
        base_volumes = seven_zip(OUT / f"MedScribe-base-v{VERSION}.7z", base_dir)

    assets = sorted(OUT.glob("MedScribe-*.7z.0*"))
    sums = [f"{sha256(p)}  {p.name}" for p in assets]
    (OUT / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")

    total = sum(p.stat().st_size for p in assets)
    if base_volumes:
        log(f"基础包：{len(base_volumes)} 卷 / {sum(p.stat().st_size for p in base_volumes)/2**30:.2f} GB")
    if ext_volumes:
        log(f"扩展包：{len(ext_volumes)} 卷 / {sum(p.stat().st_size for p in ext_volumes)/2**30:.2f} GB")
    log(f"合计：{total/2**30:.2f} GB → {OUT}")

    if not args.keep:
        shutil.rmtree(BUILD, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
