@echo off
REM One-time: run the pipeline automatically at sign-in and when you unlock the laptop.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0schedule_task.ps1"
pause
