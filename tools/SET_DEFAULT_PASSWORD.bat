@echo off
REM Saves one default portal password in Windows Credential Manager. Typed here only; never stored in a file.
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0.."
if not exist .venv\Scripts\python.exe (
  echo The pipeline is not installed yet. Double-click INSTALL.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe pipeline\set_default_password.py
pause
