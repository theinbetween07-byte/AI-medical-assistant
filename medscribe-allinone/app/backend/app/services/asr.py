"""
ASR 服務 - 封裝 faster-whisper

職責：
- 單例加載 Whisper 模型（首次慢，後續常駐內存）
- 動態切換模型型號（內置 small；擴展包提供 medium / large-v3 與 CapsWriter 引擎）
- 統一音頻預處理（ffmpeg → 16kHz mono WAV）
- 提供同步轉寫接口
- 粵/普/英 多語言支持
"""

import gc
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from app.config import (
    COMPUTE_TYPE,
    DEFAULT_BEAM_SIZE,
    DEFAULT_LANGUAGE,
    DEFAULT_MODEL,
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
        self.engine = "whisper"
        self.caps = None
        # 切换模型会把 self.model 置空再重新加载；与并发转写/预热互斥，避免拿到 None。
        self._lock = threading.RLock()
        self._initialized = True
        logger.info("ASRService 初始化完成（模型按需延遲加載）")

    def _resolve_model_path(self, model_size: str) -> tuple[str, str]:
        """根據型號決定加載路徑；發行版禁止在線下載。

        內置只有 small；medium / large-v3 以擴展包形式安裝在 models/ext 下。
        """
        from app.services import extensions

        entry = extensions.find_extension(model_size)
        if entry is not None and entry.get("kind") == "whisper":
            if not extensions.is_installed(entry):
                raise FileNotFoundError(
                    f"模型 {model_size} 的擴展包尚未安裝（{extensions.extension_path(entry)}）。"
                )
            return str(extensions.extension_path(entry)), "extension"
        if model_size in LOCAL_MODELS:
            return LOCAL_MODELS[model_size], "local"
        raise FileNotFoundError(
            f"模型 {model_size} 未隨包安裝（{MODEL_CACHE_DIR}）。"
            f"本發行版不聯網下載，請使用內置模型或安裝對應擴展包。"
        )

    def _load(self, model_size: str) -> None:
        """底層加載某個型號到 self.model"""
        from faster_whisper import WhisperModel

        path, source = self._resolve_model_path(model_size)
        logger.info(
            f"[ASR] 加載模型 {model_size}（source={source}, path={path}）"
        )
        start = time.time()
        self.model = WhisperModel(
            path,
            device=DEVICE,
            compute_type=COMPUTE_TYPE,
            download_root=str(MODEL_CACHE_DIR),
            local_files_only=True,  # 發行版：只用隨包模型，不觸發下載
        )
        self.current_model_size = model_size
        self.engine = "whisper"
        elapsed = time.time() - start
        logger.success(f"[ASR] 模型 {model_size} 加載完成，耗時 {elapsed:.1f}s")

    def ensure_loaded(self) -> None:
        """確保已加載默認模型（首次調用時）"""
        with self._lock:
            if self.engine == "caps":
                return
            if self.model is None:
                self._load(_pick_default_model())

    def set_model(self, model_size: str) -> dict:
        """
        切換模型：內置 Whisper 型號或已安裝的擴展模塊。

        Returns:
            {"model": str, "elapsed": float, "changed": bool}
        """
        from app.services import extensions

        entry = extensions.find_extension(model_size)
        if entry is not None:
            extensions.require_installed(model_size)
            if entry.get("kind") == "caps":
                with self._lock:
                    if self.current_model_size == model_size and self.engine == "caps":
                        return {"model": model_size, "elapsed": 0.0, "changed": False}
                    old = self.current_model_size
                    self._release_whisper()
                    from app.services.caps import CapsEngine

                    self.caps = CapsEngine(extensions.extension_path(entry))
                    self.engine = "caps"
                    self.current_model_size = model_size
                    # 只註冊，不預啟動：首次轉寫時再加載 1.4GB 模型，切換可秒回
                    return {
                        "model": model_size,
                        "previous": old,
                        "elapsed": 0.0,
                        "changed": True,
                        "source": "extension",
                    }
            # whisper 擴展（medium / large-v3）：走下面的模型加載流程
        elif model_size not in SUPPORTED_MODELS:
            raise ValueError(
                f"不支援的型號：{model_size}。支援：{SUPPORTED_MODELS}，"
                f"擴展：{[e['id'] for e in extensions._read_manifest()]}"
            )
        elif model_size not in LOCAL_MODELS:
            raise FileNotFoundError(f"模型 {model_size} 未隨包安裝。")

        with self._lock:
            if self.current_model_size == model_size and self.engine == "whisper" and self.model is not None:
                logger.info(f"[ASR] 模型已是 {model_size}，跳過")
                return {"model": model_size, "elapsed": 0.0, "changed": False}

            old = self.current_model_size
            self._stop_caps()
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
                "source": "extension" if entry is not None else "local",
            }

    def _release_whisper(self) -> None:
        self.model = None
        gc.collect()

    def _stop_caps(self) -> None:
        if getattr(self, "caps", None) is not None:
            try:
                self.caps.stop()
            finally:
                self.caps = None

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
        progress=None,
    ) -> dict:
        """同步轉寫音頻文件（自動確保模型已加載）

        ``progress``：可選回調 ``(fraction, phase, detail)``，用於上報真實進度。
        phase 取值：prepare / load / decode / finalize。
        """

        def emit(fraction: float, phase: str, detail: Optional[dict] = None) -> None:
            if progress is None:
                return
            try:
                progress(max(0.0, min(1.0, float(fraction))), phase, detail or {})
            except Exception:  # 進度上報失敗不影響轉寫
                pass

        emit(0.02, "prepare")
        self.ensure_loaded()

        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"音頻文件不存在：{audio_path}")
        if not validate_audio_filename(audio_path):
            logger.warning(f"非常見音頻格式：{audio_path}")

        logger.info(
            f"[ASR] 開始處理：{audio_path} "
            f"(engine={self.engine}, model={self.current_model_size}, language={language})"
        )
        t0 = time.time()
        emit(0.06, "prepare")
        wav_path = self._convert_to_wav(audio_path)
        t_convert = time.time() - t0
        logger.info(f"[ASR] 音頻轉碼耗時 {t_convert:.2f}s")
        emit(0.12, "decode")

        try:
            if self.engine == "caps":
                emit(0.2, "decode")
                result = self.caps.transcribe(wav_path, language)
                result["template"] = template
                emit(1.0, "finalize")
                return result

            # 取一次引用：切換模型只會替換 self.model，已開始的轉寫繼續用舊實例。
            model = self.model
            model_size = self.current_model_size
            if language in (None, "", "auto", "mix"):
                effective_lang = None
                logger.info("[ASR] 語言：自動檢測")
            else:
                effective_lang = language
                logger.info(f"[ASR] 語言：{language}（手動指定）")

            t1 = time.time()
            emit(0.14, "decode")
            segments_iter, info = model.transcribe(
                wav_path,
                language=effective_lang,
                initial_prompt=INITIAL_PROMPT,
                beam_size=beam_size,
                vad_filter=vad_filter,
                word_timestamps=True,
                condition_on_previous_text=True,
            )
            total = float(getattr(info, "duration", 0.0) or 0.0)
            emit(0.16, "decode", {"processed": 0.0, "total": total})
            segments = []
            text_parts = []
            last_emit = 0.0
            for seg in segments_iter:
                seg_data = {
                    "id": seg.id,
                    "start": round(seg.start, 2),
                    "end": round(seg.end, 2),
                    "text": seg.text.strip(),
                }
                segments.append(seg_data)
                text_parts.append(seg.text.strip())
                # 按已識別音頻時長推進進度；節流到 ~4 次/秒，避免過度寫狀態
                now = time.time()
                if now - last_emit >= 0.25:
                    last_emit = now
                    ratio = (float(seg.end) / total) if total > 0 else 0.0
                    emit(0.16 + 0.80 * min(max(ratio, 0.0), 1.0), "decode",
                         {"processed": round(float(seg.end), 1), "total": round(total, 1)})
            emit(0.96, "finalize", {"processed": round(total, 1), "total": round(total, 1)})
            t_transcribe = time.time() - t1
            emit(1.0, "finalize", {"processed": round(total, 1), "total": round(total, 1)})
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
                "model": model_size,
            }
        finally:
            if wav_path != audio_path and os.path.exists(wav_path):
                try:
                    os.unlink(wav_path)
                except Exception as e:
                    logger.warning(f"清理 WAV 失敗：{e}")


def _pick_default_model() -> str:
    """默認型號：環境變量指定值 → 隨包模型 → 第一個可用型號。"""
    wanted = os.environ.get("WHISPER_MODEL", DEFAULT_MODEL)
    if wanted in LOCAL_MODELS:
        return wanted
    if LOCAL_MODELS:
        return next(iter(LOCAL_MODELS))
    raise FileNotFoundError(
        "未找到随包 Whisper 模型：app/models/faster-whisper-small。"
        "请确认基础包完整解压（本发行版不联网下载模型）。"
    )


# 全局單例
_service: Optional[ASRService] = None


def get_asr_service() -> ASRService:
    """獲取 ASR 服務單例"""
    global _service
    if _service is None:
        _service = ASRService()
    return _service
