"""venuestale (TFO 19:31 WITA 29 Oct 2026): fill staleness limit = venue feed delay + 5 min (LSE 25); real-time venues 15. PAPER."""
import datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
from veteran_desk.tests.test_stalefill_20261029 import _arm, _run
U = dt.timezone.utc
T = dt.datetime(2026, 10, 29, 11, 30, tzinfo=U)   # 11:30 London, LSE open
def _bar(age): return dict(last=1000.0, hi=1002.0, lo=998.0, asof=(T - dt.timedelta(minutes=age)).isoformat())
def test_limits():
    assert BK.stale_limit_min("LSE") == 25 and BK.stale_limit_min("NORDIC") == 15 and BK.stale_limit_min("US") == 15 and BK.stale_limit_min("CRYPTO") == 15
def test_lse_20min_old_fills_and_logs():   # the SMT.L case
    p = _run(_arm("SMT.L", "LSE", 1000, 960, 1100, shares=5), "macro", T, _bar(20))
    assert p["status"] == "open" and p["venue_delay_min"] == 20 and p["stale_limit_min"] == 25 and 19.5 < p["fill_px_age_min"] < 20.5
def test_lse_over_25_no_fill():
    p = _run(_arm("SMT.L", "LSE", 1000, 960, 1100, shares=5), "macro", T, _bar(27))
    assert p["status"] == "armed" and "stale" in p["exec_note"]
def test_realtime_venue_still_15():
    p = _run(_arm("NIBE-B.ST", "NORDIC", 1000, 960, 1100, shares=5), "macro", T, _bar(20))
    assert p["status"] == "armed" and "stale" in p["exec_note"]
def test_rule_log(): assert any(r.get("id") == "tfo_venuestale_20261029_1931" for r in BK.RULE_LOG)
