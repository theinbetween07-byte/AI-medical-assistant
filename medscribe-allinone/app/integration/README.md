# MedScribe HK 集成层（medical-ai-assistant + report-produce + 医学术语库_v2.xls）

把三块已有资产串成 PRD 描述的 **录音 → 转写 → 术语纠正 → 要素抽取 → 初稿 → 校审 → 签署 → 导出（S1→E）**
端到端流程，对外提供统一的 HTTP API 与单页操作界面。

```
浏览器 (app/static/index.html)
        │  REST /sessions/*
        ▼
集成层 FastAPI  :8100   ← 本目录
   ├── ASR 适配    → medical-ai-assistant  :18000  POST /asr/transcribe   (JWT)
   ├── 术语纠正    → terms/*.json（由 xls 编译，本地算法，可撤销）
   └── 报告引擎    → report-produce         :8790  /api/generate、/review、/sign、/export
```

集成层自己实现 PRD 的 `/sessions` 系列接口、状态机、审计与本地存档；两个上游只作为能力提供方，
不修改其代码。

单页界面（`app/static/`）自 2026-11-02 起复用已确认的 v9.0 视觉，并**只渲染下面这些 API 的真实返回**：
不包含模拟会话、模拟转写、模拟报告或模拟导出文件。语言（简/繁/英）、主题（浅/深/系统）与导出预览是纯前端能力。

启动/停止脚本自 2026-11-02 起做服务身份校验：`/health` 形状 + 监听进程命令行双重匹配。
陌生程序占用端口时 `start_all.py` 会中止并提示，`stop_all.py` 不会按端口误杀。
集成层另有 `/health/live`（不聚合上游），供启动与身份探测使用。

---

## 1. 快速开始

```bat
:: 一键启动三段服务（ASR 18000 / 引擎 8790 / 集成层 8100）并打开界面
start.bat

:: 停止
stop.bat
```

启动后浏览器打开 <http://127.0.0.1:8100/>。

单独启动集成层（上游已在跑）：

```bat
:: 需要能 import fastapi/uvicorn 的 Python
python run.py --host 127.0.0.1 --port 8100
```

`run.py` 支持 `--reload`（开发）与 `--no-upstream-check`（跳过启动前的上游探测）。

### 环境要求

| 项 | 说明 |
| --- | --- |
| Python | 复用 `medical-ai-assistant\backend\python\python.exe`（3.12 嵌入式发行版，含 fastapi/uvicorn/pydantic/python-multipart） |
| ffmpeg | 自动按 `MSHK_FFMPEG` → 本目录 `ffmpeg.exe` → `medical-ai-assistant\backend\ffmpeg.exe` → `PATH` 解析 |
| 端口 | ASR 18000、引擎 8790、集成层 8100（可用环境变量覆盖） |
| 存储 | `data/`（SQLite + 音频 + 导出文件），纯本地，不上传云端 |
| 依赖清单 | `requirements.txt`（fastapi / uvicorn / pydantic / python-multipart / reportlab / pillow / xlrd，版本与便携 Python 内一致）|
| PDF 导出 | 引擎用 reportlab 生成 PDF，已装入便携 Python：`reportlab 5.1.0` + `pillow 13.0.0` + `charset-normalizer 3.5.2`（中文字体 `STSong-Light`）|

### 语音识别与转录实现（可选路径）

转录层对上游只暴露 `transcribe(audio_path) -> [TranscriptSegment]`，因此底层引擎可替换。目前有两条本地路径：

| 维度 | 默认路径：faster-whisper small（随包内置） | 可选路径：CapsWriter-Offline（扩展包） |
| --- | --- | --- |
| 状态 | 已在用（ASR 服务 `:18000`，离线） | 已接入适配层，以扩展包形式提供 |
| 系统环境 | Windows / macOS / Linux；`ffmpeg`；Python 3.11+ | 官方仅保证 Windows 10/11 64 位；VC++ 运行库；文件转录需 `ffmpeg` |
| CPU | 4 核起可用，CPU int8；无需独显 | 无独显也可跑（SenseVoice-Small / Fun-ASR-Nano） |
| 内存 | 约 1.5 GB 量级（small 模型 + 运行时） | 常驻 3–4 GB |
| GPU / 显存 | 非必需；CUDA 可选，medium 约 1.5–2 GB 显存 | DirectML / Vulkan / CUDA 可选；SenseVoice <1 GB，Fun-ASR 与 Qwen3 约 1–2 GB |
| 准确性 | 合成语料 WER 4.7%（SOAP）/ 20.0%（病理） | 官方 Qwen3-ASR 最高档；未同语料对照 |
| 耗时 | 43/72/90 s 音频实测 15.5/30.1/58.9 s | 官方 20 s 音频 CPU 0.6–4.0 s、RTX5050 0.15–1.0 s |

