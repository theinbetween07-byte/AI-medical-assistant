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
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from app.config import (
    ASR_CHUNK_OVERLAP,
    ASR_CHUNK_SECONDS,
    ASR_CPU_THREADS,
    AUTO_LANGUAGE,
    COMPUTE_TYPE,
    DEFAULT_BEAM_SIZE,
    DEFAULT_LANGUAGE,
    DEFAULT_MODEL,
    DEFAULT_VAD_FILTER,
    DEVICE,
    FFMPEG_PATH,
    INITIAL_PROMPT_YUE,
    INITIAL_PROMPT_ZH,
    LOCAL_MODELS,
    MODEL_CACHE_DIR,
    SUPPORTED_MODELS,
    validate_audio_filename,
)


# ============ 運行設備選擇 ============
_DLL_HANDLES: list = []
_RUNTIME_CACHE: Optional[tuple[str, str]] = None

_PUNCT_SKIP = set(" \t\r\n，。、；：,.!?;:")


def _overlap_key(text: str) -> list[tuple[int, str]]:
    """归一化后的 (原索引, 小写字符)，忽略空白与标点，用于判断重复开头。"""
    return [(i, ch.lower()) for i, ch in enumerate(text)
            if ch not in _PUNCT_SKIP and not ch.isspace()]


def _extends_previous(prev_text: str, cur_text: str) -> bool:
    """上一块输出的是被切口截断的半句，这一块才是完整版。"""
    prev_key = [ch for _, ch in _overlap_key(prev_text)]
    cur_key = [ch for _, ch in _overlap_key(cur_text)]
    if len(prev_key) < 6 or len(cur_key) <= len(prev_key):
        return False
    return cur_key[:len(prev_key)] == prev_key


def _trim_overlap_text(text: str, prev_tail: str, max_overlap: int = 16) -> str:
    """去掉 text 开头与上一块结尾重复的部分。

    分块重叠后，切口附近的句子会被两块各解一次：上一块是截断版，
    这一块是完整版。这里按最长公共前后缀裁掉重复开头，保留完整表达。
    完全重复时返回空串，由调用方丢弃。
    """
    if not prev_tail or not text:
        return text
    prev_key = _overlap_key(prev_tail)
    cur_key = _overlap_key(text)
    limit = min(len(prev_key), len(cur_key), max_overlap)
    for k in range(limit, 3, -1):
        if [ch for _, ch in prev_key[-k:]] == [ch for _, ch in cur_key[:k]]:
            end_index = cur_key[k - 1][0] + 1
            return text[end_index:].lstrip(" \t，。、；：,.;:!?")
    return text


def _register_cuda_dlls() -> None:
    """把 runtime 内 nvidia 包的 DLL 目錄加入 Windows 搜索路徑。

    ctranslate2 不會自動找 site-packages/nvidia/*/bin；未註冊時即使有 GPU，
    加載也會報 "cublas64_12.dll is not found"，只能退回 CPU。
    """
    if _DLL_HANDLES:
        return
    base = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    if not base.exists():
        return
    for name in ("cublas", "cudnn", "cuda_nvrtc", "cuda_runtime"):
        binp = base / name / "bin"
        if not binp.exists():
            continue
        try:
            _DLL_HANDLES.append(os.add_dll_directory(str(binp)))
        except (AttributeError, OSError):
            pass
        os.environ["PATH"] = str(binp) + os.pathsep + os.environ.get("PATH", "")


def _pick_runtime() -> tuple[str, str]:
    want_device = (DEVICE or "auto").strip().lower()
    want_compute = (COMPUTE_TYPE or "auto").strip().lower()
    cpu_compute = "int8" if want_compute == "auto" else want_compute
    if want_device == "cpu":
        return "cpu", cpu_compute
    _register_cuda_dlls()
    cuda_count = 0
    try:
        import ctranslate2

        cuda_count = int(ctranslate2.get_cuda_device_count())
    except Exception as exc:  # 無驅動 / 無 CUDA runtime
        logger.warning(f"[ASR] CUDA 偵測失敗，使用 CPU：{exc}")
    if want_device == "cuda" or cuda_count > 0:
        if cuda_count > 0:
            return "cuda", ("float16" if want_compute == "auto" else want_compute)
    return "cpu", cpu_compute


def resolve_runtime() -> tuple[str, str]:
    """(device, compute_type)，結果緩存；GPU 加載失敗時會改寫為 CPU。"""
    global _RUNTIME_CACHE
    if _RUNTIME_CACHE is None:
        _RUNTIME_CACHE = _pick_runtime()
    return _RUNTIME_CACHE


