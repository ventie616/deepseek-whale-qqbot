@echo off
chcp 65001 >nul
title AstrBot
cd /d D:\QQrobot\astrbot
set "ASTRBOT_ROOT=D:\QQrobot\astrbot"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
echo Starting AstrBot ...
D:\QQrobot\venv\Scripts\python.exe -m astrbot.cli.__main__ run --port 6185
echo.
echo AstrBot exited. Press any key to close.
pause >nul
