@echo off
REM Double-click-friendly launcher for the gajab load-test worker.
REM Example: run-worker.cmd -Sessions 10 -Duration 120
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0worker.ps1" %*
pause
