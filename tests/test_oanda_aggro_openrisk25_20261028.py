"""28 Oct 2026 13:22 WITA (Brett, PAPER): AGGRO account open-risk cap 15% -> 25%; other rails unchanged. No network."""
import oanda_aggro as A
def test_cap_25():
    c = A.cfg(); assert abs(c["open_risk_cap_pct"] - 0.25) < 1e-9
def test_cap_hard_rail_25():
    A.CFG["open_risk_cap_pct"], old = 0.9, A.CFG["open_risk_cap_pct"]
    try: assert A.cfg()["open_risk_cap_pct"] == 0.25
    finally: A.CFG["open_risk_cap_pct"] = old
def test_other_rails_unchanged():
    c = A.cfg()
    assert c["risk_pct"] == 0.03 and c["group_cap"] == {"METALS": 0.06, "OIL": 0.03, "GAS": 0.03} and c["usd_cap_pct"] == 0.09
    assert c["margin_cap_frac"] == 0.60 and c["weekly_halt_pct"] == -0.15 and c["kill_nav_frac"] == 0.60
def _rows(n): return [dict(ins="EUR_JPY", dir=1, risk_frac=0.03, owner="AGGRO") for _ in range(n)]
def test_caps_allows_24_blocks_27():
    c = dict(A.cfg(), usd_cap_pct=1.0)
    assert not (A.caps(_rows(7), "EUR_JPY", 1, 0.03, c) or "").startswith("skip:open_risk_cap")   # 24%
    assert (A.caps(_rows(8), "EUR_JPY", 1, 0.03, c) or "").startswith("skip:open_risk_cap")       # 27%
