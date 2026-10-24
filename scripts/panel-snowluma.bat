@echo off
chcp 65001 >nul
title SnowLuma Panel
echo Opening SnowLuma WebUI : http://127.0.0.1:5099
netstat -ano | findstr /c:":5099 " | findstr LISTENING >nul 2>&1
if errorlevel 1 (
  echo.
  echo   [WARN] 5099 is not listening. SnowLuma is down.
  echo          Run start.bat first, then click Inject/Load on the BOT account.
  echo.
  ping -n 4 127.0.0.1 >nul
)
start "" http://127.0.0.1:5099
exit
