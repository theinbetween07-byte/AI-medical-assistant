"""
ASR 服務 - 封裝 faster-whisper

職責：
- 單例加載 Whisper 模型（首次慢，後續常駐內存）
- 動態切換模型型號（tiny / base / small / medium / large-v3）
- 統一音頻預處理（ffmpeg → 16kHz mono WAV）
- 提供同步轉寫接口
- 粵/普/英 多語言支持
"""

import gc
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from app.config import (
    COMPUTE_TYPE,
    DEFAULT_BEAM_SIZE,
    DEFAULT_LANGUAGE,
    DEFAULT_VAD_FILTER,
    DEVICE,
    FFMPEG_PATH,
    INITIAL_PROMPT,
    LOCAL_MODELS,
    MODEL_CACHE_DIR,
    SUPPORTED_MODELS,
    validate_audio_filename,
)


class ASRService:
    """ASR 服務單例（支持動態切換模型）"""

    _instance: Optional["ASRService"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.model = None
        self.current_model_size: Optional[str] = None
        self._initialized = True
        logger.info("ASRService 初始化完成（模型按需延遲加載）")

    def _resolve_model_path(self, model_size: str) -> tuple[str, str]:
        """
        根據型號決定加載路徑

        Returns:
            (model_path, source)
            source: "local" | "hf-hub" | "hf-cache"
        """
        if model_size in LOCAL_MODELS:
            return LOCAL_MODELS[model_size], "local"
        # 沒本地 → 從 HF Hub 下載（會自動緩存）
        return model_size, "hf-hub"

    def _load(self, model_size: str) -> None:
        """底層加載某個型號到 self.model"""
        from faster_whisper import WhisperModel

        path, source = self._resolve_model_path(model_size)
        logger.info(
            f"[ASR] 加載模型 {model_size}（source={source}, path={path}）"
        )
        MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        start = time.time()
        self.model = WhisperModel(
            path,
            device=DEVICE,
            compute_type=COMPUTE_TYPE,
            download_root=str(MODEL_CACHE_DIR),
            local_files_only=(source == "local"),
        )
        self.current_model_size = model_size
        elapsed = time.time() - start
        logger.success(f"[ASR] 模型 {model_size} 加載完成，耗時 {elapsed:.1f}s")

    def ensure_loaded(self) -> None:
        """確保已加載默認模型（首次調用時）"""
        if self.model is None:
            self._load(_pick_default_model())

    def set_model(self, model_size: str) -> dict:
        """
        動態切換模型型號

        Returns:
            {"model": str, "elapsed": float, "changed": bool}
        """
        if model_size not in SUPPORTED_MODELS:
            raise ValueError(
                f"不支援的型號：{model_size}。支援：{SUPPORTED_MODELS}"
            )

        if self.current_model_size == model_size and self.model is not None:
            logger.info(f"[ASR] 模型已是 {model_size}，跳過")
            return {"model": model_size, "elapsed": 0.0, "changed": False}

        # 釋放舊模型
        old = self.current_model_size
        self.model = None
        self.current_model_size = None
        gc.collect()

        t0 = time.time()
        self._load(model_size)
        elapsed = time.time() - t0
        return {
            "model": model_size,
            "previous": old,
            "elapsed": round(elapsed, 2),
            "changed": True,
            "source": self._resolve_model_path(model_size)[1],
        }

    # ============ 音頻預處理 ============
    def _convert_to_wav(self, input_path: str) -> str:
        """用 ffmpeg 將任意格式音頻轉為 16kHz mono WAV"""
        output_path = input_path + ".converted.wav"
        cmd = [
            FFMPEG_PATH,
            "-y",
            "-i", input_path,
            "-ar", "16000",
            "-ac", "1",
            "-f", "wav",
            "-loglevel", "error",
            output_path,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except FileNotFoundError:
            raise RuntimeError(
                f"找不到 ffmpeg：{FFMPEG_PATH}\n"
                f"請用 winget 安裝：winget install -e --id Gyan.FFmpeg\n"
                f"或設置環境變量 FFMPEG_PATH"
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("ffmpeg 轉碼超時（>60s）")

        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg 轉碼失敗：{result.stderr}")
        if not os.path.exists(output_path):
            raise RuntimeError("ffmpeg 轉碼後未生成輸出文件")
        return output_path

    # ============ 轉寫 ============
    def transcribe(
        self,
        audio_path: str,
        language: str = DEFAULT_LANGUAGE,
        template: str = "discharge_summary",
        beam_size: int = DEFAULT_BEAM_SIZE,
        vad_filter: bool = DEFAULT_VAD_FILTER,
    ) -> dict:
        """同步轉寫音頻文件（自動確保模型已加載）"""
        self.ensure_loaded()

        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"音頻文件不存在：{audio_path}")
        if not validate_audio_filename(audio_path):
            logger.warning(f"非常見音頻格式：{audio_path}")

        logger.info(
            f"[ASR] 開始處理：{audio_path} "
            f"(model={self.current_model_size}, language={language})"
        )
        t0 = time.time()
        wav_path = self._convert_to_wav(audio_path)
        t_convert = time.time() - t0
        logger.info(f"[ASR] 音頻轉碼耗時 {t_convert:.2f}s")

        if language in (None, "", "auto", "mix"):
            effective_lang = None
            logger.info("[ASR] 語言：自動檢測")
        else:
            effective_lang = language
            logger.info(f"[ASR] 語言：{language}（手動指定）")

        try:
            t1 = time.time()
            segments_iter, info = self.model.transcribe(
                wav_path,
                language=effective_lang,
                initial_prompt=INITIAL_PROMPT,
                beam_size=beam_size,
                vad_filter=vad_filter,
                word_timestamps=True,
                condition_on_previous_text=True,
            )
            segments = []
            text_parts = []
            for seg in segments_iter:
                seg_data = {
                    "id": seg.id,
                    "start": round(seg.start, 2),
                    "end": round(seg.end, 2),
                    "text": seg.text.strip(),
                }
                segments.append(seg_data)
                text_parts.append(seg.text.strip())
            t_transcribe = time.time() - t1
            logger.info(
                f"[ASR] 轉寫完成：{len(segments)} 段，"
                f"音頻 {info.duration:.1f}s，耗時 {t_transcribe:.1f}s，"
                f"語言={info.language}（概率 {info.language_probability:.2f}）"
            )

            return {
                "language": info.language,
                "language_probability": round(info.language_probability, 4),
                "duration": round(info.duration, 2),
                "text": "\n".join(text_parts),
                "segments": segments,
                "elapsed": round(t_transcribe, 2),
                "template": template,
                "model": self.current_model_size,
            }
        finally:
            if wav_path != audio_path and os.path.exists(wav_path):
                try:
                    os.unlink(wav_path)
                except Exception as e:
                    logger.warning(f"清理 WAV 失敗：{e}")


def _pick_default_model() -> str:
    """優先選本地已有的最大型號，否則回退 medium"""
    if LOCAL_MODELS:
        # 按 SUPPORTED_MODELS 順序，本地的優先
        for m in SUPPORTED_MODELS:
            if m in LOCAL_MODELS:
                return m
    return os.environ.get("WHISPER_MODEL", "medium")


# 全局單例
_service: Optional[ASRService] = None


def get_asr_service() -> ASRService:
    """獲取 ASR 服務單例"""
    global _service
    if _service is None:
        _service = ASRService()
    return _service
