"""2026-10-29: fixed +3%/-8% levels still need room_R >= 1.0R (RULE_LOG 27 Oct)."""
import hunt48_room as HR

def test_fixed_pct_under_1r_is_bench():
    # No opposing structure: room = 0.375R - cost << 1.0R
    bars = [dict(h=50 + i * 0.1, l=49 + i * 0.1, c=49.5 + i * 0.1) for i in range(250)]
    t = dict(direction="long", max_fill=100.0, stop=92.0, t2=103.0, cost_R=0.05)
    why = HR.room(t, bars)
    assert why and ("1.0R" in why or "room" in why), why
    assert t["room_R"] < 1.0
    assert abs(t["room_t2_R"] - 0.375) < 0.01

def test_is_fixed_detects_3_8():
    assert HR.is_fixed(100, 92, 103)
