"""Weekly per-book A$ scorecard (Brett 2026-10-28 12:17 WITA, STEP 3). PAPER, REPORT ONLY.
Each book's A$ P&L after costs vs its own passive benchmark over the same window, sample size, and a pre-committed cut-rule FLAG
for Brett. It NEVER switches anything off, pauses, resizes or edits any book/routine. Writes state/scorecard_weekly.json (+ static copy
for the War Room) and reports/weekly_cut_review_<Sat YYYYMMDD>_scorecard.md; if that Saturday's weekly_cut_review_<date>.md exists it
appends one marked scorecard section (once)."""
import csv, datetime as dt, json, os, re
import strategy_tracker as ST
BASE = os.path.dirname(os.path.abspath(__file__)); OUT = os.path.join(BASE, "state", "scorecard_weekly.json"); STATIC = os.path.join(BASE, "static", "scorecard_weekly.json")
RULES_PATH = os.path.join(BASE, "state", "scorecard_rules.json"); WITA = dt.timezone(dt.timedelta(hours=8)); UTC = dt.timezone.utc; FX = ST.FX
RULES = {"_note": "Pre-committed 2026-10-28 12:17 WITA (Brett). REPORT ONLY - a flag never switches anything off.",
         "R1_underperform": "excess vs benchmark < -max(underperf_pct x capital, underperf_floor_aud) with sample >= min_sample",
         "underperf_pct": 0.02, "underperf_floor_aud": 5000, "min_sample_trades": 20, "min_sample_sessions": 20,
         "R2_no_fills": "0 fills in the last no_fill_sessions sessions (trade books only, book live >= that long)", "no_fill_sessions": 10}
MARK = "<!-- SCORECARD_WEEKLY (auto, report only) -->"
def _j(p, fb=None):
    try:
        with open(p if os.path.isabs(p) else os.path.join(BASE, p)) as f: return json.load(f)
    except Exception: return fb
def ts(x):
    try:
        t = dt.datetime.fromisoformat(str(x).replace("Z", "+00:00")); return t if t.tzinfo else t.replace(tzinfo=UTC)
    except Exception: return None
def sessions(a, b):
    if not a: return 0
    n, d = 0, a.date()
    while d < b.date():
        d += dt.timedelta(days=1); n += d.weekday() < 5
    return n
def rules():
    r = _j(RULES_PATH)
    if not isinstance(r, dict):
        r = dict(RULES)
        try: json.dump(r, open(RULES_PATH, "w"), indent=1)
        except OSError: pass
    return r
def yf_return(sym, start, end):
    """Passive buy-and-hold return start->end from daily adjusted closes (read-only market data). None if unavailable."""
    try:
        import warnings; warnings.filterwarnings("ignore"); import yfinance as yf
        d = yf.Ticker(sym).history(start=(start - dt.timedelta(days=6)).date().isoformat(), end=(end + dt.timedelta(days=1)).date().isoformat(), interval="1d", auto_adjust=True)
        if d.empty: return None
        c = d["Close"]; idx = [i.date() for i in c.index]
        base = [v for i, v in zip(idx, c.values) if i <= start.date()] or [c.values[0]]
        return float(c.values[-1]) / float(base[-1]) - 1
    except Exception: return None
def nasdaq2x_return(start):
    try:
        rows = list(csv.DictReader(open(os.path.join(BASE, "state", "shadow_nasdaq2x_history.csv"))))
        base = [r for r in rows if r["date"] <= start.date().isoformat()] or rows[:1]
        return float(rows[-1]["shadow_value_usd"]) / float(base[-1]["shadow_value_usd"]) - 1
    except Exception: return None
def first_hist(p, col="utc"):
    try:
        with open(os.path.join(BASE, p)) as f: return ts(next(csv.DictReader(f))[col])
    except Exception: return None

