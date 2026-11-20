@echo off
chcp 65001 >nul
title MedScribe HK
setlocal
set "ROOT=%~dp0"
set "MSHK_PYTHON=%ROOT%runtime\python\python.exe"
set "MSHK_ASR_DIR=%ROOT%app\backend"
set "MSHK_ENGINE_DIR=%ROOT%app\report-engine"
set "FFMPEG_PATH=%ROOT%bin\ffmpeg.exe"
set "PATH=%ROOT%bin;%PATH%"
set "WHISPER_MODEL=small"
set "WHISPER_DEVICE=cpu"
set "WHISPER_COMPUTE_TYPE=int8"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if not exist "%MSHK_PYTHON%" (
  echo [ERROR] Bundled Python not found: %MSHK_PYTHON%
  echo Please extract the archive completely and try again.
  pause
  exit /b 1
)

rem Windows caps DLL paths at 260 characters. PyAV loads FFmpeg DLLs from a
rem deep site-packages path, so a very long install root breaks transcription.
rem Keep this whole file ASCII-only: legacy cmd may parse UTF-8 Chinese wrongly.
set "MSHK_ROOT=%ROOT:~0,-1%"
set "MSHK_TMP=%MSHK_ROOT%"
set /a MSHK_LEN=0
:mshk_len_loop
if defined MSHK_TMP (
  set "MSHK_TMP=%MSHK_TMP:~1%"
  set /a MSHK_LEN+=1 >nul
  goto mshk_len_loop
)
if %MSHK_LEN% GEQ 120 (
  echo.
  echo [WARNING] Install path is too long: %MSHK_LEN% characters
  echo   %MSHK_ROOT%
  echo   Windows caps DLL paths at 260 characters; transcription would fail.
  echo   Move the MedScribe folder to a short path such as C:\MedScribe.
  echo.
  pause
  exit /b 1
)

rem Prefer the tray launcher: icon stays in the notification area, menu has
rem open / start / stop / about. Fall back to the console for older packages.
if exist "%ROOT%tray\MedScribeTray.ps1" (
  start "" wscript //nologo "%ROOT%tray\launch-tray.vbs"
  exit /b 0
)

cd /d "%ROOT%app\integration"
echo.
echo   ============================================
echo    MedScribe HK - offline clinical scribe
echo   ============================================
echo    Starting three local services; first model load takes 30-60s.
echo    The browser opens automatically when ready.
echo   ============================================
echo.
"%MSHK_PYTHON%" scripts\start_all.py --open
echo.
echo  Services stopped. Logs: app\integration\logs\
pause
