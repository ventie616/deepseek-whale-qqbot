@echo off
setlocal
title QQBot Launcher
cd /d C:\QQBot

if exist "C:\QQBot\watchdog_pause" (
  del /q "C:\QQBot\watchdog_pause"
  echo   watchdog_pause removed ^(watchdog will supervise again^)
  echo.
)
echo ============================================================
echo   QQBot Launcher  (SnowLuma + AstrBot)
echo ============================================================
echo.
echo   IMPORTANT: QQ must be currently logged in with the BOT
echo   account (your spare account), NOT your main account.
echo   SnowLuma does NOT auto-inject now (manual mode).
echo.
pause
echo.

echo [1/2] Starting SnowLuma ...
start "SnowLuma" cmd /c C:\QQBot\_snowluma.bat
ping -n 5 127.0.0.1 >nul

echo [2/2] Starting AstrBot ...
start "AstrBot" cmd /c C:\QQBot\_astrbot.bat

echo.
echo Done. Wait about 20 seconds, then open SnowLuma WebUI and click
echo   Inject / Load on the BOT account row.
echo.
echo Panels:
echo   SnowLuma WebUI : http://127.0.0.1:5099   (admin / SnowLuma@2026)
echo   AstrBot  WebUI : http://127.0.0.1:6185   (astrbot / Qqbot2026)
echo.
echo To stop everything, run stop.bat
echo.
pause
