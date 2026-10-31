"""Permanent paper-desk feeds (2026-10-31). No network. Fail-open inputs, rails untouched."""
import datetime as dt, json, os, sqlite3
from veteran_desk import crypto_universe as CU, coinalyze_client as CZ, benzinga_news as BZ
from veteran_desk import flow_ab as FA, earnings_advisory as EA, grade_ledger as GL, intraday_health as IH
from veteran_desk import vd_engine as E, pit as P, massive as M
UTC = dt.timezone.utc


def test_stable_bases_and_all_quotes():
    assert CU.is_stable_pair("AUSDUSD") and CU.is_stable_pair("QCADUSD") and CU.is_stable_pair("EURUSD")
    assert not CU.is_stable_pair("XBTUSD") and not CU.is_stable_pair("BTCEUR")
    assert CU.split_alt("BTCUSDT") == ("BTC", "USDT")
    got = CU.eligible([
        dict(alt="AUSDUSD", liquid=True, vol24h_aud=9e6),
        dict(alt="QCADUSD", liquid=True, vol24h_aud=9e6),
        dict(alt="BTCEUR", liquid=True, vol24h_aud=9e6),
        dict(alt="XBTUSD", liquid=True, vol24h_aud=1000),
        dict(alt="SOLUSD", liquid=False, vol24h_aud=9e6),
    ])
    assert set(got) == {"KRAKEN:BTCEUR"}
    assert CU.quote_to_usd("ZEUR", {"EURUSD": 1.1}) == 1.1
    assert abs(CU.quote_to_usd("ZJPY", {"USDJPY": 150}) - 1 / 150) < 1e-12
    assert CU.quote_to_usd("ZAUD", {}) is None   # unpriced quote is not an invented ADV
    w = E.SEATS["crypto"]["w"]
    assert abs(sum(w) - 1) < 1e-9 and w[3] == 0.10


def test_hourly_signal_and_daily_fallback():
    now = dt.datetime(2026, 10, 31, 12, tzinfo=UTC)
    rows = []
    for i in range(25):
        ts = (now - dt.timedelta(hours=24 - i)).isoformat()
        rows.append((ts, 100, 101, 99, 100, 10))
    rows[-1] = (now.isoformat(), 100, 130, 99, 130, 50)
    sig = CU.hourly_signal(rows, now, 1)
    assert sig["broke_up"] and not sig["stale"] and sig["rvol"] == 5
    old = [((now - dt.timedelta(hours=10)).isoformat(), 1, 2, 0.5, 1, 1)] * 21
    assert CU.hourly_signal(old, now, 1)["stale"] is True
    assert CU.hourly_signal(rows[:5], now, 1) is None
    f = dict(dir=1, close=130, atr=1, e20=130, stab=True, rvol=1, hi20p=100, lo20p=90, rs_btc7=0, rs_btc30=0, stack=3,
             h1=dict(stale=False, rvol=3, broke_up=True, broke_dn=False))
    assert E.signals("crypto", f, {"news": {}})["vol_breakout"] is True
    stale = dict(f, h1=dict(stale=True, rvol=0, broke_up=False, broke_dn=False), rvol=3)
    assert E.signals("crypto", stale, {"news": {}})["vol_breakout"] is True   # daily proxy, fail open
    assert E.zs([None, None, None]) == [0.0, 0.0, 0.0]


def test_hourly_bars_roundtrip(tmp_path):
    def get(url):
        assert "interval=60" in url
        return {"result": {"XXBTZUSD": [
            [1700000000, "1", "2", "0.5", "1.5", "1", "10", 1],
            [1700003600, "9", "9", "9", "9", "9", "1", 1],
        ], "last": 1700003600}}
    c = sqlite3.connect(":memory:")
    assert P.ingest_kraken_hourly(c, "XBTUSD", get=get) == 1   # forming bar dropped
    rows = P.hourly_asof(c, "KRAKEN:XBTUSD", "2026-01-01T00:00:00+00:00")
    assert len(rows) == 1 and rows[0][4] == 1.5
    assert P.hourly_asof(sqlite3.connect(":memory:"), "KRAKEN:XBTUSD", "2026-01-01") == []


