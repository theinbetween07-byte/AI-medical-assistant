"""可选 LLM 增强（AI-03）。默认关闭，符合 NFR-04 / NFR-06。

设计原则：
  1. 只有显式配置并开启（settings.llm_enabled）时才可能被调用；
  2. 只发送文本，绝不发送音频；
  3. 模型输出必须通过来源校验，未通过则回退到规则内核的结果（防幻觉，AI-03 目标幻觉字段数 = 0）；
  4. 调用次数与超时受配置上限约束（NFR-11）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .config import Settings
from .schemas import ClinicalElement, GeneratedReport
from .validation import evidence_ratio

_SYSTEM_PROMPT = (
    "你是病历整理助手。只能使用给定的口述来源文本中的信息，禁止推断、补全或编造任何临床内容。"
    "无法确定的内容必须写 \"未提及\"。输出必须是严格 JSON，键为给定字段 key，值为整理后的文本。"
    "不要输出 JSON 以外的任何字符。"
)


class LLMRefiner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.calls = 0

    @property
    def available(self) -> bool:
        s = self.settings
        return bool(s.llm_enabled and s.llm_base_url and s.llm_model)

    def _chat(self, messages: list[dict]) -> str:
        s = self.settings
        url = s.llm_base_url.rstrip("/") + "/chat/completions"
        body = json.dumps(
            {
                "model": s.llm_model,
                "messages": messages,
                "temperature": 0,
                "response_format": {"type": "json_object"},
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if s.llm_api_key:
            headers["Authorization"] = f"Bearer {s.llm_api_key}"
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=s.llm_timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload["choices"][0]["message"]["content"]

    def refine(
        self, report: GeneratedReport, elements: list[ClinicalElement]
    ) -> tuple[dict[str, str], str]:
        """返回 (key -> 建议值, 说明)。任何失败都不抛出，交由上层记录。"""
        if not self.available:
            return {}, "LLM 未启用或未配置"
        if self.calls >= self.settings.llm_max_calls_per_report:
            return {}, "已达单份报告的 LLM 调用上限（NFR-11）"

        source_text = "\n".join(
            f"[{e.id}] {e.text}" for e in elements if e.type != "uncategorized"
        )
        field_keys = [f.key for f in report.fields]
        user_prompt = (
            "口述来源（编号为要素 ID）：\n"
            f"{source_text}\n\n"
            f"请针对以下字段 key 输出 JSON：{json.dumps(field_keys, ensure_ascii=False)}\n"
            "要求：每个字段的值必须能由上列来源文本直接支撑；无依据写 \"未提及\"。"
        )
        self.calls += 1
        try:
            content = self._chat(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ]
            )
            suggested = json.loads(content)
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError, OSError) as exc:
            return {}, f"LLM 调用失败，已回退规则内核结果：{exc}"

        if not isinstance(suggested, dict):
            return {}, "LLM 返回结构不符合预期，已回退规则内核结果"

        source_texts = [e.text for e in elements if e.type != "uncategorized"]
        accepted: dict[str, str] = {}
        rejected: list[str] = []
        for key, value in suggested.items():
            if not isinstance(value, str):
                rejected.append(str(key))
                continue
            if value.strip() in {"", "未提及"}:
                continue
            if evidence_ratio(value, source_texts) < 0.75:
                rejected.append(str(key))  # 无来源支撑 -> 丢弃（防幻觉）
                continue
            accepted[key] = value.strip()

        note = f"LLM 精修 {len(accepted)} 个字段"
        if rejected:
            note += f"；{len(rejected)} 个字段因缺少来源支撑被丢弃：{', '.join(sorted(rejected))}"
        return accepted, note
