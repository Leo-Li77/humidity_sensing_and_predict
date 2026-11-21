#!/usr/bin/env bash
# Launcher for the Raspberry Pi. Same job as start_ui.bat on Windows.
set -euo pipefail
# The project root, one level above this file, holds .venv, data and the package.
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
  echo "First run: creating the virtual environment."
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  # The Pi image is aarch64; torch is a large download and only needed for the
  # LSTM. requirements-pi.txt leaves it out, so the console still records and
  # plots, and training falls back to the baseline.
  .venv/bin/pip install -r requirements-pi.txt
fi

exec .venv/bin/python -m soilcast web "$@"
