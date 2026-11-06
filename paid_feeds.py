#!/usr/bin/env python3
"""paid_feeds.py — paid-first market data for the TFO fundamental book. PAPER ONLY. 2026-10-30 HT mandate.

Quotes:  EU/LSE (Cboe Europe)     -> EODHD websocket tape when healthy       else the rows below
         US (no suffix)            -> Massive 1m aggs (real-time)            fallback Yahoo chart
         .AX .L .T .HK             -> FMP /stable/historical-chart/1min      fallback Yahoo chart
         anything else             -> Yahoo chart (unchanged)
Each paid quote is converted into a Yahoo-chart-shaped payload and run through the UNCHANGED
tfo_equity_refresh.parse_chart, so session, executable, paper_executable, price class and age
semantics are byte-for-byte the same code path as before. Any paid failure (HTTP error, 401/403/429
breaker, no bars, missing currency) falls back to tfo_equity_refresh.fetch_quote (Yahoo).
Fundamentals: FMP /stable/ profile + ratios-ttm + key-metrics-ttm + balance-sheet-statement.
Earnings:     EODHD calendar/earnings.
Keys are read from ~/.tfo_secrets/*.env inside code only; never printed, logged or stored."""
import datetime as dt, json, os, threading, time, urllib.error, urllib.parse, urllib.request
from zoneinfo import ZoneInfo
UTC = dt.timezone.utc
SECRETS = os.path.expanduser("~/.tfo_secrets")
BREAKER_S = 1800
_BRK, _LOCK, _CCY = {}, threading.Lock(), {}
STATS = {"massive": 0, "fmp": 0, "eodhd": 0, "fallback_yahoo": 0, "errors": {}}

def _key(fn, var):
    v = os.environ.get(var)
    if v: return v.strip()
    try:
        for ln in open(os.path.join(SECRETS, fn)):
            if ln.strip().startswith(var + "="): return ln.split("=", 1)[1].strip().strip('"\'')
    except Exception: return None

# Token buckets (calls/min). FMP Ultimate allows 3,000/min; HT ruling 30 Oct: stay at ~1,500/min.
RATE_PER_MIN = {"fmp": float(os.environ.get("FMP_RATE_PER_MIN", "1500"))}
_TB = {}
def _take(provider):
    rate = RATE_PER_MIN.get(provider)
    if not rate: return
    per_s, burst = rate / 60.0, max(1.0, rate / 60.0)
    while True:
        with _LOCK:
            tok, last = _TB.get(provider, (burst, time.time())); now = time.time()
            tok = min(burst, tok + (now - last) * per_s)
            if tok >= 1.0:
                _TB[provider] = (tok - 1.0, now); return
            _TB[provider] = (tok, now); wait = (1.0 - tok) / per_s
        time.sleep(wait)

