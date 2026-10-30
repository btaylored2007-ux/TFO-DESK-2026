"""Brett 28 Oct 14:57 WITA: the 30 Oct manual second-half deploy must not double-fire as the 2 Nov monthly; Kraken fail-closed."""
import os, sys, datetime as dt, types, pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import trend18 as T

def test_2nov_monthly_blocked_after_30oct_manual():
    L = {"rebalances": [{"session": "2026-10-30", "reason": "manual"}]}
    assert T.first_session_of_month(dt.date(2026, 11, 2)) == dt.date(2026, 11, 2)
    assert T.recent_full_rebalance(L, dt.date(2026, 11, 2))["session"] == "2026-10-30"

def test_monthly_ok_without_recent_manual():
    L = {"rebalances": [{"session": "2026-10-05", "reason": "entry"}, {"session": "2026-10-30", "reason": "refresh"}]}
    assert T.recent_full_rebalance(L, dt.date(2026, 11, 2)) is None
    assert T.recent_full_rebalance({"rebalances": [{"session": "2026-10-30", "reason": "manual"}]}, dt.date(2026, 12, 1)) is None

def test_execute_refuses_monthly_2nov(monkeypatch):
    class FakeNow(dt.datetime):
        @classmethod
        def now(cls, tz=None): return dt.datetime(2026, 11, 2, 20, 50, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(T, "now_utc", lambda: dt.datetime(2026, 11, 2, 20, 50, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(T, "cmd_mark", lambda *a, **k: None)
    monkeypatch.setattr(T, "load_ledger", lambda: {"rebalances": [{"session": "2026-10-30", "reason": "manual"}], "kill": {}})
    a = types.SimpleNamespace(reason="monthly", force=False, scale=1.0, signal_asof=None)
    with pytest.raises(SystemExit, match="monthly refused"): T.cmd_execute(a)

def test_kraken_missing_fails_closed(monkeypatch):
    monkeypatch.setattr(T, "now_utc", lambda: dt.datetime(2026, 10, 30, 19, 50, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(T, "cmd_mark", lambda *a, **k: None)
    monkeypatch.setattr(T, "load_ledger", lambda: {"rebalances": [], "kill": {}, "holdings": {}, "scale_current": 0.5})
    monkeypatch.setattr(T, "compute_signals", lambda asof, s: {"asof": asof, "sleeves": []})
    monkeypatch.setattr(T, "scale_up_guard", lambda *a: None)
    monkeypatch.setattr(T, "get_prices", lambda *a, **k: ({s: {"px": 1.0} for s in T.TREND_ETFS + T.MF}, "test"))
    def boom(*a, **k): raise RuntimeError("kraken down")
    monkeypatch.setattr(T, "kraken_btc", boom)
    a = types.SimpleNamespace(reason="manual", force=False, scale=1.0, signal_asof=None)
    with pytest.raises(SystemExit, match="fail closed"): T.cmd_execute(a)
