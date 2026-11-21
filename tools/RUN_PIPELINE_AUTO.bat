@echo off
REM Used by the scheduled tasks. Exits silently when no scan is due (see scan_every_hours in config.yaml).
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0.."
if not exist .venv\Scripts\python.exe exit /b 1
REM Below-normal priority so a game or anything else you are doing always comes first.
start "" /B /BELOWNORMAL /WAIT .venv\Scripts\python.exe pipeline\run_all.py --auto > last_auto_run.txt 2>&1
if errorlevel 1 (
  copy /y last_auto_run.txt last_auto_error.txt >nul
  if "%PIPELINE_HIDDEN%"=="1" exit /b 1
  type last_auto_run.txt
  echo.
  echo The pipeline stopped with an error ^(shown above^). Nothing was submitted or sent.
  echo Paste the lines above into the Claude project and say /pipeline, or run INSTALL.bat to re-check the setup.
  pause
  exit /b 1
)
REM Success: close quietly. The Control Center shows the results. Only offer APPLY when auto_open_apply is on.
findstr /C:"APPLY_READY" last_auto_run.txt >nul || exit /b 0
if "%PIPELINE_HIDDEN%"=="1" exit /b 0
type last_auto_run.txt
echo.
choice /C YN /M "Applications are ready. Open them now"
if errorlevel 2 exit /b
.venv\Scripts\python.exe pipeline\run_all.py --apply
pause
