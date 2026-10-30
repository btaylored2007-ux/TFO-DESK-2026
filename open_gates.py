#!/usr/bin/env python3
"""OPEN GATES - pre-open / open gate layer for every hunt book. PAPER ONLY. No orders, no keys.
Brett add-on approved 2026-10-27 20:01 WITA (go live on paper now):
 (1) GAP: re-score each armed setup on its pre-market / auction price; cancel if the gap is past its entry or its stop.
 (2) OPENING RANGE: wait out the first OR_MIN minutes (15-30), then enter only on a break of that range, with volume
     confirmation (opening-range volume >= VOL_MIN x the average same-window volume of the last 20 sessions).
 (5) RELATIVE STRENGTH vs the market index pre-market: longs only if beating it, shorts only if lagging it.
 (7) LIVE LIQUIDITY: skip a name whose opening spread is > SPREAD_MAX x normal.
Cancelled setups -> the orchestrator promotes the next backup after re-checking all gates.
Pure gate functions are unit-tested; data helpers use yfinance only (read-only market data)."""
import datetime as dt, json, math, os, statistics, time
UTC = dt.timezone.utc
OR_MIN = 30; VOL_MIN = 1.2; SPREAD_MAX = 2.0; MAX_R_STRETCH = 1.5; T1_R = 0.45; T2_R = 1.5
INDEX = dict(us="SPY", asx="^AXJO", jp="^N225", hk="^HSI", sg="^STI", kr="^KS11", tw="^TWII", lse="^FTSE")

def _f(x):
    try:
        v = float(x); return v if v == v else None
    except Exception: return None
def is_long(t): return (t.get("direction") or "short").lower() == "long"

# ------------------------------------------------------------------ pure gates
def gap_gate(t, px):
    """(1) price before the open vs plan. Returns (ok, reason, gap_R). gap_R = distance still to the trigger in R (+ = not yet reached)."""
    if px is None: return True, "no pre-market/auction print: gap unverified (flat assumed)", None
    tr, st = t["trigger"], t["stop"]; R = abs(st - tr) or 1e-9; lg = is_long(t)
    gap_R = round(((tr - px) if lg else (px - tr)) / R, 3)
    if (px >= st) if not lg else (px <= st): return False, f"gap past stop: {px:g} vs stop {st:g}", gap_R
    if (px <= tr) if not lg else (px >= tr): return False, f"gap past entry: {px:g} vs trigger {tr:g}", gap_R
    return True, None, gap_R
def pct(a, b): return None if (a is None or not b) else 100.0 * (a / b - 1)
def rs_gate(t, stock_chg, index_chg):
    """(5) longs only if beating the index, shorts only if lagging. Changes in % vs prior close."""
    if index_chg is None: return False, "index pre-market change unknown (fail closed)"
    s = 0.0 if stock_chg is None else stock_chg
    if is_long(t): return (s > index_chg, None if s > index_chg else f"long not beating index: {s:+.2f}% vs {index_chg:+.2f}%")
    return (s < index_chg, None if s < index_chg else f"short not lagging index: {s:+.2f}% vs {index_chg:+.2f}%")
def opening_range(bars, open_utc, minutes=OR_MIN):
    """bars: [{start(datetime UTC), h, l, c, v, mins}] intraday. Returns dict(hi, lo, vol, complete) for [open, open+minutes)."""
    end = open_utc + dt.timedelta(minutes=minutes)
    w = [b for b in bars if open_utc <= b["start"] < end]
    if not w: return None
    last_end = max(b["start"] + dt.timedelta(minutes=b.get("mins", 15)) for b in w)
    return dict(hi=max(b["h"] for b in w), lo=min(b["l"] for b in w), vol=sum(b.get("v") or 0 for b in w), complete=last_end >= end, n=len(w))
def or_volume_baseline(bars, open_times, minutes=OR_MIN):
    """Average opening-window volume over prior sessions (open_times = prior session opens, UTC)."""
    vols = []
    for o in open_times:
        e = o + dt.timedelta(minutes=minutes); v = sum(b.get("v") or 0 for b in bars if o <= b["start"] < e)
        if v > 0: vols.append(v)
    return (sum(vols) / len(vols), len(vols)) if vols else (None, 0)
def volume_gate(or_vol, base):
    if not base: return False, "no opening-volume baseline (fail closed)", None
    r = or_vol / base
    return (r >= VOL_MIN, None if r >= VOL_MIN else f"opening volume {r:.2f}x < {VOL_MIN}x normal", round(r, 2))
