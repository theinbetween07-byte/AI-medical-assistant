"""集成层运行配置。

三个组件的位置全部可用环境变量覆盖，便于换机器/换端口部署：

    MSHK_INTEGRATION_HOST / MSHK_INTEGRATION_PORT   集成层监听地址（默认 127.0.0.1:8100）
    MSHK_ASR_URL                                    ASR 服务（medical-ai-assistant 后端，默认 :18000）
    MSHK_ENGINE_URL                                 报告引擎（默认 :8790）
    MSHK_ASR_USER / MSHK_ASR_PASSWORD               ASR 服务需要的账号（缺省自动注册）
    MSHK_FFMPEG                                     ffmpeg 可执行文件路径
    MSHK_DATA_DIR                                   音频/导出/SQLite 存放目录
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FFMPEG = ROOT.parent / "medical-ai-assistant" / "backend" / "ffmpeg.exe"


def _env(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_ffmpeg() -> str:
    """按 环境变量 -> 随 ASR 项目附带的 ffmpeg -> PATH 的顺序查找。"""
    candidate = _env("MSHK_FFMPEG")
    if candidate:
        return candidate
    if DEFAULT_FFMPEG.is_file():
        return str(DEFAULT_FFMPEG)
    found = shutil.which("ffmpeg")
    if found:
        return found
    return "ffmpeg"


def _resolve_ffprobe(ffmpeg: str) -> str:
    """ffprobe 通常与 ffmpeg 同目录；也允许单独用 MSHK_FFPROBE 指定。"""
    candidate = _env("MSHK_FFPROBE")
    if candidate:
        return candidate
    sibling = Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    if sibling.is_file():
        return str(sibling)
    found = shutil.which("ffprobe")
    if found:
        return found
    return "ffprobe"


class Settings:
    def __init__(self) -> None:
        # ---- 集成层自身
        self.host: str = _env("MSHK_INTEGRATION_HOST", "127.0.0.1")
        self.port: int = _env_int("MSHK_INTEGRATION_PORT", 8100)

        # ---- 上游服务
        self.asr_url: str = _env("MSHK_ASR_URL", "http://127.0.0.1:18000").rstrip("/")
        self.engine_url: str = _env("MSHK_ENGINE_URL", "http://127.0.0.1:8790").rstrip("/")

        # ASR 需要登录；首次启动会自动注册这个演示账号（PRD 明确不做账号体系，此处仅过上游鉴权）
        self.asr_username: str = _env("MSHK_ASR_USER", "demo_doctor")
        self.asr_password: str = _env("MSHK_ASR_PASSWORD", "MedScribe2026")

        # ---- 存储
        self.data_dir: Path = Path(
            _env("MSHK_DATA_DIR", str(ROOT / "data"))
        ).expanduser()
        self.terms_dir: Path = Path(_env("MSHK_TERMS_DIR", str(ROOT / "terms"))).expanduser()
        self.static_dir: Path = Path(__file__).resolve().parent / "static"

        # ---- 外部依赖
        self.ffmpeg: str = _resolve_ffmpeg()
        self.ffprobe: str = _resolve_ffprobe(self.ffmpeg)
        self.ffmpeg_timeout_s: int = _env_int("MSHK_FFMPEG_TIMEOUT_S", 180)

        # ---- 音频预处理（转写前自动执行，见 audio_prep.py）
        self.prep_enabled: bool = _env_bool("MSHK_PREP_ENABLED", True)
        self.prep_target_sr: int = _env_int("MSHK_PREP_SR", 16000)
        # 目标响度：-20 LUFS 是 Whisper 类模型的稳妥值；-23 LUFS 为 EBU R128 广播标准
        self.prep_target_lufs: float = _env_float("MSHK_PREP_LUFS", -20.0)
        self.prep_true_peak: float = _env_float("MSHK_PREP_TP", -2.0)
        # LRA 是"允许的最大响度范围"。设 11（广播标准）会让原始 LRA 12.9 的录音
        # 无法线性归一化，loudnorm 自动回退到动态模式并压缩到 4.4 LU。
        # ASR 不需要压动态，放宽到 30 以保证 linear 优先、避免压缩。
        self.prep_lra: float = _env_float("MSHK_PREP_LRA", 30.0)
        self.prep_highpass_hz: int = _env_int("MSHK_PREP_HIGHPASS_HZ", 80)
        self.prep_denoise: str = _env("MSHK_PREP_DENOISE", "auto")      # auto|on|off
        self.prep_denoise_nr: float = _env_float("MSHK_PREP_DENOISE_NR", 10.0)
        self.prep_noise_gate_db: float = _env_float("MSHK_PREP_NOISE_GATE_DB", -55.0)
        # 阈值由带参考文本的 5 dB / 15 dB 白噪语料校准（见 outputs/audio-preprocess/REPORT.md）：
        # 估计 SNR 16 dB 时降噪使 WER 0.108→0.081；28 dB 时降噪反而使 WER 0.095→0.135。
        self.prep_snr_gate_db: float = _env_float("MSHK_PREP_SNR_GATE_DB", 19.0)
        self.prep_dereverb: str = _env("MSHK_PREP_DEREVERB", "auto")    # auto|on|off
        self.prep_dereverb_snr_gate_db: float = _env_float("MSHK_PREP_DEREVERB_SNR_DB", 12.0)
        self.prep_vad: bool = _env_bool("MSHK_PREP_VAD", True)
        self.prep_silence_db: float = _env_float("MSHK_PREP_SILENCE_DB", -38.0)
        self.prep_silence_min_s: float = _env_float("MSHK_PREP_SILENCE_MIN_S", 0.55)
        self.prep_keep_pad_ms: int = _env_int("MSHK_PREP_KEEP_PAD_MS", 250)
        # 首尾静音短于该值时是否跳过裁剪。实测中"总是裁"在 clean 上略有益、
        # 在部分混响样本上略有害，方向不稳定，故默认 0（保持已验证行为）；
        # 混响/远场场景可调高到 1.5 以减少边界抖动。
        self.prep_min_trim_s: float = _env_float("MSHK_PREP_MIN_TRIM_S", 0.0)
        self.prep_trim_mid: bool = _env_bool("MSHK_PREP_TRIM_MID", True)
        self.prep_mid_gap_ms: int = _env_int("MSHK_PREP_MID_GAP_MS", 8000)

        # ---- 业务阈值（PRD BR-01 / BR-02 / FR-02）
        self.min_duration_ms: int = _env_int("MSHK_MIN_DURATION_MS", 10_000)
        self.max_duration_ms: int = _env_int("MSHK_MAX_DURATION_MS", 15 * 60 * 1000)
        self.low_confidence_threshold: float = _env_float("MSHK_CONF_THRESHOLD", 0.6)
        # 16-bit PCM 的 RMS 低于该值视为静音（约 -40 dBFS）
        self.silence_rms: float = _env_float("MSHK_SILENCE_RMS", 300.0)
        self.silence_ratio: float = _env_float("MSHK_SILENCE_RATIO", 0.97)

        # ASR 首次调用要加载 whisper 模型，给足时间
        self.asr_timeout_s: int = _env_int("MSHK_ASR_TIMEOUT_S", 900)
        self.engine_timeout_s: int = _env_int("MSHK_ENGINE_TIMEOUT_S", 180)

        # ---- 默认值
        self.default_template: str = _env("MSHK_DEFAULT_TEMPLATE", "outpatient_record")
        # 香港醫療場景默認粵語：Whisper 會把粵語檢測成 zh，按普通話解碼錯字率極高
        self.default_language: str = _env("MSHK_DEFAULT_LANGUAGE", "yue")
        self.synthetic_tag: str = "【SYNTHETIC】"
        # 引擎自带脱敏基于简易 NER，会把「病人今日發熱」整句当成姓名替换成
        # [NAME_ZH_1]，破坏可读性；PRD 无强制脱敏要求，默认关闭。
        self.deidentify_enabled: bool = _env_bool("MSHK_DEIDENTIFY", False)

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def export_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "integration.db"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.audio_dir, self.export_dir):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
