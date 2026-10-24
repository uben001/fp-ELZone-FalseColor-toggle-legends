#!/bin/sh
# Build both variants and run every offline check (about 3 minutes).
# No camera, no USB: an ARM emulator runs the card's code against your MAIN image.
set -e
cd "$(dirname "$0")"
.venv/bin/python tools/build_fcscale_autorun.py
.venv/bin/python tools/build_fcscale_autorun.py --fast-echo
.venv/bin/python tools/verify_autorun_replay.py
.venv/bin/python tools/verify_gestures.py