选用 CapsWriter-Offline（<https://github.com/HaujetZhao/CapsWriter-Offline>）时注意：

- 它的识别准确性更高，但处理耗时可能更长，并且需要一定的本地硬件算力支持；
- 无独显的机器建议 SenseVoice-Small，有独显再上 Qwen3-ASR；
- 它自带 C/S 架构与文件转录，接入本项目需要一层适配：把它的 `.json`（时间戳）输出映射成 `TranscriptSegment`，并保持 `language` / 置信度字段；
- 切换前应在同一批语料上比较 WER/CER 与耗时，没有对照数据时不对外宣称"更准"。

界面工具条的「模型」下拉读取 `GET /api/asr/models`，切换走 `POST /api/asr/model`（集成本层代理上游 `POST /asr/load-model`，自动带演示账号 JWT）。选项会标注「本地 / 需下载」；未下载档位首次切换会从 HuggingFace 拉取权重，期间下拉禁用。

### ASR 各档位实测（2026-11-03）

测试机：Intel Core Ultra 7 270K Plus（24 核）/ 32 GB / RTX 5070 Ti 16 GB；语料 3 条（英 SOAP 43 s、中门诊 72 s、英病理 90 s）+ 6 种扰动。完整方法、数据与局限见 [REPORT.md](../../../outputs/ui-integration/asr-benchmark/REPORT.md)。

| 模型 | 平均错误率 | CPU RTF | GPU RTF | 峰值内存 | 显存 |
| --- | --- | --- | --- | --- | --- |
| Whisper-small | 12.3% | 0.10 | 0.02 | 0.6 GB | 0.6 GB |
| Whisper-medium | 13.0% | 0.28 | 0.04 | 1.4 GB | 1.9 GB |
| Whisper-large-v3 | 9.2% | — | 0.05 | 0.8 GB | 3.8 GB |
| Qwen3-ASR-1.7B | 8.1% | 0.18 | 0.09 | 4.0 GB | 2.1 GB |
| SenseVoice-Small | 23.2% | 0.05 | 0.04 | 1.5 GB | 1.0 GB |
| Paraformer | 41.3% | 0.08 | — | 0.6 GB | 0 GB |

选型：有 8 GB 显卡综合选 medium、病理选 large-v3/Qwen3；无显卡 8 核选 small，4 核或低内存选 SenseVoice/Paraformer；噪声场景优先 Qwen3，large-v3 需前置降噪。

> 上表是研究期基准（9 档模型），**发布包只内置 small**，另以扩展包提供 Qwen3；其余档位不再随包分发。

### 环境变量

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `MSHK_HOST` / `MSHK_PORT` | `127.0.0.1` / `8100` | 集成层监听地址 |
| `MSHK_ASR_URL` | `http://127.0.0.1:18000` | ASR 服务地址 |
| `MSHK_ENGINE_URL` | `http://127.0.0.1:8790` | 报告引擎地址 |
| `MSHK_ASR_USER` / `MSHK_ASR_PASSWORD` | `demo_doctor` / `MedScribe2026` | ASR 账号；首次登录失败会自动 `POST /auth/register` |
| `MSHK_TERMS_DIR` | `terms/` | 术语目录（须同时含 `seed_terms.json` 与 `extraction_rules.json`） |
| `MSHK_DATA_DIR` | `data/` | SQLite / 音频 / 导出 |
| `MSHK_MIN_AUDIO_SEC` / `MSHK_MAX_AUDIO_SEC` | `10` / `900` | 录音时长阈值（BR-01） |
| `MSHK_LOW_CONF` | `0.6` | 低置信度阈值（BR-09） |
| `MSHK_SILENCE_RMS` | `300` | 静音判定 RMS 阈值（AC-11） |
| `MSHK_DEIDENTIFY` | `0` | 是否让引擎做脱敏；默认关闭（其 NER 会把「病人今日發熱」整句误判成姓名） |

