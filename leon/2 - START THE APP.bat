@echo off
REM ============================================================
REM  Double-click this to start the app. It opens your browser
REM  automatically. Close this black window to stop the server.
REM ============================================================
cd /d "%~dp0"
title Leon Care Platform - Running (close this window to stop)

set PY=python
where python >nul 2>&1
if errorlevel 1 set PY=py

echo.
echo  ============================================================
echo   STARTING - your browser will open in a few seconds
echo  ============================================================
echo.
echo   App:  http://localhost:8000
echo   API:  http://localhost:8000/docs
echo.
echo   Leave this window open. Close it to stop the server.
echo.

REM Give the server a moment to boot, then open the browser.
start "" /b cmd /c "timeout /t 4 >nul && start http://localhost:8000"

%PY% run.py

echo.
echo  Server stopped.
pause
