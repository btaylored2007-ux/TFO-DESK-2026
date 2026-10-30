#!/bin/bash
# Watches maker_fill_probe and emits ONE line per event worth acting on.
# Silence is not success: it alerts on milestones, on the first real adverse-
# selection reading, AND on a stalled probe — a dead probe must never look like
# a quiet one.
STATE=/private/tmp/claude-501/-Users-test-Downloads/ad94ca3f-fc8a-4e60-8fcd-c99e53d7d7be/scratchpad/fill_seen.json
mkdir -p "$(dirname "$STATE")"; [ -f "$STATE" ] || echo '{}' > "$STATE"
while true; do
  /usr/bin/env python3 - "$STATE" <<'PYEOF'
import json, os, sys, time
statef = sys.argv[1]
PROBE = "/Users/test/Applications/MultiVenueDesk/reports/maker_fill_probe.json"
PRIMARY = "5bp"          # the offset the Crypto Rumour-Rider actually posts at
try:
    d = json.load(open(PROBE))
except Exception as e:
    print("FILL PROBE: unreadable (%s) — the probe may be down" % type(e).__name__, flush=True)
    raise SystemExit
try:
    seen = json.load(open(statef))
except Exception:
    seen = {}
out = []
stats = d.get("stats") or {}
resolved = len(d.get("resolved") or [])

# 1. stall detection FIRST — a probe that stopped is the failure that looks like silence
age_h = (time.time() - (d.get("epoch") or 0)) / 3600.0
if age_h > 4 and "stalled" not in seen:
    seen["stalled"] = 1
    out.append("FILL PROBE STALLED — no tick for %.1f hours. It is not collecting." % age_h)
elif age_h <= 4 and "stalled" in seen:
    del seen["stalled"]

# 2a. POOLED adverse selection across all offsets — the question "do the orders
#     we miss run away?" is not offset-specific, and pooling gets a real answer
#     in hours instead of the ~28h the 5bp offset alone would take. Reported as
#     pooled, and superseded by the per-offset reading when that thickens.
pool_f = [r for r in (d.get("resolved") or [])
          if r.get("outcome") == "FILL" and "fwd_pct" in r]
pool_m = [r for r in (d.get("resolved") or [])
          if r.get("outcome") == "MISS" and "fwd_pct" in r]
# CM-060: the pooled trigger was WRONG and is disarmed. Adverse selection REVERSES
# sign across offsets -- negative (missing is good) at 0-10bp, positive (missing is
# bad) at 25-50bp. Pooling them averaged a sign flip and 58% of the sample came from
# offsets the sleeve does not post at. Speed was bought with validity. The only
# reading that decides anything is the one at the sleeve's OWN offset, thresholded
# on ITS own missed count.
if False and len(pool_m) >= 30 and len(pool_f) >= 30 and "pooled_adverse" not in seen:
    seen["pooled_adverse"] = 1
    import statistics as _st
    mf = _st.mean(r["fwd_pct"] for r in pool_f)
    mm = _st.mean(r["fwd_pct"] for r in pool_m)
    adv = mm - mf
    fills_all = sum(1 for r in (d.get("resolved") or []) if r.get("outcome") == "FILL")
    tot_all = len(d.get("resolved") or [])
    fr_all = fills_all / tot_all if tot_all else 0
    # what it costs the sleeve: miss rate x the gap, vs the 15bp entry saving
    cost_pp = (1 - fr_all) * adv
    out.append("ADVERSE SELECTION — FIRST POOLED READING (all offsets, %d filled / "
               "%d missed with 4h outcomes): filled %+.3f%% | missed %+.3f%% | "
               "ADVERSE %+.3f%% | overall fill %.1f%% => costs the sleeve %.3f pp "
               "against a 0.150 pp fee saving -> v1.1 is %s"
               % (len(pool_f), len(pool_m), mf, mm, adv, 100 * fr_all, cost_pp,
                  "NET NEGATIVE, recommend reverting to taker" if cost_pp > 0.15
                  else "NET POSITIVE, keep maker execution"))

# 2b. the per-offset reading on the offset we actually trade
p = stats.get(PRIMARY) or {}
if (p.get("adverse_selection_pct") is not None and p.get("n_fwd_missed", 0) >= 30
        and p.get("n_fwd_filled", 0) >= 30 and "first_adverse" not in seen):
    seen["first_adverse"] = 1
    _cost = (1 - p["fill_rate"]) * p["adverse_selection_pct"]
    out.append("ADVERSE SELECTION AT THE SLEEVE'S OWN %s OFFSET (n=%d, %d filled / "
               "%d missed with 4h outcomes): fill %.1f%% | filled %+.3f%% | "
               "missed %+.3f%% | ADVERSE %+.3f%% => costs %.3f pp against a "
               "0.150 pp fee saving -> v1.1 is %s"
               % (PRIMARY, p["n"], p.get("n_fwd_filled", 0), p["n_fwd_missed"],
                  100 * p["fill_rate"], p["fwd_filled_pct"], p["fwd_missed_pct"],
                  p["adverse_selection_pct"], _cost,
                  "NET NEGATIVE, revert to taker" if _cost > 0.15
                  else "NET POSITIVE, keep maker"))

# 3. sample-size milestones on the primary offset
for lvl in (100, 250, 500, 1000):
    k = "n@%d" % lvl
    if p.get("n", 0) >= lvl and k not in seen:
        seen[k] = 1
        tag = "  <<< READY — this is a usable estimate" if lvl >= 1000 else ""
        out.append("FILL PROBE n=%d at the %s offset: fill %.1f%%%s%s"
                   % (p["n"], PRIMARY, 100 * p["fill_rate"],
                      (" | adverse %+.3f%%" % p["adverse_selection_pct"])
                      if p.get("adverse_selection_pct") is not None else "",
                      tag))
        if lvl >= 1000:
            rows = []
            for off, v in sorted(stats.items(), key=lambda kv: float(kv[0][:-2])):
                rows.append("%s: fill %.0f%% adverse %s"
                            % (off, 100 * v["fill_rate"],
                               ("%+.2f%%" % v["adverse_selection_pct"])
                               if v.get("adverse_selection_pct") is not None else "n/a"))
            out.append("   full grid — " + " | ".join(rows))

# 4. a fill rate that would BREAK the assumption the sleeve now runs on
if (p.get("n", 0) >= 200 and p.get("fill_rate", 1) < 0.85
        and "fill_low" not in seen):
    seen["fill_low"] = 1
    out.append("WARNING — fill rate at %s is %.1f%% on n=%d, well under the 99.1%% the "
               "amendment assumed. Crypto Rumour-Rider's maker execution needs review."
               % (PRIMARY, 100 * p["fill_rate"], p["n"]))

json.dump(seen, open(statef, "w"))
for line in out:
    print(line, flush=True)
PYEOF
  sleep 1800
done
