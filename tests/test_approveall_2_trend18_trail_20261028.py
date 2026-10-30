"""Brett 28 Oct 13:40 approve-all #2: Trend18 UPRO/TQQQ weekly 20% trail LIVE (paper). No live orders."""
import datetime as dt, os, sys, tempfile, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import trend18 as t, trend18_trail as tt

def _L():
    return {"holdings": {"UPRO": 100.0, "TQQQ": 200.0}, "entry_px": {"UPRO": 100.0, "TQQQ": 50.0},
            "mark_px_usd": {"UPRO": 79.0, "TQQQ": 45.0}, "mark_utc": {}, "mark_source": {}, "cash": 0.0,
            "fills": [{"inst": "UPRO", "side": "BUY", "utc": "2026-09-01T00:00:00Z"}, {"inst": "TQQQ", "side": "BUY", "utc": "2026-09-01T00:00:00Z"}]}

def _hist(path):
    idx = pd.bdate_range("2026-09-01", periods=len(path) * 5)
    return pd.Series([p for p in path for _ in range(5)], index=idx)

def test_levels_and_trigger_only_3x_20pct():
    L = _L(); today = dt.date(2026, 11, 10)
    h = {"UPRO": _hist([100, 120, 110, 95]), "TQQQ": _hist([50, 60, 55, 49])}   # UPRO 95 <= 96 stop; TQQQ 49 > 48
    S = tt.load.__call__() if False else {"pos": {}, "pending": [], "stopped": [], "exits": [], "last_week_checked": None}
    new = tt.check(L, S, h, 0.70, "2026-W45", today)
    assert S["pos"]["UPRO"]["stop_usd"] == 96.0 and S["pos"]["TQQQ"]["stop_usd"] == 48.0
    assert abs(S["pos"]["UPRO"]["stop_aud"] - 96 / 0.70) < 1e-3
    assert new == ["UPRO"] and S["pending"] == ["UPRO"]
    assert tt.check(L, S, h, 0.70, "2026-W45", today) == []   # once per week

def test_execute_needs_live_price_and_books_paper_sell():
    L = _L(); S = {"pos": {}, "pending": ["UPRO"], "stopped": [], "exits": []}
    def closed(s): raise RuntimeError("US market is closed")
    fills, lg = t.FILLS, t.log
    t.FILLS = os.path.join(tempfile.mkdtemp(), "f.csv"); t.log = lambda m: None
    try:
        assert tt.execute_pending(L, S, closed) == [] and L["holdings"]["UPRO"] == 100.0
        done = tt.execute_pending(L, S, lambda s: {"UPRO": {"px": 80.0, "utc": "2026-11-10T15:00:00Z", "kind": "iex_last_trade"}})
    finally: t.FILLS, t.log = fills, lg
    assert len(done) == 1 and done[0]["side"] == "SELL" and done[0]["paper_only"]
    assert "UPRO" not in L["holdings"] and S["stopped"] == ["UPRO"] and S["pending"] == []

def test_stopped_ticker_not_rebought(monkeypatch=None):
    import trend18_trail
    orig = trend18_trail.stopped_tickers
    trend18_trail.stopped_tickers = lambda path=None: {"UPRO"}
    try:
        L = {"holdings": {}, "cash": 1e6, "scale_current": 1.0}
        sig = {"sleeves": [{"inst": "UPRO", "kind": "etf", "notional_w": 0.5}]}
        px = {"UPRO": {"px": 100.0}, **{m: {"px": 30.0} for m in t.MF}}
        plan = t.plan_orders(sig, 100000, 1.0, L, px, None, no_add=[], mf_enabled=False)
        r = [x for x in plan["rows"] if x["inst"] == "UPRO"][0]
        assert r["order_qty"] == 0
    finally: trend18_trail.stopped_tickers = orig