`scripts/start_all.py` 用 **Python subprocess + 显式 env** 拉起上游，避免 `set VAR=... && start`
污染当前 shell，也保证引擎只看到自己的 `MSHK_TERMS_DIR` / `MSHK_DATA_DIR`。
启动前会用 `service_identity.py` 校验端口上的服务身份（`/health` 形状 + 进程命令行）：
陌生服务会中止启动；`stop_all.py` 同理拒绝误杀。

---

## 2. 目录结构

```
integration/
├─ app/
│  ├─ main.py           FastAPI 路由 + 异常映射（404/400/502）
│  ├─ service.py        流程编排、状态机守卫、BR-06/09/10/11 实现
│  ├─ schemas.py        Session / Segment / 请求模型
│  ├─ db.py             SQLite（会话/分段/要素/纠正/报告/导出/审计）
│  ├─ audio.py          ffmpeg 转码 16k 单声道 + 时长/静音校验
│  ├─ asr_client.py     ASR 适配：JWT 自动登录、分段映射、置信度代理
│  ├─ textsplit.py      长段落按句/短句切分，供引擎按句归类要素
│  ├─ postprocess.py    引擎输出后处理：把落错字段的体征内容归位
│  ├─ suggest.py        缺失必填字段的候选建议（主诉 / 评估线索），仅供人工采纳
│  ├─ history.py        会话列表的关键词/状态/时间筛选（纯函数，可单测）
│  ├─ engine_client.py  报告引擎适配
│  ├─ terms.py          xls 编译（保守策略）+ 可撤销纠正算法
│  ├─ tc_sc.py          繁→简折叠表（仅用于匹配，不改写原文）
│  └─ static/            单页界面：index.html + app.css / app-extra.css / app.js
│                        （archive/ 下保存 2026-10-29 旧界面，仅作回溯）
├─ terms/               seed_terms.json / glossary_full.json / import_report.json
│                       extraction_rules.json / overrides.json（本地补丁层）
├─ scripts/             import_terms.py / run_engine.py / start_all.py / stop_all.py
│                       run_tests.py（统一测试入口）/ service_identity.py（端口身份探测）
├─ tests/test_integration.py
├─ data/               运行时生成
├─ run.py  start.bat  stop.bat
```

---

## 3. API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 单页界面 |
| GET | `/health` | 三段链路探测（集成层 / ASR / 引擎 / 术语库） |
| GET | `/health/live` | 集成层存活探针，不聚合上游；供启动与身份校验使用 |
| POST | `/sessions` | 新建会话 `{template_id?, title?}` |
| GET | `/sessions` | 历史会话列表（FR-10），支持 `q`（标题/会话号/报告号/报告全文）、`status`（`signed`/`draft`/`none`）、`days`（最近 N 天）、`limit` |
| GET | `/sessions/{id}` | 会话全量状态（含分段、要素、纠正 diff、报告、导出） |
| DELETE | `/sessions/{id}` | 级联删除音频/报告/导出（BR-12） |
| POST | `/sessions/{id}/audio` | 上传录音（multipart `file`），返回时长/静音/短音频告警 |
| GET | `/sessions/{id}/audio` | 回放音频（FR-07 音频跳转） |
| POST | `/sessions/{id}/transcribe` | 转写 `{language?, beam_size?, template?}` |
| PATCH | `/sessions/{id}/segments/{seg_id}` | 人工修正 `{text?, confirmed?}`（BR-09 核对） |
| POST | `/sessions/{id}/correct-terms` | 术语纠正，返回 diff + warnings |
| POST | `/sessions/{id}/correct-terms/undo` | 一键撤销（回到纠正前文本，保留人工编辑） |
| POST | `/sessions/{id}/extract` | 抽取 S/O/A/P 要素 `{deidentify?}` |
| POST | `/sessions/{id}/generate` | 生成初稿 `{template_id?}` |
| GET | `/sessions/{id}/report` | 当前报告快照 |
| GET | `/templates` | 可用模板（引擎不可达时回退内置列表） |
| PATCH | `/reports/{report_id}/fields/{key}` | 字段校审 `{action: edit|accept|flag, value?, reviewer?}` |
| POST | `/reports/{report_id}/sign` | 签署 `{signed_by}`（BR-10 必填校验） |
| POST | `/reports/{report_id}/export` | 导出 `{formats: ["text","json","pdf"]}`（缺依赖的格式自动跳过并在 `skipped` 说明） |
| GET | `/reports/{report_id}/preview` | 导出前预览 |
| GET | `/exports/{filename}` | 下载导出文件 |
| GET | `/api/glossary` | 术语库全集查阅（含未参与自动替换的条目） |
| POST | `/api/reload-terms` | 重载术语库并通知引擎 `/api/reload` |
| GET | `/api/audit` | 审计事件（可按 session 过滤） |

