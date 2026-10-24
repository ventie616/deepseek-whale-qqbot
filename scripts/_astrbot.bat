@echo off
chcp 65001 >nul
title AstrBot
cd /d C:\QQBot\astrbot
set "ASTRBOT_ROOT=C:\QQBot\astrbot"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
echo Starting AstrBot ...
C:\QQBot\venv\Scripts\astrbot.exe run --port 6185
echo.
echo AstrBot exited. Press any key to close.
pause >nul
