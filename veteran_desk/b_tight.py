"""tfo_btight_20261101 (Brett HARD 2026-11-01 15:15 WITA, PAPER ONLY, all VD/THF seats incl Kai):
Grade-B setups must be REALLY tight and fire only on confirmation.
  (a) entry <= 1.5% from the reference price (A/A+: 3% general arm guard in book.py)
  (b) stop no wider than B_MAX_STOP_ATR x ATR14 (conservative pick 1.5 ATR; A band is 0.5-6 ATR)
  (c) confirmation: breakout-side trigger only (LONG entry >= price, SHORT entry <= price; no resting dip-buy/fade B),
      AND a volume pickup (last daily bar volume >= 1.5x 20-bar median) and/or a fresh news/catalyst field on the proposal
  (d) B queue entries are re-checked every arm tick (distance/side) and the seat must re-confirm each round (prompt text).
Stops, 2-5% firm rails, kill switch untouched. Half size for B stays in desk.py/book.py (histsize 2026-10-31)."""
B_MAX_ENTRY_DIST = 0.015
B_MAX_STOP_ATR = 1.5
B_MIN_RVOL = 1.5
TAG = "tfo_btight_20261101"
import re as _re
_NO_CAT = _re.compile(r"^\s*(?:(?:no|none|null|nil|not|nothing|without|unknown|tbd|na)\b|n/a\b|-+\s*$)", _re.I)   # nocat_20261103 (fixer): "No fresh headline ..." is NOT a catalyst (GOOGL VET-GOOGL-20261103-0938 B waived the 1.5x volume check on it)
def _f(x):
    try: return float(x)
    except Exception: return None
# fillfix_20261103 (F5c): a catalyst must be DATED and fresh. The desk's own catalyst sources (catalyst_stream, news.json, the event/earnings
# calendars) all stamp a date, and seats quote them that way ("(CNBC, 01:41 UTC 3 Nov)", "EUR CPI flash Wed 04 Nov"). Any free string used to
# waive the B 1.5x volume check. Counted: a calendar date in the text (2026-11-03 / 3 Nov / Nov 3) from CAT_MAX_AGE_D days back to
# CAT_MAX_AHEAD_D days ahead, a catalyst_date/readout_date field in that window, or a news-feed hit on the order (tags.news_risk).
CAT_MAX_AGE_D = 3; CAT_MAX_AHEAD_D = 14
_MONTHS = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_ISO_D = _re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")
_DM_D = _re.compile(r"(?<![:\d])\b(\d{1,2})(?:st|nd|rd|th)?\s*" + _MONTHS + r"\b\.?(?:,?\s+(20\d\d)\b)?", _re.I)
_MD_D = _re.compile(r"\b" + _MONTHS + r"\b\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?![:.]\d)(?:,?\s+(20\d\d)\b)?", _re.I)   # (?![:.]\d): "02 Nov 22:44Z" is not Nov 22
_MON_N = dict(jan=1, feb=2, mar=3, apr=4, may=5, jun=6, jul=7, aug=8, sep=9, oct=10, nov=11, dec=12)
def catalyst_dates(text, now=None):
    """fillfix_20261103: calendar dates written in a catalyst/headline text; a missing year is the one nearest to now."""
    import datetime as _dt
    s = str(text or ""); today = (now or _dt.datetime.now(_dt.timezone.utc)).date(); out = []
    def add(y, mo, d):
        try:
            if y: out.append(_dt.date(int(y), int(mo), int(d))); return
            out.append(min((_dt.date(today.year + k, int(mo), int(d)) for k in (-1, 0, 1)), key=lambda x: abs((x - today).days)))
        except ValueError: pass
    for m in _ISO_D.finditer(s): add(m.group(1), m.group(2), m.group(3))
    for m in _DM_D.finditer(s): add(m.group(3), _MON_N[m.group(2).lower()[:3]], m.group(1))
    for m in _MD_D.finditer(s): add(m.group(3), _MON_N[m.group(1).lower()[:3]], m.group(2))
    return out
def dated_fresh(text, now=None):
    import datetime as _dt
    today = (now or _dt.datetime.now(_dt.timezone.utc)).date()
    return any(-CAT_MAX_AGE_D <= (d - today).days <= CAT_MAX_AHEAD_D for d in catalyst_dates(text, now))
