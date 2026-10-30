import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import value_deploy as V
EQ = 500_000.0; SLOT = V.SLOT_PCT * EQ
def q(ex=True): return {"executable": ex, "px_usd": 10.0, "px": 10.0, "fx_usd": 1.0, "ccy": "USD"}
def rows(n, sec="Energy"): return [{"symbol": "S%d" % i, "sector": sec} for i in range(n)]
ok = lambda *a: (True, "ok")
def test_constants():
    assert (V.SLOT_PCT, V.CASH_RESERVE_PCT, V.MAX_OPEN, V.MAX_PER_SECTOR, V.MAX_PCT_ADV, V.STOP_PCT) == (0.065, 0.05, 15, 3, 0.20, 0.15)
    assert "yf.Ticker(s).fast_info" not in open(V.__file__).read()
def test_sector_cap_counts_holdings():
    led = {"positions": {"H": {}}}
    r = rows(5); Q = {x["symbol"]: q() for x in r}; L = {x["symbol"]: (1e9, .02) for x in r}
    o, sk = V.plan(led, r, Q, L, ok, SLOT, 5 * SLOT, {"Energy": 2}, 5)
    assert len(o) == 1
def test_no_live_quote_no_fill_and_rescale():
    r = rows(3, "X") + [{"symbol": "Y", "sector": "Y"}]
    Q = {"S0": q(), "S1": q(False), "S2": q(), "Y": q()}; L = {k: (1e9, .02) for k in Q}
    o, sk = V.plan({"positions": {}}, r, Q, L, ok, SLOT, 3 * SLOT * 0.9, {}, 3)
    assert [x["sym"] for x in o] == ["S0", "S2", "Y"] and any(s == "S1" for s, _ in sk)
    assert all(abs(x["usd"] - 0.9 * SLOT) < 1 for x in o)
def test_rescale_when_excluded_capped_at_7pct():
    r = rows(2, "A"); Q = {x["symbol"]: q() for x in r}; L = {x["symbol"]: (1e9, .02) for x in r}
    o, _ = V.plan({"positions": {}}, r, Q, L, ok, SLOT, 10 * SLOT, {}, 2)
    assert all(abs(x["usd"] - V.SLOT_MAX_PCT * EQ) < 1 for x in o)
def test_liquidity_20pct_adv_and_cost():
    r = rows(1); Q = {"S0": q()}; L = {"S0": (100_000.0, .04)}
    o, _ = V.plan({"positions": {}}, r, Q, L, ok, SLOT, SLOT, {}, 1)
    assert abs(o[0]["usd"] - 20_000) < 1 and abs(o[0]["pct_adv"] - 0.2) < 1e-9
    assert o[0]["cost_bps"] > 10 + 75 + 170   # 4% sigma * sqrt(0.2) ~ 179bp impact
def test_theme_cap_blocks():
    r = rows(1); Q = {"S0": q()}; L = {"S0": (1e9, .02)}
    o, sk = V.plan({"positions": {}}, r, Q, L, lambda *a: (False, "theme cap"), SLOT, SLOT, {}, 1)
    assert not o and sk[0][1] == "theme cap"
