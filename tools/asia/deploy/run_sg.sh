#!/bin/bash
# PAPER ONLY. HUNT48_SG book tick (launchd com.tfo.hunt48.sg every 120s). Full tick Mon-Fri 820-1820 WITA (pre-open live check + session + exits);
# otherwise hourly re-stage only. No broker, no orders, no keys.
cd /Users/test/Applications/MultiVenueDesk || exit 1
PY=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3
DOW=$(TZ=Asia/Makassar date +%u); HM=$((10#$(TZ=Asia/Makassar date +%H%M))); MI=$((10#$(TZ=Asia/Makassar date +%M)))
if [ "$DOW" -le 5 ] && [ $HM -ge 820 ] && [ $HM -le 1820 ]; then :; elif [ $MI -ge 2 ]; then exit 0; fi
[ -f state/hunt48_sg/preopen.lock ] && exit 0
echo "$(TZ=Asia/Makassar date '+%F %T WITA') $($PY -W ignore asia_hunt48.py sg 2>&1 | head -1)"
