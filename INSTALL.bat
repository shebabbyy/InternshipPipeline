@echo off
REM One-time setup: creates the Python environment and installs everything. Safe to run again.
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3.12 -m venv .venv 2>nul || py -3 -m venv .venv 2>nul || python -m venv .venv
)
if not exist .venv\Scripts\python.exe (
  echo Could not find Python. Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^), then run this again.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Some packages failed to install. The usual cause is the job-board scraper on a very new Python.
  echo Installing everything else so the rest of the pipeline works...
  .venv\Scripts\python.exe -m pip install -r requirements-core.txt
)
.venv\Scripts\python.exe -m playwright install chromium
if not exist .env copy .env.example .env >nul
echo.
.venv\Scripts\python.exe pipeline\selftest.py
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\MAKE_SHORTCUT.ps1"
echo.
echo Next: open .env in Notepad and paste your Gemini key (START_HERE.md step 3), then use "Start Control Center" on your desktop.
echo The other .bat tools are in the tools folder.
pause
