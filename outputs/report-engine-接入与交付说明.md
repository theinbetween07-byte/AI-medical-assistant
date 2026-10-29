# MedScribe HK · 报告生成引擎 —— 接入与交付说明

> 交付形态：**独立单文件 exe + 本地 HTTP 服务**。既可单机使用（双击即用、自带界面），也可被现有网页直接调用，或被其他程序当子进程拉起。
> 内核完全离线、结果可复现；不依赖任何云端服务即可完成「转写 → 结构化报告初稿」。

---

## 一、交付内容

| 交付物 | 路径 | 说明 |
| --- | --- | --- |
| **可执行程序** | `report-engine\dist\medscribe-report-engine.exe` | Windows 单文件 exe，23.6 MB，含全部依赖与内置模板/术语/界面（3 个模板：通用 SOAP、门诊记录、外科病理报告） |
| 源码工程 | `report-engine\` | Python 源码、模板、术语库、测试、打包配置 |
| 接入文档 | 本文件 | 接口契约、三种集成方式、验收对照 |
| 病理模板设计说明 | `outputs\病理报告模板设计说明.md` | 目标报告格式 → 20 个字段的映射、取值规则、与样本的复现对照 |
| 示例报告 | `outputs\示例报告\` | 两种报告类型各一份已签署报告的四格式产物 + 全链路日志 |
| 构建产物清单 | `report-engine\dist\` | 每次重新打包的 exe 输出位置 |

**首次获取后即可使用，无需安装 Python、无需联网。**

---

## 二、五分钟验收（推荐先做这一步）

1. 双击 `medscribe-report-engine.exe`（或命令行 `medscribe-report-engine.exe --open`）；
2. 浏览器自动打开 `http://127.0.0.1:8790/` —— 这是自带界面；
3. 顶部选择报告类型（**外科病理報告（結直腸）** 或 通用 SOAP / 门诊病历记录），点「载入合成示例」，再点「生成报告初稿」；
4. 在右侧逐字段「采纳 / 编辑 / 拒绝」，未提及的必填字段会显红并提示；
5. 点「签署」→ 点「导出 PDF / JSON / TXT」，文件落在 exe 同目录 `data\exports\`。

对照示例产物核对期望输出样式：

| 报告类型 | 参照文件 |
| --- | --- |
| 外科病理报告（結直腸） | `outputs\示例报告\pathology-colorectal\RPT-*.pdf` / `.txt` |
| 通用 SOAP | `outputs\示例报告\general-soap\RPT-*.pdf` / `.txt` |

---

## 三、三种集成方式

### 方式 A：独立 exe（单机自用）

```bat
medscribe-report-engine.exe --open
```

自带网页界面完成全流程，适合医生端单机试点、演示与验收。

### 方式 B：现有网页 / 前端调用（推荐给「整合进网页」的场景）

exe 启动后是一个标准 HTTP 服务，网页侧只需 `fetch`。服务默认允许跨源（`MSHK_CORS_ORIGINS=*`），上线前可收敛到指定域名。

```html
<script>
// 1) 转写结果（可直接用上游 ASR 输出：只需带 start/end 时间戳与 text）
const segments = [
  { id: "S-001", start_ms: 0,     end_ms: 9000,  text: "陳先生今日返嚟覆診，佢話呢三日有喉嚨痛同咳，無發燒。" },
  { id: "S-002", start_ms: 9000,  end_ms: 17000, text: "On examination, blood pressure 128 over 76." },
  { id: "S-003", start_ms: 17000, end_ms: 23000, text: "No known drug allergy. Past history of hypertension." }
];

// 2) 生成报告初稿
const res = await fetch("http://127.0.0.1:8790/api/generate", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    session_id: "SES-2026-001",
    template_id: "outpatient_record",          // 未注册则自动回退通用 SOAP 并在 warnings 中说明
    segments,
    options: { deidentify: true, term_correction: true, use_llm: false }
  })
});
const data = await res.json();                  // data.report.fields 即字段级初稿（含来源时间点）

// 3) 医生逐字段校审
await fetch(`http://127.0.0.1:8790/api/reports/${data.report.id}/review/chief_complaint`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ action: "edit", value: "医生修订后的主诉", reviewer: "dr-chan" })
});

// 4) 签署（必填未确认时返回 ok=false + blocked_by 清单，不会抛错）
const signed = await (await fetch(`http://127.0.0.1:8790/api/reports/${data.report.id}/sign`, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ signed_by: "dr-chan" })
})).json();

