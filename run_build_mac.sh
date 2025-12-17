#!/bin/bash
cd "$(dirname "$0")"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install -r requirements.txt
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name "KL_AI_Register" --distpath "${BUILD_OUT:-dist}" --add-data "kling_xpaths.json:." --add-data "kl-mail.csv:." gui_ctk.py
