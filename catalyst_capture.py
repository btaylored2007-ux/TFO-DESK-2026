"""Daily catalyst capture (Brett 2026-10-28 04:50 WITA). PAPER ONLY, read-only free data (yfinance + SEC EDGAR).
For every US hunt candidate (top CAND_POOL ibkr rows of state/calendar/inputs/ranked.csv + today's candidates.json) and the
latest ASX plan names, records: earnings surprise (yfinance, report in last 10d), guidance proxy (8-K item 2.02/7.01 in last
5d), analyst up/downgrades (yfinance, last 5d), unusual volume (scan relvol >= 2), 8-K filings (EDGAR, last 3d).
ASX announcements: no yfinance/EDGAR source -> recorded as 'unavailable' (never fabricated); ASX names get yfinance news count only.
catalyst_flag = any of: |surprise| >= 5% with report <=10d, up/downgrade <=5d, relvol >= 2, 8-K <=3d.
Writes state/catalyst_capture/<YYYYMMDD WITA>.json (atomic). hunt_orchestrator.us_build_plan reads it: +CAT_BONUS to score."""
import csv, datetime as dt, glob, json, os, sys, time, urllib.request
BASE = os.environ.get("DESK_BASE", os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, BASE)
OUT = os.path.join(BASE, "state", "catalyst_capture"); UA = {"User-Agent": __import__("sec_contact").user_agent()}
CAND_POOL = 150; RELVOL_MIN = 2.0; SURPRISE_MIN = 5.0
WITA = dt.timezone(dt.timedelta(hours=8))
def fnum(x):
    try: v = float(x); return v if v == v else None
    except Exception: return None
def _get(url):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read())
_CIK = None
def cik_of(sym):
    global _CIK
    if _CIK is None:
        p = os.path.join(OUT, "company_tickers.json")
        if not os.path.exists(p) or time.time() - os.path.getmtime(p) > 7 * 86400:
            os.makedirs(OUT, exist_ok=True); d = _get("https://www.sec.gov/files/company_tickers.json"); json.dump(d, open(p, "w"))
        _CIK = {v["ticker"].upper(): int(v["cik_str"]) for v in json.load(open(p)).values()}
    return _CIK.get(sym.upper().replace(".", "-"))
def edgar_8k(sym, now, days=5):
    c = cik_of(sym)
    if not c: return None
    r = _get(f"https://data.sec.gov/submissions/CIK{c:010d}.json")["filings"]["recent"]; time.sleep(0.15)   # SEC fair access
    out = []
    for f, d, it in zip(r["form"], r["filingDate"], r.get("items", [""] * len(r["form"]))):
        if f in ("8-K", "8-K/A") and (now.date() - dt.date.fromisoformat(d)).days <= days: out.append(dict(date=d, items=it))
    return out
def yf_bits(sym, now):
    import yfinance as yf
    t = yf.Ticker(sym); res = {}
    try:
        ed = t.get_earnings_dates(limit=6); ed = ed[ed["Reported EPS"].notna()]
        if len(ed):
            d0 = ed.index[0].to_pydatetime(); res["last_report"] = d0.date().isoformat(); res["surprise_pct"] = fnum(ed["Surprise(%)"].iloc[0])
            res["report_age_d"] = (now - d0.astimezone(dt.timezone.utc)).days
    except Exception as e: res["earn_err"] = str(e)[:60]
    try:
        ud = t.upgrades_downgrades
        if ud is not None and len(ud):
            ud = ud[ud.index >= (now - dt.timedelta(days=5)).replace(tzinfo=None)]
            res["actions_5d"] = [dict(firm=r.Firm, action=r.Action, to=r.ToGrade) for r in ud.head(10).itertuples()]
    except Exception as e: res["ud_err"] = str(e)[:60]
    return res
