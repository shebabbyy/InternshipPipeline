@echo off
REM Re-makes the PDF (with Word) of every resume / cover letter you edited in output\. Never submits.
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0.."
if not exist .venv\Scripts\python.exe (
  echo The pipeline is not installed yet. Double-click INSTALL.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe pipeline\refresh_pdfs.py
echo.
pause
