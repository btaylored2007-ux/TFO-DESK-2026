#!/bin/bash
# Watches the Crypto Rumour-Rider and emits ONE line per event worth acting on.
# Silence is not success: it emits on milestones, on EVERY exit, on a band
# freeze, and on a stalled tick — so a dead sleeve cannot look like a quiet one.
PY=/usr/bin/env
BOOK=/Users/test/Applications/MultiVenueDesk/reports/crypto_rumour_book.json
STATE=/private/tmp/claude-501/-Users-test-Downloads/ad94ca3f-fc8a-4e60-8fcd-c99e53d7d7be/scratchpad/rumour_seen.json
mkdir -p "$(dirname "$STATE")"; [ -f "$STATE" ] || echo '{}' > "$STATE"
while true; do
  $PY python3 - "$BOOK" "$STATE" <<'PYEOF'
import json, os, sys, time
book, statef = sys.argv[1], sys.argv[2]
try:
    d = json.load(open(book))
except Exception as e:
    print("WATCH: ledger unreadable (%s) — the sleeve may be down" % type(e).__name__, flush=True)
    raise SystemExit
try:
    seen = json.load(open(statef))
except Exception:
    seen = {}
out = []
# 1. per-position milestones, each announced ONCE per level
for bname, b in (d.get("bands") or {}).items():
    for sym, p in (b.get("positions") or {}).items():
        e, m = p.get("entry"), p.get("mark")
        if not e or not m:
            continue
        r = 100 * (m / e - 1)
        for lvl in (5, 10, 15, 20, 30, 50):
            k = "%s@%d" % (sym, lvl)
            if r >= lvl and k not in seen:
                seen[k] = 1
                out.append("HIT +%d%%  %s (%s) now %+.2f%%  entry %.6g mark %.6g  vol %sx"
                           % (lvl, sym, bname, r, e, m, (p.get("signal") or {}).get("vol_ratio")))
# 2. EVERY exit, the moment it lands
for bname, b in (d.get("bands") or {}).items():
    for c in (b.get("closed") or []):
        k = "closed:%s:%s" % (c.get("sym"), c.get("utc"))
        if k in seen:
            continue
        seen[k] = 1
        out.append("EXIT  %s (%s) %+.2f%%  $%+.2f  after %.1fd  — %s"
                   % (c.get("sym"), bname, c.get("ret_pct") or 0, c.get("pnl_usd") or 0,
                      c.get("held_d") or 0, c.get("why")))
# 3. band freeze / experiment end
for bname, b in (d.get("bands") or {}).items():
    if b.get("frozen") and ("frozen:" + bname) not in seen:
        seen["frozen:" + bname] = 1
        out.append("FROZEN  %s band — %s. New entries refused; exits still run."
                   % (bname, b.get("frozen_reason")))
if d.get("experiment_over") and "over" not in seen:
    seen["over"] = 1
    out.append("TRIAL CONCLUDED — %s closed trades, day %s. Time to write the verdict."
               % (d.get("closed_trades"), d.get("days_live")))
# 4. sleeve-level equity milestone
seed = d.get("seed_usd") or 0
eq = d.get("equity") or 0
if seed:
    pct = 100 * (eq / seed - 1)
    for lvl in (5, -5, 10, -10, -20):
        k = "sleeve@%d" % lvl
        hit = (pct >= lvl) if lvl > 0 else (pct <= lvl)
        if hit and k not in seen:
            seen[k] = 1
            out.append("SLEEVE %+d%%  equity $%,.2f (%+.2f%%)".replace(",", "")
                       % (lvl, eq, pct))
# 5. staleness: a tick that stopped is not a quiet sleeve
age_h = (time.time() - (d.get("epoch") or 0)) / 3600.0
if age_h > 3 and "stalled" not in seen:
    seen["stalled"] = 1
    out.append("STALLED — no tick for %.1f hours. The sleeve is not running." % age_h)
elif age_h <= 3 and "stalled" in seen:
    del seen["stalled"]
json.dump(seen, open(statef, "w"))
for line in out:
    print(line, flush=True)
PYEOF
  sleep 600
done
