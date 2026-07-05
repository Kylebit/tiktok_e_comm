@echo off
setlocal

set "ROOT=%~dp0"
set "SCRIPT=%ROOT%scripts\start_orbit_desktop.py"

if not exist "%SCRIPT%" (
  echo [Orbit Desktop] Missing launcher script:
  echo %SCRIPT%
  pause
  exit /b 1
)

set "PYTHON_EXE="
set "SYSTEM_PYTHON=%LocalAppData%\Programs\Python\Python39\python.exe"
set "SYSTEM_PYTHONW=%LocalAppData%\Programs\Python\Python39\pythonw.exe"
set "VENV_PYTHON=%ROOT%.venv\Scripts\python.exe"
set "VENV_PYTHONW=%ROOT%.venv\Scripts\pythonw.exe"

if exist "%SYSTEM_PYTHON%" (
  "%SYSTEM_PYTHON%" -c "import tkinter, webview" >nul 2>&1
  if not errorlevel 1 (
    if exist "%SYSTEM_PYTHONW%" (
      set "PYTHON_EXE=%SYSTEM_PYTHONW%"
    ) else (
      set "PYTHON_EXE=%SYSTEM_PYTHON%"
    )
  )
)

if not defined PYTHON_EXE if exist "%VENV_PYTHON%" (
  "%VENV_PYTHON%" -c "import tkinter, webview" >nul 2>&1
  if not errorlevel 1 (
    if exist "%VENV_PYTHONW%" (
      set "PYTHON_EXE=%VENV_PYTHONW%"
    ) else (
      set "PYTHON_EXE=%VENV_PYTHON%"
    )
  )
)

if not defined PYTHON_EXE set "PYTHON_EXE=python"

start "" /D "%ROOT%" "%PYTHON_EXE%" "%SCRIPT%"
exit /b 0
