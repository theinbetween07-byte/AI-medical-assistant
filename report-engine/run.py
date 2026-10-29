"""启动入口（开发运行与 PyInstaller 打包入口）。

    python run.py                # 启动本地服务，默认 127.0.0.1:8790
    python run.py --open         # 启动并自动打开浏览器
    python run.py --port 9000    # 指定端口（宿主程序可动态分配）
"""

from __future__ import annotations

import argparse
import contextlib
import socket
import threading
import webbrowser


def _pick_free_port(host: str) -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def main() -> int:
    from app.config import settings

    parser = argparse.ArgumentParser("medscribe-report-engine")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器界面")
    parser.add_argument("--port-file", default="", help="把实际监听端口写入该文件（供宿主程序读取）")
    args = parser.parse_args()

    port = args.port
    if port == 0:
        port = _pick_free_port(args.host)

    from app.main import create_app

    app = create_app()
    url = f"http://{args.host}:{port}/"

    if args.port_file:
        from pathlib import Path

        Path(args.port_file).write_text(str(port), encoding="utf-8")

    if args.open:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    print(f"[medscribe-report-engine] 服务地址: {url}")
    print(f"[medscribe-report-engine] 接口文档: {url}docs")

    import uvicorn

    uvicorn.run(app, host=args.host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
