"""28 Oct 2026 Brett B1: Trend18 BTC stop rail LIVE (paper), same rule as trend_core_shadow BTC_STOP (weekly 20% trail)."""
import datetime as dt, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import trend18 as t, trend18_trail as tt
def _L():
    return {"holdings": {"XBT/USD": 1.0}, "entry_px": {"XBT/USD": 100000.0}, "mark_px_usd": {"XBT/USD": 79000.0}, "mark_utc": {}, "mark_source": {},
            "cash": 0.0, "fills": [{"inst": "XBT/USD", "side": "BUY", "utc": "2026-09-01T00:00:00Z"}]}
def _hist(path):
    idx = pd.date_range("2026-09-01", periods=len(path) * 7, freq="D"); return pd.Series([p for p in path for _ in range(7)], index=idx)
def test_btc_trigger():
    S = {"pos": {}, "pending": [], "stopped": [], "exits": [], "last_week_checked": None}
    new = tt.check(_L(), S, {"XBT/USD": _hist([100000, 110000, 87000])}, 0.70, "2026-W45", dt.date(2026, 11, 10))
    assert S["pos"]["XBT/USD"]["stop_usd"] == 88000.0 and new == ["XBT/USD"]
def test_btc_exec_live_bid_only():
    L = _L(); S = {"pos": {}, "pending": ["XBT/USD"], "stopped": [], "exits": []}
    fills, lg = t.FILLS, t.log; t.FILLS = os.path.join(tempfile.mkdtemp(), "f.csv"); t.log = lambda m: None
    try:
        assert tt.execute_pending(L, S, lambda s: {}, lambda: {"tape": "STALE", "bid": 1}) == []
        done = tt.execute_pending(L, S, lambda s: {}, lambda: {"tape": "LIVE", "bid": 80000.0, "utc": "2026-11-10T01:00:00Z"})
    finally: t.FILLS, t.log = fills, lg
    assert len(done) == 1 and done[0]["side"] == "SELL" and S["stopped"] == ["XBT/USD"] and not L["holdings"].get("XBT/USD")
