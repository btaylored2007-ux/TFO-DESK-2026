"""Massive (formerly Polygon.io) US-equity quotes/bars + news. tfo_massive_20261030. PAPER research only.
Key: $MASSIVE_API_KEY or ~/.tfo_secrets/massive.env (chmod 600, outside the repo). The key is sent only in the Authorization
header and is NEVER printed, logged or put in a URL. PRIMARY for US equities in book.live_px; Yahoo stays the fallback.
Entitlement-aware: 401/403 (plan) or 429 (rate limit) trips an in-process breaker for BREAKER_MIN, and a last bar older than
MAX_AGE_MIN is refused (returns None) so stale/delayed data can never replace a live Yahoo print. Disabled under pytest unless
a getter is injected. Benzinga news (/benzinga/v2/news) is tried first; if not entitled, Massive reference news is used."""
import datetime as dt, json, os, time, urllib.error, urllib.parse, urllib.request
UTC = dt.timezone.utc; HOST = "https://api.massive.com"; SECRET = os.path.expanduser("~/.tfo_secrets/massive.env")
MAX_AGE_MIN = float(os.environ.get("MASSIVE_MAX_AGE_MIN", "20")); BREAKER_MIN = 30; _BRK = {}
def _key():
    k = os.environ.get("MASSIVE_API_KEY")
    if k: return k.strip()
    try:
        for ln in open(SECRET):
            if ln.strip().startswith("MASSIVE_API_KEY="): return ln.split("=", 1)[1].strip().strip('"\'')
    except Exception: return None
def _http(path, q, key, t=8):
    r = urllib.request.Request(HOST + path + "?" + urllib.parse.urlencode(q), headers={"Authorization": "Bearer " + key, "User-Agent": "TFO paper desk"})
    try:
        with urllib.request.urlopen(r, timeout=t) as f: return f.status, json.loads(f.read())
    except urllib.error.HTTPError as e: return e.code, None
def _call(name, path, q, get=None, now=None):
    now = now or time.time()
    if _BRK.get(name, 0) > now: return None
    if get is None:
        if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("MASSIVE_DISABLE") == "1": return None
        key = _key()
        if not key: return None
        get = lambda p, qq: _http(p, qq, key)
    try: code, js = get(path, q)
    except Exception: return None
    if code in (401, 403, 429): _BRK[name] = now + BREAKER_MIN * 60; return None
    return js if code == 200 and isinstance(js, dict) else None
def quote(sym, get=None, now_utc=None):
    """Last 5m bars -> dict like book.live_px (last, hi, lo, open over last 6 bars, asof, window_start, src) or None."""
    t = now_utc or dt.datetime.now(UTC); s = str(sym).upper().replace("-", ".")
    js = _call("aggs", "/v2/aggs/ticker/%s/range/5/minute/%s/%s" % (urllib.parse.quote(s), (t - dt.timedelta(days=4)).date(), t.date()),
               dict(adjusted="true", sort="asc", limit=5000), get, t.timestamp())
    rows = [r for r in ((js or {}).get("results") or []) if all(k in r for k in ("t", "o", "h", "l", "c"))]
    if not rows: return None
    last = rows[-6:]; ts = dt.datetime.fromtimestamp(rows[-1]["t"] / 1000, UTC) + dt.timedelta(minutes=5)   # bar close time
    if (t - ts).total_seconds() / 60 > MAX_AGE_MIN: return None   # delayed/EOD-only plan or closed market -> Yahoo
    return dict(last=float(rows[-1]["c"]), hi=float(max(r["h"] for r in last)), lo=float(min(r["l"] for r in last)), open=float(last[0]["o"]),
                asof=str(ts - dt.timedelta(minutes=5)), window_start=dt.datetime.fromtimestamp(last[0]["t"] / 1000, UTC).isoformat(), src="massive_aggs_5m")
def news(tickers=None, limit=25, get=None):
    """Benzinga via Massive if entitled, else Massive reference news. Items: title, link, source, published_utc."""
    q = dict(limit=limit, sort="published_utc", order="desc")
    if tickers: q["tickers"] = ",".join(tickers)
    for name, path, src in (("benzinga", "/benzinga/v2/news", "massive_benzinga"), ("refnews", "/v2/reference/news", "massive_news")):
        qq = dict(q)
        if name == "benzinga": qq.pop("order", None); qq["sort"] = "published.desc"   # tfo_benzsort_20261030: published_utc/order=desc -> 400 on Benzinga
        if name == "refnews" and tickers: qq.pop("tickers"); qq["ticker"] = tickers[0]
        js = _call(name, path, qq, get)
        out = []
        for r in (js or {}).get("results") or []:
            ti = (r.get("title") or "").strip(); pub = r.get("published_utc") or r.get("published")
            if ti and pub: out.append(dict(title=ti[:220], link=r.get("article_url") or r.get("url") or "", source=src, published_utc=str(pub)))
        if out: return out
    return []
