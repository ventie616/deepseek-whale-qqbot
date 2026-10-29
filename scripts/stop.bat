@echo off
setlocal enabledelayedexpansion
title QQBot Stopper
cd /d D:\QQrobot
echo Stopping QQBot ...
echo.
rem 1) pause the watchdog first, otherwise it will restart AstrBot in ~60s
if not exist "D:\QQrobot\watchdog_pause" (
  type nul > "D:\QQrobot\watchdog_pause"
  echo   [1/2] watchdog paused  ^(created watchdog_pause^)
) else (
  echo   [1/2] watchdog already paused
)
echo.
echo   [2/2] killing listeners ...
for %%P in (5099 6185 6199 3000 3001) do (
  for /f "tokens=5" %%I in ('netstat -ano ^| findstr /c:":%%P " ^| findstr /c:"LISTENING"') do (
    echo         port %%P -^> PID %%I
    taskkill /F /PID %%I >nul 2>&1
  )
)
echo.
echo Stopped: chat + Bilibili push are both down. QQ itself keeps running.
echo To start again, run start.bat ^(it removes watchdog_pause^).
ping -n 4 127.0.0.1 >nul
