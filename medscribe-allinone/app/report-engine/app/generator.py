"""报告生成：模板 + 要素 -> 字段化初稿（FR-05 / FR-06）。

确定性内核：字段值只能由已抽取的要素文本拼接而成，
未被口述覆盖的字段一律写"未提及"，绝不填充推测内容（BR-03）。

字段选材优先级（从强到弱）：
  1. 章节标题：口述出现「MACROSCOPIC EXAMINATION」这类标题后，后续内容归该字段，
     且**标题作用域强于关键词**——镜下描述里出现 "invading through" 不会把整段
     搬去浸润深度字段（v1.0.2 的实测错位就出在这里）；
  2. 标签直配：「Tumour site: sigmoid colon」按最长标签匹配，值取冒号之后的部分；
     口述提示语（病人講 / 主訴 / complains of）没有冒号时也剥掉，只留症状内容；
  3. 关键词匹配：命中越长的关键词越具体（"past history" 优先于 "history"）；
     同一 S/O/A/P 分区内，无关键词的续写句归上一句所属字段（查体续述）；
  4. 兜底：accepts_fallback 的字段按模板顺序领取尚未被认领的内容。

派生字段（模板声明 compose）：本字段没有被口述/粘贴覆盖时，用其他字段**已抽取值**拼一句话
（如病理报告的 DIAGNOSIS 段），只做连接不做推断，并标记"自动生成，请核对"；缺项则放弃拼装（BR-03）。
"""

from __future__ import annotations

import re
import uuid
from functools import lru_cache

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
    """命中的最长关键词长度：越长越具体（"past history" 优先于 "history"）。

    3 个字母以内的纯 ASCII 关键词按词边界匹配，避免 "BD"（每日两次）命中
    "abdominal"、PO 命中 "post" 这类子串误配。
    """
    haystack = _element_text(element).lower()
    best = 0
    for keyword in keywords:
        kw = keyword.lower()
        if not kw:
            continue
        if kw.isascii() and len(kw) <= 3:
            hit = re.search(rf"(?<![a-z]){re.escape(kw)}(?![a-z])", haystack)
        else:
            hit = kw in haystack
        if hit:
            best = max(best, len(keyword))
    return best


def _sort_key(element: ClinicalElement) -> tuple[int, str]:
    if element.sources:
        return (element.sources[0].start_ms, element.id)
    return (10**9, element.id)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


# 口述提示语：这类前缀不是字段标签，而是"病人说"的引语，必须剥掉。
# 例：「病人講喉嚨痛，痛咗三日」若整句保留，主诉字段会变成一句带叙事主语的二手转述。
SPEECH_CUES = (
    "病人講", "病人讲", "病人說", "病人说", "主訴", "主诉",
    "chief complaint", "complaint of", "complains of", "complains", "complain",
    "c/o", "presents with", "presented with",
)
_SPEECH_CUE_RES = tuple(
    (
        cue,
        re.compile(
            rf"^{re.escape(cue)}(?![a-z])\s*[:：,，]?\s*" if cue.isascii()
            else rf"^{re.escape(cue)}\s*[:：,，]?\s*",
            re.IGNORECASE,
        ),
    )
    for cue in SPEECH_CUES
)


def _strip_speech_cue(raw: str) -> str:
    """剥掉句首引语；剥完为空则保留原句，避免产生空字段值。"""
    for _cue, pattern in _SPEECH_CUE_RES:
        match = pattern.match(raw)
        if match:
            rest = raw[match.end():].strip().rstrip("。.").strip()
            return rest or raw
    return raw


