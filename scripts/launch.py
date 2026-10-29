"""服務啟動器 - 以脫離控制台的方式啟動後端與前端。

為什麼需要它：
  ctranslate2（Whisper 推理引擎）在 Windows 上收到「控制台窗口關閉」事件時
  會直接 abort 進程（forrtl: error 200）。用 DETACHED_PROCESS 啟動可以讓
  服務完全脫離父控制台，關閉任何窗口都不影響它。

重要：
  DETACHED_PROCESS (0x8) 與 CREATE_NO_WINDOW (0x08000000) 互斥，
  同時設置會導致子進程創建異常、標準流句柄失效（表現為日誌 0 字節、
  進程瞬間消失且無任何錯誤輸出）。因此這裡只用 DETACHED_PROCESS。
"""

import os
import subprocess
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = os.path.join(ROOT, ".tmp")

DETACHED_PROCESS = 0x00000008
FLAGS = DETACHED_PROCESS


def _open_log(name: str):
    os.makedirs(TMP, exist_ok=True)
    return open(
        os.path.join(TMP, name), "w", encoding="utf-8", errors="replace"
    )


def start_backend() -> None:
    """啟動 FastAPI 後端（:8000），使用 pythonw.exe 避免控制台依賴。"""
    exe = os.path.join(ROOT, "backend", "python", "pythonw.exe")
    subprocess.Popen(
        [exe, "-u", "-m", "app.main"],
        cwd=os.path.join(ROOT, "backend"),
        stdin=subprocess.DEVNULL,
        stdout=_open_log("backend.log"),
        stderr=subprocess.STDOUT,
        creationflags=FLAGS,
        close_fds=True,
    )


def start_frontend() -> None:
    """啟動 Vite 前端（:5173），使用內置 Node.js。"""
    node = os.path.join(ROOT, "node", "node.exe")
    env = os.environ.copy()
    env["PATH"] = os.path.join(ROOT, "node") + os.pathsep + env.get("PATH", "")
    subprocess.Popen(
        [node, os.path.join("node_modules", "vite", "bin", "vite.js")],
        cwd=os.path.join(ROOT, "frontend"),
        stdin=subprocess.DEVNULL,
        stdout=_open_log("frontend.log"),
        stderr=subprocess.STDOUT,
        env=env,
        creationflags=FLAGS,
        close_fds=True,
    )


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    if target in ("all", "backend"):
        start_backend()
    if target in ("all", "frontend"):
        start_frontend()
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        os.makedirs(TMP, exist_ok=True)
        with open(os.path.join(TMP, "launch-error.log"), "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        code = 1
    try:
        print("launch: ok" if code == 0 else "launch: failed (see .tmp/launch-error.log)")
    except Exception:
        pass
    sys.exit(code)
