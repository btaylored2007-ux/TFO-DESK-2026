import datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fundamental_true_return as T
U = dt.timezone.utc
def _book(events, eq):
    return {"equity": eq, "seed_usd": 1, "opened_utc": "2026-01-01T00:00:00Z", "updated": "2027-01-01T00:00:00Z", "capital_events": events}
def test_net_contributed_subtracts_outflows():
    b = _book([{"type": "injection", "amount_usd": 100000, "utc": "2026-02-01T00:00:00Z"},
               {"type": "capital_transfer", "usd": -200000, "utc": "2026-03-01T00:00:00Z"},
               {"type": "authorization", "usd": 0}], 420000)
    r = T.compute(b, [])
    assert r["net_contributed_usd"] == 400000 and r["true_pnl_usd"] == 20000
def test_repairs_are_not_flows():
    b = _book([{"type": "capital_identity_repair", "usd": -4466.15, "utc": "2026-02-01T00:00:00Z"}], 495533.85)
    assert T.compute(b, [])["true_pnl_usd"] == -4466.15
def test_twr_neutral_to_flow():
    t = [dt.datetime(2026, 1, d, tzinfo=U) for d in (1, 2, 3)]
    curve = [(t[0], 100.0), (t[1], 110.0), (t[2], 220.0)]
    assert abs(T.twr(curve, [(t[1] + dt.timedelta(hours=1), 110.0, "x", None)]) - 0.10) < 1e-9
def test_irr_simple():
    assert abs(T.irr([(0, -100), (1, 110)]) - 0.10) < 1e-6
def test_fx_fallback(monkeypatch):
    monkeypatch.setattr(T, "BASE", "/nonexistent")
    f = T.audusd(); assert f["fallback"] and f["rate"] == 0.7022
