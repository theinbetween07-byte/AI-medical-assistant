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

    if PID_FILE.is_file():
        try:
            pids = json.loads(PID_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pids = {}
        for name, pid in pids.items():
            if not isinstance(pid, int) or pid in killed:
                continue
            cmd = command_line(pid)
            if not command_matches(name, cmd):
                skipped.append(f"{name}(pid {pid} 命令行不匹配，可能已被复用)")
                continue
            kill_pid(pid)
            killed.add(pid)
            stopped.append(f"{name}(pid {pid})")
        if pids:
            PID_FILE.unlink(missing_ok=True)

    # 回退：仅在服务身份与命令行都匹配时才按端口停止
    for name, port in PORTS.items():
        if not is_ours(name, port):
            continue
        for pid in pids_on_port(port):
            if pid in killed:
                continue
            if not command_matches(name, command_line(pid)):
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
