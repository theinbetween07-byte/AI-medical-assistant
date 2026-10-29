"""HTTP 层集成测试（FastAPI TestClient），覆盖宿主程序/网页集成时依赖的接口契约。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

PROJECT = Path(__file__).resolve().parents[1]
# 术语库在仓库 data/terms；兼容旧的 report-engine/terms 布局
TERMS_DIR = PROJECT / "terms" if (PROJECT / "terms").is_dir() else PROJECT.parent / "data" / "terms"

SEGMENTS = [
    {"id": "S-001", "start_ms": 0, "end_ms": 6000, "lang": "mixed", "confidence": 0.93,
     "text": "病人講呢三日喉嚨痛同咳，無發燒。"},
    {"id": "S-002", "start_ms": 6000, "end_ms": 12000, "lang": "en", "confidence": 0.9,
     "text": "On examination, blood pressure 128 over 76. Impression is upper respiratory tract infection."},
    {"id": "S-003", "start_ms": 12000, "end_ms": 18000, "lang": "en", "confidence": 0.9,
     "text": "No known drug allergy. I will prescribe panadol 500 mg PO TDS for 3 days."},
]


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    cfg = Settings()
    cfg.data_dir = tmp_path / "data"
    cfg.templates_dir = PROJECT / "templates"
    cfg.terms_dir = TERMS_DIR
    cfg.ensure_dirs()
    app = create_app(cfg)
    with TestClient(app) as test_client:
        yield test_client
    app.state.service.store.close()


def _generate(client: TestClient) -> dict:
    response = client.post("/api/generate", json={
        "session_id": "SES-API",
        "template_id": "general_soap",
        "segments": SEGMENTS,
        "options": {"term_correction": True},
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_health_and_templates_and_page(client: TestClient) -> None:
    health = client.get("/health").json()
    assert health["status"] == "ok"
    assert "general_soap" in health["templates"]
    assert health["llm"]["enabled"] is False, "默认必须离线（NFR-04）"

    templates = client.get("/api/templates").json()
    assert any(t["id"] == "general_soap" for t in templates)
    assert all(t["fields"] for t in templates)

    page = client.get("/")
    assert page.status_code == 200 and "MedScribe HK" in page.text

    sample = client.get("/api/sample").json()
    assert sample["segments"] and sample["note"]


def test_generate_review_sign_export_flow(client: TestClient) -> None:
    result = _generate(client)
    report = result["report"]
    report_id = report["id"]
    assert result["pipeline"] and report["fields"]

    # 未签署导出 -> 409（BR-10）
    blocked = client.post(f"/api/reports/{report_id}/export", json={"formats": ["text"]})
    assert blocked.status_code == 409

    # 字段校审：编辑一个字段，其余必填采纳
    first = report["fields"][0]
    edited = client.post(
        f"/api/reports/{report_id}/review/{first['key']}",
        json={"action": "edit", "value": "医生修订内容", "reviewer": "dr-api"},
    )
    assert edited.status_code == 200
    edited_fields = {f["key"]: f for f in edited.json()["fields"]}
    assert edited_fields[first["key"]]["final_value"] == "医生修订内容"
    assert edited_fields[first["key"]]["ai_value"] == first["ai_value"]
    # 确认过的字段必须从缺失清单中移除（门禁随校审实时重算）
    assert first["key"] not in edited.json()["missing_required"]

    for field in report["fields"]:
        if field["required"] and field["key"] != first["key"]:
            assert client.post(
                f"/api/reports/{report_id}/review/{field['key']}", json={"action": "accept"}
            ).status_code == 200

    preview = client.get(f"/api/reports/{report_id}/preview?format=text")
    assert preview.status_code == 200 and "医" in preview.text

    signed = client.post(f"/api/reports/{report_id}/sign", json={"signed_by": "dr-api"}).json()
    assert signed["ok"] is True and signed["report"]["status"] == "signed"

    exported = client.post(
        f"/api/reports/{report_id}/export", json={"formats": ["text", "json"], "language_mode": "en"}
    )
    assert exported.status_code == 200
    assert {r["format"] for r in exported.json()} == {"text", "json"}

    # 签署后不可再改（BR-11）
    locked = client.post(
        f"/api/reports/{report_id}/review/{first['key']}", json={"action": "reject"}
    )
    assert locked.status_code == 409

    listed = client.get("/api/reports").json()
    assert any(item["id"] == report_id for item in listed)

    audit = client.get("/api/audit", params={"session_id": "SES-API"}).json()
    assert {e["event_type"] for e in audit} >= {"report_created", "field_review", "report_signed"}


def test_error_contracts(client: TestClient) -> None:
    assert client.get("/api/reports/RPT-not-exist").status_code == 404
    assert client.post(
        "/api/reports/RPT-not-exist/sign", json={"signed_by": "x"}
    ).status_code == 404

    result = _generate(client)
    report_id = result["report"]["id"]
    bad = client.post(
        f"/api/reports/{report_id}/review/{result['report']['fields'][0]['key']}",
        json={"action": "edit", "value": "   "},
    )
    assert bad.status_code == 400

    unknown_template = client.post("/api/generate", json={
        "session_id": "SES-API", "template_id": "not_registered", "segments": SEGMENTS,
    }).json()
    assert unknown_template["report"]["template_id"] == "general_soap"
    assert any("回退" in w for w in unknown_template["report"]["warnings"])


def test_deidentify_option(client: TestClient) -> None:
    response = client.post("/api/generate", json={
        "session_id": "SES-DEID",
        "template_id": "general_soap",
        "segments": [{"id": "S-001", "start_ms": 0, "end_ms": 5000,
                      "text": "病人陳大文，身份證 A123456(7)，電話 9123 4567，話今日頭痛。"}],
        "options": {"deidentify": True, "term_correction": True},
    })
    assert response.status_code == 200
    body = response.json()
    assert body["deidentified"] is True
    assert "A123456" not in body["report"]["fields"][0]["ai_value"]
    audit = client.get("/api/audit", params={"session_id": "SES-DEID"}).json()
    assert any(e["event_type"] == "deidentify" for e in audit)
