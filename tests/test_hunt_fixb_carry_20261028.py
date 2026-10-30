"""Brett 2026-10-28 20:59 WITA approved US hunt fix (b): no overnight short carry unless >=1R or stop at breakeven (PAPER)."""
import datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hunt48_short_paper as H
from test_hunt_fixes_20261028 import _t, _b, NOW
UTC = dt.timezone.utc
def test_flat_at_close_when_under_1R():
    t = _t(); ev = H.manage(t, [_b(19, 45, 9.7, 9.8, 9.6, 9.7)], NOW)   # 15:45 ET last bar, +0.3 < 1R(1.0)
    assert t["status"] == "CLOSED" and "eod_flat" in ev and t["open_qty"] == 0 and "overnight" in t["exit_reason"]
def test_carry_when_1R_profit():
    # Brett 2026-10-29 17:30: half at net >=1R (not T1). Bars need valid OHLC (low <= close for shorts).
    t = _t(shares=100); H.manage(t, [_b(19, 45, 9.1, 9.2, 8.95, 9.0)], NOW)   # low 8.95 = +1.05R gross -> 1R half, trail=entry -> carry
    assert t["status"] == "FILLED" and t["open_qty"] == 50 and t.get("half_rule") == "1R_net"
    t = _t(t1=8.0, shares=100); H.manage(t, [_b(19, 45, 9.1, 9.2, 8.95, 8.98)], NOW)  # +1.05R on low; T1=8.0 not the trigger
    assert t["status"] == "FILLED" and t["half_done"] and t["open_qty"] == 50 and t.get("half_rule") == "1R_net"
def test_carry_when_stop_at_breakeven():
    t = _t(trail=10.0, half_done=True, open_qty=50); H.manage(t, [_b(19, 45, 9.9, 9.95, 9.85, 9.9)], NOW)
    assert t["status"] == "FILLED"
def test_midday_bar_not_flattened_and_early_close():
    t = _t(); H.manage(t, [_b(17, 0, 9.9, 9.95, 9.85, 9.9)], NOW)
    assert t["status"] == "FILLED"
    assert H.is_session_last_bar(dt.datetime(2026, 11, 27, 18, 0, tzinfo=UTC))      # 13:00 ET early close (EST)
    assert not H.is_session_last_bar(dt.datetime(2026, 11, 26, 21, 0, tzinfo=UTC))  # Thanksgiving
    assert H.is_session_last_bar(dt.datetime(2026, 10, 27, 20, 0, tzinfo=UTC))
def test_short_trail_is_fee_floor_after_1r_half():
    # amend 17:37: after the 1R-net half the short trail sits BELOW entry by RT cost/share (fee floor), not at entry
    t = _t(shares=100); H.manage(t, [_b(19, 45, 9.1, 9.2, 8.95, 9.0)], NOW)
    assert t.get("stop_rule") == "fee_floor_after_half" and t["trail"] < t["entry"]
