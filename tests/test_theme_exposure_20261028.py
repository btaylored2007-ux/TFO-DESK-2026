"""theme_exposure (Brett 2026-10-28 12:17 WITA): warn-only theme cap + combined stress."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import theme_exposure as T

CFG = T.load_cfg.__globals__["DEFAULT_CFG"]
def _b(P, nav=1_000_000):
    import json; c = json.loads(json.dumps(CFG)); return T.build(cfg=c, positions=P, nav={"x": nav})

def test_cap_is_20pct_of_10pct_budget():
    d = _b([]); assert d["stress_budget_aud"] == 100_000 and d["theme_cap_aud"] == 20_000

def test_tqqq_leverage_ndx_shock_and_aud_fx():
    d = _b([T.pos("b", "TQQQ", 10_000, ccy="USD")]); t = d["themes"][0]
    assert t["lev_adj_aud"] == 30_000
    assert t["stress_loss_aud"] == round(-(10_000 * ((1 - 0.6) / 0.95 - 1)))   # 3x -20% then AUD -5% revalue

def test_aud_asset_no_fx_benefit_and_short_gains():
    d = _b([T.pos("b", "NAB.AX", 10_000), T.pos("b", "FLG", 10_000, "short")])
    other = [t for t in d["themes"] if t["theme"] == "other"][0]
    assert other["stress_loss_aud"] == round(1500 + (-10_000 * (1 - 0.85 / 0.95)))

def test_silver_up_hurts_short_and_crypto_btc():
    d = _b([T.pos("b", "XAG_USD", 10_000, "short"), T.pos("b", "XBT/USD", 10_000)])
    th = {t["theme"]: t for t in d["themes"]}
    assert th["metals"]["stress_loss_aud"] > 1500 and th["crypto"]["stress_loss_aud"] == round(-(10_000 * (0.7 / 0.95 - 1)))

def test_ai_lookthrough_sub_theme_not_in_total():
    d = _b([T.pos("b", "NVDA", 10_000), T.pos("b", "TQQQ", 10_000)])
    th = {t["theme"]: t for t in d["themes"]}
    assert th["ai_infra"]["lev_adj_aud"] == 10_000 + 9_000
    assert d["total_stress_loss_aud"] == sum(t["stress_loss_aud"] for t in d["themes"] if t["theme"] != "ai_infra")

def test_flags_and_worse_of_now_vs_staged():
    d = _b([T.pos("b", "TQQQ", 50_000), T.pos("b", "TQQQ", -20_000, staged=True)])
    t = d["themes"][0]
    assert t["current_pct_of_cap"] > 1 and t["proforma_pct_of_cap"] < t["current_pct_of_cap"] and t["flag"] == "RED"
    assert _b([])["themes"][0]["flag"] == "GREEN"

def test_warn_only_flags():
    # approve-all #5 (Brett 28 Oct 13:40): live cfg is now ENFORCE; warn mode keeps the old flags; never forces sales either way
    d = _b([]); assert d["paper_only"] and d["never_forces_sales"]
    assert d["warn_only"] == (d["mode"] == "warn") and d["never_blocks_or_resizes"] == (d["mode"] == "warn")

def test_module_has_no_order_or_gate_hooks():
    src = open(T.__file__).read()
    for bad in ("place_order", "requests.post", "funder_gate", "risk_caps", "oanda_risk"): assert bad not in src
