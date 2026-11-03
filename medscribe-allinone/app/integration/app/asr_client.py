"""ASR 适配层（FR-02 / 技术设计 4.1）。

上游是 medical-ai-assistant 后端：
    POST /auth/login      -> {token, user}
    POST /asr/transcribe  -> {language, language_probability, duration, text,
                              segments:[{id,start,end,text}], elapsed, template, model}

该服务要求 JWT。本项目不做账号体系，所以这里用一个固定的演示账号自动登录
（首次登录失败会尝试注册一次）。token 缓存在内存里，401 时自动刷新重试。

上游不返回逐段置信度，集成层只能按"段语种是否与整段识别语种一致"给出代理值，
并在 confidence_source 中如实标注，避免把代理值当成真实置信度（AI-02 失败行为）。
"""

from __future__ import annotations

import json
import mimetypes
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Optional

from .config import Settings


class ASRError(RuntimeError):
    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


def _urlopen_retry(req, timeout: int, retries: int = 2, delay: float = 0.8):
    """Windows 下 localhost 偶发 ConnectionResetError（WinError 10054）突发，有界重试两次。"""
    for attempt in range(retries + 1):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.URLError as exc:
            if not isinstance(exc.reason, ConnectionResetError):
                raise
            if attempt < retries:
                time.sleep(delay)
                continue
            raise
        except ConnectionResetError:
            if attempt < retries:
                time.sleep(delay)
                continue
            raise
    raise RuntimeError("unreachable")  # pragma: no cover


def _read_body_retry(req, timeout: int, retries: int = 2, delay: float = 0.8) -> bytes:
    """读取完整响应体，并对 Windows localhost 偶发的连接重置做有界重试。

    已知现象：连接已建立，但读取响应时抛 ConnectionResetError [WinError 10054]。
    只重试这类传输错误；HTTP 错误码与其它网络错误原样抛出。
    """
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError as exc:
            if not isinstance(exc.reason, ConnectionResetError):
                raise
            if attempt >= retries:
                raise
            time.sleep(delay)
        except (ConnectionResetError, ConnectionAbortedError):
            if attempt >= retries:
                raise
            time.sleep(delay)
    raise RuntimeError("unreachable")  # pragma: no cover


