"""报告生成：模板 + 要素 -> 字段化初稿（FR-05 / FR-06）。

确定性内核：字段值只能由已抽取的要素文本拼接而成，
未被口述覆盖的字段一律写"未提及"，绝不填充推测内容（BR-03）。

字段选材优先级（从强到弱）：
  1. 章节标题：口述出现「MACROSCOPIC EXAMINATION」这类标题后，后续内容归该字段；
  2. 标签直配：「Tumour site: sigmoid colon」按最长标签匹配，值取冒号之后的部分；
  3. 关键词匹配：命中越长的关键词越具体（"past history" 优先于 "history"）；
  4. 兜底：accepts_fallback 的字段按模板顺序领取尚未被认领的内容。

派生字段（模板声明 compose）：本字段没有被口述/粘贴覆盖时，用其他字段**已抽取值**拼一句话
（如病理报告的 DIAGNOSIS 段），只做连接不做推断，并标记"自动生成，请核对"；缺项则放弃拼装（BR-03）。
"""

from __future__ import annotations

import re
import uuid

from .schemas import (
    ClinicalElement,
    GeneratedReport,
    ReportField,
    ReportTemplate,
    TemplateField,
    utcnow_iso,
)
from .validation import PLACEHOLDER_MISSING

COMPOSE_FLAG = "derived"
_PLACEHOLDER_RE = re.compile(r"\{([a-z0-9_]+)\}")


def _element_text(element: ClinicalElement) -> str:
    return (element.text or "").strip()


def _matches_keywords(element: ClinicalElement, keywords: list[str]) -> bool:
    return _keyword_score(element, keywords) > 0


def _keyword_score(element: ClinicalElement, keywords: list[str]) -> int:
    """命中的最长关键词长度：越长越具体（"past history" 优先于 "history"）。"""
    haystack = _element_text(element).lower()
    best = 0
    for keyword in keywords:
        if keyword and keyword.lower() in haystack:
            best = max(best, len(keyword))
    return best


def _sort_key(element: ClinicalElement) -> tuple[int, str]:
    if element.sources:
        return (element.sources[0].start_ms, element.id)
    return (10**9, element.id)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _label_parts(template_field: TemplateField) -> list[str]:
    """字段可用的标签前缀：关键词 + label 的中英文部分。"""
    parts = [k.strip() for k in template_field.keywords if k and k.strip()]
    parts += [p.strip() for p in template_field.label.split(" / ") if p.strip()]
    return parts


def _heading_owner(text: str, ordered: list[TemplateField]) -> str | None:
    """严格标题匹配：整个句子就是某字段的章节标题时，返回该字段 key。

    仅做"整句相等"判断，避免把含标题词的正文误判为标题切换。
    """
    candidate = _norm(text).strip(" 　:：。.").strip().lower()
    if not candidate or len(candidate) > 60:
        return None
    for template_field in ordered:
        for heading in template_field.scope_headings:
            if heading and candidate == heading.strip().strip(":：").lower():
                return template_field.key
    return None


def _prefix_owner(
    text: str, ordered: list[TemplateField]
) -> tuple[str, str] | None:
    """「标签: 值」直配，返回 (字段 key, 冒号之后的值)；多字段命中时取最长标签。

    兼容口述与打字两种习惯：``Tumour site: sigmoid colon`` /
    ``Tumour site is sigmoid colon`` / ``Case No.: SP-2026-004521``。
    """
    raw = _norm(text)
    best: tuple[int, int, str, str] | None = None
    for template_field in ordered:
        for prefix in _label_parts(template_field):
            pattern = re.compile(
                r"^"
                + re.escape(prefix)
                + r"\s*\.?\s*(?::|：|is|was|were|are|of)?\s*(?P<value>.*\S)\s*$",
                re.IGNORECASE | re.DOTALL,
            )
            match = pattern.match(raw)
            if not match:
                continue
            value = match.group("value").strip().rstrip("。.").strip()
            if not value:
                continue
            candidate = (len(prefix), -template_field.order, template_field.key, value)
            if best is None or candidate[:3] > best[:3]:
                best = candidate
    return (best[2], best[3]) if best else None


