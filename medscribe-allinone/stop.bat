@echo off
chcp 65001 >nul
title MedScribe HK - 停止服务
setlocal
set "ROOT=%~dp0"
"%ROOT%runtime\python\python.exe" "%ROOT%app\integration\scripts\stop_all.py"
echo.
pause
