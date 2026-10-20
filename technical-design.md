# 简要技术设计：MedScribe HK

> **阶段**：Brief Technical Design（阶段 3）
> **日期**：2026-10-15 · **状态**：初始设计，将于第一个原型后细化
> **上游文档**：《项目提案》《MVP 产品需求文档》
> **建议篇幅**：2–3 页（不含附录与追溯表）
> **诚实性说明**：本文档描述**尚未实现**的设计。所有运行命令均为**拟定命令（unverified）**，成本数据为**未测量**，架构选型待 W1–W2 spike 结果确认。凡未执行的内容均已标注状态，不作已完成陈述。

---

## 1. 系统结构

### 1.1 组件图

```
┌──────────────────────────────────────────────────────────────────┐
│                浏览器（医生工作站，本地 localhost）                │
│  S1 会话  |  S2 录音  |  S3 转写核对  |  S4/S5 初稿与校审  |  E 导出 │
└───────────────────────────┬──────────────────────────────────────┘
                            │ HTTP (JSON)
┌───────────────────────────▼──────────────────────────────────────┐
│                     后端服务（Python 单体应用）                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ │
│  │ 音频管理  │ │ ASR 适配层│ │ 术语纠正  │ │ 要素抽取  │ │ 报告生成 │ │
│  │ (FR-01)  │ │ (FR-02)  │ │ (FR-03)  │ │ (FR-04)  │ │(FR-05/06)│ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └─────────┘ │
│  ┌───────────────────────┐ ┌──────────────┐ ┌──────────────────┐ │
│  │ 校审与签署 (FR-07/08)  │ │ 导出 (FR-09)  │ │ 存档与审计(FR-10)│ │
│  └───────────────────────┘ └──────────────┘ └──────────────────┘ │
└───────┬───────────────────────┬──────────────────────┬───────────┘
        │                       │                      │
   ┌────▼─────┐          ┌──────▼───────┐        ┌─────▼──────┐
   │ 本地文件  │          │  SQLite      │        │ 外部服务   │
   │ 音频/导出 │          │  会话与结构化 │        │ ASR / LLM  │
   │ (原始数据)│          │  数据        │        │ (可选, 可控)│
   └──────────┘          └──────────────┘        └────────────┘
                                    ▲
                       ┌────────────┴────────────┐
                       │ 术语库 / 模板文件（版本化）│
                       └─────────────────────────┘
```

### 1.2 组件职责

| 组件 | 职责 | 支撑需求 | 为什么需要 | 更简单的做法？ | 失败时会怎样 |
| --- | --- | --- | --- | --- | --- |
| 音频管理 | 录音上传、时长与静音校验、文件命名 | FR-01, BR-01/02 | 一切数据流的起点 | 直接由前端存浏览器，但无法持久化 | 提示重录，不进入后续流程 |
| ASR 适配层 | 统一本地模型与云 API 的调用接口、输出分段与置信度 | FR-02 | 需在成本/隐私/精度间切换方案 | 只接一个引擎，但会失去降级路径 | 提示重试；低置信度片段标黄，人工核对 |
| 术语纠正 | 词表匹配与替换、保留可撤销记录 | FR-03, BR-04 | 混合语种下 ASR 对药名/病名最不可靠 | 可由医生手改，但耗时且不可复现 | 保留原始文本，仅记录未纠正 |
| 要素抽取 | 转写文本 → S/O/A/P + 来源引用 | FR-04 | 结构化是后续模板生成的前提 | 正则规则可覆盖部分模式，但覆盖不足 | 保留转写文本，允许重试 |
| 报告生成 | 模板 + 要素 → 字段化初稿 | FR-05, FR-06 | 用户最终交付物 | 直接拼字符串，但无法适配模板与校验 | 校验不通过则不输出，报告保持草稿态 |
| 校审与签署 | 字段级采纳/编辑/拒绝、签署门禁 | FR-07, FR-08 | 临床安全的必要环节 | 只有整体"确认"按钮，但无法追溯 | 未确认字段禁止签署（BR-10） |
| 导出 | 文本 / PDF / JSON | FR-09 | 与 EHR 的替代集成方式 | 只导出文本，但会丢失字段追溯信息 | 导出失败不影响已保存数据 |
| 存档与审计 | 会话历史、删除、事件日志 | FR-10, BR-12 | 可复现与合规追溯 | 无存档，但无法回归测试 | 只读历史不可用时不影响新会话 |

