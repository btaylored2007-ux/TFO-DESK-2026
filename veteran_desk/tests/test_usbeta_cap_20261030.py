"""US BETA CAP (tfo_usbeta_cap_20261030). PAPER only."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
def Q(tk, side="LONG", px=100.0, sh=1000, venue="US", status="open"):
    return dict(id=tk, ticker=tk, side=side, fill=px, mark=px, shares=sh, venue=venue, status=status)
def D(*ps): return dict(equity_aud=750000 / BK.US_BETA_CAP_PCT / 0.75, seats={"a": dict(positions={p["id"]: p for p in ps})})
def test_beta_math_long_short_levered_nonus():
    d = D(Q("AAA"), Q("BBB", side="SHORT", sh=400), Q("TQQQ", sh=100), Q("BHP.AX", venue="ASX"), Q("ZZZ", status="armed"))
    assert BK.us_beta_usd(d) == 100000 - 40000 + 30000
def test_blocks_new_long_over_cap():
    d = D(Q("AAA", sh=7000)); assert BK.us_beta_block(d, Q("NEW", sh=600, status="armed"), 100.0, au=0.75) and BK.us_beta_block(d, Q("NEW", sh=400, status="armed"), 100.0, au=0.75) is None
def test_levered_counts_at_multiple():
    d = D(Q("AAA", sh=7000)); assert BK.us_beta_block(d, Q("SOXL", sh=200, status="armed"), 100.0, au=0.75)   # 20k x3 = 60k -> 760k
def test_reducing_short_allowed_and_non_us_ignored():
    d = D(Q("AAA", sh=8000)); assert BK.us_beta_block(d, Q("NEW", side="SHORT", sh=500, status="armed"), 100.0, au=0.75) is None
    assert BK.us_beta_block(d, Q("BHP.AX", venue="ASX", sh=99999, status="armed"), 100.0, au=0.75) is None
def test_rule_log(): assert any(r.get("id") == "tfo_usbeta_cap_20261030" for r in BK.RULE_LOG)
