#!/usr/bin/env python3
"""HUNT48 SINGLE ROOM DEFINITION + B1 SHORT SCREEN (PAPER ONLY).
Rules Auditor 2026-10-27 08:40 WITA (approved version of filter_review_20261027), used by asx_hunt48, asia_hunt48 and hunt_clearance:
  room_R = min(structure_room, T2_room) - cost_R ; MIN_ROOM_R = 1.0 (unchanged until 30+ closed hunt48 trades)
    structure_room = |nearest opposing level - max_fill| / R  (5-bar swing pivots + 20d/50d MA, LOOKBACK 1y daily, today excluded)
    T2_room        = |t2 - max_fill| / R                      (T2 - cost is a HARD gate, not info)
    no opposing structure level within 1y -> room_R = T2_room - cost_R (no free pass, no flag-pass)
  NEVER FAIL OPEN: missing daily bars / missing cost_R / bad levels -> BENCH; room_R is always written as a number (never null).
B1 short screen (first screen, all hunt48 books): short with RSI(14) < 30 -> blocked; RSI missing -> blocked (no default 50);
  short at a TRUE 20-day low (px within 1% of min of the last 20 daily closes, not the lo10 proxy) with 5d drop > 1.5 ATR -> blocked;
  missing bars/roc5/atr for that check -> blocked.  (Mirrors hunt_clearance BEHAVIOR lines 230/240.) No keys, no orders."""
# 2026-10-27 Brett pt1486u "make it actually trade": hunt48 ASX + jp/hk/sg/kr/tw second target = 1.5R (was shared CAL_T2_R 1.05R, which made
# room >= 1.0R need cost_R <= 0.05R -> 0/45 passed). Hard constant, NOT env-overridable. US/crypto/FX staging keep event_calendar.T2_R.
T2_R = 1.5
# Brett 2026-10-28 07:12 WITA: NEW hunt-book trades (US main/Claude/GPT, ASX, JP/HK/SG/KR/TW, London) = target +3% / stop -8% from entry
# (shorts -3% / +8%), sized so the 8% stop risks the per-trade cap. Open trades keep their levels. 48-72h time exit + max-3-open unchanged.
FIXED_TP_PCT = 0.03; FIXED_STOP_PCT = 0.08
def is_fixed(e, st, t2):
    try: return abs(abs(e - st) / e - FIXED_STOP_PCT) < 0.002 and abs(abs(t2 - e) / e - FIXED_TP_PCT) < 0.002
    except Exception: return False
import math   # audit retry-edge hardening (finite-bar filter)
MIN_ROOM_R = 1.0; LOOKBACK = "1y"; MIN_RSI_SHORT = 30.0; EXH_ATR = 1.5; LOW_PAD = 0.01
_CACHE = {}
# Review fix #3 (hunt_review_20261027, Brett-approved 27 Oct): one shared yfinance failure was scored as a RULE failure for every name
# (JP 35/48, HK 47/56 DISARMED). daily() now retries, keeps a per-symbol disk cache (<= CACHE_MAX_AGE_H old, used ONLY when the live
# fetch fails), never memoises a failure for the whole process, and records DATA_STATUS[sym] so callers can label DATA_FAIL != RULE_FAIL.
import os, json, time, datetime as _dt
RETRIES = 3; RETRY_SLEEP = (1.0, 3.0); FAIL_MEMO_S = 120; CACHE_MAX_AGE_H = 24.0
CACHE_DIR = os.path.join(os.environ.get("DESK_BASE", os.path.dirname(os.path.abspath(__file__))), "state", "hunt48_daily_cache")
DATA_STATUS = {}; _FAIL_AT = {}; _sleep = time.sleep
DATA_FAIL_MARKERS = ("data missing", "no daily bars", "NO_DAILY_BARS", "DATA_FAIL", "breakdown failed")
def is_data_fail(reason):
    """True when a block reason is a DATA failure (feed/fetch), not a trading-rule failure."""
    return any(m in str(reason) for m in DATA_FAIL_MARKERS)