def assign_elements(
    template: ReportTemplate,
    elements: list[ClinicalElement],
) -> tuple[dict[str, list[ClinicalElement]], list[ClinicalElement], dict[str, str]]:
    """要素 -> 字段的确定性分配，返回 (字段->要素, 未被覆盖的要素, 值覆盖表)。

    每个要素最多归属一个字段，保证来源引用不重复、可追溯（FR-04）。
    """
    ordered = template.sorted_fields()
    selection: dict[str, list[ClinicalElement]] = {f.key: [] for f in ordered}
    overrides: dict[str, str] = {}
    claimed: set[str] = set()

    if template.pool_all_sections:
        # 病理等报告类型：章节靠标题划分，内容天然跨 S/O/A/P，统一入池
        pool_for: dict[str, list[ClinicalElement]] = {f.key: list(elements) for f in ordered}
    else:
        grouped: dict[str, list[ClinicalElement]] = {}
        for element in elements:
            grouped.setdefault(element.type, []).append(element)
        pool_for = {f.key: grouped.get(f.section, []) for f in ordered}

    # 1) 章节标题 + 2) 标签直配 + 3) 作用域内归属（按时间顺序扫描）
    scope: str | None = None
    owners: dict[str, tuple[int, int, str]] = {}
    for element in sorted(elements, key=_sort_key):
        heading = _heading_owner(element.text, ordered)
        if heading:
            scope = heading
            claimed.add(element.id)  # 标题自身不进字段值，避免污染
            continue
        direct = _prefix_owner(element.text, ordered)
        if direct:
            selection[direct[0]].append(element)
            overrides[element.id] = direct[1]
            claimed.add(element.id)
            continue
        if scope:
            selection[scope].append(element)
            claimed.add(element.id)
            continue

        best: tuple[int, int, str] | None = None
        for template_field in ordered:
            keywords = [k for k in template_field.keywords if k]
            if not keywords or element not in pool_for[template_field.key]:
                continue
            score = _keyword_score(element, keywords)
            if not score:
                continue
            candidate = (score, -template_field.order, template_field.key)
            if best is None or candidate > best:
                best = candidate
        if best:
            owners[element.id] = best

    for element in sorted(elements, key=_sort_key):
        if element.id in claimed:
            continue
        owner = owners.get(element.id)
        if owner is None:
            continue
        selection[owner[2]].append(element)
        claimed.add(element.id)

    # 4) 兜底：不给结构化字段塞无关内容（accepts_fallback=False 时宁可留"未提及"）
    for template_field in ordered:
        if selection[template_field.key] or not template_field.accepts_fallback:
            continue
        for element in sorted(pool_for[template_field.key], key=_sort_key):
            if element.id not in claimed:
                selection[template_field.key].append(element)
                claimed.add(element.id)
                break

    for key in selection:
        selection[key] = sorted(selection[key], key=_sort_key)

    unclaimed = [
        e
        for e in elements
        if e.id not in claimed and (e.type != "uncategorized" or template.pool_all_sections)
    ]
    return selection, unclaimed, overrides


def _clean_fragment(value: str) -> str:
    """拼装用的碎片：去掉首尾空白与句末句号，避免出现「..., .」这类拼接痕迹。"""
    return (value or "").strip().rstrip("。.")


