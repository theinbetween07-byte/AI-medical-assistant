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
    if platform.system() == "Windows":
        script = (
            f"Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\" "
            "| Select-Object -ExpandProperty CommandLine"
        )
        return _run_text(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script], 15
        ).strip()
    return _run_text(["ps", "-p", str(int(pid)), "-o", "command="], 10).strip()


def command_matches(name: str, cmdline: str) -> bool:
    if not cmdline:
        return False
    if name == "asr":
        return "uvicorn" in cmdline and "app.main:app" in cmdline
    if name == "engine":
        return "run_engine.py" in cmdline
    if name == "integration":
        return bool(re.search(r"(^|\s|\\\\)run\.py(\s|$)", cmdline))
    return False


def is_ours(name: str, port: int) -> bool:
    """端口上既有本服务的健康检查形状，监听进程也带本服务的命令行标记。"""
    if identify(port) != name:
        return False
    pids = pids_on_port(port)
    return any(command_matches(name, command_line(pid)) for pid in pids)


def describe(port: int) -> str:
    if not port_open(port):
        return "free"
    return identify(port) or "unknown"


def env_port(name: str, default: int) -> int:
    if name == "asr":
        return int(os.environ.get("MSHK_ASR_PORT", default))
    return default
