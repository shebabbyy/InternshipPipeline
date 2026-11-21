@echo off
REM Makes networking drafts (LinkedIn note, email, follow-up, thank-you) for a person at any company. Nothing is sent.
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0.."
if not exist .venv\Scripts\python.exe (
  echo The pipeline is not installed yet. Double-click INSTALL.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe pipeline\outreach.py --network
pause
