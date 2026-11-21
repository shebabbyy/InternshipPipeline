@echo off
REM Runs the offline self-test of the whole pipeline (fake data, touches nothing real). Run after any code change.
chcp 65001 >nul
cd /d "%~dp0.."
.venv\Scripts\python.exe pipeline\tests\offline_test.py
echo.
pause
