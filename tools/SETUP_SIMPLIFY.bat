@echo off
REM One-time: adds Simplify Copilot to the APPLY window (separate Chrome profile) so autofill works there too.
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0.."
if not exist .venv\Scripts\python.exe (
  echo The pipeline is not installed yet. Double-click INSTALL.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe pipeline\setup_simplify.py
