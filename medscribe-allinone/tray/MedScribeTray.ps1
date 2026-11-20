param(
    [string]$Root = ''
)

# MedScribe HK 托盘图标：启动服务、打开界面、停止服务、关于
# 单文件、无第三方依赖，只用 Windows 自带的 .NET / WinForms。
# 约定：脚本放在 <包根>\tray\MedScribeTray.ps1，<包根> 下是 MedScribe.bat、
# runtime\python、app\integration 等。

$ErrorActionPreference = 'Continue'
if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
$Root = (Resolve-Path -LiteralPath $Root).Path

$Py = Join-Path $Root 'runtime\python\python.exe'
$Integration = Join-Path $Root 'app\integration'
$Logs = Join-Path $Integration 'logs'
$PidFile = Join-Path $Logs 'pids.json'
$StateFile = Join-Path $Root 'tray\state.json'

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# ---------------------------------------------------------------- 单实例
$mutex = New-Object System.Threading.Mutex($false, 'Global\MedScribeTrayIcon')
if (-not $mutex.WaitOne(0)) { exit 0 }

function Test-PathLength {
    # PyAV 的 FFmpeg DLL 依賴受 Windows 260 字元路徑上限約束；
    # 安裝目錄太深時轉寫會在加載音頻庫時失敗，先讓使用者選一次。
    $len = $Root.TrimEnd('\').Length
    if ($len -le 120) { return $true }
    $text = @(
        "安装路径过长：$len 个字符",
        $Root,
        '',
        'Windows 对音频库 DLL 路径有 260 字符上限，路径过深会导致转写失败。',
        '建议把 MedScribe 文件夹移到 C:\MedScribe 这类短目录。',
        '',
        '仍要尝试启动吗？'
    ) -join "`r`n"
    $r = [System.Windows.Forms.MessageBox]::Show(
        $text, 'MedScribe HK', 'YesNo', 'Warning')
    return ($r -eq 'Yes')
}

function Get-IntegrationPort {
    try {
        $data = Get-Content -LiteralPath $PidFile -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json
        if ($data.ports -and $data.ports.integration) { return [int]$data.ports.integration }
    } catch { }
    return 8100
}

function Test-Alive([int]$Port) {
    try {
        $resp = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/health/live" -f $Port) `
            -TimeoutSec 3 -UseBasicParsing
        return ($resp.StatusCode -eq 200)
    } catch { return $false }
}

function Set-TrayState([string]$Text, [string]$Tip) {
    try {
        $obj = [ordered]@{ updated = (Get-Date).ToString('s'); status = $Text; tip = $Tip }
        [System.IO.File]::WriteAllText($StateFile, ($obj | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
    } catch { }
}

function Start-Services {
    if (-not (Test-PathLength)) { return }
    if (-not (Test-Path -LiteralPath $Py)) {
        [System.Windows.Forms.MessageBox]::Show(
            "找不到内置 Python：$Py`n请确认压缩包已完整解压。", 'MedScribe HK',
            'OK', 'Error') | Out-Null
        return
    }
    $port = Get-IntegrationPort
    if (Test-Alive $port) {
        Open-Interface
        return
    }
    New-Item -ItemType Directory -Force -Path $Logs | Out-Null

    # 与 MedScribe.bat 相同的环境变量；start_all.py 依赖它们定位上游目录
    $env:MSHK_PYTHON = $Py
    $env:MSHK_ASR_DIR = Join-Path $Root 'app\backend'
    $env:MSHK_ENGINE_DIR = Join-Path $Root 'app\report-engine'
    $env:FFMPEG_PATH = Join-Path $Root 'bin\ffmpeg.exe'
    $env:MSHK_FFMPEG = Join-Path $Root 'bin\ffmpeg.exe'
    $env:WHISPER_MODEL = 'small'
    $env:WHISPER_DEVICE = 'cpu'
    $env:WHISPER_COMPUTE_TYPE = 'int8'
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PATH = (Join-Path $Root 'bin') + ';' + $env:PATH

    $outLog = Join-Path $Logs 'tray-start.log'
    $errLog = Join-Path $Logs 'tray-start.err.log'
    $args = @('scripts\start_all.py', '--open')
    try {
        Start-Process -FilePath $Py -ArgumentList $args -WorkingDirectory $Integration `
            -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog | Out-Null
    } catch {
        [System.Windows.Forms.MessageBox]::Show("启动失败：$($_.Exception.Message)", 'MedScribe HK',
            'OK', 'Error') | Out-Null
        return
    }
    Set-TrayState 'starting' '正在启动服务（首次 10–30 秒）'
    $script:Tray.Text = 'MedScribe HK · 正在启动…'
    $script:Tray.ShowBalloonTip(4000, 'MedScribe HK', '正在启动服务，就绪后会自动打开界面。',
        [System.Windows.Forms.ToolTipIcon]::Info)
}

function Open-Interface {
    $port = Get-IntegrationPort
    try { Start-Process ("http://127.0.0.1:{0}/" -f $port) } catch { }
}

function Show-About {
    $port = Get-IntegrationPort
    $version = '未知'
    $rules = '未知'
    $terms = '未知'
    $build = '未知'
    try {
        $h = Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/health" -f $port) -TimeoutSec 4
        if ($h.version) { $version = $h.version }
        if ($h.rules_version) { $rules = $h.rules_version }
        if ($h.terminology_version) { $terms = $h.terminology_version }
    } catch { }
    try {
        $l = Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/health/live" -f $port) -TimeoutSec 4
        if ($l.build) { $build = $l.build }
    } catch { }
    $text = @(
        'MedScribe HK · 香港临床口述病历助手',
        '',
        "版本        $version",
        "界面构建    $build",
        "抽取规则    $rules",
        "术语库      $terms",
        "界面地址    http://127.0.0.1:$port/",
        '',
        '数据只在本机处理，不联网、不接入患者真实数据；',
        '报告须由有资质医师逐项核对并签署。',
        '',
        '停止服务：托盘菜单「停止服务」或 stop.bat'
    ) -join "`r`n"
    [System.Windows.Forms.MessageBox]::Show($text, '关于 MedScribe HK', 'OK',
        [System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
}

function Stop-Services {
    if (Test-Path -LiteralPath $Py) {
        try {
            Start-Process -FilePath $Py -ArgumentList @('scripts\stop_all.py') `
                -WorkingDirectory $Integration -WindowStyle Hidden -Wait | Out-Null
        } catch {
            Start-Process -FilePath (Join-Path $Root 'stop.bat') -WindowStyle Hidden -Wait | Out-Null
        }
    } else {
        Start-Process -FilePath (Join-Path $Root 'stop.bat') -WindowStyle Hidden -Wait | Out-Null
    }
    Set-TrayState 'stopped' '服务已停止'
}

# ---------------------------------------------------------------- 托盘图标
$bmp = New-Object System.Drawing.Bitmap 64, 64
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.Clear([System.Drawing.Color]::Transparent)
$bg = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(11, 107, 95))
$g.FillEllipse($bg, 2, 2, 60, 60)
$fg = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::White)
# 白色医疗十字 + 底部 ECG 折线，64px 下仍可辨识
$g.FillRectangle($fg, 27, 13, 10, 38)
$g.FillRectangle($fg, 13, 27, 38, 10)
$pen = New-Object System.Drawing.Pen ([System.Drawing.Color]::White), 3
$pts = @(
    (New-Object System.Drawing.Point 10, 50),
    (New-Object System.Drawing.Point 20, 50),
    (New-Object System.Drawing.Point 26, 42),
    (New-Object System.Drawing.Point 33, 56),
    (New-Object System.Drawing.Point 40, 47),
    (New-Object System.Drawing.Point 54, 47)
)
$g.DrawLines($pen, $pts)
$g.Dispose()
$hicon = $bmp.GetHicon()
$icon = [System.Drawing.Icon]::FromHandle($hicon)

