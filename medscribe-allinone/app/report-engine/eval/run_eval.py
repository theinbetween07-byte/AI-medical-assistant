"""字段级评测：跑语料 → 抽取 → 生成 → 与 gold 比对。

用法（在 app/report-engine 目录下）：
    python eval/run_eval.py
    python eval/run_eval.py --json out.json --verbose

指标：
    gold_pass    gold 要求的片段是否全部出现在对应字段；
    missing_ok   expect_missing 的字段是否保持「未提及」；
    其它         （诊断用）每个字段实际取值。

语料是合成的（NFR-05），只用于回归比较，不代表真实病历分布。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.extractor import ExtractionRules, extract_elements  # noqa: E402
from app.generator import generate_report  # noqa: E402
from app.schemas import TranscriptSegment  # noqa: E402
from app.templates import TemplateRegistry  # noqa: E402

MISSING = "未提及"


def norm(text: str) -> str:
    return re.sub(r"[\s。，,.;；:：、]+", "", unicodedata.normalize("NFKC", text or "").lower())


def run_case(case: dict, rules: ExtractionRules, registry: TemplateRegistry) -> dict:
    template, fallback = registry.resolve(case["template"])
    segments = [
        TranscriptSegment(
            id=f"{case['id']}-S{i:03d}",
            start_ms=i * 3000,
            end_ms=(i + 1) * 3000,
            text=text,
            lang="mixed",
            confidence=0.9,
        )
        for i, text in enumerate(case["segments"])
    ]
    elements = extract_elements(segments, rules)
    report = generate_report(template, elements, session_id=case["id"])
    values = {f.key: f.ai_value for f in report.fields}

    failures: list[str] = []
    gold_pass = 0
    for key, wanted in case.get("gold", {}).items():
        value = norm(values.get(key, ""))
        if not value or value == norm(MISSING):
            failures.append(f"{key}: 未提及（期望包含 {' / '.join(wanted)}）")
            continue
        missed = [w for w in wanted if norm(w) not in value]
        if missed:
            failures.append(f"{key}: 缺少 {' / '.join(missed)}｜实际={values.get(key)}")
        else:
            gold_pass += 1

    missing_ok = 0
    leaks = 0
    for key in case.get("expect_missing", []):
        value = values.get(key, MISSING)
        if value == MISSING:
            missing_ok += 1
        else:
            leaks += 1
            failures.append(f"{key}: 应为未提及，实际={value}")

    return {
        "id": case["id"],
        "template": template.id,
        "fallback": fallback,
        "gold_total": len(case.get("gold", {})),
        "gold_pass": gold_pass,
        "missing_total": len(case.get("expect_missing", [])),
        "missing_ok": missing_ok,
        "leaks": leaks,
        "failures": failures,
        "values": values,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", default=str(Path(__file__).with_name("corpus.json")))
    parser.add_argument("--json", default="")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    corpus = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    rules = ExtractionRules.load(ROOT / "terms")
    registry = TemplateRegistry(ROOT / "templates")

    results = [run_case(case, rules, registry) for case in corpus["cases"]]

    gold_total = sum(r["gold_total"] for r in results)
    gold_pass = sum(r["gold_pass"] for r in results)
    missing_total = sum(r["missing_total"] for r in results)
    missing_ok = sum(r["missing_ok"] for r in results)
    leaks = sum(r["leaks"] for r in results)

    for result in results:
        status = "PASS" if not result["failures"] else "FAIL"
        print(f"[{status}] {result['id']:8s} gold {result['gold_pass']}/{result['gold_total']}")
        for failure in result["failures"]:
            print(f"        - {failure}")
        if args.verbose:
            for key, value in result["values"].items():
                print(f"        {key:28s} {value}")

    print("\nSUMMARY")
    print(f"gold_fields   {gold_pass}/{gold_total}  ({gold_pass / max(1, gold_total):.1%})")
    print(f"missing_ok    {missing_ok}/{missing_total}")
    print(f"leaks         {leaks}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "corpus_version": corpus.get("version"),
                    "summary": {
                        "gold_fields": gold_total,
                        "gold_pass": gold_pass,
                        "gold_pass_rate": round(gold_pass / max(1, gold_total), 4),
                        "missing_total": missing_total,
                        "missing_ok": missing_ok,
                        "leaks": leaks,
                    },
                    "cases": results,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
