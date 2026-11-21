@echo off
REM Forwarder: the scheduled task was registered against this path. The real file is tools\RUN_PIPELINE_AUTO.bat.
call "%~dp0tools\RUN_PIPELINE_AUTO.bat"
exit /b %ERRORLEVEL%
