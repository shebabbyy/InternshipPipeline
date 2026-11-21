@echo off
REM Rebuilds the resume and cover letter for every "Packet ready" job (after a tailoring fix). Never submits.
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0.."
if not exist .venv\Scripts\python.exe (
  echo The pipeline is not installed yet. Double-click INSTALL.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe pipeline\run_all.py --retailor
echo.
pause
