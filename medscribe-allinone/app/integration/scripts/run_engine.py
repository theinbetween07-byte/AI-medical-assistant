"""报告引擎启动垫片（解决嵌入式 Python 的 ._pth 路径限制）。

背景：
    medical-ai-assistant 的便携 Python 带 `python312._pth`，其中 `sys.path` 被写死为
    `.<python目录>`、`..\\<backend>`、`Lib\\site-packages`，**脚本所在目录不会入 path**。
    因此 `python run.py` 在引擎目录里执行时，`import app.config` 会命中 ASR 的
    `backend/app/config.py`，直接 ImportError（两个上游包的顶层名都叫 app）。

做法：
    显式把引擎根目录插到 sys.path[0]，再 runpy 执行它的 run.py，argv 原样透传。

    python run_engine.py <引擎目录> [--port 8790 ...]
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("用法：python run_engine.py <报告引擎目录> [额外参数...]")
        return 2
    engine_dir = Path(sys.argv[1]).resolve()
    entry = engine_dir / "run.py"
    if not entry.is_file():
        print(f"找不到引擎入口：{entry}")
        return 2

    sys.argv = [str(entry), *sys.argv[2:]]
    sys.path.insert(0, str(engine_dir))
    runpy.run_path(str(entry), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
