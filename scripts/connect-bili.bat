@echo off
chcp 65001 >nul
title Connect Bilibili Account
setlocal
cd /d C:\QQBot

echo ============================================================
echo    Read Bilibili cookies from Edge and connect the account
echo ============================================================
echo.
echo   Edge locks its cookie database while running, so:
echo     1) Quit Edge completely (tray icon -^> Exit, or end all
echo        msedge.exe in Task Manager)
echo     2) Then press any key here
echo.
echo   NOTE: closing Edge also closes this chat page. Just reopen
echo         http://127.0.0.1:59455 afterwards.
echo.
pause
echo.

:check
tasklist /FI "IMAGENAME eq msedge.exe" 2>nul | find /I "msedge.exe" >nul
if not errorlevel 1 (
  echo   Edge is still running.
  echo   Press any key to force-kill it, or close it yourself first.
  pause
  taskkill /F /IM msedge.exe >nul 2>&1
  ping -n 3 127.0.0.1 >nul
  goto check
)

echo [1/2] Reading cookies ...
C:\QQBot\venv\Scripts\python.exe C:\QQBot\tools\read_edge_cookie.py
if errorlevel 1 (
  echo.
  echo Failed. Nothing changed.
  pause
  exit /b 1
)

echo.
echo [2/2] Restarting AstrBot only (SnowLuma / QQ keep running) ...
for /f "tokens=5" %%I in ('netstat -ano ^| findstr /c:":6185 " ^| findstr /c:"LISTENING"') do taskkill /F /PID %%I >nul 2>&1
ping -n 3 127.0.0.1 >nul
start "AstrBot" cmd /c C:\QQBot\_astrbot.bat
ping -n 20 127.0.0.1 >nul

echo.
echo Done. Bilibili account connected.
echo Try telling the bot (in Chinese): brush Bilibili / what did I watch lately
echo.
pause
