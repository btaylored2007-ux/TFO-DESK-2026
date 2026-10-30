"""oanda_designs.py - signal plug-ins for oanda_sleeve.py (TFO OANDA PRACTICE sleeve, 2026-10-04).
Formulas identical to research/oanda_ml/code/run_l.py (OX-L1, OX-L2), tested with oml.playbook_sim (live rails).
Both FAILED the desk gate (see STATUS) - they trade on the PRACTICE account only because Brett authorised practice trading 4 Oct 19:06."""
import os, json, time, datetime as dt, urllib.request
STATUS = {
  "OXL1": "Gold SMA200 trend (live rails). FAILED desk gate: IS 2006-24 CAGR +0.7%/yr, Sharpe 0.15, maxDD -19% at 1% risk/trade; see OANDA_ML_REPORT_20261004.md",
  "OXL2": "G10 carry, positive-financing pairs (live rails). FAILED desk gate: IS 2002-24 CAGR +2.7%/yr, Sharpe 0.24, maxDD -77% at 1% risk/trade (2008 carry crash); run at 0.5% risk",
  "OXP1": "Commodity trend pullback (oanda_setups.py, 16 Oct 2026). Backtest 2011-26 metals+energy, 2xATR stop/3xATR trail, spread costs, NO financing: n=644 win 30% avgR +0.09 (pre-2019 +0.10, 2019+ +0.09). Same rule on FX/indices LOST (-0.06R) -> not traded there",
  "OXB1": "Commodity vol-filtered 55d breakout (oanda_setups.py, 16 Oct 2026). Backtest 2011-26 metals+energy: n=200 win 33% avgR +0.38 (pre-2019 +0.55, 2019+ +0.24). Same rule on FX/indices LOST (-0.1 to -0.28R) -> not traded there",
  "OXT1": "Passive commodity trend (GPT idea 3, 16 Oct 2026): 63/126/252d returns agree, 4xATR stop, 6xATR trail. Backtest XAU/XAG/XCU/WTI/Brent 2011-26 incl. spread, slippage, financing at current rates: n=665 avgR +0.054 (pre-2019) / +0.042 (2019+). Broad FX/bond/index version FAILED",
  "OXF1": "Daily EMA20/60 trend, 20 pairs + gold (GPT blueprint 12 Oct 2026). FROZEN paper sleeve, NOT desk-gate tested; 0.5% risk (gold 0.25%)",
}
# 13 Oct 2026 (GPT blueprint Part C1/C7): immutable strategy versions; last_processed_bar is keyed per instrument + version.
# Part B EMA baseline (OXF1) and Part A TSMOM+carry (shadow score only) are versioned SEPARATELY and never auto-combined.
VERSION = {"OXL1": "OXL1-sma200-v1-20261004", "OXL2": "OXL2-carry-v2-20261012", "OXF1": "partB-ema20_60-v1-20261012",
           "OXP1": "OXP1-cmdty-pullback-v2-gpt-20261016", "OXB1": "OXB1-cmdty-breakout-v2-gpt-20261016", "OXT1": "OXT1-cmdty-passive-v1-gpt-20261016", "SHADOW_PARTA": "partA-tsmom_carry-v1-20261012-shadow"}
RISK = {"OXL1": 0.005, "OXL2": 0.005, "OXF1": 0.005, "OXF1_XAU": 0.0025, "OXP1": 0.0075, "OXB1": 0.01, "OXT1": 0.005}   # 16 Oct 2026 21:03 (Brett): validated commodity setups up to 1%
STOP_ATR = {"OXT1": 4.0}; TRAIL_ATR = {"OXT1": 6.0}; TIME_EXIT_BARS = {"OXP1": 60}
CT5 = ["XAU_USD", "XAG_USD", "XCU_USD", "WTICO_USD", "BCO_USD"]   # 12 Oct 2026 (Brett 21:41 AEDT): every design at ~0.5% equity per trade until the frozen scale gate passes
MAXSLOTS = {"OXL1": 1, "OXL2": 8, "OXF1": 4, "OXP1": 4, "OXB1": 4, "OXT1": 5}   # 13 Oct 2026: OXL1/OXL2 share the book's 8 trades / 4%; OXF1 has its own 4 trades / 2%
PRIORITY = {"OXL1": 0, "OXL2": 1, "OXF1": 2, "OXB1": 3, "OXP1": 4, "OXT1": 5}
CM6 = ["XAU_USD", "XAG_USD", "XPT_USD", "XCU_USD", "BCO_USD", "WTICO_USD"]   # 16 Oct 2026: the only asset class where the setups backtested positive   # queue order: existing designs first, then OXF1 (one position per pair across the desk)
# OXF1 (12 Oct 2026, GPT blueprint F): 7 majors + 13 crosses, no exotics; gold traded separately at 0.25% risk.
F20 = ['EUR_USD','GBP_USD','USD_JPY','USD_CHF','AUD_USD','NZD_USD','USD_CAD',
       'EUR_GBP','EUR_JPY','EUR_CHF','EUR_AUD','EUR_CAD','GBP_JPY','GBP_CHF','GBP_AUD','GBP_CAD','AUD_JPY','AUD_NZD','AUD_CAD','NZD_JPY']       # 12 Oct 2026: widened; real limits are the portfolio open-risk cap (4%) and per-currency net cap (1.5%)
