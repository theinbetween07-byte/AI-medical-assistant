@echo off
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%..\medical-ai-assistant\backend\python\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%ROOT%scripts\stop_all.py"
timeout /t 3 /nobreak >nul
