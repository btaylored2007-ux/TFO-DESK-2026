"""TFO shared paid-feed data hub (PAPER infrastructure, 2026-10-30 Brett FULL AUTH + HARD RULE: paid feeds primary, Yahoo fallback only).
One cross-process fetcher per paid feed (Massive, EODHD, FMP /stable/ only, Unusual Whales, EIA) with a file-locked rate limiter and a
shared sqlite TTL cache under state/data_hub/, so every desk (main book, VD, THF, V2, Fundamental, hunts) shares one budget.
Crypto prices: Kraken via kraken_dns (DoH) first, box relay snapshot fallback. Every result carries feed_source; every Yahoo use is
logged to state/data_hub/yahoo_fallback.jsonl with the paid failure reason. Keys are read from ~/.tfo_secrets/*.env and are NEVER logged:
URLs containing tokens are never written anywhere. CoinGlass is blocked (401 Upgrade plan) and refused without a network call."""
import datetime as dt, fcntl, json, os, sqlite3, time, urllib.error, urllib.parse, urllib.request
BASE = os.path.dirname(os.path.abspath(__file__)); HUB = os.path.join(BASE, "state", "data_hub"); os.makedirs(HUB, exist_ok=True)
DB = os.path.join(HUB, "cache.sqlite"); YLOG = os.path.join(HUB, "yahoo_fallback.jsonl"); USE = os.path.join(HUB, "usage.jsonl")
WS_LAST = os.path.join(HUB, "massive_ws_last.json"); RELAY = os.path.expanduser("~/.tfo_swing/box_relay/relay_snapshot.json")
SECRETS = os.path.expanduser("~/.tfo_secrets"); UTC = dt.timezone.utc
FEEDS = {   # min seconds between calls (conservative; shared by ALL processes on the Mac)
    "massive": dict(env="massive.env", var="MASSIVE_API_KEY", host="https://api.massive.com", min_gap=0.05, auth="bearer"),
    "eodhd": dict(env="eodhd.env", var="EODHD_API_KEY", host="https://eodhd.com/api", min_gap=0.1, auth="api_token"),
    "fmp": dict(env="fmp.env", var="FMP_API_KEY", host="https://financialmodelingprep.com/stable", min_gap=0.25, auth="apikey"),
    "unusual_whales": dict(env="unusual_whales.env", var="UNUSUAL_WHALES_API_KEY", host="https://api.unusualwhales.com", min_gap=0.5, auth="bearer"),
    "eia": dict(env="eia.env", var="EIA_API_KEY", host="https://api.eia.gov", min_gap=0.5, auth="api_key"),
}
BLOCKED = {"coinglass": "401 Upgrade plan (Brett fixing) - treated as blocked"}
BREAKER_S = 600; _BRK = {}
class FeedError(Exception): pass
def _key(feed):
    f = FEEDS[feed]; v = os.environ.get(f["var"])
    if v: return v.strip()
    try:
        for ln in open(os.path.join(SECRETS, f["env"])):
            if ln.strip().startswith(f["var"] + "="): return ln.split("=", 1)[1].strip().strip('"\'')
    except OSError: return None
