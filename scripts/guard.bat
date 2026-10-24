@echo off
chcp 65001 >nul
title AstrBot Watchdog
cd /d C:\QQBot
C:\QQBot\venv\Scripts\python.exe tools\watchdog.py
pause
