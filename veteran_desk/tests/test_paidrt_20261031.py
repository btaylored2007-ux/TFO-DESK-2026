"""Offline tests for paid realtime marks. No websocket, no API keys. tfo_paidrt_20261031."""
import datetime as dt, json, os, time
import pytest
UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 28, 10, 0, tzinfo=UTC)   # Wednesday, London cash session (GMT after the Oct 25 change)
SAT = dt.datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


def _touch(path, when):
    ts = when.timestamp(); os.utime(path, (ts, ts))


def _wire(tmp_path, monkeypatch, quotes, when=NOW, authorized=True):
    from veteran_desk import eodhd_eu_ws as EU
    monkeypatch.setattr(EU, "LAST", str(tmp_path / "last.json"))
    monkeypatch.setattr(EU, "HEALTH", str(tmp_path / "health.json"))
    json.dump(quotes, open(EU.LAST, "w"))
    json.dump({"authorized": authorized, "streams": {"eu": "up", "eu-candles": "up"}, "paper_only": True}, open(EU.HEALTH, "w"))
    _touch(EU.HEALTH, when); _touch(EU.LAST, when)


def test_classify_and_auth_shapes():
    from veteran_desk import eodhd_eu_ws as EU
    assert EU.classify({"status_code": 200, "message": "Authorized"})[0] == "auth_ok"
    assert EU.classify({"status": 403, "message": "Server error"})[0] == "auth_fail"
    assert EU.classify({"status_code": 422, "message": "Symbols limit reached"})[0] == "status"
    assert EU.classify({"s": "VOD.LSE", "p": 80.5, "v": 10, "c": [], "ms": "open", "t": 1})[0] == "trade"
    assert EU.classify({"s": "VOD.LSE", "bp": 80.1, "ap": 80.2, "bs": 1, "as": 2, "t": 1})[0] == "quote"
    assert EU.classify({"s": "VOD.LSE", "i": "1m", "t": 1, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 3})[0] == "candle"
    tape = EU.Tape(); tape.ingest("eu", {"status": 403, "message": "no"})
    assert tape.auth_failed
    url = EU._url("eu", "SUPERSECRET")
    assert "SUPERSECRET" in url and "SUPERSECRET" not in json.dumps(EU.health())


def test_symbol_map():
    from veteran_desk import eodhd_eu_ws as EU
    assert EU.is_eu("VOD.L") and EU.is_eu("SAP.DE") and EU.is_eu("BNP.PA") and EU.is_eu("ASML.AS")
    assert not EU.is_eu("AAPL") and not EU.is_eu("BHP.AX") and not EU.is_eu("KRAKEN:XBTUSD")
    assert EU.to_desk("VOD.LSE") == "VOD.L" and EU.to_desk("SAP.XETRA") == "SAP.DE" and EU.to_desk("ASML.AS") == "ASML.AS"
    assert "HSBA.L" in EU.subscribe_symbols()


def test_fresh_ws_is_primary_stale_falls_through(tmp_path, monkeypatch):
    from veteran_desk import eodhd_eu_ws as EU
    ts = int((NOW - dt.timedelta(seconds=5)).timestamp() * 1000)
    _wire(tmp_path, monkeypatch, {"VOD.L": {"px": 80.5, "ts_ms": ts, "ms": "open", "eodhd": "VOD.LSE", "src": "eodhd_eu_ws"}})
    q = EU.live_px("VOD.L", now=NOW)
    assert q["src"] == "eodhd_eu_ws" and q["last"] == 80.5
    chart = EU.chart_quote("VOD.L", now=NOW)
    assert chart["paid_feed"] is True and chart["px"] == 80.5 and chart["executable"] is True
    assert "websocket" in chart["source"] and chart.get("url") is None
    h = EU.health(now=NOW)
    assert h["healthy"] and "LSE" in h["venues_live"] and "SUPERSECRET" not in json.dumps(h)
    stale = int((NOW - dt.timedelta(seconds=600)).timestamp() * 1000)
    _wire(tmp_path, monkeypatch, {"VOD.L": {"px": 80.5, "ts_ms": stale, "ms": "open", "src": "eodhd_eu_ws"}})
    assert EU.live_px("VOD.L", now=NOW) is None and EU.health(now=NOW)["healthy"] is False


