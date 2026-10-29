"""HTTP 冒烟测试：真实拉起 run.py（uvicorn）或打包好的 exe，走完整接口链路后关闭。

    .venv\\Scripts\\python.exe tools\\http_smoke.py
    .venv\\Scripts\\python.exe tools\\http_smoke.py --exe dist\\medscribe-report-engine.exe

覆盖：健康检查、静态界面页、生成、校审、签署、导出、审计，
以及外科病理报告（結直腸）模板的整池匹配 + 诊断句自动拼装。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 8899
BASE = f"http://127.0.0.1:{PORT}"

SEGMENTS = [
    {"id": "S-001", "start_ms": 0, "end_ms": 9000, "lang": "mixed", "confidence": 0.93,
     "text": "陳先生今日返嚟覆診，佢話呢三日有喉嚨痛同咳，無發燒。"},
    {"id": "S-002", "start_ms": 9000, "end_ms": 17000, "lang": "en", "confidence": 0.91,
     "text": "On examination, blood pressure 128 over 76, pulse 82, temperature 36.8 degree Celsius."},
    {"id": "S-003", "start_ms": 17000, "end_ms": 23000, "lang": "en", "confidence": 0.88,
     "text": "No known drug allergy. Past history of hypertension."},
    {"id": "S-004", "start_ms": 23000, "end_ms": 29000, "lang": "en", "confidence": 0.85,
     "text": "Impression is upper respiratory tract infection. I will prescribe panadol 500 mg PO TDS for 3 days."},
    {"id": "S-005", "start_ms": 29000, "end_ms": 34000, "lang": "mixed", "confidence": 0.55,
     "text": "建議多休息多飲水，一星期後返嚟覆診。"},
]

log: list[str] = []


def call(method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        status = exc.code
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


def _pathology_flow() -> None:
    """外科病理报告：整池匹配 + 诊断句由模板拼装（医生未口述诊断原文时）。"""
    log.append("-- 外科病理报告（結直腸） --")
    status, sample = call("GET", "/api/sample?template_id=pathology_colorectal")
    assert status == 200, f"取合成语料失败 {status}"
    segments = sample["segments"]  # type: ignore[index]
    log.append(f"GET /api/sample?template_id=pathology_colorectal -> {status} 口述 {len(segments)} 段")

    status, result = call("POST", "/api/generate", {
        "session_id": "SES-HTTP-PATH",
        "template_id": "pathology_colorectal",
        "segments": segments,
        "options": {"deidentify": False, "term_correction": True, "use_llm": False},
    })
    assert status == 200, f"病理生成失败 {status} {result}"
    report = result["report"]  # type: ignore[index]
    fields = {f["key"]: f for f in report["fields"]}
    diagnosis = fields["diagnosis"]
    assert "derived" in diagnosis["flags"], "未口述诊断时应由模板拼装并标记"
    assert "moderately differentiated" in diagnosis["ai_value"]
    log.append(
        f"POST /api/generate(pathology) -> {status} fields={len(report['fields'])} "
        f"missing={report['missing_required']} 诊断={diagnosis['ai_value'][:60]}..."
    )

    for key, field in fields.items():
        if field["required"]:
            status, _ = call("POST", f"/api/reports/{report['id']}/review/{key}",
                             {"action": "accept", "reviewer": "dr-http"})
            assert status == 200, f"校审失败 {key}"
    status, signed = call("POST", f"/api/reports/{report['id']}/sign", {"signed_by": "dr-http"})
    assert signed["ok"], f"病理报告签署被阻断：{signed}"  # type: ignore[index]
    log.append(f"POST sign(pathology) -> {status} status={signed['report']['status']}")  # type: ignore[index]

    status, records = call("POST", f"/api/reports/{report['id']}/export",
                           {"formats": ["text", "json", "pdf"], "language_mode": "en"})
    assert status == 200, f"病理导出失败 {status} {records}"
    log.append(f"POST export(pathology, en) -> {status} {[r['format'] for r in records]}")  # type: ignore[index]

    status, preview = call("GET", f"/api/reports/{report['id']}/preview?format=text&language_mode=en")
    body = str(preview)
    assert "未提及" not in body, "英文模式导出不应出现中文占位符"
    assert "Surgical Pathology Report" in body and "[auto-generated]" in body
    log.append(f"GET preview(pathology, en) -> {status} 行数={len(body.splitlines())} 诊断句带自动生成标记")


def _engine_pids(image: str) -> set[int]:
    """当前系统里同名进程的 PID 集合（Windows；用于清理 onefile exe 遗留的子进程）。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    except OSError:
        return set()
    pids: set[int] = set()
    for line in out.splitlines():
        parts = [p.strip().strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == image.lower():
            try:
                pids.add(int(parts[1]))
            except ValueError:
                continue
    return pids


def main() -> int:
    # 默认用源码启动；传 "--exe dist\medscribe-report-engine.exe" 可改为验证打包产物
    argv = sys.argv[1:]
    if argv and argv[0] == "--exe":
        target = Path(argv[1])
        if not target.is_absolute():
            target = ROOT / target
        command = [str(target), "--port", str(PORT)]
        label = f"exe:{target.name}"
    else:
        command = [str(ROOT / ".venv" / "Scripts" / "python.exe"), str(ROOT / "run.py"), "--port", str(PORT)]
        label = "source:run.py"

    preexisting = _engine_pids(Path(command[0]).name) if command[0].endswith(".exe") else set()
    proc = subprocess.Popen(
        command,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    log.append(f"启动方式={label}")
    try:
        ready = False
        for _ in range(40):
            if proc.poll() is not None:
                break
            try:
                status, health = call("GET", "/health")
                if status == 200:
                    ready = True
                    break
            except Exception:  # noqa: BLE001 - 启动期连接失败属预期
                time.sleep(0.5)
        assert ready, "服务未在 20 秒内就绪"
        log.append(f"GET /health -> {status} templates={health['templates']} terms={health['terminology_entries']}")  # type: ignore[index]

        status, page = call("GET", "/")
        log.append(f"GET / -> {status} 内置界面={'MedScribe HK' in str(page)} 长度={len(str(page))}")

        status, result = call("POST", "/api/generate", {
            "session_id": "SES-HTTP",
            "template_id": "general_soap",
            "segments": SEGMENTS,
            "options": {"deidentify": False, "term_correction": True, "use_llm": False},
        })
        assert status == 200, f"生成失败 {status} {result}"
        report = result["report"]  # type: ignore[index]
        log.append(f"POST /api/generate -> {status} report={report['id']} fields={len(report['fields'])} missing={report['missing_required']}")

        # 未签署先导出 -> 409
        status, blocked = call("POST", f"/api/reports/{report['id']}/export", {"formats": ["text"]})
        log.append(f"POST export(未签署) -> {status} {blocked}")

        for field in report["fields"]:
            if field["required"]:
                status, _ = call("POST", f"/api/reports/{report['id']}/review/{field['key']}",
                                 {"action": "accept", "reviewer": "dr-http"})
                assert status == 200, f"校审失败 {field['key']}"
        log.append(f"POST review x{sum(1 for f in report['fields'] if f['required'])} -> 200")

        status, signed = call("POST", f"/api/reports/{report['id']}/sign", {"signed_by": "dr-http"})
        log.append(f"POST sign -> {status} ok={signed['ok']} status={signed['report']['status']}")  # type: ignore[index]

        status, records = call("POST", f"/api/reports/{report['id']}/export",
                               {"formats": ["text", "json", "pdf"], "language_mode": "bilingual"})
        log.append(f"POST export(已签署) -> {status} {[r['format'] for r in records]}")  # type: ignore[index]

        status, preview = call("GET", f"/api/reports/{report['id']}/preview?format=text")
        log.append(f"GET preview -> {status} 首行={str(preview).splitlines()[0] if preview else ''}")

        status, audit = call("GET", "/api/audit?session_id=SES-HTTP")
        log.append(f"GET /api/audit -> {status} 事件={[e['event_type'] for e in audit]}")  # type: ignore[index]

        _pathology_flow()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        # exe 是 PyInstaller onefile：父进程退出后监听端口的子进程可能仍存活，
        # 只清理本次启动新增的同名进程（不动用户自己开着的实例）
        for pid in sorted(_engine_pids(Path(command[0]).name) - preexisting):
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )

    (ROOT / "docs" / "demo-output").mkdir(parents=True, exist_ok=True)
    (ROOT / "docs" / "demo-output" / "http_smoke.txt").write_text("\n".join(log), encoding="utf-8")
    print("\n".join(log))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
