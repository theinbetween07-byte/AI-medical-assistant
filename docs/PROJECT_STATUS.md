# MedScribe HK 项目背景与当前进度

> 最后更新：2026-11-03
>
> 当前结论：**视觉预览 v9.0 已通过；新 UI 已接入实际应用；3 套模板的完整链路已走通并完成签署与导出；真人/粤语/中英混合语料、准确性证据与其余 41 条验收仍未完成。**
>
> 本文写给接手项目的开发、测试、设计和下一位 Agent。事实基准为工作区现有文件与 2026-11-01 / 11-02 的本地验证记录；未实测的内容不按已完成陈述。

---

## 1. 项目背景

MedScribe HK 是香港临床口述智能病历助手。医生用粤语、英语或中英混合口语口述诊疗过程，系统完成：

```text
录音 → 多语言转写 → 术语纠正 → S/O/A/P 抽取 → 模板化报告
     → 医生逐字段校审 → 签署 → 导出 TXT / JSON / PDF
```

**目标用户**：门诊 / 住院医生（主要操作者）；病案室 / 质控人员（格式与完整性）；患者（数据安全受影响方）。

**核心价值**：把 3–5 分钟口述变成医生约 1 分钟内可审阅的报告初稿。该时长是**待验证目标**，不是已实测结果。

**课程与团队**：TJU 44511105 课程项目，周期 2026-10-12 至 2026-11-29，5 人团队；项目提案日期 2026-10-15。

## 2. MVP 范围与边界

MVP 必须跑通五个能力：

| 编号 | 能力 | 说明 |
| --- | --- | --- |
| F1 | 录音与多语言 ASR | 本地 faster-whisper small（随包内置、完全离线）；界面「模型」下拉另含可选扩展 Qwen3-ASR、Whisper medium、Whisper large-v3，未安装时显示下载地址与步骤；演示以粤语 / 普通话为主（见《技术设计》2.1） |
| F2 | 术语纠正 | 602 条术语，436 条可自动替换；可撤销、可审计 |
| F3 | 临床要素抽取 | 按中文医学关键词归入 S / O / A / P；未归类内容保留 |
| F4 | 模板化报告与校审 | 3 套模板；字段级采纳 / 编辑 / 缺失提示；签署门禁 |
| F5 | 本地存档与导出 | SQLite + 本地文件；TXT / JSON / PDF；音频与病历不出本机 |

**明确不在 MVP 内**：真实患者数据、EHR / eHRSS 实时对接、移动端 App、多说话人分离、模型微调、多专科模板全覆盖、账号体系、公网部署、自动诊断或治疗建议。

**关键约束**：只使用合成语料；默认全程本地；生成内容必须由有资质医师逐项核对并签署；项目用于课程技术验证，不作为医疗器械。

## 3. 系统架构

```text
浏览器
  │  HTTP / JSON
  ▼
集成层 FastAPI :8100
  ├── 会话、音频、状态机、SQLite、审计、导出
  ├── ASR 适配 ──► ASR 后端 :18000（faster-whisper small，CPU int8；可选扩展 Qwen3-ASR / medium / large-v3）
  ├── 术语纠正（terms/*.json，本地算法）
  └── 报告引擎 :8790（抽取 / 生成 / 校审 / 签署 / 导出）
```

| 组件 | 目录 | 默认地址 | 职责 |
| --- | --- | --- | --- |
| ASR 后端 | `medscribe-allinone/app/backend` | `127.0.0.1:18000` | 转写、JWT 鉴权、模型加载 |
| 报告引擎 | `medscribe-allinone/app/report-engine` | `127.0.0.1:8790` | 术语、抽取、模板报告、导出 |
| 集成层 | `medscribe-allinone/app/integration` | `127.0.0.1:8100` | 编排、持久化、网页界面 |
| ASR 模型 | `medscribe-allinone/app/models/faster-whisper-small` | — | 约 464 MB（随包，离线） |

启动入口：`medscribe-allinone/start.bat` 调用 `scripts/start_all.py --open`；停止入口：`stop.bat`。完整环境变量与接口见 [集成层 README](../medscribe-allinone/app/integration/README.md)。

