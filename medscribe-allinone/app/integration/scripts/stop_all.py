"""停止 scripts/start_all.py 拉起的三个服务。

安全规则（2026-11-02 起）：
- pid 文件里的进程，命令行必须带本服务标记才停止，避免 pid 被复用后误杀；
- pid 文件缺失时，只有端口上的服务同时通过 `/health` 身份校验与命令行校验才停止；
- 端口被陌生程序占用时只报告、不杀进程。

    python scripts/stop_all.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from service_identity import (  # noqa: E402
    command_line,
    command_matches,
    is_ours,
    pids_on_port,
)

ROOT = Path(__file__).resolve().parents[1]
PID_FILE = ROOT / "logs" / "pids.json"
PORTS = {
    "asr": int(os.environ.get("MSHK_ASR_PORT", "18000")),
    "engine": 8790,
    "integration": 8100,
}


def kill_pid(pid: int) -> None:
    # 不解码 taskkill 输出：Windows 本地编码在 PYTHONUTF8=1 下会抛 UnicodeDecodeError
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(pid)],
        capture_output=True,
    )


def main() -> int:
    stopped: list[str] = []
    skipped: list[str] = []
    killed: set[int] = set()

    saved_ports: dict[str, int] = {}
    if PID_FILE.is_file():
        try:
            data = json.loads(PID_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        # 新格式 {"pids": {...}, "ports": {...}}；兼容旧格式 {"asr": 123, ...}
        if isinstance(data.get("pids"), dict):
            pids = data["pids"]
            raw_ports = data.get("ports") or {}
            saved_ports = {k: v for k, v in raw_ports.items() if isinstance(v, int)}
        else:
            pids = data
        for name, pid in pids.items():
            if not isinstance(pid, int) or pid in killed:
                continue
            port = saved_ports.get(name, PORTS.get(name, -1))
            cmd = command_line(pid)
            # 命令行读不到（CIM 受限/超时）时退回「pid 仍在对应端口监听」这一证据，
            # 否则受限环境会永远停不掉自己的服务。
            if cmd:
                if not command_matches(name, cmd):
                    skipped.append(f"{name}(pid {pid} 命令行不匹配，可能已被复用)")
                    continue
            elif port < 0 or pid not in pids_on_port(port):
                skipped.append(f"{name}(pid {pid} 无法确认监听端口)")
                continue
            kill_pid(pid)
            killed.add(pid)
            stopped.append(f"{name}(pid {pid})")
        if pids:
            PID_FILE.unlink(missing_ok=True)

    # 回退：仅在服务身份与命令行都匹配时才按端口停止
    fallback = dict(PORTS)
    fallback.update(saved_ports)
    for name, port in fallback.items():
        if not is_ours(name, port):
            continue
        for pid in pids_on_port(port):
            if pid in killed:
                continue
            cmd = command_line(pid)
            # 端口 + HTTP 身份已确认时，命令行读不到不再阻断停止
            if cmd and not command_matches(name, cmd):
                skipped.append(f"{name}(port {port} pid {pid} 命令行不匹配)")
                continue
            kill_pid(pid)
            killed.add(pid)
            stopped.append(f"{name}(port {port} pid {pid})")

    if stopped:
        print("已停止：" + "，".join(sorted(set(stopped))))
    else:
        print("没有发现由本项目启动的服务。")
    for item in skipped:
        print("  [skip] " + item)
    return 0


if __name__ == "__main__":
    sys.exit(main())