def books(now, ret=yf_return):
    B = []
    def add(name, kind, pnl, capital, start, bench_name, bench_ret, trades=None, fills_recent=None, src=""):
        if isinstance(bench_ret, float) and bench_ret != bench_ret: bench_ret = None   # 2026-10-29: NaN benchmark (Yahoo gap) -> missing, shown blank
        B.append(dict(book=name, kind=kind, pnl_aud=None if pnl is None else round(pnl), capital_aud=round(capital or 0), start_utc=start.strftime("%Y-%m-%d") if start else None,
                      benchmark=bench_name, bench_return=bench_ret, bench_pnl_aud=None if bench_ret is None else (0 if bench_ret == 0 else (round(bench_ret * capital) if capital else None)),
                      trades=trades, sessions=sessions(start, now), fills_last_10_sessions=fills_recent, src=src))
    t = _j("state/trend18.json") or {}
    if t.get("start"):
        s = ts(t.get("created_utc")); add("Trend18 (US$ ledger)", "allocation", (float(t["equity"]) - float(t["start"])) / FX, float(t["start"]) / FX, s, "Shadow Nasdaq 2x (same window)", nasdaq2x_return(s), src="state/trend18.json")
    tc = _j("static/trend_core_status.json") or {}
    if tc.get("seed_aud"):
        s = first_hist("state/trend_core_history.csv"); add("TREND-CORE A$1m", "allocation", tc.get("vs_seed_aud"), tc["seed_aud"], s, "Shadow Nasdaq 2x (same window)", nasdaq2x_return(s), src="static/trend_core_status.json")
    es = _j("static/essentials_status.json") or {}
    if es.get("seed_aud"):
        s = first_hist("state/essentials_history.csv"); add("ESSENTIALS A$1m", "allocation", es.get("vs_seed_aud"), es["seed_aud"], s, "JXI global utilities ETF (buy & hold)", ret("JXI", s, now), src="static/essentials_status.json")
    v = _j("reports/v2/tfo_v2_book.json") or {}
    if v.get("start"):
        s = ts(v.get("funded_utc")); add("TFO V2", "allocation", (float(v["equity"]) - float(v["start"])) / FX, float(v["start"]) / FX, s, "SPY (buy & hold)", ret("SPY", s, now), trades=len(v.get("closed") or []), src="reports/v2/tfo_v2_book.json")
    f = _j("reports/fundamental_book.json") or {}
    if f.get("seed_usd"):
        s = ts(f.get("opened_utc"))
        try:   # 2026-10-28: TRUE since-inception (equity - net contributed) plus the old REBASED view, both labelled
            import fundamental_true_return as _FTR; _tr = _FTR.compute(f, _FTR.load_curve())
            add("Fundamental book - TRUE since inception (vs net contributed)", "allocation", _tr["true_pnl_usd"] / FX, _tr["net_contributed_usd"] / FX, s, "ACWI (buy & hold)", ret("ACWI", s, now), src="fundamental_true_return.py")
        except Exception:
            pass
        add("Fundamental book - REBASED since 19 Sep reset (vs seed_usd)", "allocation", (float(f["equity"]) - float(f["seed_usd"])) / FX, float(f["seed_usd"]) / FX, s, "ACWI (buy & hold)", ret("ACWI", s, now), src="reports/fundamental_book.json")
    o = _j("state/oanda/snapshot.json") or {}
    if o.get("start_nav_aud"):
        try: s = ts(json.loads(open(os.path.join(BASE, "state", "oanda", "nav_daily.jsonl")).readline())["utc"])
        except Exception: s = None
        add("OANDA practice (TFO sleeve, whole NAV)", "allocation", float(o["nav"]) - float(o["start_nav_aud"]), float(o["start_nav_aud"]), s, "cash (A$0)", 0.0, src="state/oanda/snapshot.json")
    for name, path, ccy, _ in ST.HUNTS:
        L = _j(path) or {}; T = L.get("trades") or []
        if not T: continue
        r = ST.hunt_row(name, path, ccy, "")
        s = min([x for x in (ts(t.get("recorded_utc") or t.get("armed_utc")) for t in T) if x] or [None]) if any(t.get("recorded_utc") or t.get("armed_utc") for t in T) else None
        cutoff = now - dt.timedelta(days=14)
        recent = sum(1 for t in T if t.get("entry") and (ts(t.get("entry_utc")) or now - dt.timedelta(days=99)) >= cutoff)
        if "claude" in name.lower(): name = re.sub(r"(?i)claude", "ex-Claude", name) + " (engine now codex; Claude retired 2026-10-29)"
        add(name, "trades", r["pnl_aud"], float(L.get("sleeve_aud") or 0) or None, s, "cash (A$0; risk-budgeted swing book)", 0.0, trades=r["trades"], fills_recent=recent, src=path)
    return B

