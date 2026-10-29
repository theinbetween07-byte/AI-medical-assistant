"""HTTP 接口层（FastAPI）。

设计目标：同一份服务既能被独立 exe 自带界面使用，也能被现有网页前端 fetch 调用。
所有响应均为 JSON；界面页面通过 "/" 直接访问。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from . import __version__
from .config import Settings, settings as default_settings
from .schemas import (
    ExportRecord,
    ExportRequest,
    FieldReviewRequest,
    GenerateRequest,
    GenerateResponse,
    GeneratedReport,
    SignRequest,
    SignResponse,
)
from .service import InvalidOperation, ReportLocked, ReportNotFound, ReportService

STATIC_DIR = Path(__file__).resolve().parent / "static"

SAMPLE_SEGMENTS = [
    {
        "id": "S-001", "start_ms": 0, "end_ms": 9000, "lang": "mixed", "confidence": 0.93,
        "text": "陳先生今日返嚟覆診，佢話呢三日有喉嚨痛同咳，無發燒。病人講喉嚨痛，痛咗三日。",
    },
    {
        "id": "S-002", "start_ms": 9000, "end_ms": 17000, "lang": "en", "confidence": 0.91,
        "text": "On examination, blood pressure 128 over 76, pulse 82, temperature 36.8 degree Celsius. Throat is mildly injected, chest clear.",
    },
    {
        "id": "S-003", "start_ms": 17000, "end_ms": 23000, "lang": "en", "confidence": 0.88,
        "text": "No known drug allergy. He is not allergic to penicillin. Past history of hypertension.",
    },
    {
        "id": "S-004", "start_ms": 23000, "end_ms": 29000, "lang": "en", "confidence": 0.85,
        "text": "Impression is upper respiratory tract infection. I will prescribe panadol 500 mg PO TDS for 3 days and panadol as needed.",
    },
    {
        "id": "S-005", "start_ms": 29000, "end_ms": 34000, "lang": "mixed", "confidence": 0.72,
        "text": "建議多休息多飲水，一星期後返嚟覆診，如果發燒再返嚟。",
    },
]

SAMPLE_TERMS = "【SYNTHETIC】合成模拟口述，非真实患者数据（NFR-05）"

# 病理报告示例：字段口径对齐结直肠切除标本病理报告样本（合成数据，非真实病例）
SAMPLE_SEGMENTS_PATHOLOGY = [
    {"id": "P-001", "start_ms": 0, "end_ms": 3000, "lang": "en", "confidence": 0.95,
     "text": "Case No.: SP-2026-004521."},
    {"id": "P-002", "start_ms": 3000, "end_ms": 7000, "lang": "en", "confidence": 0.94,
     "text": "Tumour site: sigmoid colon."},
    {"id": "P-003", "start_ms": 7000, "end_ms": 11000, "lang": "en", "confidence": 0.93,
     "text": "Operative procedure: laparoscopic anterior resection."},
    {"id": "P-004", "start_ms": 11000, "end_ms": 16000, "lang": "en", "confidence": 0.92,
     "text": "Histological tumour type: adenocarcinoma, not otherwise specified."},
    {"id": "P-005", "start_ms": 16000, "end_ms": 21000, "lang": "en", "confidence": 0.92,
     "text": "Histological tumour grade: moderately differentiated."},
    {"id": "P-006", "start_ms": 21000, "end_ms": 26000, "lang": "en", "confidence": 0.9,
     "text": "Tumour dimension: 3.5 x 2.8 x 1.2 cm."},
    {"id": "P-007", "start_ms": 26000, "end_ms": 30000, "lang": "en", "confidence": 0.9,
     "text": "Perforation: not identified."},
    {"id": "P-008", "start_ms": 30000, "end_ms": 36000, "lang": "en", "confidence": 0.89,
     "text": "Relation of tumour to anterior peritoneal reflection: not applicable."},
    {"id": "P-009", "start_ms": 36000, "end_ms": 42000, "lang": "en", "confidence": 0.89,
     "text": "Plane of mesorectal excision: not applicable."},
    {"id": "P-010", "start_ms": 42000, "end_ms": 48000, "lang": "en", "confidence": 0.88,
     "text": "Extent of invasion: tumour invades through muscularis propria into subserosa."},
    {"id": "P-011", "start_ms": 48000, "end_ms": 54000, "lang": "en", "confidence": 0.88,
     "text": "Lymphatic and venous invasion: not identified."},
    {"id": "P-012", "start_ms": 54000, "end_ms": 58000, "lang": "en", "confidence": 0.88,
     "text": "Perineural invasion: present."},
    {"id": "P-013", "start_ms": 58000, "end_ms": 64000, "lang": "en", "confidence": 0.87,
     "text": "Lymph node status: 2 out of 16 lymph nodes show metastatic carcinoma."},
    {"id": "P-014", "start_ms": 64000, "end_ms": 68000, "lang": "en", "confidence": 0.87,
     "text": "Tumour deposits: absent."},
    {"id": "P-015", "start_ms": 68000, "end_ms": 74000, "lang": "en", "confidence": 0.86,
     "text": "Proximal margin: free of tumour, 6.0 cm from tumour."},
    {"id": "P-016", "start_ms": 74000, "end_ms": 80000, "lang": "en", "confidence": 0.86,
     "text": "Distal margin: free of tumour, 4.5 cm from tumour."},
    {"id": "P-017", "start_ms": 80000, "end_ms": 86000, "lang": "en", "confidence": 0.85,
     "text": "Circumferential margin: free of tumour, 1.2 cm."},
    {"id": "P-018", "start_ms": 86000, "end_ms": 89000, "lang": "en", "confidence": 0.9,
     "text": "Macroscopic examination."},
    {"id": "P-019", "start_ms": 89000, "end_ms": 97000, "lang": "en", "confidence": 0.84,
     "text": "The specimen consists of a segment of sigmoid colon measuring 18.0 cm in length and 4.0 cm in diameter."},
    {"id": "P-020", "start_ms": 97000, "end_ms": 105000, "lang": "en", "confidence": 0.83,
     "text": "An ulcerated tumour measuring 3.5 x 2.8 x 1.2 cm is identified 6.0 cm from the proximal resection margin."},
    {"id": "P-021", "start_ms": 105000, "end_ms": 112000, "lang": "en", "confidence": 0.82,
     "text": "The serosal surface is not involved by tumour and multiple lymph nodes are found in the mesocolic fat."},
    {"id": "P-022", "start_ms": 112000, "end_ms": 115000, "lang": "en", "confidence": 0.9,
     "text": "Microscopic examination."},
    {"id": "P-023", "start_ms": 115000, "end_ms": 124000, "lang": "en", "confidence": 0.8,
     "text": "Sections show a moderately differentiated adenocarcinoma with glandular formation, invading through the muscularis propria into the subserosal adipose tissue."},
    {"id": "P-024", "start_ms": 124000, "end_ms": 132000, "lang": "en", "confidence": 0.79,
     "text": "The tumour cells show moderate nuclear pleomorphism and no lymphatic or venous permeation is identified."},
    {"id": "P-025", "start_ms": 132000, "end_ms": 140000, "lang": "en", "confidence": 0.78,
     "text": "Metastatic carcinoma is present in 2 of 16 lymph nodes and the remaining lymph nodes show reactive changes."},
    {"id": "P-026", "start_ms": 140000, "end_ms": 148000, "lang": "en", "confidence": 0.78,
     "text": "Sections of the proximal, distal and circumferential resection margins are free of tumour."},
    {"id": "P-027", "start_ms": 148000, "end_ms": 151000, "lang": "en", "confidence": 0.9,
     "text": "Diagnosis."},
    # 说明：医生不口述诊断原文 —— DIAGNOSIS 段由模板按已抽取值自动拼装（标记"自动生成，请核对"）
]

SAMPLES = {
    "general_soap": (SAMPLE_SEGMENTS, SAMPLE_TERMS),
    "outpatient_record": (SAMPLE_SEGMENTS, SAMPLE_TERMS),
    "pathology_colorectal": (
        SAMPLE_SEGMENTS_PATHOLOGY,
        "【SYNTHETIC】合成模拟病理口述，非真实病例数据（NFR-05）",
    ),
}


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or default_settings
    service = ReportService(cfg)

    app = FastAPI(
        title="MedScribe HK · 报告生成引擎",
        version=__version__,
        description="多语言临床口述 -> SOAP 要素 -> 模板化报告初稿（离线确定性内核 + 可选 LLM 增强）",
    )
    app.state.service = service

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------ 异常映射
    @app.exception_handler(ReportNotFound)
    async def _not_found(_request: Request, exc: ReportNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": f"报告不存在：{exc}"})

    @app.exception_handler(ReportLocked)
    async def _locked(_request: Request, exc: ReportLocked) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(InvalidOperation)
    async def _invalid(_request: Request, exc: InvalidOperation) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    # ------------------------------------------------------------ 界面
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> HTMLResponse:
        page = STATIC_DIR / "index.html"
        if not page.is_file():
            return HTMLResponse("<h1>MedScribe HK 报告生成引擎</h1><p>界面文件缺失，请使用 /docs 调用 API。</p>")
        return HTMLResponse(page.read_text(encoding="utf-8"))

    # ------------------------------------------------------------ 元信息
    @app.get("/health", summary="健康检查与运行配置")
    async def health() -> dict:
        return service.health()

    @app.get("/api/templates", summary="列出可用报告模板")
    async def list_templates() -> list[dict]:
        return [t.model_dump() for t in service.registry.list()]

    @app.post("/api/reload", summary="重新加载模板与术语库（免重启）")
    async def reload_resources() -> dict:
        return service.reload_resources()

    @app.get("/api/sample", summary="获取一段合成模拟口述（[SYNTHETIC]，按模板）")
    async def sample(template_id: str = Query("general_soap")) -> dict:
        segments, note = SAMPLES.get(template_id, SAMPLES["general_soap"])
        return {"note": note, "template_id": template_id, "segments": segments}

    # ------------------------------------------------------------ 生成
    @app.post("/api/generate", response_model=GenerateResponse, summary="转写文本 -> 结构化报告初稿")
    async def generate(request: GenerateRequest) -> GenerateResponse:
        return service.generate(request)

    # ------------------------------------------------------------ 报告
    @app.get("/api/reports", summary="历史报告列表（FR-10）")
    async def list_reports(limit: int = Query(50, ge=1, le=200)) -> list[dict]:
        return service.list_reports(limit)

    @app.get("/api/reports/{report_id}", response_model=GeneratedReport, summary="读取报告")
    async def get_report(report_id: str) -> GeneratedReport:
        return service.get_report(report_id)

    @app.delete("/api/reports/{report_id}", summary="删除报告（BR-12）")
    async def delete_report(report_id: str) -> dict:
        removed = service.delete_report(report_id)
        return {"deleted": removed, "report_id": report_id}

    @app.post(
        "/api/reports/{report_id}/review/{field_key}",
        response_model=GeneratedReport,
        summary="字段级校审：采纳 / 编辑 / 拒绝（FR-07）",
    )
    async def review_field(
        report_id: str, field_key: str, request: FieldReviewRequest
    ) -> GeneratedReport:
        return service.review_field(report_id, field_key, request)

    @app.post("/api/reports/{report_id}/sign", response_model=SignResponse, summary="签署（FR-08 / BR-10）")
    async def sign(report_id: str, request: SignRequest) -> SignResponse:
        return service.sign(report_id, request)

    @app.post(
        "/api/reports/{report_id}/export",
        response_model=list[ExportRecord],
        summary="导出 text / json / pdf / html（FR-09）",
    )
    async def export(report_id: str, request: ExportRequest) -> list[ExportRecord]:
        return service.export(report_id, request)

    @app.get("/api/reports/{report_id}/preview", response_class=PlainTextResponse, summary="导出前预览")
    async def preview(
        report_id: str,
        format: str = Query("text", pattern="^(text|json|html)$"),
        language_mode: str = Query("bilingual", pattern="^(en|zh|bilingual)$"),
    ) -> PlainTextResponse:
        body = service.preview(report_id, format, language_mode)  # type: ignore[arg-type]
        media = {"json": "application/json", "html": "text/html"}.get(format, "text/plain")
        return PlainTextResponse(body, media_type=f"{media}; charset=utf-8")

    # ------------------------------------------------------------ 审计
    @app.get("/api/audit", summary="审计事件（去标识化、签署、导出等）")
    async def audit(session_id: str = "", limit: int = Query(200, ge=1, le=1000)) -> list[dict]:
        return service.audit_log(session_id, limit)

    return app


app = create_app()
