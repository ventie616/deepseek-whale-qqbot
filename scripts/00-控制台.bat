@echo off
chcp 65001 >nul
cd /d C:\QQBot
rem ????????????????
powershell -NoProfile -Command "if (Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if %errorlevel%==0 (
  start "" "http://127.0.0.1:8765/"
  exit /b
)
start "" /min "C:\QQBot\venv\Scripts\pythonw.exe" "C:\QQBot\tools\dashboard_server.py"