def capture_one(sym, relvol=None, market="us", now=None):
    now = now or dt.datetime.now(dt.timezone.utc); rec = dict(sym=sym, market=market, relvol=fnum(relvol))
    try: rec.update(yf_bits(sym, now))
    except Exception as e: rec["yf_err"] = str(e)[:60]
    if market == "us":
        try: rec["8k_5d"] = edgar_8k(sym, now)
        except Exception as e: rec["edgar_err"] = str(e)[:60]
    else: rec["asx_announcements"] = "unavailable (no free yfinance/EDGAR source)"
    rec.update(flags(rec, now)); return rec
def flags(rec, now):
    why = []
    s, age = rec.get("surprise_pct"), rec.get("report_age_d")
    if s is not None and age is not None and 0 <= age <= 10 and abs(s) >= SURPRISE_MIN: why.append(f"earnings surprise {s:+.0f}% ({age}d ago)")
    ups = [a for a in rec.get("actions_5d") or [] if str(a.get("action")).lower() in ("up", "down", "init")]
    if ups: why.append(f"{len(ups)} analyst up/down/init 5d")
    if (rec.get("relvol") or 0) >= RELVOL_MIN: why.append(f"relvol {rec['relvol']:.1f}x")
    k = [f for f in rec.get("8k_5d") or [] if (now.date() - dt.date.fromisoformat(f["date"])).days <= 3]
    if k: why.append(f"8-K {k[0]['date']} items {k[0]['items']}")
    g = [f for f in rec.get("8k_5d") or [] if any(i in str(f.get("items")) for i in ("2.02", "7.01"))]
    return dict(catalyst_flag=bool(why), catalyst_why=why, guidance_8k=bool(g))
def universe(base=BASE):
    rows = []
    try: rows = [r for r in csv.DictReader(open(os.path.join(base, "state", "calendar", "inputs", "ranked.csv"))) if r.get("venue") == "ibkr"]
    except Exception: pass
    rows.sort(key=lambda r: -(fnum(r.get("rank_score")) or 0)); us = {r["sym"]: r.get("relvol") for r in rows[:CAND_POOL]}
    for f in sorted(glob.glob(os.path.join(base, "state", "hunt_orch", "us_scan", "2*", "candidates.json")))[-1:]:
        try:
            for c in json.load(open(f))["cands"]: us.setdefault(c["ticker"], (c.get("analysis") or {}).get("vol", {}).get("relvol"))
        except Exception: pass
    asx = set()
    for f in glob.glob(os.path.join(base, "state", "asx*", "plan*.json"))[-3:]:
        try: asx |= {x.get("ticker") or x.get("sym") for x in json.load(open(f)).get("set", [])}
        except Exception: pass
    return us, sorted(a for a in asx if a and not a.endswith(".JK"))
def run(base=BASE, now=None, limit=None):
    now = now or dt.datetime.now(dt.timezone.utc); us, asx = universe(base); D = now.astimezone(WITA).strftime("%Y%m%d")
    recs = {}
    for i, (s, rv) in enumerate(us.items()):
        if limit and i >= limit: break
        recs[s] = capture_one(s, rv, "us", now)
    for s in asx[: (limit or 999)]: recs[s] = capture_one(s, None, "asx", now)
    res = dict(paper_only=True, utc=now.isoformat(timespec="seconds"), n=len(recs), flagged=sorted(k for k, v in recs.items() if v["catalyst_flag"]), recs=recs)
    os.makedirs(os.path.join(base, "state", "catalyst_capture"), exist_ok=True); p = os.path.join(base, "state", "catalyst_capture", f"{D}.json")
    json.dump(res, open(p + ".tmp", "w"), indent=1, default=str); os.replace(p + ".tmp", p); return res
def load_flags(D, base=BASE):
    try: return {k: v for k, v in json.load(open(os.path.join(base, "state", "catalyst_capture", f"{D}.json")))["recs"].items()}
    except Exception: return {}
if __name__ == "__main__":
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else None; r = run(limit=lim); print(json.dumps(dict(n=r["n"], flagged=r["flagged"]), indent=1))
