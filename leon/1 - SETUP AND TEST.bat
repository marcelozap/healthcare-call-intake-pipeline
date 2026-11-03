@echo off
REM ============================================================
REM  Double-click this first. It installs what's needed, builds
REM  the database, and runs every check. The window stays open
REM  at the end so you can read what happened.
REM ============================================================
cd /d "%~dp0"
title Leon Care Platform - Setup and Test

REM Find Python. Windows sometimes has "py" but not "python".
set PY=python
where python >nul 2>&1
if errorlevel 1 (
    set PY=py
    where py >nul 2>&1
    if errorlevel 1 (
        echo.
        echo  Python is not installed, or not on your PATH.
        echo  Install it from python.org and tick "Add Python to PATH".
        echo.
        pause
        exit /b 1
    )
)

echo.
echo  ============================================================
echo   STEP 1 of 4 - checking Python
echo  ============================================================
%PY% --version
echo.

echo  ============================================================
echo   STEP 2 of 4 - installing packages (takes a minute)
echo  ============================================================
%PY% -m pip install --quiet --upgrade pip
%PY% -m pip install --quiet fastapi uvicorn scikit-learn pandas pytest
if errorlevel 1 (
    echo.
    echo  Install failed. Copy everything above and send it to Claude.
    echo.
    pause
    exit /b 1
)
echo  done.
echo.

echo  ============================================================
echo   STEP 3 of 4 - building the database
echo  ============================================================
%PY% run.py --check
if errorlevel 1 (
    echo.
    echo  Build failed. Copy everything above and send it to Claude.
    echo.
    pause
    exit /b 1
)
echo.

echo  ============================================================
echo   STEP 4 of 4 - running the tests
echo  ============================================================
%PY% -m pytest tests/ -q
echo.

echo  ============================================================
echo   FINISHED
echo  ============================================================
echo.
echo   You want to have seen:
echo      "all checks passed"
echo      "49 passed"
echo.
echo   The database is now at:  data\outreach.db
echo.
echo   Next: double-click  "2 - START THE APP.bat"
echo.
pause