---

## 2. 技术选择

| 层 | 选择 | 理由 | 更简单的替代方案 |
| --- | --- | --- | --- |
| 前端 | React + Vite + TypeScript | 校审界面交互状态多（字段级编辑、差异显示），组件化便于维护 | 单页原生 HTML + 少量 JS，或 Streamlit；若进度紧张可退化为 Streamlit 原型 |
| 后端 | Python + FastAPI | 与 ASR/LLM 生态同为 Python；自动生成接口文档便于团队协作 | 纯脚本 + 命令行，但无法支持界面对话 |
| ASR | **本地 faster-whisper 为主，云 ASR API 为可选增强** | 本地运行满足隐私约束（NFR-04），成本可控；云 API 用于精度对比 | 仅用云 API：精度可能更高，但违反"数据不出本机"的默认假设 |
| 语言模型 | 通用 LLM API（结构化 JSON 输出）+ 提示词约束 | 团队精力应放在术语、模板与校审闭环，而非模型训练 | 本地小模型：隐私最好，但中文医学结构化质量需实测，可能不达标 |
| 输出约束 | JSON Schema + 后端校验（pydantic） | 防止 LLM 返回自由文本导致字段缺失（BR-03、AI-03 失败行为） | 纯提示词要求"只输出 JSON"，但无法拦截违规输出 |
| 存储 | SQLite（结构化）+ 本地文件系统（音频、导出） | 单机单用户场景足够，零运维 | 纯 JSON 文件：简单但并发与查询能力弱 |
| 术语库 | 版本化 JSON/CSV 文件（可导入 SQLite 加速） | 便于人工审核与 diff，符合 NFR-10 | 直接写进代码常量，但不可维护与回溯 |
| 评测 | Python + 编辑距离库（如 `jiwer`）计算 CER/WER | 与学术定义一致，结果可复现 | 手工抽样比对，但无法覆盖 1 小时语料 |

**关键取舍说明**

1. **准实时而非流式**：录音结束后统一处理，避免在音频不完整时反复触发抽取与生成（《MVP 产品需求文档》第 4.3 节）。
2. **不做微调**：MVP 阶段用提示工程 + 术语库 + 模板约束达到可验证效果；微调需要数据规模，列入长期计划（《MVP 产品需求文档》第 3 节）。
3. **模板用声明式定义**：模板以 JSON 描述字段（key/label/required/order），新增报告类型只需新增文件，不改代码。

> **选型风险**：ASR 与 LLM 两项选择均为**待验证**，最终以 W1–W2 spike 的分语种实测指标为准；若本地 ASR 不达标，按《7 周执行计划》的范围收缩预案处理。

---

## 3. 数据模型

### 3.1 主要实体

| 实体 | 关键字段 | 关系 |
| --- | --- | --- |
| `Session` | id, created_at, status, audio_path, duration_ms, deleted_at | 1 : N `TranscriptSegment`、`ClinicalElement`、`GeneratedReport` |
| `TranscriptSegment` | id, session_id, start_ms, end_ms, text_raw, text_corrected, lang_tag, confidence | N : 1 `Session`；被 `ClinicalElement.source_segment_ids` 引用 |
| `TermEntry` | id, term_norm, aliases[], category(drug/disease/exam/abbr), source, version, reviewed_by | 独立词表；被 `TermCorrection` 引用 |
| `TermCorrection` | id, segment_id, before, after, rule(exact/fuzzy), term_id, reversible | N : 1 `TranscriptSegment` |
| `ClinicalElement` | id, session_id, type(S/O/A/P/uncategorized), text, source_segment_ids[], state(pending/confirmed/edited/rejected) | N : 1 `Session` |
| `ReportTemplate` | id, name, specialty, version, fields[{key,label,required,order,type}] | 文件化定义，带版本号 |
| `GeneratedReport` | id, session_id, template_id, model_meta(model, prompt_version), status(draft/signed), signed_at, signed_by | N : 1 `Session`；1 : N `FieldReview`、`ExportRecord` |
| `FieldReview` | id, report_id, field_key, ai_value, final_value, action(accept/edit/reject), reviewed_at | N : 1 `GeneratedReport` |
| `ExportRecord` | id, report_id, format, path, exported_at | N : 1 `GeneratedReport` |
| `EvalRun` | id, dataset_version, metric, value, worst_language, notes | 独立记录，用于回归 |
| `AuditEvent` | id, session_id, event_type, payload, at | 记录删除、外部调用、签署等关键事件 |

