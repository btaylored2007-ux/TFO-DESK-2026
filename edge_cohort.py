"""Review fix #1 (research/hunt_review_20261027, Brett-approved): PROVE EDGE FIRST - frozen-version shadow cohort. PAPER/OFFLINE ONLY.
Every US hunt day the exact funnel output (main-book plan candidates + clearance verdict + setup hash) is frozen with a hash of the code
that produced it. `score` replays each frozen row on later DAILY bars and reports net R, fill rate and a 95% CI, split all / CLEARED.
Daily-bar replay is an approximation of the 15m engine (stop-first on ambiguous bars = conservative). Nothing here places or arms orders.
NOT done (no point-in-time data): an exact 12-month replay of this funnel - clearance verdicts and catalyst/earnings fields are 'as of now'
(yfinance), so a backfill would look ahead. The cohort accrues forward from the first recorded day.
CLI: python3 edge_cohort.py record [YYYYMMDD] [--force] | score"""
import datetime as dt, hashlib, json, math, os, sys
BASE = os.path.dirname(os.path.abspath(__file__)); UTC = dt.timezone.utc; WITA = dt.timezone(dt.timedelta(hours=8))
CODE = ("hunt_orchestrator.py", "hunt_clearance.py", "hunt48_room.py", "hunt48_short_paper.py", "event_calendar.py", "open_gates.py",
        "setup_hash.py", os.path.join("state", "calendar", "ranker", "run_scan.py"))
HOLD = 3   # NY sessions incl. the entry session (engine HOLD_SESSIONS)
def out_dir(base=BASE): return os.path.join(base, "state", "edge_cohort")
def code_version(base=BASE):
    files = {}
    for f in CODE:
        p = os.path.join(base, f)
        files[f] = hashlib.sha256(open(p, "rb").read()).hexdigest()[:12] if os.path.exists(p) else None
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:12], files
def _j(p, d=None):
    try: return json.load(open(p))
    except Exception: return d
def record(D=None, base=BASE, force=False):
    import setup_hash as SH
    D = D or dt.datetime.now(WITA).strftime("%Y%m%d"); p = os.path.join(out_dir(base), f"{D}.json")
    if os.path.exists(p) and not force: return p   # frozen: first record of the day wins
    P = _j(os.path.join(base, "state", "hunt48_short", f"plan_bench_{D}.json"), {}) or {}
    S = ((_j(os.path.join(base, "state", "clearance", D, "summary.json"), {}) or {}).get("books") or {}).get("main") or {}
    if not P.get("set"): return None   # no plan for D (or empty): record nothing, so a later real plan is not shadowed by an empty freeze
    rows = []
    for s in P.get("set", []):
        v = S.get(s["ticker"]) or {}; h = SH.setup_hash(s)
        rows.append(dict({k: s.get(k) for k in ("ticker", "direction", "trigger", "max_fill", "stop", "t1", "t2", "atr", "cost_R", "scan_rank", "sector")},
                         setup_hash=h, final=v.get("final") or "NOT_REVIEWED", cleared=v.get("final") == "CLEARED" and v.get("setup_hash") == h))
    ver, files = code_version(base)
    os.makedirs(out_dir(base), exist_ok=True); tmp = p + ".tmp"
    json.dump(dict(date=D, recorded_utc=dt.datetime.now(UTC).isoformat(timespec="seconds"), code_version=ver, code_files=files, n=len(rows), rows=rows), open(tmp, "w"), indent=1)
    os.replace(tmp, p); return p