def test_closed_session_keeps_last_ws_print(tmp_path, monkeypatch):
    from veteran_desk import eodhd_eu_ws as EU
    ts = int(dt.datetime(2026, 10, 30, 16, 0, tzinfo=UTC).timestamp() * 1000)
    _wire(tmp_path, monkeypatch, {"SAP.DE": {"px": 190.0, "ts_ms": ts, "ms": "closed", "src": "eodhd_eu_ws"}}, when=SAT)
    q = EU.live_px("SAP.DE", now=SAT)
    assert q["src"] == "eodhd_eu_ws" and q["last"] == 190.0
    assert "XETRA" in EU.health(now=SAT)["venues_live"]


def test_dead_consumer_is_not_primary(tmp_path, monkeypatch):
    from veteran_desk import eodhd_eu_ws as EU
    ts = int((NOW - dt.timedelta(seconds=5)).timestamp() * 1000)
    _wire(tmp_path, monkeypatch, {"VOD.L": {"px": 80.5, "ts_ms": ts, "ms": "open"}}, when=NOW - dt.timedelta(minutes=10))
    assert EU.live_px("VOD.L", now=NOW) is None
    assert EU.health(now=NOW)["why"] == "consumer heartbeat stale"


def test_tape_flush_writes_quotes_and_1m(tmp_path, monkeypatch):
    from veteran_desk import eodhd_eu_ws as EU, intraday_store as IB
    monkeypatch.setattr(EU, "LAST", str(tmp_path / "last.json"))
    monkeypatch.setattr(EU, "HEALTH", str(tmp_path / "health.json"))
    monkeypatch.setenv("INTRADAY_DB", str(tmp_path / "bars.sqlite"))
    tape = EU.Tape(); ts = int(NOW.timestamp() * 1000)
    assert tape.ingest("eu", json.dumps({"status_code": 200, "message": "Authorized"})) == ["auth_ok"]
    tape.ingest("eu", json.dumps({"s": "VOD.LSE", "p": 80.5, "v": 10, "c": [], "dp": False, "ms": "open", "t": ts}))
    tape.ingest("eu-candles", json.dumps({"s": "VOD.LSE", "i": "1m", "t": ts - 60000, "o": 80, "h": 81, "l": 79, "c": 80.4, "v": 100}))
    meta = EU.flush(tape, now=NOW)
    assert meta["authorized"] and "api_token" not in json.dumps(meta)
    _touch(EU.HEALTH, NOW)
    assert EU.live_px("VOD.L", now=NOW)["last"] == 80.5
    bars = IB.recent("VOD.L", "1m", 5)
    assert bars and bars[-1]["c"] == 80.4 and bars[-1]["src"] == "eodhd_eu_ws"
    assert IB.roll_symbols(["VOD.L"], since_ms=ts - 120000) >= 1
    assert IB.recent("VOD.L", "5m", 5)


def test_book_and_paid_feeds_prefer_ws(monkeypatch):
    from veteran_desk import book as B, eodhd_eu_ws as EU
    import paid_feeds as PF
    monkeypatch.setattr(EU, "live_px", lambda s, now=None: dict(last=7, hi=8, lo=6, open=6.5, asof="t", window_start="t", src="eodhd_eu_ws") if s == "VOD.L" else None)
    assert B.live_px("VOD.L")["src"] == "eodhd_eu_ws"
    monkeypatch.setattr(EU, "chart_quote", lambda s, now=None: dict(px=4, ccy="GBp", source="EODHD Cboe Europe websocket (real-time)", paid_feed=True))
    called = []
    monkeypatch.setattr(PF, "_fmp_payload", lambda *a, **k: called.append(1))
    q = PF.fetch_quote("VOD.L")
    assert q["px"] == 4 and called == []