### 3.2 持久化与访问规则

- **默认全部本地**：SQLite 数据库文件 + `data/audio/`、`data/exports/` 目录；无云端同步。
- **外部访问边界**：仅当用户显式启用时，才把**文本**（不发送音频）发送给外部 LLM；外部 ASR 需单独启用（NFR-06）。
- **可识别信息**：在写库前对转写文本执行一次规则化去标识化（姓名/电话/证件号模式），处理结果记入 `AuditEvent`（NFR-04、Q-04）。
- **删除语义**：删除会话为级联删除，音频文件同步物理移除（BR-12、AC-10）。
- **版本化**：`ReportTemplate` 与 `TermEntry` 均带 `version`；`GeneratedReport.model_meta` 记录生成时使用的模板与提示词版本，保证可复现。

---

## 4. 关键实现方法

### 4.1 需求到模块的映射

| 需求 | 实现要点 |
| --- | --- |
| FR-01 | 前端 MediaRecorder 采集音频（webm/opus）→ 上传 → 后端用 ffmpeg 转为 16 kHz 单声道 wav；校验时长与静音（能量阈值） |
| FR-02 | ASR 适配层接口 `transcribe(audio_path) -> [Segment]`；本地用 faster-whisper（按段落切分处理长音频），可选云 API；输出统一映射为 `TranscriptSegment`，语种标签由模型 language probability + 段内混合检测规则产生 |
| FR-03 | 归一化（转小写、去标点、全角转半角）→ 精确匹配 → 模糊匹配（编辑距离 ≤ 2，仅对词长 ≥ 5 的词启用，避免误替换短词）→ 记录 `TermCorrection` |
| FR-04 | 分块 + 滑动窗口调用 LLM，要求输出固定 schema；每段带 `segment_id`，模型必须回填来源 ID；后端校验来源 ID 是否真实存在，不存在则标记为"待核实" |
| FR-05 | 模板引擎读取 `ReportTemplate.fields`，把 `ClinicalElement`（仅 state ∈ {confirmed, edited}）填入对应字段；未覆盖字段填"未提及" |
| FR-06 | 生成后用规则校验：① 必填字段是否为空；② 字段文本是否能在来源片段中找到依据（关键词/相似度阈值）；不通过则标"待核实"，**不阻断显示但阻断签署** |
| FR-07 | 前端字段卡片直接读取 `FieldReview`；每次操作写入一条记录，保留 `ai_value` 不变（差异可回溯，AC-07） |
| FR-08 | 签署前校验：所有 `required` 字段的 `FieldReview.action ∈ {accept, edit}`；通过后 `status = signed` |
| FR-09 | 文本导出：字段拼接为可粘贴文本；PDF：模板排版渲染；JSON：字段 + `source_segment_ids` + 时间戳 |
| FR-10 | 会话列表读取 SQLite；删除触发级联删除并写 `AuditEvent` |

### 4.2 主要接口（最小集合，非完整 API 规范）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/sessions` | 新建会话 |
| POST | `/sessions/{id}/audio` | 上传录音，返回时长与校验结果 |
| POST | `/sessions/{id}/transcribe` | 执行 ASR，返回分段转写 |
| PATCH | `/segments/{id}` | 人工修正转写片段 |
| POST | `/sessions/{id}/correct-terms` | 运行术语纠正，返回替换记录 |
| POST | `/sessions/{id}/extract` | 抽取 S/O/A/P 要素 |
| GET | `/templates` | 列出可用模板 |
| POST | `/sessions/{id}/generate` | 指定模板生成初稿 |
| PATCH | `/reports/{id}/fields/{key}` | 字段校审（采纳/编辑/拒绝） |
| POST | `/reports/{id}/sign` | 签署（含必填校验） |
| POST | `/reports/{id}/export` | 导出 text / pdf / json |

### 4.3 LLM 调用结构（防止幻觉的具体手段）

