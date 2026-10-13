@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.ps1 first. See README.md for manual setup.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -X utf8 studio\server.py --open-browser %*
if errorlevel 1 pause
