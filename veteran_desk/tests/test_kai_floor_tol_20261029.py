"""floortol HT 2026-10-29 16:24 WITA: crypto seat accepts >= 0.98x of the 0.5% floor only when the shortfall is integer-unit rounding (floor_rounding_ok); genuine sub-floor rejected. PAPER."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_kai_seat_grade_20261029 as KS
def test_rounding_shortfall_accepted():
    p, ev = KS._arm("crypto", mult=0.5)   # 1% x0.5 = 0.5%; int units -> ~0.49x%
    assert p is not None and p.get("floor_note") is None   # tfo_review_20261030: fractional Kraken units hit the 0.5% floor exactly; 0.98 tolerance removed
    assert abs(p["risk_aud"] - .005 * 500000) <= 1
def test_genuine_0p4pct_rejected():
    p, ev = KS._arm("crypto", mult=0.4)   # 1% x0.4 = 0.4% -> genuinely below the floor
    assert p is None and ev[2] == "below_crypto_floor"
def test_normal_arm_has_no_floor_note():
    p, ev = KS._arm("crypto")
    assert p is not None and p.get("floor_note") is None and abs(p["risk_pct"] - .01) < 1e-9
