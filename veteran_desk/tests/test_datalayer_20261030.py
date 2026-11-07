from veteran_desk import data_layer as D
def test_symbol_map():
    assert D.eodhd_symbol("AAPL") == "AAPL.US" and D.eodhd_symbol("BHP.AX") == "BHP.AU" and D.eodhd_symbol("VOD.L") == "VOD.LSE"
    assert D.eodhd_symbol("RY.TO") == "RY.TO" and D.eodhd_symbol("0700.HK") == "0700.HK" and D.eodhd_symbol("^GSPC") == "GSPC.INDX"
    assert D.eodhd_symbol("BRK.B") == "BRK-B.US" and D.eodhd_symbol("7203.T") == "7203.TSE"
    for s in ("KRAKEN:XBTUSD", "ES=F", "BTC-USD", "BBCA.JK"): assert D.eodhd_symbol(s) is None
def test_breaker_429_and_fallback():
    D._BRK.clear(); n = []
    def g(p, q): n.append(p); return 429, None
    assert D.eod_rows("AAPL", "2026-10-01", get=g) == [] and D.breaker_open("eod")
    assert D.eod_rows("AAPL", "2026-10-01", get=g) == [] and len(n) == 1   # breaker short-circuits
    D._BRK.clear()
    for code in (401, 403):
        assert D.call("x%d" % code, "/eod/A.US", {}, get=lambda p, q, c=code: (c, None)) is None and D.breaker_open("x%d" % code)
    D._BRK.clear()
def test_rows_and_yahoo_fallback():
    js = [{"date": "2026-10-29", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}, {"date": "2026-10-30", "open": 1, "high": 1, "low": 2, "close": 1}]
    assert D.eod_rows("AAPL", "2026-10-01", get=lambda p, q: (200, js)) == [("2026-10-29", 1.0, 2.0, 0.5, 1.5, 10.0)]
    r, src = D.daily_rows("AAPL", 5, yahoo_fn=lambda s: [("2026-10-29", 1, 1, 1, 1, 0)])   # pytest -> paid disabled -> fallback
    assert src == "yahoo_fallback" and r
def test_no_key_in_errors():
    assert "api_token=***" in D.redact("https://eodhd.com/api/eod/A.US?api_token=SECRET123&fmt=json")
def test_paid_first_frame_shape_and_adjust():
    # Needs pandas. Receipt: docs/TEST_DEPENDENCY_RECEIPT_20261106.json. Missing pandas raises ModuleNotFoundError.
    from veteran_desk import paid_first as PF
    js = [{"date": "2026-10-28", "open": 10, "high": 11, "low": 9, "close": 10, "adjusted_close": 5, "volume": 1},
          {"date": "2026-10-29", "open": 10, "high": 12, "low": 9, "close": 11, "adjusted_close": 5.5, "volume": 2}]
    g = lambda p, q: (200, js) if p.startswith("/eod/") else (200, {})
    D_ = PF.frame("BHP.AX", "5d", auto_adjust=False, actions=False, get=g)
    assert list(D_.columns) == ["Open", "High", "Low", "Close", "Adj Close", "Volume"] and str(D_.index.tz) == "Australia/Sydney" and D_.attrs["src"] == "eodhd"
    A = PF.frame("AAPL", "5d", get=g)
    assert abs(A.Close.iloc[-1] - 5.5) < 1e-9 and "Adj Close" not in A.columns and "Dividends" in A.columns
    assert PF.frame("ES=F", "5d", get=g) is None and PF.frame("KRAKEN:XBTUSD", "5d", get=g) is None and PF.frame("AUDUSD=X", "5d", get=g) is None
    from veteran_desk import data_layer as D
    D._BRK.clear()
