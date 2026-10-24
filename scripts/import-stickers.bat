@echo off
chcp 65001 >nul
title Import Stickers
echo ============================================================
echo   Import stickers from C:\QQBot\sticker_inbox
echo ============================================================
echo.
echo Drop your sticker images (png / jpg / gif / webp) into:
echo   C:\QQBot\sticker_inbox
echo.
echo Filename keywords decide the mood folder:
echo   happy  : xiao/xingfu/haoye/zan ...  (Chinese: xiao, kuaixin)
echo   angry  : shengqi/hen/taoyan ...
echo   sad    : ku/nanguo/weiqu ...
echo   sleepy : kun/shui/wanan/lei ...
echo   shy    : haixiu/bixin/aini ...
echo   others go to misc
echo.
pause
echo.
C:\QQBot\venv\Scripts\python.exe C:\QQBot\tools\import_stickers.py
echo.
pause