```
system:   你是病历整理助手。只能使用给定转写文本中的信息；
          输出必须严格符合 schema；无法确定的内容写 "未提及"；
          禁止推断诊断、剂量、过敏史。
few-shot: 1–2 个"转写 → JSON"示例（含一条故意缺失的字段，示范写 "未提及"）
user:     [S-001 | 00:12-00:19] …口述片段…
          [S-002 | 00:19-00:27] …
          schema: { subjective: [...], objective: [...], assessment: [...], plan: [...] }
后处理:   JSON Schema 校验 → 来源 ID 校验 → 失败则重试 1 次 → 仍失败则报错，不输出
```

---

## 5. 失败处理

| 失败情形 | 系统行为 | 用户可见反馈 |
| --- | --- | --- |
| 音频为空 / 静音 | 不调用 ASR，不调用 LLM | "未检测到有效语音，请重新录制"（AC-11） |
| ASR 返回空文本 | 记录失败样本，保存音频 | "转写失败，请重试或重录"（AC-13） |
| ASR / LLM 超时 | 保留已完成步骤，允许重试；不产生半成品报告 | 明确错误信息 + 重试按钮（AC-12） |
| LLM 输出不符合 schema | 自动重试 1 次；仍失败则中止本次生成 | "生成失败，未修改任何已确认内容" |
| LLM 输出含无来源内容 | 标为"待核实"并计入幻觉统计；**禁止签署**直至处理 | 字段旁红色标记（AI-03 评估指标：幻觉字段数 = 0） |
| 模板不存在 / 版本不匹配 | 回退到通用 SOAP 模板，记录事件 | 提示"模板不可用，已使用通用模板"（AC-14） |
| 术语库文件损坏 | 跳过纠正步骤，保留原始转写 | 提示"术语库加载失败，结果未做术语纠正" |
| 外部 API 配额耗尽 / 密钥失效 | 阻止调用，保留现场；若配置了本地模型则提示切换 | 明确错误 + 降级说明 |
| 磁盘写入失败（音频/导出） | 中止并保留内存中数据，提示另存 | 错误信息 + 路径 |
| 数据库损坏 | 启动时自检；损坏则提示并允许进入只读模式 | 只读模式提示 |

---

## 6. 运行与验证指令

> **状态说明**：以下命令为**拟定命令**，尚未执行验证（unverified）。团队首次跑通后须在本文档标注"已验证 + 日期 + 执行人"。

### 6.1 前置条件

- Windows / macOS / Linux 桌面环境；
- Python 3.11+、Node.js 20+、`ffmpeg`（需在 PATH 中）；
- 可选：NVIDIA GPU（加速本地 ASR）；无 GPU 时使用较小模型或云 API；
- 可选的 LLM API 密钥（写入本地 `.env`，**不提交到仓库**）。

### 6.2 安装与启动（拟定）

```bash
# 后端
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000

# 前端
npm install
npm run dev                        # 默认 http://localhost:5173
```

### 6.3 验证步骤

| 检查项 | 方法 | 期望结果 |
| --- | --- | --- |
| 端到端细线 | 用 `data/eval/sample_*.wav` 走完录音 → 转写 → 抽取 → 生成 → 校审 → 导出 | 得到 PDF 与 JSON，字段可追溯到时间点 |
| ASR 回归 | `python scripts/eval_asr.py --dataset data/eval/regression_30min --report reports/asr_report.json` | 输出分语种 CER/WER 与术语命中率（《MVP 产品需求文档》指标 1） |
| 结构化准确率 | `python scripts/eval_report.py --samples data/eval/reports --rubric docs/rubric_v1.md` | 输出字段级准确率与幻觉字段数（《MVP 产品需求文档》指标 2） |
| 失败路径 | 依次触发 AC-11 至 AC-14 场景 | 与《MVP 产品需求文档》第 7.2 节描述一致 |
| 隐私检查 | 用含虚构姓名的音频跑一次，检查本地存储与外部请求内容 | 无音频外发；文本已去标识化 |

### 6.4 目录约定（拟定）

```
app/            后端服务
web/            前端界面
data/audio/     会话音频（本地，不提交）
data/eval/      评测语料（合成数据，标注 [SYNTHETIC]）
terms/          术语库（版本化）
templates/      报告模板（版本化）
scripts/        评测与工具脚本
docs/           本文档集
```

---

## 7. 部署与成本

