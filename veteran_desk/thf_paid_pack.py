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
# 2026-10-30 22:10 WITA (Brett via TFO): CoinGlass retired -> Coinalyze is the crypto derivatives source (funding, OI, liquidations)
# for Kai and the THF crypto seat. Key: ~/.tfo_secrets/coinalyze.env COINALYZE_API_KEY (never printed). No key -> listed as inactive, not a failure.
CZ = "https://api.coinalyze.net/v1"
CZ_SYMS = {"BTC": "BTCUSDT_PERP.A", "ETH": "ETHUSDT_PERP.A", "SOL": "SOLUSDT_PERP.A"}   # Binance USDT perps
def coinalyze_key(): return _key("coinalyze", "COINALYZE_API_KEY")
def coinalyze_derivs():
    k = coinalyze_key()
    if not k: return None
    h = {"api_key": k}; syms = ",".join(CZ_SYMS.values()); now = int(time.time())
    oi = _get("cz_oi", CZ + "/open-interest?convert_to_usd=true&symbols=" + syms, h)
    fr = _get("cz_fr", CZ + "/funding-rate?symbols=" + syms, h)
    lq = _get("cz_liq", CZ + "/liquidation-history?interval=1hour&convert_to_usd=true&from=%d&to=%d&symbols=%s" % (now - 86400, now, syms), h)
    ls = _get("cz_ls", CZ + "/long-short-ratio-history?interval=1hour&from=%d&to=%d&symbols=%s" % (now - 7200, now, syms), h)
    out = {}
    for c, sym in CZ_SYMS.items():
        r = {}
        for x in (oi or []):
            if x.get("symbol") == sym: r["oi_usd"] = x.get("value")
        for x in (fr or []):
            if x.get("symbol") == sym: r["funding_rate"] = x.get("value")
        for x in (lq or []):
            if x.get("symbol") == sym:
                hist = x.get("history") or []
                r["liq_24h_long_usd"] = round(sum(float(z.get("l") or 0) for z in hist)); r["liq_24h_short_usd"] = round(sum(float(z.get("s") or 0) for z in hist))
        for x in (ls or []):
            if x.get("symbol") == sym and x.get("history"):
                z = x["history"][-1]; r["long_short_ratio"] = z.get("r"); r["long_pct"] = z.get("l"); r["short_pct"] = z.get("s")
        if r: out[c] = r
    if not out: return None
    res = dict(src="coinalyze_v1_oi_funding_liq_ls", rows=out)
    try:   # snapshot for the dashboards (VD dash Crypto derivatives card); atomic write, no key inside
        import datetime as _dt
        if os.environ.get("PYTEST_CURRENT_TEST"): return res
        d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "data_hub"); os.makedirs(d, exist_ok=True)
        t = os.path.join(d, "coinalyze_latest.json.tmp"); json.dump(dict(res, utc=_dt.datetime.now(_dt.timezone.utc).isoformat(), feed_source="coinalyze"), open(t, "w")); os.replace(t, os.path.join(d, "coinalyze_latest.json"))
    except Exception: pass
    return res
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
        "uw_congress": uw_congress, "coinalyze_derivs": coinalyze_derivs, "eia_spot": eia_spot}
def pack():
    """All paid feeds that answered, each source-tagged; misses listed so the seat knows what fell back."""
    out, miss, inactive = {}, [], []
    for n, f in PACK.items():
        if n == "coinalyze_derivs" and not coinalyze_key(): inactive.append("coinalyze_derivs: key missing (awaiting new key from Brett)"); continue
        try: v = f()
        except Exception: v = None
        (out.__setitem__(n, v) if v else miss.append(n))
    return dict(feeds=out, unavailable=miss, inactive=inactive, note="paid feeds first (Brett 30 Oct 21:17 WITA); Yahoo/free feeds are fallback only")
if __name__ == "__main__":
    p = pack(); print(json.dumps({k: (v["src"], len(v["rows"])) for k, v in p["feeds"].items()}), "unavailable:", p["unavailable"])
