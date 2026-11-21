@echo off
REM Finds, scores, and tailors now (ignores the 12-hour wait). Never submits.
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0.."
if not exist .venv\Scripts\python.exe (
  echo The pipeline is not installed yet. Double-click INSTALL.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe pipeline\run_all.py --force %*
echo.
pause
