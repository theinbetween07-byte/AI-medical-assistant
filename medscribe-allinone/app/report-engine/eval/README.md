# 字段级评测

合成语料 + 确定性比对，用来回答一个问题：**改了归属规则之后，字段是变对了还是只是换了一种错**。

## 用法

```powershell
# 在 app/report-engine 目录下
python eval/run_eval.py                    # 人读摘要
python eval/run_eval.py --json out.json    # 机器可读，供版本间对比
python eval/run_eval.py --verbose          # 打印每个字段的实际取值
```

## 语料格式（`corpus.json`）

```json
{
  "id": "soap-01",
  "template": "general_soap",
  "segments": ["Chief complaint: sore throat for three days.", "..."],
  "gold": {"chief_complaint": ["sore throat"]},
  "expect_missing": ["investigations"]
}
```

- `gold`：字段值**必须包含**的片段（大小写、空白、标点归一后做包含判断）。不要求逐字相等，避免因连接符差异产生假失败。
- `expect_missing`：该字段必须保持「未提及」，用来抓“把无关内容塞进结构化字段”。
- 每个 case 的 `segments` 一句一段，避免切句逻辑干扰归属评测。

## 指标

| 指标 | 含义 |
| --- | --- |
| `gold_fields` | gold 片段全部命中的字段数 / 总数 |
| `missing_ok` | `expect_missing` 字段确实为空的数量 |
| `leaks` | 应空却填了内容的结构化字段数量 |

## 边界

- 合成数据，非真实病例（NFR-05），不能代表真实口述的噪声分布与科室差异。
- 14 个用例只覆盖「标签直配、标题段落、同分区续写、引语、残句、中英粤」几条主路径，不覆盖 ASR 错字与长段落漂移。
- 它的价值是**版本间回归比较**（同一语料跑 v1.0.2 与 v1.0.3），不是绝对质量分。
