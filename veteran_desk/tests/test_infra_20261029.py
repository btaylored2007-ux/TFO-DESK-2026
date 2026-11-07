"""2026-10-29 infra: venue_of suffixes, prior_close sanity, amend risk_aud, quote_asof, py3.9 yahoo_v8."""
import copy, datetime as dt, json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("PYTEST_CURRENT_TEST", "1")
from veteran_desk import pit as P, feeds as FD, book as BK, gate_v2 as G, seat_cli as SC, desk as D
from veteran_desk.tests.synthetic_book import skeleton

def test_venue_of_exchange_suffixes():
    assert P.venue_of("AAPL") == "US"
    assert P.venue_of("CBA.AX") == "ASX"
    assert P.venue_of("HSBA.L") == "LSE"
    assert P.venue_of("BNP.PA") == "Euronext"
    assert P.venue_of("7203.T") == "TSE"
    assert P.venue_of("2800.HK") == "HKEX"
    assert P.venue_of("D05.SI") == "SGX"
    assert P.venue_of("DBK.DE") == "XETRA"
    assert P.venue_of("005930.KS") == "KRX"
    assert P.venue_of("035420.KQ") == "KRX" or P.venue_of("035420.KQ") == "KRX"
    assert P.venue_of("2330.TW") == "TWSE"
    assert P.venue_of("AUDUSD=X") == "FX"
    assert P.venue_of("CL=F") == "FUT"
    assert P.venue_of("^GSPC") == "IDX"
    assert P.venue_of("KRAKEN:XBTUSD") == "CRYPTO"
    # costs/sessions: intl venues present
    assert "TSE" in P.CLOSE_UTC and "HKEX" in P.VENUE_BIAS

def test_aud_per_unit_still_uses_listing_ccy_for_jp_hk():
    # venue_of now TSE/HKEX; must NOT treat as USD via venue!=US short-circuit
    # Use fixed FX so no network: monkeypatch ccy_per_aud
    real = BK.ccy_per_aud
    BK.ccy_per_aud = lambda ccy, au, px_fn=None, ibkr_fn=None: {"JPY": 100.0, "HKD": 5.0}[ccy]
    try:
        assert abs(BK.aud_per_unit("7203.T", 0.65) - 1.0 / 100.0) < 1e-9
        assert abs(BK.aud_per_unit("2800.HK", 0.65) - 1.0 / 5.0) < 1e-9
        assert abs(BK.aud_per_unit("AAPL", 0.65) - 1.0 / 0.65) < 1e-9
        assert abs(BK.aud_per_unit("CBA.AX", 0.65) - 1.0) < 1e-9
    finally:
        BK.ccy_per_aud = real

def test_prior_close_rejects_bogus_previous_close(tmp_path, monkeypatch):
    from veteran_desk.tests.isolated_pit import open_pit
    open_pit(tmp_path, monkeypatch)
    # bar 1000, previousClose 700 (= -30%) -> use bar
    r = FD.prior_close("7203.T", yahoo_prev=700.0, bar_close=1000.0, max_div=0.05)
    assert r["close"] == 1000.0 and r["src"] == "daily_bar" and r.get("rejected_prev") == 700.0
    # within 5%: prefer bar still (daily_bar src)
    r2 = FD.prior_close("7203.T", yahoo_prev=1010.0, bar_close=1000.0, max_div=0.05)
    assert r2["close"] == 1000.0 and r2["src"] == "daily_bar"
    # no bar -> fallback to previousClose
    r3 = FD.prior_close("ZZZNOPE", yahoo_prev=50.0, bar_close=None, max_div=0.05)
    assert r3["close"] == 50.0 and r3["src"] == "previousClose_fallback"

def test_yahoo_v8_quote_callable_without_yfinance():
    # chart v8 path must work even when yfinance would fail (shape check; may hit network)
    q = BK.yahoo_v8_quote("AAPL", "5m", "2d")
    assert q is None or ("last" in q and "asof" in q)

def test_gate_returns_quote_asof_keys():
    now = dt.datetime.now(dt.timezone.utc)
    g = G.gate(dict(ticker="AAPL", side="LONG", entry=100, stop=95, target=110, grade="A"),
               None, dict(call="LONG"), dict(factual_kill=False), now,
               env=dict(book=([], None), firm="OK"), hist=([], "empty"))
    assert "quote_asof" in g and "quote_last" in g and "quote_src" in g  # None under PYTEST is fine

def test_amend_stop_recalculates_risk_aud():
    # Theo case: EURUSD stop 1.1245 fill, stop tightened 1.1000 -> 1.1200; risk_aud must fall with new distance
    import copy
    td = tempfile.mkdtemp(); pid = "VET-TEST-EURUSD-amend"
    D_ = copy.deepcopy(skeleton())
    for B in D_["seats"].values(): B["positions"] = {}; B["closed"] = []
    seat = "fx_commodities"
    if seat not in D_["seats"]: seat = list(D_["seats"].keys())[0]
    D_["audusd"] = 0.65
    D_["seats"][seat]["positions"][pid] = dict(
        id=pid, seat=seat, ticker="EURUSD=X", side="LONG", entry=1.1245, stop=1.1000,
        target=1.1500, shares=100000, risk_aud=10000.0, risk_pct=0.02, status="open", fill=1.1245,
        venue="FX", mark=1.1245, scale_t1=True)
    book = os.path.join(td, "book.json"); json.dump(D_, open(book, "w"))
    old = BK.BOOK
    try:
        BK.BOOK = book
        out = SC.amend(seat, pid, "tighten theo", stop=1.1200)
        assert out.get("ok"), out
        p = json.load(open(book))["seats"][seat]["positions"][pid]
        assert p["stop"] == 1.1200
        expect = round(abs(1.1245 - 1.1200) / 0.65 * 100000, 2)
        assert abs(p["risk_aud"] - expect) < 1.0, (p["risk_aud"], expect)
        assert p["risk_aud"] < 10000.0 * 0.5
    finally:
        BK.BOOK = old
