#!/bin/bash
# PAPER ONLY. HUNT48_HK pre-open chain (launchd com.tfo.hunt48.hk.preopen, Mon-Fri 7:45 WITA):
# full-universe scan -> engine (bench) -> 3-desk clearance (GPT/Claude pending) -> engine (arm ONLY same-day PASS, before open; else flat today).
cd /Users/test/Applications/MultiVenueDesk || exit 1
PY=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3
[ "$(TZ=Asia/Makassar date +%u)" -le 5 ] || exit 0
touch state/hunt48_hk/preopen.lock; trap 'rm -f state/hunt48_hk/preopen.lock' EXIT
T() { TZ=Asia/Makassar date '+%F %T WITA'; }
echo "$(T) START preopen hk"
$PY -W ignore universe_asia.py hk 2>&1 | tail -3
$PY -W ignore asia_hunt48.py hk 2>&1 | head -1
$PY -W ignore hunt_clearance.py run --book hk --no-llm --no-claude 2>&1 | tail -2
$PY -W ignore hunt_clearance.py finalize --book hk 2>&1 | tail -1 >/dev/null
$PY -W ignore asia_hunt48.py hk 2>&1 | head -8
echo "$(T) END preopen hk"
