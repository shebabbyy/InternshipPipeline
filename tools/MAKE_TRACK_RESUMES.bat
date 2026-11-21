@echo off
REM Builds your 3 track resumes (Corporate Finance, Banking, Accounting/Advisory) for Career Gateway and Simplify.
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0.."
if not exist .venv\Scripts\python.exe (
  echo The pipeline is not installed yet. Double-click INSTALL.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe pipeline\make_track_resumes.py
pause
