@echo off
title Data Bandar

REM Change to script directory
cd /d "%~dp0"

echo Starting Data Bandar...
echo ======================================

REM Check if venv exists, create if not
if not exist "venv\" (
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo Failed to create virtual environment
        echo Please ensure Python 3 is installed
        pause
        exit /b 1
    )
    echo Virtual environment created
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Check if requirements are installed
if not exist "venv\.requirements_installed" (
    echo Installing dependencies...
    echo (This may take 2-3 minutes on first run)
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    if errorlevel 1 (
        echo Failed to install dependencies
        pause
        exit /b 1
    )
    echo. > venv\.requirements_installed
    echo Dependencies installed
)

echo.
echo Launching Data Bandar...
echo ======================================
echo.

REM Run the control GUI
python control.py

echo.
echo Data Bandar closed
echo.
pause
