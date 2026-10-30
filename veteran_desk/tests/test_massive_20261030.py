import datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import massive as M
UTC = dt.timezone.utc; T = dt.datetime(2026, 10, 30, 14, 0, tzinfo=UTC)
def _bars(end, n=8):
    return [dict(t=int((end - dt.timedelta(minutes=5 * (n - i))).timestamp() * 1000), o=10 + i, h=11 + i, l=9 + i, c=10.5 + i) for i in range(n)]
def setup_function(_): M._BRK.clear()
def test_fresh_quote_primary():
    q = M.quote("AAPL", get=lambda p, q: (200, dict(results=_bars(T))), now_utc=T)
    assert q["src"] == "massive_aggs_5m" and q["last"] == 17.5 and q["hi"] == 18 and q["lo"] == 11
def test_stale_bars_refused():
    assert M.quote("AAPL", get=lambda p, q: (200, dict(results=_bars(T - dt.timedelta(hours=20)))), now_utc=T) is None
def test_403_trips_breaker():
    n = []
    g = lambda p, q: (n.append(1), (403, None))[1]
    assert M.quote("AAPL", get=g, now_utc=T) is None and M.quote("MSFT", get=g, now_utc=T) is None and len(n) == 1
def test_disabled_under_pytest_without_getter():
    assert M.quote("AAPL") is None and M.news(["AAPL"]) == []
def test_news_benzinga_then_reference():
    def g(p, q):
        if "benzinga" in p: return 403, None
        return 200, dict(results=[dict(title="Apple beats", article_url="http://x", published_utc="2026-10-30T12:00:00Z")])
    h = M.news(["AAPL"], get=g); assert h[0]["source"] == "massive_news" and h[0]["title"] == "Apple beats"
def test_live_px_massive_primary_yahoo_fallback(monkeypatch):
    from veteran_desk import book as B
    monkeypatch.setattr(M, "quote", lambda s: dict(last=1.0, src="massive_aggs_5m"))
    assert B.live_px("AAPL")["src"] == "massive_aggs_5m"
    monkeypatch.setattr(M, "quote", lambda s: None)
    monkeypatch.setattr(B, "yahoo_v8_quote", lambda s: dict(last=2.0, src="yahoo_chart_v8"))
    import types; monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=lambda s: (_ for _ in ()).throw(RuntimeError("no net"))))
    assert B.live_px("AAPL")["src"] == "yahoo_chart_v8"
    called = []; monkeypatch.setattr(M, "quote", lambda s: called.append(s))
    B.live_px("BHP.AX"); assert called == []   # non-US never routed to Massive