## 4. 当前进度

### 4.1 已完成

| 项目 | 状态 | 证据 / 位置 |
| --- | --- | --- |
| 项目提案 | 完成 | [project-proposal.md](../project-proposal.md)，2026-10-15 |
| MVP PRD | 草案，功能范围明确 | [mvp ai medical assistant-prd.md](<../mvp ai medical assistant-prd.md>) |
| 技术设计 | 初始设计，部分命令未验证 | [technical-design.md](../technical-design.md) |
| 课程文档合集 | 已生成 | [project-documents.html](../project-documents.html)；含 7 周计划、调研方案、决策记录与 AGENTS 草案 |
| 一体化原型 | 可运行 | `medscribe-allinone/`；2026-10-30 / 10-31 |
| 术语库 | 已编译 | 602 条，436 条自动替换；`terms/import_report.json` |
| 模板 | 3 套 | 通用 SOAP、门诊记录、外科病理报告（结直肠） |
| 集成测试 | 40/40，连续 3 轮通过 | `scripts/run_tests.py`；2026-11-02 另对读取重置加有界重试 |
| 质量方案 | 完成 v1.0 | [质量保障与体验优化方案](../outputs/medscribe-测试验收与质量优化方案.md) |
| 验收矩阵 | 54 条，13 条已有证据 | [acceptance-matrix.csv](../outputs/medscribe-acceptance-matrix.csv)：5 已验证、8 部分通过 |
| Agent 技能基线 | 已安装并锁定 | [技能基线](agent-skills/README.md)，8 个技能 |
| UI 预览 | v9.0 视觉通过 | [medscribe-ui-preview.html](../outputs/ui-preview/medscribe-ui-preview.html)，2026-11-02 |
| 新 UI 接入实际应用 | 完成 | `medscribe-allinone/app/integration/app/static/index.html` + `app.css` / `app-extra.css` / `app.js`，2026-11-02；旧界面归档为 `static/archive/index-legacy-2026-10-29.html` |
| 真实链路端到端 | 5 条合成音频 / 3 套模板走通 | 28.6s 病理朗读两轮 + 英文 SOAP、普通话门诊、英文病理各 1 条；三段模板均走完上传 → 转写 → 签署 → PDF/JSON。证据 [e2e](../outputs/ui-integration/e2e) / [e2e-run2](../outputs/ui-integration/e2e-run2) / [e2e-multilingual](../outputs/ui-integration/e2e-multilingual) |
| 启动/停止安全 | 完成身份校验 | `/health` + 命令行双重校验；陌生端口中止启动、停止不误杀；2026-11-02 演练 |

### 4.2 UI 预览 v9.0 与生产接入

用户于 2026-11-02 确认视觉预览通过。`outputs/ui-preview/medscribe-ui-preview.html` 仍是**静态 HTML 原型**，不连接真实后端，保留作为视觉基线与对比件。

- E 字号方案：转写正文 14.5px、报告正文 13px、字段标签 11.5px、侧栏 meta 12.5px、页面标题 20px。
- 三语界面：简体中文 / 繁體中文 / English；约 170 个 i18n 键；语言写入 `medscribe-lang`。
- 主题：浅色 / 深色 / 系统；写入 `medscribe-theme`。
- 转写标签位于颜色框内；SOAP 颜色框与正文视觉间距 10px；英文左列 76px，中文 34px。
- 导出按钮先打开 PDF / JSON 预览，不直接下载；PDF 版式与 JSON 字段来自当前模拟状态。
- 左下角顺序：缩放说明在上，账号在最底部。
- 已验证 1440 / 1366 / 1024 / 981 / 390 宽度；三种语言无横向溢出；繁体签署、导出、刷新持久化通过。
- 历史归档：`outputs/ui-preview/archive/medscribe-ui-preview-v1.0.html` 至 `v9.0.html`；E 候选副本在 `outputs/ui-preview/font-variants/font-e-layered.html`。
- 生产页面复用了本预览的样式与信息架构（`app.css` 由本预览的样式块导出），但**不再包含**预览里的陈先生模拟会话、模拟片段、模拟报告与模拟导出：`app.js` 只渲染集成层 API 返回的数据。