P32 = ['EUR_USD','GBP_USD','USD_JPY','USD_CHF','AUD_USD','NZD_USD','USD_CAD','EUR_JPY','GBP_JPY','AUD_JPY','NZD_JPY','CAD_JPY','CHF_JPY','EUR_GBP',
       'EUR_CHF','EUR_AUD','EUR_CAD','EUR_NZD','GBP_AUD','GBP_CAD','GBP_CHF','GBP_NZD','AUD_NZD','AUD_CAD','AUD_CHF','NZD_CAD','NZD_CHF','CAD_CHF',
       'EUR_NOK','EUR_SEK','USD_NOK','USD_SEK']
# 12 Oct 2026: ENTRY universe = 7 majors + 11 liquid crosses (18). NOK/SEK and thin crosses dropped for new entries. P32 stays for EXIT
# scoring so any trade already open on a dropped pair is still managed on its own signal.
P18 = ['EUR_USD','GBP_USD','USD_JPY','USD_CHF','AUD_USD','NZD_USD','USD_CAD',
       'EUR_JPY','GBP_JPY','AUD_JPY','CAD_JPY','CHF_JPY','EUR_GBP','EUR_CHF','EUR_AUD','GBP_CHF','AUD_CHF','AUD_NZD']
CCY_CODE = {"USD":"US","EUR":"EZ","JPY":"JP","GBP":"GB","CHF":"CH","AUD":"AU","NZD":"NZ","CAD":"CA","NOK":"NO","SEK":"SE"}
LIVE_FIN = {}   # injected by oanda_sleeve.daily(): {instrument: {"longRate":..,"shortRate":..}} from OANDA /instruments
BASE = os.path.dirname(os.path.abspath(__file__)); CACHE = os.path.join(BASE, "state", "oanda", "rates_cache.json")
def universe(names):
    u = []
    if "OXL1" in names: u.append("XAU_USD")
    if "OXL2" in names: u += P18
    if "OXF1" in names: u += F20 + ["XAU_USD"]
    if "OXP1" in names or "OXB1" in names or "OXT1" in names: u += CM6
    return list(dict.fromkeys(u))
def exit_universe(names):
    return list(dict.fromkeys((CM6 if ("OXP1" in names or "OXB1" in names or "OXT1" in names) else []) + (["XAU_USD"] if ("OXL1" in names or "OXF1" in names) else []) + (P32 if "OXL2" in names else []) + (F20 if "OXF1" in names else [])))
def _rates():
    """OECD 3m interbank (FRED IR3TIB01xxM156N), monthly; cached 24h."""
    if os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < 86400: return json.load(open(CACHE))
    out = {}
    for c, code in CCY_CODE.items():
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id=IR3TIB01{code}M156N"
        txt = urllib.request.urlopen(url, timeout=30).read().decode().splitlines()[1:]
        out[c] = [(ln.split(",")[0], float(ln.split(",")[1])) for ln in txt if ln.split(",")[1] not in ("", ".")]
    json.dump(out, open(CACHE, "w")); return out
def rate_asof(ccy, day, R):
    """value of the latest month m with (m + 1 month) <= day  (1-month publication lag, as in research rate_daily(lag_months=1))."""
    best = None
    for d, v in R[ccy]:
        y, m, _ = map(int, d.split("-")); m += 1
        if m > 12: y, m = y + 1, 1
        if dt.date(y, m, 1) <= day: best = v
    return None if best is None else best / 100.0
def _gold(data):
    cs = data.get("XAU_USD")
    if not cs or len(cs) < 200: return None
    sma = sum(c["c"] for c in cs[-200:]) / 200.0; x = cs[-1]["c"] - sma
    return 1.0 if x > 0 else -1.0 if x < 0 else 0.0
