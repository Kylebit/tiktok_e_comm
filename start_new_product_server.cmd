@echo off
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
"C:\Users\Windows11\AppData\Local\Programs\Python\Python39\python.exe" tools\run_console.py --smoke