def _json_request(
    url: str, payload: Optional[dict], timeout: int, token: str = ""
) -> tuple[int, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with _urlopen_retry(req, timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"detail": body}
        return exc.code, parsed
    except urllib.error.URLError as exc:
        raise ASRError(f"无法连接 ASR 服务：{exc.reason}") from exc


def _get_json(url: str, timeout: int) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        # 用带重试的读取：Windows 上 localhost 的 RST 常发生在 urlopen 成功之后
        body = _read_body_retry(req, timeout)
    except urllib.error.URLError as exc:
        raise ASRError(f"无法连接 ASR 服务：{exc.reason}") from exc
    return json.loads(body.decode("utf-8", errors="replace"))


class ASRClient:
    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self._token: str = ""
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ auth
    def _login(self) -> str:
        status, body = _json_request(
            f"{self.cfg.asr_url}/auth/login",
            {"username": self.cfg.asr_username, "password": self.cfg.asr_password},
            timeout=30,
        )
        if status == 200 and isinstance(body, dict) and body.get("token"):
            return body["token"]
        if status in (400, 401):
            # 首次部署：注册演示账号
            _json_request(
                f"{self.cfg.asr_url}/auth/register",
                {
                    "username": self.cfg.asr_username,
                    "password": self.cfg.asr_password,
                    "display_name": "演示医生",
                    "role": "doctor",
                },
                timeout=30,
            )
            status, body = _json_request(
                f"{self.cfg.asr_url}/auth/login",
                {"username": self.cfg.asr_username, "password": self.cfg.asr_password},
                timeout=30,
            )
            if status == 200 and isinstance(body, dict) and body.get("token"):
                return body["token"]
        raise ASRError(f"ASR 登录失败（{status}）：{body}", status=status)

    def token(self, refresh: bool = False) -> str:
        with self._lock:
            if refresh or not self._token:
                self._token = self._login()
            return self._token

    # ---------------------------------------------------------------- health
    def health(self) -> dict:
        return _get_json(f"{self.cfg.asr_url}/health", timeout=10)

    def models(self) -> dict:
        return _get_json(f"{self.cfg.asr_url}/asr/models", timeout=10)

    def load_model(self, model: str) -> dict:
        """切换上游 Whisper 型号。

        上游接口是 `POST /asr/load-model?model=<id>`，成功时同步返回加载耗时。
        扩展模型首次加载需读取本地权重（数十秒），因此沿用 asr_timeout_s。
        """
        url = f"{self.cfg.asr_url}/asr/load-model?{urllib.parse.urlencode({'model': model})}"
        for attempt in (0, 1):
            req = urllib.request.Request(
                url,
                data=b"",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self.token(refresh=attempt > 0)}",
                    "Content-Length": "0",
                },
                method="POST",
            )
            try:
                raw = _read_body_retry(req, self.cfg.asr_timeout_s)
                return json.loads(raw.decode("utf-8", errors="replace")) if raw else {}
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code in (401, 403) and attempt == 0:
                    continue
                raise ASRError(f"切换模型失败（{exc.code}）：{detail}", status=exc.code) from exc
            except urllib.error.URLError as exc:
                raise ASRError(f"无法连接 ASR 服务：{exc.reason}") from exc
        raise ASRError("切换模型失败：鉴权重试后仍未通过。", status=401)

    # ------------------------------------------------------------- transcribe
    def transcribe(
        self,
        wav_path: Path,
        language: str = "",
        asr_template: str = "soap",
        beam_size: int = 5,
    ) -> dict:
        boundary = f"----MedScribe{uuid.uuid4().hex}"
        mime = mimetypes.guess_type(str(wav_path))[0] or "audio/wav"
        file_bytes = wav_path.read_bytes()

        parts: list[bytes] = []
        for name, value in (
            ("template", asr_template),
            ("language", language or "auto"),
            ("beam_size", str(beam_size)),
        ):
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n".encode("utf-8")
            )
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{wav_path.name}"\r\nContent-Type: {mime}\r\n\r\n'.encode("utf-8")
            + file_bytes
            + b"\r\n"
        )
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(parts)

        for attempt in (0, 1):
            req = urllib.request.Request(
                f"{self.cfg.asr_url}/asr/transcribe",
                data=body,
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self.token(refresh=attempt > 0)}",
                    "Content-Length": str(len(body)),
                },
                method="POST",
            )
            try:
                # 转写响应较大，Windows localhost 可能在读取阶段重置连接；
                # 用整段读取重试，避免把上游一次成功的转写变成 500。
                raw = _read_body_retry(req, self.cfg.asr_timeout_s)
                return json.loads(raw.decode("utf-8", errors="replace"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code in (401, 403) and attempt == 0:
                    continue
                if exc.code == 413:
                    raise ASRError("音频文件超过上游 50 MB 限制，请缩短录音。", status=413) from exc
                raise ASRError(f"ASR 转写失败（{exc.code}）：{detail}", status=exc.code) from exc
            except urllib.error.URLError as exc:
                raise ASRError(f"无法连接 ASR 服务：{exc.reason}") from exc
        raise ASRError("ASR 转写失败：鉴权重试后仍未通过。", status=401)


# --------------------------------------------------------------------------
# 分段映射（ASR 结构 -> TranscriptSegment）
# --------------------------------------------------------------------------

_CJK = lambda ch: "\u4e00" <= ch <= "\u9fff"  # noqa: E731


def detect_lang(text: str, global_lang: str = "") -> str:
    """按字符构成判定语种标签；global_lang 来自模型整体判断，仅作提示。"""
    zh = sum(1 for ch in text if _CJK(ch))
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    total = zh + latin
    if total == 0:
        return "unknown"
    zh_ratio = zh / total
    if zh_ratio >= 0.85:
        # whisper 对粤语口述常返回 yue；退化到 zh 时仍标 zh-yue 会误报，这里据全局判断收敛
        if global_lang in ("yue", "zh-yue"):
            return "zh-yue"
        return "zh"
    if zh_ratio <= 0.15:
        return "en"
    return "mixed"


def map_segments(result: dict, threshold: float) -> list[dict]:
    """把 ASR 返回的 segments 映射为集成层分段结构。"""
    global_lang = str(result.get("language") or "").lower()
    prob = float(result.get("language_probability") or 0.0)
    raw_segments = result.get("segments") or []
    mapped: list[dict] = []

    for i, seg in enumerate(raw_segments):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        lang = detect_lang(text, global_lang)
        # 代理置信度：整段概率 × 语种一致性折扣
        consistent = (
            (lang in ("zh", "zh-yue") and global_lang in ("yue", "zh-yue", "zh"))
            or (lang == "en" and global_lang == "en")
            or lang == "mixed"
        )
        conf = prob if consistent else round(prob * 0.75, 4)
        conf = max(0.0, min(1.0, conf))
        mapped.append(
            {
                "id": f"S-{i + 1:03d}",
                "index": i,
                "start_ms": int(round(float(seg.get("start") or 0) * 1000)),
                "end_ms": int(round(float(seg.get("end") or 0) * 1000)),
                "text": text,
                "text_raw": text,
                "lang": lang,
                "confidence": conf,
                "confidence_source": "asr-global",
                "low_confidence": conf < threshold,
                "confirmed": False,
                "edited": False,
            }
        )
    return mapped
