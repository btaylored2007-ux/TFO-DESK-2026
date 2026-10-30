"""fxsize 2026-10-29: Asian/European listings are sized in real AUD (listing currency x live FX), USD/AUD unchanged, no FX -> fail closed."""
import pytest
from veteran_desk import book as BK, desk as DK, gate_v2 as G
AU = 0.7054   # AUDUSD
RATES = {"AUDJPY=X": 111.185, "AUDHKD=X": 5.5087, "AUDKRW=X": 941.51, "AUDTWD=X": 22.929, "AUDSGD=X": 0.90273, "AUDGBP=X": 0.5287, "AUDEUR=X": 0.6324, "AUDUSD=X": AU}
def yahoo(sym): return dict(last=RATES[sym]) if sym in RATES else None
@pytest.fixture(autouse=True)
def _px(monkeypatch):
    BK._FX.clear(); monkeypatch.setattr(BK, "live_px", yahoo); monkeypatch.setattr(BK, "_ibkr_fx", lambda c: None); yield; BK._FX.clear()
SEAT = 500000.0; RISK = SEAT * BK.RISK_NON_A_MIN   # A$10k (2% rail; tfo_review_fix_20261031 A default is now 1%)
CASES = [("4519.T", 6400.0, 6150.0, "JPY"), ("2800.HK", 23.85, 25.30, "HKD"), ("005930.KS", 61000.0, 58500.0, "KRW"),
         ("2330.TW", 1050.0, 1005.0, "TWD"), ("D05.SI", 42.10, 40.60, "SGD")]
@pytest.mark.parametrize("sym,e,s,ccy", CASES)
def test_asian_listing_sized_to_2pct_real_aud(sym, e, s, ccy):
    assert BK.listing_ccy(sym) == ccy
    q = DK.size(dict(ticker=sym, entry=e, stop=s, target=e + 3 * (e - s)), None, RISK, AU)["shares"]
    real = q * abs(e - s) / RATES["AUD%s=X" % ccy]          # true AUD risk: local-ccy risk / (local per AUD)
    assert abs(real - RISK) / RISK < 0.02, (sym, q, real)   # ~A$10k = 2% of A$500k
    usd_bug = int(RISK / (abs(e - s) / AU)); assert q != usd_bug   # not the old 'price is USD' size
def test_2800hk_old_vs_new():
    old_real = 4848 * 1.45 / RATES["AUDHKD=X"]; assert old_real < 1500   # the withdrawn VET-2800HK: 'A$9,999' was ~A$1.3k real
    q = DK.size(dict(ticker="2800.HK", entry=23.85, stop=25.30, target=20.95), None, RISK, AU)["shares"]; assert 37000 < q < 38500
def test_usd_and_aud_unchanged():
    for sym, e, s in (("XLE", 68.2, 66.0), ("MSFT", 543.0, 532.0)):
        assert BK.aud(sym, 10.0, AU) == 10.0 / AU
        assert DK.size(dict(ticker=sym, entry=e, stop=s, target=e + 5), None, RISK, AU)["shares"] == int(RISK / (abs(e - s) / AU))
    for sym in ("TCL.AX", "GMG.AX"): assert BK.aud(sym, 10.0, AU) == 10.0
    for sym in ("GC=F", "^GSPC", "KRAKEN:XBTUSD", "EURUSD=X"): assert BK.aud(sym, 10.0, AU) == 10.0 / AU   # existing handling kept
def test_pence_and_eur():
    assert abs(BK.aud("VOD.L", 100.0, AU) - 1.0 / RATES["AUDGBP=X"]) < 1e-9   # 100p = GBP1
    assert abs(BK.aud("SAP.DE", 1.0, AU) - 1.0 / RATES["AUDEUR=X"]) < 1e-9
def test_via_usd_when_direct_missing(monkeypatch):
    r = {"AUDUSD=X": AU, "JPY=X": 157.6}; monkeypatch.setattr(BK, "live_px", lambda s: dict(last=r[s]) if s in r else None)
    assert abs(BK.ccy_per_aud("JPY", AU) - AU * 157.6) < 1e-9
def test_ibkr_only_after_yahoo(monkeypatch):
    calls = []; monkeypatch.setattr(BK, "_ibkr_fx", lambda c: calls.append(c) or 111.0)
    assert BK.ccy_per_aud("JPY", AU) == RATES["AUDJPY=X"] and calls == []   # Yahoo first
    BK._FX.clear(); monkeypatch.setattr(BK, "live_px", lambda s: None)
    assert BK.ccy_per_aud("JPY", AU) == 111.0 and calls == ["JPY"]   # IBKR fallback
def test_no_fx_fails_closed(monkeypatch):
    monkeypatch.setattr(BK, "live_px", lambda s: None)
    with pytest.raises(BK.FxUnavailable): BK.aud("4519.T", 100.0, AU)
    z = DK.size(dict(ticker="4519.T", entry=6400, stop=6150, target=7000), None, RISK, AU); assert z["shares"] == 0 and not z["passes_doubled_cost"]
    assert G.ccy_issue("4519.T", AU)
    with pytest.raises(BK.FxUnavailable): BK.aud("FOO.ZZ", 1.0, AU)   # unknown suffix
def test_insane_rate_rejected(monkeypatch):
    monkeypatch.setattr(BK, "live_px", lambda s: dict(last=0.0064) if s == "AUDJPY=X" else None)   # inverted quote
    with pytest.raises(BK.FxUnavailable): BK.ccy_per_aud("JPY", AU)
def test_gate_no_longer_blanket_rejects_hkd_jpy():
    for s in ("2800.HK", "4519.T", "005930.KS", "2330.TW", "D05.SI"): assert G.ccy_issue(s, AU) is None
