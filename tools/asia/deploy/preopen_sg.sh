#!/bin/bash
# PAPER ONLY. HUNT48_SG pre-open chain (launchd com.tfo.hunt48.sg.preopen, Mon-Fri 7:15 WITA):
# full-universe scan -> engine (bench) -> 3-desk clearance (GPT/Claude pending) -> engine (arm ONLY same-day PASS, before open; else flat today).
cd /Users/test/Applications/MultiVenueDesk || exit 1
PY=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3
[ "$(TZ=Asia/Makassar date +%u)" -le 5 ] || exit 0
touch state/hunt48_sg/preopen.lock; trap 'rm -f state/hunt48_sg/preopen.lock' EXIT
T() { TZ=Asia/Makassar date '+%F %T WITA'; }
echo "$(T) START preopen sg"
$PY -W ignore universe_asia.py sg 2>&1 | tail -3
$PY -W ignore asia_hunt48.py sg 2>&1 | head -1
$PY -W ignore hunt_clearance.py run --book sg --no-llm --no-claude 2>&1 | tail -2
$PY -W ignore hunt_clearance.py finalize --book sg 2>&1 | tail -1 >/dev/null
$PY -W ignore asia_hunt48.py sg 2>&1 | head -8
echo "$(T) END preopen sg"
