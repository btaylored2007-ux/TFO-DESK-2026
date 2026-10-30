"""CoinGlass retired -> Coinalyze; news desk paid-first with source tags; promptcap/learning covered in their own tests."""
import inspect
from veteran_desk import thf_paid_pack as P, news_desk as N
def test_no_coinglass_in_pack():
    assert "coinglass_oi" not in P.PACK and "coinalyze_derivs" in P.PACK
    assert "open-api-v4.coinglass" not in inspect.getsource(P)
def test_coinalyze_missing_key_is_inactive_not_failure(monkeypatch):
    monkeypatch.setattr(P, "coinalyze_key", lambda: None)
    p = P.pack(); assert "coinalyze_derivs" not in p["unavailable"] and any("coinalyze" in x for x in p["inactive"])
def test_coinalyze_parse(monkeypatch):
    def g(name, url, headers=None, t=10):
        sym = "BTCUSDT_PERP.A"
        return {"cz_oi": [dict(symbol=sym, value=1e9)], "cz_fr": [dict(symbol=sym, value=0.01)],
                "cz_liq": [dict(symbol=sym, history=[dict(l=5, s=7), dict(l=1, s=1)])], "cz_ls": [dict(symbol=sym, history=[dict(r=2.0, l=66.7, s=33.3)])]}[name]
    monkeypatch.setattr(P, "_get", g); monkeypatch.setattr(P, "coinalyze_key", lambda: "k")
    monkeypatch.setattr(P.os, "replace", lambda a, b: None)
    r = P.coinalyze_derivs()["rows"]["BTC"]
    assert r == dict(oi_usd=1e9, funding_rate=0.01, liq_24h_long_usd=6, liq_24h_short_usd=8, long_short_ratio=2.0, long_pct=66.7, short_pct=33.3)
def test_news_paid_first_and_tagged():
    src = inspect.getsource(N.headlines)
    assert src.index("_paid_news") < src.index("FEEDS.items()") and 'feed_source="rss_fallback"' in src