def has_catalyst(p, now=None):
    p = p or {}; t = p.get("tags") or {}
    from veteran_desk.filing_freshness import sec_claim_fresh
    verified=sec_claim_fresh(p,now)
    if verified is False:return False
    for v in (p.get("catalyst_date"), p.get("readout_date"), t.get("catalyst_date")):   # fillfix_20261103: structured dated catalyst
        if v and dated_fresh(str(v)[:10], now): return True
    for v in (p.get("catalyst"), p.get("news"), p.get("news_hit"), p.get("catalyst_ref"), t.get("catalyst")):
        if isinstance(v, str) and v.strip() and v.strip().lower() not in ("none", "null", "no", "false", "-", "n/a") and not _NO_CAT.match(v) and dated_fresh(v, now): return True   # fillfix_20261103: undated text no longer counts
    return bool(t.get("news_risk"))
def rvol_from_rows(rows):
    try:
        v = [float(r[5]) for r in rows[-21:] if r[5] is not None]
        if len(v) < 11 or v[-1] <= 0: return None
        b = sorted(v[:-1]); med = b[len(b) // 2]
        return v[-1] / med if med > 0 else None
    except Exception: return None
def check(p, ref, atr, rvol):
    """Return list of hard reasons ([] = B passes). p: proposal/queue entry with entry/stop/side/grade."""
    if (p or {}).get("grade") != "B": return []
    why = []; e = _f(p.get("entry")); s = _f(p.get("stop")); side = p.get("side"); ref = _f(ref); atr = _f(atr)
    if not (e and s and ref): return ["B tight: entry/stop/price missing - fail closed"]
    d = abs(e / ref - 1)
    if d > B_MAX_ENTRY_DIST + 1e-12: why.append("B tight: entry %g is %.2f%% from price %g (> %.1f%% for B)" % (e, d * 100, ref, B_MAX_ENTRY_DIST * 100))
    if atr and atr > 0:
        k = abs(e - s) / atr
        if k > B_MAX_STOP_ATR + 1e-9: why.append("B tight: stop %.2f ATR wider than %.1f ATR B cap" % (k, B_MAX_STOP_ATR))
    else: why.append("B tight: ATR unknown - stop width unverified (fail closed)")
    if (side == "LONG" and e < ref * (1 - 1e-9)) or (side == "SHORT" and e > ref * (1 + 1e-9)):
        why.append("B confirm: trigger must be on the breakout side of price (no resting dip-buy/fade B)")
    vol_ok = rvol is not None and rvol >= B_MIN_RVOL
    if not vol_ok and not has_catalyst(p):
        why.append("B confirm: needs volume pickup (rvol %s < %.1fx) or a fresh news/catalyst" % (("%.2f" % rvol) if rvol is not None else "n/a", B_MIN_RVOL))
    return why
def apply_gate(p, g, f=None, asof=None):
    """desk.risk_gate hook: reference = live print else last close; ATR from gate history; rvol from PIT daily bars."""
    if (p or {}).get("grade") != "B": return g
    h = g.get("history") or {}; lq = g.get("live_quote") or {}
    ref = lq.get("last") or g.get("quote_last")
    if not ref:   # kai3: live mark before yesterday's close (a stale close mis-measured LINK 1.6% vs 0.3% live)
        try:
            from veteran_desk import book as _BK; ref = (_BK.live_px(p["ticker"]) or {}).get("last")
        except Exception: ref = None
    ref = ref or h.get("close") or (f or {}).get("close")
    atr = h.get("atr14") or (f or {}).get("atr14")
    rv = (f or {}).get("rvol")
    if rv is None:
        try:
            from veteran_desk import gate_v2 as G2
            rows, _ = G2.history(p["ticker"], asof); rv = rvol_from_rows(rows)
        except Exception: rv = None
    why = check(p, ref, atr, rv)
    g.setdefault("warnings", []).append("B tight: rvol %s (%s)" % (("%.2f" % rv) if rv is not None else "n/a", TAG))
    if why: g["passed"] = False; g.setdefault("reasons", []).extend(why)
    return g
def arm_check(e, px):
    """book.py arm hook for B: distance <=1.5% from the live mark and breakout side. Volume/news were proven at the gate."""
    if (e or {}).get("grade") != "B" or not px: return None
    ent = _f(e.get("entry")); px = _f(px)
    if not ent or not px: return None
    d = abs(ent / px - 1)
    if d > B_MAX_ENTRY_DIST + 1e-12: return "b_tight_too_far: entry %g is %.2f%% from mark %g (> %.1f%%)" % (ent, d * 100, px, B_MAX_ENTRY_DIST * 100)
    if (e.get("side") == "LONG" and ent < px * (1 - 1e-9)) or (e.get("side") == "SHORT" and ent > px * (1 + 1e-9)): return "b_tight_wrong_side: B must trigger on movement through the level, not rest as a dip/fade order"
    return None
# ---- fill-time confirmation (Brett HARD 1 Nov 15:15 (c) + 15:54 TEST trade): B / confirm_rvol orders fill only on volume (or catalyst for plain B) ----
FILL_BAR = "intraday"; _VCACHE = {}
TEST_VOLS = [100.0] * 21 + [200.0]
def _intraday_vols(ticker):
    """Recent bar volumes (crypto 15m Kraken, else 5m EODHD), oldest first. Crypto: Kraken public OHLC; else Yahoo chart v8. None on any failure (caller fails closed)."""
    import json as _j, time as _t, urllib.request as _u, urllib.parse as _up
    import os as _os
    if _os.environ.get("PYTEST_CURRENT_TEST"): return TEST_VOLS   # tests never hit the network; default = confirmed volume, tests override
    c = _VCACHE.get(ticker)
    if c and _t.time() - c[0] < 60: return c[1]
    vols = None; _nt = None
    try:
        if str(ticker).upper().startswith("KRAKEN:"):
            from veteran_desk import book as _BK
            pair = str(ticker).split(":", 1)[1]
            try:
                _kp = _BK.kraken_pair(ticker); pair = _kp[0] if isinstance(_kp, (tuple, list)) else (_kp or pair)
            except Exception: pass
            J = _j.loads(_u.urlopen(_u.Request("https://api.kraken.com/0/public/OHLC?pair=%s&interval=15" % _up.quote(str(pair)), headers={"User-Agent": "Mozilla/5.0"}), timeout=10).read())
            R = [v for k, v in (J.get("result") or {}).items() if k != "last"]
            vols = [float(r[6]) for r in R[0]] if R else None
            _nt = float(R[0][-1][0]) if R and R[0] else None
        else:   # equities/futures: paid-first data layer (EODHD 5m intraday), never a direct Yahoo call
            from veteran_desk import data_layer as _DL
            import datetime as _dt
            rows = _DL.intraday(ticker, "5m", _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=3))
            vols = [float(r[5]) for r in rows] if rows else None
            _nt = float(rows[-1][0]) if rows else None
    except Exception: vols = None
    _why = _stale_vols(_nt) if vols else None   # fillfix_20261103 (F5c): pre-open rvol was computed from yesterday's closing bars (GOOGL 7.8x)
    if _why: vols = None; _VINFO[ticker] = _why
    else: _VINFO.pop(ticker, None)
    _VCACHE[ticker] = (_t.time(), vols); return vols
