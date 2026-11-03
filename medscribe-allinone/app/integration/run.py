"""集成层启动入口。

    python run.py                # 默认 127.0.0.1:8100
    python run.py --reload       # 开发模式热重载

上游依赖（可用环境变量指向别处）：
    ASR      http://127.0.0.1:18000   (medical-ai-assistant 后端)
    引擎     http://127.0.0.1:8790   (report-produce 报告引擎)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="MedScribe HK 集成层")
    parser.add_argument("--host", default="")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    try:
        import uvicorn
        from app.config import settings
    except ImportError as exc:
        print("缺少依赖：", exc)
        print("请用带 fastapi/uvicorn 的 Python 运行，例如：")
        print(r"  ..\medical-ai-assistant\backend\python\python.exe run.py")
        return 1

    host = args.host or settings.host
    port = args.port or settings.port
    print(f"集成层      http://{host}:{port}")
    print(f"ASR 上游    {settings.asr_url}")
    print(f"报告引擎    {settings.engine_url}")
    print(f"术语库      {settings.terms_dir}")
    print(f"数据目录    {settings.data_dir}")
    print(f"ffmpeg      {settings.ffmpeg}")
    print("-" * 60)

    uvicorn.run(
        "app.main:app" if args.reload else _load_app(),
        host=host,
        port=port,
        reload=args.reload,
        reload_dirs=[str(ROOT / "app")] if args.reload else None,
        log_level="info",
        # Windows 上 httptools+ProactorEventLoop 会间歇性 RST 连接，改用 h11
        http="h11",
    )
    return 0


def _load_app():
    from app.main import app

    return app


if __name__ == "__main__":
    raise SystemExit(main())
