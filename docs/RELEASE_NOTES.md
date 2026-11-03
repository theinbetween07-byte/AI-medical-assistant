# MedScribe HK v1.0.0

香港临床口述病历助手首个可分发版本：把粤语 / 英语 / 中英混合口述整理为可校审的结构化病历初稿。
全部推理在本机完成，不上传音频、不联网下载模型。

## 下载内容

| 资产 | 说明 | 分卷 | 下载 | 解压后 |
| --- | --- | --- | --- | --- |
| `MedScribe-base-v1.0.0.7z.001`–`.003` | 基础包：便携 Python 3.12 + 界面 + 报告引擎 + Whisper small + ffmpeg | 3 | 0.52 GB | 908 MB |
| `MedScribe-ext-caps-qwen3-v1.0.0.7z.001`–`.013` | 可选扩展：Qwen3-ASR（CapsWriter-Offline） | 13 | 2.23 GB | 2.9 GB |
| `SHA256SUMS.txt` | 全部分卷的 SHA256 | — | — | — |

## 安装

1. 把基础包的 3 个分卷放在同一文件夹，用 7-Zip 解压 `.001`（会自动合并其余分卷），得到 `MedScribe` 文件夹。
2. 双击 `MedScribe\MedScribe.bat`，等控制台显示三段服务就绪（首次 10–30 秒）。
3. 浏览器自动打开 <http://127.0.0.1:8100>。关闭控制台或双击 `停止.bat` 停服。

安装扩展（可选，用于病理长术语等高精度场景）：

1. 解压扩展包 `.001` 得到 `MedScribe-ext`，把其中的 `app` 复制到 `MedScribe\` 根目录并合并。
2. 重启 `MedScribe.bat`，在工具条「模型」选择 **Qwen3-ASR**。首次转写加载模型约 20–60 秒。

未安装扩展时，下拉中该项显示「Qwen3-ASR · 未安装」，点击弹出下载地址与步骤，不会联网下载。

## 校验

```powershell
certutil -hashfile MedScribe-base-v1.0.0.7z.001 SHA256
```

与 `SHA256SUMS.txt` 对应行比对。

## 实测数据

| 场景 | Whisper small（内置） | Qwen3-ASR（扩展） |
| --- | --- | --- |
| 43.4 s 英文 SOAP | 8.1 s，13 段 | — |
| 90 s 英文病理 | 错误率 9.09% | 35.3 s，32 段，错误率 3.64% |
| 中文门诊 | CER 13.76%–20.11% | — |

基准为合成语料、每场景 1 条，方向性参考；77 次运行明细见仓库 `outputs/ui-integration/asr-benchmark/REPORT.md`。

## 系统要求

- Windows 10 / 11 64 位
- 4 GB 内存（扩展建议 8 GB）
- 磁盘：基础包 908 MB；扩展再加 2.9 GB
- 显卡：不需要，默认 CPU int8；扩展可选 Vulkan 加速

## 已知限制

- 未接真实患者数据、未连 EHR；报告必须由有资质医师逐项核对签署。
- 粤语真人、中英混合、真实噪声未覆盖，现有数据为 SAPI 合成语料。
- 扩展引擎以 PyInstaller 打包，未在无关机验证过运行库兼容性。
- Windows localhost 偶发连接重置，已做有界重试，仍可能重试耗尽后报错。
- 病理报告有两处标签内部切分问题（`tumour_grade` 为空、`tumour_type` 混入分级），待修。

## 隐私

音频转码、ASR、模板抽取全部在本机完成；除用户主动导出文件外不产生外部请求。
