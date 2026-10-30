#!/bin/zsh
# Daily intl borrow refresh (TFO 19:43 WITA 29 Oct 2026). PAPER. Writes borrow_refresh_status.json ok:true/false; 24h stale block stays the fallback.
cd /Users/test/Applications/MultiVenueDesk || exit 1
PY=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3
S=state/veteran_desk/borrow_refresh_status.json
echo "$(date '+%F %T WITA') START intl_borrow refresh"
OUT=$($PY -m veteran_desk.intl_borrow 2>&1); rc=$?
echo "$OUT"
$PY - "$rc" "$OUT" "$S" <<'P'
import json, sys, datetime as dt, os
rc, out, s = int(sys.argv[1]), sys.argv[2], sys.argv[3]
now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
rec = dict(ok=rc == 0, utc=now, rc=rc)
if rc == 0:
    try: rec["markets"] = json.loads(out.strip().splitlines()[-1])
    except Exception: pass
else: rec["error"] = out.strip()[-2000:]
tmp = s + ".tmp"; json.dump(rec, open(tmp, "w"), indent=1); os.replace(tmp, s)
P
echo "$(date '+%F %T WITA') END intl_borrow refresh rc=$rc"
# thfreview 20261030 item5: engine-readable borrow rule file (state/borrow_status.json); never fatal
$PY -m veteran_desk.borrow_status >> logs/intl_borrow_refresh.log 2>&1 || true
exit $rc
