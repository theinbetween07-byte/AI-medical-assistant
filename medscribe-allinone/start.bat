@echo off
chcp 65001 >nul
title MedScribe HK - 医用语音报告生成系统
setlocal
set "ROOT=%~dp0"
set "MSHK_PYTHON=%ROOT%runtime\python\python.exe"
set "MSHK_ASR_DIR=%ROOT%app\backend"
set "MSHK_ENGINE_DIR=%ROOT%app\report-engine"
set "FFMPEG_PATH=%ROOT%bin\ffmpeg.exe"
set "PATH=%ROOT%bin;%PATH%"
set "WHISPER_MODEL=medium"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if not exist "%MSHK_PYTHON%" (
  echo [错误] 找不到内置 Python: %MSHK_PYTHON%
  pause
  exit /b 1
)
cd /d "%ROOT%app\integration"
echo.
echo   ============================================
echo    MedScribe HK  医用语音报告生成系统
echo   ============================================
echo    正在启动三个服务，首次加载语音模型约 30-60 秒
echo    就绪后浏览器会自动打开
echo   ============================================
echo.
"%MSHK_PYTHON%" scripts\start_all.py --open
echo.
echo  服务已退出。日志位置: app\integration\logs\
pause