def or_entry(t, orng, risk_usd, buf_frac=0.0005):
    """(2) Re-level so the entry is a BREAK of the opening range (the stricter of plan trigger and the range edge), stop
    unchanged, T1/T2 re-derived (T2 = 1.5R), shares re-sized to the same risk. Returns (new_t | None, reason)."""
    lg = is_long(t); st = t["stop"]
    if (orng["hi"] >= st) if not lg else (orng["lo"] <= st): return None, f"opening range traded through stop {st:g}"
    edge = orng["hi"] * (1 + buf_frac) if lg else orng["lo"] * (1 - buf_frac)
    trig = max(t["trigger"], edge) if lg else min(t["trigger"], edge)
    mf = trig * (1.0015 if lg else 0.9985)
    R0 = abs(t["stop"] - t["max_fill"]); R = abs(st - mf)
    if R0 and R > MAX_R_STRETCH * R0: return None, f"range break {trig:g} stretches risk to {R / R0:.2f}x plan R (> {MAX_R_STRETCH})"
    sg = 1 if lg else -1
    n = dict(t, trigger=round(trig, 4), max_fill=round(mf, 4), t1=round(mf + sg * T1_R * R, 4), t2=round(mf + sg * T2_R * R, 4),
             shares=int(risk_usd // R) if R > 0 else 0, or_hi=round(orng["hi"], 4), or_lo=round(orng["lo"], 4),
             or_note=f"entry = break of {OR_MIN}m opening range ({'high' if lg else 'low'} {edge:g}); plan trigger {t['trigger']:g}")
    if n["shares"] <= 0: return None, "zero shares after re-size"
    return n, None
def corwin_schultz_bp(bars):
    """Normal spread estimate (bp) from consecutive intraday high/low pairs (Corwin & Schultz 2012); negatives -> 0."""
    k = 3 - 2 * math.sqrt(2); out = []
    for a, b in zip(bars, bars[1:]):
        if min(a["l"], b["l"]) <= 0: continue
        beta = math.log(a["h"] / a["l"]) ** 2 + math.log(b["h"] / b["l"]) ** 2
        gamma = math.log(max(a["h"], b["h"]) / min(a["l"], b["l"])) ** 2
        al = (math.sqrt(2 * beta) - math.sqrt(beta)) / k - math.sqrt(gamma / k)
        s = 2 * (math.exp(al) - 1) / (1 + math.exp(al)); out.append(max(s, 0.0))
    return round(1e4 * statistics.median(out), 2) if out else None
SPREAD_DIR = None   # set by the orchestrator (state/hunt_orch/spreads); None = no history kept (tests)
def tick_floor_bp(px, tick=0.01): return round(1e4 * tick / px, 2) if px else None
def normal_spread_bp(sym, cs_bp, px, hist=None):
    """Normal spread = max(Corwin-Schultz estimate, one price tick, median of this desk's own recorded opening spreads (>=3))."""
    if hist is None and SPREAD_DIR:
        try: hist = json.load(open(os.path.join(SPREAD_DIR, sym.replace("/", "_") + ".json")))
        except Exception: hist = []
    h = [x for x in (hist or []) if x]
    c = [x for x in (cs_bp, tick_floor_bp(px), statistics.median(h) if len(h) >= 3 else None) if x]
    return round(max(c), 2) if c else None
def record_spread(sym, bp):
    if not (SPREAD_DIR and bp): return
    p = os.path.join(SPREAD_DIR, sym.replace("/", "_") + ".json")
    try: h = json.load(open(p))
    except Exception: h = []
    os.makedirs(SPREAD_DIR, exist_ok=True); json.dump((h + [bp])[-30:], open(p, "w"))
def spread_gate(live_bp, normal_bp):
    """(7) skip if live spread > 2x normal. Unknown live quote -> allowed but flagged UNVERIFIED (reported)."""
    if live_bp is None: return True, "spread UNVERIFIED (no live bid/ask)"
    if not normal_bp: return True, "spread normal unknown: UNVERIFIED"
    return (live_bp <= SPREAD_MAX * normal_bp, None if live_bp <= SPREAD_MAX * normal_bp else f"spread {live_bp:.1f}bp > {SPREAD_MAX}x normal {normal_bp:.1f}bp")

# ------------------------------------------------------------------ data (yfinance, read-only)
def intraday(sym, interval="5m", period="5d", prepost=False):
    import yfinance as yf
    d = yf.Ticker(sym).history(period=period, interval=interval, prepost=prepost)
    m = int(interval.rstrip("m"))
    return [dict(start=i.to_pydatetime().astimezone(UTC), o=float(r.Open), h=float(r.High), l=float(r.Low), c=float(r.Close), v=float(r.Volume or 0), mins=m)
            for i, r in d.iterrows() if r.Close == r.Close]
def prev_close(sym, open_utc):
    import yfinance as yf
    d = yf.Ticker(sym).history(period="10d", interval="1d")
    cl = [(i.date(), float(r.Close)) for i, r in d.iterrows() if r.Close == r.Close]
    od = open_utc.date(); prior = [c for (dd, c) in cl if dd < od]
    return prior[-1] if prior else None
def pre_open_px(sym, open_utc):
    """Last pre-market (US) / pre-open print before the open; None when the venue publishes none."""
    try:
        b = [x for x in intraday(sym, "1m", "1d", prepost=True) if open_utc - dt.timedelta(hours=6) <= x["start"] < open_utc]
        return b[-1]["c"] if b else None
    except Exception: return None
LIVE_QUOTE_MIN_SIZE = 50
LIVE_SPREAD_SANE_BP = 500.0   # a >5% quoted spread on a hunt-screened liquid name is a bad data quote, not liquidity
def live_spread_bp(sym):
    try:
        import yfinance as yf
        i = yf.Ticker(sym).info or {}; bid, ask = _f(i.get("bid")), _f(i.get("ask"))
        if not bid or not ask or ask <= bid: return None
        bs, az = _f(i.get("bidSize")), _f(i.get("askSize"))
        if bs is not None and az is not None and min(bs, az) < LIVE_QUOTE_MIN_SIZE: return None   # odd-lot/junk book (AAPL/MSFT/SFD show size 2-4 with 1-16% spreads) -> UNVERIFIED
        bp = round(1e4 * (ask - bid) / ((ask + bid) / 2), 2)
        if bp > LIVE_SPREAD_SANE_BP: return None   # gate overlord 2026-10-28 22:10 WITA: Yahoo .info junk quote (e.g. SFD 16.57/19.47 size 2/2 = 1609bp) -> UNVERIFIED, not a real spread
        return bp
    except Exception: return None

def pre_open_check(t, open_utc, index_sym, cache=None, px_fn=pre_open_px, pc_fn=prev_close):
    """Gates (1) + (5) on the pre-market/auction print. Returns dict(ok, why[], gap_R, chg, idx_chg, pm_px)."""
    cache = cache if cache is not None else {}
    if index_sym not in cache: cache[index_sym] = (px_fn(index_sym, open_utc), pc_fn(index_sym, open_utc))
    ip, ic = cache[index_sym]; sp, sc = px_fn(t["ticker"], open_utc), pc_fn(t["ticker"], open_utc)
    why = []; ok1, w1, gR = gap_gate(t, sp)
    if not ok1: why.append(w1)
    ok5, w5 = rs_gate(t, pct(sp, sc) if sp is not None else 0.0, pct(ip, ic) if ip is not None else None)
    if not ok5: why.append(w5)
    return dict(ok=not why, why=why, gap_R=gR, pm_px=sp, chg=pct(sp, sc) if sp is not None else None, idx_chg=pct(ip, ic) if ip else None, note=w1 if ok1 else None)
def open_check(t, open_utc, risk_usd, or_min=OR_MIN, bars_fn=None, spread_fn=live_spread_bp, prior_opens=None):
    """Gates (2) + (7) after the opening range. Returns dict(ok, why[], new_t, vol_ratio, spread_bp, normal_bp)."""
    bars_fn = bars_fn or (lambda s: intraday(s, "5m", "30d"))
    bars = bars_fn(t["ticker"]); orng = opening_range(bars, open_utc, or_min); why = []
    if not orng or not orng["complete"]: return dict(ok=False, why=["opening range not complete / no bars"], wait=True)
    opens = prior_opens or [b["start"] for i, b in enumerate(bars) if b["start"] < open_utc and (i == 0 or (b["start"] - bars[i - 1]["start"]).total_seconds() > 3600)][-20:]
    base, n = or_volume_baseline(bars, opens, or_min)
    okv, wv, vr = volume_gate(orng["vol"], base)
    if not okv: why.append(wv)
    prior = [b for b in bars if b["start"] < open_utc][-78:]
    last_px = bars[-1]["c"] if bars else None
    normal = normal_spread_bp(t["ticker"], corwin_schultz_bp(prior), last_px); live = spread_fn(t["ticker"])
    record_spread(t["ticker"], live)
    oks, ws = spread_gate(live, normal)
    if not oks: why.append(ws)
    nt, wo = or_entry(t, orng, risk_usd)
    if nt is None: why.append(wo)
    return dict(ok=not why, why=why, new_t=nt, vol_ratio=vr, vol_base_n=n, spread_bp=live, normal_bp=normal, spread_note=ws if oks else None, or_=orng)
