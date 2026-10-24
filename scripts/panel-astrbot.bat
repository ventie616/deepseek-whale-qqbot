@echo off
chcp 65001 >nul
title AstrBot Panel
echo Opening AstrBot WebUI : http://127.0.0.1:6185
netstat -ano | findstr /c:":6185 " | findstr LISTENING >nul 2>&1
if errorlevel 1 (
  echo.
  echo   [WARN] 6185 is not listening. AstrBot is down.
  echo          Run start.bat, or wait for the watchdog to bring it back.
  echo.
  ping -n 4 127.0.0.1 >nul
)
start "" http://127.0.0.1:6185
exit
