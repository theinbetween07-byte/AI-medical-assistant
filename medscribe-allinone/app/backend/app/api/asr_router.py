"""
ASR API 路由

- GET  /health            健康檢查
- GET  /asr/models        列出可用 Whisper 型號
- POST /asr/load-model    動態切換模型（需登錄）
- POST /asr/transcribe    上傳音頻並轉寫（需登錄）
- POST /asr/warmup        預熱當前模型（需登錄）
"""

import os
import tempfile
import threading
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from loguru import logger

from app import db
from app.auth import get_current_user
from app.config import LOCAL_MODELS, SUPPORTED_MODELS
from app.models import HealthResponse, TranscribeResponse
from app.services import extensions
from app.services.asr import get_asr_service


router = APIRouter(prefix="", tags=["ASR"])


# ============ Health ============
@router.get("/health", response_model=HealthResponse, summary="健康檢查")
async def health():
    """前端啟動時調用，確認後端可達。不觸發模型加載。"""
    asr = get_asr_service()
    return HealthResponse(
        ok=True,
        model=asr.current_model_size or os.environ.get("WHISPER_MODEL", "small"),
        device=os.environ.get("WHISPER_DEVICE", "cpu"),
        compute_type=os.environ.get("WHISPER_COMPUTE_TYPE", "int8"),
        model_loaded=asr.model is not None,
    )


# ============ Models ============
@router.get("/asr/models", summary="列出可用模型（內置 + 擴展）")
def list_models():
    """
    返回內置 Whisper 型號與可選擴展模塊。

    - ``installed=false`` 的擴展項附帶 ``install``：下載地址、分卷大小、安裝步驟
    - ``local``：無需額外安裝即可使用
    """
    asr = get_asr_service()
    models = extensions.model_entries()
    return {
        "supported": models,
        "local": [m["id"] for m in models if m.get("local")],
        "current": asr.current_model_size or "",
        "engine": asr.engine,
    }


@router.post("/asr/load-model", summary="切換 Whisper 模型")
def load_model(
    model: str,
    user=Depends(get_current_user),
):
    """
    切換模型型號。

    - 內置 small：隨包分發，秒級加載
    - 擴展模塊：需先安裝；未安裝返回 409 + 安裝步驟
    - 本發行版**不聯網下載**，缺失模型直接報錯
    """
    asr = get_asr_service()
    logger.info(f"[API] 用戶 {user['username']} 請求切換模型: {model}")
    try:
        info = asr.set_model(model)
        return {
            "ok": True,
            "model": info["model"],
            "previous": info.get("previous"),
            "elapsed": info["elapsed"],
            "changed": info["changed"],
            "source": info.get("source", "local"),
        }
    except ValueError as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        logger.exception(f"[API] 加載模型失敗：{e}")
        raise HTTPException(500, f"加載失敗：{e}")


