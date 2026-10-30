"""2026-10-29 krakenfx: Kraken crypto live price mapping (Yahoo primary, Kraken DoH backup) + FX quote-ccy sizing (USDKRW etc)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import pytest
from veteran_desk import book as BK
def test_kraken_pair_mapping():
    assert BK.kraken_pair("KRAKEN:XMRUSD") == ("XMRUSD", "XMR", "USD")
    assert BK.kraken_pair("KRAKEN:XBTUSD")[1] == "XBT"
def test_crypto_yahoo_primary_symbol():
    seen = []
    m = BK.crypto_live_px("KRAKEN:XBTUSD", yahoo_fn=lambda s: seen.append(s) or dict(last=80000.0), kraken_fn=lambda s: None)
    assert seen == ["BTC-USD"] and m["last"] == 80000.0
def test_crypto_kraken_backup_when_yahoo_empty():
    m = BK.crypto_live_px("KRAKEN:XMRUSD", yahoo_fn=lambda s: None, kraken_fn=lambda s: dict(last=589.6, src="kraken_rest_doh:XXMRZUSD"))
    assert m["last"] == 589.6
def test_kraken_ticker_key_differs_from_alt():
    J = {"error": [], "result": {"XXMRZUSD": {"c": ["589.58", "1"], "h": ["597", "598"], "l": ["582", "581"]}}}
    m = BK.kraken_live_px("KRAKEN:XMRUSD", get=lambda u: J)
    assert m["last"] == 589.58 and m["src"].endswith("XXMRZUSD")
def test_live_px_never_sends_kraken_prefix_to_yfinance(monkeypatch):
    monkeypatch.setattr(BK, "crypto_live_px", lambda s: dict(last=1.0, src="x"))
    assert BK.live_px("KRAKEN:XMRUSD")["last"] == 1.0
@pytest.mark.parametrize("pair,usdq,units", [("USDKRW=X", 1400.0, 1400.0), ("USDJPY=X", 150.0, 150.0), ("USDINR=X", 84.0, 84.0), ("USDCNH=X", 7.1, 7.1), ("USDMXN=X", 19.0, 19.0)])
def test_fx_quote_ccy_aud_per_unit(pair, usdq, units):
    au = 0.65; px = lambda t: dict(last=usdq)
    assert BK.aud_per_unit(pair, au, px_fn=px) == pytest.approx(1.0 / (au * units))
def test_usdkrw_theo_case_risk():
    au = 0.65; px = lambda t: dict(last=1400.0)
    per = BK.aud_per_unit("USDKRW=X", au, px_fn=px) * 13.2
    assert 536 * per == pytest.approx(7075.2 / (0.65 * 1400), rel=1e-3)   # ~A$7.8, not A$10,064
    q = int(BK.risk_plan(500000.0, "B")["risk_aud"] / per)
    assert q * per == pytest.approx(10000, rel=1e-3)   # sized to the 2% rail in AUD (non-A; tfo_review_fix_20261031)
def test_usd_quoted_fx_unchanged():
    assert BK.aud_per_unit("EURUSD=X", 0.65) == pytest.approx(1 / 0.65)
def test_fx_no_rate_fails_closed():
    with pytest.raises(BK.FxUnavailable): BK.aud_per_unit("USDKRW=X", 0.65, px_fn=lambda t: None)
def test_crypto_yahoo_collision_uses_kraken():
    m = BK.crypto_live_px("KRAKEN:ARBUSD", yahoo_fn=lambda s: dict(last=0.000629), kraken_fn=lambda s: dict(last=0.1683, src="kraken_rest_doh:ARBUSD"))
    assert m["last"] == 0.1683 and "yahoo_mismatch" in m["src"]
def test_crypto_yahoo_used_when_agrees():
    m = BK.crypto_live_px("KRAKEN:XMRUSD", yahoo_fn=lambda s: dict(last=590.4, src="yahoo_chart_v8"), kraken_fn=lambda s: dict(last=589.6))
    assert m["last"] == 590.4
