# build.ps1 -- build a standalone kol-ble-pairing.exe on Windows.
#
#   powershell -ExecutionPolicy Bypass -File build.ps1   -> dist\kol-ble-pairing.exe
#
# Needs Python 3.10+ on the BUILD machine only; the result runs without
# Python, pip or the requirements installed.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

py -3 -m venv .build-venv
.build-venv\Scripts\python -m pip install --quiet --upgrade pip
.build-venv\Scripts\python -m pip install --quiet -r requirements.txt -r requirements-build.txt
# bleak's WinRT backend loads its winrt modules dynamically: collect them too.
.build-venv\Scripts\pyinstaller --noconfirm --clean --onefile --name kol-ble-pairing `
  --collect-submodules bleak --collect-submodules winrt kol_ble_pairing.py
if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }
Write-Host "built: $PSScriptRoot\dist\kol-ble-pairing.exe"
