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
import time
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from loguru import logger

from app import db
from app.auth import get_current_user
from app.config import LOCAL_MODELS, SUPPORTED_MODELS
from app.models import HealthResponse, TranscribeResponse
from app.services.asr import get_asr_service


router = APIRouter(prefix="", tags=["ASR"])


# ============ Health ============
@router.get("/health", response_model=HealthResponse, summary="健康檢查")
async def health():
    """前端啟動時調用，確認後端可達。不觸發模型加載。"""
    asr = get_asr_service()
    return HealthResponse(
        ok=True,
        model=asr.current_model_size or os.environ.get("WHISPER_MODEL", "medium"),
        device=os.environ.get("WHISPER_DEVICE", "cpu"),
        compute_type=os.environ.get("WHISPER_COMPUTE_TYPE", "int8"),
        model_loaded=asr.model is not None,
    )


# ============ Models ============
@router.get("/asr/models", summary="列出可用 Whisper 型號")
def list_models():
    """
    返回所有支持的型號、已下載到本地的型號、當前加載的型號
    """
    asr = get_asr_service()
    return {
        "supported": SUPPORTED_MODELS,
        "local": list(LOCAL_MODELS.keys()),  # 本地有的（無需下載）
        "current": asr.current_model_size,
    }


@router.post("/asr/load-model", summary="切換 Whisper 模型")
def load_model(
    model: str,
    user=Depends(get_current_user),
):
    """
    動態切換 Whisper 模型型號。

    - 本地有的型號：秒級切換
    - 本地沒有的型號：首次會從 HuggingFace 下載（可能耗時幾分鐘到十幾分鐘）
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
            "source": info.get("source", "local" if model in LOCAL_MODELS else "hf-hub"),
        }
    except ValueError as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError as e:
        raise HTTPException(404, f"模型文件未找到：{e}")
    except Exception as e:
        logger.exception(f"[API] 加載模型失敗：{e}")
        raise HTTPException(500, f"加載失敗：{e}")


# ============ Transcribe ============
@router.post("/asr/transcribe", response_model=TranscribeResponse, summary="轉寫音頻")
async def transcribe(
    file: UploadFile = File(..., description="音頻文件（webm / wav / mp3 / m4a）"),
    template: str = Form(default="discharge_summary"),
    language: Optional[str] = Form(default="yue"),
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
            content = await file.read()
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