会话状态机（只允许前进，签署后锁定）：

```
created → recorded → transcribed → corrected → extracted → generated → signed → exported
```

---

## 4. 术语库导入策略（保守导入）

`scripts/import_terms.py` 把 `医学术语库_v2.xls` 编译成两份产物：

* `terms/seed_terms.json` —— 引擎契约格式，**只含可自动替换**的条目；
* `terms/glossary_full.json` —— 全部 602 条 + 每条 `auto_replace` / `skip_reasons`，供界面查阅与审计。

自动替换准入（`app/terms.py:compile_terms`）：

1. 有英文规范名；
2. 术语类型不属于 时间(TME)/程度(SEV)/否定(NEG)/疑似(UNC)/文书(DOC)/角色(ROL)/科室(DEP)/给药途径(DOS)/病史(HIS)；
3. 别名长度 ≥ 2（纯 ASCII 别名 ≥ 3）；
4. 不命中泛词停用词表（今日 / 有 / 病人 / 轻度 …）；
5. 同一别名不指向两个不同规范名（歧义即整体弃用，如 `血压高` 同时指向 Hypertension 与 Elevated blood pressure）。

实测结果（`terms/import_report.json`）：

```
source_rows=602  entries_auto_replace=436  alias_total=752
skip: TME 38 / DEP 35 / SEV 26 / too_short 24 / ambiguous 20 / DOS 20 / DOC 18 / HIS 15 / NEG 6 / UNC 3 / ROL 2
```

### 本地覆盖层（`terms/overrides.json`）

源文件是外部资产，不改它；需要修正的地方写在本地补丁里，导入时叠加：

```json
{
  "version": "2026-10-29-local.1",
  "add_terms":    [{"term_id": "LOCAL-DRUG-001", "zh": "阿司匹林", "en": "Aspirin", "yue": "阿斯匹林", "type_code": "DRU"}],
  "add_aliases":  [{"term_id": "LAB-003", "alias": "呼吸頻率"}],
  "drop_aliases": [{"term_id": "LAB-003", "alias": "呼吸"}]
}
```

* 三种操作：新增术语、给已有术语加别名、移除过宽别名；
* 每条改动都会记进 `import_report.json` 的 `overrides` 段（NFR-10 可追溯）；
* 冲突（重复 term_id）、找不到的 term_id/别名不会被静默忽略，而是写 `warnings` 并跳过。

当前补了两处源数据问题：

| 问题 | 处理 |
| --- | --- |
| 源库没有「阿司匹林」条目，`阿斯匹林` 不会被纠正 | 新增 `LOCAL-DRUG-001`（Aspirin），别名含 阿斯匹林/亚士匹灵/阿士匹灵 |
| `LAB-003`（呼吸频率）的粤语别名是「呼吸」，会把「呼吸音粗」误伤 | 移除该别名，改为「呼吸頻率」 |

重新生成：`python scripts/import_terms.py --overrides terms/overrides.json`（默认就是这个路径）。

### 繁简折叠

粤语 ASR 输出多为**繁体**，而术语库中文名是**简体**。`app/tc_sc.py` 提供长度守恒的逐字折叠表，
`TermCorrector` 在**折叠后的副本**上定位别名，再按下标回填原文本：

