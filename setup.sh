#!/bin/sh
# One-time setup for building from source.
#   ./setup.sh /path/to/FP__V502.BIN
# Needs: macOS or Linux, python3, clang (macOS: xcode-select --install), curl.
set -e
cd "$(dirname "$0")"

FIRMWARE="$1"
if [ -z "$FIRMWARE" ]; then
    echo "usage: ./setup.sh /path/to/FP__V502.BIN   (SIGMA fp firmware Ver.5.02, from SIGMA's support site)"
    exit 1
fi
command -v clang >/dev/null || { echo "clang not found (macOS: xcode-select --install)"; exit 1; }

# fpSup's assembler wrapper (GPL-3.0, by bei / ijigen), fetched from its own
# repository at the exact commit this project was built and tested with.
ARMASM_URL=https://raw.githubusercontent.com/ijigen/fpSup/58c2b1c5223e6fdb513b61805a823aa3bf8de9b4/fp_usb_shell/armasm.py
ARMASM_SHA256=fb3206b1492f0729e88571c1990969acaa78db45401bd4d06e3d95ece4bb7cb6
mkdir -p reference/fpSup/fp_usb_shell analysis
curl -fsSL "$ARMASM_URL" -o reference/fpSup/fp_usb_shell/armasm.py
echo "$ARMASM_SHA256  reference/fpSup/fp_usb_shell/armasm.py" | shasum -a 256 -c -

python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

# Unpack the firmware's MAIN image locally (checked against its SHA-256).
# It stays on your computer: analysis/ is never committed.
.venv/bin/python tools/inspect_firmware.py "$FIRMWARE" --out analysis/MAIN_c0000000.bin
echo "Setup done. Next: ./build.sh"
