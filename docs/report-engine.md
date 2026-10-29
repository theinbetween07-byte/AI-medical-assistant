# MedScribe HK · 报告生成引擎（report-engine）

把「带时间戳的多语言临床口述转写」变成「结构化病历报告初稿」的独立模块。
内核完全离线、确定性（规则 + 声明式模板），外部 LLM 为可选增强且默认关闭。

- 单一入口：转写文本进 → 要素抽取 → 模板化报告初稿出（门诊 SOAP / 外科病理等）
- 独立 exe：双击即用，自带网页界面；也可被现有网页 fetch 调用，或被其他程序当子进程拉起
- 全程可追溯：每个字段都带来源音频片段与时间点（NFR-12）
- 安全门禁：必填未确认禁止签署、未签署禁止导出、导出后只读（BR-10 / BR-11）

## 快速开始

### 1) 直接用打包好的 exe（交付形态）

> 以下命令均在**仓库根目录**执行；exe 位于 `report-engine\dist\`。

```bat
report-engine\dist\medscribe-report-engine.exe                 :: 启动服务，默认 http://127.0.0.1:8790
report-engine\dist\medscribe-report-engine.exe --open          :: 启动并自动打开界面
report-engine\dist\medscribe-report-engine.exe --port 9000     :: 指定端口
report-engine\dist\medscribe-report-engine.exe --port 0 --port-file port.txt   :: 自动挑空闲端口并写文件，供宿主程序读取
```

浏览器打开 `http://127.0.0.1:8790/` 即为自带界面：左侧输入转写、右侧字段级校审，可一键导出 PDF/JSON/TXT。

### 2) 从源码运行（开发）

```bat
cd report-engine
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe run.py --open
```

### 3) 重新打包 exe

```bat
cd report-engine
.venv\Scripts\pyinstaller.exe --clean --noconfirm build\report_engine.spec
:: 产物：dist\medscribe-report-engine.exe
```

## 目录结构

```
report-engine/            引擎工程（Python 源码与打包配置）
├─ app/
│  ├─ main.py           HTTP 接口（FastAPI）
│  ├─ service.py        业务门面：校审/签署/导出门禁
│  ├─ pipeline.py       端到端编排
│  ├─ extractor.py      SOAP 要素抽取（规则内核）
│  ├─ terminology.py    术语纠正（精确 + 模糊）
│  ├─ deidentify.py     去标识化（HKID/电话/姓名…）
│  ├─ generator.py      要素 -> 模板字段填充
│  ├─ validation.py     缺失/来源追溯/剂量一致性校验
│  ├─ exporter.py       导出 text / json / pdf / html
│  ├─ store.py          SQLite 存档与审计
│  ├─ templates.py      声明式模板注册表
│  ├─ schemas.py        全部数据契约
│  └─ static/index.html 内置单页界面
├─ templates/*.json     报告模板（可热改，不必改代码）
│  ├─ general_soap.json        通用 SOAP（兜底模板，BR-06）
│  ├─ outpatient_record.json   门诊病历记录
│  └─ pathology_colorectal.json 外科病理報告（結直腸）
├─ tests/               22 项自动化测试
└─ build/report_engine.spec

data/terms/*.json       术语库与抽取规则（可热改，引擎自动加载）
scripts/                冒烟脚本（命令行 / HTTP 全链路）
docs/                   设计说明、接入交付说明、本文件
outputs/                交付示例报告（病理 / SOAP）
```

## 接口一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 内置网页界面 |
| GET | `/health` | 健康检查 + 运行配置（模板数、术语条数、LLM 状态） |
| GET | `/docs` | 自动生成的 OpenAPI 文档 |
| GET | `/api/templates` | 模板与字段定义 |
| POST | `/api/reload` | 重载模板/术语库（免重启） |
| GET | `/api/sample` | 合成模拟口述（`[SYNTHETIC]`）；`?template_id=pathology_colorectal` 取该报告类型的语料 |
| POST | `/api/generate` | **主入口**：转写 → 报告初稿（自动存档） |
| GET | `/api/reports` | 历史报告列表 |
| GET | `/api/reports/{id}` | 读取报告 |
| DELETE | `/api/reports/{id}` | 删除报告 |
| POST | `/api/reports/{id}/review/{field}` | 字段级校审：`accept` / `edit` / `reject` |
| POST | `/api/reports/{id}/sign` | 签署（必填未确认则返回 `ok=false` + `blocked_by`） |
| POST | `/api/reports/{id}/export` | 导出（未签署返回 409） |
| GET | `/api/reports/{id}/preview` | 导出前预览（text/json/html） |
| GET | `/api/audit` | 审计事件 |

错误码：`400` 非法操作（编辑空值等）、`404` 报告不存在、`409` 报告已锁定/未签署。

