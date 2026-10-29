"""命令行冒烟演示：不启动服务，直接跑完整业务链路并写出报告文件。

    report-engine\\.venv\\Scripts\\python.exe scripts\\smoke_demo.py [输出目录] [--template <模板 id>]

（在仓库根目录执行）

用于快速人工检查字段映射质量、校审门禁与导出结果。示例口述来自 app.main.SAMPLES
（合成数据），因此门診 / 病理等模板都有现成语料可直接跑。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]      # 仓库根
ENGINE = ROOT / "report-engine"                  # 引擎工程目录
sys.path.insert(0, str(ENGINE))

from app.config import Settings  # noqa: E402
from app.main import SAMPLES  # noqa: E402
from app.schemas import (  # noqa: E402
    ExportRequest,
    FieldReviewRequest,
    GenerateOptions,
    GenerateRequest,
    SignRequest,
    TranscriptSegment,
)
from app.service import ReportService  # noqa: E402

SEGMENTS = [
    TranscriptSegment(id="S-001", start_ms=0, end_ms=9000, lang="mixed", confidence=0.93,
                      text="陳先生今日返嚟覆診，佢話呢三日有喉嚨痛同咳，無發燒。"),
    TranscriptSegment(id="S-002", start_ms=9000, end_ms=17000, lang="en", confidence=0.91,
                      text="On examination, blood pressure 128 over 76, pulse 82, temperature 36.8 degree Celsius."),
    TranscriptSegment(id="S-003", start_ms=17000, end_ms=23000, lang="en", confidence=0.88,
                      text="No known drug allergy. Past history of hypertension."),
    TranscriptSegment(id="S-004", start_ms=23000, end_ms=29000, lang="en", confidence=0.85,
                      text="Impression is upper respiratory tract infection. I will prescribe panadol 500 mg PO TDS for 3 days."),
    TranscriptSegment(id="S-005", start_ms=29000, end_ms=34000, lang="mixed", confidence=0.55,
                      text="建議多休息多飲水，一星期後返嚟覆診。"),
]


def _parse_args(argv: list[str]) -> tuple[Path, str]:
    out_dir = ENGINE / "docs" / "demo-output"
    template_id = "general_soap"
    rest = list(argv)
    while rest:
        arg = rest.pop(0)
        if arg == "--template" and rest:
            template_id = rest.pop(0)
        else:
            out_dir = Path(arg)
    return out_dir, template_id


def _segments_for(template_id: str) -> list[TranscriptSegment]:
    sample = SAMPLES.get(template_id)
    if not sample:
        return SEGMENTS
    return [TranscriptSegment(**seg) for seg in sample[0]]


def main() -> int:
    out_dir, template_id = _parse_args(sys.argv[1:])
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = Settings()
    service = ReportService(cfg)
    template = service.registry.resolve(template_id)[0]

    response = service.generate(
        GenerateRequest(
            session_id="SES-DEMO",
            template_id=template_id,
            segments=_segments_for(template_id),
            options=GenerateOptions(deidentify=False, term_correction=True, use_llm=False),
        )
    )
    report = response.report

    lines = [
        f"== 模板 {report.template_id} v{report.template_version} / 口述 {len(response.elements)} 个要素 ==",
        "",
        "== 流水线 ==",
        " -> ".join(response.pipeline),
        "",
        "== 抽取要素 ==",
        *(f"[{e.type}] {e.id} score={e.score} src={e.source_segment_ids} :: {e.text}" for e in response.elements),
        "",
        f"== 报告初稿 {report.id}（模板 {report.template_id} v{report.template_version}）==",
        *(
            f"- {f.key:<28} {f.ai_value}"
            + (f"   <来源 {f.sources[0].segment_id} {f.sources[0].start_ms}-{f.sources[0].end_ms}ms>" if f.sources else "   <无来源>")
            + (f"   [{'/'.join(f.flags)}]" if f.flags else "")
            + (f"   {f.note}" if f.note else "")
            for f in sorted(report.fields, key=lambda f: f.order)
        ),
        "",
        f"缺失必填：{report.missing_required or '无'}",
        "警告：",
        *(f"  - {w}" for w in report.warnings),
        "",
    ]

    # 走完校审 -> 签署 -> 导出，验证门禁链路
    for field in report.fields:
        if field.required:
            service.review_field(report.id, field.key, FieldReviewRequest(action="accept", reviewer="dr-demo"))

    sign_result = service.sign(report.id, SignRequest(signed_by="dr-demo"))
    lines.append(f"签署结果：ok={sign_result.ok} {sign_result.message}")

    language_mode = "en" if template.language == "en" else "bilingual"
    records = service.export(
        report.id,
        ExportRequest(formats=["text", "json", "pdf", "html"], language_mode=language_mode),
    )
    lines.append(f"导出文件（language_mode={language_mode}）：")
    lines.extend(f"  - {r.format:<5} {r.path}" for r in records)

    lines.append("")
    lines.append("== 导出文本 ==")
    lines.append(next(r.path for r in records if r.format == "text"))

    (out_dir / f"smoke_demo_{report.template_id}.txt").write_text("\n".join(lines), encoding="utf-8")
    for record in records:
        src = Path(record.path)
        if src.is_file():
            (out_dir / src.name).write_bytes(src.read_bytes())

    print(f"smoke demo written to {out_dir}")
    print(f"signed={sign_result.ok} exports={len(records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