def _throttle(feed):
    lk = os.path.join(HUB, ".rl_%s.lock" % feed)
    with open(lk, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.seek(0); last = float(fh.read().strip() or 0)
        except ValueError: last = 0.0
        w = FEEDS[feed]["min_gap"] - (time.time() - last)
        if w > 0: time.sleep(w)
        fh.seek(0); fh.truncate(); fh.write(repr(time.time())); fh.flush()
        fcntl.flock(fh, fcntl.LOCK_UN)
def _db():
    c = sqlite3.connect(DB, timeout=10); c.execute("pragma journal_mode=wal")
    c.execute("create table if not exists cache(k text primary key, ts real, body text)"); return c
def _cache_get(k, ttl):
    try:
        with _db() as c:
            r = c.execute("select ts, body from cache where k=?", (k,)).fetchone()
        return json.loads(r[1]) if r and time.time() - r[0] <= ttl else None
    except Exception: return None
def _cache_put(k, body):
    try:
        with _db() as c: c.execute("insert or replace into cache values(?,?,?)", (k, time.time(), json.dumps(body)))
    except Exception: pass
def _usage(feed, path, code, who):
    try:
        with open(USE, "a") as f: f.write(json.dumps(dict(utc=dt.datetime.now(UTC).isoformat(), feed=feed, path=path, code=code, caller=who)) + "\n")
    except Exception: pass
def fetch(feed, path, params=None, ttl=60, caller=None, timeout=12, get=None):
    """GET a paid-feed JSON endpoint through the shared limiter + cache. Raises FeedError (never returns Yahoo)."""
    if feed in BLOCKED: raise FeedError("%s blocked: %s" % (feed, BLOCKED[feed]))
    if feed not in FEEDS: raise FeedError("unknown feed %s" % feed)
    if feed == "fmp" and path.startswith("/api/"): raise FeedError("FMP legacy /api/ paths are not on the plan; use /stable/")
    params = dict(params or {}); ck = feed + "|" + path + "|" + json.dumps(params, sort_keys=True)
    hit = _cache_get(ck, ttl)
    if hit is not None: return hit
    if _BRK.get(feed, 0) > time.time(): raise FeedError("%s breaker open" % feed)
    f = FEEDS[feed]; key = _key(feed)
    if not key: raise FeedError("%s key missing" % feed)
    hdr = {"User-Agent": "TFO paper desk hub", "Accept": "application/json"}
    if f["auth"] == "bearer": hdr["Authorization"] = "Bearer " + key
    else: params[f["auth"]] = key
    if feed == "eodhd": params.setdefault("fmt", "json")
    url = f["host"] + path + ("?" + urllib.parse.urlencode(params) if params else "")
    _throttle(feed)
    try:
        if get: code, body = get(url, hdr)
        else:
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=timeout) as r: code, body = r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: code, body = e.code, None
    except Exception as e:
        _usage(feed, path, "neterr", caller); raise FeedError("%s network error %s" % (feed, type(e).__name__))   # never str(e): may hold the URL
    _usage(feed, path, code, caller)
    if code in (401, 403, 429): _BRK[feed] = time.time() + (60 if code == 429 else BREAKER_S); raise FeedError("%s HTTP %s" % (feed, code))
    if code != 200 or body is None: raise FeedError("%s HTTP %s" % (feed, code))
    _cache_put(ck, body); return body
def _yahoo_log(sym, why, caller):
    try:
        with open(YLOG, "a") as f: f.write(json.dumps(dict(utc=dt.datetime.now(UTC).isoformat(), sym=sym, paid_fail=why, caller=caller, feed_source="yahoo_fallback")) + "\n")
    except Exception: pass
