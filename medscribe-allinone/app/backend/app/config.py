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
ASR_CHUNK_SECONDS = int(os.environ.get("WHISPER_CHUNK_SECONDS", "60"))

# ============ 擴展模塊 ============
EXTENSIONS_ROOT = Path(
    os.environ.get("MEDSCRIBE_EXT_DIR", str(MODELS_ROOT / "ext"))
)
EXTENSIONS_MANIFEST = EXTENSIONS_ROOT / "extensions.json"

# ============ ASR 默認參數 ============
# 香港醫療場景默認粵語：Whisper 的語言檢測會把粵語判成 zh，按普通話解碼錯字率
# 高達 40%+（hk-clinical-synthetic 實測），因此默認直接指定 yue。
# 普通話/英語錄音可在界面切換，或用 MSHK_ASR_LANGUAGE 覆蓋。
DEFAULT_LANGUAGE = os.environ.get("MSHK_ASR_LANGUAGE", "yue")
DEFAULT_BEAM_SIZE = 5
# auto 请求在本部署中按此语言解码；Whisper 的 zh 检测会把粤语带偏
AUTO_LANGUAGE = os.environ.get("MSHK_ASR_AUTO_LANGUAGE", "yue")
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
# 粵語（香港醫療場景）：用一段同風格的粵英混雜口述做引導。
# 相比術語清單，示例句能同時對齊「繁體字 + 中文數字 + 中英混排 + 口述節奏」，
# hk-clinical-synthetic 上字錯率 0.058 → 0.047（術語清單反而稀釋了風格引導）。
# 可用 MSHK_INITIAL_PROMPT_YUE / MSHK_PROMPT_YUE_FILE 覆蓋。
INITIAL_PROMPT_YUE = os.environ.get("MSHK_INITIAL_PROMPT_YUE") or (
    "以下係香港醫生嘅臨床口述。呢個病人因為發燒入院，今日冇胸痛，冇氣促。"
    "血壓一百二十八 over 七十四，心跳每分鐘七十八下，SpO2 喺 room air 係九十七 percent。"
    "Chest X-ray 見右下肺實變，blood culture 仲 pending。"
    "計劃繼續 intravenous antibiotics，聽日 repeat full blood count 同 renal function。"
    " 常見詞：醫囑、拇指、韌帶、併發症、手術、切除、麻醉、上臂。"
)

# 運維可把提示詞放在 UTF-8 文件裏，避免命令行環境變量編碼問題
_prompt_file = os.environ.get("MSHK_PROMPT_YUE_FILE")
if _prompt_file:
    try:
        INITIAL_PROMPT_YUE = Path(_prompt_file).read_text(encoding="utf-8").strip() or INITIAL_PROMPT_YUE
    except OSError:
        pass

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
