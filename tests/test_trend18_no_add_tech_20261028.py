"""Brett tech-risk cut 28 Oct 2026: 30 Oct second-half scale-up adds no UPRO/TQQQ; no forced sale; other legs <= normal target."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import trend18 as T

SIG = {"asof": "2026-10-29", "sleeves": [
    {"name": "SPX", "inst": "UPRO", "kind": "etf", "notional_w": 0.15, "trend": 1},
    {"name": "NDX", "inst": "TQQQ", "kind": "etf", "notional_w": 0.15, "trend": 1},
    {"name": "BOND_L", "inst": "TMF", "kind": "etf", "notional_w": 0.0, "trend": 0},
    {"name": "GOLD", "inst": "UGL", "kind": "etf", "notional_w": 0.20, "trend": 1},
    {"name": "BTC", "inst": "XBT/USD", "kind": "crypto", "notional_w": 0.05, "trend": 1}]}
PX = {k: {"px": 100.0} for k in ["UPRO", "TQQQ", "TMF", "UGL", "DBMF", "KMLM", "CTA"]}
BTC = {"ask": 100000.0, "bid": 100000.0}

def L(): return {"scale_current": 0.5, "cash": 0.0, "holdings": {"UPRO": 750, "TQQQ": 750, "UGL": 1000, "XBT/USD": 0.25}}

def plan(scale, cfg, tmp_path, monkeypatch):
    p = tmp_path / "cfg.json"; p.write_text(json.dumps(cfg)); monkeypatch.setattr(T, "NO_ADD_CFG", str(p))
    return {r["inst"]: r for r in T.plan_orders(SIG, 1_000_000, scale, L(), PX, BTC, powder_avail=1e6, mf_powder=True)["rows"]}

ON = {"enabled": True, "no_add_tickers": ["UPRO", "TQQQ"], "applies_to": "second_half_scale_up"}

def test_second_half_adds_no_tech(tmp_path, monkeypatch):
    r = plan(1.0, ON, tmp_path, monkeypatch)
    for t in ("UPRO", "TQQQ"):
        assert r[t]["order_qty"] == 0 and r[t]["side"] == "-" and r[t]["target_qty"] == 750 and r[t]["no_add"]
    assert r["UGL"]["target_usd"] == 200000 and r["XBT/USD"]["target_usd"] == 50000   # normal target, not above
    assert r["TMF"]["target_qty"] == 0                                                    # gate off -> nothing
    mf = sum(r[m]["target_usd"] for m in T.MF)
    assert mf <= T.mf_cap_usd(SIG, 1_000_000, 1.0) + 1                                  # MF never above its normal cap

def test_disabled_or_not_scale_up_is_normal(tmp_path, monkeypatch):
    r = plan(1.0, dict(ON, enabled=False), tmp_path, monkeypatch)
    assert r["UPRO"]["target_qty"] == 1500 and r["UPRO"]["side"] == "BUY"
    r = plan(0.5, ON, tmp_path, monkeypatch)          # same-scale run (refresh/monthly): rule not applied
    assert r["TQQQ"]["target_qty"] == 750 and "no_add" not in r["TQQQ"]

def test_no_forced_sale(tmp_path, monkeypatch):
    s = json.loads(json.dumps(SIG)); s["sleeves"][0]["notional_w"] = 0.15
    r = plan(1.0, ON, tmp_path, monkeypatch)
    assert r["UPRO"]["side"] != "SELL" and r["TQQQ"]["side"] != "SELL"

def test_live_config_present():
    c = json.load(open(T.NO_ADD_CFG)); assert c["enabled"] and set(c["no_add_tickers"]) == {"UPRO", "TQQQ"}