def is_crypto(sym): s = str(sym).upper(); return s.startswith("KRAKEN:") or s.endswith("-USD") and s.split("-")[0] in ("BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "XMR", "LTC", "DOT", "AVAX", "LINK")
def is_us(sym): s = str(sym); return "." not in s.replace("BRK.B", "").replace("BF.B", "") and "=" not in s and "^" not in s and ":" not in s and not s.endswith("-USD")
def ws_quote(sym, max_age_s=60):
    try:
        d = json.load(open(WS_LAST)).get(str(sym).upper())
        if d and time.time() - d["t"] / 1000.0 <= max_age_s: return dict(last=d["p"], bid=d.get("bp"), ask=d.get("ap"), asof=dt.datetime.fromtimestamp(d["t"] / 1000.0, UTC).isoformat(), feed_source="massive_ws")
    except Exception: return None
def kraken_quote(sym):
    import kraken_dns; kraken_dns.install()
    pair = str(sym).upper().replace("KRAKEN:", "").replace("-USD", "USD").replace("BTCUSD", "XBTUSD")
    try:
        j = json.loads(urllib.request.urlopen("https://api.kraken.com/0/public/Ticker?pair=" + urllib.parse.quote(pair), timeout=8).read())
        r = list((j.get("result") or {}).values())[0]
        return dict(last=float(r["c"][0]), bid=float(r["b"][0]), ask=float(r["a"][0]), asof=dt.datetime.now(UTC).isoformat(), feed_source="kraken")
    except Exception:
        try:
            snap = json.load(open(RELAY)); v = (snap.get("prices") or snap).get(pair)
            if v: return dict(last=float(v.get("last") if isinstance(v, dict) else v), asof=snap.get("utc"), feed_source="box_relay")
        except Exception: pass
    return None
def eodhd_symbol(sym):
    s = str(sym).upper()
    for suf, ex in ((".TO", ".TO"), (".V", ".V"), (".AX", ".AU"), (".L", ".LSE"), (".T", ".TSE"), (".HK", ".HK"), (".SI", ".SG"), (".PA", ".PA"), (".DE", ".XETRA")):
        if s.endswith(suf): return s[: -len(suf)] + ex
    return s if "." in s else s + ".US"
def quote(sym, caller=None, yahoo_fn=None):
    """Last price with feed_source. Crypto: Kraken -> relay. US: Massive WS -> Massive REST -> FMP -> Yahoo(fallback). Other: EODHD real-time -> FMP -> Yahoo(fallback)."""
    s = str(sym).upper(); why = []
    if is_crypto(s):
        q = kraken_quote(s)
        if q: return q
        why.append("kraken+relay failed")
    else:
        if is_us(s):
            q = ws_quote(s)
            if q: return q
            try:
                j = fetch("massive", "/v2/last/trade/%s" % urllib.parse.quote(s), ttl=5, caller=caller); r = j.get("results") or {}
                if r.get("p"): return dict(last=float(r["p"]), asof=dt.datetime.fromtimestamp(r["t"] / 1e9, UTC).isoformat(), feed_source="massive_rest")
                why.append("massive empty")
            except FeedError as e: why.append(str(e))
        else:
            try:
                j = fetch("eodhd", "/real-time/%s" % eodhd_symbol(s), ttl=15, caller=caller); px = j.get("close")
                ts = j.get("timestamp")
                if px not in (None, "NA") and ts and time.time() - float(ts) < 30 * 60: return dict(last=float(px), asof=dt.datetime.fromtimestamp(float(ts), UTC).isoformat(), feed_source="eodhd_rt")
                why.append("eodhd stale/empty")
            except (FeedError, ValueError, TypeError) as e: why.append(str(e))
        for v in dict.fromkeys([s, s.replace(".TO", ".TSX"), s.replace(".TO", "")]):   # FMP symbol variants (BNS.TO had an empty reply)
            try:
                j = fetch("fmp", "/quote", dict(symbol=v), ttl=15, caller=caller)
                if isinstance(j, list) and j and j[0].get("price"): return dict(last=float(j[0]["price"]), asof=dt.datetime.fromtimestamp(int(j[0].get("timestamp") or time.time()), UTC).isoformat(), feed_source="fmp:" + v)
            except FeedError as e: why.append(str(e)); break
        why.append("fmp empty")
    if yahoo_fn:
        _yahoo_log(s, "; ".join(why)[:300], caller)
        try:
            q = yahoo_fn(s)
            if q: return dict(q, feed_source="yahoo_fallback", paid_fail="; ".join(why)[:300])
        except Exception: pass
    return None
def eod_bars(sym, frm=None, caller=None):
    """Daily bars from EODHD (paid primary) as [{date,o,h,l,c,v}] or raises FeedError."""
    p = {"from": frm} if frm else {}
    rows = fetch("eodhd", "/eod/%s" % eodhd_symbol(sym), p, ttl=3600, caller=caller)
    return [dict(date=r["date"], o=r["open"], h=r["high"], l=r["low"], c=r.get("adjusted_close", r["close"]), v=r["volume"]) for r in rows]
