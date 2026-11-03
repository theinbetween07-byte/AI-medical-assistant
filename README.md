# MedScribe HK

> 香港临床口述智能病历助手：把粤语、英语及中英混合口述整理为可校审的结构化病历初稿。
>
> 课程项目 TJU 44511105 · 2026-10-12 – 2026-11-29 · 5 人团队

## 当前状态

- **UI 预览 v9.0 已通过（2026-11-02）**：E 字号、简体中文 / 繁體中文 / English、浅色 / 深色 / 系统主题、导出 PDF / JSON 预览、桌面与手机布局。
- **新 UI 已接入实际应用（2026-11-02）**：`medscribe-allinone/app/integration/app/static/` 下线旧界面，改用 v9.0 外壳 + 真实 API。
- **真实链路已走通（2026-11-02）**：同一 28.6 秒合成音频在两个会话各跑一轮：14 段转写 → 术语纠正 → 14 条要素 → 9 字段初稿 → 补齐 5 个必填 → 签署 → 导出 PDF / JSON；两轮均无 JS 错误与 4xx/5xx。另用 3 条 SAPI 合成语料跑通 3 套模板（英文 SOAP 43s / 普通话门诊 72s / 英文病理 90s），均完成签署与 PDF/JSON 导出。证据：[e2e](outputs/ui-integration/e2e)、[e2e-run2](outputs/ui-integration/e2e-run2)、[e2e-multilingual](outputs/ui-integration/e2e-multilingual)。
- **仍缺验收数据**：54 条验收矩阵中 13 条已填证据（5 已验证、8 部分通过），其余 41 条待执行；中文 CER、结构化准确率、UAT 尚无实测数据，英文 WER 仅合成语料初测（4.7% / 20.0%）。
- **一体化原型可运行**：`medscribe-allinone/` 内置 Python 3.12、ffmpeg 与 faster-whisper small 模型，三段本地服务可一键启动，完全离线。
- **可选 ASR 路径**：除默认的 faster-whisper 外，也可选用 [CapsWriter-Offline](https://github.com/HaujetZhao/CapsWriter-Offline)（离线高精度档，病理术语实测更准，但处理耗时更长、需要本地硬件算力）；两条路径的硬件门槛与接入方式见 [technical-design.md](technical-design.md)，5 档 Whisper + 4 档 CapsWriter 的实测对比见 [REPORT.md](outputs/ui-integration/asr-benchmark/REPORT.md)。
- **尚未完成验收**：54 条验收矩阵中 41 条仍为“待执行”；中文 CER、结构化准确率、UAT 尚无实测数据。

完整事实、证据与风险见 [项目背景与当前进度](docs/PROJECT_STATUS.md)。

## 下载与安装（Windows，免安装）

发布地址：<https://github.com/theinbetween07-byte/AI-medical-assistant/releases/tag/v1.0.0>

| 包 | 内容 | 分卷 | 下载体积 | 解压后 |
| --- | --- | --- | --- | --- |
| 基础包 | 便携 Python 3.12、整合界面、报告引擎、Whisper small、ffmpeg | `MedScribe-base-v1.0.0.7z.001`–`.003` | 0.52 GB | 908 MB |
| 扩展包（可选） | Qwen3-ASR（CapsWriter-Offline）高精度引擎 | `MedScribe-ext-caps-qwen3-v1.0.0.7z.001`–`.013` | 2.23 GB | 2.9 GB |

安装基础包：

1. 把 3 个分卷放到同一文件夹，用 [7-Zip](https://www.7-zip.org/) 解压 `.001`（自动合并其余分卷），得到 `MedScribe` 文件夹。
2. 双击 `MedScribe\MedScribe.bat`，等待控制台显示三段服务就绪（首次 10–30 秒）。
3. 浏览器自动打开 <http://127.0.0.1:8100>；关闭右上角提示或双击 `停止.bat` 停服。

安装扩展包（仅当需要更高病理术语准确率）：

1. 下载全部 13 个分卷，解压 `.001`，得到 `MedScribe-ext` 文件夹。
2. 把 `MedScribe-ext\app` 整个复制到 `MedScribe\` 根目录并合并（覆盖同名文件）。
3. 重启 `MedScribe.bat`，在工具条「模型」里选择 **Qwen3-ASR**。首次转写加载模型约 20–60 秒。

未安装扩展时，下拉中该项显示「Qwen3-ASR · 未安装」，点击会弹出下载地址与步骤，不会联网自动下载。

校验：`SHA256SUMS.txt` 与各分卷同目录，可用 `certutil -hashfile <文件名> SHA256` 逐个比对。系统要求：Windows 10/11 64 位、4 GB 内存、约 1 GB 磁盘（装扩展再加 2.9 GB）；默认 CPU，无需显卡。

## 项目背景

香港门诊与住院医生需要在有限时间内完成病历和报告书写，口述中常混用粤语、英语和医学英文术语。现有工具通常只做语音输入，缺少“多语言转写 → 术语纠正 → S/O/A/P 抽取 → 模板化报告 → 医生校审签署 → 导出”的完整闭环。

MedScribe HK 的目标是把 3–5 分钟口述变成医生可在 1 分钟内审阅的报告初稿。上述时长为待验证目标，不是已实测结果。MVP 明确不接入真实患者数据、不连接 EHR、不做自动诊断或治疗建议；报告必须由有资质医师逐项核对并签署。

## 核心功能

| 编号 | 功能 | 当前实现 |
| --- | --- | --- |
| F1 | 录音与多语言 ASR | faster-whisper small，本地 CPU int8，随包内置、离线可用；工具条「模型」可切到可选扩展 Qwen3-ASR（未安装时显示下载地址与安装步骤，绝不联网自动下载）；演示以粤语 / 普通话为主 |
| F2 | 术语纠正 | 602 条术语，436 条可自动替换；支持撤销与审计 |
| F3 | S/O/A/P 要素抽取 | 报告引擎按中文医学关键词归类，未归类内容保留 |
| F4 | 模板化报告与校审 | 3 套模板；字段级采纳 / 编辑 / 缺失提示；签署门禁 |
| F5 | 本地存档与导出 | SQLite + 本地文件；TXT / JSON / PDF；全程不出本机 |

三套模板：通用 SOAP 记录、门诊记录、外科病理报告（结直肠，20 字段 / 17 必填）。

## 系统架构

```text
浏览器
  │
  ▼
集成层 FastAPI :8100  ──► ASR 后端 :18000（faster-whisper）
  │                         │
  ├── 术语纠正（本地词表）   │
  └── 报告引擎 :8790（抽取 / 生成 / 校审 / 导出）
```

数据默认全部保存在本机：SQLite、`data/audio/`、`data/exports/`。外部 ASR / LLM 不在默认链路中。

## 快速开始

### 完整原型

1. 确认 `medscribe-allinone/` 已完整解压，模型目录约 1.53 GB。
2. 双击 `medscribe-allinone/start.bat`。
3. 首次启动等待 30–60 秒加载模型，浏览器打开 <http://127.0.0.1:8100>。
4. 停止服务：双击 `medscribe-allinone/stop.bat`，或关闭启动窗口。

更细的端口、环境变量、接口与限制见 [集成层 README](medscribe-allinone/app/integration/README.md)。

### 静态 UI 预览

直接打开 [medscribe-ui-preview.html](outputs/ui-preview/medscribe-ui-preview.html)。

- 这是静态预览，不连接后端，不会创建真实会话或文件。
- 左下角“介面語言 / 界面语言”可切换简体中文、繁體中文、English。
- 主题可切换浅色、深色、系统。
- 导出按钮打开 PDF / JSON 版式预览；下载为演示行为。

## 测试与验收

- 集成测试：`medscribe-allinone/app/integration/tests/test_integration.py`，共 40 项；统一入口 `python scripts/run_tests.py`（2026-11-02 连续 3 轮 40/40）。
- 启动/停止安全：`scripts/start_all.py` 与 `scripts/stop_all.py` 已加 `/health` 身份与命令行双重校验，陌生端口会中止启动、停止时不会按端口误杀。
- 2026-11-02 复跑：40/40 通过（`scripts/run_tests.py`，见 `docs/PROJECT_STATUS.md` §5.1）。
- 实际应用 UI：`app/integration/app/static/index.html` + `app.css` / `app-extra.css` / `app.js`，真实 API 驱动；旧界面已归档到 `app/integration/app/static/archive/`。
- 端到端证据：[outputs/ui-integration/e2e](outputs/ui-integration/e2e)（截图、PDF、JSON、evidence.json）。
- 验收方案：[MedScribe HK 质量保障与体验优化方案](outputs/medscribe-测试验收与质量优化方案.md)。
- 验收矩阵：[medscribe-acceptance-matrix.csv](outputs/medscribe-acceptance-matrix.csv)，54 条，当前 13 条已填证据（5 已验证、8 部分通过）。
- 当前不要用“集成测试通过”替代“AC 验收通过”；两者不是同一层证据。

## 目录

```text
project-proposal.md             项目提案
mvp ai medical assistant-prd.md MVP PRD
technical-design.md             技术设计（初始设计，部分命令未验证）
medscribe-allinone/             一体化可运行原型
outputs/                        质量方案、验收矩阵、UI 预览、接入后截图
docs/                           项目状态与 Agent 技能基线
```

## 文档索引

| 文档 | 用途 |
| --- | --- |
| [项目提案](project-proposal.md) | 背景、用户、范围、风险、里程碑 |
| [MVP PRD](<mvp ai medical assistant-prd.md>) | FR / BR / NFR / AC 与成功指标 |
| [技术设计](technical-design.md) | 组件、数据模型、接口与失败处理 |
| [课程项目文档合集](project-documents.html) | 7 周执行计划、调研方案、决策记录与 AGENTS 草案（2026-10-15 合集） |
| [项目背景与当前进度](docs/PROJECT_STATUS.md) | 当前状态、证据、缺口与交接 |
| [质量保障方案](outputs/medscribe-测试验收与质量优化方案.md) | 测试、视觉、交互、准确性与稳定性方案 |
| [UI 预览](outputs/ui-preview/medscribe-ui-preview.html) | 已确认的 v9.0 视觉与交互预览 |
| [Agent 技能基线](docs/agent-skills/README.md) | 8 个技能、冲突规则与恢复方式 |

## 约束与声明

- 只使用合成语料；不接入真实患者数据。
- 音频、转写和导出默认仅存本机；不部署公网。
- 生成内容不是诊断或治疗建议；必须由有资质医师逐项核对并签署。
- 本项目用于课程技术验证与研究，不作为医疗器械。
- 仓库未提供 LICENSE；对外复用前需项目负责人确认。

## 已知限制

- 真实链路只在一条 28.6 秒合成音频上走通；该音频偏病理/手术叙述，通用 SOAP 模板只产出 1 个有内容的字段，尚未覆盖门诊 SOAP 语料。
- 54 条验收用例状态均为“待执行”；ASR CER、结构化准确率、UAT 没有实测报告。
- ASR 逐段置信度仍是整段语言概率代理值，不能支撑严格的低置信度验收。
- 8100 / 8790 端口仍为硬编码；启动脚本缺少服务身份校验，停止脚本可能误杀占用端口的进程。
- 当前目录不是 Git 仓库，变更无法通过提交历史追溯。
- 根目录缺少独立的 `AGENTS.md` / `CLAUDE.md`；7 周执行计划、调研方案、决策记录与 AGENTS 草案已合并收录在 [project-documents.html](project-documents.html)，但未拆成独立 Markdown 文件。
