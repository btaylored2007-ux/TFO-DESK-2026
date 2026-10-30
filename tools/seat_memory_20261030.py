#!/usr/bin/env python3
"""Per-seat CORE MEMORY (TFO 2026-10-30, Brett top priority). PAPER ONLY. Read-only on books/ledgers.
Builds state/<desk>/seat_memory/<seat>.json + .md for VD (veteran_desk) and THF from:
 - data/history/history.sqlite eod (EODHD max history) falling back to pit.sqlite bars
 - history.sqlite fundamentals (FMP earnings) for earnings reactions
 - state/learning/<seat>/journal.jsonl for the desk's own win rate / avg R per setup
Setups are mechanical (stop = 1.5*ATR14, exit = 10 bars or stop) so base rates are honest, not narrative."""
import json, os, sqlite3, statistics as st, sys, datetime as dt
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, BASE)
from veteran_desk import pit as P
HIST = os.path.join(BASE, "data", "history", "history.sqlite"); PIT = P.DB
MAXSYM = int(os.environ.get("SEAT_MEM_MAXSYM", "80"))
def seats():
    d = os.path.join(BASE, "veteran_desk", "seats"); out = {}
    for f in sorted(os.listdir(d)):
        if f.endswith(".json"):
            j = json.load(open(os.path.join(d, f)))
            if j.get("role") == "proposer": out[j["seat"]] = j
    return out
def universe(j):
    if j.get("universe") == "pit:all": g = list(P.UNIVERSE)
    else: g = j.get("universe_groups") or []
    syms = []
    for x in g:
        for s in (P.UNIVERSE.get(x) or []):
            if s not in syms: syms.append(s)
    return syms[:MAXSYM]
def _eod_sym(s):
    s2 = s.replace("-USD", "-USD.CC") if s.endswith("-USD") else s
    return [s, s2, s.split(".")[0] + ".US" if "." not in s else s]
def bars(s, hc, pc):
    if hc:
        for k in _eod_sym(s):
            try:
                r = hc.execute("select date,o,h,l,c from eod where sym=? order by date", (k,)).fetchall()
            except Exception: r = []
            if len(r) > 300: return r, "eodhd_history"
    r = pc.execute("select date,o,h,l,c from bars where sym=? order by date", (s,)).fetchall()
    return r, "pit"
def atr(b, i, n=14):
    tr = [max(b[k][2] - b[k][3], abs(b[k][2] - b[k-1][4]), abs(b[k][3] - b[k-1][4])) for k in range(i-n+1, i+1)]
    return sum(tr) / n
def outcome(b, i, a, H=10):
    e = b[i][4]; stop = e - 1.5 * a; R = 1.5 * a
    for k in range(i+1, min(i+1+H, len(b))):
        if b[k][3] <= stop: return -1.0
    j = min(i+H, len(b)-1)
    return (b[j][4] - e) / R if R > 0 else 0.0
SETUPS = ("breakout20", "pullback_uptrend", "meanrev_z2", "gap_up2")
def scan(b):
    res = {k: [] for k in SETUPS}; n = len(b)
    for i in range(60, n-1):
        c = b[i][4]; a = atr(b, i)
        if a <= 0 or c <= 0: continue
        cl = [x[4] for x in b[i-50:i]]; sma50 = sum(cl)/50; c20 = cl[-20:]; m20 = sum(c20)/20
        sd = st.pstdev(c20) or 0
        if c > max(x[2] for x in b[i-20:i]): res["breakout20"].append(outcome(b, i, a))
        if c > sma50 and c < min(x[3] for x in b[i-5:i]): res["pullback_uptrend"].append(outcome(b, i, a))
        if sd and (c - m20)/sd < -2: res["meanrev_z2"].append(outcome(b, i, a))
        if b[i][1] > b[i-1][4]*1.02: res["gap_up2"].append(outcome(b, i, a))
    return res
def summ(v):
    if not v: return {"n": 0}
    return {"n": len(v), "win_rate": round(sum(1 for x in v if x > 0)/len(v), 3), "avg_r": round(sum(v)/len(v), 3)}
