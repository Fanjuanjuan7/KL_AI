@echo off
setlocal
set DIR=%~dp0
set VENV=%DIR%.venv
set REQ=%DIR%requirements.txt
if not exist "%REQ%" (
  echo requirements.txt not found
  exit /b 1
)
if not exist "%VENV%" (
  py -3 -m venv "%VENV%"
)
set PY=%VENV%\Scripts\python.exe
set PIP=%VENV%\Scripts\pip.exe
"%PY%" -m pip install --upgrade pip
"%PIP%" install -r "%REQ%"
"%PY%" "%DIR%gui_ctk.py"

