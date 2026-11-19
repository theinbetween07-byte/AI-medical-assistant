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
# 發行版把 ffmpeg.exe 放在 tools/ 下；開發環境依次回退到 backend/ 與系統 PATH。
_ffmpeg_candidates = [
    BASE_DIR / "ffmpeg.exe",
    PROJECT_ROOT / "tools" / "ffmpeg.exe",
    PROJECT_ROOT / "ffmpeg.exe",
]
_portable_ffmpeg = next((p for p in _ffmpeg_candidates if p.exists()), None)
FFMPEG_PATH = os.environ.get(
    "FFMPEG_PATH",
    str(_portable_ffmpeg) if _portable_ffmpeg else "ffmpeg",
)

# ============ Whisper 模型 ============
# 發行版只內置 small。所有模型必須隨包分發，禁止在運行時聯網下載。
# 擴展模型（CapsWriter）由 services/extensions.py 提供，目錄約定見 models/ext/extensions.json。
SUPPORTED_MODELS = ["small"]

# 自動掃描 models/ 目錄，收集本地已有的模型
LOCAL_MODELS: dict[str, str] = {}
for _m in SUPPORTED_MODELS:
    _candidate = MODELS_ROOT / f"faster-whisper-{_m}"
    if _candidate.exists() and (_candidate / "config.json").exists():
        LOCAL_MODELS[_m] = str(_candidate)

DEFAULT_MODEL = os.environ.get("WHISPER_MODEL", "small")
# auto：有可用的 CUDA 就走 GPU（float16），否则 CPU（int8）。
# 发行版 runtime 自带 nvidia-cublas-cu12 / nvidia-cudnn-cu12，DLL 由 asr.py 注册。
DEVICE = os.environ.get("WHISPER_DEVICE", "auto")
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "auto")
# CPU 推理线程数；0 = 自动（上限 8，全核反而更慢且易触发内存分配失败）
ASR_CPU_THREADS = int(os.environ.get("WHISPER_CPU_THREADS", "0"))
# 分块解码长度（秒）。块内开启上下文续写以恢复标点/专名，块间互不影响，
# 避免长音频开启上下文后续写导致的循环重复与幻觉。
ASR_CHUNK_SECONDS = int(os.environ.get("WHISPER_CHUNK_SECONDS", "30"))

# ============ 擴展模塊 ============
EXTENSIONS_ROOT = Path(
    os.environ.get("MEDSCRIBE_EXT_DIR", str(MODELS_ROOT / "ext"))
)
EXTENSIONS_MANIFEST = EXTENSIONS_ROOT / "extensions.json"

# ============ ASR 默認參數 ============
DEFAULT_LANGUAGE = "auto"  # 默認粵語，香港醫療場景
DEFAULT_BEAM_SIZE = 5
DEFAULT_VAD_FILTER = True

# 醫學術語提示詞（注入 Whisper，提升專有名詞識別率）
# 普通話 / 內地門診：用簡體引導，避免輸出被「繁化」
INITIAL_PROMPT_ZH = (
    "以下是一段医生的临床口述记录，内容涉及主诉、现病史、既往史、过敏史、"
    "个人史、体格检查、辅助检查、诊断、处理计划、处方与随访，可能混用英文医学缩写。"
    "常见词汇：主诉、现病史、既往史、体格检查、血压、心率、血常规、尿常规、"
    "心电图 ECG、CT、MRI、慢性胃炎、高血压、糖尿病、冠心病、"
    "奥美拉唑、阿莫西林、头孢、低脂饮食、规律作息、复诊、随访。"
)
# 粵語（香港醫療場景）：繁體引導，並給出粵語常用字
INITIAL_PROMPT_YUE = (
    "以下是一段香港醫生的臨床口述記錄，內容涉及問診、查體、診斷、處方、"
    "檢驗檢查、影像所見等。可能混合使用粵語、普通話及英文醫學術語。"
    "常見醫學詞彙：心電圖 ECG、CT scan、MRI、血壓 hypertension、"
    "糖尿病 diabetes、心肌梗塞 myocardial infarction、覆診 follow-up、"
    "處方 prescription、藥物 medication、轉介 referral。"
    "常見專科：心臟科 cardiology、骨科 orthopaedics、內科 medicine、"
    "外科 surgery、兒科 paediatrics、婦產科 O&G、急症科 A&E。"
    "粵語常用字：睇、嘅、喺、咁、冇、咩、畀、攞、醫生、醫院。"
)
# 兼容舊引用；自動識別時按普通話處理
INITIAL_PROMPT = INITIAL_PROMPT_ZH

# ============ 服務 ============
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "18000"))
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