@lru_cache(maxsize=None)
def _prefix_pattern(prefix: str) -> re.Pattern:
    """「标签 + 可选分隔符 + 值」的正则，按标签缓存。

    v1.0.2 在 assign_elements 里为每个要素、每个字段、每个标签现编译一次正则，
    300 句时报文分配要 71 ms，其中大部分耗在重复编译上。
    """
    return re.compile(
        r"^"
        + re.escape(prefix)
        + r"\s*\.?\s*(?:(?::|：|is|was|were|are|為|为)\s*(?P<value>.*\S)|(?P<rest>.*\S))\s*$",
        re.IGNORECASE | re.DOTALL,
    )



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
            # 有显式冒号/系动词才剥掉标签；口述里 "tumor size 6 cm" /
            # "主诉反复上腹痛" 没有分隔符，标签只用于确定字段归属，
            # 值保留整句，否则会变成 "6 cm" 这种丢上下文的碎片。
            match = _prefix_pattern(prefix).match(raw)
            if not match:
                continue
            if match.group("value") is not None:
                value = match.group("value").strip().rstrip("。.").strip()
                value = value.lstrip(" ,，、;；:：").strip()
            else:
                # 无分隔符：保留整句，但剥掉「病人講 / complains of」这类引语，
                # 否则主诉字段会变成一句带叙事主语的转述
                value = _strip_speech_cue(raw.strip())
            candidate = (len(prefix), -template_field.order, template_field.key, value)
            if best is None or candidate[:3] > best[:3]:
                best = candidate
    return (best[2], best[3]) if best else None


def _looks_like_label(text: str, key: str, ordered: list[TemplateField]) -> bool:
    """整句只是字段标签（如 "histological tumor grade"），不应作为字段值。"""
    template_field = next((f for f in ordered if f.key == key), None)
    if template_field is None:
        return False
    norm = text.lower().rstrip(" .,:;，。：；")
    if not norm or any(ch.isdigit() for ch in norm):
        return False
    return any(norm.endswith(k.lower()) for k in template_field.keywords if k)


