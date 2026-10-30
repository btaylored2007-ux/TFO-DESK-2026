"""tfo_betacap_dyn_20261030: US beta cap = 60% of book (40% -> 50% -> 60%) equity x live AUDUSD (Brett 19:22 WITA 30 Oct 2026). PAPER only."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK, gate_v2 as G
from veteran_desk.tests.test_gate_v2 import HIST, ENV, CH, DA, asof, P0
def test_cap_is_60pct_equity_times_audusd():
    assert abs(BK.us_beta_cap_usd(dict(equity_aud=5494206.42, seats={}), 0.704) - 0.6 * 5494206.42 * 0.704) < 1e-6
    assert abs(BK.us_beta_cap_usd(dict(seats={"a": dict(equity_aud=1e6), "b": dict(equity_aud=2e6)}), 0.5) - 900000) < 1e-6
    assert "US_BETA_CAP_USD" not in dir(BK) and BK.US_BETA_CAP_PCT == 0.60
def test_fill_cap_scales_with_equity():
    pos = dict(id="AAA", ticker="AAA", side="LONG", fill=100.0, mark=100.0, shares=10000, venue="US", status="open")
    new = dict(id="N", ticker="NEW", side="LONG", shares=1000, venue="US", status="armed")
    small = dict(equity_aud=2e6, seats={"a": dict(positions={"AAA": pos})})   # cap 0.6*2m*0.7 = 840k < 1.1m
    big = dict(equity_aud=5e6, seats={"a": dict(positions={"AAA": pos})})     # cap 2.1m > 1.1m
    assert BK.us_beta_block(small, new, 100.0, au=0.7) and BK.us_beta_block(big, new, 100.0, au=0.7) is None
def _g(th):
    p = P0(); return G.gate(p, None, CH, DA, asof(), 0, hist=HIST, env=ENV, th=th), p
def test_gate_arm_counts_open_armed_staged_and_rejects():
    g0, p = _g(dict(us_beta_book=dict(equity_aud=5e6, seats={}), us_beta_staged=[], us_beta_cap_usd=1e12))
    if G.P.venue_of(p["ticker"]) != "US": return
    new = g0["us_beta_usd"]; assert new != 0
    book = dict(equity_aud=5e6, seats={"a": dict(positions={
        "O": dict(id="O", ticker="OOO", side="LONG", mark=100.0, shares=1000, venue="US", status="open"),
        "A": dict(id="A", ticker="AAR", side="LONG", entry=50.0, shares=1000, venue="US", status="armed")})})
    staged = [dict(id="S", ticker="SSS", side="LONG", entry=10.0, shares=1000, venue="US", book="veteran")]
    g1, _ = _g(dict(us_beta_book=book, us_beta_staged=staged, us_beta_cap_usd=abs(new) + 1e6))
    assert g1["history"]["us_beta"]["cur_usd"] == 160000 and g1["passed"] == g0["passed"]
    g2, _ = _g(dict(us_beta_book=book, us_beta_staged=staged, us_beta_cap_usd=abs(new) + 100000))
    assert not g2["passed"] and any("us_beta_cap" in r for r in g2["reasons"])
