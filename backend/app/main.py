"""
AI 醫生助手 - 後端主入口

啟動：
    python -m app.main
    或：
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

import sys
from pathlib import Path

# 確保項目根目錄在 sys.path 中
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from loguru import logger

from app.config import HOST, LOG_DIR, PORT
from app.api.asr_router import router as asr_router

# ============ 日誌配置 ============
logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss.SSS}</green> | <level>{level:<8}</level> | <level>{message}</level>",
    level="INFO",
)
logger.add(
    LOG_DIR / "asr_{time:YYYY-MM-DD}.log",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {message}",
    level="DEBUG",
    rotation="00:00",
    retention="7 days",
    encoding="utf-8",
)

# ============ FastAPI 應用 ============
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import CORS_ORIGINS

app = FastAPI(
    title="AI 醫生助手 · 後端 API",
    description="粵/普/英 多語言語音識別 + 用戶認證（MVP 第一步）",
    version="0.2.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由
app.include_router(asr_router)
# 注意：auth_router 在 startup 時註冊（避免導入順序問題）


@app.on_event("startup")
async def startup_event():
    logger.info("=" * 60)
    logger.info("AI 醫生助手後端啟動 v0.2.0")
    logger.info(f"  Swagger 文檔：http://{HOST}:{PORT}/docs")
    logger.info(f"  日誌目錄：{LOG_DIR}")
    logger.info("=" * 60)

    # 1. 初始化 SQLite 表結構
    try:
        from app import db as _db
        _db.init_db()
        logger.success("✓ SQLite 表結構就緒")
    except Exception as e:
        logger.error(f"✗ 初始化數據庫失敗：{e}")

    # 2. 註冊 auth 路由（延後導入，避免循環依賴）
    try:
        from app.api.auth_router import router as auth_router
        app.include_router(auth_router)
        logger.success("✓ Auth 路由已註冊（/auth/*）")
    except Exception as e:
        logger.error(f"✗ Auth 路由註冊失敗：{e}")

    # 3. 預加載默認 Whisper 模型
    logger.info("預加載 Whisper 模型...")
    import time
    t0 = time.time()
    try:
        from app.services.asr import get_asr_service
        get_asr_service().ensure_loaded()
        elapsed = time.time() - t0
        from app.services.asr import get_asr_service as _get
        logger.success(f"✓ 模型預加載完成：{_get().current_model_size}（{elapsed:.1f}s）")
    except Exception as e:
        logger.error(f"✗ 模型預加載失敗：{e}")
        logger.warning("服務仍會啟動，首次 /asr/transcribe 會自動重試")


@app.get("/")
async def root():
    return {
        "service": "AI 醫生助手 · 後端",
        "version": "0.2.0",
        "docs": "/docs",
        "endpoints": [
            "GET  /health",
            "POST /auth/register",
            "POST /auth/login",
            "GET  /auth/me",
            "GET  /asr/models",
            "POST /asr/load-model",
            "POST /asr/transcribe",
            "POST /asr/warmup",
        ],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=HOST,
        port=PORT,
        reload=False,
    )
