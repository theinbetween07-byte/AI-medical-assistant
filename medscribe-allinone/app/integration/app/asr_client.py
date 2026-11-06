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

import http.client
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


_RST_TYPES = (ConnectionResetError, ConnectionAbortedError)


def _is_transport_reset(exc: BaseException) -> bool:
    """是否属于 Windows 回环上「响应完成前后连接被重置」这一类可重试故障。"""
    if isinstance(exc, _RST_TYPES):
        return True
    if isinstance(exc, urllib.error.URLError):
        return isinstance(exc.reason, _RST_TYPES)
    # IncompleteRead / RemoteDisconnected：同源，读 body 时连接已断
    return isinstance(exc, http.client.HTTPException)


def _request_retry(req, timeout: int, retries: int = 6) -> tuple[int, bytes]:
    """发请求并读完响应体；传输层重置自动重试。

    RST 既可能发生在建立连接（urlopen）阶段，也可能发生在读 body 阶段，
    因此必须把「发请求 + 读 body」放在同一个重试单元里，否则会漏掉后者。
    """
    last: BaseException | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, exc.read()
            except Exception as inner:  # body 读到一半被重置
                if not _is_transport_reset(inner):
                    raise
                last = inner
        except Exception as exc:
            if not _is_transport_reset(exc):
                raise
            last = exc
        if attempt < retries - 1:
            time.sleep(min(0.4 * (2**attempt), 3.0))
    raise ASRError(f"与本机服务通信被重置（已重试 {retries} 次）：{last}") from last


def _urlopen_retry(req, timeout: int, retries: int = 4):
    """兼容旧调用：只关心响应对象、需要自行读取 body 的场景请改用 _request_retry。"""
    for attempt in range(retries):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except Exception as exc:
            if not _is_transport_reset(exc) or attempt >= retries - 1:
                raise
            time.sleep(0.25 * (attempt + 1))
    raise RuntimeError("unreachable")  # pragma: no cover


def _read_body_retry(req, timeout: int, retries: int = 4) -> bytes:
    """读取完整响应体，并对 Windows localhost 偶发的连接重置做有界重试。"""
    return _request_retry(req, timeout, retries)[1]



def _json_request(
    url: str, payload: Optional[dict], timeout: int, token: str = ""
) -> tuple[int, Any]:
    data = None
    # Connection: close 让服务端在响应后正常 FIN，避免 Windows 回环 keep-alive 关闭竞态触发 RST
    headers = {"Accept": "application/json", "Connection": "close"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    status, raw = _request_retry(req, timeout)
    body = raw.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(body) if body else None
    except json.JSONDecodeError:
        parsed = {"detail": body}
    return status, parsed


def _get_json(url: str, timeout: int) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "Connection": "close"})
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
                    "Connection": "close",
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
        progress: Optional[Any] = None,
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

        if progress is not None:
            return self._transcribe_via_job(body, boundary, progress)

        for attempt in (0, 1):
            req = urllib.request.Request(
                f"{self.cfg.asr_url}/asr/transcribe",
                data=body,
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Accept": "application/json",
                    "Connection": "close",
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

    # --------------------------------------------------- transcribe with progress
    def _transcribe_via_job(self, body: bytes, boundary: str, progress) -> dict:
        """提交异步转写任务并轮询进度；进度回调 (fraction, phase, detail)。"""
        job_id = self._create_job(body, boundary)
        deadline = time.time() + max(float(self.cfg.asr_timeout_s), 180.0)
        delay = 0.35
        while True:
            if time.time() > deadline:
                raise ASRError("转写超时：上游长时间没有返回结果，请重试。")
            time.sleep(delay)
            delay = min(delay * 1.15, 1.2)
            state = self._get_job(job_id)
            status = state.get("status")
            if status == "running":
                try:
                    progress(
                        float(state.get("progress") or 0.0),
                        str(state.get("phase") or "decode"),
                        {
                            "processed": state.get("processed"),
                            "total": state.get("total"),
                        },
                    )
                except Exception:  # 进度回调异常不影响转写
                    pass
                continue
            if status == "done":
                result = state.get("result")
                if not result:
                    raise ASRError("转写任务已完成但未返回结果，请重试。")
                return result
            raise ASRError(f"ASR 转写失败：{state.get('error') or '未知错误'}")

    def _create_job(self, body: bytes, boundary: str) -> str:
        for attempt in (0, 1):
            req = urllib.request.Request(
                f"{self.cfg.asr_url}/asr/transcribe-jobs",
                data=body,
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Accept": "application/json",
                    "Connection": "close",
                    "Authorization": f"Bearer {self.token(refresh=attempt > 0)}",
                    "Content-Length": str(len(body)),
                },
                method="POST",
            )
            try:
                raw = _read_body_retry(req, 120.0)
                return str(json.loads(raw.decode("utf-8", errors="replace"))["job_id"])
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code in (401, 403) and attempt == 0:
                    continue
                if exc.code == 413:
                    raise ASRError("音频文件超过上游 50 MB 限制，请缩短录音。", status=413) from exc
                if exc.code in (404, 405):
                    # 上游版本较旧，没有任务端点
                    raise ASRError("当前 ASR 服务版本不支持进度任务，请更新基础包。",
                                   status=exc.code) from exc
                raise ASRError(f"ASR 转写失败（{exc.code}）：{detail}", status=exc.code) from exc
            except urllib.error.URLError as exc:
                raise ASRError(f"无法连接 ASR 服务：{exc.reason}") from exc
        raise ASRError("ASR 转写失败：鉴权重试后仍未通过。", status=401)

    def _get_job(self, job_id: str) -> dict:
        url = f"{self.cfg.asr_url}/asr/transcribe-jobs/{urllib.parse.quote(job_id)}"
        for attempt in (0, 1):
            req = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "Connection": "close",
                    "Authorization": f"Bearer {self.token(refresh=attempt > 0)}",
                },
                method="GET",
            )
            try:
                raw = _read_body_retry(req, 30.0)
                return json.loads(raw.decode("utf-8", errors="replace"))
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403) and attempt == 0:
                    continue
                detail = exc.read().decode("utf-8", errors="replace")
                raise ASRError(f"读取转写进度失败（{exc.code}）：{detail}", status=exc.code) from exc
            except urllib.error.URLError as exc:
                raise ASRError(f"无法连接 ASR 服务：{exc.reason}") from exc
        raise ASRError("读取转写进度失败：鉴权重试后仍未通过。", status=401)


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


def _has_content(text: str) -> bool:
    """至少含一个文字或数字，用于过滤纯标点/占位符号（防上游幻觉污染转写）。"""
    return any(ch.isalnum() for ch in text)


def map_segments(result: dict, threshold: float) -> list[dict]:
    """把 ASR 返回的 segments 映射为集成层分段结构。"""
    global_lang = str(result.get("language") or "").lower()
    prob = float(result.get("language_probability") or 0.0)
    raw_segments = result.get("segments") or []
    mapped: list[dict] = []

    for i, seg in enumerate(raw_segments):
        text = (seg.get("text") or "").strip()
        if not text or not _has_content(text):
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