def _cache_path(sym): return os.path.join(CACHE_DIR, "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in str(sym)) + ".json")
def _fetch_yf(sym):
    import yfinance as yf
    d = yf.Ticker(sym).history(period=LOOKBACK, interval="1d")
    try:
        if len(d): note_bar(d.index[-1].to_pydatetime())   # fix 2: daily bar session date (exchange tz)
    except Exception: pass
    return [dict(h=float(r.High), l=float(r.Low), c=float(r.Close)) for _, r in d.iterrows() if r.Close == r.Close]
_fetch = _fetch_yf
# 2026-10-29 TFO 11:12 WITA fix 2 (additive, no gate change): latest bar timestamp seen by this process, for hunt health bars_asof
BARS_ASOF = None
def note_bar(ts):
    global BARS_ASOF
    try:
        if ts is None or getattr(ts, "tzinfo", None) is None: return
        if BARS_ASOF is None or ts > BARS_ASOF: BARS_ASOF = ts
    except Exception: pass
def bars_asof_iso():
    return BARS_ASOF.isoformat() if BARS_ASOF is not None else None
def _cache_write(sym, out):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True); p = _cache_path(sym); tmp = p + ".tmp"
        with open(tmp, "w") as f: json.dump(dict(sym=sym, fetched_utc=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), bars=out), f)
        os.replace(tmp, p)
    except Exception: pass
def _cache_read(sym, now=None):
    try:
        o = json.load(open(_cache_path(sym))); age = ((now or _dt.datetime.now(_dt.timezone.utc)) - _dt.datetime.fromisoformat(o["fetched_utc"])).total_seconds() / 3600
        return o["bars"] if (0 <= age <= CACHE_MAX_AGE_H and o.get("bars")) else None
    except Exception: return None
def fnum(x):
    try:
        v = float(x); return v if v == v else None
    except Exception: return None
def daily(sym):
    """1y daily bars [{h,l,c}] (successes cached per process); retried RETRIES times; on total failure the disk cache
    (<= CACHE_MAX_AGE_H) is used; else [] -> callers BENCH (still fail closed). DATA_STATUS[sym] = ok | cache | fail."""
    if sym in _CACHE: return _CACHE[sym]
    if sym in _FAIL_AT and time.time() - _FAIL_AT[sym] < FAIL_MEMO_S: return []      # short memo only: no hammering inside one run
    out = []
    for i in range(RETRIES):
        try: out = _fetch(sym) or []
        except Exception: out = []
        if out: break
        if i < RETRIES - 1: _sleep(RETRY_SLEEP[min(i, len(RETRY_SLEEP) - 1)])
    if out:
        _CACHE[sym] = out; DATA_STATUS[sym] = "ok"; _FAIL_AT.pop(sym, None); _cache_write(sym, out); return out
    c = _cache_read(sym)
    if c:
        DATA_STATUS[sym] = "cache"; _CACHE[sym] = c; return c
    DATA_STATUS[sym] = "fail"; _FAIL_AT[sym] = time.time(); return []
daily_fetch = daily   # stable handle on the real implementation (tests may monkeypatch HR.daily)
def opposing_levels(bars, long=False):
    d = bars[:-1] if len(bars) > 5 else bars; lv = []
    for i in range(2, len(d) - 2):
        if not long and d[i]["l"] <= min(d[j]["l"] for j in (i-2, i-1, i+1, i+2)): lv.append(d[i]["l"])
        if long and d[i]["h"] >= max(d[j]["h"] for j in (i-2, i-1, i+1, i+2)): lv.append(d[i]["h"])
    for n in (20, 50):
        if len(d) >= n: lv.append(sum(x["c"] for x in d[-n:]) / n)
    return lv
US_MIN_BARS = 200; US_MAX_COST_R = 0.15   # audit H2+M4 (Brett 27 Oct 2026 19:35 WITA): US hunt48 uses this SAME room(); <200 daily bars or cost_R > 0.15 -> BENCH
def _ok_bar(b):
    try: return all(math.isfinite(float(b[k])) and float(b[k]) > 0 for k in ("h", "l", "c"))
    except Exception: return False