def simulate(r, bars):
    """r: frozen row; bars: daily dict(o,h,l,c) from the entry session on (oldest first). Returns dict(filled, R) (R gross, per 1R)."""
    sh = str(r.get("direction") or "short").lower() != "long"; tr, mf, st, t1, t2 = (float(r[k]) for k in ("trigger", "max_fill", "stop", "t1", "t2"))
    if not bars: return dict(filled=None, R=None, why="no bars")
    b0 = bars[0]
    if sh: hit = b0["l"] <= tr and b0["o"] >= mf; entry = min(b0["o"], tr)
    else: hit = b0["h"] >= tr and b0["o"] <= mf; entry = max(b0["o"], tr)
    if not hit: return dict(filled=False, R=0.0, why="no trigger / gapped past max_fill")
    R = abs(entry - st)
    if R <= 0: return dict(filled=False, R=0.0, why="zero risk")
    sign = -1 if sh else 1; mv = lambda px: sign * (px - entry) / R
    half, trail = False, st; got = 0.0
    for i, b in enumerate(bars[:HOLD]):
        adverse = b["h"] if sh else b["l"]; fav = b["l"] if sh else b["h"]
        if (adverse >= trail) if sh else (adverse <= trail): return dict(filled=True, R=round(got + (0.5 if half else 1.0) * mv(trail), 4), why="stop" if not half else "BE stop")
        if not half and ((fav <= t1) if sh else (fav >= t1)): half = True; got += 0.5 * mv(t1); trail = entry
        if half and ((fav <= t2) if sh else (fav >= t2)): return dict(filled=True, R=round(got + 0.5 * mv(t2), 4), why="T2")
    last = bars[min(HOLD, len(bars)) - 1]["c"]
    if len(bars) < HOLD: return dict(filled=True, R=None, why="open (hold not complete)")
    return dict(filled=True, R=round(got + (0.5 if half else 1.0) * mv(last), 4), why="time exit")
def _stats(xs):
    n = len(xs)
    if not n: return dict(n=0, mean_R=None, ci95=None)
    m = sum(xs) / n; sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) if n > 1 else None
    return dict(n=n, mean_R=round(m, 4), total_R=round(sum(xs), 3), ci95=[round(m - 1.96 * sd / math.sqrt(n), 4), round(m + 1.96 * sd / math.sqrt(n), 4)] if sd else None)
def _yf_bars(sym, start):
    import yfinance as yf
    d = yf.Ticker(sym).history(start=start.isoformat(), interval="1d")
    return [dict(d=i.date().isoformat(), o=float(r.Open), h=float(r.High), l=float(r.Low), c=float(r.Close)) for i, r in d.iterrows() if r.Close == r.Close]
def score(base=BASE, fetch=None):
    fetch = fetch or _yf_bars; res = []
    for f in sorted(os.listdir(out_dir(base))) if os.path.isdir(out_dir(base)) else []:
        if not (len(f) == 13 and f.endswith(".json") and f[:8].isdigit()): continue
        C = _j(os.path.join(out_dir(base), f), {}); D = dt.date(int(f[:4]), int(f[4:6]), int(f[6:8]))   # WITA day D -> NY session D
        for r in C.get("rows", []):
            try: bars = [b for b in fetch(r["ticker"], D) if b["d"] >= D.isoformat()]
            except Exception as e: res.append(dict(r, date=f[:8], version=C.get("code_version"), filled=None, R=None, why=f"data error {str(e)[:60]}")); continue
            s = simulate(r, bars); net = None if s["R"] is None else round(s["R"] - (float(r["cost_R"]) if (s["filled"] and r.get("cost_R") is not None) else 0.0), 4)
            res.append(dict(r, date=f[:8], version=C.get("code_version"), **s, net_R=net))
    def agg(rows):
        done = [x for x in rows if x["filled"] is not None and not (x["filled"] and x["R"] is None)]
        fills = [x for x in done if x["filled"]]
        return dict(rows=len(rows), decided=len(done), fills=len(fills), fill_rate=round(len(fills) / len(done), 3) if done else None,
                    net=_stats([x["net_R"] for x in fills if x["net_R"] is not None]), gross=_stats([x["R"] for x in fills]),
                    cost_R_missing=sum(1 for x in fills if x.get("cost_R") is None))
    rep = dict(asof_utc=dt.datetime.now(UTC).isoformat(timespec="seconds"), method="daily-bar replay of frozen cohorts (approximation of 15m engine; stop-first on ambiguous bars)",
               all=agg(res), cleared=agg([x for x in res if x.get("cleared")]), by_version={v: agg([x for x in res if x["version"] == v]) for v in sorted({x["version"] for x in res if x.get("version")})},
               rows=res)
    if os.path.isdir(out_dir(base)):
        tmp = os.path.join(out_dir(base), "report.json.tmp"); json.dump(rep, open(tmp, "w"), indent=1, default=str); os.replace(tmp, os.path.join(out_dir(base), "report.json"))
    return rep
if __name__ == "__main__":
    a = sys.argv[1:] or ["score"]
    if a[0] == "record": print(record(a[1] if len(a) > 1 and a[1].isdigit() else None, force="--force" in a))
    elif a[0] == "score": r = score(); print(json.dumps({k: r[k] for k in ("all", "cleared")}, indent=1))
