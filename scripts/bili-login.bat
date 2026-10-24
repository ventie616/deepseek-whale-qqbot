@echo off
chcp 65001 >nul
title Bilibili Login Panel
cd /d C:\QQBot
start "" http://127.0.0.1:8777
C:\QQBot\venv\Scripts\python.exe tools\bili_login_ui.py
pause