### 4.3 进行中 / 未完成

| 项目 | 状态 | 说明 |
| --- | --- | --- |
| 多语种 / 多模板真实链路 | 部分完成 | 3 套模板（general_soap / outpatient_record / pathology_colorectal）各跑通 1 条合成音频；英文、普通话已覆盖；粤语、中英混合与每条模板 ≥3 条语料仍未覆盖 |
| AC-01–AC-14 验收 | 部分有证据 | 54 条中 13 条已填：AC-05/06、I-04、I-10、P-06 已验证；AC-02/04/07/08/09、P-02/07、V-07 部分通过；其余 41 条待执行 |
| ASR CER / WER | 未实测 | 技术设计中的 `eval_asr.py`、`data/eval/` 等资产不存在 |
| 结构化准确率 | 未实测 | `eval_report.py`、评分量表不存在 |
| 逐段置信度 | 代理值 | ASR 只返回整段 `language_probability`，集成层复制到片段 |
| 端口与进程治理 | 部分修复 | 已有身份校验与安全停止；未实现端口参数化与自动回退（P-01/03/04/05 仍待执行） |
| UAT / 可用性 | 未开始 | SM-01–SM-04 无记录 |
| CI / 覆盖率 / 浸泡 | 未开始 | 无 CI 配置，无覆盖率与浸泡报告 |
| 根目录 Agent 指南 | 缺失 | `AGENTS.md` / `CLAUDE.md` 不存在 |

## 5. 证据与可信度

### 5.1 已验证

