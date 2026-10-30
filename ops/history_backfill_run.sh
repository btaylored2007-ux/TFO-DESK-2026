#!/bin/bash
# 2026-10-30 history backfill runner (launchd com.tfo.history-backfill): one process per paid feed, resumable; nightly refresh re-runs (progress table skips done items).
cd /Users/test/Applications/MultiVenueDesk || exit 1
LOCK=reports/.history_backfill.lock; mkdir "$LOCK" 2>/dev/null || { kill -0 "$(cat $LOCK/pid 2>/dev/null)" 2>/dev/null && exit 0; rm -rf "$LOCK"; mkdir "$LOCK"; }
echo $$ > "$LOCK/pid"; trap 'rm -rf "$LOCK"' EXIT
for p in coinalyze kraken eodhd fmp massive; do /usr/bin/nice -n 10 /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tools/history_backfill_20261030.py $p >> logs/history_backfill_$p.out 2>&1 & done
wait
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tools/seat_memory_20261030.py >> logs/seat_memory.out 2>&1 || true