def test_mark_fetcher_massive_primary_yahoo_fallback(monkeypatch):
    import tfo_equity_refresh as T, paid_feeds as PF
    monkeypatch.setattr(PF, "fetch_quote", lambda s: {"px": 10, "ccy": "USD", "source": "Massive 1m aggs (real-time)", "paid_feed": True,
                                                      "session": "regular", "executable": False, "paper_executable": False, "quote_utc": "2026-10-28T14:00:00Z", "status": "regular_delayed"})
    monkeypatch.setattr(T, "fetch_quote", lambda s: (_ for _ in ()).throw(AssertionError("yahoo primary")))
    assert "Massive" in T.mark_fetcher("AAPL")["source"]
    qs, err = T.collect_quotes(["AAPL"], fx_provider=lambda c: ({"USD": {"rate": 1.0, "status": "identity", "quote_utc": "2026-10-28T14:00:00Z"}}, {}))
    assert qs["AAPL"]["px"] == 10 and "AAPL" not in err
    monkeypatch.setattr(PF, "fetch_quote", lambda s: (_ for _ in ()).throw(RuntimeError("paid down")))
    monkeypatch.setattr(T, "fetch_quote", lambda s: {"px": 2, "source": "Yahoo public chart", "ccy": "USD"})
    assert T.mark_fetcher("AAPL")["source"].startswith("Yahoo")


def test_command_marks_use_mark_fetcher(monkeypatch):
    import command_server as CS, tfo_equity_refresh as T
    monkeypatch.setattr(T, "mark_fetcher", lambda s: {"px": 9, "ccy": "USD", "quote_utc": "2026-10-28T14:00:00Z"})
    assert CS._yf_fetch(["AAPL"])["AAPL"]["px"] == 9


def test_rollup_put_retention_and_rank(tmp_path, monkeypatch):
    from veteran_desk import intraday_store as IB
    monkeypatch.setenv("INTRADAY_DB", str(tmp_path / "bars.sqlite"))
    assert IB.rollup([(0, 10, 11, 9, 10.5, 1), (60_000, 10.5, 12, 10, 11, 2), (240_000, 11, 11, 8, 9, 3)], "5m") == [(0, 10, 12, 8, 9, 6.0)]
    now = int(time.time() * 1000)
    ts = now - 60_000
    assert IB.recent("AAPL", "5m") == []
    IB.put([("AAPL", "5m", ts, 1, 2, 0.5, 9, 4)], "massive")
    IB.put([("AAPL", "5m", ts, 1, 1, 1, 1, 1)], "eodhd_roll")
    assert IB.recent("AAPL", "5m")[-1]["c"] == 9 and IB.recent("AAPL", "5m")[-1]["src"] == "massive"
    old = now - 20 * 86400 * 1000
    IB.put([("AAPL", "1m", old, 1, 1, 1, 1, 1), ("AAPL", "1m", now - 1000, 2, 2, 2, 2, 1), ("AAPL", "15m", old, 3, 3, 3, 3, 1)], "massive")
    c = IB.connect(); IB.prune(c, now); c.commit(); c.close()
    assert all(r["ts"] != old for r in IB.recent("AAPL", "1m", 20))
    assert IB.recent("AAPL", "15m", 5)[0]["c"] == 3
    sm = IB.summary("AAPL")
    assert sm["tf"] == "5m" and sm["last"] == 9
    feat = IB.attach({"sym": "AAPL", "close": 1})
    assert feat["intraday"]["src"] == "massive"
    h = IB.health(); assert h["n_5m"] >= 1 and "api_token" not in json.dumps(h)


