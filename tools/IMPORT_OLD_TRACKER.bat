@echo off
REM One-time: copies the old Excel tracker's outreach + application history into the Google Sheet. Safe to run twice.
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0.."
if not exist .venv\Scripts\python.exe (
  echo The pipeline is not installed yet. Double-click INSTALL.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe tools\old_tracker\import_old_tracker.py
pause