- **UI 预览**：本地 Playwright 验证三语切换、语言持久化、签署弹窗、PDF / JSON 预览、下载 Toast、手机端 390px 布局；无页面错误。
- **UI 版式**：桌面三语、981px 语言栏、390px 手机布局；`scrollWidth` 未超过视口。
- **静态结构**：HTML 0 标签错误、无重复 ID、19 个 symbol 完整；2 个样式块括号平衡；2 段 JS 语法正常。
- **集成测试记录**：2026-11-01 记录 40 项；2026-11-02 增加读取重试后，用 `scripts/run_tests.py` 连续 3 轮 40/40（单轮约 4–6s）。ASR 转写与引擎响应都改为整段读取重试，覆盖 `ConnectionResetError [WinError 10054]`。
- **实际应用 UI**：2026-11-02 本地 Playwright（Chromium）对 `http://127.0.0.1:8100/` 验证：页面无 JS 错误、无 4xx/5xx；7 条既有会话渲染；加载会话后 14 段转写、9 个报告字段、5 个必填缺失、签署按钮禁用、导出预览 PDF 4 个分区与 JSON 成功；三语切换与语言持久化、深浅色主题、筛选、1280×800 与 390×844 无横向溢出、会话抽屉开合、新建/删除会话均通过。
- **真实链路（2026-11-02）**：完整三段服务（ASR medium 本地、引擎、集成层）下，用 `ses_06b3b0faf532.wav`（28.6s）跑了 2 个会话：每轮都是上传 → 转写 14 段 → 术语纠正（0 处匹配）→ 抽取 14 条 → 生成 9 字段（5 项必填缺失）→ 界面补齐必填 → 签署 `demo-doctor` → 导出 PDF / JSON。第一轮 `ses_d4e90b563942`（PDF 3535B / JSON 5383B）；第二轮 `ses_f4879d7729a0`（PDF 3536B / JSON 5593B，导出记录各 1 条）。两轮均无 JS 错误与 4xx/5xx。证据 [outputs/ui-integration/e2e](../outputs/ui-integration/e2e)、[e2e-run2](../outputs/ui-integration/e2e-run2)。
- **三模板真实链路（2026-11-02）**：SAPI 合成 3 条语料（英文 SOAP 43s、普通话门诊 72s、英文病理 90s），分别用 `general_soap` / `outpatient_record` / `pathology_colorectal` 走完 API 全链路并签署导出。转写 16 / 12 / 25 段，抽取 16 / 12 / 25 条要素（全部带来源），生成 9 / 12 / 20 字段，缺失必填 2 / 1 / 1，补齐后签署 `demo-doctor`，PDF 与 JSON 各 1 条记录。会话 `ses_564438f2aa73` / `ses_883bb000dbb4` / `ses_f18624d7feb4`。证据 [e2e-multilingual](../outputs/ui-integration/e2e-multilingual)（含中文报告 UI 截图 `outpatient-zh-ui.png`）。
- **帮助说明（2026-11-03）**：帮助按钮由 toast 改为对话框，含快捷键与「语音识别与转录实现」两条路径（faster-whisper / CapsWriter-Offline）的硬件门槛对比；简/繁/英三语与 390×844 窄屏截图验证，页面无横向溢出。证据 [help-dialog](../outputs/ui-integration/help-dialog)。
- **发布形态（2026-11-03）**：基础包（单文件 / 0.52 GB / 解压 908 MB）内置便携 Python、small 模型与 ffmpeg，解压即用、完全离线；另有三个扩展包：Qwen3-ASR（2 卷 / 2.23 GB）、Whisper medium（单文件 / 1.30 GB）、Whisper large-v3（2 卷 / 2.62 GB），未安装时下拉显示「未安装」并弹出下载地址。基础包与 Qwen3 扩展在干净目录解压后实测：small 43.4 s 音频 8.1 s、13 段；扩展切换 0.3 s、90 s 病理音频 35.3 s、32 段；Whisper medium 加载 5.0 s、43.4 s 音频 18.5 s / 16 段，Whisper large-v3 加载 8.1 s，切回 small 1.1 s（CPU int8）。证据 [release-smoke](../outputs/ui-integration/release-smoke)、[asr-benchmark](../outputs/ui-integration/asr-benchmark)。
- **ASR 模型切换（2026-11-03）**：工具条「模型」下拉读取 `GET /api/asr/models`，切换走 `POST /api/asr/model`（集成层代理上游 `/asr/load-model`）。发布版含「Whisper small · 本地」与三个扩展项（Qwen3-ASR / Whisper medium / Whisper large-v3，未安装时标注「未安装」）；未安装时弹窗给出 Release 下载地址、分卷与合并步骤，不触发任何联网下载。实测扩展切换 0.3 s（懒加载）、切回 small 1.0 s；三语与 390×844 均可用，页面无横向溢出。帮助对话框正文字号由 9.5–10.5 px 提升到 13–14 px，与主界面正文 13.5 px 同档。证据 [ext-dialog](../outputs/ui-integration/ext-dialog)、[help-dialog](../outputs/ui-integration/help-dialog)。
- **ASR 多档位实测（2026-11-03）**：基础 Whisper 5 档（tiny/base/small/medium/large-v3）与 CapsWriter 4 档（Paraformer/SenseVoice-Small/Fun-ASR-Nano/Qwen3-ASR-1.7B）在 CPU 与 GPU 上跑完准确率、速度、内存/显存与鲁棒性（噪声、语速、音高、5 分钟长音频）对比，共 77 次有效运行。结论：综合最优 medium（GPU 平均 9.59%），病理最优 Qwen3-ASR（3.64%），中文最优 small（13.76%）；large-v3 噪声下退化明显（6.25%→15.62%）。证据 [asr-benchmark](../outputs/ui-integration/asr-benchmark)（`REPORT.md` / `results.csv` / `raw/`）。
- **英文转写 WER（合成语料）**：剔除数字与 tumour/tumor 等拼写变体后，SOAP 4.7%、病理 20.0%。分段重组（把 ASR 在词/短语中间切断的续片接回前一片段）把上一次的 10.9% / 29.1% 降了下来；病理仍有两处标签内部切分（`histological tumor` / `grade, ...`、`differentiate` / `d ...`），导致 `tumour_grade` 为空、`tumour_type` 混入分级。该数值只说明合成语料上的相对水平，不是真人临床环境的准确率。
- **启停安全**：`start_all` 对陌生端口中止并提示（18001 演练）；`stop_all` 对伪造 pid 与陌生 HTTP 服务拒绝误杀（演练中陌生服务存活）；上游全挂时靠 `/health/live` 仍能立即判断集成层身份。
- **术语与模板**：602 条术语、436 条自动替换、3 套模板已写入一体化原型。