def assign_elements(
    template: ReportTemplate,
    elements: list[ClinicalElement],
) -> tuple[dict[str, list[ClinicalElement]], list[ClinicalElement], dict[str, str]]:
    """要素 -> 字段的确定性分配，返回 (字段->要素, 未被覆盖的要素, 值覆盖表)。

    每个要素最多归属一个字段，保证来源引用不重复、可追溯（FR-04）。
    """
    ordered = template.sorted_fields()
    by_key = {f.key: f for f in ordered}
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

    def best_keyword_owner(element: ClinicalElement) -> tuple[int, int, str] | None:
        best: tuple[int, int, str] | None = None
        for template_field in ordered:
            keywords = [k for k in template_field.keywords if k]
            if not keywords:
                continue
            # 非病理模板按 S/O/A/P 分区池化：字段只在自己的分区里选材。
            # 用分区比较而不是 `element in pool_for[key]`，避免 300 句时的列表相等比较开销。
            if not template.pool_all_sections and element.type != template_field.section:
                continue
            score = _keyword_score(element, keywords)
            if not score:
                continue
            candidate = (score, -template_field.order, template_field.key)
            if best is None or candidate > best:
                best = candidate
        return best

    # 每个 S/O/A/P 分区里「承接剩余叙述」的字段（模板中第一个 accepts_fallback 的字段）。
    # 通用 SOAP 里是现病史：开场白、咳嗽、无发烧这类拆不开的 S 句归它，而不是并进主诉。
    residual_by_section: dict[str, str] = {}
    for template_field in ordered:
        section = template_field.section
        if (
            template_field.accepts_fallback
            and section != "uncategorized"
            and section not in residual_by_section
        ):
            residual_by_section[section] = template_field.key

    # 1) 章节标题 + 2) 标签直配 + 3) 作用域内归属（按时间顺序扫描）
    scope: str | None = None
    # weak：标签/关键词建立的段落，遇到更具体的关键词可以被抢走；
    # strong：整句标题建立的段落（MACROSCOPIC / MICROSCOPIC / DIAGNOSIS），
    #         段落内的叙述一律归该字段，关键词只用于段落内的字段定位而不跳段。
    scope_strength = "weak"
    owners: dict[str, tuple[int, int, str]] = {}
    last_owner: dict[str, str] = {}
    last_owner_any: str | None = None

    for element in sorted(elements, key=_sort_key):
        heading = _heading_owner(element.text, ordered)
        if heading:
            scope = heading
            scope_strength = "strong"
            claimed.add(element.id)  # 标题自身不进字段值，避免污染
            continue

        direct = _prefix_owner(element.text, ordered)
        if direct:
            key, value = direct
            if value:
                selection[key].append(element)
                overrides[element.id] = value
            claimed.add(element.id)  # 纯标签句自身不进值，只用来开启段落归属
            if template.pool_all_sections:
                scope = key
                scope_strength = "weak"
            last_owner[by_key[key].section] = key
            last_owner_any = key
            continue

        if scope and scope_strength == "strong":
            # 标题段落内的叙述不再让关键词抢走：镜下描述里的
            # "invading through / lymph nodes / margins" 曾把整段搬进 synoptic 字段
            selection[scope].append(element)
            claimed.add(element.id)
            last_owner_any = scope
            continue

        best = best_keyword_owner(element)
        if best:
            owners[element.id] = best
            key = best[2]
            last_owner[by_key[key].section] = key
            last_owner_any = key
            if template.pool_all_sections:
                # 病理 synoptic 报告：标签即段落，后续无标签句归入该字段
                scope = key
                scope_strength = "weak"
                if _looks_like_label(element.text, key, ordered):
                    claimed.add(element.id)
            continue

        if scope:
            # 弱作用域续写；关键词已经在上面优先处理过
            selection[scope].append(element)
            claimed.add(element.id)
            last_owner_any = scope
            continue

        # 没有临床关键词的口语残句（如查体段的「Throat is erythematous」）：
        # 非病理模板下跟随上一句的字段，避免内容被静默丢弃（AC-04）。
        if not template.pool_all_sections and element.type == "uncategorized" and last_owner_any:
            selection[last_owner_any].append(element)
            claimed.add(element.id)
            continue

        # 分区残句归属：模板给该分区指定了「承接剩余叙述」的字段（现病史）时，
        # 拆不开归属的句子直接归它，不要再被上一句的字段顺手带走。
        if not template.pool_all_sections:
            residual = residual_by_section.get(element.type)
            if residual:
                selection[residual].append(element)
                claimed.add(element.id)
                last_owner_any = residual
                continue

        # 查体续述：O 段没有关键词的句子归上一句所属字段
        # （「On examination, BP 128/76…」之后的「Throat is mildly injected」）。
        # 只在 O 段启用：S 段的零散叙述通常是现病史，不应并进主诉。
        if not template.pool_all_sections and element.type == "O":
            key = last_owner.get(element.type)
            if key:
                selection[key].append(element)
                claimed.add(element.id)
                last_owner_any = key

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

    unclaimed = [e for e in elements if e.id not in claimed]
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
        parts = [
            overrides.get(e.id, _element_text(e))
            for e in selected
            if overrides.get(e.id, _element_text(e))
        ]
        # 同一字段同时被 synoptic 行和镜下重述两遍时只留一份：
        # "Lymphatic and venous invasion: present" + "Lymphovascular invasion is present"
        # 曾拼成 "present present"。
        unique: list[str] = []
        seen: set[str] = set()
        for part in parts:
            key_norm = re.sub(r"\s+", " ", part.strip().rstrip("。.")).lower()
            if key_norm in seen:
                continue
            seen.add(key_norm)
            unique.append(part)
        value = template.field_separator.join(unique).strip()
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

    # 全空不是"正常留白"：多数情况是模板与口述科室不匹配（如病理标本用了
    # 门诊模板），或转写文本无法归类。必须给出可操作提示，而不是让用户
    # 对着一屏"未提及"自己猜（实测报障点）。
    filled = [f for f in fields if f.ai_value and f.ai_value != PLACEHOLDER_MISSING]
    if elements and not filled:
        warnings.append(
            "所有字段均为「未提及」：口述内容没有落入当前模板的任何字段。"
            "请先确认模板选择（病理标本/会诊录音应选择病理报告模板），"
            "再检查转写文本是否完整、是否选对了识别语言。"
        )

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

