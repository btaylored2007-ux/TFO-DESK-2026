#!/bin/bash
# 2026-10-30 backupfix: standalone runner for offsite_backup.sh (launchd com.tfo.offsite-backup).
# Single-flight lock (mkdir + pid, stale-safe) and its own 4h cap; independent of desk_tick budget.
DESK="/Users/test/Applications/MultiVenueDesk"
LOCK="$DESK/reports/.offsite_backup.lock"
LOG="$DESK/reports/offsite_backup.log"
if ! mkdir "$LOCK" 2>/dev/null; then
  OLD=$(cat "$LOCK/pid" 2>/dev/null)
  if [ -n "$OLD" ] && kill -0 "$OLD" 2>/dev/null; then
    echo "$(date -u '+%F %T')Z skip: previous run pid $OLD still active" >> "$LOG"; exit 0
  fi
  rm -rf "$LOCK"; mkdir "$LOCK" || exit 1
fi
echo $$ > "$LOCK/pid"
# backupfix2: sweep any leftover local archives after EVERY run (success, failure or 4h-alarm kill).
# Safe: only this lock-holder ever creates /tmp/tier1_core_* / tier2_data_*.
sweep(){ rm -f /tmp/tier1_core_*.tar.gz /tmp/tier2_data_*.tar.gz 2>/dev/null; }
sweep
trap 'sweep; rm -rf "$LOCK"' EXIT
# 2026-10-30 encbackup (Brett: cloud): ENCRYPTED ledgers+state only, daily (tools/encrypted_state_backup.sh). Plaintext offsite_backup.sh retired.
/usr/bin/perl -e 'alarm shift; exec @ARGV or exit 127' 14400 /bin/bash "$DESK/tools/encrypted_state_backup.sh" "$@"
RC=$?
# 2026-10-30 enccode: encrypted CODE backup too (replaces plaintext tier1 code copy)
/usr/bin/perl -e 'alarm shift; exec @ARGV or exit 127' 7200 /bin/bash "$DESK/tools/encrypted_code_backup.sh" "$@"
RC2=$?; [ $RC -eq 0 ] && RC=$RC2
if [ $RC -ne 0 ]; then
  echo "$(date -u '+%F %T')Z runner: offsite_backup rc=$RC — running local_ledger_backup fallback" >> "$LOG"
  /bin/bash "$DESK/tools/local_ledger_backup.sh" >> "$LOG" 2>&1 || true
fi
exit $RC
