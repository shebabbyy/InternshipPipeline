@echo off
REM Tests every career-site link (no AI, free) and keeps the ones that work. Run once after INSTALL, then whenever a company keeps getting skipped.
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0.."
if not exist .venv\Scripts\python.exe (
  echo The pipeline is not installed yet. Double-click INSTALL.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe pipeline\discover.py --check
pause
