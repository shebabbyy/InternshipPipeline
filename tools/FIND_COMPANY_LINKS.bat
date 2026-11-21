@echo off
REM Asks Claude (Pro login) for career-site links of target companies that have none yet, then tests every link.
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0.."
if not exist .venv\Scripts\python.exe (
  echo The pipeline is not installed yet. Double-click INSTALL.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe pipeline\claude_discover.py --watchlist
pause