def test_coinalyze_rate_limit_and_neutral():
    bucket = []
    assert all(CZ.allow(now=10, bucket=bucket) for _ in range(40))
    assert CZ.allow(now=10, bucket=bucket) is False
    assert CZ.allow(now=80, bucket=bucket) is True
    def get(url, headers):
        assert headers.get("api_key")
        if "open-interest" in url: return [{"symbol": "BTCUSDT_PERP.A", "value": 100}]
        if "funding-rate" in url: return [{"symbol": "BTCUSDT_PERP.A", "value": 0.0002}]
        return [{"symbol": "BTCUSDT_PERP.A", "history": [{"l": 1, "s": 4}]}]
    snap = CZ.snapshot(["KRAKEN:XBTUSD"], get=get, now=1000, cache_path=os.path.join(str(_tmp()), "cz.json"), bucket=[])
    assert snap["ok"] and snap["blocks_trade"] is False
    row = snap["by_sym"]["KRAKEN:XBTUSD"]
    assert row["funding_rate"] == 0.0002 and row["liq_short"] == 4
    assert CZ.f_score(row, 1) is not None and CZ.f_score({}, 1) is None
    def boom(url, headers): raise OSError("down")
    bad = CZ.snapshot(["KRAKEN:XBTUSD"], get=boom, now=5, cache_path=os.path.join(str(_tmp()), "cz2.json"), bucket=[])
    assert bad["ok"] is False and bad["by_sym"] == {} and bad["blocks_trade"] is False


def _tmp():
    import tempfile
    d = tempfile.mkdtemp(); return d


def test_benzinga_sentiment_recency_and_quinn(monkeypatch):
    assert BZ.sentiment_of("Apple beats and raised guidance") == 1
    assert BZ.sentiment_of("Bank probe and plunges") == -1
    assert BZ.sentiment_of("market wrap", raw="negative") == -1
    now = dt.datetime(2026, 10, 31, 13, tzinfo=UTC)
    rows = [dict(title="Apple beats", tickers=["AAPL"], published_utc="2026-10-31T12:00:00+00:00")]
    assert BZ.score_rows(rows, now)["AAPL"] > 0
    assert BZ.recency_weight("2026-10-01T00:00:00+00:00", now) == 0
    def g(path, q):
        return 200, {"results": [dict(title="Apple beats", published_utc="2026-10-31T12:00:00Z", tickers=["AAPL"], sentiment="positive", url="http://x")]}
    q = BZ.for_quinn(get=g, now=now)
    assert q["ok"] and q["blocks_trade"] is False and q["rows"][0]["sentiment"] == 1 and q["rows"][0]["recency_w"] > 0
    monkeypatch.setattr(M, "_key", lambda: None)
    missing = BZ.load()
    assert missing["ok"] is False and missing["scores"] == {} and missing["blocks_trade"] is False


def test_flow_ab_flag_fail_open():
    off = FA.for_pack(env="without")
    assert off["included"] is False and off["blocks_trade"] is False and off["paper_only"] is True
    def g(url, headers):
        if "open-interest" in url: return {"data": [{"open_interest_usd": 5}]}
        if "funding-rate" in url: return {"data": [{"funding_rate": 0.01}]}
        return {"data": [{"ticker": "AAPL", "type": "call", "total_premium": 10, "created_at": "t"}]}
    on = FA.for_pack(get_cg=g, get_uw=g, env="with", now=1)
    assert on["included"] and on["blocks_trade"] is False
    assert on["coinglass"]["rows"]["BTC"]["oi_usd"] == 5 and on["unusual_whales"]["rows"][0]["ticker"] == "AAPL"
    def boom(url, headers): raise RuntimeError("nope")
    bad = FA.for_pack(get_cg=boom, get_uw=boom, env="with", now=2)
    assert bad["ok"] is False and bad["blocks_trade"] is False and bad["included"] is False


def test_earnings_advisory_fail_open(tmp_path):
    today = dt.date(2026, 11, 1)
    rows = [{"code": "AAPL.US", "report_date": "2026-11-02"}, {"code": "BHP.AU", "report_date": "2026-11-20"},
            {"code": "VOD.LSE", "report_date": "2026-11-01"}, {"code": "NOPE.ZZ", "report_date": "2026-11-01"}]
    flags = EA.flags_for(["AAPL", "BHP.AX", "VOD.L"], today=today, rows=rows)
    assert flags["AAPL"]["flag"] == "earnings_blackout" and flags["AAPL"]["blocks_trade"] is False
    assert flags["VOD.L"]["advisory"] is True and "BHP.AX" not in flags and "NOPE.ZZ" not in flags
    assert EA.flags_for(["AAPL"], today=today, fetch=lambda t: (_ for _ in ()).throw(RuntimeError("down")), cache_path=str(tmp_path / "missing.json")) == {}
    cache = tmp_path / "fresh.json"
    json.dump([{"code": "MSFT.US", "report_date": "2026-11-01"}], open(cache, "w"))
    got, warn = EA.load_rows(today, fetch=lambda t: None, now_ts=os.path.getmtime(cache) + 10, cache_path=str(cache))
    assert warn is None and got[0]["code"] == "MSFT.US"
    stale = EA.flags_for(["MSFT"], today=today, fetch=lambda t: None, now_ts=os.path.getmtime(cache) + 40 * 3600, cache_path=str(cache))
    assert stale == {}


