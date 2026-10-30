#!/usr/bin/env python3
"""hunt_giveback.py - Brett HARD DNA 2026-10-29 17:17 WITA (spec final TFO 17:21 WITA): giveback protection for the
equity/session hunt engines (asia_hunt48 JP/HK/KR/SG/TW/LSE, asx_hunt48, hunt48_short_paper main+gpt). PAPER ONLY.

While a FILLED position has open profit at a completed bar close, EARLY BANK the whole remaining position at that close on ANY of:
  (a) peak open profit reached >= 0.5R AND it has since given back >= 50% of that peak;
  (b) the bar closes through the prior bar's low (longs) / high (shorts), against the position, on >= 2x average volume;
  (c) a news item is logged for the symbol after the fill (state/veteran_desk/news_alerts.jsonl, or t["news_utc"]).
R = original risk = |fill - original stop|. Peak uses the bar's favourable extreme (high for longs, low for shorts).
Conservative choice (documented): bank ALL of the remainder, so the open profit is kept rather than half of it.
Additive only: runs AFTER the engine's stop / T1 half / T2 / thesis checks; never moves a stop, never changes the
+2%/T1 half-bank, never arms or opens anything. Every early bank logs reason, exit price and rail_px (the normal rail
exit level at that moment: live stop/trail). Python 3.9 compatible."""
import datetime as dt, json, os
PEAK_MIN_R = 0.5; GIVEBACK_FRAC = 0.5; VOL_MULT = 2.0; VOL_LOOKBACK = 20
BASE = os.environ.get("DESK_BASE", os.path.dirname(os.path.abspath(__file__)))
NEWS_FILE = os.path.join(BASE, "state", "veteran_desk", "news_alerts.jsonl")
LOG_FILE = os.path.join(BASE, "state", "hunt_giveback", "early_banks.jsonl")

def _ts(v):
    if v is None: return None
    if isinstance(v, dt.datetime): return v if v.tzinfo else v.replace(tzinfo=dt.timezone.utc)
    try:
        x = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")); return x if x.tzinfo else x.replace(tzinfo=dt.timezone.utc)
    except Exception: return None

def load_news(path=None):
    """{SYMBOL_UPPER: [utc, ...]} from the desk news log; unreadable/missing -> {} (never blocks)."""
    out = {}
    try:
        for ln in open(path or NEWS_FILE):
            try:
                r = json.loads(ln); u = _ts(r.get("utc")); k = str(r.get("ticker") or "").upper()
                if u and k: out.setdefault(k, []).append(u)
            except Exception: pass
    except Exception: pass
    return out

def news_hit(t, fill_utc, bar_end, news):
    syms = {str(t.get(k) or "").upper() for k in ("ticker", "sym", "yahoo")} - {""}
    times = [u for s in syms for u in (news or {}).get(s, [])]
    nu = _ts(t.get("news_utc"))
    if nu: times.append(nu)
    f = _ts(fill_utc); e = _ts(bar_end)
    return any((f is None or u > f) and (e is None or u <= e) for u in times)

def check(t, bar, prev_bars, sg, entry, orig_stop, fill_utc=None, bar_end=None, news=None):
    """Update t['gb_peak_R']; return reason 'a'|'b'|'c' if an early bank is due at bar['c'], else None.
    prev_bars: completed bars BEFORE this one (oldest first). Requires open profit at this close."""
    R = abs(float(entry) - float(orig_stop))
    if R <= 0: return None
    fav = bar["h"] if sg > 0 else bar["l"]
    t["gb_peak_R"] = max(float(t.get("gb_peak_R") or 0.0), sg * (fav - entry) / R)
    cur_R = sg * (bar["c"] - entry) / R
    if cur_R <= 0: return None                                   # no open profit -> never an early bank
    pk = t["gb_peak_R"]
    if pk >= PEAK_MIN_R and (pk - cur_R) >= GIVEBACK_FRAC * pk: return "a"
    if prev_bars:
        pb = prev_bars[-1]; vs = [x.get("v") for x in prev_bars[-VOL_LOOKBACK:] if x.get("v")]
        v = bar.get("v")
        through = (bar["c"] < pb["l"]) if sg > 0 else (bar["c"] > pb["h"])
        if through and v and vs and v >= VOL_MULT * (sum(vs) / len(vs)): return "b"
    if news_hit(t, fill_utc, bar_end, news): return "c"
    return None

