import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import value_deploy as vd, value_rank as vr
def _q(): return {"px_usd": 1.0, "px": 1.0, "fx_usd": 1.0, "executable": True, "age_sec": 1}
def test_basic_materials_cap_two_incl_holdings():
    ranked = [{"symbol": "M%d.AX" % i, "sector": "Basic Materials"} for i in range(4)] + [{"symbol": "E.AX", "sector": "Energy"}]
    Q = {r["symbol"]: _q() for r in ranked}; L = {r["symbol"]: (50e6, 0.02) for r in ranked}
    o, sk = vd.plan({"positions": {}}, ranked, Q, L, lambda *a: (True, ""), 65000, 500000, {}, 10)
    assert [x["sym"] for x in o if x["sector"] == "Basic Materials"] == ["M0.AX", "M1.AX"]
    o, sk = vd.plan({"positions": {}}, ranked, Q, L, lambda *a: (True, ""), 65000, 500000, {"Basic Materials": 1}, 10)
    assert [x["sym"] for x in o if x["sector"] == "Basic Materials"] == ["M0.AX"]
    assert vd.sector_cap("Energy") == 3
def test_preferred_bonus_and_rank_file():
    assert vr.PREFERRED_BONUS == 0.15
    R = json.load(open(os.path.join(vd.BASE, "reports", "value_rank.json")))
    assert R["floors"]["preferred_bonus"] == 0.15
    for r in R["ranked"]:
        assert r["sector_bonus"] == (0.15 if r.get("sector") in vr.PREFERRED_SECTORS else 0.0)
