#!/usr/bin/env python3
"""'Should be trading but isn't' check (Brett 2026-10-29). READ-ONLY, PAPER. Writes state/should_be_trading.json.
Used by tools/health_overlord.py (item should_be_trading) and the gate-overlord routine:  python3 tools/should_be_trading.py [--brief]
Checks: A never-traded running books | B hunts with nothing armed | C VD proposer seats silent / model errors | D stale reports."""
import os, sys, json, glob, time, datetime
BASE = os.path.expanduser("~/Applications/MultiVenueDesk"); NOW = time.time()
VD_WINDOW_H = 48; VD_MIN_RUNS = 3
STALE = [("state/funder_queue.json", 6), ("state/execution/summary.json", 26), ("data/pit/refresh_report.json", 26),
         ("reports/veteran_desk_book.json", 26), ("state/all_desks_since_snap.json", 26), ("state/health_overlord.json", 4)]
def age_h(p): return (NOW - os.path.getmtime(p)) / 3600 if os.path.exists(p) else None
def J(p, d=None):
    try: return json.load(open(p))
    except Exception: return d
F = []   # dict(check, level, what, why)
def flag(c, lvl, what, why): F.append(dict(check=c, level=lvl, what=what, why=why))
# A. running books that have never traded (since-start 0, nothing open/armed). Shadow/benchmark books excluded.
try:
    sys.path.insert(0, os.path.join(BASE, "tools")); import all_desks as A
    for c in A.build().get("cards", []):
        if not str(c.get("status") or "").lower().startswith("running"): continue
        if not (c.get("since") or 0) and not (c.get("open") or 0) and not (c.get("armed") or 0):
            flag("A_never_traded", "amber", c["name"], "running, since-start 0, no open/armed positions (%s)" % (c.get("why") or ""))
except Exception as e: flag("A_never_traded", "amber", "all_desks", "check error %s" % e)
# B. hunts: nothing ARMED/FILLED/awaiting; or health stale > 26h
for h in sorted(glob.glob(os.path.join(BASE, "state", "hunt48_*", "health.json"))):
    d = J(h, {}) or {}; n = os.path.basename(os.path.dirname(h)); k = d.get("counts") or {}; a = age_h(h)
    if a is not None and a > 26: flag("B_hunt_stale", "red", n, "health.json %.0fh old - hunt not running" % a); continue
    live = sum(int(k.get(s) or 0) for s in ("ARMED", "FILLED", "awaiting_funder"))
    if live == 0:
        why = "nothing armed/filled; reviewed %s passed %s regime %s bench %s" % (d.get("reviewed"), d.get("passed"), d.get("regime"), d.get("bench"))
        if d.get("passed") == 0 and (d.get("reviewed") or 0) > 0: why += " -> every candidate failed review (check regime/long-only/borrow/score filters)"
        flag("B_hunt_nothing_armed", "amber", n, why)
# C. VD proposer seats with no proposal in the last 48h, or model errors (raw_ok false) in their runs
try:
    sys.path.insert(0, BASE); from veteran_desk import desk as D
    props = [s for s, v in D.seats().items() if v.get("role") == "proposer"]
    runs = [r for r in (J(p) for p in sorted(glob.glob(os.path.join(BASE, "state", "veteran_desk", "runs", "*.json")))) if r]
    cut = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=VD_WINDOW_H)
    runs = [r for r in runs if datetime.datetime.fromisoformat(r.get("asof_utc") or "1970-01-01T00:00:00+00:00") >= cut]
    qn = {}
    for e in ((J(os.path.join(BASE, "state", "funder_queue.json"), {}) or {}).get("entries") or {}).values():
        if e.get("book") == "veteran" and (e.get("queued_utc") or "") >= cut.isoformat():
            sd = next((x for x in props if str(e.get("id", "")).endswith("-" + x)), None)
            if sd: qn[sd] = qn.get(sd, 0) + 1
    for s in props:
        rr = [r["seats"][s] for r in runs if s in (r.get("seats") or {})]
        if len(rr) < VD_MIN_RUNS: flag("C_vd_seat_not_run", "amber", s, "seat ran %d times in %dh (<%d)" % (len(rr), VD_WINDOW_H, VD_MIN_RUNS)); continue
        err = sum(1 for x in rr if not x.get("raw_ok")); pr = sum(1 for x in rr if x.get("action") == "propose") + qn.get(s, 0)   # seat_cli proposals live in the queue, not runs/
        if err and not rr[-1].get("raw_ok"): flag("C_vd_seat_model_error", "red" if err == len(rr) else "amber", s, "%d/%d runs model error (raw_ok false); latest run errored" % (err, len(rr)))   # fixer 2026-10-29: only flag while the LATEST run is erroring (old recovered errors no longer amber for 48h)
        if pr == 0: flag("C_vd_seat_silent", "amber", s, "0 proposals in %d runs/%dh; last: %s" % (len(rr), VD_WINDOW_H, (rr[-1].get("reason") or "")[:160]))
except Exception as e: flag("C_vd", "amber", "veteran_desk", "check error %s" % e)
# D. stale reports
for p, mx in STALE:
    a = age_h(os.path.join(BASE, p))
    if a is None: flag("D_stale_report", "red", p, "missing")
    elif a > mx: flag("D_stale_report", "red" if a > 2 * mx else "amber", p, "%.1fh old (max %dh)" % (a, mx))
lvl = "red" if any(f["level"] == "red" for f in F) else "amber" if F else "green"
out = dict(asof_local=datetime.datetime.now().isoformat(timespec="seconds"), paper_only=True, read_only=True, overall=lvl, n=len(F), flags=F)
json.dump(out, open(os.path.join(BASE, "state", "should_be_trading.json"), "w"), indent=1)
if "--brief" in sys.argv: print(lvl, len(F)); [print(" ", f["level"], f["check"], f["what"], "-", f["why"][:160]) for f in F]
else: print(json.dumps(out, indent=1))
