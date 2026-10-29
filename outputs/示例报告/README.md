# 示例报告（真实运行产物）

本目录是报告生成引擎的**实测产物**，不是手工画的样例。每份都来自一条完整链路：
合成模拟口述 → 要素抽取 → 模板化初稿 → 逐字段校审（采纳）→ 签署 → 导出四格式。

## 目录结构

```
outputs/示例报告/
├─ pathology-colorectal/           外科病理報告（結直腸）—— 与使用者提供的 SampleReport 格式对齐
│  ├─ RPT-*.txt                    纯文本报告（英文，行样式与样本一致）
│  ├─ RPT-*.pdf                    打印版 PDF（A4 表格 + 来源/告警）
│  ├─ RPT-*.html                   网页预览版（可直接双击打开）
│  ├─ RPT-*.json                   机器可读全量数据（字段 / 来源时间点 / 审计 / 校审记录）
│  ├─ smoke_demo_pathology_colorectal.txt   全链路日志（抽取要素 → 字段归位 → 门禁 → 导出）
│  └─ _work/superseded/            上一轮产物备份（不参与交付）
└─ general-soap/                   通用 SOAP 记录（默认兜底模板，BR-06）
   ├─ RPT-*.txt / .pdf / .html / .json
   ├─ smoke_demo_general_soap.txt
   └─ _work/superseded/
```

## 病理报告（pathology-colorectal）看点

- 口述 27 句 → **20 个字段全部归位**，缺失必填 = 0；
- 17 行 synoptic 逐行「`标签: 值`」，与样本排版一致（`Tumour site: sigmoid colon` … `Circumferential margin: free of tumour, 1.2 cm`）；
- 三段叙述按标题路由：`MACROSCOPIC EXAMINATION:` / `MICROSCOPIC EXAMINATION:` / `DIAGNOSIS:`，各段互不串内容；
- `DIAGNOSIS` 段模拟「医生没口述诊断」的情形：由模板按 5 个已抽取值**自动拼装**成一句诊断，并带
  `[auto-generated]`（中文导出为 `[自动生成，请核对]`）标记 + 来源引用，医生核对确认后才可签署：
  `Sigmoid colon, laparoscopic anterior resection: moderately differentiated adenocarcinoma, not otherwise specified, tumour invades through muscularis propria into subserosa.`
  缺关键项（如仅口述到分型）时**不生成**，如实留占位符并继续阻断签署（BR-03）；
- 英文模式导出：表头 `Template v1.0.0 · Generated … · Status signed`、占位符 `Not mentioned`，正文无中文残留；
- 未口述的结构化字段一律写占位符并进入缺失清单，**绝不编造**（BR-03）；
- `RPT-*.json` 里每个字段都带 `sources[].start_ms / end_ms`，可回溯到口述的具体时间点（NFR-12）。

## 通用 SOAP（general-soap）看点

- 9 字段（主诉 / 现病史 / 体格检查 / 处理计划 …），中英混合口述；
- "处理计划"字段带 `[低置信度，请核对]` 标记，演示 BR-09；
- 未口述的必填字段（体格检查）在报告里显示「未提及」，签署前被门禁拦住（BR-10）。

## 复现方法

```bat
:: 在仓库根目录执行
report-engine\.venv\Scripts\python.exe scripts\smoke_demo.py "outputs\示例报告\pathology-colorectal" --template pathology_colorectal
report-engine\.venv\Scripts\python.exe scripts\smoke_demo.py "outputs\示例报告\general-soap"        --template general_soap
```

> 全部内容为**合成模拟数据**（`[SYNTHETIC]`），不含任何真实病人信息；
> 使用者提供的真实样本仅用于确定字段清单，其原文与扫描图不进入交付物。
