"""Brett 28 Oct 2026 13:15 WITA hunt fix 3: touch fill capped at max_fill, gap-through at open within band, max_fill = trigger + 0.4 ATR (config)."""
import datetime as dt, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cal_paper_bench as CB, event_calendar as EC, hunt_shape_trial as H
UTC = dt.timezone.utc; A = dt.datetime(2026, 11, 2, 0, 0, tzinfo=UTC)
def tr(direction="long", trig=10.0, mf=10.4):
    return dict(direction=direction, status="ARMED", armed_utc=A.isoformat(), trigger=trig, max_fill=mf, stop=9.0 if direction == "long" else 11.0, t1=11, t2=11, shares=100, est_cost_usd=0)
def bar(h, o, hi, lo, c): return dict(t=A + dt.timedelta(hours=h), o=o, h=hi, l=lo, c=c)

def test_touch_fills_at_trigger_even_if_close_back_below():
    t = tr(); CB.step(t, [bar(1, 9.8, 10.1, 9.7, 9.9)], A + dt.timedelta(hours=2), 3, rule="touch")
    assert t["status"] == "FILLED" and t["fill"] == 10.0 and t["fill_rule"] == "touch"

def test_old_close_rule_unchanged_for_calendar():
    t = tr(); CB.step(t, [bar(1, 9.8, 10.1, 9.7, 9.9)], A + dt.timedelta(hours=2), 3)
    assert t["status"] == "ARMED"

def test_gap_through_within_band_fills_at_open_beyond_band_missed():
    t = tr(); CB.step(t, [bar(1, 10.3, 10.5, 10.2, 10.4)], A + dt.timedelta(hours=2), 3, rule="touch"); assert t["status"] == "FILLED" and t["fill"] == 10.3
    t = tr(); CB.step(t, [bar(1, 10.6, 10.7, 10.5, 10.6)], A + dt.timedelta(hours=2), 3, rule="touch"); assert t["status"] == "MISSED"

def test_short_touch():
    t = tr("short", 10.0, 9.6); CB.step(t, [bar(1, 10.2, 10.3, 9.95, 10.1)], A + dt.timedelta(hours=2), 3, rule="touch"); assert t["fill"] == 10.0
    t = tr("short", 10.0, 9.6); CB.step(t, [bar(1, 9.7, 9.8, 9.5, 9.6)], A + dt.timedelta(hours=2), 3, rule="touch"); assert t["fill"] == 9.7

def test_config_default_and_band_in_stage_levels(tmp_path):
    assert CB.hunt_fill_rule(str(tmp_path)) == dict(rule="touch", max_fill_atr=0.4)
    r = dict(sym="X.AX", px=10.0, atr=0.5, hi20=10.0, pdh=10.0, lo10=9, pdl=9, venue="asx", adv_usd=1e8)
    lv = EC.stage_levels(r, "long", 2500, 0.7, fixed_pct=(0.03, 0.08), maxfill_atr=0.4)
    assert abs(lv["max_fill"] - (lv["trigger"] + 0.2)) < 1e-3 and abs(lv["stop"] - lv["max_fill"] * 0.92) < 1e-3
    assert EC.stage_levels(r, "long", 2500, 0.7, fixed_pct=(0.03, 0.08))["max_fill"] == round(lv["trigger"] * 1.0015, 4)

def test_trial_shadow_fill_uses_same_touch_rule_both_arms():
    t = dict(direction="long", trigger=10.0, max_fill=10.4, armed_utc=A.isoformat())
    assert H.shadow_fill(t, [bar(1, 10.3, 10.5, 10.2, 10.4)], A + dt.timedelta(hours=24))[0] == 10.3
    assert H.shadow_fill(t, [bar(1, 10.6, 10.7, 10.5, 10.6)], A + dt.timedelta(hours=24)) is None
