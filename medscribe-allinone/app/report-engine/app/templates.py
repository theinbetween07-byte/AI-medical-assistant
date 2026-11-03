"""模板注册表（FR-05 / 技术设计 2.3）。

模板以声明式 JSON 定义字段（key/label/section/required/order/keywords），
新增报告类型只需新增文件，不改代码（BR-06 回退通用 SOAP 模板）。
"""

from __future__ import annotations

import json
from pathlib import Path

from .schemas import ReportTemplate, TemplateField

# 内置兜底模板：模板目录缺失或全部损坏时使用
GENERAL_SOAP = ReportTemplate(
    id="general_soap",
    name="通用 SOAP 记录 / General SOAP Note",
    specialty="general",
    version="1.0.0",
    language="bilingual",
    description="通用兜底模板（BR-06）",
    fields=[
        TemplateField(key="chief_complaint", label="主诉 / Chief Complaint", section="S",
                      required=True, order=1, keywords=["chief complaint", "主訴", "主诉", "complain"]),
        TemplateField(key="history_of_present_illness", label="现病史 / History of Present Illness",
                      section="S", order=2, keywords=["history", "現病史", "现病史", "since", "duration"]),
        TemplateField(key="past_medical_history", label="既往史 / Past Medical History",
                      section="S", order=3, keywords=["past history", "既往", "病史"]),
        TemplateField(key="allergy_history", label="过敏史 / Allergy History", section="S",
                      required=True, order=4, keywords=["allergy", "allergic", "過敏", "过敏"]),
        TemplateField(key="physical_examination", label="体格检查 / Physical Examination",
                      section="O", required=True, order=5,
                      keywords=["examination", "檢查", "检查", "O/E", "vitals"]),
        TemplateField(key="investigations", label="检查结果 / Investigations", section="O", order=6,
                      keywords=["blood test", "x-ray", "CT", "investigation", "檢驗", "检验", "報告", "报告"]),
        TemplateField(key="assessment", label="诊断/评估 / Assessment", section="A", required=True, order=7,
                      keywords=["diagnosis", "impression", "assessment", "診斷", "诊断", "評估", "评估"]),
        TemplateField(key="plan", label="处理计划 / Plan", section="P", required=True, order=8,
                      keywords=["plan", "advise", "refer", "follow up", "計劃", "计划", "建議", "建议"]),
        TemplateField(key="medications", label="用药 / Medications", section="P", type="medication",
                      order=9, keywords=["mg", "tablet", "tab", "PO", "處方", "处方", "prescribe"]),
    ],
)


class TemplateRegistry:
    def __init__(self, templates_dir: Path) -> None:
        self.templates_dir = templates_dir
        self._cache: dict[str, ReportTemplate] = {}
        self.errors: list[str] = []
        self.reload()

    def reload(self) -> None:
        self._cache.clear()
        self.errors.clear()
        if self.templates_dir.is_dir():
            for path in sorted(self.templates_dir.glob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    template = ReportTemplate.model_validate(data)
                except Exception as exc:  # noqa: BLE001 - 单文件损坏不应影响整体
                    self.errors.append(f"{path.name}: {exc}")
                    continue
                if template.fields:
                    self._cache[template.id] = template
        if not self._cache:
            self._cache[GENERAL_SOAP.id] = GENERAL_SOAP

    def list(self) -> list[ReportTemplate]:
        return sorted(self._cache.values(), key=lambda t: t.id)

    def get(self, template_id: str) -> ReportTemplate | None:
        return self._cache.get(template_id)

    def resolve(self, template_id: str) -> tuple[ReportTemplate, str | None]:
        """返回 (模板, 回退提示)。模板未注册时回退通用 SOAP 模板（BR-06 / AC-14）。"""
        template = self.get(template_id)
        if template is not None:
            return template, None
        return GENERAL_SOAP, f"模板 {template_id!r} 未注册，已回退到通用 SOAP 模板（事件已记录）"
