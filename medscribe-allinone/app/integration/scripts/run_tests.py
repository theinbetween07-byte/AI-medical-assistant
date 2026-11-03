"""统一的集成测试入口。

背景：一体化包使用 Python embeddable 发行版（`python312._pth` 未包含 cwd），
直接 `python -m unittest tests.test_integration` 会导入失败，之前只能靠一长串
`python -c` 绕过。本脚本固化路径与发现规则，供人工与 CI 共用。

    python scripts/run_tests.py
    python scripts/run_tests.py -v --failfast
"""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 MedScribe HK 集成测试")
    parser.add_argument("-v", "--verbose", action="store_true", help="逐条输出用例名")
    parser.add_argument("--failfast", action="store_true", help="首个失败即停止")
    parser.add_argument(
        "--pattern", default="test_*.py", help="测试文件匹配模式（默认 test_*.py）"
    )
    args = parser.parse_args()

    for path in (ROOT, ROOT / "tests"):
        entry = str(path)
        if entry not in sys.path:
            sys.path.insert(0, entry)

    suite = unittest.defaultTestLoader.discover(
        str(ROOT / "tests"), pattern=args.pattern, top_level_dir=str(ROOT)
    )
    runner = unittest.TextTestRunner(
        verbosity=2 if args.verbose else 1,
        failfast=args.failfast,
        stream=sys.stdout,
    )
    result = runner.run(suite)
    print(
        f"\nRESULT ok={result.wasSuccessful()} run={result.testsRun} "
        f"failures={len(result.failures)} errors={len(result.errors)} "
        f"skipped={len(result.skipped)}"
    )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
