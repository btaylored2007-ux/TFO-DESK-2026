import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import theme_exposure as T
def _d(cur, pro, cap=100.0):
    th = [dict(theme=k, current_stress_loss_aud=0, proforma_stress_loss_aud=0) for k in T.THEMES]
    for t in th:
        if t["theme"] == "other": t["current_stress_loss_aud"], t["proforma_stress_loss_aud"] = cur, pro
    return dict(theme_cap_aud=cap, themes=th)
def test_staged_does_not_block():
    cfg = T.load_cfg(); cfg["mode"] = "enforce"
    ok, why = T.check_buy("XYZ", 10, ccy="AUD", d=_d(20, 500), cfg=cfg); assert ok, why
def test_holdings_over_cap_blocks():
    cfg = T.load_cfg(); cfg["mode"] = "enforce"
    ok, _ = T.check_buy("XYZ", 10, ccy="AUD", d=_d(120, 0), cfg=cfg); assert not ok
def test_flag_on_holdings():
    p = [T.pos("A", "XYZ", 1000, ccy="AUD", staged=True)]
    d = T.build(cfg=dict(T.load_cfg(), firm_nav_aud_override=1000), positions=p, nav={})
    o = [t for t in d["themes"] if t["theme"] == "other"][0]
    assert o["flag"] == "GREEN" and o["staged_info_pct_of_cap"] > 1 and "other" not in d["blocks_new_buys_in"]
