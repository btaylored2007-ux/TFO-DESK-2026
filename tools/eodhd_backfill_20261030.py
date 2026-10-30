"""tfo_datalayer_20261030: backfill pit.sqlite bars from EODHD (paid) for the desks' universes. PAPER. Writes via pit.put_bars
(knowable_utc, hl sanity, no unfinished sessions). Units guard: if EODHD close vs stored close on an overlap date differs >15%
the symbol is skipped and logged (GBX/GBP etc.). Pauses in tick windows. Rate: <=8 calls/s, stops at CALL_BUDGET.
Usage: python3 tools/eodhd_backfill_20261030.py [desk|full] [years_for_new_syms=10]"""
import sys, os, json, time, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from veteran_desk import pit as P, data_layer as D
CALL_BUDGET = 40000
def tickwin():
    m = dt.datetime.now().minute % 15; return m <= 3
def universe(c, mode):
    S = set(P.stale_universe(c))
    try:
        J = json.load(open(os.path.join(P.BASE, "data", "pit", "scan_universe.json"))); U = J.get("universe")
        S |= set(U if isinstance(U, list) else (U or {}).keys())
    except Exception: pass
    if mode == "full": S |= {r[0] for r in c.execute("SELECT DISTINCT sym FROM universe WHERE status='listed'")}
    return sorted(s for s in S if D.eodhd_symbol(s) and not P.banned(s) and not s.endswith("=X"))   # FX stays OANDA/Yahoo
def main(mode="desk", years=10):
    c = P.con(); c.execute("PRAGMA busy_timeout=20000"); now = dt.datetime.now(P.UTC); t0 = time.time()
    syms = universe(c, mode); rep = dict(started_utc=now.isoformat(), mode=mode, n_universe=len(syms), updated={}, new={}, uptodate=0, empty=[], units_skip={}, err={})
    calls = 0; mn, mx = None, None
    for i, s in enumerate(syms):
        while tickwin(): time.sleep(10)
        lb = P.last_bar(c, s); exp = P.expected_last_date(s, now)
        if lb and lb[0] >= exp: rep["uptodate"] += 1; continue
        start = (dt.date.fromisoformat(lb[0]) - dt.timedelta(days=7)).isoformat() if lb else (now.date() - dt.timedelta(days=365 * years)).isoformat()
        rows = D.eod_rows(s, start); calls += 1; time.sleep(0.12)
        if D.breaker_open("eod"): rep["err"]["_breaker"] = D.STATS.get("last_trip"); break
        if not rows: rep["empty"].append(s); continue
        if lb:
            ov = dict(c.execute("SELECT date,c FROM bars WHERE sym=? AND date>=?", (s, start)).fetchall())
            bad = [r[0] for r in rows if r[0] in ov and ov[r[0]] and abs(r[4] / ov[r[0]] - 1) > 0.15]
            if bad: rep["units_skip"][s] = "%s eod=%.4g pit=%.4g" % (bad[0], [r for r in rows if r[0] == bad[0]][0][4], ov[bad[0]]); continue
            rows = [r for r in rows if r[0] > lb[0]]
            if not rows: rep["uptodate"] += 1; continue
        try:
            P.put_bars(c, s, rows, "eodhd"); c.commit()
            (rep["updated"] if lb else rep["new"])[s] = [rows[0][0], rows[-1][0], len(rows)]
            mn = min(mn or rows[0][0], rows[0][0]); mx = max(mx or rows[-1][0], rows[-1][0])
        except Exception as e: rep["err"][s] = str(e)[:100]
        if calls >= CALL_BUDGET: rep["err"]["_budget"] = calls; break
        if i % 200 == 0: print(i, len(syms), calls, len(rep["updated"]), round(time.time() - t0), flush=True)
    rep.update(finished_utc=dt.datetime.now(P.UTC).isoformat(), calls=calls, date_min=mn, date_max=mx, secs=round(time.time() - t0),
               n_updated=len(rep["updated"]), n_new=len(rep["new"]), n_empty=len(rep["empty"]), n_units_skip=len(rep["units_skip"]), stats=D.STATS)
    json.dump(rep, open(os.path.join(P.BASE, "data", "pit", "eodhd_backfill_%s_report.json" % mode), "w"), indent=1)
    print(json.dumps({k: v for k, v in rep.items() if k not in ("updated", "new", "empty", "units_skip", "err")}), "err", len(rep["err"]))
if __name__ == "__main__": main(sys.argv[1] if len(sys.argv) > 1 else "desk", int(sys.argv[2]) if len(sys.argv) > 2 else 10)
