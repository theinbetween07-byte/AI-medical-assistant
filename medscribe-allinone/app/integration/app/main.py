"""HTTP 接口层 —— 对齐《技术设计》4.2 的最小接口集合。

    POST   /sessions                     新建会话
    POST   /sessions/{id}/audio          上传录音，返回时长与校验结果
    POST   /sessions/{id}/transcribe     执行 ASR，返回分段转写
    PATCH  /segments/{id}                人工修正转写片段
    POST   /sessions/{id}/correct-terms  运行术语纠正，返回替换记录
    POST   /sessions/{id}/correct-terms/undo   一键撤销（FR-03）
    POST   /sessions/{id}/extract        抽取 S/O/A/P 要素
    GET    /templates                    列出可用模板
    POST   /sessions/{id}/generate       指定模板生成初稿
    PATCH  /reports/{id}/fields/{key}    字段校审（采纳/编辑/拒绝）
    POST   /reports/{id}/sign            签署（含必填校验）
    POST   /reports/{id}/export          导出 text / pdf / json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, File, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .assets import build_version
from .config import settings
from .schemas import (
    CorrectTermsOptions,
    CreateSessionRequest,
    ExportRequest,
    ExtractOptions,
    GenerateOptions,
    ModelSwitchRequest,
    ReviewRequest,
    SegmentPatch,
    SignRequest,
    TranscribeOptions,
)
from .service import IntegrationService, InvalidState, NotFound, UpstreamError

service = IntegrationService(settings)

ERROR_CODES = {
    NotFound: 404,
    InvalidState: 400,
    UpstreamError: 502,
}


class _RevalidatingStaticFiles(StaticFiles):
    """静态资源允许浏览器缓存，但每次必须回源校验（ETag 命中时 304，不重传正文）。

    没有 Cache-Control 时浏览器会按 Last-Modified 做启发式缓存，
    升级后仍使用旧的 app.js，与新 index.html 混搭导致界面元素缺失。
    """

    async def get_response(self, path: str, scope):  # type: ignore[override]
        response = await super().get_response(path, scope)
        if response.status_code < 400:
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


def create_app() -> FastAPI:
    app = FastAPI(
        title="MedScribe HK · 端到端集成层",
        version="0.1.0",
        description="录音 → 多语言 ASR → 术语纠正 → S/O/A/P 抽取 → 模板化初稿 → 校审签署 → 导出",
    )
    app.state.service = service
    # 静态资源版本：随文件内容变化，避免浏览器沿用旧脚本（见 assets.py 注释）。
    asset_version = build_version(settings.static_dir)
    app.state.asset_version = asset_version

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _no_store_api(request: Request, call_next):
        # API 响应不缓存：Firefox 对没有缓存头的 GET 会启发式缓存，刷新会拿到旧列表
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    for exc_type, code in ERROR_CODES.items():
        def _handler(_request: Request, exc: Exception, _code: int = code) -> JSONResponse:
            return JSONResponse(status_code=_code, content={"detail": str(exc)})

        app.add_exception_handler(exc_type, _handler)  # type: ignore[arg-type]

    # ---------------------------------------------------------------- 界面
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> HTMLResponse:
        page = settings.static_dir / "index.html"
        if not page.is_file():
            return HTMLResponse("<h1>静态资源缺失</h1><p>请重新解压基础包，或访问 /docs。</p>")
        html = page.read_text(encoding="utf-8").replace("__ASSET_VERSION__", asset_version)
        return HTMLResponse(
            html,
            headers={
                "Cache-Control": "no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    if settings.static_dir.is_dir():
        app.mount("/static", _RevalidatingStaticFiles(directory=str(settings.static_dir)), name="static")

    # ---------------------------------------------------------------- 健康
    @app.get("/health/live", summary="集成层存活探针（不聚合上游）", include_in_schema=False)
    def health_live() -> dict:
        """启动与身份校验使用：上游全挂时 /health 会变慢，这里必须立即返回。

        `build` 是静态资源内容哈希：启动脚本据此判断已在运行的进程是不是当前这版代码，
        覆盖安装后旧进程仍在监听时可以自动重启，而不是直接跳过、让用户看到旧界面。
        """
        return {
            "ok": True,
            "service": "integration",
            "build": asset_version,
            "root": str(Path(__file__).resolve().parents[1]),
        }

    @app.get("/health", summary="三段链路健康检查")
    def health() -> dict:
        return service.health()

    # ------------------------------------------------------------ ASR 模型
    @app.get("/api/asr/models", summary="列出 ASR 可用型号与当前型号（FR-02）")
    def asr_models() -> dict:
        return service.asr_models()

    @app.post("/api/asr/model", summary="切换 ASR 型号（FR-02）")
    def asr_model(opts: ModelSwitchRequest = Body(...)) -> dict:
        return service.select_asr_model(opts.model)

    @app.post("/api/reload-terms", summary="重新加载术语库并通知引擎")
    def reload_terms() -> dict:
        return service.reload_terms()

    @app.get("/api/glossary", summary="术语库查阅（全集，含未参与自动替换的条目）")
    def glossary(
        q: str = Query("", description="按中文/英文/粤语/别名模糊搜索"),
        limit: int = Query(200, ge=1, le=1000),
    ) -> dict:
        return service.glossary(q, limit)

    @app.get("/api/audit", summary="审计事件")
    def audit(
        session_id: str = Query(""), limit: int = Query(200, ge=1, le=1000)
    ) -> list[dict]:
        return service.audit(session_id, limit)

    # ------------------------------------------------------------ S1 会话
    @app.post("/sessions", summary="新建会话")
    def create_session(req: CreateSessionRequest = Body(default=CreateSessionRequest())) -> dict:
        return service.create_session(req.template_id or "", req.title).model_dump()

    @app.get("/sessions", summary="历史会话列表（本地存档，FR-10；支持关键词/状态/时间筛选）")
    def list_sessions(
        limit: int = Query(50, ge=1, le=200),
        q: str = Query("", max_length=80, description="关键词：标题/会话号/报告号/报告全文"),
        status: str = Query("", pattern="^(signed|draft|none)?$", description="报告状态"),
        days: int = Query(0, ge=0, le=3650, description="只看最近 N 天，0 为不限"),
    ) -> list[dict]:
        return [
            s.model_dump() for s in service.list_sessions(limit, q=q, status=status, days=days)
        ]

    @app.get("/sessions/{session_id}", summary="读取会话全量状态（转写/要素/差异/导出）")
    def get_session(session_id: str) -> dict:
        return service.get_detail(session_id).model_dump()

    @app.delete("/sessions/{session_id}", summary="删除会话（级联删除音频与报告，BR-12）")
    def delete_session(session_id: str) -> dict:
        return service.delete_session(session_id)

    # ------------------------------------------------------------ S2 录音
    @app.post("/sessions/{session_id}/audio", summary="上传录音并校验时长/静音（FR-01/BR-02）")
    def upload_audio(session_id: str, file: UploadFile = File(...)) -> dict:
        content = file.file.read()
        result = service.save_audio(session_id, file.filename or "audio.webm", content)
        payload = result.model_dump()
        if not result.ok:
            return JSONResponse(status_code=400, content=payload)
        return payload

    @app.get("/sessions/{session_id}/audio", summary="回放会话音频（FR-07 音频跳转）")
    def get_audio(session_id: str) -> FileResponse:
        detail = service.get_detail(session_id)
        path = settings.audio_dir / detail.session.audio_path
        if not detail.session.audio_path or not path.is_file():
            raise NotFound("该会话没有可回放的音频")
        return FileResponse(str(path), media_type="audio/wav", filename=path.name)

    # ------------------------------------------------------------ S3 转写
    @app.post("/sessions/{session_id}/transcribe", summary="执行 ASR 转写（FR-02）")
    def transcribe(
        session_id: str, opts: TranscribeOptions = Body(default=TranscribeOptions())
    ) -> dict:
        return service.transcribe(
            session_id,
            language=opts.language,
            beam_size=opts.beam_size,
            asr_template=opts.asr_template,
        )

    @app.post("/sessions/{session_id}/transcribe-jobs",
              summary="建立转写任务（带实时进度）")
    def start_transcribe_job(
        session_id: str, opts: TranscribeOptions = Body(default=TranscribeOptions())
    ) -> dict:
        return service.start_transcribe_job(
            session_id,
            language=opts.language,
            beam_size=opts.beam_size,
            asr_template=opts.asr_template,
        )

    @app.get("/transcribe-jobs/{job_id}", summary="查询转写任务进度")
    def transcribe_job(job_id: str) -> dict:
        return service.transcribe_job(job_id)

    @app.patch("/sessions/{session_id}/segments/{segment_id}",
               summary="人工修正/确认转写片段（FR-02/BR-09）")
    def patch_segment(session_id: str, segment_id: str, patch: SegmentPatch) -> dict:
        return service.patch_segment(session_id, segment_id, patch.text, patch.confirmed)

    @app.patch("/segments/{segment_id}", include_in_schema=False)
    def patch_segment_legacy(segment_id: str, _patch: SegmentPatch) -> dict:
        raise NotFound(
            "接口已变更：请使用 PATCH /sessions/{session_id}/segments/{segment_id}"
        )

    @app.post("/sessions/{session_id}/correct-terms", summary="术语纠正（FR-03/BR-04）")
    def correct_terms(
        session_id: str, _opts: CorrectTermsOptions = Body(default=CorrectTermsOptions())
    ) -> dict:
        return service.correct_terms(session_id)

    @app.post("/sessions/{session_id}/correct-terms/undo", summary="撤销术语纠正（FR-03）")
    def undo_corrections(session_id: str) -> dict:
        return service.undo_corrections(session_id)

    # ------------------------------------------------------------ S4 生成
    @app.post("/sessions/{session_id}/extract", summary="抽取 S/O/A/P 要素（FR-04）")
    def extract(
        session_id: str, opts: ExtractOptions = Body(default=ExtractOptions())
    ) -> dict:
        return service.extract(session_id, deidentify=opts.deidentify)

    @app.get("/templates", summary="列出可用报告模板")
    def templates() -> list[dict]:
        return service.templates()

    @app.post("/sessions/{session_id}/generate", summary="生成结构化初稿（FR-05/FR-06）")
    def generate(
        session_id: str, opts: GenerateOptions = Body(default=GenerateOptions())
    ) -> dict:
        return service.generate(
            session_id,
            template_id=opts.template_id or "",
            deidentify=opts.deidentify,
            language_mode=opts.language_mode,
            keep_unclassified=opts.keep_unclassified,
        )

    @app.get("/sessions/{session_id}/report", summary="读取当前报告快照")
    def get_session_report(session_id: str) -> dict:
        detail = service.get_detail(session_id)
        if not detail.report:
            raise NotFound("该会话尚未生成报告")
        return detail.report

    # ------------------------------------------------------------ S5 校审
    @app.patch("/reports/{report_id}/fields/{field_key}", summary="字段校审（FR-07）")
    def review_field(
        report_id: str, field_key: str, req: ReviewRequest
    ) -> dict:
        return service.review_field(report_id, field_key, req.action, req.value, req.reviewer)

    @app.post("/reports/{report_id}/fields/{field_key}", include_in_schema=False)
    def review_field_post(
        report_id: str, field_key: str, req: ReviewRequest
    ) -> dict:
        return service.review_field(report_id, field_key, req.action, req.value, req.reviewer)

    @app.post("/reports/{report_id}/sign", summary="签署（FR-08/BR-10）")
    def sign(report_id: str, req: SignRequest = Body(default=SignRequest())) -> dict:
        return service.sign(report_id, req.signed_by)

    # --------------------------------------------------------------- E 导出
    @app.post("/reports/{report_id}/export", summary="导出 text / json / pdf（FR-09）")
    def export(report_id: str, req: ExportRequest = Body(default=ExportRequest())) -> dict:
        return service.export(report_id, list(req.formats), req.language_mode)

    @app.get("/reports/{report_id}/preview", response_class=PlainTextResponse,
             summary="导出前预览")
    def preview(
        report_id: str,
        format: str = Query("text", pattern="^(text|json|html)$"),
        language_mode: str = Query("bilingual", pattern="^(en|zh|bilingual)$"),
    ) -> PlainTextResponse:
        body = service.preview(report_id, format, language_mode)
        media = {"json": "application/json", "html": "text/html"}.get(format, "text/plain")
        return PlainTextResponse(body, media_type=f"{media}; charset=utf-8")

    @app.get("/exports/{filename}", summary="下载导出文件")
    def download_export(filename: str) -> FileResponse:
        safe = Path(filename).name  # 防目录穿越
        path = settings.export_dir / safe
        if not path.is_file():
            raise NotFound(f"导出文件不存在：{safe}")
        media = {
            ".pdf": "application/pdf",
            ".json": "application/json",
            ".txt": "text/plain; charset=utf-8",
            ".html": "text/html; charset=utf-8",
        }.get(path.suffix.lower(), "application/octet-stream")
        return FileResponse(str(path), media_type=media, filename=safe)

    # ------------------------------------------------------------- 调试辅助
    @app.get("/api/sessions/{session_id}/debug", include_in_schema=False)
    def debug_session(session_id: str) -> dict:
        detail = service.get_detail(session_id)
        return json.loads(detail.model_dump_json())

    return app


app = create_app()
