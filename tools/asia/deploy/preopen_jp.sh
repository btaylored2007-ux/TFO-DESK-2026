#!/bin/bash
# PAPER ONLY. HUNT48_JP pre-open chain (launchd com.tfo.hunt48.jp.preopen, Mon-Fri 6:15 WITA):
# full-universe scan -> engine (bench) -> 3-desk clearance (GPT/Claude pending) -> engine (arm ONLY same-day PASS, before open; else flat today).
cd /Users/test/Applications/MultiVenueDesk || exit 1
PY=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3
[ "$(TZ=Asia/Makassar date +%u)" -le 5 ] || exit 0
touch state/hunt48_jp/preopen.lock; trap 'rm -f state/hunt48_jp/preopen.lock' EXIT
T() { TZ=Asia/Makassar date '+%F %T WITA'; }
echo "$(T) START preopen jp"
$PY -W ignore universe_asia.py jp 2>&1 | tail -3
$PY -W ignore asia_hunt48.py jp 2>&1 | head -1
$PY -W ignore hunt_clearance.py run --book jp --no-llm --no-claude 2>&1 | tail -2
$PY -W ignore hunt_clearance.py finalize --book jp 2>&1 | tail -1 >/dev/null
$PY -W ignore asia_hunt48.py jp 2>&1 | head -8
echo "$(T) END preopen jp"