// 5) 导出（未签署返回 409，前端据此提示"请先完成校审与签署"）
const exports = await fetch(`http://127.0.0.1:8790/api/reports/${data.report.id}/export`, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ formats: ["pdf", "json", "text"], language_mode: "bilingual" })
});
</script>
```

### 方式 C：宿主程序以子进程拉起（推荐给「整合进其他 exe」的场景）

```bat
:: 自动挑空闲端口，并把端口号写入文件；宿主读取该文件后再发请求，避免端口冲突
medscribe-report-engine.exe --port 0 --port-file "%TEMP%\mshk_port.txt"
:: 读取 %TEMP%\mshk_port.txt -> 例如 56808 -> http://127.0.0.1:56808/health 就绪后开始调用
```

- 就绪探测：轮询 `GET /health`，返回 200 即代表模板/术语/数据库均已加载；
- 退出方式：宿主结束子进程即可（无外部状态残留，数据在 `data\mshk.db`）；
- 本路径已实测通过（`--port 0 --port-file` 写出端口 56808，`/health` 返回 200）。

---

## 四、接口契约

完整交互式文档：服务启动后访问 `http://127.0.0.1:8790/docs`（自动生成的 OpenAPI）。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 就绪检查：模板列表、术语条数、LLM 开关状态 |
| GET | `/api/templates` | 模板与字段定义（供前端动态渲染表单） |
| POST | `/api/reload` | 热重载模板/术语库（换模板不必重启、不必重打包） |
| GET | `/api/sample` | 合成模拟口述（仅供演示/自测，内容标注 `[SYNTHETIC]`）；`?template_id=pathology_colorectal` 取病理报告语料 |
| **POST** | **`/api/generate`** | **主入口**：转写 → 报告初稿（自动存档 + 审计） |
| GET | `/api/reports` | 历史报告列表 |
| GET | `/api/reports/{id}` | 读取单份报告 |
| DELETE | `/api/reports/{id}` | 删除报告（审计留痕） |
| POST | `/api/reports/{id}/review/{field_key}` | 字段级校审：`accept` / `edit` / `reject` |
| POST | `/api/reports/{id}/sign` | 签署 |
| POST | `/api/reports/{id}/export` | 导出 `pdf` / `json` / `text` / `html` |
| GET | `/api/reports/{id}/preview` | 导出前预览（不落盘、不受签署门禁限制） |
| GET | `/api/audit` | 审计事件（可按 `session_id` 过滤） |

### 主入口 `/api/generate` 返回结构（实测）

```json
{
  "session_id": "SES-DOC",
  "pipeline": ["deidentify(skipped)", "term_correction(0)", "extract(10, unclassified=0)", "validate(missing_required=1)"],
  "deidentified": false,
  "corrections": [],
  "elements": [ { "id": "E-001", "section": "S", "text": "病人講喉嚨痛，痛咗三日。", "score": 4.5,
                  "sources": [{ "segment_id": "S-001", "start_ms": 6157, "end_ms": 9000 }] } ],
  "report": {
    "id": "RPT-xxxxxxxxxxxx", "session_id": "SES-DOC",
    "template_id": "outpatient_record", "template_name": "门诊病历记录 / Outpatient Record", "template_version": "1.0.0",
    "status": "draft", "created_at": "2026-10-20T03:20:00+00:00", "signed_at": null, "signed_by": null,
    "fields": [
      {
        "key": "chief_complaint", "label": "主诉 / Chief Complaint", "section": "S",
        "required": true, "order": 1, "type": "text",
        "ai_value": "病人講喉嚨痛，痛咗三日。",     // AI 初稿，签署后仍保留（不可变）
        "final_value": "",                        // 校审后终值；导出取此值，为空时回落 ai_value
        "sources": [ { "segment_id": "S-001", "start_ms": 6157, "end_ms": 9000,
                       "text": "病人講喉嚨痛，痛咗三日。" } ],
        "flags": [],                              // 如 low_confidence / unverified / missing
        "evidence_ok": true,                      // 来源可追溯校验
        "note": ""
      }
    ],
    "missing_required": ["physical_examination"],
    "warnings": ["存在未提及的必填字段：physical_examination，签署与导出已被阻断（BR-10）"],
    "mode": "rules",
    "reviews": {}
  },
  "audit": [ { "event_type": "report_created", "created_at": "2026-10-20T03:20:00+00:00" } ]
}
```

### 错误约定

| 状态码 | 触发场景 | 前端建议处理 |
| --- | --- | --- |
| `400` | 非法操作（如编辑时提交空值） | 提示补全输入 |
| `404` | 报告 ID 不存在 | 回到列表重新选择 |
| `409` | 报告已签署被锁定（BR-11）；或未签署就导出（BR-10） | 提示"请先完成校审与签署"/"报告已锁定，如需修改请新建" |

`409` 响应示例（实测原文）：

