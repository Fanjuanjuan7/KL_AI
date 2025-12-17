@echo off
cd /d "%~dp0"
if not exist .venv (
    python -m venv .venv
)
call .venv\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller
if "%BUILD_OUT%"=="" set BUILD_OUT=dist
pyinstaller --noconfirm --onefile --windowed --name "KL_AI_Register" --distpath "%BUILD_OUT%" --add-data "kling_xpaths.json;." --add-data "kl-mail.csv;." gui_ctk.py
pause
