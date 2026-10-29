"""导出（FR-09）：纯文本 / JSON / PDF / HTML。

文本用于粘贴进 EHR，JSON 保留字段级来源引用，PDF 用于打印归档。
中英对照模式按 label 的 "中文 / English" 结构取舍。
"""

from __future__ import annotations

import json
from pathlib import Path
from xml.sax.saxutils import escape

from .schemas import ExportRecord, GeneratedReport, LanguageMode
from .validation import PLACEHOLDER_MISSING

_FLAG_LABEL = {
    "missing": "未提及",
    "unsupported": "待核实",
    "dose_mismatch": "剂量待核实",
    "low_confidence": "低置信度，请核对",
    "derived": "自动生成，请核对",
}

_FLAG_LABEL_EN = {
    "missing": "not mentioned",
    "unsupported": "verify",
    "dose_mismatch": "verify dose",
    "low_confidence": "low confidence",
    "derived": "auto-generated",
}

# 英文报告（如病理模板）里占位符也应是英文，但内部仍统一存 "未提及" 以便门禁判定（BR-10）
PLACEHOLDER_MISSING_EN = "Not mentioned"


def _flag_label(flag: str, mode: LanguageMode) -> str:
    table = _FLAG_LABEL_EN if mode == "en" else _FLAG_LABEL
    return table.get(flag, flag)


def _render_value(value: str, mode: LanguageMode) -> str:
    if mode == "en" and value.strip() == PLACEHOLDER_MISSING:
        return PLACEHOLDER_MISSING_EN
    return value


def _meta_line(report: GeneratedReport, mode: LanguageMode, with_mode: bool = False) -> str:
    if mode == "en":
        parts = [
            f"Template v{report.template_version}",
            f"Generated {report.created_at}",
            f"Status {report.status}",
        ]
        if with_mode:
            parts.append(f"Mode {report.mode}")
        return " · ".join(parts)
    parts = [
        f"模板版本 {report.template_version}",
        f"生成时间 {report.created_at}",
        f"状态 {report.status}",
    ]
    if with_mode:
        parts.append(f"模式 {report.mode}")
    return " · ".join(parts)


def _label(label: str, mode: LanguageMode) -> str:
    if " / " in label:
        zh, en = label.split(" / ", 1)
        if mode == "en":
            return en.strip()
        if mode == "zh":
            return zh.strip()
    return label.strip()


def _field_value(report: GeneratedReport, key: str) -> str:
    field = report.field(key)
    return field.effective_value() if field else ""


def to_text(report: GeneratedReport, mode: LanguageMode = "bilingual") -> str:
    lines: list[str] = []
    title = _label(report.template_name or report.template_id, mode)
    lines.append(title)
    lines.append(_meta_line(report, mode))
    if report.signed_at:
        prefix = "Signed" if mode == "en" else "签署"
        lines.append(f"{prefix}: {report.signed_by or 'demo-doctor'} @ {report.signed_at}")
    lines.append("-" * 60)

    separator = ": " if mode == "en" else "："
    for field in sorted(report.fields, key=lambda f: f.order):
        value = _render_value(field.effective_value(), mode)
        marks = [_flag_label(f, mode) for f in field.flags if f in _FLAG_LABEL]
        if marks and field.effective_value() not in {PLACEHOLDER_MISSING, ""}:
            value = f"{value}  [{'，'.join(sorted(set(marks)))}]"
        lines.append(f"{_label(field.label, mode)}{separator}{value}")

    if report.warnings:
        lines.append("-" * 60)
        lines.append("Notes:" if mode == "en" else "提示：")
        lines.extend(f"  - {w}" for w in report.warnings)
    return "\n".join(lines)