def _carry(ins, data, R, for_exit=False):
    """Entry score (for_exit=False): rate diff if |diff| >= 1.5% AND OANDA's live quoted financing for that side is a credit >= 0.5%/yr, else None.
    Exit score (for_exit=True, 12 Oct 2026): None ONLY when inputs are missing (no candles / no rate). Valid inputs -> the signed rate diff,
    or 0.0 when |diff| < 1.5% (a real 'flat'). The live financing guard is an ENTRY filter only: OANDA intermittently quotes 0/0 financing
    (e.g. at the Sunday 17:00 NY rollover), which made _carry return None and closed AUD/USD/GBP_JPY on 12 Oct 08:05 AEDT."""
    cs = data.get(ins)
    if not cs: return None
    day = dt.date.fromisoformat(cs[-1]["t"][:10]) + dt.timedelta(days=1)  # candle open 21:00/22:00 UTC -> trading date (research: +4h normalise)
    b, q = ins.split("_"); rb, rq = rate_asof(b, day, R), rate_asof(q, day, R)
    if rb is None or rq is None: return None
    s = rb - rq
    if for_exit: return s if abs(s) >= 0.015 else 0.0
    if abs(s) < 0.015: return None
    f = LIVE_FIN.get(ins) or {}
    # premise guard (red-team kill test): OANDA's own quoted financing for this direction must be a credit >= 0.5%/yr.
    # 13 Oct 2026 (GPT N08): MISSING financing data is not a pass - no entry without a valid side-specific rate.
    raw = f.get("longRate" if s > 0 else "shortRate")
    try: q = float(raw)
    except (TypeError, ValueError): return None
    if q < 0.005: return None
    return s
def _trend(ins, data):
    """OXF1 state: +1 if EMA20 > EMA60 on completed 17:00-NY daily closes, -1 if below, 0 if equal; None if < 61 bars.
    Returns (dir, last_cross_time) - last_cross_time = candle time of the most recent sign change (None if none in history)."""
    import oanda_risk as RK
    cs = data.get(ins)
    if not cs or len(cs) < 61: return None, None
    c = [x["c"] for x in cs]; f = RK.ema_series(c, 20); sl = RK.ema_series(c, 60)
    sg = [None if (a is None or b is None) else (1 if a > b else -1 if a < b else 0) for a, b in zip(f, sl)]
    d = sg[-1]; cross = None
    for i in range(len(sg) - 1, 0, -1):
        if sg[i - 1] is not None and sg[i - 1] != sg[i]: cross = cs[i]["t"]; break
    return d, cross
OXF1_MIN_BARS = 300   # blueprint B3: >= 300 completed bars before an OXF1 entry decision
def _fresh_cross(ins, data, min_bars=OXF1_MIN_BARS):
    """13 Oct 2026 (audit #1, blueprint B3): OXF1 ENTRY signal. +1 only if EMA20_prev <= EMA60_prev and EMA20_last > EMA60_last on the
    last completed bar; -1 for the mirror; else 0. (0, None) if < min_bars bars. Returns (dir, cross_time = last bar time)."""
    import oanda_risk as RK
    cs = data.get(ins)
    if not cs or len(cs) < max(min_bars, 61): return 0, None
    c = [x["c"] for x in cs]; f = RK.ema_series(c, 20); sl = RK.ema_series(c, 60)
    if f[-2] is None or sl[-2] is None: return 0, None
    if f[-2] <= sl[-2] and f[-1] > sl[-1]: return 1, cs[-1]["t"]
    if f[-2] >= sl[-2] and f[-1] < sl[-1]: return -1, cs[-1]["t"]
    return 0, None
