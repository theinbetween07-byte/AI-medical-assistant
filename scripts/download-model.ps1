# 下載 Whisper 模型到本地
#
# 用法：
#   .\scripts\download-model.ps1 -Model medium
#
# 已下載的模型會出現在左側「模型」下拉的「本地」分組

param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("tiny","base","small","medium","large-v3","distil-large-v3")]
    [string]$Model
)

$ErrorActionPreference = "Stop"

$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$TARGET = Join-Path $ROOT "models\faster-whisper-$Model"

if (Test-Path "$TARGET\config.json") {
    Write-Host "[✓] $Model 已存在於 $TARGET" -ForegroundColor Green
    exit 0
}

Write-Host "[1/3] 創建目錄 $TARGET ..."
New-Item -ItemType Directory -Path $TARGET -Force | Out-Null

Write-Host "[2/3] 從 HuggingFace 下載 $Model ..."
Write-Host "      （~1.5GB，請耐心等待）"

$env:HF_HOME = Join-Path $ROOT "models\.hf_cache"
$py = Join-Path $ROOT "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "[✗] 找不到 Python venv：$py" -ForegroundColor Red
    Write-Host "    請先運行 start.bat 建立 venv" -ForegroundColor Red
    exit 1
}

& $py -c "
from huggingface_hub import snapshot_download
import os
os.environ['HF_HUB_ENABLE_HF_TRANSFER'] = '1'
path = snapshot_download(
    repo_id='Systran/faster-whisper-$Model',
    local_dir=r'$TARGET',
    local_dir_use_symlinks=False,
    allow_patterns=['*.json', '*.txt', '*.bin', 'tokenizer.json', 'vocabulary.txt', 'README.md'],
)
print('下載完成：', path)
" 2>&1 | Tee-Object -Variable pyOutput

if ($LASTEXITCODE -ne 0) {
    Write-Host "[✗] 下載失敗" -ForegroundColor Red
    exit 1
}

Write-Host "[3/3] 驗證 ..."
if (Test-Path "$TARGET\config.json") {
    $size = (Get-Item "$TARGET\model.bin").Length / 1GB
    Write-Host "[✓] $Model 下載完成（$([math]::Round($size, 2)) GB）" -ForegroundColor Green
    Write-Host "    路徑：$TARGET"
    Write-Host "    重啟後端即可在 UI 中選擇該型號"
} else {
    Write-Host "[✗] 驗證失敗，缺少 config.json" -ForegroundColor Red
    exit 1
}