$script:Tray = New-Object System.Windows.Forms.NotifyIcon
$script:Tray.Icon = $icon
$script:Tray.Text = 'MedScribe HK'
$script:Tray.Visible = $true

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$itemOpen = $menu.Items.Add('打开界面')
$itemStart = $menu.Items.Add('启动服务')
$itemStop = $menu.Items.Add('停止服务')
[void]$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator))
$itemAbout = $menu.Items.Add('关于')
[void]$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator))
$itemQuit = $menu.Items.Add('退出托盘（服务继续运行）')
$script:Tray.ContextMenuStrip = $menu

$itemOpen.add_Click({ Open-Interface })
$itemStart.add_Click({ Start-Services })
$itemStop.add_Click({
    $script:Tray.Text = 'MedScribe HK · 正在停止…'
    Stop-Services
    $script:Tray.Text = 'MedScribe HK · 已停止'
})
$itemAbout.add_Click({ Show-About })
$itemQuit.add_Click({
    $script:Tray.Visible = $false
    $script:Tray.Dispose()
    [System.Windows.Forms.Application]::Exit()
})
$script:Tray.add_MouseDoubleClick({ Open-Interface })
$script:Tray.add_BalloonTipClicked({ Open-Interface })

# 启动时若服务没起来，自动拉起
$port = Get-IntegrationPort
if (-not (Test-Alive $port)) {
    Start-Services
} else {
    $script:Tray.Text = 'MedScribe HK · 运行中'
    Set-TrayState 'running' '服务运行中'
}

[System.Windows.Forms.Application]::Run()
$script:Tray.Visible = $false
$script:Tray.Dispose()