# ============ Transcribe ============
@router.post("/asr/transcribe", response_model=TranscribeResponse, summary="轉寫音頻")
def transcribe(
    file: UploadFile = File(..., description="音頻文件（webm / wav / mp3 / m4a）"),
    template: str = Form(default="discharge_summary"),
    language: Optional[str] = Form(default="auto"),
    beam_size: int = Form(default=5),
    user=Depends(get_current_user),
):
    """
    上傳音頻，返回轉寫結果。

    需要登錄（JWT Bearer Token）。
    """
    if language in (None, "", "auto", "mix"):
        lang = None
    else:
        lang = language

    asr = get_asr_service()
    if asr.model is None:
        # 首次調用時會自動加載默認模型，可能耗時
        logger.info("[API] 首次調用，正在加載默認模型，請耐心等待...")

    suffix = os.path.splitext(file.filename or "")[1] or ".webm"
    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = file.file.read()
            if len(content) == 0:
                raise HTTPException(400, "上傳文件為空")
            if len(content) > 50 * 1024 * 1024:
                raise HTTPException(413, "文件超過 50MB 限制")
            tmp.write(content)
            tmp_path = tmp.name

        logger.info(
            f"[API] {user['username']} 上傳音頻：{file.filename} "
            f"({len(content)/1024:.1f}KB), template={template}, language={lang or 'auto'}"
        )

        t0 = time.time()
        with _DECODE_LOCK:
            result = asr.transcribe(
                audio_path=tmp_path,
                language=lang,
                template=template,
                beam_size=beam_size,
            )

        # 寫審計日誌
        try:
            db.log_asr(
                user_id=user["id"],
                username=user["username"],
                template=template,
                language=lang or "auto",
                model_size=asr.current_model_size or "unknown",
                audio_duration=result["duration"],
                elapsed=result["elapsed"],
                text_length=len(result["text"]),
            )
        except Exception as e:
            logger.warning(f"寫 ASR 日誌失敗（不影響返回）：{e}")

        logger.success(
            f"[API] 轉寫成功：{result['duration']}s 音頻 → {result['elapsed']}s，"
            f"模型={result['model']}，語言={result['language']}，"
            f"用戶={user['username']}"
        )
        return TranscribeResponse(**result)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[API] 轉寫失敗：{e}")
        raise HTTPException(500, f"轉寫失敗：{str(e)}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception as e:
                logger.warning(f"清理臨時文件失敗：{e}")


# ============ Transcribe jobs（帶真實進度的異步轉寫）============
# 前端輪詢 GET /asr/transcribe-jobs/{id} 取進度，避免長轉寫時界面無反饋。
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_JOB_TTL_S = 1800
# faster-whisper 的模型实例不是线程安全的：并发解码会串扰上下文，
# 实测会退化成重复的占位符号。这里强制串行，同一时刻只跑一个转写。
_DECODE_LOCK = threading.Lock()


def _job_update(job_id: str, **fields) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        job.update(fields)
        job["updated"] = time.time()


def _job_sweep() -> None:
    now = time.time()
    with _JOBS_LOCK:
        stale = [
            jid
            for jid, job in _JOBS.items()
            if job.get("status") != "running" and now - job.get("updated", now) > _JOB_TTL_S
        ]
        for jid in stale:
            _JOBS.pop(jid, None)


def _run_transcribe_job(
    job_id: str,
    tmp_path: str,
    language: Optional[str],
    template: str,
    beam_size: int,
    user: dict,
) -> None:
    asr = get_asr_service()

    def on_progress(fraction: float, phase: str, detail: dict) -> None:
        _job_update(
            job_id,
            progress=round(float(fraction), 4),
            phase=phase,
            processed=(detail or {}).get("processed"),
            total=(detail or {}).get("total"),
        )

    try:
        _job_update(job_id, status="running", phase="prepare", progress=0.02)
        with _DECODE_LOCK:
            result = asr.transcribe(
                audio_path=tmp_path,
                language=language,
                template=template,
                beam_size=beam_size,
                progress=on_progress,
            )
        try:
            db.log_asr(
                user_id=user["id"],
                username=user["username"],
                template=template,
                language=language or "auto",
                model_size=asr.current_model_size or "unknown",
                audio_duration=result["duration"],
                elapsed=result["elapsed"],
                text_length=len(result["text"]),
            )
        except Exception as e:
            logger.warning(f"寫 ASR 日誌失敗（不影響返回）：{e}")
        logger.success(
            f"[API] 轉寫成功（job {job_id}）：{result['duration']}s 音頻 → "
            f"{result['elapsed']}s，模型={result['model']}"
        )
        _job_update(job_id, status="done", phase="done", progress=1.0, result=result)
    except Exception as e:  # noqa: BLE001 - 需要把失敗原因帶回前端
        logger.exception(f"[API] 轉寫任務失敗（job {job_id}）：{e}")
        _job_update(job_id, status="error", phase="error", error=str(e))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception as e:
                logger.warning(f"清理臨時文件失敗：{e}")


@router.post("/asr/transcribe-jobs", summary="建立轉寫任務（帶進度）")
async def create_transcribe_job(
    file: UploadFile = File(..., description="音頻文件（webm / wav / mp3 / m4a）"),
    template: str = Form(default="discharge_summary"),
    language: Optional[str] = Form(default="auto"),
    beam_size: int = Form(default=5),
    user=Depends(get_current_user),
):
    """上傳音頻後立即返回 job_id，轉寫在後台線程執行。"""
    lang = None if language in (None, "", "auto", "mix") else language
    suffix = os.path.splitext(file.filename or "")[1] or ".webm"
    content = await file.read()
    if not content:
        raise HTTPException(400, "上傳文件為空")
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(413, "文件超過 50MB 限制")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp_path = tmp.name
    try:
        tmp.write(content)
    finally:
        tmp.close()

    _job_sweep()
    job_id = uuid.uuid4().hex
    with _JOBS_LOCK:
        _JOBS[job_id] = {
            "id": job_id,
            "status": "running",
            "phase": "prepare",
            "progress": 0.0,
            "processed": 0.0,
            "total": 0.0,
            "updated": time.time(),
            "result": None,
            "error": None,
        }
    logger.info(
        f"[API] {user['username']} 建立轉寫任務 {job_id}：{file.filename} "
        f"({len(content)/1024:.1f}KB), template={template}, language={lang or 'auto'}"
    )
    threading.Thread(
        target=_run_transcribe_job,
        args=(job_id, tmp_path, lang, template, beam_size, dict(user)),
        daemon=True,
        name=f"asr-job-{job_id[:8]}",
    ).start()
    return {"job_id": job_id, "status": "running"}


@router.get("/asr/transcribe-jobs/{job_id}", summary="查詢轉寫任務進度")
def get_transcribe_job(job_id: str, user=Depends(get_current_user)):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        snapshot = None if job is None else {
            "id": job["id"],
            "status": job["status"],
            "phase": job["phase"],
            "progress": job["progress"],
            "processed": job.get("processed"),
            "total": job.get("total"),
            "error": job.get("error"),
            "result": job.get("result") if job["status"] == "done" else None,
        }
    if snapshot is None:
        raise HTTPException(404, "轉寫任務不存在或已過期")
    return snapshot


# ============ Warmup ============
@router.post("/asr/warmup", summary="預熱當前模型")
def warmup(user=Depends(get_current_user)):
    """主動加載當前模型，避免首次轉寫時長時間等待"""
    try:
        asr = get_asr_service()
        if asr.model is not None:
            return {"ok": True, "model": asr.current_model_size, "already_loaded": True}
        t0 = time.time()
        asr.ensure_loaded()
        elapsed = time.time() - t0
        logger.info(f"[API] {user['username']} 預熱模型：{asr.current_model_size} ({elapsed:.1f}s)")
        return {"ok": True, "model": asr.current_model_size, "elapsed": round(elapsed, 2)}
    except Exception as e:
        logger.exception(f"[API] 預熱失敗：{e}")
        raise HTTPException(500, str(e))