VOL_MAX_AGE_MIN = 40; _VINFO = {}   # fillfix_20261103 (F5c): newest volume bar (bar start) older than this -> volume unknown -> B held (fail closed); fits a 20-min delayed venue + one 15m bar
def _stale_vols(newest_s, now_s=None):
    """fillfix_20261103 (F5c): reason str when the newest intraday volume bar is older than VOL_MAX_AGE_MIN, else None (None/unknown time -> None)."""
    import time as _t
    if newest_s is None: return None
    age = ((now_s if now_s is not None else _t.time()) - float(newest_s)) / 60.0
    return ("newest volume bar %.0f min old > %d" % (age, VOL_MAX_AGE_MIN)) if age > VOL_MAX_AGE_MIN else None
def intraday_rvol(vols, n=20):
    """max(current, last completed) 15m bar volume / median of the n bars before them."""
    import statistics as _s
    if not vols or len(vols) < n + 2: return None
    base = [v for v in vols[-(n + 2):-2] if v > 0]
    if len(base) < n // 2: return None
    med = _s.median(base)
    return (max(vols[-1], vols[-2]) / med) if med > 0 else None
def fill_confirm(p, vols_fn=None):
    """book.py fill hook. None = may fill. Applies to grade B and any order carrying confirm_rvol (e.g. TEST orders).
    confirm_rvol set -> volume REQUIRED (>= confirm_rvol). Plain B -> volume >= 1.5x OR a catalyst on the order. Unknown volume -> hold (fail closed)."""
    p = p or {}
    req = p.get("confirm_rvol")
    if p.get("grade") != "B" and not req: return None
    thr = float(req or B_MIN_RVOL)
    rv = intraday_rvol((vols_fn or _intraday_vols)(p.get("ticker")))
    p["fill_rvol"] = None if rv is None else round(rv, 2)
    if rv is not None and rv >= thr: return None
    if not req and has_catalyst(p): return None
    _vi = (" (%s)" % _VINFO[p.get("ticker")]) if rv is None and vols_fn is None and _VINFO.get(p.get("ticker")) else ""   # fillfix_20261103: say why volume is unknown
    return "b_confirm_wait: %s rvol %s%s < %.1fx%s - order held, not filled" % (FILL_BAR, ("%.2f" % rv) if rv is not None else "n/a", _vi, thr, "" if req else " and no dated catalyst")
