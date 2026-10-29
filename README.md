# AI Medical Assistant

把医生的**口语化口述转写**，生成**可直接核签的书面报告**（本地运行，不外联）。

当前实现：`report-engine` —— 报告生成引擎（Python / FastAPI），已支持三类报告模板：

| 模板 | 场景 | 字段 |
| --- | --- | --- |
| `pathology_colorectal` | 外科病理报告（結直腸） | 20 个（17 必填） |
| `general_soap` | 通用 SOAP 记录 | SOAP 四段 |
| `outpatient_record` | 门诊病历 | 结构化字段 |

## 仓库结构

```
AI-medical-assistant/
├─ backend/                 桌面应用后端（Python / FastAPI + faster-whisper）
│  ├─ app/                  main / config / auth / db / models
│  │  ├─ api/               auth_router / asr_router
│  │  └─ services/          asr.py（Whisper 服务，支持动态切换模型）
│  ├─ scripts/              download_model.py
│  └─ requirements.txt
├─ frontend/                桌面应用前端（Vue 3 + Vite + Element Plus）
│  ├─ src/                  views / components / composables / stores / router
│  ├─ index.html
│  ├─ package.json
│  └─ vite.config.js
├─ report-engine/           报告生成引擎（源码）
│  ├─ app/                  抽取 → 生成 → 校验 → 导出 → 服务
│  ├─ templates/            声明式报告模板（JSON，可直接改）
│  ├─ tests/                22 项自动化测试
│  ├─ build/                PyInstaller 打包配置（二进制不入库）
│  └─ run.py                启动入口
├─ scripts/                 启动与工具脚本
│  ├─ start.bat             一键启动（Windows）
│  ├─ stop.bat              停止服务
│  ├─ launch.py             后台启动器（DETACHED_PROCESS，见文件头注释）
│  ├─ download-model.ps1    Whisper 模型下载
│  ├─ smoke_demo.py         报告引擎：命令行全链路冒烟
│  └─ http_smoke.py         报告引擎：HTTP 全链路冒烟
├─ docs/                    项目文档
│  ├─ prd-mvp.md            MVP 产品需求文档
│  ├─ project-proposal.md   项目提案
│  ├─ technical-design.md   技术设计
│  ├─ SOAP-rules-v1.md      SOAP 报告书写规则
│  ├─ app-readme.md         桌面应用说明（架构 / API / 开发命令）
│  ├─ app-quick-start.txt   便携版使用说明（面向最终使用者）
│  ├─ report-engine.md      报告生成引擎说明
│  ├─ report-engine-接入与交付说明.md
│  └─ 病理报告模板设计说明.md
├─ data/                    静态数据
│  ├─ medical-terms-v2.xls  医学词典
│  └─ terms/                抽取规则与术语库（可热改，引擎自动加载）
├─ outputs/                 交付示例报告
│  └─ 示例报告/            TXT / HTML / PDF / JSON 成品
└─ README.md                本文件
```

> 不入库（`.gitignore` 已屏蔽）：`models/`（Whisper 模型）、`node/`、`backend/python/`、
> `backend/ffmpeg.exe`、`frontend/node_modules/`、`backend/data/`（用户库与密钥）、`.tmp/`、`backend/logs/`。

## 快速开始（桌面应用）

桌面应用由 `backend`（FastAPI + faster-whisper）与 `frontend`（Vue 3 + Vite）组成：
前端 `http://localhost:5173`，后端 `http://localhost:8000`（API 文档 `/docs`）。

```powershell
# 1) 安装依赖
python -m venv backend\.venv
backend\.venv\Scripts\activate
pip install -r backend\requirements.txt

cd frontend
npm install
cd ..

# 2) 一键启动 / 停止（后端 + 前端）
scripts\start.bat
scripts\stop.bat
```

> `scripts\start.bat` 与 `scripts\launch.py` 依赖便携版打包的运行时目录（`backend\python\`、`node\`、
> `models\`、`backend\ffmpeg.exe`），这些目录**不入库**，需自行准备。
> 详见 [docs/app-readme.md](docs/app-readme.md) 与 [docs/app-quick-start.txt](docs/app-quick-start.txt)。

## 快速开始（report-engine）

```bash
cd report-engine
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
python run.py                     # 默认 http://127.0.0.1:8790
```

启动后浏览器会打开操作界面：点「载入示例」→ 松开手看字段抽取 → 逐字段「采纳/编辑」→ 签署 → 导出。

打包成单文件 exe：

```bash
pip install -r requirements-dev.txt
python -m PyInstaller --noconfirm --distpath dist --workpath build/pyi build/report_engine.spec
dist\medscribe-report-engine.exe --port 0 --port-file port.txt
```

## 验证

```bash
# 单元测试（在引擎目录内执行）
cd report-engine
.venv\Scripts\python.exe -m pytest -q                                            # 22 passed
cd ..

# 冒烟（在仓库根目录执行）
report-engine\.venv\Scripts\python.exe scripts\smoke_demo.py                                     # SOAP 全链路
report-engine\.venv\Scripts\python.exe scripts\smoke_demo.py out --template pathology_colorectal  # 病理全链路
report-engine\.venv\Scripts\python.exe scripts\http_smoke.py                                     # HTTP 全链路
report-engine\.venv\Scripts\python.exe scripts\http_smoke.py --exe report-engine\dist\medscribe-report-engine.exe
```

## 设计要点

- **不编造**：未被口述覆盖的字段一律写占位符并进入缺失清单，阻断签署（BR-03 / BR-10）；
- **可追溯**：每个字段带 `sources[].start_ms / end_ms`，能回到口述的具体时间点；
- **自动生成要标记**：诊断句等派生内容按模板 `compose` 拼装，带「自动生成，请核对」标记与来源引用，医生确认后才可签署；
- **模板声明式**：改报告格式只改 `templates/*.json`，不动代码；
- **默认离线**：LLM 默认关闭（`MSHK_LLM_*` 才启用），服务仅监听本机。

## 隐私与合规声明

- 本仓库**不包含任何真实病患数据**：示例报告全部由合成口述生成，`.gitignore` 已屏蔽上传的原始样本、标本照片、本地数据库；
- 项目用于技术验证与研究，**不作为医疗器械**，生成的报告必须由有资质的医师逐项核对并签署；
- 若用于真实临床环境，需另行完成数据合规、权限与审计要求。

## 文档

- MVP 需求：[docs/prd-mvp.md](docs/prd-mvp.md)
- 项目提案：[docs/project-proposal.md](docs/project-proposal.md)
- 技术设计：[docs/technical-design.md](docs/technical-design.md)
- SOAP 规则：[docs/SOAP-rules-v1.md](docs/SOAP-rules-v1.md)
- 桌面应用说明：[docs/app-readme.md](docs/app-readme.md)
- 引擎说明：[docs/report-engine.md](docs/report-engine.md)
- 接入与交付：[docs/report-engine-接入与交付说明.md](docs/report-engine-接入与交付说明.md)
- 病理模板设计：[docs/病理报告模板设计说明.md](docs/病理报告模板设计说明.md)
- 示例报告：[outputs/示例报告/README.md](outputs/示例报告/README.md)
