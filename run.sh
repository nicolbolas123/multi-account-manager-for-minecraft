#!/bin/zsh
# Launch Multi-Account Manager for Minecraft, creating the virtualenv on first run.
# Usage: ./run.sh [--mock] [--debug]
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
fi
exec .venv/bin/python -m mcmanager "$@"
