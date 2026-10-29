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
├─ report-engine/          报告生成引擎（源码）
│  ├─ app/                 抽取 → 生成 → 校验 → 导出 → 服务
│  ├─ templates/           声明式报告模板（JSON，可直接改）
│  ├─ terms/               抽取规则与术语库（可热改）
│  ├─ tests/               22 项自动化测试
│  ├─ tools/               命令行冒烟 / HTTP 冒烟
│  ├─ build/               PyInstaller 打包配置（二进制不入库）
│  └─ run.py               启动入口
├─ outputs/                交付文档与示例报告
│  ├─ report-engine-接入与交付说明.md
│  ├─ 病理报告模板设计说明.md
│  └─ 示例报告/            TXT / HTML / PDF / JSON 成品
├─ SOAP规则_v1.md          报告书写规则
├─ mvp-prd.md / mvp ai medical assistant-prd.md / project-proposal.md / technical-design.md
└─ 医学术语库_v2.xls
```

## 快速开始

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
cd report-engine
.venv\Scripts\python.exe -m pytest -q                                            # 22 passed
.venv\Scripts\python.exe tools\smoke_demo.py                                     # SOAP 全链路
.venv\Scripts\python.exe tools\smoke_demo.py out --template pathology_colorectal  # 病理全链路
.venv\Scripts\python.exe tools\http_smoke.py                                     # HTTP 全链路
.venv\Scripts\python.exe tools\http_smoke.py --exe dist\medscribe-report-engine.exe
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

- 接入与交付：[outputs/report-engine-接入与交付说明.md](outputs/report-engine-接入与交付说明.md)
- 病理模板设计：[outputs/病理报告模板设计说明.md](outputs/病理报告模板设计说明.md)
- 示例报告：[outputs/示例报告/README.md](outputs/示例报告/README.md)
- 引擎说明：[report-engine/README.md](report-engine/README.md)