* 命中才替换，替换目标是英文规范名；
* 未命中的字符一个不改（AC-04「不丢失内容」）；
* 命中繁体时会返回 warning，说明「按简体匹配，原文未被改写」。

### 纠正算法

与报告引擎 v0.1.0 `terminology.py` 对齐：

* 精确匹配优先，长别名优先（`高血壓` 先于 `高血壓性心臟病` 之外的短名）；
* 模糊匹配仅对词长 ≥ 4 的 token 生效：4–5 字容忍 1 个字符差异，≥ 6 字容忍 2 个；
* ASCII 别名加词边界，避免 `CBC` 命中 `CBCL`；
* 每次替换记入 `corrections` 表（before/after/rule/term_id），并写 `text_raw` = 纠正前文本。

### 送引擎时回滚纠正（重要）

引擎的要素抽取是**中文关键词规则**。实测把「咳嗽」换成 `Cough`、「抽血」换成 `Blood test`
之后，主诉/现病史/计划全部失配，整份报告只剩「检查结果」一项。因此：

* `service._engine_segments()` 在调用 `/api/generate` 前，按本会话 `corrections` 逆序把
  `after → before` 复原，送**纠正前的中文原文**给引擎；
* 本地纠正结果仍然保留在会话里，服务于 S2 的「对照、可撤销、审计」（FR-03）；
* 撤销过的纠正不会留在 `corrections`，因此人工编辑不会被回滚覆盖。

引擎自己也会做一轮术语替换（`term_correction` 参数在 v0.1.0 实测**无效**，传真传假输出一致），
报告里出现 `Cough` / `Blood test` 属于引擎行为，不是本地纠正残留。

### 长段落切分

Whisper 在无长停顿时会把整段话输出成**一个** segment（实测 25 秒粤语门诊独白 = 1 段 70+ 字、0 个句号）。
引擎按句归类，整段只能落到一个字段。`app/textsplit.py` 因此在转写后：

1. 按句末标点切；2. 单句 > 24 字再按逗号/顿号/分号切，过短碎片（<6 字）并入前句；
3. 时间戳按字数比例分摊（保证「点句子跳音频」大致对齐）；4. 顺带把 ASR 的小号标点（`﹐` `﹑`）统一成中文标点。

实测同一段音频：切分前 9 个字段里 8 个「未提及」，切分后现病史/既往史/体格检查/检查结果/计划均被填充。

### 引擎输出后处理：体征归位

引擎的客观段（O）会同时产出「体格检查」和「检查结果」，归类靠关键词命中顺序。实测
`體查體溫38度5` 被放进「检查结果」，而 `血壓160，95` 落在「体格检查」——医生会看到体温跑到化验单里。

`app/postprocess.py:rebalance_report()` 在 `/extract` 与 `/generate` 返回后做一次保守归位：

* 只处理 `investigations → physical_examination` 一个方向；
* 片段必须同时满足「含体征词（體溫/血壓/脈搏/血氧…）+ 含数字」且「不含化验/影像词（血常規/肝功能/抽血/X光…）」；
* 因此 `抽血檢查血常規和肝功能` 不会被误搬；
* 字段已有 `final_value`（人工改过）则完全不碰；
* 每次搬运写 `field_rebalanced` 审计事件，字段 `note` 里留痕，并重算 `missing_required`。

归位不能只改本地快照：引擎的 `/review`、`/sign` 会返回整份报告覆盖本地，导出与预览也直接读引擎。
所以 `service._push_rebalanced()` 会把新值用 `action=edit` 回写引擎（reviewer 记为 `system:rebalance`），
上游失败时降级为「只改本地 + `field_rebalance_sync_failed` 审计」。

引擎的 `effective_value()` 在 `final_value` 为空时回落 `ai_value`，也不接受空值 edit，
因此清空字段时写入占位符 `—`（`postprocess.EMPTY_MARK`），并在展示层用
`hide_rebalanced_source()` 把该字段的旧 `ai_value` 隐藏（原文仍留在 `reviews` 里可回溯）。

真实联调效果（导出正文）：

```
体格检查：160，95；體查體溫38度5
检查结果：—
```

### 缺失必填字段的建议（主诉 / 评估）

