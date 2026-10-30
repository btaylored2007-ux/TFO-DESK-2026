"""Brett 17 Oct 2026 item 2: NATGAS in AGGRO's OXB1 breakout only; GAS group cap 3%; shared CM6 untouched; rails intact."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import oanda_aggro as A, oanda_designs as D


def test_shared_universe_untouched():
    assert "NATGAS_USD" not in D.CM6 and "NATGAS_USD" not in D.universe(["OXB1", "OXP1", "OXT1"])
    assert A.AGGRO_OXB1_EXTRA == ["NATGAS_USD"]


def test_rails_unchanged():
    c = A.cfg()
    assert c["risk_pct"] <= 0.03 and c["open_risk_cap_pct"] <= 0.15 and c["weekly_halt_pct"] >= -0.15 and c["kill_nav_frac"] >= 0.60
    assert A.CFG["group_cap"]["GAS"] == 0.03


def test_gas_group_cap_one_bet():
    c = dict(A.cfg(), usd_cap_pct=1.0, open_risk_cap_pct=0.15)
    assert A.caps([], "NATGAS_USD", 1, 0.03, c) is None
    held = [{"ins": "NATGAS_USD", "dir": 1, "risk_frac": 0.03}]
    assert A.caps(held, "NATGAS_USD", 1, 0.03, c).startswith("skip:group_cap GAS")


def test_extra_signals_only_oxb1(monkeypatch):
    class I:  # stub indicator
        def __init__(self, cs): pass
    monkeypatch.setattr(A.X, "Ind", I); monkeypatch.setattr(A.X, "breakout", lambda I, n: 1)
    monkeypatch.setattr(A.X, "trigger_level", lambda I, n, d: 3.1)
    cs = [{"t": "2026-10-16T21:00:00Z"}] * (A.X.MIN_BARS + 2)
    out = A.extra_signals(["OXB1"], {"NATGAS_USD": cs})
    assert [(e["design"], e["ins"], e["dir"], e["trigger"]) for e in out] == [("OXB1", "NATGAS_USD", 1, 3.1)]
    assert A.extra_signals(["OXP1", "OXT1"], {"NATGAS_USD": cs}) == []


def test_never_touches_brett_index_positions():
    import oanda_sleeve as S
    assert {"61365", "61368"} <= {str(x) for x in S.PROTECTED_TRADE_IDS}
    assert not A.is_aggro({"id": "61365", "clientExtensions": {"tag": "AGGRO"}})
