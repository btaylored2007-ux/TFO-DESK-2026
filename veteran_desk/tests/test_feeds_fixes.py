import io, json, os, sys, tempfile, urllib.error
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import feeds as F, news_desk as N
def test_308_handler_and_urls():
    assert hasattr(F._Redir, "http_error_308") and "theregister" not in json.dumps(N.FEEDS) and N.FEEDS["CoinDesk"].endswith("/rss")
def test_coingecko_backoff_then_cache():
    F.CG_CACHE = os.path.join(tempfile.mkdtemp(), "cg.json"); calls, slept = [], []
    good = json.dumps(dict(data=dict(market_cap_percentage=dict(btc=55.123, eth=12.0), market_cap_change_percentage_24h_usd=1.0))).encode()
    def get(u, t=15):
        calls.append(u)
        if len(calls) < 3: raise urllib.error.HTTPError(u, 429, "Too Many", {}, io.BytesIO(b""))
        return good
    r = F.coingecko_global(sleep=slept.append, get=get); assert r["btc_dominance_pct"] == 55.12 and slept == [2.0, 4.0]
    def bad(u, t=15): raise urllib.error.HTTPError(u, 429, "x", {}, io.BytesIO(b""))
    r = F.coingecko_global(sleep=lambda s: None, get=bad); assert r["stale"] and r["btc_dominance_pct"] == 55.12
def test_fred_fallback():
    csv = b"observation_date,DEXUSAL\n2026-10-23,0.6512\n2026-10-24,.\n"
    assert F.fred_last("DEXUSAL", get=lambda u, t=15: csv) == ("2026-10-23", 0.6512)
    orig = F._get; F._get = lambda u, t=15: csv
    try: p = F.px_with_fallback("AUDUSD=X", lambda s: None); assert p["last"] == 0.6512 and p["src"] == "fred:DEXUSAL", p
    finally: F._get = orig
    assert F.px_with_fallback("XOM", lambda s: dict(last=1)) == dict(last=1) and F.fred_px("XOM") is None
if __name__ == "__main__":
    for k, v in dict(globals()).items():
        if k.startswith("test_"): v(); print("PASS", k)