引擎靠口述标签判定字段（`extraction_rules.json` 的 markers：`主诉` / `診斷` / `評估` …），
自由口述时 `chief_complaint`、`assessment` 常为空，签署会被 BR-10 拦住。集成层做两件事：

1. **录音页提示口述顺序**：`主訴 → 現病史 → 查體 → 評估 → 計劃`（纯文案，不改变引擎判定）；
2. **空主诉给候选**：`app/suggest.py:suggest_missing()` 从现病史取前两个片段，去掉
   「病人/今日/我」等铺垫词，拼成 ≤24 字的候选，随 `GET /sessions/{id}` 的 `suggestions` 返回；
   界面显示「建议内容 + 采纳建议」，点击后走正常的 `action=edit` 校审（reviewer 是当前医生），
   **不会自动写入**——主诉属于临床判断，必须医生确认。

`assessment` 只给**参考线索**，不生成诊断：`assessment_hints()` 从现病史 / 既往史 /
体格检查 / 检查结果里匹配 `extraction_rules.json` A 段 score ≥ 1.2 的病名（元词如
`diagnosis`/`印象` 已排除），**跳过否定语境**——「否認肺炎」不会被当成肺炎。界面把命中项
列成可点的「可参考的病名」，点一下才以 `action=edit` 写入，医生仍要自己确认措辞与本次诊断归属。
报告头部的「必填缺失 N 项」也变成可点击链接，直接滚到第一个空字段并聚焦。

### 历史会话的搜索与筛选

单机跑久了列表会到几十条，左栏顶部提供三件套：

- **关键词**：`q` 同时匹配会话标题、会话号、报告号与**报告正文**（患者名、主诉、诊断都在正文里），
  输入 250ms 防抖后请求；
- **报告状态**：`signed`（已签）/ `draft`（生成了没签）/ `none`（还没生成报告），左栏用颜色区分；
- **时间窗**：按 `updated_at` 取最近 1/7/30 天。

实现上 `store.list_sessions_for_search()` 一次 join 出 `report_payload`，`app/history.py:filter_sessions()`
在 Python 侧过滤（会话量级小，且不依赖 SQLite 的 JSON 扩展），返回值会剥掉 `report_payload`，
只带一个派生字段 `report_status` 供前端上色。

---

## 5. 测试

```bat
python scripts/run_tests.py        # 40 项；-v 逐条输出，--failfast 首错即停
```

> 便携 Python 的 `._pth` 不会把当前目录加入 `sys.path`，所以不要直接
> `python -m unittest tests.test_integration`；`run_tests.py` 已固化路径与发现规则。

> 2026-11-02：ASR 转写与引擎响应都改用 `asr_client._read_body_retry`，对 Windows localhost
> 偶发的 `ConnectionResetError [WinError 10054]`（可能发生在建立连接之后的读取阶段）做有界重试。
> `run_tests.py` 连续 3 轮 40/40；另以 30 次连续 `/templates` 压测验证无 500。

40 个用例全部通过（进程内 ASR/引擎 stub + 真实 uvicorn + 真实 SQLite）：

