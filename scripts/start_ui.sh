#!/usr/bin/env bash
# Launcher for the Raspberry Pi. Same job as start_ui.bat on Windows.
set -euo pipefail
# The project root, one level above this file, holds .venv, data and the package.
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
  echo "First run: creating the virtual environment."
  # --system-site-packages matters on the Pi. The GPIO stack (lgpio, gpiozero,
  # RPi.GPIO's replacement) is installed by apt for the system Python, and a
  # plain venv cannot see it, so gpiozero would find no pin factory and every
  # sensor read would fail. With this flag the venv reuses the apt copies.
  python3 -m venv --system-site-packages .venv
  .venv/bin/pip install --upgrade pip
  # The Pi image is aarch64; torch is a large download and only needed for the
  # LSTM. requirements-pi.txt leaves it out, so the console still records and
  # plots, and training falls back to the baseline.
  .venv/bin/pip install -r requirements-pi.txt
fi

# The sensor only works if a pin factory is importable. Say so once, in the
# place where the failure would otherwise appear later as a traceback.
if ! .venv/bin/python -c "import lgpio" >/dev/null 2>&1; then
  echo "note: lgpio is not visible to the venv, so the gpio and ads1115 drivers"
  echo "      will fail. Fix with:  sudo apt install python3-lgpio python3-gpiozero"
  echo "      then delete .venv and run this script again."
  echo
fi

exec .venv/bin/python -m soilcast web "$@"