def _get(provider, url, headers=None, timeout=8):
    if os.environ.get("PAID_FEEDS_DISABLE") == "1": raise RuntimeError("paid feeds disabled")
    if _BRK.get(provider, 0) > time.time(): raise RuntimeError("%s breaker open" % provider)
    _take(provider)
    req = urllib.request.Request(url, headers=dict({"User-Agent": "TFO paper desk"}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as f:
            with _LOCK: STATS[provider] = STATS.get(provider, 0) + 1
            return json.loads(f.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 429): _BRK[provider] = time.time() + BREAKER_S
        raise RuntimeError("%s HTTP %d" % (provider, e.code)) from None   # never echo the URL (it may carry a key)
    except urllib.error.URLError as e:
        raise RuntimeError("%s network %s" % (provider, type(e.reason).__name__)) from None

def massive(path, **q):
    k = _key("massive.env", "MASSIVE_API_KEY")
    if not k: raise RuntimeError("massive key missing")
    return _get("massive", "https://api.massive.com%s?%s" % (path, urllib.parse.urlencode(q)), {"Authorization": "Bearer " + k})

def fmp(path, **q):
    k = _key("fmp.env", "FMP_API_KEY")
    if not k: raise RuntimeError("fmp key missing")
    q["apikey"] = k
    return _get("fmp", "https://financialmodelingprep.com/stable/%s?%s" % (path, urllib.parse.urlencode(q)))

def eodhd(path, **q):
    k = _key("eodhd.env", "EODHD_API_KEY")
    if not k: raise RuntimeError("eodhd key missing")
    q.update(api_token=k, fmt="json")
    return _get("eodhd", "https://eodhd.com/api/%s?%s" % (path, urllib.parse.urlencode(q)))

# ---------------------------------------------------------------- venues / sessions
# (tz, pre_start, reg_start, reg_end, post_end, exchangeName) — local wall-clock; pre/post zero-length where none.
VENUES = {"US": ("America/New_York", "04:00", "09:30", "16:00", "20:00", "Massive"),
          ".AX": ("Australia/Sydney", "10:00", "10:00", "16:00", "16:00", "ASX"),
          ".L": ("Europe/London", "08:00", "08:00", "16:30", "16:30", "LSE"),
          ".T": ("Asia/Tokyo", "09:00", "09:00", "15:30", "15:30", "JPX"),
          ".HK": ("Asia/Hong_Kong", "09:30", "09:30", "16:00", "16:00", "HKSE")}

def venue_of(sym):
    s = str(sym)
    if "." not in s and "=" not in s and not s.startswith("^"): return "US"
    for suf in (".AX", ".L", ".T", ".HK"):
        if s.endswith(suf): return suf
    return None

def _at(day, hhmm, tz):
    h, m = map(int, hhmm.split(":"))
    return dt.datetime(day.year, day.month, day.day, h, m, tzinfo=tz).timestamp()

def periods(venue, now_epoch):
    tzn, ps, rs, re_, pe, _ = VENUES[venue]; tz = ZoneInfo(tzn)
    day = dt.datetime.fromtimestamp(now_epoch, tz).date()
    if day.weekday() >= 5:   # weekend: zero-length periods -> parse_chart says 'closed'
        z = {"start": 0, "end": 0}; return {"pre": z, "regular": z, "post": z}
    return {"pre": {"start": _at(day, ps, tz), "end": _at(day, rs, tz)},
            "regular": {"start": _at(day, rs, tz), "end": _at(day, re_, tz)},
            "post": {"start": _at(day, re_, tz), "end": _at(day, pe, tz)}}

def _in_regular(venue, epoch):
    tzn, _ps, rs, re_, _pe, _ = VENUES[venue]; tz = ZoneInfo(tzn)
    d = dt.datetime.fromtimestamp(epoch, tz)
    return d.weekday() < 5 and _at(d.date(), rs, tz) <= epoch < _at(d.date(), re_, tz)

def _close_epoch(venue, bar_epoch):
    tzn, _ps, _rs, re_, _pe, _ = VENUES[venue]; tz = ZoneInfo(tzn)
    return _at(dt.datetime.fromtimestamp(bar_epoch, tz).date(), re_, tz)

def _official_close(sym, venue, last_reg_epoch):
    """Official close for the session of last_reg_epoch (includes the closing auction 1m bars miss). None if unknown."""
    tz = ZoneInfo(VENUES[venue][0]); d = dt.datetime.fromtimestamp(last_reg_epoch, tz).date()
    if venue == "US":
        js = massive("/v2/aggs/ticker/%s/range/1/day/%s/%s" % (urllib.parse.quote(sym.replace("-", ".")), d, d), adjusted="true")
        r = (js.get("results") or [])
        return float(r[-1]["c"]) if r and r[-1].get("c") else None
    q = (fmp("quote", symbol=sym) or [{}])[0]
    ts = q.get("timestamp")
    if ts and dt.datetime.fromtimestamp(float(ts), tz).date() == d and q.get("price"):
        return float(q["price"])
    return None

def _regular(sym, venue, reg, now_epoch):
    """(epoch, px): last regular bar while the session runs; the official close once that session has ended."""
    e, px = reg[-1]
    ce = _close_epoch(venue, e)
    if now_epoch >= ce:
        try:
            c = _official_close(sym, venue, e)
        except Exception:
            c = None
        if c: return ce, c
    return e, px

def _payload(ccy, venue, bars, regular_epoch, regular_px, now_epoch):
    return {"chart": {"result": [{"meta": {"currency": ccy, "regularMarketTime": regular_epoch, "regularMarketPrice": regular_px,
                                            "currentTradingPeriod": periods(venue, now_epoch), "exchangeName": VENUES[venue][5],
                                            "exchangeTimezoneName": VENUES[venue][0]},
                                   "timestamp": [b[0] for b in bars], "indicators": {"quote": [{"close": [b[1] for b in bars]}]}}]}}

def _massive_payload(sym, now_epoch):
    day = dt.datetime.fromtimestamp(now_epoch, ZoneInfo("America/New_York")).date()
    js = massive("/v2/aggs/ticker/%s/range/1/minute/%s/%s" % (urllib.parse.quote(sym.replace("-", ".")), day - dt.timedelta(days=5), day),
                 adjusted="true", sort="asc", limit=50000)
    bars = [(r["t"] / 1000.0, float(r["c"])) for r in (js.get("results") or []) if r.get("c")]
    if not bars: raise RuntimeError("massive: no bars")
    reg = [b for b in bars if _in_regular("US", b[0])]
    if not reg: raise RuntimeError("massive: no regular-session bars")
    today = [b for b in bars if dt.datetime.fromtimestamp(b[0], ZoneInfo("America/New_York")).date() == day]
    return _payload("USD", "US", today, *_regular(sym, "US", reg, now_epoch), now_epoch=now_epoch)

def _fmp_ccy(sym):
    if sym not in _CCY:
        p = (fmp("profile", symbol=sym) or [{}])[0]
        c = p.get("currency")
        if not c: raise RuntimeError("fmp: no currency")
        _CCY[sym] = c
    return _CCY[sym]

def _fmp_payload(sym, venue, now_epoch):
    tz = ZoneInfo(VENUES[venue][0]); ccy = _fmp_ccy(sym)
    js = fmp("historical-chart/1min", symbol=sym)
    bars = []
    for r in js or []:
        try: e = dt.datetime.strptime(r["date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz).timestamp(); bars.append((e, float(r["close"])))
        except Exception: continue
    bars.sort()
    if not bars: raise RuntimeError("fmp: no bars")
    reg = [b for b in bars if _in_regular(venue, b[0])]
    if not reg: raise RuntimeError("fmp: no regular-session bars")
    day = dt.datetime.fromtimestamp(now_epoch, tz).date()
    today = [b for b in bars if dt.datetime.fromtimestamp(b[0], tz).date() == day]
    return _payload(ccy, venue, today, *_regular(sym, venue, reg, now_epoch), now_epoch=now_epoch)

# fxfeed_20261103 (helper 2): OANDA PRACTICE pricing for OANDA-book FX pairs (=X). The fx_commodities seat reported "no paid quote hit
# any of my 10 symbols, all live px are Yahoo delayed fallback" (3 Nov 15:58 WITA): fetch_quote sent every =X/=F/^ straight to Yahoo.
# OANDA practice = OANDA's real-time prices on the PAPER account (token from ~/.tfo_secrets/oanda.env, read in code, never printed).
# Futures (=F) and indices (^) stay Yahoo: OANDA spot (XAU_USD, WTICO_USD ...) is not the future (basis) and is never substituted.
OANDA_HOST = "https://api-fxpractice.oanda.com"   # PRACTICE only - never api-fxtrade
STATS["oanda"] = 0
def _oanda_ins(sym):
    try:
        from veteran_desk.pit_fx_sgp import oanda_instrument
        return oanda_instrument(sym)
    except Exception: return None

def _fx_periods(now_epoch):
    """FX regular session = the London weekday (00:00-24:00 Europe/London, Yahoo's FX convention); weekend -> closed."""
    tz = ZoneInfo("Europe/London"); d = dt.datetime.fromtimestamp(now_epoch, tz).date(); z = {"start": 0, "end": 0}
    if d.weekday() >= 5: return {"pre": z, "regular": z, "post": z}
    n = d + dt.timedelta(days=1)
    return {"pre": z, "regular": {"start": _at(d, "00:00", tz), "end": _at(n, "00:00", tz)}, "post": z}

def _oanda_payload(sym, now_epoch):
    """(Yahoo-chart-shaped payload, extra fields) from OANDA practice pricing (bid/ask -> mid) + the last 30 M1 mid closes."""
    assert OANDA_HOST.startswith("https://api-fxpractice."), "practice host only"
    ins = _oanda_ins(sym)
    if not ins: raise RuntimeError("oanda: not an OANDA-book pair")
    tok = _key("oanda.env", "OANDA_PRACTICE_TOKEN"); acct = _key("oanda.env", "OANDA_ACCOUNT_ID")
    if not tok or not acct: raise RuntimeError("oanda: no practice credentials")
    H = {"Authorization": "Bearer " + tok, "Accept-Datetime-Format": "UNIX"}
    pj = _get("oanda", "%s/v3/accounts/%s/pricing?instruments=%s" % (OANDA_HOST, acct, ins), headers=H)
    p = (pj.get("prices") or [None])[0]
    if not p or not p.get("bids") or not p.get("asks"): raise RuntimeError("oanda: no price")
    bid, ask, t = float(p["bids"][0]["price"]), float(p["asks"][0]["price"]), float(p["time"]); mid = round((bid + ask) / 2.0, 7)
    if not (bid > 0 and ask >= bid): raise RuntimeError("oanda: bad bid/ask")
    cj = _get("oanda", "%s/v3/instruments/%s/candles?granularity=M1&count=30&price=M" % (OANDA_HOST, ins), headers=H)
    bars = sorted((float(x["time"]), float(x["mid"]["c"])) for x in (cj.get("candles") or []) if x.get("mid"))
    bars = [b for b in bars if b[0] < t] + [(t, mid)]   # the live quote is the newest observed print
    meta = {"currency": sym[3:6], "regularMarketTime": t, "regularMarketPrice": mid, "currentTradingPeriod": _fx_periods(now_epoch),
            "exchangeName": "OANDA practice", "exchangeTimezoneName": "Europe/London"}
    extra = dict(bid=bid, ask=ask, spread=round(ask - bid, 10), oanda_instrument=ins, tradeable=p.get("tradeable"))
    return {"chart": {"result": [{"meta": meta, "timestamp": [b[0] for b in bars], "indicators": {"quote": [{"close": [b[1] for b in bars]}]}}]}}, extra

# fxmark_20261103 (F18, helper of the master fixer, PAPER): the THF BOOK now marks, trigger-checks and fills OANDA-book FX pairs (=X) on
# the SAME OANDA practice mid the macro + fx_commodities seats see in their prompts (was Yahoo =X 5m bars: measured 2-3 Nov, Yahoo 5m lows
# sat 0.6-9 bp under the OANDA mid lows at the median and up to 115 bp in single bars = phantom touches the seats never saw).
# GET only (pricing + candles), practice host only, token/account read in code and never printed. book.live_px calls these; Yahoo stays
# the fallback there. Futures (=F) are NOT substituted (see OANDA_CFD below).
OANDA_BOOK_TTL_S = 20; OANDA_WINDOW_M1 = 30; _OBQ = {}
def _oanda_auth():
    assert OANDA_HOST.startswith("https://api-fxpractice."), "practice host only"
    tok = _key("oanda.env", "OANDA_PRACTICE_TOKEN"); acct = _key("oanda.env", "OANDA_ACCOUNT_ID")
    if not tok or not acct: raise RuntimeError("oanda: no practice credentials")
    return {"Authorization": "Bearer " + tok, "Accept-Datetime-Format": "UNIX"}, acct

def _iso_ep(ep): return dt.datetime.fromtimestamp(float(ep), UTC).isoformat()

def oanda_book_quote(sym, ttl=OANDA_BOOK_TTL_S):
    """fxmark_20261103: book-shaped live quote for an OANDA-book FX pair: last = live mid (bid+ask)/2, hi/lo/open over the last 30 M1 MID
    candles + the live mid (same 30-min window as the Yahoo 6x5m quote it replaces), asof = OANDA price time, window_start = first candle
    start, bid/ask/spread_bp kept, src 'oanda_practice_mid'. 20 s in-process cache (one tick asks the same pair many times). Raises."""
    ins = _oanda_ins(sym)
    if not ins: raise RuntimeError("oanda: not an OANDA-book pair")
    t0 = time.time(); hit = _OBQ.get(sym)
    if hit and t0 - hit[0] < ttl: return dict(hit[1])
    H, acct = _oanda_auth()
    pj = _get("oanda", "%s/v3/accounts/%s/pricing?instruments=%s" % (OANDA_HOST, acct, ins), headers=H)
    p = (pj.get("prices") or [None])[0]
    if not p or not p.get("bids") or not p.get("asks"): raise RuntimeError("oanda: no price")
    bid, ask, pt = float(p["bids"][0]["price"]), float(p["asks"][0]["price"]), float(p["time"])
    if not (bid > 0 and ask >= bid): raise RuntimeError("oanda: bad bid/ask")
    mid = round((bid + ask) / 2.0, 7)
    try: cj = _get("oanda", "%s/v3/instruments/%s/candles?granularity=M1&count=%d&price=M" % (OANDA_HOST, ins, OANDA_WINDOW_M1), headers=H)
    except Exception: cj = {}   # candles down, pricing up: still the OANDA mid (hi = lo = mid, window starts at the price time)
    bars = sorted((float(x["time"]), float(x["mid"]["o"]), float(x["mid"]["h"]), float(x["mid"]["l"])) for x in (cj.get("candles") or []) if x.get("mid"))
    bars = [b for b in bars if b[0] <= pt]
    q = dict(last=mid, hi=max([b[2] for b in bars] + [mid]), lo=min([b[3] for b in bars] + [mid]), open=bars[0][1] if bars else mid,
             asof=_iso_ep(pt), window_start=_iso_ep(bars[0][0] if bars else pt), bid=bid, ask=ask, spread_bp=round((ask - bid) / mid * 1e4, 2),
             tradeable=p.get("tradeable"), oanda_instrument=ins, src="oanda_practice_mid")
    _OBQ[sym] = (t0, q)
    return dict(q)

def oanda_m1_since(sym, since_epoch, until_epoch=None):
    """fxmark_20261103: [(start_epoch, hi, lo, close)] OANDA practice M1 MID candles starting in [since, until] (only the newest 5000 min).
    The book's grade-A secondary trigger check for OANDA-marked FX rows (same feed as the mark; was Yahoo 1m). Raises on failure."""
    ins = _oanda_ins(sym)
    if not ins: raise RuntimeError("oanda: not an OANDA-book pair")
    until = float(until_epoch or time.time()); since = max(float(since_epoch), until - 4999 * 60.0)
    H, _ = _oanda_auth()
    cj = _get("oanda", "%s/v3/instruments/%s/candles?granularity=M1&price=M&from=%d&count=5000" % (OANDA_HOST, ins, int(since)), headers=H, timeout=15)
    out = []
    for x in cj.get("candles") or []:
        m = x.get("mid"); ts = float(x.get("time") or 0)
        if m and since <= ts <= until: out.append((ts, float(m["h"]), float(m["l"]), float(m["c"])))
    return out

# fxmark_20261103 (master 22:5x; Brett 20:08 "OANDA PRACTICE TOKEN IS THE ONE FOR ALL TRADES"; fx_commodities 20:17 asked for =F -> OANDA):
# futures stay on the FUTURE. The book marks, triggers and fills =F on the Yahoo future (delayed ~10 min); an OANDA CFD is a different
# instrument (no expiry/roll, spot-vs-future basis: 3 Nov 22:59 WITA BZ=F 103.50 vs BCO_USD 106.86 = -3.1%, NG=F +8.0% vs NATGAS_USD,
# CL=F -1.1%, GC=F +0.3%) and is NEVER substituted. In THF seat prompts the Yahoo future quote carries the OANDA CFD mid NEXT TO it, labelled.
# Ags CFDs are quoted in USD while the Yahoo futures quote US cents (ZC/ZW/ZS/SB) - the note shows the x100 figure.
OANDA_CFD = {"GC=F": "XAU_USD", "MGC=F": "XAU_USD", "SI=F": "XAG_USD", "PL=F": "XPT_USD", "PA=F": "XPD_USD", "HG=F": "XCU_USD",
             "CL=F": "WTICO_USD", "MCL=F": "WTICO_USD", "BZ=F": "BCO_USD", "NG=F": "NATGAS_USD",
             "ZC=F": "CORN_USD", "ZW=F": "WHEAT_USD", "ZS=F": "SOYBN_USD", "SB=F": "SUGAR_USD",
             "ES=F": "SPX500_USD", "NQ=F": "NAS100_USD", "YM=F": "US30_USD", "RTY=F": "US2000_USD"}
CFD_CENTS = {"ZC=F", "ZW=F", "ZS=F", "SB=F"}
CFD_TTL_S = 60; _CFDQ = {}

def oanda_cfd_quote(fut):
    """(instrument, mid, price_epoch) of the OANDA practice CFD on the same underlying as a Yahoo future, or None. GET only, 60 s cache."""
    ins = OANDA_CFD.get(str(fut or "").upper())
    if not ins: return None
    t0 = time.time(); hit = _CFDQ.get(ins)
    if hit and t0 - hit[0] < CFD_TTL_S: return hit[1]
    H, acct = _oanda_auth()
    pj = _get("oanda", "%s/v3/accounts/%s/pricing?instruments=%s" % (OANDA_HOST, acct, ins), headers=H)
    p = (pj.get("prices") or [None])[0]
    if not p or not p.get("bids") or not p.get("asks"): return None
    out = (ins, round((float(p["bids"][0]["price"]) + float(p["asks"][0]["price"])) / 2.0, 6), float(p["time"]))
    _CFDQ[ins] = (t0, out)
    return out

def cfd_note(fut, fut_px=None):
    """One short label: the OANDA CFD mid beside a Yahoo future + 'the book marks the future'. '' when no CFD / OANDA down."""
    try: c = oanda_cfd_quote(fut)
    except Exception: c = None
    if not c: return ""
    ins, mid, ep = c; f = str(fut).upper(); shown = mid * 100.0 if f in CFD_CENTS else mid
    basis = (", %+.2f%% vs this future" % ((shown / float(fut_px) - 1) * 100)) if fut_px else ""
    return "OANDA CFD %s %s%s @%sZ%s (CFD, NOT this future: book marks/fills %s on the future)" % (   # short: the pack's live-quote budget is 2.2KB
        ins, "%.6g" % mid, (" (=%.6g US cents)" % shown) if f in CFD_CENTS else "", _iso_ep(ep)[11:16], basis, f)

def fetch_quote(symbol):
    """Drop-in for tfo_equity_refresh.fetch_quote: paid first, Yahoo fallback. Same dict schema + source fields."""
    import tfo_equity_refresh as ter
    try:  # tfo_paidrt_20261031: Cboe Europe websocket before FMP/Yahoo when the consumer is healthy
        from veteran_desk import eodhd_eu_ws as _EU
        q = _EU.chart_quote(symbol)
        if q: return q
    except Exception: pass
    if _oanda_ins(symbol):   # fxfeed_20261103: OANDA-book FX -> OANDA practice real-time mid; Yahoo below stays the fallback
        try:
            fetched_at = ter.utc_now(); now = dt.datetime.fromisoformat(fetched_at.replace("Z", "+00:00")).timestamp()
            pl, extra = _oanda_payload(symbol, now)
            q = ter.parse_chart(symbol, pl, fetched_at)
            q.update(extra); q.update(source="OANDA practice pricing (real-time mid)", provider="oanda_practice", url=None, paid_feed=True)
            return q
        except Exception as e:
            with _LOCK:
                STATS["errors"][symbol] = str(e)[:120]; STATS["fallback_yahoo"] += 1
    v = venue_of(symbol)
    if v is not None:
        try:
            fetched_at = ter.utc_now(); now = dt.datetime.fromisoformat(fetched_at.replace("Z", "+00:00")).timestamp()
            pl = _massive_payload(symbol, now) if v == "US" else _fmp_payload(symbol, v, now)
            q = ter.parse_chart(symbol, pl, fetched_at)
            q.update(source="Massive 1m aggs (real-time)" if v == "US" else "FMP /stable/ 1min chart", url=None, paid_feed=True)
            return q
        except Exception as e:
            with _LOCK:
                STATS["errors"][symbol] = str(e)[:120]; STATS["fallback_yahoo"] += 1
    q = ter.fetch_quote(symbol); q["paid_feed"] = False
    if str(symbol).upper() in OANDA_CFD and os.environ.get("THF_DESK") == "1":   # fxmark_20261103: OANDA CFD shown BESIDE the Yahoo future in THF seat prompts (src text); never replaces it
        _n = cfd_note(symbol, q.get("px"))
        if _n: q["oanda_cfd_note"] = _n; q["source"] = "%s | %s" % (q.get("source") or "Yahoo public chart", _n)
    return q

# ---------------------------------------------------------------- fundamentals (FMP)
def _n(v):
    try:
        f = float(v); return f if f == f and abs(f) != float("inf") else None
    except (TypeError, ValueError): return None

def fmp_fundamentals(sym):
    """Raw fields in value_hunt's Yahoo units (debtToEquity in %, ROE as fraction). Raises on any gap."""
    p = (fmp("profile", symbol=sym) or [{}])[0]
    rt = (fmp("ratios-ttm", symbol=sym) or [{}])[0]
    km = (fmp("key-metrics-ttm", symbol=sym) or [{}])[0]
    bs = (fmp("balance-sheet-statement", symbol=sym, limit=1) or [{}])[0]
    if not p.get("marketCap"): raise RuntimeError("fmp: no market cap")
    de = _n(rt.get("debtToEquityRatioTTM"))
    return {"name": (p.get("companyName") or "")[:44], "ccy": p.get("currency"), "mcap": _n(p.get("marketCap")),
            "pb_provider": _n(rt.get("priceToBookRatioTTM")), "pe": _n(rt.get("priceToEarningsRatioTTM")), "fpe": None,
            "ev_ebitda": _n(rt.get("enterpriseValueMultipleTTM") or km.get("evToEBITDATTM")),
            "ps": _n(rt.get("priceToSalesRatioTTM")), "roe": _n(km.get("returnOnEquityTTM")),
            "de": None if de is None else de * 100.0, "dy": None if _n(rt.get("dividendYieldTTM")) is None else _n(rt.get("dividendYieldTTM")) * 100.0,  # Yahoo reports %
            "sector": p.get("sector"), "industry": p.get("industry"),
            "equity": _n(bs.get("totalStockholdersEquity")), "bs_asof": bs.get("date"), "fin_ccy": (bs.get("reportedCurrency") or "").upper(),
            "fund_source": "FMP /stable/ profile+ratios-ttm+key-metrics-ttm+balance-sheet-statement"}

# ---------------------------------------------------------------- earnings (EODHD)
EOD_SUFFIX = {"US": ".US", ".AX": ".AU", ".L": ".LSE", ".HK": ".HK", ".T": ".TSE"}
def eodhd_symbol(sym):
    v = venue_of(sym)
    if v is None: return None
    base = sym if v == "US" else sym[: -len(v)]
    return base.replace(".", "-") + EOD_SUFFIX[v] if v == "US" else base + EOD_SUFFIX[v]

def eodhd_next_earnings(symbols, today=None):
    """{sym: {date, timing, source, fetched_utc}} for the NEXT report_date >= today. Only what EODHD returns."""
    today = today or dt.date.today().isoformat()
    m = {eodhd_symbol(s): s for s in symbols if eodhd_symbol(s)}
    out = {}
    if not m: return out
    js = eodhd("calendar/earnings", symbols=",".join(m), **{"from": today, "to": (dt.date.fromisoformat(today) + dt.timedelta(days=200)).isoformat()})
    fetched = dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    for r in sorted(js.get("earnings") or [], key=lambda r: r.get("report_date") or ""):
        s = m.get(r.get("code")); d = r.get("report_date")
        if s and d and d >= today and s not in out:
            out[s] = {"date": d, "timing": r.get("before_after_market"), "fiscal_period_end": r.get("date"),
                      "source": "EODHD calendar/earnings (%s)" % r.get("code"), "fetched_utc": fetched}
    return out