| 用例 | 覆盖 |
| --- | --- |
| `TermStrategyTest.test_conservative_filter` | BR-04 准入/歧义/停用词，引擎词表只含 auto 条目 |
| `TermStrategyTest.test_corrector_exact_and_fuzzy` | 繁简折叠精确匹配、模糊匹配、规范写法不重复替换 |
| `TermStrategyTest.test_correct_segments_marks_raw` | `text_raw` 记录纠正前文本 |
| `TextSplitTest.test_short_segment_untouched` | 短句不被切碎，编号保持 S-001 |
| `TextSplitTest.test_long_segment_split_and_timed` | 长段落切分、编号连续、时间戳首尾对齐且无缝 |
| `TextSplitTest.test_small_comma_normalised` | ASR 小号逗号 `﹐` 归一 |
| `PostprocessTest.test_vital_moved_to_physical_exam` | 体温片段从「检查结果」搬到「体格检查」 |
| `PostprocessTest.test_lab_fragment_stays` | 化验片段留在「检查结果」，只搬体征那句 |
| `PostprocessTest.test_no_move_without_vital` | 无体征内容时不动报告 |
| `PostprocessTest.test_manual_edit_respected` | 人工已改（`final_value` 非空）时不归位 |
| `PostprocessTest.test_hide_placeholder_mark` | 清空字段的 `—` 占位在视图里隐藏旧 `ai_value` |
| `PostprocessTest.test_hide_rejected_source` | 系统 reject 的字段不在页面回显原文 |
| `PostprocessTest.test_hide_noop_for_manual_reject` | 医生自己的 reject 不受系统清洗影响 |
| `SuggestTest.test_chief_candidate_strips_noise` | 主诉候选去掉「病人/今日」等口述铺垫词 |
| `SuggestTest.test_chief_candidate_caps_length` | 主诉候选长度上限 24 字 |
| `SuggestTest.test_suggest_only_when_missing` | 只在主诉为空时给建议，已填不打扰 |
| `SuggestTest.test_assessment_hints_skip_negation` | 评估线索跳过否定句（「否認肺炎」不算肺炎）|
| `SuggestTest.test_assessment_suggest_only_when_empty` | 评估为空才给线索，已有值不打扰 |
| `SuggestTest.test_assessment_no_hint_without_disease` | 正文没有病名时不给评估线索 |
| `HistoryFilterTest.test_search_hits_report_body` | 关键词能命中报告正文（患者/主诉/诊断）|
| `HistoryFilterTest.test_status_buckets` | 报告状态归并为 signed / draft / none |
| `HistoryFilterTest.test_days_window` | 时间窗按 updated_at 过滤 |
| `HistoryFilterTest.test_newest_first_and_payload_stripped` | 最近在前，且不外泄报告原文 |
| `SuggestTest.test_suggest_when_engine_says_not_mentioned` | 引擎写「未提及」时也视为空并给候选 |
| `SuggestTest.test_suggest_empty_when_no_source` | 无现病史可依据时不给建议 |
| `TermOverrideTest.test_apply_overrides_semantics` | 新增术语/加别名/删别名三种补丁生效 |
| `TermOverrideTest.test_override_duplicate_is_warned` | 补丁冲突与找不到的别名只告警不静默失败 |
| `TermOverrideTest.test_built_terms_reflect_overrides` | 产物里 `呼吸` 已移除、`呼吸頻率`/`阿斯匹林` 已生效 |
| `TermOverrideTest.test_corrector_keeps_breath_sound` | 「呼吸音粗」→ Coarse breath sounds，不会被当成呼吸频率 |
| `ExportFormatTest.test_keeps_available_formats` | 可用格式原样透传 |
| `ExportFormatTest.test_drops_pdf_when_dependency_missing` | 缺 reportlab 时 pdf 被剔除而非整单失败 |
| `AudioRuleTest.test_silence_rejected` | AC-11 静音拒识 |
| `AudioRuleTest.test_short_audio_warns_but_passes` | BR-01 <10s 告警不阻断 |
| `AudioRuleTest.test_over_long_rejected` | BR-01 >15min 阻断 |
| `AudioRuleTest.test_normal_audio_ok` | 正常音频通过 |
| `EndToEndTest.test_health_reports_components` | 三段健康检查 |
| `EndToEndTest.test_full_flow` | S1→E 全链路：录音→转写→改→纠正→撤销→再纠正→抽取→生成→校审→签署→导出→删除 |
| `EndToEndTest.test_low_confidence_gate_and_confirm` | BR-09 低置信度未核对片段被排除，确认后放行 |
| `EndToEndTest.test_export_blocked_before_sign` | BR-10 必填缺失阻止签署、导出被拒 |
| `EndToEndTest.test_delete_session_cascades` | BR-12 级联删除 |

---

## 6. 已知行为与限制

1. **ASR 不返回逐段置信度**：适配层用全局 `language_probability` 作为代理置信度
   （`confidence_source="asr-global"`），因此 BR-09 的低置信度提示在真实上游上是「整段音频级」而非「每句级」。
   引擎返回逐段置信度后可平滑替换。
2. **模糊纠正仍可能过度替换**：编辑距离 1 对 4 字词就等于 25% 不同，保守策略已把别名限制在确定性强的集合；
   如遇误替换，可用「撤销」回到纠正前文本。严格度调整入口在 `terms.py` 的 `_fuzzy_limit` / `TYPE_NOT_AUTO`。
