"""
全局配置 - 後端服務的所有可調參數集中在此
"""

import os
import secrets
from pathlib import Path

# ============ 路徑 ============
BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
PROJECT_ROOT = BASE_DIR.parent  # medical-ai-assistant/
MODELS_ROOT = PROJECT_ROOT / "models"
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

MODEL_CACHE_DIR = Path(
    os.environ.get("WHISPER_MODEL_DIR", str(Path.home() / ".cache" / "huggingface"))
)

# ============ ffmpeg ============
# 優先使用 backend/ 下的 ffmpeg.exe（portable，自包含安裝包）
# 找不到時回退到系統安裝的 ffmpeg（winget Gyan.FFmpeg）
_portable_ffmpeg = BASE_DIR / "ffmpeg.exe"
_system_ffmpeg = Path(
    r"C:\Users\thein\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe"
)
FFMPEG_PATH = os.environ.get(
    "FFMPEG_PATH",
    str(_portable_ffmpeg if _portable_ffmpeg.exists() else _system_ffmpeg),
)

# ============ Whisper 模型 ============
# 支持的 faster-whisper 標準型號
SUPPORTED_MODELS = [
    "tiny",           # 75MB,  最快但精度最低
    "base",           # 142MB, 平衡
    "small",          # 466MB, 中等
    "medium",         # 1.5GB, 推薦（默認）
    "large-v3",       # 3.1GB, 最高精度（需 GPU 加速）
    "distil-large-v3" # 1.5GB, large-v3 蒸餾版，速度更快
]

# 自動掃描 models/ 目錄，收集本地已有的模型
LOCAL_MODELS: dict[str, str] = {}
for _m in SUPPORTED_MODELS:
    _candidate = MODELS_ROOT / f"faster-whisper-{_m}"
    if _candidate.exists() and (_candidate / "config.json").exists():
        LOCAL_MODELS[_m] = str(_candidate)

DEFAULT_MODEL = os.environ.get("WHISPER_MODEL", "medium")
DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")

# ============ ASR 默認參數 ============
DEFAULT_LANGUAGE = "yue"  # 默認粵語，香港醫療場景
DEFAULT_BEAM_SIZE = 5
DEFAULT_VAD_FILTER = True

# 醫學術語提示詞（注入 Whisper，提升專有名詞識別率）
# 注意：粵語書面語常用繁體字，這裡用繁體引導
INITIAL_PROMPT = (
    "以下是一段香港醫生的臨床口述記錄，內容涉及問診、查體、診斷、處方、"
    "檢驗檢查、影像所見等。可能混合使用粵語、普通話及英文醫學術語。"
    "常見醫學詞彙：心電圖 ECG、CT scan、MRI、血壓 hypertension、"
    "糖尿病 diabetes、心肌梗塞 myocardial infarction、覆診 follow-up、"
    "處方 prescription、藥物 medication、轉介 referral。"
    "常見專科：心臟科 cardiology、骨科 orthopaedics、內科 medicine、"
    "外科 surgery、兒科 paediatrics、婦產科 O&G、急症科 A&E。"
    "粵語常用字：睇、嘅、喺、咁、冇、咩、畀、攞、醫生、醫院。"
)

# ============ 服務 ============
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))
CORS_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
]

# 上傳限制：50 MB
MAX_UPLOAD_SIZE = 50 * 1024 * 1024

# 支持的音頻格式
ALLOWED_AUDIO_EXT = {".webm", ".wav", ".mp3", ".m4a", ".mp4", ".ogg", ".flac"}


def validate_audio_filename(filename: str) -> bool:
    """檢查上傳文件擴展名是否合法"""
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_AUDIO_EXT


# ============ Auth ============
# JWT 密鑰：首次啟動隨機生成，後續保持不變（存在 .jwt_secret 文件）
_JWT_SECRET_FILE = DATA_DIR / ".jwt_secret"


def _get_or_create_jwt_secret() -> str:
    """獲取或生成 JWT 密鑰（首次隨機生成並持久化）"""
    if _JWT_SECRET_FILE.exists():
        return _JWT_SECRET_FILE.read_text().strip()
    secret = secrets.token_urlsafe(64)
    _JWT_SECRET_FILE.write_text(secret)
    try:
        # Windows 下隱藏文件
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(_JWT_SECRET_FILE), 2)
    except Exception:
        pass
    return secret


JWT_SECRET = os.environ.get("JWT_SECRET") or _get_or_create_jwt_secret()
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(os.environ.get("JWT_EXPIRE_HOURS", "24"))

# SQLite 數據庫
DATABASE_PATH = DATA_DIR / "users.db"
