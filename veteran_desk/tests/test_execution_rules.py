import datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import execution_rules as X
NOW = dt.datetime(2026, 10, 28, 10, 0, tzinfo=dt.timezone.utc)
BD = dict(updated_utc="2026-10-28T08:22:54+00:00", us=dict(names={"NVDA": dict(fee_pa=.0025, available=1000, ok=True), "MARA": dict(fee_pa=.25, available=50000, ok=True)}),
          asx=dict(names={"A2M": dict(fee_pa=.02, available=25000, ok=True)}))
def test_borrow():
    assert X.borrow_check("NVDA", 500, BD, NOW)["ok"]
    assert not X.borrow_check("NVDA", 5000, BD, NOW)["ok"]
    assert X.borrow_check("MARA", 100, BD, NOW)["htb"]
    assert X.borrow_check("A2M.AX", 100, BD, NOW)["ok"]
    assert not X.borrow_check("ZZZZ", 1, BD, NOW)["ok"]
    assert not X.borrow_check("NVDA", 1, BD, NOW + dt.timedelta(days=3))["ok"]   # stale
    assert not X.pre_trade("ZZZZ", "SHORT", 1, 10, NOW, BD)["ok"] and X.pre_trade("ZZZZ", "LONG", 1, 10, NOW, BD)["ok"]
    assert abs(X.borrow_cost(36000, .10, 36) - 360) < 1e-9
def test_futures():
    r = X.futures_contracts("CL=F", 80.0, 78.0, 5000); assert r["contracts"] == 2 and r["risk_usd"] == 4000 and r["notional_usd"] == 160000
    assert X.futures_margin("GC=F", 2000, 1) == 14000
    r = X.futures_contracts("ZW=F", 550, 540, 1000); assert r["contracts"] == 2
def test_fx():
    assert X.pip_size("USDJPY=X") == .01 and X.pip_size("AUDUSD=X") == .0001
    assert X.pip_value_usd("AUDUSD=X", .65) == 10.0
    assert abs(X.pip_value_usd("USDJPY=X", 150.0) - 1000 / 150) < 1e-9
    r = X.fx_units("AUDUSD=X", .6500, .6450, 1000); assert r["units"] == 200000 and r["risk_usd"] <= 1000
def test_kraken():
    assert X.kraken_fee(10000, True) == 25.0 and X.kraken_fee(10000, False) == 40.0
    assert abs(X.kraken_round_trip_pct() - .0065) < 1e-12
    assert X.perp_funding(100000, .0001, 8, "LONG") == 80.0 and X.perp_funding(100000, .0001, 8, "SHORT") == -80.0
    q = X.crypto_qty(100.0, 95.0, 1000); assert 0 < q and q * (5 + .25 + .38) <= 1000 + 1e-6
    assert not X.pre_trade("KRAKEN:XBTUSD", "SHORT", 1, 1)["ok"]
if __name__ == "__main__":
    for k, v in dict(globals()).items():
        if k.startswith("test_"): v(); print("PASS", k)