def build(seat, j, hc, pc, desk):
    U = universe(j); agg = {k: [] for k in SETUPS}; month = {m: [] for m in range(1, 13)}; dow = {d: [] for d in range(5)}
    vol = []; src = {}; nb = 0; rng = [None, None]; earn = []
    for s in U:
        b, sr = bars(s, hc, pc); b = [x for x in b if None not in x[1:5] and x[4] > 0]
        if len(b) < 80: continue
        src[sr] = src.get(sr, 0) + 1; nb += len(b)
        rng[0] = min(rng[0] or b[0][0], b[0][0]); rng[1] = max(rng[1] or b[-1][0], b[-1][0])
        for k, v in scan(b).items(): agg[k] += v
        for i in range(1, len(b)):
            r = b[i][4]/b[i-1][4] - 1
            if abs(r) > 0.5: continue
            d = dt.date.fromisoformat(b[i][0][:10]); month[d.month].append(r)
            if d.weekday() < 5: dow[d.weekday()].append(r)
        ap = [atr(b, i)/b[i][4] for i in range(len(b)-min(len(b)-15, 1260), len(b))]
        cur = ap[-1]; pct = sum(1 for x in ap if x <= cur)/len(ap)
        vol.append({"sym": s, "atr_pct": round(cur*100, 2), "pctile_5y": round(pct, 2), "regime": "high" if pct > .8 else "low" if pct < .2 else "normal"})
        if hc:
            try:
                ed = [r[0] for r in hc.execute("select date from fundamentals where sym=? and kind='earnings'", (s.split(".")[0],))]
            except Exception: ed = []
            idx = {x[0][:10]: i for i, x in enumerate(b)}
            for e in ed:
                i = idx.get(e[:10])
                if i and i+1 < len(b): earn.append(b[i+1][4]/b[i-1][4] - 1)
    jr = os.path.join(BASE, "state", "learning", seat, "journal.jsonl"); own = {}
    if os.path.exists(jr):
        for ln in open(jr):
            try: x = json.loads(ln)
            except Exception: continue
            r = x.get("r_multiple")
            if r is None or abs(r) > 20: continue
            k = x.get("setup") or x.get("grade") or "unknown"
            if isinstance(k, dict): k = k.get("name") or k.get("type") or k.get("kind") or "setup"
            own.setdefault(str(k)[:40], []).append(r)
    mem = {"seat": seat, "desk": desk, "built_wita": dt.datetime.now().strftime("%Y-%m-%d %H:%M WITA"), "paper_only": True,
           "universe_n": len(U), "symbols_with_history": sum(src.values()), "bars": nb, "date_range": rng, "bar_sources": src,
           "method": "daily bars; entry=close; stop=1.5*ATR14; exit=10 bars or stop; R in stop units",
           "setups": {k: summ(v) for k, v in agg.items()},
           "seasonality_month_avg_pct": {m: round(sum(v)/len(v)*100, 3) for m, v in month.items() if v},
           "seasonality_dow_avg_pct": {["Mon","Tue","Wed","Thu","Fri"][d]: round(sum(v)/len(v)*100, 3) for d, v in dow.items() if v},
           "earnings_reaction": ({"n": len(earn), "avg_abs_move_pct": round(sum(abs(x) for x in earn)/len(earn)*100, 2), "up_rate": round(sum(1 for x in earn if x > 0)/len(earn), 3)} if earn else {"n": 0, "note": "FMP earnings history still backfilling"}),
           "vol_regime": sorted(vol, key=lambda x: -x["pctile_5y"])[:25],
           "desk_record": {k: summ(v) for k, v in own.items()}}
    return mem
def md(m):
    L = [f"# Core memory — {m['seat']} ({m['desk']}) — built {m['built_wita']} — PAPER",
         f"History: {m['symbols_with_history']}/{m['universe_n']} symbols, {m['bars']} daily bars, {m['date_range'][0]}→{m['date_range'][1]} ({m['bar_sources']}). {m['method']}.",
         "## Setup base rates (mechanical)"]
    L += [f"- {k}: n={v['n']} win={v.get('win_rate')} avgR={v.get('avg_r')}" for k, v in m["setups"].items()]
    L.append("## Seasonality (avg daily % by month): " + ", ".join(f"{k}:{v}" for k, v in m["seasonality_month_avg_pct"].items()))
    L.append("Day-of-week: " + ", ".join(f"{k}:{v}" for k, v in m["seasonality_dow_avg_pct"].items()))
    L.append(f"## Earnings reactions: {m['earnings_reaction']}")
    L.append("## Vol regime (ATR% vs 5y): " + "; ".join(f"{x['sym']} {x['atr_pct']}% p{x['pctile_5y']} {x['regime']}" for x in m["vol_regime"]))
    L.append("## This desk's own record (R capped |R|≤20): " + (", ".join(f"{k}: n={v['n']} win={v.get('win_rate')} avgR={v.get('avg_r')}" for k, v in m["desk_record"].items()) or "no closed trades yet"))
    return "\n".join(L) + "\n"
def main():
    hc = sqlite3.connect(f"file:{HIST}?mode=ro", uri=True) if os.path.exists(HIST) else None
    pc = sqlite3.connect(f"file:{PIT}?mode=ro", uri=True); S = seats()
    for desk in ("veteran_desk", "thf"):
        od = os.path.join(BASE, "state", desk, "seat_memory"); os.makedirs(od, exist_ok=True)
        for seat, j in S.items():
            m = build(seat, j, hc, pc, desk)
            for ext, body in (("json", json.dumps(m, indent=1)), ("md", md(m))):
                p = os.path.join(od, f"{seat}.{ext}"); t = p + ".tmp"; open(t, "w").write(body); os.replace(t, p)
            print(desk, seat, m["symbols_with_history"], m["bars"], m["date_range"], flush=True)
if __name__ == "__main__": main()
