"""业务门面：把流水线、存档、校审门禁与导出组合起来，供 HTTP 层调用。

门禁规则（BR-10 / BR-11 / FR-08）集中在这里，避免散落在接口层。
"""

from __future__ import annotations

from . import __version__
from .config import Settings, settings as default_settings
from .exporter import export_report, to_html, to_json, to_text
from .extractor import ExtractionRules
from .llm import LLMRefiner
from .pipeline import run_pipeline
from .schemas import (
    AuditEvent,
    ExportRecord,
    ExportRequest,
    FieldReview,
    FieldReviewRequest,
    GenerateRequest,
    GenerateResponse,
    GeneratedReport,
    LanguageMode,
    SignRequest,
    SignResponse,
    utcnow_iso,
)
from .store import Store
from .templates import TemplateRegistry
from .terminology import Terminology

_CONFIRMED_ACTIONS = {"accept", "edit"}


class ReportNotFound(Exception):
    pass


class ReportLocked(Exception):
    pass


class InvalidOperation(Exception):
    pass


class ReportService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or default_settings
        self.settings.ensure_dirs()
        self.registry = TemplateRegistry(self.settings.templates_dir)
        self.terminology = Terminology.load(self.settings.terms_dir)
        self.rules = ExtractionRules.load(self.settings.terms_dir)
        self.store = Store(self.settings.db_path)
        self.refiner = LLMRefiner(self.settings)

    # ------------------------------------------------------------ 元信息
    def health(self) -> dict:
        return {
            "status": "ok",
            "version": __version__,
            "templates": [t.id for t in self.registry.list()],
            "template_errors": self.registry.errors,
            "terminology_version": self.terminology.version,
            "terminology_entries": len(self.terminology.entries),
            "rules_version": self.rules.version,
            "llm": {
                "enabled": self.settings.llm_enabled,
                "available": self.refiner.available,
                "model": self.settings.llm_model or None,
                "allow_external": self.settings.llm_allow_external,
            },
            "paths": {
                "data": str(self.settings.data_dir),
                "templates": str(self.settings.templates_dir),
                "terms": str(self.settings.terms_dir),
                "exports": str(self.settings.export_dir),
            },
        }

    def reload_resources(self) -> dict:
        self.registry.reload()
        self.terminology = Terminology.load(self.settings.terms_dir)
        self.rules = ExtractionRules.load(self.settings.terms_dir)
        return {
            "templates": [t.id for t in self.registry.list()],
            "template_errors": self.registry.errors,
            "terminology_entries": len(self.terminology.entries),
            "rules_version": self.rules.version,
        }

    # ------------------------------------------------------------ 生成
    def generate(self, request: GenerateRequest) -> GenerateResponse:
        response = run_pipeline(
            request,
            registry=self.registry,
            terminology=self.terminology,
            rules=self.rules,
            settings=self.settings,
            refiner=self.refiner,
        )
        self.store.save_report(response.report)
        for event in response.audit:
            self.store.log(event)
        self.store.log(
            AuditEvent(
                session_id=request.session_id,
                event_type="report_created",
                payload={
                    "report_id": response.report.id,
                    "template_id": response.report.template_id,
                    "mode": response.report.mode,
                    "missing_required": response.report.missing_required,
                },
            )
        )
        return response

    # ------------------------------------------------------------ 查询
    def _require(self, report_id: str) -> GeneratedReport:
        report = self.store.get_report(report_id)
        if report is None:
            raise ReportNotFound(report_id)
        return report

    def get_report(self, report_id: str) -> GeneratedReport:
        return self._require(report_id)

    def list_reports(self, limit: int = 50) -> list[dict]:
        return self.store.list_reports(limit)

    def delete_report(self, report_id: str) -> bool:
        return self.store.delete_report(report_id)

    def audit_log(self, session_id: str = "", limit: int = 200) -> list[dict]:
        return self.store.list_audit(session_id, limit)

    # ------------------------------------------------------------ 校审
    @staticmethod
    def unresolved_required(report: GeneratedReport) -> list[str]:
        """必填字段中尚未确认（采纳/编辑）的清单（BR-10）。"""
        unresolved: list[str] = []
        for field in report.fields:
            if not field.required:
                continue
            review = next((r for r in report.reviews if r.field_key == field.key), None)
            if review is None or review.action not in _CONFIRMED_ACTIONS:
                unresolved.append(field.key)
            elif review.action == "edit" and not review.final_value.strip():
                unresolved.append(field.key)
        return unresolved

    def review_field(
        self, report_id: str, field_key: str, request: FieldReviewRequest
    ) -> GeneratedReport:
        report = self._require(report_id)
        if report.status == "signed":
            raise ReportLocked("报告已签署并锁定，如需修改请另建修订版本（BR-11）")

        field = report.field(field_key)
        if field is None:
            raise InvalidOperation(f"字段 {field_key!r} 不存在于该报告")

        if request.action == "accept":
            final_value = field.ai_value
        elif request.action == "edit":
            final_value = (request.value or "").strip()
            if not final_value:
                raise InvalidOperation("编辑操作必须提供非空值")
        elif request.action == "reject":
            final_value = ""
        elif request.action == "pending":
            final_value = ""
        else:
            raise InvalidOperation(f"未知操作 {request.action!r}")

        review = next((r for r in report.reviews if r.field_key == field_key), None)
        if review is None:
            review = FieldReview(report_id=report.id, field_key=field_key, ai_value=field.ai_value)
            report.reviews.append(review)
        # ai_value 始终保留 AI 原始值，差异可回溯（FR-07 / AC-07）
        review.ai_value = field.ai_value
        review.final_value = final_value
        review.action = request.action
        review.reviewed_at = utcnow_iso()
        review.reviewer = request.reviewer
        field.final_value = final_value

        report.missing_required = self.unresolved_required(report)
        self.store.save_report(report)
        self.store.log(
            AuditEvent(
                session_id=report.session_id,
                event_type="field_review",
                payload={"report_id": report.id, "field": field_key, "action": request.action},
            )
        )
        return report

    # ------------------------------------------------------------ 签署
    def sign(self, report_id: str, request: SignRequest) -> SignResponse:
        report = self._require(report_id)
        if report.status == "signed":
            return SignResponse(
                ok=False, report=report, message="报告已签署；如需修改请另建修订版本（BR-11）"
            )

        blocked = self.unresolved_required(report)
        if blocked:
            report.missing_required = blocked
            self.store.save_report(report)
            return SignResponse(
                ok=False,
                report=report,
                blocked_by=blocked,
                message="以下必填字段尚未确认，签署被阻止：" + "、".join(blocked),
            )

        report.status = "signed"
        report.signed_at = utcnow_iso()
        report.signed_by = request.signed_by
        report.missing_required = []
        self.store.save_report(report)
        self.store.log(
            AuditEvent(
                session_id=report.session_id,
                event_type="report_signed",
                payload={"report_id": report.id, "signed_by": request.signed_by},
            )
        )
        return SignResponse(ok=True, report=report, message="签署完成，报告已锁定（BR-11）")

    # ------------------------------------------------------------ 导出
    def export(self, report_id: str, request: ExportRequest) -> list[ExportRecord]:
        report = self._require(report_id)
        if report.status != "signed":
            raise ReportLocked(
                "报告尚未签署或仍有未确认的必填字段，禁止导出（BR-10）。请先完成校审与签署。"
            )
        records = export_report(
            report, list(request.formats), self.settings.export_dir, request.language_mode
        )
        self.store.log(
            AuditEvent(
                session_id=report.session_id,
                event_type="report_exported",
                payload={"report_id": report.id, "formats": [r.format for r in records]},
            )
        )
        return records

    def preview(self, report_id: str, fmt: str = "text", mode: LanguageMode = "bilingual") -> str:
        report = self._require(report_id)
        if fmt == "json":
            return to_json(report, mode)
        if fmt == "html":
            return to_html(report, mode)
        return to_text(report, mode)