### 5.2 不能据此宣称完成的事项

- **“集成测试通过”不等于“AC 验收通过”**。54 条验收矩阵中 13 条已填证据（5 已验证、8 部分通过），其余 41 条仍为“待执行”。
- **“3 条链路走通”不等于“实际应用通过”**。三段均为 SAPI 合成音频，无真人、无粤语、无中英混合，每条模板仅 1 条语料；多会话并发未压测。
- **ASR 只有英文初步 WER**（4.7% / 20.0%，剔除数字与拼写变体）；中文 CER 未做（繁简与数字归一未处理），病理标签内部切分仍存在，不能代表真人临床环境。
- **CapsWriter-Offline 已接入但不覆盖全场景**：同一 77 次运行基准中 Qwen3-ASR 病理错误率 3.64%（small 9.09%），但仍是合成语料、单条/场景；扩展包依赖 PyInstaller 运行时的兼容性，未在无关机验证。
- **没有 UAT**。SM-01–SM-04 无试用者记录。
- **测试入口**：已提供 `scripts/run_tests.py`；直接 `python -m unittest tests.test_integration` 仍不可用（便携 Python 的 `._pth` 不把当前目录加入 `sys.path`），文档应统一引用脚本。
- **连接重置已兜底而非根除**：ASR / 引擎 / 身份探测都加了有界重试，Windows localhost 的 10054 仍可能发生，只是不再直接变成 500。

## 6. 关键风险与缺陷

| 优先级 | 问题 | 影响 |
| --- | --- | --- |
| P0 | 端口被占用时只能安全中止，不能自动回退 | P-01/03/04/05 未通过；换端口仍需人工设环境变量 |
| P0 | 逐段置信度是整段语言概率代理 | 低置信度高亮不可信，AC-02 无有效依据 |
| P0 | 评测脚本、语料、评分量表缺失 | CER / 结构化准确率 / NFR-03 无法复现 |
| P1 | 病理标签内部切分未完全修复 | WER 20.0%；`histological tumor`/`grade` 被拆开导致 `tumour_grade` 为空、`tumour_type` 混入分级；`differentiate`/`d` 仍截断 |
| P1 | 中文 ASR 繁体 + 错字未纠正 | 主宿/廚房/血腸規/輕眉素 等导致 `chief_complaint`、`medications` 为空；术语库未覆盖这些变体 |
| P1 | 真实语料仍为合成、每条模板仅 1 条 | 粤语、中英混合、真人语料与每条模板 ≥3 条未覆盖 |
| P1 | `stop_all` 双实例场景未演练 | 已验证 18001 + 伪造 pid；未在“本项目 8101 + 陌生 8100”场景复验 |
| P1 | 无 CI、覆盖率、浸泡、故障注入 | 无法持续回归，稳定性只能靠手测 |
| P1 | ASR 默认绑定地址需核对；并发 / 日志 / 磁盘边界未治理 | 局域网暴露、并发 OOM、日志无限增长风险 |
| P1 | 根目录缺独立的 `AGENTS.md` / `CLAUDE.md`；7 周计划、调研方案、决策记录与 AGENTS 草案只在 `project-documents.html` 合集中 | 新 Agent / 新成员缺少可直接加载的行为约束；过程文档不是独立源文件 |
| P1 | 当前目录不是 Git 仓库 | 变更无法通过提交历史追溯，回滚困难 |

## 7. 下一步

按“先交付可见价值，再补验收证据”的顺序：

