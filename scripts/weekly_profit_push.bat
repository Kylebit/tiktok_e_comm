@echo off
setlocal
cd /d "%~dp0.." || exit /b 1
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m shared_platform.weekly_profit_runner --persist-local %*
) else (
  python -m shared_platform.weekly_profit_runner --persist-local %*
)
exit /b %ERRORLEVEL%