# ---- TEST orders (head trader 1 Nov 16:38 WITA, under Brett's one-test-trade ask; PAPER) ----
# TEST-tagged THF crypto orders may skip ONLY the 0.5% crypto floor. Max 1 TEST per UTC day, risk <= A$700, limit/stop entry.
# Every other rail still applies (3% arm, B 1.5% entry, 1.5 ATR stop, 1.5x volume fill confirm). Only seat_cli (operator) can tag TEST.
TEST_MAX_RISK_AUD = 700.0; TEST_MAX_PER_DAY = 1
def is_test(e):
    e = e or {}
    v = e.get("test") if e.get("test") is not None else (e.get("tags") or {}).get("test")
    return v is True or str(v or "").upper() in ("TEST", "TRUE")
def _today_utc():
    import datetime as _dt; return _dt.datetime.now(_dt.timezone.utc).date().isoformat()
def tests_used_today(queue_path=None, exclude_id=None, day=None):
    """TEST entries queued today (UTC) in the funder queue, any state except gate-rejected (never enqueued)."""
    import json as _j
    day = day or _today_utc()
    try:
        if queue_path is None:
            import funder_gate as _FG; queue_path = _FG.QUEUE
        Q = _j.load(open(queue_path)).get("entries", {})
    except Exception: return 0
    return sum(1 for k, e in Q.items() if k != exclude_id and is_test(e) and str(e.get("queued_utc") or "")[:10] == day)
def apply_test(p, g, venue=None, queue_path=None):
    """Gate hook for TEST proposals: drop only below_crypto_floor; enforce crypto-only, 1/day, risk <= A$700."""
    if not is_test(p): return g
    rs = list(g.get("reasons") or [])
    keep = [r for r in rs if not str(r).startswith("below_crypto_floor")]
    v = venue or ("CRYPTO" if str(p.get("ticker", "")).upper().startswith(("KRAKEN:", "PF_")) else None)
    if v != "CRYPTO": keep.append("test_crypto_only: TEST floor exemption is for THF crypto orders only")
    if tests_used_today(queue_path) >= TEST_MAX_PER_DAY: keep.append("test_daily_cap: %d TEST already queued today (UTC)" % TEST_MAX_PER_DAY)
    if p.get("order_type") not in (None, "limit", "stop_limit"): keep.append("test_order_type: TEST must be a limit or stop entry")
    g["reasons"] = keep; g["passed"] = not keep
    g["risk_aud"] = round(min(float(g.get("risk_aud") or TEST_MAX_RISK_AUD), TEST_MAX_RISK_AUD), 2)
    g.setdefault("warnings", []).append("TEST order: crypto 0.5%% floor waived, risk capped A$%.0f, 1/day (head trader 1 Nov 16:38 WITA)" % TEST_MAX_RISK_AUD)
    return g
