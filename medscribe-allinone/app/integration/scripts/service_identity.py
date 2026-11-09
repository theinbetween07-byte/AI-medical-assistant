"""服务身份探测：判断某个端口上跑的到底是不是本项目的服务。

背景：只能判断“端口是否被占用”会把陌生进程误当成自己的服务（跳过启动），
停止脚本按端口回退时还可能误杀别的程序。这里用两层证据：

1. HTTP `/health` 的返回形状（ASR / 报告引擎 / 集成层各不相同）；
2. 监听该端口的进程命令行标记（uvicorn app.main:app / run_engine.py / run.py）。

只做只读探测，不修改任何进程。Windows 下用 PowerShell 读取命令行；
其它平台回退到 ps（如果可用）。
"""

from __future__ import annotations

import json
import os
import platform
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

# 本文件的上一级（app/integration）；用于判断端口上的服务是否属于同一份安装
APP_ROOT = Path(__file__).resolve().parents[1]

TIMEOUT_S = 2.0


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def _http_json(port: int, path: str = "/health", timeout: float = TIMEOUT_S) -> dict | None:
    # 身份探测本身也必须对 Windows localhost 偶发 RST 有界重试，
    # 否则会把正在运行的服务误判为“陌生”甚至“未运行”。
    for attempt in range(3):
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}{path}", timeout=timeout
            ) as resp:
                if resp.status != 200:
                    return None
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, OSError, ValueError):
            if attempt == 2:
                return None
            time.sleep(0.5)
    return None


def _looks_like(name: str, data: dict | None) -> bool:
    if not isinstance(data, dict):
        return False
    if name == "asr":
        return "model_loaded" in data and "model" in data
    if name == "engine":
        return data.get("status") == "ok" and "templates" in data
    if name == "integration":
        return "components" in data and "terms" in data
    return False


def identify(port: int) -> str | None:
    """返回 'asr' / 'engine' / 'integration' / None（不是本项目服务或不可达）。"""
    # 集成层优先用不聚合上游的存活探针，避免上游全挂时探测超时。
    live = _http_json(port, "/health/live", timeout=2.0)
    if isinstance(live, dict) and live.get("service") == "integration":
        return "integration"
    data = _http_json(port)
    for name in ("asr", "engine", "integration"):
        if _looks_like(name, data):
            return name
    # 集成层的 /health 会聚合上游；上游全挂时可能超过探测超时。
    # 此时改用立即可用的 OpenAPI 文档做身份校验。
    spec = _http_json(port, "/openapi.json", timeout=3.0)
    if isinstance(spec, dict):
        paths = spec.get("paths") or {}
        if "/sessions" in paths and "/templates" in paths:
            return "integration"
    return None


def _run_text(args: list[str], timeout: float) -> str:
    """运行外部命令并按 Windows 本地编码解码；避免 PYTHONUTF8=1 时 text=True 解码崩。"""
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    raw = proc.stdout or b""
    for encoding in ("utf-8", "gbk", "mbcs"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def pids_on_port(port: int) -> list[int]:
    out = _run_text(["netstat", "-ano", "-p", "TCP"], 15)
    found: list[int] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5 or "LISTENING" not in line:
            continue
        if parts[1].endswith(f":{port}") and parts[4].isdigit():
            found.append(int(parts[4]))
    return found


def command_line(pid: int) -> str:
    """读取进程命令行；受限环境（CIM 被禁用、PowerShell 冷启动慢）返回空串。

    PowerShell 首次启动在部分机器上可能超过 10s（杀软扫描、组策略脚本），
    因此这里给足超时并重试一次，避免把「读得慢」误判成「不是本服务」。
    """
    for attempt in range(2):
        if platform.system() == "Windows":
            script = (
                f"Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\" "
                "| Select-Object -ExpandProperty CommandLine"
            )
            text = _run_text(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script], 20
            ).strip()
        else:
            text = _run_text(["ps", "-p", str(int(pid)), "-o", "command="], 10).strip()
        if text:
            return text
    return ""


def pid_alive(pid: int) -> bool:
    """判断进程是否仍存在；Windows 下不能用 os.kill(pid, 0)（会发送终止信号）。"""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if platform.system() == "Windows":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False



def command_matches(name: str, cmdline: str) -> bool:
    if not cmdline:
        return False
    if name == "asr":
        return "uvicorn" in cmdline and "app.main:app" in cmdline
    if name == "engine":
        return "run_engine.py" in cmdline and str(APP_ROOT.parent).lower() in cmdline.lower()
    if name == "integration":
        return bool(re.search(r"(^|\s|\\\\)run\.py(\s|$)", cmdline))
    return False


def is_ours(name: str, port: int, pid: int | None = None) -> bool:
    """端口上是否本服务。

    证据分三层，任一成立即可：
    1. 调用方刚启动并持有的 pid 仍存活（最强，不依赖 PowerShell）；
    2. 监听进程命令行带本服务标记；
    3. HTTP 形状已完全匹配、但命令行读不到（受限环境）——降级信任 HTTP。
    读到命令行却不匹配时仍判定为陌生进程，保留原有误杀保护。
    """
    if identify(port) != name:
        return False
    # 同类型服务可能来自另一份 MedScribe 安装（用户解压了多个副本）：
    # 这类实例既不能复用（模型/数据目录不同），也不能被本脚本停止。
    live = None
    if name == "integration":
        live = _http_json(port, "/health/live", timeout=2.0)
    elif name == "asr":
        live = _http_json(port)
    if isinstance(live, dict):
        served_root = live.get("root")
        if served_root:
            try:
                Path(str(served_root)).resolve().relative_to(APP_ROOT.parent)
            except (ValueError, OSError):
                return False
    if pid is not None and pid_alive(pid):
        return True
    pids = pids_on_port(port)
    if not pids:
        return False
    evidence = [command_line(p) for p in pids]
    if any(command_matches(name, c) for c in evidence):
        return True
    return not any(evidence)



def describe(port: int) -> str:
    if not port_open(port):
        return "free"
    return identify(port) or "unknown"


def env_port(name: str, default: int) -> int:
    if name == "asr":
        return int(os.environ.get("MSHK_ASR_PORT", default))
    return default