## 配置（环境变量）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MSHK_HOST` / `MSHK_PORT` | `127.0.0.1` / `8790` | 监听地址；仅监听本机，不对外暴露（NFR-04） |
| `MSHK_DATA_DIR` | `exe 同目录/data` | SQLite 与导出的存放位置 |
| `MSHK_TEMPLATES_DIR` / `MSHK_TERMS_DIR` | `exe 同目录/templates`、`data/terms` | 外部资源目录，优先级高于内置资源 |
| `MSHK_CORS_ORIGINS` | `*` | 允许跨源调用的前端来源，逗号分隔 |
| `MSHK_LLM_ENABLED` | `false` | 是否启用 LLM 增强 |
| `MSHK_LLM_ALLOW_EXTERNAL` | `false` | 明确允许把内容送出本机 |
| `MSHK_LLM_BASE_URL` / `MSHK_LLM_MODEL` / `MSHK_LLM_API_KEY` | 空 | OpenAI 兼容端点配置 |
| `MSHK_LLM_MAX_CALLS_PER_REPORT` | `2` | 单份报告 LLM 调用上限（NFR-11） |

## 模板扩展（不改代码）

把 JSON 放进 `exe 同目录/templates/` 即生效（或调用 `POST /api/reload` 热加载）：

```json
{
  "id": "outpatient_record",
  "name": "门诊病历记录 / Outpatient Record",
  "version": "1.0.0",
  "fields": [
    {"key": "chief_complaint", "label": "主诉 / Chief Complaint", "section": "S",
     "required": true, "order": 1, "keywords": ["主诉", "complain", "病人講"]}
  ]
}
```

字段级属性：

| 属性 | 默认 | 说明 |
| --- | --- | --- |
| `key` / `label` / `order` | — | 字段标识 / 显示名（`中文 / English` 结构支持 en/zh 取舍）/ 报告行序 |
| `section` | `uncategorized` | `S` / `O` / `A` / `P` / `uncategorized`（默认按分区池化匹配） |
| `required` | `false` | `true` 时未确认会阻断签署与导出（BR-10） |
| `keywords` | `[]` | 选材关键词，越长越具体；同时用作「`标签: 值`」直配的前缀 |
| `scope_headings` | `[]` | 章节标题词：口述出现该标题后，后续句子默认归入本字段（标题句本身不入值） |
| `accepts_fallback` | `true` | 关键词全未命中时是否允许按模板顺序兜底领取一条内容；结构化字段建议 `false`（宁可「未提及」，BR-03） |
| `compose` | `null` | 派生字段：本字段缺内容时按 `pattern` 用其他字段的已抽取值拼一句话（如病理 `DIAGNOSIS`），只连接不推断，结果带 `derived` 标记待医生核对；`required_keys` 缺项则放弃生成 |
| `placeholder_missing` | `未提及` | 未被口述覆盖时的占位符 |

模板级属性：

| 属性 | 默认 | 说明 |
| --- | --- | --- |
| `pool_all_sections` | `false` | `true` 时不按 S/O/A/P 分区池化（章节由标题划分的报告类型，如病理），且强制保留未分类句子 |
| `field_separator` | `；` | 同字段多句拼接符（叙述段落用 `" "` 更接近报告原文） |

字段选材优先级（从强到弱）：**章节标题 → `标签: 值` 直配（取最长标签，值去掉标签）→ 作用域内归属 → 关键词最强匹配 → 受托兜底**；
每个要素最多归属一个字段，保证来源引用不重复。未注册的 `template_id` 自动回退通用 SOAP 模板并写入警告（BR-06）。

## 测试与冒烟

```bat
:: 单元测试在引擎目录内执行
cd report-engine && .venv\Scripts\python.exe -m pytest -q
cd ..

:: 冒烟脚本在仓库根目录执行
report-engine\.venv\Scripts\python.exe scripts\smoke_demo.py                                  :: SOAP 全链路，产物在 report-engine\docs\demo-output
report-engine\.venv\Scripts\python.exe scripts\smoke_demo.py out --template pathology_colorectal  :: 病理报告全链路
report-engine\.venv\Scripts\python.exe scripts\http_smoke.py                  :: 真实拉起服务跑 HTTP 全链路（含病理报告 + 诊断句拼装）
report-engine\.venv\Scripts\python.exe scripts\http_smoke.py --exe report-engine\dist\medscribe-report-engine.exe   :: 验证打包产物
```

## 已知限制

- 抽取规则与术语库基于少量粤英混合模拟语料调参，真实语料需再校准（阈值见 `data/terms/extraction_rules.json`）。
- 服务仅监听本机、无鉴权，定位是桌面端/内网单机工具；如需多用户需在接入层补认证。
- LLM 增强为可选路径，关闭时全部结果由规则内核产生，可完整复现。
- 导出的报告「提示/Notes」告警文案暂只有中文；英文模式下表头、占位符、字段标签与标记已本地化。
- 结构化字段的取值依赖口述/打字出现「标签: 值」形式；同一句话里说多个字段仍只归一个字段（一句一要素）。
- 派生字段（`compose`）只连接已有字段值，不做语义改写；如需更贴近人工撰写的诊断句，可改模板 `pattern`。