def signals(names, data):
    out = []
    if "OXL1" in names:
        g = _gold(data)
        if g: out.append(dict(design="OXL1", ins="XAU_USD", dir=int(g), score=10.0, risk_pct=RISK["OXL1"], reason=f"XAU close {'above' if g>0 else 'below'} SMA200"))
    if "OXL2" in names:
        R = _rates(); c = []
        for ins in P18:
            s = _carry(ins, data, R)
            if s is not None: c.append(dict(design="OXL2", ins=ins, dir=1 if s > 0 else -1, score=abs(s), risk_pct=RISK["OXL2"],
                                            reason=f"rate diff {s*100:+.2f}% (OECD 3m, 1m lag) >= 1.5%; OANDA quoted financing credit {float((LIVE_FIN.get(ins) or {}).get('longRate' if s>0 else 'shortRate') or 0)*100:+.2f}%/yr"))
        out += sorted(c, key=lambda x: -x["score"])[:MAXSLOTS["OXL2"] * 3]
    if "OXF1" in names:
        for ins in F20 + ["XAU_USD"]:
            d, cross = _fresh_cross(ins, data)   # 13 Oct 2026 audit #1: ENTRY only on a fresh crossover on the last completed bar
            if d: out.append(dict(design="OXF1", ins=ins, dir=d, score=1.0, cross_utc=cross,
                                  risk_pct=RISK["OXF1_XAU"] if ins == "XAU_USD" else RISK["OXF1"],
                                  reason=f"fresh EMA20/60 cross {'up' if d > 0 else 'down'} on bar {cross}"))
    if "OXP1" in names or "OXB1" in names:
        import oanda_setups as X
        for ins in CM6:
            cs = data.get(ins)
            if not cs or len(cs) < X.MIN_BARS + 1: continue
            I = X.Ind(cs); n = len(cs) - 1
            for dz, fn, why in (("OXB1", X.breakout, "close beyond prior 55-bar extreme after ATR compression, EMA50/200 aligned"),
                                ("OXP1", X.pullback, "EMA50/200 trend, pullback to EMA20, close beyond prior bar extreme")):
                if dz in names:
                    d = fn(I, n)
                    if d: out.append(dict(design=dz, ins=ins, dir=d, score=1.0, risk_pct=RISK[dz], trigger=X.trigger_level(I, n, d),
                                          reason=f"{dz} {'long' if d > 0 else 'short'}: {why} (bar {cs[-1]['t'][:10]}); stop-entry trigger next session"))
            if "OXT1" in names and ins in CT5:
                d = X.passive(I, n)
                if d: out.append(dict(design="OXT1", ins=ins, dir=d, score=0.5, risk_pct=RISK["OXT1"], stop_atr=STOP_ATR["OXT1"],
                                      reason=f"OXT1 {'long' if d > 0 else 'short'}: 63/126/252d returns all {'positive' if d > 0 else 'negative'} (bar {cs[-1]['t'][:10]})"))
    return out
def score_for(design, data, ins, d=None):
    """EXIT score for an open trade of `design` (from its clientExtensions comment). None = UNKNOWN (missing data) and must never
    close a position (12 Oct 2026). OXF1 exits on the opposite EMA crossover."""
    if design == "OXF1": return _trend(ins, data)[0]
    if design in ("OXP1", "OXB1", "OXT1"):   # 16 Oct 2026: own exit rule; returns the held direction (hold) or 0 (exit); None if unknown
        import oanda_setups as X
        cs = data.get(ins)
        if not cs or len(cs) < X.MIN_BARS + 1 or d is None: return None
        I = X.Ind(cs); return 0 if X.exit_signal(design, I, len(cs) - 1, d) else d
    if ins == "XAU_USD" and design == "OXL1": return _gold(data)
    if ins in P32 and design == "OXL2":
        try: R = _rates()
        except Exception: return None
        return _carry(ins, data, R, for_exit=True)
    return None
def design_of(t):
    """Design of an open trade: its clientExtensions comment; legacy fallback by instrument."""
    d = (t.get("clientExtensions") or {}).get("comment")
    if d in STATUS: return d
    return "OXL1" if t.get("instrument") == "XAU_USD" else "OXL2"
# 27 Oct 2026 21:xx WITA (Brett 20:58 WITA chat: 'OANDA paper trade setups tonight'; TFO trades its own daily-chart setups, PRACTICE):
# OXD1 = TFO discretionary daily-chart setups (trend pullback / breakout retest / range edge, structural stop, broker SL+TP attached),
# placed as GTD LIMIT/STOP entries by research/oanda_setups_20261027/place_setups.py. Book bucket. No signal generator (not in config
# "designs"), so score_for() returns None -> the sleeve HOLDS the trade (never a carry/OXL2 signal exit); broker SL/TP decide the exit,
# plus the book's tighten-only 2xATR chandelier trail. Registered here only so design_of() does not fall back to OXL2.
STATUS["OXD1"] = "TFO discretionary daily setups (27 Oct 2026, Brett). NOT backtested; reviewed per trade by GPT+Claude; vol-capped risk <= 1%"
VERSION["OXD1"] = "OXD1-tfo-daily-setups-v1-20261027"
MAXSLOTS["OXD1"] = 3