def test_pull_symbol_offline(tmp_path, monkeypatch):
    from veteran_desk import intraday_store as IB
    monkeypatch.setenv("INTRADAY_DB", str(tmp_path / "bars.sqlite"))
    ts = int(time.time() * 1000) - 120_000
    def mag(sym, mult, span):
        return [dict(t=ts, o=1, h=2, l=0.5, c=1.5, v=10)] if (sym, mult, span) == ("AAPL", 5, "minute") else []
    assert IB.pull_symbol("AAPL", massive_get=mag) == 1
    assert IB.recent("AAPL", "5m")[-1]["src"] == "massive"
    base = (int(time.time()) // 900) * 900 - 900
    def eod(sym, interval):
        if interval == "5m": return [(base, 1, 2, 0.5, 1.5, 3), (base + 300, 1.5, 3, 1, 1.2, 4)]
        return []
    assert IB.pull_symbol("VOD.L", eodhd_get=eod) >= 2
    assert IB.recent("VOD.L", "5m")[-1]["src"] == "eodhd_intraday"
    assert IB.recent("VOD.L", "15m")[-1]["src"] == "eodhd_roll"
    assert IB.recent("VOD.L", "15m")[-1]["v"] == 7


def test_history_import(tmp_path, monkeypatch):
    import sqlite3
    from veteran_desk import intraday_store as IB
    hist = tmp_path / "history.sqlite"
    c = sqlite3.connect(hist)
    c.execute("CREATE TABLE aggs(sym TEXT, res TEXT, ts INTEGER, o REAL, h REAL, l REAL, c REAL, v REAL, vw REAL, n INTEGER, src TEXT)")
    ts = int(time.time() * 1000) - 60_000
    c.execute("INSERT INTO aggs VALUES(?,?,?,?,?,?,?,?,?,?,?)", ("MSFT", "minute", ts, 1, 2, 0.5, 1.4, 8, None, None, "massive"))
    c.commit(); c.close()
    monkeypatch.setattr(IB, "HIST", str(hist))
    monkeypatch.setenv("INTRADAY_DB", str(tmp_path / "bars.sqlite"))
    assert IB.import_history() == 1
    assert IB.recent("MSFT", "1m")[-1]["c"] == 1.4


def test_feed_delay_and_borrow_and_dash(tmp_path, monkeypatch):
    from veteran_desk import eodhd_eu_ws as EU, feed_delay as FD, intraday_store as IB, borrow_status as BS, thf_dash as TD
    monkeypatch.setattr(EU, "health", lambda now=None: dict(healthy=True, venues_live=["LSE", "XETRA"], why=None))
    assert FD.label("LSE")["delayed"] is False and FD.label("LSE")["src"] == "eodhd_eu_ws"
    monkeypatch.setattr(EU, "health", lambda now=None: dict(healthy=False, venues_live=[], why="down"))
    assert FD.label("LSE")["delayed"] is True
    p = tmp_path / "ibkr_borrow.json"
    fresh = dt.datetime.now(UTC).replace(microsecond=0).isoformat()
    json.dump({"updated_utc": fresh, "us": {"names": {"AAPL": {"ok": True, "available": 10}}}, "asx": {"names": {}}}, open(p, "w"))
    d = BS.dash(str(p))
    assert d["fresh_24h"] and d["status"] == "fresh" and d["us_names"] == 1
    old = (dt.datetime.now(UTC) - dt.timedelta(hours=30)).isoformat()
    json.dump({"updated_utc": old, "us": {"names": {"AAPL": {}}}}, open(p, "w"))
    assert BS.dash(str(p))["fresh_24h"] is False
    assert BS.dash(str(tmp_path / "missing.json"))["status"] == "missing"
    monkeypatch.setattr(BS, "dash", lambda path=None: dict(status="fresh", fresh_24h=True, age_h=1.0))
    monkeypatch.setattr(EU, "health", lambda now=None: dict(healthy=True, venues_live=["LSE"], why=None, symbols=2))
    monkeypatch.setattr(IB, "health", lambda: dict(n_1m=3, n_5m=2, n_15m=1, newest_utc="t", retain_days={"1m": 10}))
    fc = TD.feed_cards()
    assert fc["borrow"]["fresh_24h"] and fc["paid_feeds"]["eu_ws"]["healthy"] and fc["paid_feeds"]["intraday"]["n_5m"] == 2
    html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "thf_dash.html")).read()
    assert "IBKR borrow" in html and "Paid marks" in html


def test_data_layer_quote_asks_ws_first(monkeypatch):
    from veteran_desk import data_layer as D, eodhd_eu_ws as EU
    monkeypatch.setattr(EU, "live_px", lambda s, now=None: dict(last=3, src="eodhd_eu_ws") if str(s).endswith(".PA") else None)
    assert D.quote("BNP.PA", yahoo_fn=lambda s: pytest.fail("yahoo"))["src"] == "eodhd_eu_ws"