def flag(b, R):
    out = []
    if b["pnl_aud"] is not None and b["bench_pnl_aud"] is not None:
        b["excess_aud"] = b["pnl_aud"] - b["bench_pnl_aud"]
        lim = max(R["underperf_pct"] * (b["capital_aud"] or 0), R["underperf_floor_aud"])
        samp = (b["trades"] or 0) if b["kind"] == "trades" else b["sessions"]
        need = R["min_sample_trades"] if b["kind"] == "trades" else R["min_sample_sessions"]
        b["sample_text"] = f"{samp}/{need} {'closed trades' if b['kind'] == 'trades' else 'sessions'}"
        if b["excess_aud"] < -lim:
            out.append(f"R1 underperform by A${-b['excess_aud']:,.0f} (> A${lim:,.0f})" + ("" if samp >= need else " - sample too small, watch"))
            if samp < need: b["r1_watch"] = True
    else:
        b["excess_aud"] = None; b["sample_text"] = f"{b['sessions']} sessions"
    if b["kind"] == "trades" and b["sessions"] >= R["no_fill_sessions"] and b.get("fills_last_10_sessions") == 0:
        out.append(f"R2 0 fills in last {R['no_fill_sessions']} sessions")
    hard = [x for x in out if "watch" not in x]
    b["flag"] = "CUT-REVIEW (report only)" if hard else ("WATCH" if out else "OK")
    b["flag_reasons"] = out
    return b

def sat_of(now):
    w = now.astimezone(WITA); d = w.date() + dt.timedelta(days=(5 - w.weekday()) % 7)   # this week's Saturday (today if Sat)
    return d.strftime("%Y%m%d")
def md(d):
    L = [MARK, f"## Per-book A$ scorecard ({d['updated_wita']}) - PAPER, REPORT ONLY (flags never switch anything off)", "",
         "| Book | P&L A$ after costs | Benchmark | Bench A$ | Excess A$ | Sample | Flag |", "|---|---:|---|---:|---:|---|---|"]
    f = lambda x: "—" if x is None else f"{x:+,.0f}"
    for b in d["books"]:
        L.append(f"| {b['book']} | {f(b['pnl_aud'])} | {b['benchmark']} | {f(b['bench_pnl_aud'])} | {f(b['excess_aud'])} | {b['sample_text']} | {b['flag']}{': ' + '; '.join(b['flag_reasons']) if b['flag_reasons'] else ''} |")
    L += ["", "Rules (pre-committed): " + d["rules"]["R1_underperform"] + f" (pct {d['rules']['underperf_pct']:.0%}, floor A${d['rules']['underperf_floor_aud']:,}, min {d['rules']['min_sample_trades']} trades / {d['rules']['min_sample_sessions']} sessions); " + d["rules"]["R2_no_fills"] + ".", ""]
    return "\n".join(L)
def build(now=None, ret=yf_return):
    now = now or dt.datetime.now(WITA); R = rules()
    B = [flag(b, R) for b in books(now, ret)]
    return dict(paper_only=True, report_only=True, never_switches_off=True, updated_wita=now.astimezone(WITA).strftime("%a %d %b %Y %H:%M WITA"),
                week_saturday=sat_of(now), rules=R, books=B, n_flagged=sum(b["flag"].startswith("CUT") for b in B))
def write(now=None, ret=yf_return, reports_dir=None):
    d = build(now, ret); rd = reports_dir or os.path.join(BASE, "reports")
    for p, x in ((OUT, d), (STATIC, d)):
        tmp = p + ".tmp"; json.dump(x, open(tmp, "w"), indent=1, default=str); os.replace(tmp, p)
    text = md(d); sc = os.path.join(rd, f"weekly_cut_review_{d['week_saturday']}_scorecard.md")
    open(sc, "w").write(text + "\n")
    rv = os.path.join(rd, f"weekly_cut_review_{d['week_saturday']}.md")
    if os.path.exists(rv) and MARK not in open(rv).read():
        with open(rv, "a") as f: f.write("\n\n" + text + "\n")
        d["hooked_into"] = rv
    d["scorecard_md"] = sc; return d
if __name__ == "__main__":
    d = write(); print(d["updated_wita"], "->", d["scorecard_md"], d.get("hooked_into", "(no review file for this Saturday yet)"))
    for b in d["books"]: print(f"{b['book'][:34]:<34} pnl {b['pnl_aud']!s:>9} bench {b['bench_pnl_aud']!s:>9} excess {b['excess_aud']!s:>9} {b['sample_text']:<22} {b['flag']} {'; '.join(b['flag_reasons'])}")