def _cpu_threads() -> int:
    """CPU 線程數：全核（24）反而更慢，且 int8 大模型易 mkl_malloc 失敗。"""
    if ASR_CPU_THREADS > 0:
        return ASR_CPU_THREADS
    return max(1, min(8, os.cpu_count() or 4))


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
        self.device = "cpu"
        self.compute_type = "int8"
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
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:
            # PyAV 的 FFmpeg DLL 依賴在 Windows 上有 260 字元路徑上限；
            # 安裝目錄過深時，底層只報 "__reduce_cython__" 之類的怪錯，這裡換成人能看懂的話
            raise RuntimeError(
                "音频解码库加载失败，转写无法开始。最常见原因是安装路径过深"
                "（Windows 对 DLL 路径有 260 字符上限）。请把 MedScribe 文件夹移到"
                " C:\\MedScribe 这类短目录后重启服务。"
                f"原始错误：{type(exc).__name__}: {exc}"
            ) from exc

        path, source = self._resolve_model_path(model_size)
        logger.info(
            f"[ASR] 加載模型 {model_size}（source={source}, path={path}）"
        )
        start = time.time()
        device, compute = resolve_runtime()
        threads = _cpu_threads()

        def _open(dev: str, ctype: str):
            return WhisperModel(
                path,
                device=dev,
                compute_type=ctype,
                cpu_threads=threads,
                download_root=str(MODEL_CACHE_DIR),
                local_files_only=True,  # 發行版：只用隨包模型，不觸發下載
            )

        try:
            self.model = _open(device, compute)
        except Exception as exc:
            if device != "cuda":
                raise RuntimeError(
                    f"模型 {model_size} 加載失敗（{type(exc).__name__}: {exc}）。"
                    "若提示內存不足，請改用 small 或 medium 模型。"
                ) from exc
            # 顯存不足 / 驅動不匹配：退回 CPU，保證可用
            logger.warning(f"[ASR] GPU 加載 {model_size} 失敗，回退 CPU：{exc}")
            global _RUNTIME_CACHE
            _RUNTIME_CACHE = ("cpu", "int8")
            device, compute = "cpu", "int8"
            try:
                self.model = _open(device, compute)
            except Exception as cpu_exc:
                raise RuntimeError(
                    f"模型 {model_size} 加載失敗（GPU 與 CPU 均不可用）。"
                    f"CPU 錯誤：{type(cpu_exc).__name__}: {cpu_exc}。"
                    "若提示內存不足，請改用 small 或 medium 模型。"
                ) from cpu_exc

        self.device, self.compute_type = device, compute
        self.current_model_size = model_size
        self.engine = "whisper"
        elapsed = time.time() - start
        logger.success(
            f"[ASR] 模型 {model_size} 加載完成，耗時 {elapsed:.1f}s"
            f"（{device}/{compute}{f', cpu_threads={threads}' if device == 'cpu' else ''}）"
        )

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

    # ============ 分块解碼 ============
    def _decode_all(self, model, wav_path, language, prompt, beam_size, emit) -> dict:
        """按 ASR_CHUNK_SECONDS 分塊解碼，塊內開上下文續寫。

        為什麼分塊：condition_on_previous_text 能讓標點與專名明顯更準，
        但整段長音頻開啟它會循環重複/幻覺（實測 289s 音頻把「門診接診記錄」
        連續輸出 7 次並夾雜亂碼）。切 30s 小塊後，塊內有上下文、塊間互不影響：
        289s 音頻內容完整無重複，耗時 21.2s（整段解碼 21.3s），逗號/句號恢復，
        「血常規未見異常」也從誤識別回歸正確。
        """
        import wave

        with wave.open(wav_path, "rb") as w:
            sr = w.getframerate()
            nch = w.getnchannels()
            sampwidth = w.getsampwidth()
            raw = w.readframes(w.getnframes())

        try:
            import numpy as np
        except Exception:  # 沒有 numpy 時退回整段解碼
            np = None

        logger.info(
            f"[ASR] WAV 格式：sr={sr}, channels={nch}, sampwidth={sampwidth}, "
            f"numpy={'yes' if np is not None else 'no'}"
        )
        if np is None or sr != 16000 or nch != 1 or sampwidth != 2:
            logger.warning("[ASR] WAV 格式不符合分塊條件，退回整段解碼")
            segments, texts = [], []
            segs, info = model.transcribe(
                wav_path,
                language=None if language in (None, "", "auto") else language,
                initial_prompt=prompt,
                beam_size=beam_size,
                vad_filter=True,
                word_timestamps=False,
                condition_on_previous_text=False,
            )
            for s in segs:
                segments.append({
                    "id": len(segments),
                    "start": round(float(s.start), 2),
                    "end": round(float(s.end), 2),
                    "text": s.text.strip(),
                })
                texts.append(s.text.strip())
            return {
                "segments": segments,
                "texts": texts,
                "duration": float(getattr(info, "duration", 0.0) or 0.0),
                "language": info.language,
                "language_probability": info.language_probability,
            }

        audio = np.frombuffer(raw, dtype=np.int16).astype("float32") / 32768.0
        total = len(audio) / float(sr)
        chunk_seconds = max(5, ASR_CHUNK_SECONDS)
        overlap = max(0.0, min(ASR_CHUNK_OVERLAP, chunk_seconds / 2.0))
        window = max(1, int(sr * chunk_seconds))
        step = max(1, int(sr * max(1.0, chunk_seconds - overlap)))
        chunk_count = (len(audio) + step - 1) // step
        logger.info(
            f"[ASR] 分塊解碼：{total:.1f}s，塊長 {chunk_seconds}s，"
            f"重疊 {overlap:.1f}s，共 {chunk_count} 塊"
        )
        lang = language
        segments, texts = [], []
        language_out = language or "zh"
        probability_out = 0.0
        covered_until = -1.0
        prev_tail = ""
        last_emit = 0.0
        for start in range(0, len(audio), step):
            chunk = audio[start:start + window]
            chunk_start = start / float(sr)
            segs, info = model.transcribe(
                chunk,
                language=None if lang in (None, "", "auto") else lang,
                initial_prompt=prompt,
                beam_size=beam_size,
                vad_filter=False,
                word_timestamps=False,
                condition_on_previous_text=True,
                repetition_penalty=1.1,
            )
            if lang in (None, "", "auto"):
                lang = info.language
                language_out = info.language
                probability_out = info.language_probability
            for s in segs:
                abs_start = chunk_start + float(s.start)
                end_abs = chunk_start + float(s.end)
                text = s.text.strip()
                if not text:
                    continue
                # 完全落在已覆盖区间：上一块已经输出过，跳过
                if covered_until > 0 and end_abs <= covered_until + 0.05:
                    continue
                # 上一块的截断版被这一块的完整版覆盖：用完整版替换，保留更早的开始时间
                replaced_tail = False
                if segments and _extends_previous(segments[-1]["text"], text):
                    replaced = segments.pop()
                    if texts:
                        texts.pop()
                    abs_start = min(abs_start, float(replaced["start"]))
                    covered_until = max(covered_until, float(replaced["end"]))
                    replaced_tail = True
                # 跨在切口上的句子：去掉与上一块重复的开头，避免同一句出现两次
                if not replaced_tail and abs_start < covered_until:
                    text = _trim_overlap_text(text, prev_tail)
                    if not text:
                        continue
                segments.append({
                    "id": len(segments),
                    "start": round(abs_start, 2),
                    "end": round(end_abs, 2),
                    "text": text,
                })
                texts.append(text)
                prev_tail = " ".join(texts[-2:])[-120:]
                now = time.time()
                if total > 0 and now - last_emit >= 0.25:
                    last_emit = now
                    emit(0.16 + 0.80 * min(max(end_abs / total, 0.0), 1.0), "decode",
                         {"processed": round(end_abs, 1), "total": round(total, 1)})
            covered_until = max(covered_until, chunk_start + len(chunk) / float(sr))
        return {
            "segments": segments,
            "texts": texts,
            "duration": total,
            "language": language_out,
            "language_probability": probability_out,
        }

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
                # Whisper 的语言检测会把粤语判成 zh，按普通话解码错字率极高。
                # 本部署把 auto 固定为粤语；需要普通话/英语时显式指定。
                if AUTO_LANGUAGE and AUTO_LANGUAGE not in ("auto", ""):
                    effective_lang = AUTO_LANGUAGE
                    logger.info(f"[ASR] 語言：自動 → {effective_lang}（部署默認）")
                else:
                    effective_lang = None
                    logger.info("[ASR] 語言：自動檢測")
            else:
                effective_lang = language
                logger.info(f"[ASR] 語言：{language}（手動指定）")

            # 中文術語提示詞只對中文 / 粵語有效。指定其他語言時再帶上它，
            # 會把解碼結果重新拉回中文（實測指定 en 仍輸出中文），因此按語言取捨。
            if effective_lang == "yue":
                prompt = INITIAL_PROMPT_YUE
            elif effective_lang in (None, "zh"):
                prompt = INITIAL_PROMPT_ZH
            else:
                prompt = None

            t1 = time.time()
            emit(0.14, "decode")
            decoded = self._decode_all(
                model, wav_path, effective_lang, prompt, beam_size, emit
            )
            segments = decoded["segments"]
            text_parts = decoded["texts"]
            total = decoded["duration"]
            emit(0.96, "finalize", {"processed": round(total, 1), "total": round(total, 1)})
            t_transcribe = time.time() - t1
            emit(1.0, "finalize", {"processed": round(total, 1), "total": round(total, 1)})
            logger.info(
                f"[ASR] 轉寫完成：{len(segments)} 段，"
                f"音頻 {total:.1f}s，耗時 {t_transcribe:.1f}s，"
                f"語言={decoded['language']}（概率 {decoded['language_probability']:.2f}）"
            )

            return {
                "language": decoded["language"],
                "language_probability": round(decoded["language_probability"], 4),
                "duration": round(total, 2),
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
