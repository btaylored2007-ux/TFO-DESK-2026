"""Brett 17 Oct 2026 item 9: 30 Oct second-half scale-up only while the trend signal is on."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import trend18 as T


def sig(*trends):
    return {"asof": "2026-10-30", "sleeves": [{"name": "S%d" % i, "trend": t, "notional_w": 0.2 if t else 0.0} for i, t in enumerate(trends)]}


def test_scale_up_allowed_when_on():
    assert T.scale_up_guard(sig(0, 0.5), 1.0, 0.5) is None


def test_scale_up_refused_when_all_off():
    msg = T.scale_up_guard(sig(0, 0), 1.0, 0.5)
    assert msg and "SECOND-HALF GUARD" in msg and "--scale 0.50" in msg


def test_same_or_lower_scale_never_blocked():
    assert T.scale_up_guard(sig(0, 0), 0.5, 0.5) is None
    assert T.scale_up_guard(sig(0, 0), 0.3, 0.5) is None


def test_guard_wired_before_orders():
    src = open(T.__file__).read()
    assert src.index("_g = scale_up_guard(sig, scale") < src.index("plan = plan_orders(sig, base, scale, L, etf_px, btc, powder_avail=fi[\"avail\"], mf_powder=mf_powder, cap_room=fi[\"cap_room\"])\n    print(\"MF basket")
