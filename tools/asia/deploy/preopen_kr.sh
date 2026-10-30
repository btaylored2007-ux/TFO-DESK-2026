#!/bin/bash
# PAPER ONLY. HUNT48_KR pre-open chain (launchd com.tfo.hunt48.kr.preopen, Mon-Fri 6:15 WITA):
# full-universe scan -> engine (bench) -> 3-desk clearance (GPT/Claude pending) -> engine (arm ONLY same-day PASS, before open; else flat today).
cd /Users/test/Applications/MultiVenueDesk || exit 1
PY=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3
[ "$(TZ=Asia/Makassar date +%u)" -le 5 ] || exit 0
touch state/hunt48_kr/preopen.lock; trap 'rm -f state/hunt48_kr/preopen.lock' EXIT
T() { TZ=Asia/Makassar date '+%F %T WITA'; }
echo "$(T) START preopen kr"
$PY -W ignore universe_asia.py kr 2>&1 | tail -3
$PY -W ignore asia_hunt48.py kr 2>&1 | head -1
$PY -W ignore hunt_clearance.py run --book kr --no-llm --no-claude 2>&1 | tail -2
$PY -W ignore hunt_clearance.py finalize --book kr 2>&1 | tail -1 >/dev/null
$PY -W ignore asia_hunt48.py kr 2>&1 | head -8
echo "$(T) END preopen kr"
