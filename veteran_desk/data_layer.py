"""Shared market-data layer. tfo_datalayer_20261030 (Brett HARD RULE 20:59 WITA: paid feeds first, Yahoo/yfinance fallback only). PAPER.
Order:  US real-time quote -> Massive (veteran_desk/massive.py) -> EODHD real-time -> caller's Yahoo fallback.
        Daily/intraday bars (US, ASX, LSE, TSX, HK, EU, JP, KR, IN, ...) -> EODHD -> Yahoo (pit.yahoo_rows) fallback.
        Fundamentals / earnings / news -> EODHD (+ Massive/Benzinga news) -> Yahoo fallback in the caller.
NOT routed here: crypto (Kraken), OANDA FX (OANDA), =F futures (no EODHD coverage -> Yahoo/IBKR as before).
Keys: $EODHD_API_KEY or ~/.tfo_secrets/eodhd.env; Massive via massive.py. Keys are NEVER printed/logged; api_token is redacted
from every error string. Circuit breaker: 401/403/429 trips that endpoint family for BREAKER_MIN; network errors count toward a
soft breaker (5 in a row). Disabled under pytest unless a getter is injected. Every result carries src so callers can show provenance.
This is the ONLY module (with yahoo fallback helpers in veteran_desk/pit.py + feeds.py) allowed to touch Yahoo for new code:
see veteran_desk/tests/test_no_direct_yf_20261030.py."""
import datetime as dt, json, os, re, time, threading, urllib.error, urllib.parse, urllib.request
UTC = dt.timezone.utc; HOST = "https://eodhd.com/api"; SECRET = os.path.expanduser("~/.tfo_secrets/eodhd.env")
BREAKER_MIN = float(os.environ.get("DATALAYER_BREAKER_MIN", "30")); _BRK = {}; _ERRN = {}; _L = threading.Lock()
STATS = {"eodhd_ok": 0, "eodhd_fail": 0, "massive_ok": 0, "yahoo_fallback": 0, "breaker_trips": 0}
# Yahoo suffix -> EODHD exchange code
EX = {"AX": "AU", "L": "LSE", "IL": "IL", "TO": "TO", "V": "V", "NE": "NEO", "HK": "HK", "T": "TSE", "KS": "KO", "KQ": "KQ", "TW": "TW",
      "TWO": "TWO", "NS": "NSE", "BO": "BSE", "NZ": "NZ", "SW": "SW", "ST": "ST", "CO": "CO", "HE": "HE", "OL": "OL", "MC": "MC",
      "MI": "MI", "PA": "PA", "AS": "AS", "BR": "BR", "LS": "LS", "VI": "VI", "IR": "IR", "DE": "XETRA", "F": "F", "SS": "SHG",
      "SZ": "SHE", "SI": "SG", "JK": None, "SA": "SA", "MX": "MX", "JO": "JSE", "WA": "WAR"}
IDX = {"^GSPC": "GSPC.INDX", "^DJI": "DJI.INDX", "^IXIC": "IXIC.INDX", "^NDX": "NDX.INDX", "^RUT": "RUT.INDX", "^VIX": "VIX.INDX",
       "^AXJO": "AXJO.INDX", "^FTSE": "FTSE.INDX", "^GDAXI": "GDAXI.INDX", "^FCHI": "FCHI.INDX", "^N225": "N225.INDX", "^HSI": "HSI.INDX",
       "^GSPTSE": "GSPTSE.INDX", "^STOXX50E": "STOXX50E.INDX", "^KS11": "KS11.INDX", "^TNX": "TNX.INDX"}
def _key():
    k = os.environ.get("EODHD_API_KEY") or os.environ.get("TFO_EODHD_KEY")
    if k: return k.strip()
    try:
        for ln in open(SECRET):
            m = re.match(r"\s*(?:export\s+)?(?:EODHD_API_KEY|TFO_EODHD_KEY)\s*=\s*[\"']?([^\"'\s]+)", ln)
            if m: return m.group(1)
    except Exception: return None
def redact(s):
    s = re.sub(r"(api_token=)[^&\s]+", r"\1***", str(s)); k = _key()
    return s.replace(k, "***") if k else s
