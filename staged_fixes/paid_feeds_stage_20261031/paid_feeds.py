#!/usr/bin/env python3
"""paid_feeds.py — paid-first market data for the TFO fundamental book. PAPER ONLY. 2026-10-30 HT mandate.

Quotes:  US (no suffix)            -> Massive 1m aggs (real-time)            fallback Yahoo chart
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

def fetch_quote(symbol):
    """Drop-in for tfo_equity_refresh.fetch_quote: paid first, Yahoo fallback. Same dict schema + source fields."""
    import tfo_equity_refresh as ter
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
