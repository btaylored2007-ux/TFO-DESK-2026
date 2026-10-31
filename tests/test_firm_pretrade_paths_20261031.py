"""OANDA, Asia and ASX hunt entry paths call the shared pre-trade gate. Mocks only."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import firm_pretrade
from veteran_desk.exec_speed import firm_risk_service as R


def _down():
    return {"reachable": False, "ok": False, "reason": "firm_risk_service_unreachable",
            "detail": "stale", "exits_allowed": True, "paper_only": True}


def test_oanda_aggro_place_refuses_when_down(monkeypatch):
    import oanda_aggro as A
    import oanda_sleeve as S
    monkeypatch.setattr(R, "QUERY_FN", lambda **k: _down())
    monkeypatch.setattr(S.RG, "entries_blocked", lambda *a, **k: False)
    monkeypatch.setattr(S, "summary", lambda: {"NAV": "100000", "marginUsed": "0", "marginAvailable": "100000"})
    monkeypatch.setattr(S, "open_trades", lambda: [])
    monkeypatch.setattr(S, "pricing", lambda ins: {ins[0]: {"bid": 1.0, "ask": 1.0001, "mid": 1.00005, "sp": 1e-5,
                                                           "tradeable": True, "q2h": {"positiveUnits": "1"}}})
    monkeypatch.setattr(S, "quote_health", lambda *a, **k: None)
    monkeypatch.setattr(S, "spread_limit", lambda *a, **k: 1.0)
    monkeypatch.setattr(S, "behaviour_ok", lambda *a, **k: True)
    monkeypatch.setattr(S, "meta", lambda ins: {"displayPrecision": 5, "tradeUnitsPrecision": 0,
                                                "minimumTradeSize": 1, "marginRate": 0.02,
                                                "financing": {"longRate": "0.01", "shortRate": "-0.01"}})
    monkeypatch.setattr(S, "margin_needed", lambda *a, **k: 10.0)
    monkeypatch.setattr(A, "real_margin_room", lambda *a, **k: 1e9)
    monkeypatch.setattr(A, "log", lambda rec: None)
    e = dict(ins="EUR_USD", dir=1, design="OXB1", stop_dist=0.01, floor_sp=1e-5, reason="t", signal_bar="2026-10-31")
    assert str(A.place(e, A.cfg(), False)).startswith("skip:firm_risk:")


def test_oanda_lab_place_entry_refuses_when_down(monkeypatch):
    import oanda_sleeve as S
    monkeypatch.setattr(R, "QUERY_FN", lambda **k: _down())
    monkeypatch.setattr(S.RG, "entries_blocked", lambda *a, **k: False)
    monkeypatch.setattr(S, "aggro_active", lambda *a, **k: False)
    monkeypatch.setattr(S, "entry_disabled", lambda *a, **k: [])
    monkeypatch.setattr(S, "summary", lambda: {"NAV": "100000", "marginUsed": "0", "marginAvailable": "100000"})
    monkeypatch.setattr(S, "open_trades", lambda: [])
    monkeypatch.setattr(S, "pricing", lambda ins: {i: {"bid": 1.0, "ask": 1.0001, "mid": 1.00005, "sp": 1e-5,
                                                       "tradeable": True, "q2h": {"positiveUnits": "1"}} for i in ins})
    monkeypatch.setattr(S, "quote_health", lambda *a, **k: None)
    monkeypatch.setattr(S, "behaviour_ok", lambda *a, **k: True)
    monkeypatch.setattr(S, "spread_limit", lambda *a, **k: 1.0)
    monkeypatch.setattr(S, "meta", lambda ins: {"displayPrecision": 5, "tradeUnitsPrecision": 0,
                                                "minimumTradeSize": 1, "marginRate": 0.02, "pipLocation": -4,
                                                "financing": {"longRate": "0.01", "shortRate": "-0.01"}})
    monkeypatch.setattr(S, "log", lambda rec: None)
    e = dict(design="OXF1", ins="EUR_USD", dir=1, score=1.0, risk_pct=0.005, reason="t",
             stop_dist=0.01, floor_sp=1e-5, close=1.0)
    got = S.place_entry(e, False)
    assert str(got).startswith("skip:firm_risk:"), got


def test_asia_and_asx_arm_helpers_refuse(monkeypatch):
    monkeypatch.setattr(R, "QUERY_FN", lambda **k: _down())
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("asia_hunt48.py", "asx_hunt48.py"):
        src = open(os.path.join(root, name)).read()
        assert "def _firm_block" in src and "firm_pretrade.blocked" in src
        assert 'status="ARMED"' in src or 't["status"] = "ARMED"' in src
    # asx imports event_calendar, which needs a local SEC contact file absent from this checkout.
    try:
        import asx_hunt48 as AX
        assert AX._firm_block(dict(sym="BHP.AX", direction="long", stop=40, max_fill=42, shares=10))
    except FileNotFoundError as exc:
        assert "sec_contact" in str(exc)
    try:
        import asia_hunt48 as AH
        assert AH._firm_block(dict(sym="7203.T", direction="long", stop=90, max_fill=100, shares=10), "jp")
    except FileNotFoundError as exc:
        assert "sec_contact" in str(exc)