def eodhd_symbol(sym):
    """Yahoo-style symbol -> EODHD code, or None if EODHD does not carry it (crypto, futures, banned)."""
    s = str(sym).strip().upper()
    if not s or s.startswith("KRAKEN:") or s.endswith("=F") or s.endswith(".JK"): return None
    if s in IDX: return IDX[s]
    if s.startswith("^"): return s[1:] + ".INDX"
    if s.endswith("=X"): return s[:-2] + ".FOREX"
    if re.search(r"-(USD|USDT|EUR|GBP|AUD)$", s): return None   # crypto stays on Kraken
    if "." in s:
        base, suf = s.rsplit(".", 1)
        if suf in EX:
            return (base + "." + EX[suf]) if EX[suf] else None
        if suf == "US": return s
        return s.replace(".", "-") + ".US" if len(suf) == 1 and suf in "ABC" else s   # BRK.B -> BRK-B.US
    return s.replace("/", "-") + ".US"
def breaker_open(fam, now=None): return _BRK.get(fam, 0) > (now or time.time())
def _trip(fam, why, now=None):
    with _L: _BRK[fam] = (now or time.time()) + BREAKER_MIN * 60; STATS["breaker_trips"] += 1; STATS.setdefault("last_trip", {})[fam] = why
def _http(path, q, t=20):
    key = _key()
    if not key: return 0, None
    u = HOST + path + "?" + urllib.parse.urlencode(dict(q, api_token=key, fmt="json"))
    r = urllib.request.Request(u, headers={"User-Agent": "TFO paper desk"})
    try:
        with urllib.request.urlopen(r, timeout=t) as f: return f.status, json.loads(f.read())
    except urllib.error.HTTPError as e: return e.code, None
def call(fam, path, q=None, get=None, now=None):
    """EODHD GET with breaker. Returns parsed JSON or None. 401/403/429 -> breaker for BREAKER_MIN; 5 consecutive errors -> breaker."""
    now = now or time.time()
    if breaker_open(fam, now): return None
    if get is None:
        if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("EODHD_DISABLE") == "1": return None
        get = _http
    try: code, js = get(path, dict(q or {}))
    except Exception as e:
        with _L: _ERRN[fam] = _ERRN.get(fam, 0) + 1; STATS["eodhd_fail"] += 1
        if _ERRN[fam] >= 5: _trip(fam, "neterr " + redact(e)[:60], now)
        return None
    if code in (401, 403, 429): _trip(fam, "http %s" % code, now); STATS["eodhd_fail"] += 1; return None
    if code != 200: STATS["eodhd_fail"] += 1; return None
    with _L: _ERRN[fam] = 0; STATS["eodhd_ok"] += 1
    return js
def _rows(js):
    out = []
    for r in js or []:
        try:
            o, h, l, c = (float(r[k]) for k in ("open", "high", "low", "close"))
            if c > 0 and h >= l: out.append((str(r["date"])[:10], o, h, l, c, float(r.get("volume") or 0)))
        except Exception: continue
    return out
def eod_rows(sym, start=None, end=None, get=None):
    """Raw (unadjusted, like pit Yahoo auto_adjust=False) daily OHLCV [(date,o,h,l,c,v)] from EODHD, or [] ."""
    code = eodhd_symbol(sym)
    if not code: return []
    q = {"period": "d"}
    if start: q["from"] = str(start)
    if end: q["to"] = str(end)
    js = call("eod", "/eod/" + urllib.parse.quote(code), q, get)
    return _rows(js if isinstance(js, list) else [])
def bulk_last_day(exchange, date=None, symbols=None, get=None):
    """EODHD bulk EOD for a whole exchange/day: {eodhd_code: (date,o,h,l,c,v)}. One call (=100 API credits) per exchange."""
    q = {}
    if date: q["date"] = str(date)
    if symbols: q["symbols"] = ",".join(symbols)
    js = call("bulk", "/eod-bulk-last-day/" + exchange, q, get)
    out = {}
    for r in js if isinstance(js, list) else []:
        rr = _rows([r])
        if rr and r.get("code"): out["%s.%s" % (r["code"], r.get("exchange_short_name") or exchange)] = rr[0]
    return out