def to_html(report: GeneratedReport, mode: LanguageMode = "bilingual") -> str:
    rows: list[str] = []
    for field in sorted(report.fields, key=lambda f: f.order):
        marks = [_flag_label(f, mode) for f in field.flags if f in _FLAG_LABEL]
        badge = "".join(f'<span class="flag">{escape(m)}</span>' for m in sorted(set(marks)))
        rows.append(
            "<tr>"
            f'<th>{escape(_label(field.label, mode))}{"<em>*</em>" if field.required else ""}</th>'
            f"<td>{escape(_render_value(field.effective_value(), mode))}{badge}</td>"
            "</tr>"
        )
    lang = "en" if mode == "en" else "zh-HK"
    notes_heading = "Notes" if mode == "en" else "提示"
    return (
        f"<!doctype html><html lang=\"{lang}\"><head><meta charset=\"utf-8\">"
        f"<title>{escape(report.template_name)}</title>"
        "<style>body{font-family:'Segoe UI','Microsoft JhengHei',sans-serif;margin:32px;}"
        "table{border-collapse:collapse;width:100%;}th,td{border:1px solid #d9d9d9;padding:8px 10px;"
        "text-align:left;vertical-align:top;}th{width:28%;background:#f7f9fc;}"
        ".flag{color:#b42318;border:1px solid #f3b7b1;background:#fff2f0;border-radius:10px;"
        "font-size:12px;padding:1px 6px;margin-left:6px;}em{color:#b42318;}"
        "h1{font-size:18px;}small{color:#666;}</style></head><body>"
        f"<h1>{escape(_label(report.template_name, mode))}</h1>"
        f"<p><small>{escape(_meta_line(report, mode, with_mode=True))}</small></p>"
        "<table><tbody>"
        + "".join(rows)
        + "</tbody></table>"
        + (
            f"<h3>{notes_heading}</h3><ul>"
            + "".join(f"<li>{escape(w)}</li>" for w in report.warnings)
            + "</ul>"
            if report.warnings
            else ""
        )
        + "</body></html>"
    )


def to_json(report: GeneratedReport, mode: LanguageMode = "bilingual") -> str:
    payload = json.loads(report.model_dump_json())
    for field in payload.get("fields", []):
        field["display_label"] = _label(field.get("label", ""), mode)
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------ PDF


def _pdf_font() -> tuple[str, str]:
    """注册中文字体；失败时退回内置 Helvetica（不阻断导出）。"""
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont

        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        return "STSong-Light", "STSong-Light"
    except Exception:  # noqa: BLE001
        return "Helvetica", "Helvetica-Bold"


def to_pdf(report: GeneratedReport, path: Path, mode: LanguageMode = "bilingual") -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    base_font, bold_font = _pdf_font()
    path.parent.mkdir(parents=True, exist_ok=True)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "MshkTitle", parent=styles["Title"], fontName=bold_font, fontSize=15, leading=20
    )
    meta_style = ParagraphStyle(
        "MshkMeta", parent=styles["Normal"], fontName=base_font, fontSize=8,
        leading=12, textColor=colors.HexColor("#666666"),
    )
    label_style = ParagraphStyle(
        "MshkLabel", parent=styles["Normal"], fontName=bold_font, fontSize=9.5, leading=13
    )
    value_style = ParagraphStyle(
        "MshkValue", parent=styles["Normal"], fontName=base_font, fontSize=9.5, leading=13.5
    )

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=report.template_name,
    )

    story = [
        Paragraph(escape(_label(report.template_name or report.template_id, mode)), title_style),
        Paragraph(escape(_meta_line(report, mode, with_mode=True)), meta_style),
        Spacer(1, 6 * mm),
    ]

    data = []
    for field in sorted(report.fields, key=lambda f: f.order):
        marks = [_flag_label(f, mode) for f in field.flags if f in _FLAG_LABEL]
        value = escape(_render_value(field.effective_value(), mode))
        if marks and field.effective_value() not in {PLACEHOLDER_MISSING, ""}:
            value += f' <font color="#b42318">[{"，".join(sorted(set(marks)))}]</font>'
        label = escape(_label(field.label, mode)) + (" *" if field.required else "")
        data.append([Paragraph(label, label_style), Paragraph(value, value_style)])

    table = Table(data, colWidths=[52 * mm, 118 * mm])
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d9d9d9")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f7f9fc")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(table)

    if report.warnings:
        story.append(Spacer(1, 5 * mm))
        story.append(Paragraph("提示 / Notes", label_style))
        for warning in report.warnings:
            story.append(Paragraph(f"· {escape(warning)}", value_style))

    doc.build(story)
    return path


def export_report(
    report: GeneratedReport,
    formats: list[str],
    out_dir: Path,
    mode: LanguageMode = "bilingual",
) -> list[ExportRecord]:
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[ExportRecord] = []
    for fmt in formats:
        if fmt == "text":
            path = out_dir / f"{report.id}.txt"
            path.write_text(to_text(report, mode), encoding="utf-8")
        elif fmt == "json":
            path = out_dir / f"{report.id}.json"
            path.write_text(to_json(report, mode), encoding="utf-8")
        elif fmt == "html":
            path = out_dir / f"{report.id}.html"
            path.write_text(to_html(report, mode), encoding="utf-8")
        elif fmt == "pdf":
            path = to_pdf(report, out_dir / f"{report.id}.pdf", mode)
        else:
            continue
        records.append(
            ExportRecord(report_id=report.id, format=fmt, path=str(path))  # type: ignore[arg-type]
        )
    return records