def room(t, bars, max_cost_R=None, min_bars=None):
    """Compute + WRITE t[room_R, room_basis, room_t2_R, room_struct_R, room_level]. Returns None if room_R >= MIN_ROOM_R else BENCH reason.
    audit H2+M4: optional max_cost_R (cost_R above it -> BENCH) and min_bars (fewer daily bars -> BENCH, fail closed). Defaults None = unchanged
    for ASX/Asia/LSE callers (they apply their own per-market cost cap; London 0.30)."""
    lg = t.get("direction") == "long"; e, st, t2, c = fnum(t.get("max_fill")), fnum(t.get("stop")), fnum(t.get("t2")), fnum(t.get("cost_R"))
    t.update(room_struct_R=None, room_level=None)
    R = abs(e - st) if (e is not None and st is not None) else 0
    if not R or t2 is None or c is None:
        t.update(room_R=0.0, room_t2_R=0.0, room_basis="UNCOMPUTABLE (missing max_fill/stop/t2/cost_R)")
        return "room uncomputable (missing max_fill/stop/t2/cost_R) -> BENCH (fail closed)"
    t2r = max(0.0, (t2 - e) / R if lg else (e - t2) / R); t["room_t2_R"] = round(t2r, 3)
    bars = [b for b in (bars or []) if _ok_bar(b)]   # audit retry-edge hardening: rows with missing/NaN prices never count as history
    if not bars:
        t.update(room_R=round(t2r - c, 3), room_basis="NO_DAILY_BARS -> BENCH")
        return "no daily bars: structure room unknown -> BENCH (fail closed)"
    if min_bars and len(bars) < min_bars:   # audit H2+M4: partial history cannot see 1y structure -> fail closed
        t.update(room_R=round(t2r - c, 3), room_basis=f"ONLY {len(bars)} DAILY BARS (<{min_bars}) -> BENCH")
        return f"only {len(bars)} daily bars (<{min_bars}): structure room unknown -> BENCH (fail closed)"
    lv = [x for x in opposing_levels(bars, lg) if (x > e if lg else x < e)]
    if lv:
        lvl = min(lv) if lg else max(lv); sr = abs(lvl - e) / R
        t.update(room_struct_R=round(sr, 3), room_level=round(lvl, 6))
        rm = min(sr, t2r) - c; basis = "structure" if sr < t2r else "T2"
        t.update(room_R=round(rm, 3), room_basis=f"min(structure {sr:.2f}R, T2 {t2r:.2f}R) - cost {c:.3f}R [{basis} binds]")
        what = f"{'resistance' if lg else 'support'} {lvl:g}" if sr < t2r else "T2"
    else:
        rm = t2r - c; t.update(room_R=round(rm, 3), room_basis=f"no structure in {LOOKBACK}: T2 {t2r:.2f}R - cost {c:.3f}R"); what = "T2 (no structure in 1y)"
    # 2026-10-29 Rules Auditor: +3%/-8% may SET the levels, but room is still measured in R from the stop.
    # Anything under MIN_ROOM_R (1.0) is BENCH before arming. The 09:32 hunt4 exemption that capped room at ~0.37R is removed (no RULE_LOG entry authorised it).
    if is_fixed(e, st, t2):
        t["room_rule"] = "fixed +3%/-8% levels; room still >= 1.0R (RULE_LOG 27 Oct)"
        if t["room_struct_R"] is not None and t["room_struct_R"] < t2r:
            return f"structure {t['room_struct_R']:.2f}R before +3% target {t2r:.2f}R -> BENCH"
    if rm < MIN_ROOM_R: return f"room {rm:.2f}R < {MIN_ROOM_R}R to {what} after costs"
    if max_cost_R is not None and c > max_cost_R: return f"cost {c:.3f}R > {max_cost_R}R cap -> BENCH"   # audit H2+M4
    return None
RS_LONG_MIN = 5.0   # 2026-10-29 Brett-approved hunt loosening: relative-strength longs allowed in a short regime
def rs_long(r, reg, min_score=3.0):
    """True if a long is allowed against a short regime: rs20 >= +5 pts vs index, px above own 50dMA, long score >= MIN_SCORE. Missing data -> False."""
    f = lambda k: (lambda v: float(v) if v not in (None, "", "nan") else None)(r.get(k))
    try: rs, px, m50, sc = f("rs20"), f("px"), f("m50"), f("score")
    except Exception: return False
    return reg == "short" and None not in (rs, px, m50, sc) and RS_LONG_MIN <= rs <= 30.0 and px > m50 and sc >= min_score   # +30 pump-chase guard, same band as asia_hunt48
def rsi_block(r):
    """B1 part 1 (no network): short RSI(14) < 30 or RSI missing -> blocked."""
    rsi = fnum(r.get("rsi"))
    if rsi is None: return ["RSI missing -> BENCH (no default)"]
    return [f"RSI {rsi:.0f} < {MIN_RSI_SHORT:.0f}: oversold short (B1)"] if rsi < MIN_RSI_SHORT else []
