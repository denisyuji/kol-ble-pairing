#!/usr/bin/env bash
# build.sh -- build a standalone kol-ble-pairing executable for THIS OS (Linux or macOS).
#
#   ./build.sh            -> dist/kol-ble-pairing
#
# PyInstaller does not cross-compile: run this on each target OS (build.ps1 on
# Windows). The result runs without Python, pip or the requirements installed.
# Build on the oldest Linux distro you need to support (glibc compatibility).

set -euo pipefail
cd "$(dirname "$0")"

PYTHON=${PYTHON:-python3}
"$PYTHON" -m venv .build-venv
.build-venv/bin/python -m pip install --quiet --upgrade pip
.build-venv/bin/python -m pip install --quiet -r requirements.txt -r requirements-build.txt
.build-venv/bin/pyinstaller --noconfirm --clean --onefile --name kol-ble-pairing \
  --collect-submodules bleak kol_ble_pairing.py
echo "built: $(pwd)/dist/kol-ble-pairing"
