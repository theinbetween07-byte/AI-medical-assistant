"""一键启动三段服务：ASR 后端(18000) → 报告引擎(8790) → 集成层(8100)。

用 Python 而不是纯 .bat 来做进程编排，原因：
    1. 报告引擎需要 MSHK_TERMS_DIR / MSHK_DATA_DIR，而集成层不能继承这两个变量
       （cmd 里 "set && start" 的引号转义极易出错）；
    2. 需要轮询 /health，ASR 首次加载 Whisper 模型要 20-40 秒；
    3. 需要记录 pid 供 stop.bat 精确停止。

    python scripts/start_all.py            # 启动
    python scripts/start_all.py --open     # 启动后打开集成层界面
    python scripts/start_all.py --no-asr   # ASR 已在别处运行时跳过
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from service_identity import describe, is_ours  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]              # integration/
REPO = ROOT.parent


def _pick_dir(env_name: str, candidates: list[Path], marker: str) -> Path:
    """定位上游目录：环境变量优先，其次取第一个含 marker 文件的候选。

    兼容两种布局：
        仓库布局   <repo>/backend + <repo>/report-engine
        开发布局   <repo>/medical-ai-assistant/backend + <repo>/report-produce/<插件目录>
    """
    explicit = os.getenv(env_name, "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    for cand in candidates:
        if (cand / marker).is_file():
            return cand
    return candidates[0]


ASR_DIR = _pick_dir(
    "MSHK_ASR_DIR",
    [REPO / "medical-ai-assistant" / "backend", REPO / "backend"],
    "app/main.py",
)
ENGINE_DIR = _pick_dir(
    "MSHK_ENGINE_DIR",
    [REPO / "report-produce" / "书面报告生成插件（含术语纠正）", REPO / "report-engine"],
    "run.py",
)
def _resolve_ffmpeg() -> str:
    """定位 ffmpeg：环境变量 → 安装包 bin/ → 上游 backend/ 便携副本。

    安装包里 ffmpeg.exe 放在 <包>/bin/，而 backend 默认找 <backend>/ffmpeg.exe，
    必须显式注入 FFMPEG_PATH，否则会回退到开发机硬编码路径而失败。
    """
    explicit = os.getenv("FFMPEG_PATH", "").strip()
    if explicit and Path(explicit).is_file():
        return explicit
    for cand in (REPO.parent / "bin" / "ffmpeg.exe", REPO / "backend" / "ffmpeg.exe"):
        if cand.is_file():
            return str(cand)
    return ""


FFMPEG_PATH = _resolve_ffmpeg()
_BASE_ENV = {
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
    # ASR 后端读 FFMPEG_PATH，集成层读 MSHK_FFMPEG，两个都注入
    **({"FFMPEG_PATH": FFMPEG_PATH, "MSHK_FFMPEG": FFMPEG_PATH} if FFMPEG_PATH else {}),
}

LOGS = ROOT / "logs"
PID_FILE = LOGS / "pids.json"

# ASR 端口默认 18000（8000 常被本机 nginx/OSService 等占用，导致 ASR 被误判为"已运行"而跳过）。
# 可用环境变量 MSHK_ASR_PORT 覆盖，start_all / stop_all / 集成层 三处保持一致。
ASR_PORT = int(os.environ.get("MSHK_ASR_PORT", "18000"))
ENGINE_PORT = 8790
APP_PORT = 8100


def resolve_python() -> str:
    """优先用 ASR 便携版 Python（唯一确认装了 fastapi/uvicorn 的解释器）。"""
    explicit = os.getenv("MSHK_PYTHON", "").strip()
    if explicit and Path(explicit).is_file():
        return explicit
    portable = ASR_DIR / "python" / "python.exe"
    if portable.is_file():
        return str(portable)
    return sys.executable


def port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def claim_port(name: str, port: int, label: str) -> str:
    """返回 free / ours / foreign；foreign 时调用方必须中止，不能盲目跳过。"""
    if not port_busy(port):
        return "free"
    if is_ours(name, port):
        return "ours"
    print(f"  [!!] 端口 {port} 已被其它程序占用（身份校验：{describe(port)}），中止启动")
    print(f"       请释放端口，或用 MSHK_ASR_PORT 等环境变量换端口；不会把它当作 {label}")
    return "foreign"


def verify_identity(name: str, port: int, label: str) -> bool:
    if is_ours(name, port):
        return True
    print(f"  [!!] {label} 健康检查通过，但身份校验失败（{describe(port)}）")
    return False


def wait_health(url: str, timeout_s: int, label: str) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status == 200:
                    print(f"  [ok] {label} 就绪")
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    print(f"  [!!] {label} 未在 {timeout_s}s 内就绪，请查看 logs/ 下的日志")
    return False


def spawn(name: str, args: list[str], cwd: Path, env_extra: dict[str, str]) -> subprocess.Popen | None:
    LOGS.mkdir(parents=True, exist_ok=True)
    log = (LOGS / f"{name}.log").open("ab")
    log.write(f"\n\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} start {name} ===\n".encode("utf-8"))
    log.flush()
    env = {**os.environ, **env_extra}
    try:
        proc = subprocess.Popen(
            args,
            cwd=str(cwd),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except OSError as exc:
        print(f"  [!!] 启动 {name} 失败：{exc}")
        return None
    print(f"  [{name}] pid={proc.pid} log=logs/{name}.log")
    return proc


def main() -> int:
    parser = argparse.ArgumentParser(description="启动 MedScribe HK 三段服务")
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    parser.add_argument("--no-asr", action="store_true", help="跳过 ASR（已在别处运行）")
    parser.add_argument("--no-engine", action="store_true", help="跳过报告引擎")
    args = parser.parse_args()

    python = resolve_python()
    print(f"解释器：{python}")
    print(f"集成层：{ROOT}")
    print(f"ASR   ：{ASR_DIR}")
    print(f"引擎  ：{ENGINE_DIR}")
    print(f"ffmpeg：{FFMPEG_PATH or '[未找到，转写会失败]'}")
    print("-" * 60)

    pids: dict[str, int] = {}

    # ---------------------------------------------------------------- ASR
    if not args.no_asr:
        state = claim_port("asr", ASR_PORT, "ASR")
        if state == "foreign":
            return 1
        if state == "ours":
            print(f"  [skip] 端口 {ASR_PORT} 已是本项目的 ASR 服务")
        elif not (ASR_DIR / "app" / "main.py").is_file():
            print(f"  [!!] 找不到 ASR 后端：{ASR_DIR}")
        else:
            proc = spawn(
                "asr",
                [python, "-m", "uvicorn", "app.main:app",
                 "--host", "127.0.0.1", "--port", str(ASR_PORT),
                 # Windows 上 httptools+ProactorEventLoop 会间歇性 RST 连接（WinError 10054），
                 # 统一用纯 Python 的 h11 解析器，转写长连接稳定
                 "--http", "h11"],
                ASR_DIR,
                dict(_BASE_ENV),
            )
            if proc:
                pids["asr"] = proc.pid

    # ------------------------------------------------------------- 引擎
    if not args.no_engine:
        state = claim_port("engine", ENGINE_PORT, "报告引擎")
        if state == "foreign":
            return 1
        if state == "ours":
            print(f"  [skip] 端口 {ENGINE_PORT} 已是本项目的报告引擎")
        elif not (ENGINE_DIR / "run.py").is_file():
            print(f"  [!!] 找不到报告引擎：{ENGINE_DIR}")
        else:
            terms = ROOT / "terms"
            if not (terms / "seed_terms.json").is_file():
                print("  [warn] terms/seed_terms.json 不存在，请先运行 scripts/import_terms.py")
            if not (terms / "extraction_rules.json").is_file():
                print("  [warn] terms/extraction_rules.json 缺失，抽取规则会退化")
            proc = spawn(
                "engine",
                [python, str(ROOT / "scripts" / "run_engine.py"), str(ENGINE_DIR),
                 "--port", str(ENGINE_PORT)],
                ENGINE_DIR,
                {
                    "MSHK_TERMS_DIR": str(terms),
                    "MSHK_DATA_DIR": str(ROOT / "data" / "engine"),
                    "PYTHONIOENCODING": "utf-8",
                    "PYTHONUTF8": "1",
                },
            )
            if proc:
                pids["engine"] = proc.pid

    # --------------------------------------------------------- 集成层
    state = claim_port("integration", APP_PORT, "集成层")
    if state == "foreign":
        return 1
    if state == "ours":
        print(f"  [skip] 端口 {APP_PORT} 已是本项目的集成层")
    else:
        proc = spawn(
            "integration",
            [python, "run.py"],
            ROOT,
            {
                **_BASE_ENV,
                # 显式告诉集成层 ASR 真实地址，避免与 config.py 默认值（18000）脱节
                "MSHK_ASR_URL": f"http://127.0.0.1:{ASR_PORT}",
            },
        )
        if proc:
            pids["integration"] = proc.pid

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    # 保留上一轮仍在运行、且身份匹配的服务 pid（例如本次只重启集成层）
    try:
        previous = json.loads(PID_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        previous = {}
    for name, pid in previous.items():
        if name in pids or not isinstance(pid, int):
            continue
        port = {"asr": ASR_PORT, "engine": ENGINE_PORT, "integration": APP_PORT}.get(name)
        if port is not None and is_ours(name, port):
            pids[name] = pid
    PID_FILE.write_text(json.dumps(pids, indent=2), encoding="utf-8")

    print("-" * 60)
    print("等待服务就绪…")
    asr_ok = True
    if not args.no_asr:
        asr_ok = wait_health(f"http://127.0.0.1:{ASR_PORT}/health", 180, "ASR")
        asr_ok = asr_ok and verify_identity("asr", ASR_PORT, "ASR")
    engine_ok = True
    if not args.no_engine:
        engine_ok = wait_health(f"http://127.0.0.1:{ENGINE_PORT}/health", 90, "报告引擎")
        engine_ok = engine_ok and verify_identity("engine", ENGINE_PORT, "报告引擎")
    app_ok = wait_health(f"http://127.0.0.1:{APP_PORT}/health/live", 90, "集成层")
    app_ok = app_ok and verify_identity("integration", APP_PORT, "集成层")
    ok = asr_ok and engine_ok and app_ok

    print("-" * 60)
    print(f"界面   http://127.0.0.1:{APP_PORT}/")
    print(f"接口   http://127.0.0.1:{APP_PORT}/docs")
    print("停止   stop.bat（或 python scripts/stop_all.py）")
    print("-" * 60)

    if args.open and ok:
        webbrowser.open(f"http://127.0.0.1:{APP_PORT}/")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
