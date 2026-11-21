@echo off
REM Puts "Start Control Center" on the desktop (INSTALL.bat does this too).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0MAKE_SHORTCUT.ps1"
pause