1. ~~**接入 UI**~~（2026-11-02 完成）：v9.0 页面结构、三语、主题、导出预览已接入 `app/integration/app/static/index.html`，数据全部来自真实 API。
2. ~~**走通单条真实链路**~~（2026-11-02 完成）：上传 → 转写 → 纠正 → 抽取 → 生成 → 补齐必填 → 签署 → 导出 PDF/JSON，证据已写入矩阵。
3. ~~**扩大真实链路覆盖（第一轮）**~~（2026-11-02 完成）：3 套模板各 1 条合成语料已走通；下一步补真人、粤语、中英混合，每条模板至少 3 条。
4. **补准确性证据**：英文 WER 已有初测（4.7% / 20.0%）；下一步做中文 CER、结构化字段准确率与 20 分钟冻结回归集，把逐段置信度换成真实信号。
5. **补齐 P0 剩余项**：端口参数化与自动回退（或明确记录“安全中止”为设计决策并更新验收口径）；CI 与覆盖率。
6. **执行其余验收**：41 条待执行项，优先 AC-01/03/10、P-01/03/04/05、I-12、S-07/S-10；未达标项书面豁免，不修改口径。
7. **补文档与工程基线**：把 `project-documents.html` 中已存在的 7 周计划、调研方案、决策记录和 AGENTS 草案拆成独立文件或明确单一来源；补 `CLAUDE.md`（如使用）。

## 8. 交接入口

- 项目入口：[README.md](../README.md)
- 课程文档合集：[project-documents.html](../project-documents.html)
- 实际应用界面：`medscribe-allinone/app/integration/app/static/index.html`（配套 `app.css` / `app-extra.css` / `app.js`）
- 静态 UI 预览：[medscribe-ui-preview.html](../outputs/ui-preview/medscribe-ui-preview.html)
- 视觉对比：[compare.html](../outputs/ui-preview/font-variants/compare.html)
- 完整应用：[start.bat](../medscribe-allinone/start.bat)
- 集成层说明：[integration/README.md](../medscribe-allinone/app/integration/README.md)
- 质量方案：[medscribe-测试验收与质量优化方案.md](../outputs/medscribe-测试验收与质量优化方案.md)
- 验收矩阵：[medscribe-acceptance-matrix.csv](../outputs/medscribe-acceptance-matrix.csv)
- 端到端证据：[outputs/ui-integration/e2e](../outputs/ui-integration/e2e)
- 技能基线：[docs/agent-skills/README.md](agent-skills/README.md)

## 9. 文档现状

- 本次新增：根 `README.md`、本文件。
- 本次更新（2026-11-02）：实际应用界面替换为 v9.0 外壳 + 真实 API（`static/index.html`、`app.css`、`app-extra.css`、`app.js`）；旧界面归档 `static/archive/index-legacy-2026-10-29.html`；`engine_client.py` / `asr_client.py` 的响应读取增加对 Windows localhost 连接重置的有界重试（`asr_client._read_body_retry`）；新增 `scripts/run_tests.py`、`scripts/service_identity.py`、集成层 `/health/live`。
- 本次验证：集成测试 3 轮 40/40；5 条合成音频、3 套模板的完整链路（转写 → 签署 → 导出）通过；启停安全演练通过；41 条验收仍待执行，13 条已填证据。
- 已知缺失：独立的 `AGENTS.md`、`CLAUDE.md`；项目提案提到的《7 周执行计划》《调研方案》《决策与验证记录》已合并收录在 `project-documents.html`，但未拆成独立 Markdown 文件。
- `project-documents.html` 是 2026-10-15 的课程文档合集；其中 AGENTS 草案仍写着 8000 端口、`npm run dev` 等拟定命令，不能当作当前运行命令。当前实际入口以 `medscribe-allinone/start.bat` 和集成层 README 为准。
- `docs/agent-skills/README.md` 原先把 `README.md` / `README-2.md` 当作事实来源；`README-2.md` 在工作区不存在，`README.md` 现已补建为项目入口。技能基线来源已改为实际技能目录、锁文件与质量方案 §4.5。
- `technical-design.md` 中标注为“拟定 / unverified”的命令仍保持未验证状态，不应被引用为已执行证据。
- 当前目录不是 Git 仓库；本文件不提供提交历史，只记录可检查的文件与验证结果。
