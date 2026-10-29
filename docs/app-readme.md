# AI 醫生助手 · Clinical Voice Assistant

> 香港公立醫院 AI 語音助手 MVP - 醫生口述 → 自動識別 → 結構化報告
> 本倉庫完全本地部署，音頻與文本不會離開您的機器

## 功能（v0.2）

- 🎙️ 粵/普/英 多語言語音識別（faster-whisper）
- 👤 本地用戶系統（註冊 / 登錄 / JWT token）
- 🔄 動態切換 Whisper 模型型號（tiny / base / small / medium / large-v3 / distil-large-v3）
- 📝 6 種報告模板（出院摘要 / 轉介信 / 手術記錄 / 會診記錄 / 病程記錄 / SOAP）
- 🏥 7 個專科選項（內 / 外 / 骨 / 兒 / 婦產 / 急症 / 心內）
- 🔐 個人中心 + 轉寫歷史審計

## 環境要求

| 依賴 | 版本 | 說明 |
|---|---|---|
| Python | 3.10+ | 後端 |
| Node.js | 18+ (推薦 24 LTS) | 前端 |
| ffmpeg | 最新版 | 音頻轉碼 |
| 磁盤空間 | 5 GB+ | 含 medium 模型 1.5 GB |

## 快速開始（首次部署）

### 1. 安裝系統依賴

```powershell
# Python（如果沒有）
winget install -e --id Python.Python.3.12

# Node.js LTS（如果沒有）
winget install -e --id OpenJS.NodeJS.LTS

# ffmpeg（必須）
winget install -e --id Gyan.FFmpeg
```

### 2. 啟動

雙擊 `scripts\start.bat`，瀏覽器會自動打開 `http://localhost:5173`。

首次啟動會自動：
- 建立 Python 虛擬環境 `.venv`
- 安裝後端依賴（~2 分鐘）
- 安裝前端依賴（~1 分鐘）

### 3. 註冊 / 登錄

首次使用：
1. 點右上角「註冊」
2. 輸入用戶名、密碼（≥6 位）、顯示名稱、選擇角色
3. 註冊成功後自動登錄，進入主頁

### 4. 錄音識別

1. 左側選擇「報告模板」「語言」「模型」
2. 點「開始錄音」錄製 5-10 秒
3. 點「提交識別」等待 1-2 分鐘
4. 右側顯示識別結果，可手動校對

### 5. 停止服務

雙擊 `scripts\stop.bat`。

## 目錄結構

```
medical-ai-assistant/
├── backend/              # FastAPI + faster-whisper
│   ├── .venv/            # Python 虛擬環境（首次自動創建）
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── db.py         # SQLite 用戶表
│   │   ├── auth.py       # bcrypt + JWT
│   │   ├── api/
│   │   │   ├── asr_router.py
│   │   │   └── auth_router.py
│   │   └── services/
│   │       └── asr.py    # Whisper 服務（支持動態切換）
│   ├── data/             # users.db + .jwt_secret
│   ├── logs/             # 每日輪轉
│   └── requirements.txt
├── frontend/             # Vue 3 + Vite + Element Plus
│   ├── node_modules/
│   ├── src/
│   │   ├── App.vue       # 頂欄 + 用戶菜單
│   │   ├── views/
│   │   │   ├── HomeView.vue      # 主頁（錄音 + 識別）
│   │   │   ├── LoginView.vue
│   │   │   └── RegisterView.vue
│   │   ├── stores/user.js        # 用戶狀態（持久化到 localStorage）
│   │   ├── api/{auth.js, config.js}
│   │   ├── router/index.js
│   │   └── components/{RecorderPanel, ReportTemplate, TranscriptPanel, StatusBar}.vue
│   └── package.json
├── models/               # Whisper 模型（首次需下載或手動放置）
│   └── faster-whisper-medium/    # 1.5 GB
├── scripts/              # 啟動與工具腳本
│   ├── start.bat         # 一鍵啟動
│   ├── stop.bat          # 停止服務
│   ├── launch.py         # 後台啟動器
│   └── download-model.ps1
├── docs/                 # 項目文檔
└── .tmp/                 # 運行時日誌和 PID 文件
```

## 模型管理

### 列出已下載模型

```
GET /asr/models
```

返回：
```json
{
  "supported": ["tiny", "base", "small", "medium", "large-v3", "distil-large-v3"],
  "local": ["medium"],
  "current": "medium"
}
```

### 切換模型

```
POST /asr/load-model?model=medium
```

- 本地有的型號：秒級切換
- 本地沒有的型號：首次會從 HuggingFace 下載（耗時幾分鐘）

### 手動下載模型

```powershell
# 方法 A：通過前端 UI 切換到未下載的型號（會自動下載）
# 方法 B：運行下載腳本
.\scripts\download-model.ps1 -Model medium
```

## 開發命令

```powershell
# 僅啟動後端（開發模式）
cd backend
.\.venv\Scripts\python.exe -m app.main

# 僅啟動前端（開發模式）
cd frontend
npm run dev

# 後端日誌
Get-Content .tmp\backend.log -Tail 50 -Wait

# Swagger 文檔
http://localhost:8000/docs
```

## API 一覽

| 方法 | 路徑 | 說明 | 需登錄 |
|---|---|---|---|
| GET | `/health` | 健康檢查 | ✗ |
| POST | `/auth/register` | 註冊 | ✗ |
| POST | `/auth/login` | 登錄（返回 JWT） | ✗ |
| GET | `/auth/me` | 當前用戶信息 | ✓ |
| GET | `/auth/logs` | 我的轉寫記錄 | ✓ |
| GET | `/asr/models` | 列出可用型號 | ✗ |
| POST | `/asr/load-model?model=...` | 切換模型 | ✓ |
| POST | `/asr/transcribe` | 上傳音頻並轉寫 | ✓ |
| POST | `/asr/warmup` | 預熱當前模型 | ✓ |

## 已知限制

- **首次冷啟動慢**：medium 模型加載 ~3 秒；首次識別需 ~30 秒預熱
- **CPU only**：當前測試在 CPU 上跑，medium ~15× 即時。如果醫院有 GPU，可改 `WHISPER_DEVICE=cuda`
- **未實現**：LLM 報告生成（第三步）/ 醫學術語詞典微調 / 用戶權限分級管理

## 路線圖

- [x] ASR 識別（v0.1）
- [x] 用戶系統 + 模型動態切換（v0.2）
- [ ] LLM SOAP 結構化報告（v0.3）
- [ ] 醫學術語詞典 + 後處理（v0.4）
- [ ] 病歷對接（FHIR / HA Go）（v1.0）

## License

內部使用 · 僅限香港公立醫院