B1_DATAFAIL_LOG = os.path.join(os.path.dirname(CACHE_DIR), "hunt48_b1_datafail.jsonl")
def b1_from_bars(bars):
    """Brett 28 Oct 13:15 WITA hunt fix 2: roc5 (%), atr_pct (ATR14 % of close), px from 1y daily bars [{h,l,c}]; None if too short."""
    b = [x for x in (bars or []) if fnum(x.get("c")) and fnum(x.get("h")) and fnum(x.get("l"))]
    if len(b) < 20: return None
    c = [x["c"] for x in b]; tr = [max(b[i]["h"], c[i - 1]) - min(b[i]["l"], c[i - 1]) for i in range(len(b) - 14, len(b))]
    return dict(roc5=(c[-1] / c[-6] - 1) * 100, atr_pct=sum(tr) / len(tr) / c[-1] * 100, px=c[-1])
_B1_LOG_DEFAULT = B1_DATAFAIL_LOG
def log_b1_datafail(sym, why):
    if os.environ.get("PYTEST_CURRENT_TEST") and B1_DATAFAIL_LOG == _B1_LOG_DEFAULT: return   # never pollute the live log from tests
    try:
        os.makedirs(os.path.dirname(B1_DATAFAIL_LOG), exist_ok=True)
        with open(B1_DATAFAIL_LOG, "a") as f: f.write(json.dumps(dict(utc=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), sym=sym, why=why, data_status=DATA_STATUS.get(sym))) + "\n")
    except Exception: pass
def fill_b1_inputs(rows, bars_fn=None):
    """Fill missing roc5/atr_pct/px in ranked input rows from daily bars (Yahoo works for .AX/.T/.HK). Unfetchable -> left missing + logged."""
    n = 0
    for sym, r in (rows or {}).items():
        if fnum(r.get("roc5")) is not None and fnum(r.get("atr_pct")) and fnum(r.get("px")): continue
        x = b1_from_bars((bars_fn or daily)(sym))
        if not x: log_b1_datafail(sym, "ranked input roc5/atr_pct missing and daily bars unfetchable"); continue
        for k in ("roc5", "atr_pct", "px"):
            if fnum(r.get(k)) is None or (k != "roc5" and not fnum(r.get(k))): r[k] = x[k]
        r["b1_filled_from"] = "daily_bars"; n += 1
    return n
def exhaustion_block(r, bars):
    """B1 part 2: short at a TRUE 20d low (px within 1% of min(last 20 daily closes)) after a 5d drop > 1.5 ATR -> blocked.
    Fix 2 (28 Oct): missing roc5/atr_pct/px are computed from the daily bars; fail closed ONLY when bars truly can't be fetched (logged)."""
    roc5, atrp, px = fnum(r.get("roc5")), fnum(r.get("atr_pct")), fnum(r.get("px"))
    closes = [b["c"] for b in (bars or [])][-20:]
    if len(closes) >= 20 and (roc5 is None or not atrp or not px):
        x = b1_from_bars(bars) or {}
        roc5 = roc5 if roc5 is not None else x.get("roc5"); atrp = atrp or x.get("atr_pct"); px = px or x.get("px")
    if len(closes) < 20 or roc5 is None or not atrp or not px:
        log_b1_datafail(r.get("sym"), f"bars={len(closes)} roc5={roc5} atr_pct={atrp} px={px}")
        return ["20d-low/5d-extension data missing -> BENCH (B1 fail closed)"]
    lo20 = min(closes); ext5 = -roc5 / atrp
    return [f"at true 20d low {lo20:g} after {ext5:.1f} ATR 5d drop: exhausted low (B1)"] if (px / lo20 - 1 < LOW_PAD and ext5 > EXH_ATR) else []
def short_screen(r, sym=None, bars_fn=daily):
    """B1 first-screen block list for a SHORT scan row r. [] = ok. Bars fetched only if RSI passes."""
    why = rsi_block(r)
    return why or exhaustion_block(r, bars_fn(sym or r.get("sym")) if bars_fn else [])

def max_bench(mkt, default=40, base=None):
    """Brett 28 Oct 2026 13:15 WITA hunt fix 4: per-market bench size from state/hunt_bench.json (default 40, was 15). Bench only:
    arming still needs the funder tick (state/funder_queue.json) and MAX_OPEN / per-market risk caps are unchanged."""
    try:
        c = json.load(open(os.path.join(base or os.path.dirname(CACHE_DIR), "hunt_bench.json"))).get("max_bench") or {}
        return int(c.get(mkt) or c.get("default") or default)
    except Exception: return default
