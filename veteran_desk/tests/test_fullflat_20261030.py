"""FULL FLAT / HARD BACKSTOP (2026-10-30 15:31 WITA) + exit Option C (tfo_exitC_20261030). PAPER only."""
import datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
import pytest
def P(side="LONG", fill=100.0, sh=100, **kw):
    p = dict(id="T", seat="energy", ticker="XYZ", side=side, fill=fill, initial_stop=90.0 if side == "LONG" else 110.0, stop=90.0 if side == "LONG" else 110.0, shares=sh, venue="US", cost_aud=0.0, target=130.0 if side == "LONG" else 70.0)
    p.update(kw); return p
@pytest.fixture(autouse=True)
def _m(monkeypatch):
    monkeypatch.setattr(BK, "net_open_aud", lambda p, px, au, marks=None: (1 if p["side"] == "LONG" else -1) * (px - p["fill"]) * p["shares"])
    monkeypatch.setattr(BK, "_notional_aud", lambda p, px, au, marks=None: px * p["shares"])
    monkeypatch.setattr(BK, "risk0_aud", lambda p, au, marks=None: 10.0 * p["shares"])
def test_defaults_option_c():
    assert BK.FULL_FLAT_PCT is None and BK.FULL_FLAT_R == 3.0 and BK.HARD_BACKSTOP_PCT == 0.05 and BK.RAIL_T2_R == 2.0
def test_full_flat_3R_long_short():
    assert BK.full_flat_trigger(P(), 129.0, 1.0) is None and BK.full_flat_trigger(P(), 130.0, 1.0) == "full_flat_3R"
    assert BK.full_flat_trigger(P("SHORT"), 71.0, 1.0) is None and BK.full_flat_trigger(P("SHORT"), 70.0, 1.0) == "full_flat_3R"
def test_old_pct_path_kept(monkeypatch):
    monkeypatch.setattr(BK, "FULL_FLAT_R", None); monkeypatch.setattr(BK, "FULL_FLAT_PCT", 0.05)
    assert BK.full_flat_trigger(P(), 105.0, 1.0) == "full_flat_5pct" and BK.full_flat_trigger(P("SHORT"), 95.0, 1.0) == "full_flat_5pct"
def test_backstop_long_short_and_hold():
    assert BK.full_flat_trigger(P(), 95.0, 1.0) == "hard_backstop_5pct" and BK.full_flat_trigger(P("SHORT"), 105.0, 1.0) == "hard_backstop_5pct"
    for side in ("LONG", "SHORT"):
        for px in (95.5, 100.0, 104.9, 115.0 if side == "LONG" else 85.0): assert BK.full_flat_trigger(P(side), px, 1.0) is None
    assert BK.full_flat_trigger(P(), None, 1.0) is None
def test_t2_trigger():
    assert BK.t2_trigger(P(), 125.0, 1.0) is None   # before t1
    p = P(sh=50, t1_done=True, shares_orig=100)
    assert BK.t2_trigger(p, 119.0, 1.0) is None and BK.t2_trigger(p, 120.0, 1.0) == "rail_2R_quarter_out" and BK.orig_shares(p) == 100
    p["t2_done"] = True; assert BK.t2_trigger(p, 125.0, 1.0) is None
    assert BK.t2_trigger(P("SHORT", sh=50, t1_done=True), 80.0, 1.0) == "rail_2R_quarter_out"
def test_manual_partials_set_t1_then_t2():
    t = dt.datetime(2026, 10, 30, 8, tzinfo=dt.timezone.utc); p = P(sh=50)
    BK.mark_half_banked(p, t, 110.0, 50, "seat_partial_close"); assert p["t1_done"] and not p.get("t2_done") and p["shares_orig"] == 100
    p["shares"] = 25; BK.mark_half_banked(p, t, 120.0, 25, "seat_partial_close"); assert p["t2_done"] and p["stop"] >= 100.0
def test_shadow_old5():
    assert BK.shadow_old5(P(), 105.0, 1.0) and not BK.shadow_old5(P(), 104.0, 1.0) and not BK.shadow_old5(P(shadow_old5_px=105.0), 106.0, 1.0)
def test_rule_logs():
    ids = {r.get("id") for r in BK.RULE_LOG}; assert {"tfo_fullflat_20261030_1531", "tfo_exitC_20261030"} <= ids
