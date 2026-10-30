"""tfo_paidpush_20261030 (Brett 30 Oct 2026 21:17 WITA): paid research feeds for seat DATA packs. PAPER research only.
Keys are loaded ONLY from ~/.tfo_secrets/<name>.env (or the env var); never printed, logged or returned. Each item is source-tagged.
Any 401/403/429/error trips a 30-min breaker and returns None -> seat falls back to the free feeds (Yahoo etc.). Disabled under pytest."""
import json, os, time, urllib.error, urllib.parse, urllib.request
_BRK = {}; BREAKER_MIN = 30
def _key(fname, var):
    k = os.environ.get(var)
    if k: return k.strip()
    try:
        for ln in open(os.path.expanduser("~/.tfo_secrets/%s.env" % fname)):
            if ln.strip().startswith(var + "="): return ln.split("=", 1)[1].strip().strip('"\'')
    except Exception: return None
def _get(name, url, headers=None, t=10):
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("PAID_FEEDS_DISABLE") == "1": return None
    if _BRK.get(name, 0) > time.time(): return None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=dict({"User-Agent": "TFO paper desk", "Accept": "application/json"}, **(headers or {}))), timeout=t) as f:
            return json.loads(f.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 429): _BRK[name] = time.time() + BREAKER_MIN * 60
        return None
    except Exception: return None
def _rows(js):
    if isinstance(js, list): return js
    if isinstance(js, dict):
        for k in ("data", "results", "response"):
            v = js.get(k)
            if isinstance(v, list): return v
            if isinstance(v, dict) and isinstance(v.get("data"), list): return v["data"]
    return []
def _slim(r, keep):
    return {k: r.get(k) for k in keep if isinstance(r, dict) and r.get(k) is not None}
def uw(path, keep, n=15):
    k = _key("unusual_whales", "UNUSUAL_WHALES_API_KEY")
    if not k: return None
    js = _get("uw" + path, "https://api.unusualwhales.com" + path, {"Authorization": "Bearer " + k})
    rows = [_slim(r, keep) for r in _rows(js)[:n]]
    return dict(src="unusual_whales" + path, rows=rows) if rows else None
def uw_flow(): return uw("/api/option-trades/flow-alerts?limit=30", ("ticker", "type", "total_premium", "strike", "expiry", "alert_rule", "underlying_price", "created_at"))
def uw_darkpool(): return uw("/api/darkpool/recent?limit=30", ("ticker", "size", "price", "premium", "executed_at"))
def uw_congress(): return uw("/api/congress/recent-trades?limit=20", ("ticker", "txn_type", "amounts", "name", "transaction_date", "filed_at_date"))
def coinglass_funding():
    k = _key("coinglass", "COINGLASS_API_KEY")
    if not k: return None
    out = {}
    for s in ("BTC", "ETH", "SOL"):
        js = _get("cg_oi", "https://open-api-v4.coinglass.com/api/futures/open-interest/exchange-list?symbol=" + s, {"CG-API-KEY": k})
        r = _rows(js)
        if r: out[s] = [_slim(x, ("exchange", "open_interest_usd", "open_interest_change_percent_24h")) for x in r[:3]]
    return dict(src="coinglass_v4_open_interest", rows=out) if out else None
def eia_spot():
    k = _key("eia", "EIA_API_KEY")
    if not k: return None
    q = urllib.parse.urlencode([("api_key", k), ("frequency", "daily"), ("data[0]", "value"), ("sort[0][column]", "period"), ("sort[0][direction]", "desc"), ("length", "12")])
    r = _rows(_get("eia", "https://api.eia.gov/v2/petroleum/pri/spt/data/?" + q))
    rows = [_slim(x, ("period", "series-description", "value", "units")) for x in r]
    return dict(src="eia_v2_petroleum_spot", rows=rows) if rows else None
def fmp_movers():
    k = _key("fmp", "FMP_API_KEY")
    if not k: return None
    out = {}
    for p in ("biggest-gainers", "biggest-losers", "most-actives"):
        r = _rows(_get("fmp_" + p, "https://financialmodelingprep.com/stable/%s?apikey=%s" % (p, urllib.parse.quote(k))))
        if r: out[p] = [_slim(x, ("symbol", "price", "changesPercentage", "exchange")) for x in r[:10]]
    return dict(src="fmp_stable_movers", rows=out) if out else None
def benzinga():
    from veteran_desk import massive as M
    r = M.news(limit=20)
    return dict(src=(r[0]["source"] if r else "massive"), rows=r) if r else None
def eodhd_earnings():
    import datetime as dt
    from veteran_desk import data_layer as DL
    d = dt.date.today(); r = DL.earnings(str(d), str(d + dt.timedelta(days=3)))
    r = _rows(r) if not isinstance(r, list) else r
    return dict(src="eodhd_earnings_calendar", rows=r[:40]) if r else None
PACK = {"benzinga_news": benzinga, "eodhd_earnings": eodhd_earnings, "fmp_movers": fmp_movers, "uw_flow": uw_flow, "uw_darkpool": uw_darkpool,
        "uw_congress": uw_congress, "coinglass_oi": coinglass_funding, "eia_spot": eia_spot}
def pack():
    """All paid feeds that answered, each source-tagged; misses listed so the seat knows what fell back."""
    out, miss = {}, []
    for n, f in PACK.items():
        try: v = f()
        except Exception: v = None
        (out.__setitem__(n, v) if v else miss.append(n))
    return dict(feeds=out, unavailable=miss, note="paid feeds first (Brett 30 Oct 21:17 WITA); Yahoo/free feeds are fallback only")
if __name__ == "__main__":
    p = pack(); print(json.dumps({k: (v["src"], len(v["rows"])) for k, v in p["feeds"].items()}), "unavailable:", p["unavailable"])
