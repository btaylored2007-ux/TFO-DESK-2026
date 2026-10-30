#!/bin/zsh
# com.tfo.veteran-desk: PIT refresh -> seat pipeline -> book tick. PAPER only.
cd "/Users/test/Applications/MultiVenueDesk" || exit 1
export PATH=/Library/Frameworks/Python.framework/Versions/3.14/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin
L=logs/veteran_desk.log
ts(){ echo "$(date '+%F %T WITA') $1" | tee -a "$L" >&2; }
ts "PIPELINE START"
ts "STEP pit.py begin"
python3 veteran_desk/pit.py 2y >> "$L" 2>&1; ec1=$?
ts "STEP pit.py end rc=$ec1"
# tfo_seatpipe_1_20261030: one launchd job (every 2h + ASX/Tokyo/HK/Europe/US opens) runs all 11 seats on weekdays;
# weekend (Sat 06:00 WITA -> Mon 06:00 WITA, equity markets shut) runs Kai only. Replaces com.tfo.veteran-desk-crypto (unloaded).
LK=state/veteran_desk/.pipeline.lock
if ! mkdir "$LK" 2>/dev/null; then
  if [ -n "$(find "$LK" -maxdepth 0 -mmin +40 2>/dev/null)" ]; then rmdir "$LK"; mkdir "$LK"; else ts "PIPELINE SKIP previous run still active"; exit 0; fi
fi
trap 'rmdir "$LK" 2>/dev/null' EXIT
DOW=$(date +%u); HR=$(date +%H)
SEATS=""
if [ "$DOW" = 7 ] || { [ "$DOW" = 6 ] && [ "$HR" -ge 6 ]; } || { [ "$DOW" = 1 ] && [ "$HR" -lt 6 ]; }; then SEATS="--seats=crypto"; fi
ts "STEP desk run begin ${SEATS:-all-seats}"
python3 -m veteran_desk.desk run $SEATS >> "$L" 2>&1; ec2=$?
ts "STEP desk run end rc=$ec2"
# 2026-10-29 ops: book tick via run_tick.sh shared .tick.lock (no double-run with com.tfo.veteran-desk-tick)
ts "STEP book tick begin"
/bin/zsh veteran_desk/run_tick.sh --any-session pipeline >> "$L" 2>&1; ec3=$?
ts "STEP book tick end rc=$ec3"
ts "PIPELINE END pit=$ec1 desk=$ec2 tick=$ec3"
exit $(( ec1 || ec2 || ec3 ))