REASON = {"a": "giveback >=50% of >=0.5R peak", "b": "close through prior bar on >=2x avg volume", "c": "news logged for symbol"}

def record(t, reason, exit_px, rail_px, when, engine, path=None):
    row = dict(utc=_ts(when).isoformat() if _ts(when) else str(when), engine=engine, sym=t.get("ticker") or t.get("sym"),
               direction=t.get("direction"), reason=reason, reason_text=REASON.get(reason), exit_px=round(float(exit_px), 6),
               rail_px=(round(float(rail_px), 6) if rail_px is not None else None), peak_R=round(float(t.get("gb_peak_R") or 0), 3),
               paper=True)
    t["giveback"] = row; t.setdefault("giveback_log", []).append(row)
    try:
        p = path or LOG_FILE; os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a") as f: f.write(json.dumps(row) + "\n")
    except Exception: pass
    return row

# ---- 1R half-bank + fee floor (Brett 2026-10-29 17:30/17:34 WITA; TFO reviewer amendment 17:37) ----
# Half-bank when NET open profit >= 1R of ORIGINAL risk (1.5R for trend setups). Net = after entry+exit commission,
# slippage and borrow (shorts). Only AFTER that half-bank does the stop move to the FEE FLOOR (entry +/- round-trip
# cost per share), then the existing ATR/structure trail. +2% no longer moves anything.
# Brett 17:38 WITA 'Yes': FULL FLAT at net >= 3R of ORIGINAL risk replaces the +5% full flat on these hunts (bankfinal).
HALF_R = 1.0; HALF_R_TREND = 1.5
def half_r(t):
    tags = " ".join(str(t.get(k)) for k in ("setup", "setup_tag", "seat", "setup_kind", "theme") if isinstance(t.get(k), str)).lower()
    return HALF_R_TREND if "trend" in tags else HALF_R
def fee_floor(entry, sg, rt_cost, qty):
    """stop level at which closing the residual nets zero after round-trip costs (sg=+1 long, -1 short)."""
    return float(entry) + sg * (float(rt_cost) / float(qty)) if qty and qty > 0 else float(entry)
FLAT_R = 3.0
def flat_level(entry, sg, risk_usd, rt_cost, qty_open, q2u=1.0):
    """price at which the OPEN qty nets FLAT_R x original risk after round-trip costs (R locked at entry)."""
    return float(entry) + sg * (FLAT_R * float(risk_usd) + float(rt_cost)) / (float(qty_open) * float(q2u))
# ---- TIME STOP (Brett 2026-10-29 17:44 WITA "Yes go ahead"; bankfinal) ----
# Exit if the trade has NOT reached +0.5R NET (original R) by the close of session 2 (48h hunt hold). Deadline fixed at
# entry (time_stop_utc, set once, never extended). Missing mark -> hold. exit_reason rail_time_stop; counterfactual
# (live stop/rail) logged to early_banks.jsonl. No stop move at 0.5R (rejected).
TIME_STOP_R = 0.5
TIME_STOP_FROM = "2026-10-29T09:45:00+00:00"   # deadline fixed AT entry -> only trades filled after deploy (17:45 WITA); earlier fills (e.g. FLG) grandfathered
def time_stop_applies(fill_iso):
    import datetime as _d
    return bool(fill_iso) and _d.datetime.fromisoformat(str(fill_iso)) >= _d.datetime.fromisoformat(TIME_STOP_FROM)
REASON["time_stop"] = "time stop: < +0.5R net of original risk by session-2 close (48h hunt hold)"
def time_stop_due(t, deadline, when, mark, sg, entry, orig_stop, shares, rt_cost, q2u=1.0):
    """True -> exit now. Hold when mark missing, before deadline, or net R >= 0.5 at the mark."""
    if mark is None or deadline is None or when < deadline: return False
    rk = abs(float(entry) - float(orig_stop)) * float(shares) * float(q2u)
    if rk <= 0: return False
    net = sg * (float(mark) - float(entry)) * float(shares) * float(q2u) - float(rt_cost)
    t["time_stop_net_R"] = round(net / rk, 3)
    return net / rk < TIME_STOP_R
