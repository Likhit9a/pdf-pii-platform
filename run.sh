#!/usr/bin/env bash
# Linux/macOS launcher. Usage: ./run.sh        (start the app)   ./run.sh test   (run the tests)
set -e
cd "$(dirname "$0")"
PY=$(command -v python3.12 || command -v python3.11 || command -v python3.10 || command -v python3)
[ -d .venv ] || "$PY" -m venv .venv
if [ ! -f .venv/deps_installed.txt ]; then
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -r requirements.txt
  echo ok > .venv/deps_installed.txt
fi
if [ "$1" = "test" ]; then exec .venv/bin/python -m pytest -q; fi
exec .venv/bin/python -m streamlit run app.py
