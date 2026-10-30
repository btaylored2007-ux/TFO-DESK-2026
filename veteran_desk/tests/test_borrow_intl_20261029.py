"""borrow (TFO 19:31 WITA 29 Oct 2026): conservative estimated non-US short borrow rows; unverified -> blocked. PAPER."""
import datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import intl_borrow as IB, execution_rules as X, book as BK
N = dt.datetime(2026, 10, 29, 11, 30, tzinfo=dt.timezone.utc)
D = dict(updated_utc="2026-10-29T11:00:00+00:00", markets=dict(spain=dict(names={"SLR": dict(fee_pa=0.03, fee_pa_ibkr=0.0073, available=200000, ok=True, estimated=True)})))
def test_located_min_fee_estimated():
    b = IB.check("SLR.MC", 7418, data=D, now=N); assert b["ok"] and b["fee_pa"] == 0.03 and b["estimated"] and b["min_charge_days"] == 3
def test_blocks():
    assert not IB.check("SLR.MC", 300000, data=D, now=N)["ok"]                       # not enough shares
    assert not IB.check("SAN.MC", 10, data=D, now=N)["ok"]                           # no row
    assert not IB.check("ZEAL.CO", 10, data=D, now=N)["ok"]                          # no source
    assert not IB.check("SLR.MC", 10, data=D, now=N + dt.timedelta(hours=30))["ok"]  # stale
def test_pre_trade_routes_intl():
    r = X.pre_trade("SLR.MC", "SHORT", 7418, 15.1, now=N, borrow_data=D); assert r["ok"] and r["borrow_estimated"]
    assert X.pre_trade("SLR.MC", "LONG", 7418, 15.1)["ok"]
def test_rule_log(): assert any(r.get("id") == "tfo_borrow_intl_20261029_1931" for r in BK.RULE_LOG)
