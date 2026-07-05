@echo off
setlocal
set "ROOT=%~dp0"
python "%ROOT%scripts\run_regression.py" %*
endlocal
