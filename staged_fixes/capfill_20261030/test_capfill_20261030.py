import pytest
from veteran_desk import book as BK
@pytest.fixture(autouse=True)
def _real_caps(monkeypatch):   # conftest relaxes seat caps for legacy tests; this test checks the real 40%/75% rails
    monkeypatch.setattr(BK, "SEAT_NOTIONAL_CAP", 0.40); monkeypatch.setattr(BK, "SEAT_ETF_CAP", 0.75); monkeypatch.setattr(BK, "SEAT_MARGIN_CAP", 0.40)
def test_isrg_like_order_resized_to_40pct():
    p = dict(ticker="ISRG", side="LONG", shares=485, risk_aud=10000.0)
    note = BK.prefill_seat_cap(p, 436.0, 302000.0, 500000.0)
    assert note and "40pct" in note and p["shares"] == int(485 * 200000 / 302000) and p["risk_aud"] < 10000
def test_broad_etf_75pct_ok_and_within_cap_untouched():
    p = dict(ticker="SPY", side="LONG", shares=100); assert BK.prefill_seat_cap(p, 500, 350000.0, 500000.0) is None and p["shares"] == 100
    p = dict(ticker="AAPL", side="LONG", shares=10); assert BK.prefill_seat_cap(p, 200, 150000.0, 500000.0) is None
def test_rounds_to_zero_rejects():
    p = dict(ticker="BRK-A", side="LONG", shares=1); assert BK.prefill_seat_cap(p, 700000, 1000000.0, 500000.0) == "reject"
