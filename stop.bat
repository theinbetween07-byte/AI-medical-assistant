@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM   AI Medical Assistant - Stop Services
REM   NOTE: keep this file pure ASCII (see start.bat for reason).
REM ============================================================

set "ROOT=%~dp0"

echo.
echo Stopping AI Medical Assistant...
echo.

REM stop by listening port (most reliable)
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8000.*LISTENING" 2^>nul') do (
    echo   stopping backend PID %%P
    taskkill /F /PID %%P >nul 2>&1
)
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":5173.*LISTENING" 2^>nul') do (
    echo   stopping frontend PID %%P
    taskkill /F /PID %%P >nul 2>&1
)

echo.
echo Stopped.
timeout /t 3 /nobreak >nul