def intraday(sym, interval="5m", start_utc=None, get=None):
    code = eodhd_symbol(sym)
    if not code: return []
    q = {"interval": interval}
    if start_utc: q["from"] = int(start_utc.timestamp())
    js = call("intraday", "/intraday/" + urllib.parse.quote(code), q, get)
    out = []
    for r in js if isinstance(js, list) else []:
        try: out.append((int(r["timestamp"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r.get("volume") or 0)))
        except Exception: continue
    return out
def eodhd_quote(sym, get=None, now_utc=None, max_age_min=30):
    """EODHD real-time (US live; other venues ~15-20 min delayed) -> dict like book.live_px or None if stale/absent."""
    code = eodhd_symbol(sym)
    if not code: return None
    js = call("rt", "/real-time/" + urllib.parse.quote(code), {}, get)
    try:
        last = float(js["close"]); ts = dt.datetime.fromtimestamp(int(js["timestamp"]), UTC)
        if not last > 0: return None
    except Exception: return None
    t = now_utc or dt.datetime.now(UTC); age = (t - ts).total_seconds() / 60
    return dict(last=last, hi=float(js.get("high") or last), lo=float(js.get("low") or last), open=float(js.get("open") or last),
                prev_close=js.get("previousClose"), asof=ts.isoformat(), age_min=round(age, 1), stale=age > max_age_min, src="eodhd_rt")
def quote(sym, yahoo_fn=None, now_utc=None):
    """Paid-first quote. EU/LSE: EODHD Cboe websocket when healthy. US: Massive 5m -> EODHD rt. Else EODHD rt. Yahoo only if those fail."""
    try:
        from veteran_desk import eodhd_eu_ws as _EU
        q = _EU.live_px(sym, now=now_utc)
        if q: return q
    except Exception: pass
    try:
        from veteran_desk import massive as _MS
        if eodhd_symbol(sym) and eodhd_symbol(sym).endswith(".US"):
            q = _MS.quote(sym, now_utc=now_utc)
            if q: STATS["massive_ok"] += 1; return q
    except Exception: pass
    q = eodhd_quote(sym, now_utc=now_utc)
    if q and not q["stale"]: return q
    if yahoo_fn:
        try:
            y = yahoo_fn(sym)
            if y: STATS["yahoo_fallback"] += 1; return y
        except Exception: pass
    return q
def daily_rows(sym, period_days=31, yahoo_fn=None):
    """Paid-first daily bars: EODHD -> Yahoo fallback. Returns (rows, src)."""
    start = (dt.datetime.now(UTC).date() - dt.timedelta(days=int(period_days))).isoformat()
    r = eod_rows(sym, start)
    if r: return r, "eodhd"
    if yahoo_fn:
        try:
            y = yahoo_fn(sym)
            if y: STATS["yahoo_fallback"] += 1; return y, "yahoo_fallback"
        except Exception: pass
    return [], "none"
def fundamentals(sym, filt=None, get=None):
    code = eodhd_symbol(sym)
    return call("fund", "/fundamentals/" + urllib.parse.quote(code), {"filter": filt} if filt else {}, get) if code else None
def earnings(start, end, symbols=None, get=None):
    q = {"from": str(start), "to": str(end)}
    if symbols: q["symbols"] = ",".join(filter(None, (eodhd_symbol(s) for s in symbols)))
    js = call("cal", "/calendar/earnings", q, get); return (js or {}).get("earnings") or []
def news(tickers=None, limit=25, get=None):
    """Massive/Benzinga first, then EODHD news. Items: title, link, source, published_utc."""
    try:
        from veteran_desk import massive as _MS
        n = _MS.news(tickers, limit)
        if n: return n
    except Exception: pass
    q = {"limit": limit}
    if tickers: q["s"] = eodhd_symbol(tickers[0]) or tickers[0]
    else: q["t"] = "markets"
    js = call("news", "/news", q, get); out = []
    for r in js if isinstance(js, list) else []:
        if r.get("title") and r.get("date"): out.append(dict(title=r["title"][:220], link=r.get("link") or "", source="eodhd_news", published_utc=str(r["date"])))
    return out
def health(get=None):
    """For the data-feed doctor: probe Massive + EODHD (1 cheap call each). Never returns key material."""
    out = {}
    js = call("health", "/eod/AAPL.US", {"from": (dt.date.today() - dt.timedelta(days=7)).isoformat()}, get)
    out["eodhd"] = dict(ok=bool(js), breaker={k: round((v - time.time()) / 60, 1) for k, v in _BRK.items() if v > time.time()}, key_present=bool(_key()))
    try:
        from veteran_desk import massive as _MS
        q = _MS._call("health", "/v2/aggs/ticker/AAPL/prev", {}, get=None)
        out["massive"] = dict(ok=bool(q), key_present=bool(_MS._key()), breaker=[k for k, v in _MS._BRK.items() if v > time.time()])
    except Exception as e: out["massive"] = dict(ok=False, err=str(e)[:80])
    out["stats"] = dict(STATS); return out
