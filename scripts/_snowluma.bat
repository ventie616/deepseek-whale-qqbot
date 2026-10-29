@echo off
chcp 65001 >nul
title SnowLuma
cd /d D:\QQrobot\snowluma
set "SNOWLUMA_HOOK_AUTOLOAD=0"
set "SNOWLUMA_ACCEPT_EULA=1"
set "SNOWLUMA_ACCEPT_PRIVACY=1"
set "SNOWLUMA_WEBUI_BOOTSTRAP_PASSWORD=SnowLuma@2026"
echo Starting SnowLuma ...
.\node.exe .\index.mjs
echo.
echo SnowLuma exited. Press any key to close.
pause >nul