3. **`呼吸 → Respiratory rate` 的误伤已修**：源库把 `呼吸` 当作 Respiratory rate（lab-003）的别名，
   会把行文里的「雙肺呼吸音粗」改成呼吸频率。现已通过 `terms/overrides.json` 的 `drop_aliases`
   移除该别名，并补上 `呼吸頻率`；`呼吸音粗` 走自己的词条 → `Coarse breath sounds`。
   注意上游引擎使用它自己 `terms/` 下的词表，若不重启引擎仍会复现。
4. **`阿斯匹林` 已补进词表**：源库没有「阿司匹林」条目，覆盖层新增 `LOCAL-DRUG-001`（Aspirin），
   别名含 阿斯匹林/亚士匹灵/拜阿司匹灵/阿士匹灵，按精确别名直接替换，不依赖模糊匹配。
5. **「主诉」「评估」最好口述标签**：引擎靠 `主訴/主诉`、`診斷/評估` 等关键词判定，
   纯自由口述会落到现病史或留空（实测 `chief_complaint` / `assessment` 为「未提及」，签署被 BR-10 拦住）。
   界面已提示口述顺序；空主诉给可采纳的候选，空评估给正文里出现过的病名作参考线索（都需医生点一下才写入），
   详见第 4 节。评估仍不会自动生成——诊断归属由医生判断。
6. **PDF 导出已可用**：`reportlab 5.1.0` 已装进便携 Python，导出用 `STSong-Light` CID 字体渲染中文
   （不嵌入字体文件，依赖阅读器的 CJK 支持；Acrobat/Chrome 正常）。换到未装 reportlab 的环境时，
   `/export` 会自动**跳过 pdf** 并在响应 `skipped` 里说明原因，`text`/`json` 照常产出，不会整单失败。
7. **签署后全流程锁定**（BR-11）：分段编辑、术语纠正、字段修改均返回 400。
8. **报告快照以引擎为准**：集成层只缓存最近一次 `/api/generate` 的响应；引擎重启后旧 `report_id` 会失效（404）。
   后处理归位会同时回写引擎（见第 4 节），因此缓存、引擎、导出三处一致。
9. **术语库 `extraction_rules.json` 为引擎必需**，`start_all.py` 会把它作为 `MSHK_TERMS_DIR` 的一部分传入；
   缺文件时引擎启动即失败（`seed_terms.json` 同理）。
10. **引擎把「未提及」当作已填内容**：非必填字段缺失时 `missing_required` 不会列它，
   因此签署前的必填校验只拦真正必填的项（这是引擎契约，集成层不重复实现）。
11. **`scripts/run_engine.py` 是必需的启动垫片**：医疗便携 Python 的 `python312._pth` 把
   `sys.path` 写死（不含脚本目录），直接 `python run.py` 会让引擎的 `import app.config`
   命中 ASR 的同名包并 ImportError。
12. **单人本地部署**：未实现多用户鉴权与并发写入隔离（SQLite + `RLock` 已足够单机使用）。
13. **对上游引擎有一处小修**：`report-produce/.../app/service.py` 的 `sign()` 在签署成功后
    清掉生成期遗留的「签署与导出已被阻断」提示行（必填已确认，提示会过期），
    否则导出的正文会自相矛盾。逻辑未变，仅同步显示状态。

---

## 7. 重建术语库

```bat
python scripts/import_terms.py ^
  --xls "..\uploads\ses_...\医学术语库_v2.xls" ^
  --out terms ^
  --rules "..\report-produce\书面报告生成插件（含术语纠正）\terms\extraction_rules.json" ^
  --overrides terms\overrides.json ^
  --version 2026-10-29
```

覆盖层是本地补丁文件，`--overrides` 省略时默认读 `terms/overrides.json`（不存在则只是纯导入）。
`xlrd` 已装进便携 Python（2.0.2），以前的 `--pylib` 离线目录参数不再需要；换机器时按 `requirements.txt` 装依赖即可。

`--pylib` 仍保留，指向解压后的 `xlrd`（完全离线的机器上用），日常不需要。脚本会记录源文件 sha256 到
`terms/import_report.json`，便于核对数据版本。
