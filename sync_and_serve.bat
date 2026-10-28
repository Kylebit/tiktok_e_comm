@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
call .venv\Scripts\activate.bat

echo === TikTok 商品同步 ===
python main.py products sync
if errorlevel 1 exit /b 1

echo.
echo === 启动 Web 控制台 ===
python main.py serve --port 8765
pause
