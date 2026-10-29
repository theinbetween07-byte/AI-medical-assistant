@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM   AI Medical Assistant - Portable Launcher
REM   Bundled Python + Node.js + ffmpeg + Whisper model
REM   NOTE: keep this file pure ASCII. Chinese characters together
REM         with "chcp 65001" make cmd.exe mis-parse the script.
REM ============================================================

REM NOTE: this file lives in scripts\, so ROOT is one level up.
set "ROOT=%~dp0..\"
cd /d "%ROOT%"
set "TMP=%ROOT%.tmp"
set "PY=%ROOT%backend\python\python.exe"
set "PYW=%ROOT%backend\python\pythonw.exe"

echo.
echo ============================================================
echo   AI Medical Assistant - Starting
echo ============================================================
echo.

REM ---------- check bundled runtime ----------
if not exist "%PY%" (
    echo [ERROR] Bundled Python not found.
    echo         Please extract the ZIP completely before running.
    pause
    exit /b 1
)
if not exist "%ROOT%node\node.exe" (
    echo [ERROR] Bundled Node.js not found.
    echo         Please extract the ZIP completely before running.
    pause
    exit /b 1
)
if not exist "%ROOT%scripts\launch.py" (
    echo [ERROR] Launcher script not found: scripts\launch.py
    pause
    exit /b 1
)

REM ---------- stop old services ----------
echo [1/3] Cleaning old services...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8000.*LISTENING" 2^>nul') do taskkill /F /PID %%P >nul 2>&1
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":5173.*LISTENING" 2^>nul') do taskkill /F /PID %%P >nul 2>&1

REM ---------- start backend and frontend ----------
echo [2/3] Starting backend and frontend...
"%PY%" "%ROOT%scripts\launch.py"
if errorlevel 1 (
    echo.
    echo [ERROR] Launcher failed. See log: %TMP%\launch-error.log
    pause
    exit /b 1
)

REM ---------- wait until ready ----------
echo [3/3] Waiting for services ^(first run loads the model, ~20-40s^)...
echo.
set /a COUNT=0
set "OPENED="

:WAIT
timeout /t 3 /nobreak >nul
set /a COUNT+=3
set "BE="
set "FE="
for /f %%C in ('curl -s -o NUL -w "%%{http_code}" --noproxy * --max-time 2 http://localhost:8000/health 2^>nul') do set "BE=%%C"
for /f %%C in ('curl -s -o NUL -w "%%{http_code}" --noproxy * --max-time 2 http://localhost:5173 2^>nul') do set "FE=%%C"

echo   [!COUNT!s]  backend=!BE!  frontend=!FE!

if "!FE!"=="200" if not defined OPENED (
    start "" "http://localhost:5173"
    set "OPENED=1"
)
if "!BE!"=="200" if "!FE!"=="200" goto READY
if !COUNT! GEQ 120 goto TIMEOUT
goto WAIT

:READY
echo.
echo ============================================================
echo   Ready
echo ============================================================
echo.
echo   Web UI:   http://localhost:5173
echo   Backend:  http://localhost:8000
echo   API docs: http://localhost:8000/docs
echo.
echo   To stop:  double-click scripts\stop.bat
echo ============================================================
echo.
echo Services are running in background. You may close this window.
timeout /t 8 /nobreak >nul
exit /b 0

:TIMEOUT
echo.
echo [WARN] Startup timed out. Check logs:
echo        %TMP%\backend.log
echo        %TMP%\frontend.log
if not defined OPENED start "" "http://localhost:5173"
pause
exit /b 1
