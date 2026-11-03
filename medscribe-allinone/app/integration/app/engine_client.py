"""报告引擎适配层（FR-05 / FR-06 / FR-07 / FR-08 / FR-09）。

上游：MedScribe HK report-engine（默认 http://127.0.0.1:8790）
只用到这些接口，全部为 JSON：

    GET    /health
    GET    /api/templates
    POST   /api/generate
    GET    /api/reports/{id}
    DELETE /api/reports/{id}
    POST   /api/reports/{id}/review/{field_key}
    POST   /api/reports/{id}/sign
    POST   /api/reports/{id}/export
    GET    /api/reports/{id}/preview
    POST   /api/reload
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from .asr_client import _read_body_retry
from .config import Settings


class EngineError(RuntimeError):
    def __init__(self, message: str, status: int = 0, detail: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail


class EngineClient:
    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg

    # ---------------------------------------------------------------- plumbing
    def _call(
        self,
        method: str,
        path: str,
        payload: Optional[dict] = None,
        query: Optional[dict] = None,
        timeout: Optional[int] = None,
        raw_text: bool = False,
    ) -> Any:
        url = f"{self.cfg.engine_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})}"

        data = None
        headers = {"Accept": "text/plain" if raw_text else "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            body = _read_body_retry(req, timeout or self.cfg.engine_timeout_s).decode(
                "utf-8", errors="replace"
            )
            return body if raw_text else (json.loads(body) if body else None)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                parsed: Any = json.loads(body)
                message = parsed.get("detail") if isinstance(parsed, dict) else body
            except json.JSONDecodeError:
                parsed, message = body, body
            raise EngineError(
                f"报告引擎返回 {exc.code}：{message}", status=exc.code, detail=parsed
            ) from exc
        except urllib.error.URLError as exc:
            raise EngineError(f"无法连接报告引擎（{url}）：{exc.reason}") from exc
        except (ConnectionResetError, ConnectionAbortedError) as exc:
            raise EngineError(f"报告引擎连接被重置（{url}）：{exc}") from exc

    # ------------------------------------------------------------------- read
    def health(self) -> dict:
        return self._call("GET", "/health", timeout=10)

    def templates(self) -> list[dict]:
        return self._call("GET", "/api/templates", timeout=15)

    def reload(self) -> dict:
        return self._call("POST", "/api/reload", timeout=30)

    # --------------------------------------------------------------- generate
    def generate(
        self,
        session_id: str,
        template_id: str,
        segments: list[dict],
        elements: Optional[list[dict]] = None,
        deidentify: bool = False,
        term_correction: bool = True,
        language_mode: str = "bilingual",
        keep_unclassified: bool = True,
    ) -> dict:
        payload: dict[str, Any] = {
            "session_id": session_id,
            "template_id": template_id,
            "segments": segments,
            "options": {
                "deidentify": deidentify,
                "term_correction": term_correction,
                "use_llm": False,
                "language_mode": language_mode,
                "keep_unclassified": keep_unclassified,
            },
        }
        if elements is not None:
            payload["elements"] = elements
        return self._call("POST", "/api/generate", payload)

    # ----------------------------------------------------------------- report
    def get_report(self, report_id: str) -> dict:
        return self._call("GET", f"/api/reports/{report_id}")

    def delete_report(self, report_id: str) -> dict:
        return self._call("DELETE", f"/api/reports/{report_id}")

    def review_field(
        self,
        report_id: str,
        field_key: str,
        action: str,
        value: Optional[str] = None,
        reviewer: str = "demo-doctor",
    ) -> dict:
        return self._call(
            "POST",
            f"/api/reports/{report_id}/review/{urllib.parse.quote(field_key, safe='')}",
            {"action": action, "value": value, "reviewer": reviewer},
        )

    def sign(self, report_id: str, signed_by: str = "demo-doctor") -> dict:
        return self._call("POST", f"/api/reports/{report_id}/sign", {"signed_by": signed_by})

    def export(
        self, report_id: str, formats: list[str], language_mode: str = "bilingual"
    ) -> list[dict]:
        return self._call(
            "POST",
            f"/api/reports/{report_id}/export",
            {"formats": formats, "language_mode": language_mode},
        )

    def preview(self, report_id: str, fmt: str = "text", language_mode: str = "bilingual") -> str:
        return self._call(
            "GET",
            f"/api/reports/{report_id}/preview",
            query={"format": fmt, "language_mode": language_mode},
            raw_text=True,
        )

    def audit(self, session_id: str = "", limit: int = 200) -> list[dict]:
        return self._call(
            "GET", "/api/audit", query={"session_id": session_id, "limit": limit}
        )