def test_grade_ledger_forwards_and_weekly(tmp_path):
    path = str(tmp_path / "outcomes.jsonl"); weekly = str(tmp_path / "weekly.json")
    now = dt.datetime(2026, 10, 31, 12, tzinfo=UTC)
    rows = [
        dict(source="thf", seat="crypto", sym="KRAKEN:XBTUSD", side="LONG", grade="A+", score=93, utc=now.isoformat(),
             pillars_z=dict(P=2.0, V=0.2, N=0, F=1.4, X=0.1), close=100, stop=90, last_bar="2026-10-01"),
        dict(source="vd", seat="news_filings", sym="AAPL", side="SHORT", grade="B", score=None, utc=now.isoformat(),
             pillars_z=dict(P=0, V=0, N=2.2, F=0, X=0), close=50, stop=55, last_bar="2026-10-01"),
        dict(source="thf", seat="trend", sym="ZZ", side="LONG", grade="C", utc=now.isoformat(), close=1, stop=0.9),
    ]
    fresh = GL.record(rows, path)
    assert [r["grade"] for r in fresh] == ["A+", "B"]
    assert GL.record(rows, path) == []   # same minute, not duplicated
    prices = {"KRAKEN:XBTUSD": [("2026-10-02", 110), ("2026-10-03", 90), ("2026-10-04", 100), ("2026-10-05", 105), ("2026-10-06", 120)],
              "AAPL": [("2026-10-02", 40), ("2026-10-03", 45), ("2026-10-04", 48), ("2026-10-05", 52), ("2026-10-06", 60)]}
    stored = GL._read(path)
    GL.apply_forwards(stored, lambda sym, after: prices.get(sym))
    # long: risk 10. d1 (110-100)/10 = 1. d5 (120-100)/10 = 2
    assert stored[0]["fwd"]["d1"]["r"] == 1 and stored[0]["fwd"]["d5"]["pct"] == 0.2
    # short: risk 5. d1 (50-40)/5 = 2. d5 (50-60)/5 = -2
    assert stored[1]["fwd"]["d1"]["r"] == 2 and stored[1]["fwd"]["d5"]["r"] == -2
    GL._rewrite(path, stored)
    rep = GL.write_weekly(GL._read(path), weekly, now=now)
    assert rep["by_grade"]["A+"]["n_resolved"] == 1 and rep["by_grade"]["A+"]["hit_rate"] == 1
    assert rep["by_grade"]["B"]["avg_r"] == -2   # longest horizon is the headline R
    assert rep["by_pillar"]["F"]["n_graded"] >= 1 and rep["by_pillar"]["N"]["n_graded"] >= 1
    assert rep["paper_only"] is True
    src = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "vd_engine.py")).read()
    assert 'not x["earnings_block"]' in src and "GL.record_thf" in src
    assert "risk_plan(" not in src.split('"""', 2)[2]


def test_intraday_health(tmp_path):
    frames = {"5m": {"AAPL": [0, 300, 600, 1500]}, "1h": {"SPY": [0, 3600, 3600 * 20]}}
    doc = IH.build(frames, now=dt.datetime(2026, 10, 31, tzinfo=UTC))
    assert doc["blocks_trade"] is False
    assert doc["timeframes"]["5m"]["gap_count"] == 1
    assert doc["timeframes"]["5m"]["symbols"]["AAPL"]["gaps"][0]["missing_bars"] == 2
    assert doc["timeframes"]["1h"]["gap_count"] == 0   # overnight hole is not a gap
    assert doc["timeframes"]["5m"]["last_bar_utc"]
    path = str(tmp_path / "health.json")
    IH.publish(frames, path=path, warnings=["feed slow"])
    saved = json.load(open(path))
    assert saved["timeframes"]["5m"]["symbols"]["AAPL"]["n"] == 4 and saved["warnings"] == ["feed slow"]
    body = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "data_layer.py")).read()
    assert "intraday_health" in body and "IH.observe" in body
