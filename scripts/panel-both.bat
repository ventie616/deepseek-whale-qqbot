@echo off
chcp 65001 >nul
title QQBot Panels
cd /d C:\QQBot
echo Opening both panels ...
netstat -ano | findstr /c:":5099 " | findstr LISTENING >nul 2>&1 || echo   [WARN] SnowLuma 5099 down - run start.bat
netstat -ano | findstr /c:":6185 " | findstr LISTENING >nul 2>&1 || echo   [WARN] AstrBot 6185 down - watchdog will restart it
start "" http://127.0.0.1:5099
ping -n 3 127.0.0.1 >nul
start "" http://127.0.0.1:6185
exit