| 项目 | 说明 |
| --- | --- |
| **运行位置** | 本地单机（开发机 / 演示笔记本），由团队控制；不部署到公网 |
| **配置项** | 外部 ASR 与 LLM 的开关、模型名、密钥（`.env`）、术语库与模板版本、API 成本上限 |
| **成本来源** | ① 外部 ASR 按音频时长计费（若启用）；② LLM 按 token 计费；③ 若使用云主机则另计 |
| **成本控制** | 单次会话 token 上限与调用次数上限（AI-01/02/03）；每日累计用量日志；超限即拒绝调用并提示；默认走本地 ASR 以降低费用 |
| **成本登记要求** | 具体单价与月度实测金额由团队在首次执行后填入下表，**本文档不预设未经核实的价格数字** |

| 月份 | 外部 ASR 用时 | LLM token 用量 | 实际费用 | 备注 |
| --- | --- | --- | --- | --- |
| 待填 | 待填 | 待填 | 待填 | 首次实测后回填 |

---

## 8. 明确不适用的设计项

| 项目 | 结论 | 解释 |
| --- | --- | --- |
| 用户认证 / 单点登录 | **Not applicable** | MVP 为单机单用户工具，无多用户权限需求；访问控制由操作系统账户提供。若未来接入医院环境，需重新评估（《MVP 产品需求文档》第 10.3 节） |
| 微服务 / 容器编排 | **Not applicable** | 组件数量少、负载低，单体应用已足够；引入会显著增加 5 人团队负担 |
| 支付 / 计费系统 | **Not applicable** | 项目为课程原型，非商业产品 |
| 公网托管 / CDN | **Not applicable** | 本地运行即可满足课程要求，且可避免患者数据外流 |
| 分布式数据库 / 分片 | **Not applicable** | 单机 SQLite 足以支撑单用户数据量 |
| 多智能体编排 | **Not applicable** | 三个 AI 功能为顺序流水线，无需智能体框架 |
| EHR 实时对接 | 暂缓（非"不适用"） | 需求成立但依赖医院授权，MVP 以导出替代（《MVP 产品需求文档》第 3 节） |

---

## 9. 追溯表（交付时填写）

| 需求 | 组件 / 模块 | 验证证据 | 最终状态 |
| --- | --- | --- | --- |
| FR-01 | 音频管理 | 待填（AC-01 测试记录） | 待填 |
| FR-02 | ASR 适配层 | 待填（AC-02 记录 + ASR 回归报告） | 待填 |
| FR-03 | 术语纠正 | 待填（AC-03 记录） | 待填 |
| FR-04 | 要素抽取 | 待填（AC-04 记录 + 要素准确率） | 待填 |
| FR-05 | 报告生成 | 待填（AC-05 记录） | 待填 |
| FR-06 | 缺失与风险提示 | 待填（AC-06 记录 + 幻觉字段数） | 待填 |
| FR-07 | 校审与签署 | 待填（AC-07 记录） | 待填 |
| FR-08 | 校审与签署 | 待填（AC-08 记录） | 待填 |
| FR-09 | 导出 | 待填（AC-09 记录） | 待填 |
| FR-10 | 存档与审计 | 待填（AC-10 记录） | 待填 |

> 最终状态取值为：implemented / changed / removed / incomplete。范围变化必须写明原因，不得静默改写原计划（课程规范 §8）。

---

## 10. 本文档的验证状态

| 项目 | 状态 |
| --- | --- |
| 架构与组件划分 | 设计草案，未实现 |
| 技术选型 | 待 spike 结果确认（ASR 与 LLM 的实际表现决定最终选择） |
| 运行命令 | **未验证**（unverified），首次跑通后回填 |
| 成本数据 | **未测量**，首次实测后回填 |
| 数据模型 | 草案，随第一个原型迭代 |

---

## 11. 配套文档

| 文档 | 作用 |
| --- | --- |
| 《项目提案》 | 问题、证据与假设状态、可行性、团队与里程碑 |
| 《MVP 产品需求文档》 | 需求编号 FR / BR / NFR / AC、成功指标与 AI 功能附加说明 |
| 《调研方案》 | 导师/助教书面确认问题清单、公开资料分析计划、模拟口述语料录制与评测规范 |
| 《7 周执行计划》 | 按系统组件划分的 WBS（WP1–WP9）、里程碑门禁、周计划与风险预案 |
| 《决策与验证记录》 | 每项重要决策的理由、被否方案与验证方式 |