```json
{ "detail": "报告尚未签署或仍有未确认的必填字段，禁止导出（BR-10）。请先完成校审与签署。" }
```

> 签署接口对"必填未确认"**不返回错误码**，而是 `{ "ok": false, "blocked_by": ["physical_examination"] }`，方便前端直接高亮待确认字段。

---

## 五、模板与术语热扩展（不改代码、不重打包）

把 JSON 放到 **exe 同目录**的 `templates\`、`terms\` 下即可（同目录同名目录非空时优先于 exe 内置资源），随后 `POST /api/reload` 即时生效。

```json
{
  "id": "pathology_colorectal",
  "name": "Surgical Pathology Report (Colorectal)",
  "version": "1.0.0",
  "pool_all_sections": true,
  "field_separator": " ",
  "fields": [
    { "key": "tumour_site", "label": "Tumour site", "section": "O", "required": true, "order": 2,
      "accepts_fallback": false, "keywords": ["tumour site", "tumor site"] },
    { "key": "macroscopic_examination", "label": "MACROSCOPIC EXAMINATION", "section": "O", "order": 18,
      "scope_headings": ["macroscopic examination", "gross description"],
      "keywords": ["specimen consists", "macroscopic"] }
  ]
}
```

- `section`：`S` / `O` / `A` / `P` / `uncategorized`；`order` 决定报告字段顺序；
- `keywords`：既用于"哪句转写进哪个字段"（越长越具体），也用于识别「`标签: 值`」行并自动去掉标签；
- `scope_headings`：章节标题词，标题出现后其后的句子（如整段宏观描写）默认归入该字段，标题句本身不入值；
- `accepts_fallback`：默认 `true`；结构化字段建议设 `false` —— 关键词全未命中时不领取无关内容，如实写「未提及」并阻断签署（BR-03）；
- `compose`：派生字段（如病理 `DIAGNOSIS`）——本字段没被口述覆盖时，按 `pattern` 用其他字段的**已抽取值**拼一句话，
  只连接不推断，结果带「自动生成，请核对」标记与来源引用；`required_keys` 缺项则放弃生成、继续阻断签署（BR-03）；
- `required: true` 的字段未确认则阻断签署与导出；
- `pool_all_sections: true`：不按 S/O/A/P 分区池化（章节由标题划分的报告类型，如病理）；
- **字段清单、必填项、识别关键词都在这一份 JSON 里，调整报告格式无需改任何代码**（改完 `POST /api/reload` 即时生效）。

> 目标格式的完整字段映射见 `outputs\病理报告模板设计说明.md`（样本 16 项 synoptic + 3 段叙述 + 诊断 = 20 字段）。

---

## 六、隐私与合规默认值

| 项 | 默认行为 | 对应要求 |
| --- | --- | --- |
| 网络暴露 | 仅监听 `127.0.0.1`，不对外提供 | NFR-04 数据不出本机 |
| LLM | `MSHK_LLM_ENABLED=false`，全部结果由本地规则内核产生 | NFR-04 / NFR-06 可复现 |
| 去标识化 | 生成时可开启，HKID/电话/姓名等在进入抽取前替换 | 隐私保护 |
| 数据落盘 | 本地 SQLite `data\mshk.db` + `data\exports\`，无云端同步 | 数据本地化 |
| 审计 | 生成/去标识化/校审/签署/导出全程留痕，可按会话追溯 | NFR-12 可追溯 |
| 无编造 | 未口述字段一律写「未提及」，不生成推测内容 | BR-03 / AI-03 |
| 来源引用 | 每个字段携带来源片段 ID 与起止毫秒 | FR-04 |

---

## 七、验收对照表（需求 → 实现 → 证据）

| 需求条款 | 实现位置 | 自动化证据 |
| --- | --- | --- |
| F3 / AI-03 字段级结构化初稿、未提及写「未提及」 | `app/generator.py` | `tests/test_pipeline.py::test_no_fabrication_marks_missing` |
| FR-04 每个字段带来源引用与时间点 | `app/generator.py`、`app/schemas.py` | `test_extraction_keeps_source_span`、示例报告 JSON 的 `sources` |
| FR-05 / FR-06 必填缺失清单、剂量冲突标「待核实」 | `app/validation.py` | `test_missing_required_blocks_sign_until_confirmed` |
| FR-07 / AC-07 AI 原值不可变，保留编辑前后 | `app/service.py`（`ai_value` / `final_value`） | `test_api.py::test_generate_review_sign_export_flow` |
| FR-08 医生签署 | `service.sign`、`POST /sign` | 同上（`status=signed`、`signed_by`） |
| FR-09 导出 text / PDF / JSON | `app/exporter.py` | `exports=4`；`outputs\示例报告\` 四格式产物 |
| BR-03 无编造 | 规则内核 + `missing` 标记 | `test_no_fabrication_marks_missing` |
| BR-05 冲突标「待核实」 | `validation.py` | 同上 |
| BR-06 未注册模板回退通用 SOAP | `app/templates.py` | `test_unknown_template_falls_back`、`test_api.py::test_error_contracts` |
| BR-09 低置信度片段提示核对 | `extractor.py` + `flags` | `test_low_confidence_flagged`（示例报告"处理计划"带`[低置信度，请核对]`） |
| BR-10 必填未确认禁止签署/导出 | `service.sign/export` | 409 实测 + `test_missing_required_blocks_sign_until_confirmed` |
| BR-11 导出后锁定 | `service._require` | `test_api.py`（签署后校审返回 409） |
| NFR-04 离线可用 | 规则内核 + LLM 默认关闭 | `/health` 返回 `llm.enabled=false` |
| NFR-11 LLM 调用上限 | `app/llm.py` | `MSHK_LLM_MAX_CALLS_PER_REPORT=2` |
| NFR-12 全链路可追溯 | `app/store.py` 审计表 | `GET /api/audit` 实测事件序列 |
| **新报告类型：外科病理报告（結直腸）** | `templates/pathology_colorectal.json` + `app/generator.py`（标签直配 / 标题作用域） | `tests/test_pathology.py` 8 项；`outputs\示例报告\pathology-colorectal\` |

---

## 八、验证记录（2026-10-20，本机实测）

| 项目 | 命令 | 结果 |
| --- | --- | --- |
| 自动化测试 | `.venv\Scripts\python.exe -m pytest -q` | **22 passed** |
| 命令行全链路（SOAP） | `.venv\Scripts\python.exe tools\smoke_demo.py` | `signed=True exports=4`，字段归位正确 |
| 命令行全链路（病理） | `tools\smoke_demo.py out --template pathology_colorectal` | 27 句 → 20/20 字段归位，诊断句自动拼装，`missing_required=0`，`signed=True exports=4` |
| HTTP 全链路（源码） | `.venv\Scripts\python.exe tools\http_smoke.py` | SOAP + 病理两套流程全部通过（含 409 门禁、英文导出、诊断句标记） |
| HTTP 全链路（exe，重建后） | `tools\http_smoke.py --exe dist\medscribe-report-engine.exe` | 与源码行为完全一致；`/health` 返回 3 个模板含 `pathology_colorectal` |
| 端口协商 | `exe --port 0 --port-file` | 写出端口 56808，`/health` 返回 200 |
| 导出字体 | 检查 `RPT-*.pdf` | 内嵌 `STSong-Light`，中文正常渲染 |
| 病理导出格式核对 | `outputs\示例报告\pathology-colorectal\RPT-*.txt` / `.html` | 17 行 synoptic「`标签: 值`」+ 宏观段 + 镜下段 + 自动生成诊断段，与样本结构一致；HTML/PDF 带 `auto-generated` 标记 |

exe 重建记录：`medscribe-report-engine.exe` 23,599,845 字节（2026-10-20 12:10），内含 3 个模板与最新界面。

---

## 九、待办与已知限制

1. **目标报告格式已落地（原阻塞项已解除）**：`SampleReport.zip` 的 10 份外科病理报告已转为声明式模板 `templates\pathology_colorectal.json`（20 字段 / 17 必填），并已用合成口述复现字段归位（见 `outputs\病理报告模板设计说明.md`）。
   **已确认口径**：① 保留三个模板（病理为主类型）；② 病理科输入为**医生口述转写**（粘贴成稿文本同样可用）；③ `DIAGNOSIS` 段**由引擎自动生成草稿**并标记「自动生成，请核对」，医生确认后才可签署。
2. 抽取规则阈值目前在粤英混合模拟语料上调参，建议用真实口述样音再校准一轮（阈值见 `terms\extraction_rules.json`，可热改）；病理科口述建议一并提供 2–3 段真实样音以校准英文标签识别。
3. 服务无鉴权、仅监听本机，定位为桌面端/内网单机工具；若需多人共享，请在接入层补认证与权限。
4. 仅提供 Windows 单文件 exe；如需 macOS/Linux，需在对应平台重新执行打包命令（源码已跨平台）。
5. 导出的「提示/Notes」告警文案暂只有中文（英文模式下表头、占位符、字段标签与标记均已本地化）。
6. 自动生成的诊断句是「按结构化字段连接」的草稿，不做语义改写；若期望更贴近人工撰写句式，调整模板 `compose.pattern` 即可（无需改代码）。