def _compose_fields(
    template: ReportTemplate, fields: list[ReportField]
) -> list[str]:
    """就地填充派生字段，返回说明清单（用于报告 warnings）。"""
    by_key = {f.key: f for f in fields}
    notes: list[str] = []
    english = template.language == "en"

    for template_field in template.sorted_fields():
        spec = template_field.compose
        if spec is None or not spec.pattern:
            continue
        field = by_key.get(template_field.key)
        if field is None or field.ai_value not in {"", PLACEHOLDER_MISSING}:
            continue  # 已由口述/粘贴提供，直接采用原值

        placeholders = list(dict.fromkeys(_PLACEHOLDER_RE.findall(spec.pattern)))
        required = set(spec.required_keys) or set(placeholders)
        used: list[str] = []
        values: dict[str, str] = {}
        missing_required: list[str] = []
        for key in placeholders:
            source = by_key.get(key)
            value = _clean_fragment(source.ai_value) if source else ""
            if not value or value == PLACEHOLDER_MISSING:
                if key in required:
                    missing_required.append(key)
                continue
            values[key] = value
            used.append(key)

        if missing_required:
            detail = ", ".join(missing_required)
            field.note = (
                f"{template_field.label} not auto-generated (missing: {detail})"
                if english
                else f"未自动生成{template_field.label}：缺少 {detail}"
            )
            notes.append(f"{field.note} / 缺少口述内容，请补录后再签署")
            continue

        composed = _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), ""), spec.pattern)
        composed = re.sub(r"[,;:]\s*(?=[,;:])", "", composed)  # 去掉可选片段缺失留下的重复分隔符
        composed = re.sub(r"\s*[,;:]\s*\.", ".", composed)
        composed = re.sub(r"\s+", " ", composed).strip()
        field.ai_value = composed[:1].upper() + composed[1:]
        field.sources = [span for key in used for span in by_key[key].sources]
        field.flags = sorted(set(field.flags) | {COMPOSE_FLAG})
        field.note = (
            f"Auto-generated from: {', '.join(used)}. Please verify."
            if english
            else "由以下字段自动生成：" + "、".join(used) + "。请核对。"
        )
        notes.append(
            f"字段 {template_field.key} 由模板按已抽取值拼装生成，已标记「自动生成，请核对」"
            f" / {template_field.key} auto-generated from extracted fields - please verify"
        )
    return notes


def build_fields(
    template: ReportTemplate,
    elements: list[ClinicalElement],
) -> tuple[list[ReportField], list[ClinicalElement], list[str]]:
    selection, unclaimed, overrides = assign_elements(template, elements)
    fields: list[ReportField] = []

    for template_field in template.sorted_fields():
        selected = selection.get(template_field.key, [])
        sources = [span for e in selected for span in e.sources]
        value = template.field_separator.join(
            overrides.get(e.id, _element_text(e)) for e in selected
            if overrides.get(e.id, _element_text(e))
        ).strip()
        flags: list[str] = []
        if any(
            rule.startswith("low_confidence") for e in selected for rule in e.matched_rules
        ):
            flags.append("low_confidence")

        fields.append(
            ReportField(
                key=template_field.key,
                label=template_field.label,
                section=template_field.section,
                required=template_field.required,
                order=template_field.order,
                type=template_field.type,
                ai_value=value or template_field.placeholder_missing,
                sources=sources,
                flags=flags,
            )
        )

    compose_notes = _compose_fields(template, fields)
    return fields, unclaimed, compose_notes


def generate_report(
    template: ReportTemplate,
    elements: list[ClinicalElement],
    session_id: str = "",
    mode: str = "rules",
    fallback_note: str | None = None,
) -> GeneratedReport:
    fields, unclaimed, compose_notes = build_fields(template, elements)

    warnings: list[str] = []
    if fallback_note:
        warnings.append(fallback_note)
    if unclaimed:
        warnings.append(
            f"有 {len(unclaimed)} 条已抽取内容未被当前模板字段覆盖，请检查模板配置或补充字段："
            + "、".join(e.id for e in unclaimed)
        )
    warnings.extend(compose_notes)

    return GeneratedReport(
        id=f"RPT-{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        template_id=template.id,
        template_name=template.name,
        template_version=template.version,
        status="draft",
        created_at=utcnow_iso(),
        fields=fields,
        warnings=warnings,
        mode=mode,  # type: ignore[arg-type]
    )
