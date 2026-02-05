@echo off
setlocal EnableDelayedExpansion

REM Change to project root directory
cd /d "%~dp0.."

echo [INFO] Starting KL_AI from %CD%...

REM Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    pause
    exit /b 1
)

REM Create/Activate Virtual Environment
if not exist venv (
    echo [INFO] Creating virtual environment...
    python -m venv venv
)
call venv\Scripts\activate

REM Install Dependencies
if exist requirements.txt (
    echo [INFO] Installing dependencies...
    pip install -r requirements.txt
)

REM Create logs directory
if not exist logs mkdir logs

REM Start GUI
echo [INFO] Launching GUI...
python -m src.gui_ctk

endlocal
pause
