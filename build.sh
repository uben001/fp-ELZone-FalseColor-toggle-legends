#!/bin/sh
# Build the card: builds/fcscale-fast/AutoRun.txt (the release build).
# ./build.sh --slow builds builds/fcscale/AutoRun.txt without the boot speed-up.
set -e
cd "$(dirname "$0")"
if [ "$1" = "--slow" ]; then
    .venv/bin/python tools/build_fcscale_autorun.py
else
    .venv/bin/python tools/build_fcscale_autorun.py --fast-echo
fi
