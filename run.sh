#!/usr/bin/env bash
# Launch Lorebook Studio on macOS / Linux.
# Creates a local virtualenv on first run, installs deps, starts the app.
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt
python app.py
