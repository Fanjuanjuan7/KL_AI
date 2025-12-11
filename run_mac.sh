#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$DIR/.venv"
REQ="$DIR/requirements.txt"
if [ ! -f "$REQ" ]; then
  echo "requirements.txt not found"
  exit 1
fi
if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV"
fi
source "$VENV/bin/activate"
python -m pip install --upgrade pip
pip install -r "$REQ"
python "$DIR/gui_ctk.py"

