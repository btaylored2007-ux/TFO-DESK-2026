"""Advisory feature store, catalysts, regime, earnings flag and hunt learning log. PAPER. No network, no live state."""
import datetime as dt, json, os
import hunt48_short_paper as H
from plan48.scanner import catalysts, data_quality, earnings_guard, gap_scan, learning, regime
from plan48.scanner.advise import annotate_rows
from plan48.scanner.feature_store import (build, parse_13f, parse_finra, parse_options, parse_surprises, parse_volume, read_borrow)
from plan48.scanner.vendors import Client, redact
UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 30, 20, 0, tzinfo=UTC)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEEP = ("score", "grade", "floors_failed", "wake_ok", "stop", "k_atr", "earnings_block")

def _client(get):
    return Client(get=get, min_interval=0, sleep=lambda s: None, now=lambda: NOW)

def _bars(n=25, last_open=130.0):
    rows = []
    for i in range(n):
        c = 100 + i
        rows.append(dict(d=(dt.date(2026, 9, 1) + dt.timedelta(days=i)).isoformat(), o=c, h=c + 1, l=c - 1, c=c, v=1000))
    rows[-1]["o"] = last_open
    rows[-1]["c"] = max(rows[-1]["c"], last_open)
    rows[-1]["h"] = rows[-1]["c"] + 1
    return rows

def _fred(series, last):
    lines = ["DATE,%s" % series]
    for i in range(3):
        lines.append("2026-10-%02d,%s" % (27 + i, last))
    return "\n".join(lines) + "\n"

def _happy(provider, url, headers, method, body):
    if "calendar/earnings" in url: return {"earnings": [{"code": "AAPL.US", "report_date": "2026-11-01"}]}
    if "/earnings?symbol=" in url: return [{"date": "2026-10-28", "eps": 1.2, "epsEstimated": 1.0}]
    if "/grades?" in url: return [{"publishedDate": "2026-10-30T12:00:00+00:00", "previousGrade": "Hold", "newGrade": "Buy"}]
    if "analyst-estimates" in url: return []
    if "price-target-consensus" in url: return {"targetConsensus": 210, "publishedDate": "2026-10-30"}
    if "consolidatedShortInterest" in url: return [{"settlementDate": "2026-10-15", "currentShortPositionQuantity": 1000, "previousShortPositionQuantity": 900, "daysToCoverQuantity": 2.1}]
    if "/insider/" in url: return [{"transactionCode": "P", "transactionDate": "2026-10-28", "reportingName": "Ada", "securitiesTransacted": 10},
                                   {"transactionCode": "P", "transactionDate": "2026-10-27", "reportingName": "Bea", "securitiesTransacted": 5}]
    if "symbol-positions-summary" in url: return [{"date": "2026-06-30", "shares": 80}, {"date": "2026-09-30", "shares": 100}]
    if "historical-sp500" in url: return [{"dateAdded": "2026-10-20", "addedSecurity": "AAPL"}]
    if "/range/1/day/" in url:
        base = dt.datetime(2026, 9, 1, tzinfo=UTC)
        return {"results": [{"t": int((base + dt.timedelta(days=i)).timestamp() * 1000), "o": 100, "h": 101, "l": 99, "c": 100, "v": 1000 if i < 20 else 3000} for i in range(21)]}
    if "/range/1/minute/" in url:
        ts = int(dt.datetime(2026, 10, 30, 12, 0, tzinfo=UTC).timestamp() * 1000)
        return {"results": [{"t": ts, "o": 140, "h": 141, "l": 139, "c": 140, "v": 8000}]}
    if "options-volume" in url: return {"call_volume": 100, "put_volume": 40, "date": "2026-10-30T19:00:00+00:00"}
    if "flow-recent" in url: return [{"ticker": "AAPL", "type": "call"}]
    if "darkpool" in url: return [{"ticker": "AAPL", "size": 10}]
    if "congress" in url: return []
    if "/profile?" in url: return [{"sector": "Technology"}]
    if "open-interest" in url: return [{"symbol": "BTCUSDT_PERP.A", "value": 1e9}]
    if "funding-rate" in url: return [{"symbol": "BTCUSDT_PERP.A", "value": 0.0001}]
    if "liquidation-history" in url: return [{"symbol": "BTCUSDT_PERP.A", "history": [{"l": 5, "s": 7}]}]
    if "long-short-ratio" in url: return [{"symbol": "BTCUSDT_PERP.A", "history": [{"r": 1.2}]}]
    if "petroleum" in url: return {"response": {"data": [{"period": "2026-10-24", "value": 420}]}}
    if "natural-gas" in url: return {"response": {"data": [{"period": "2026-10-24", "value": 3500}]}}
    if "id=VIXCLS" in url: return _fred("VIXCLS", 18)
    if "id=VXVCLS" in url: return _fred("VXVCLS", 22)
    if "id=BAMLH0A0HYM2" in url: return _fred("BAMLH0A0HYM2", 3.2)
    if "id=DTWEXBGS" in url: return _fred("DTWEXBGS", 120)
    if "id=DGS10" in url: return _fred("DGS10", 4.1)
    if "id=DGS2" in url: return _fred("DGS2", 3.8)
    return []

def _panel():
    return {"N%d" % i: [100.0] * 50 + [110.0] for i in range(30)}

def _store():
    bars = {"AAPL": _bars(), "SPY": _bars(last_open=100), "XLK": _bars(last_open=100)}
    borrow = {"updated_utc": "2026-10-30T18:00:00+00:00", "us": {"names": {"AAPL": {"ok": True, "available": 10000, "fee_pa": 0.003}}}}
    return build(["AAPL", "KRAKEN:XBTUSD", "CL=F"], client=_client(_happy), now=NOW, bars=bars, borrow=borrow,
                 panel=_panel(), guidance={"AAPL": {"change": "up", "date": "2026-10-30T12:00:00+00:00"}})

def test_missing_is_null_and_stale_cache_keeps_value():
    blank = parse_surprises([], "fmp:earnings", NOW)
    assert blank["status"] == "missing" and blank["value"] is None and blank["blocks_trade"] is False
    secret = "SUPERSECRET12345678"
    state = {"n": 0}
    def get(provider, url, headers, method, body):
        state["n"] += 1
        if state["n"] == 1: return [{"date": "2026-10-28", "eps": 2.0, "epsEstimated": 1.0}]
        raise RuntimeError("boom apikey=" + secret)
    c = Client(get=get, keys={"fmp": secret}, min_interval=0, sleep=lambda s: None, now=lambda: NOW)
    url = "https://financialmodelingprep.com/stable/earnings?symbol=AAPL"
    first, second = c.call("fmp", url), c.call("fmp", url)
    assert first["ok"] and not first["stale"] and second["stale"] and second["body"][0]["eps"] == 2.0
    assert secret not in second["error"] and "apikey=***" in second["error"]
    assert redact("apikey=" + secret, secret).count(secret) == 0

def test_paid_fields_fresh_and_free_fallbacks_present():
    doc = _store()
    assert doc["blocks_trade"] is False and doc["advisory"] is True
    a = doc["tickers"]["AAPL"]
    assert a["earnings_surprises"]["status"] == "fresh" and a["earnings_surprises"]["value"][-1]["surprise_pct"] == 0.2
    assert a["earnings_surprises"]["source"].startswith("fmp")
    assert a["estimate_revisions"]["value"]["direction"] == "up" and a["estimate_revisions"]["value"]["within_48h"]
    assert a["short_interest"]["source"].startswith("finra") and a["short_interest"]["value"]["shares"] == 1000
    assert a["ibkr_borrow"]["value"]["available"] == 10000 and a["ibkr_borrow"]["status"] == "fresh"
    assert a["insider_buys"]["value"]["distinct_buyers"] == 2
    assert a["form_13f_change"]["value"]["share_change"] == 20
    assert a["unusual_volume"]["value"]["ratio_vs_20d"] == 3.0 and a["unusual_volume"]["source"].startswith("massive")
    assert a["options"]["value"]["put_call"] == 0.4 and a["options"]["source"].startswith("unusual_whales")
    assert a["rs_index"]["value"]["bench"] == "SPY" and a["rs_sector"]["value"]["bench"] == "XLK"
    assert a["next_earnings"]["value"] == "2026-11-01" and a["next_earnings"]["source"].startswith("eodhd")
    assert doc["tickers"]["KRAKEN:XBTUSD"]["crypto_derivs"]["source"] == "coinalyze"
    assert doc["tickers"]["KRAKEN:XBTUSD"]["crypto_derivs"]["value"]["liq_long_usd"] == 5
    assert doc["energy_inventories"]["value"]["crude"]["value"] == 420
    assert doc["regime"]["vix_term"]["value"]["shape"] == "contango"
    assert doc["regime"]["breadth"]["value"]["pct_above_50"] == 100.0
    assert doc["regime"]["breadth"]["value"]["pct_above_200"] is None
    assert doc["regime"]["credit_hy_oas"]["source"].startswith("fred:BAML")
    for seat in regime.SEAT_LENS:
        tag = regime.tag_for(seat, doc["regime"])
        assert tag["tag"] == "risk-on" and tag["blocks_trade"] is False and tag["seat"] == seat

def test_one_period_13f_volume_and_borrow_stay_missing():
    snap, chg = parse_13f([{"date": "2026-09-30", "shares": 10}], NOW)
    assert snap["value"]["shares"] == 10 and chg["status"] == "missing" and chg["value"] is None
    assert parse_volume([{"d": "2026-10-01", "c": 1, "v": 5}], "bars", NOW)["value"] is None
    assert parse_options({"call_volume": 0, "put_volume": 10}, "uw", NOW)["status"] == "missing"
    assert parse_finra([], NOW)["status"] == "missing"
    miss = read_borrow("MSFT", {"updated_utc": NOW.isoformat(), "us": {"names": {"AAPL": {"ok": True, "available": 1}}}}, NOW)
    assert miss["status"] == "missing" and miss["value"] is None

def test_breadth_under_thirty_names_is_missing():
    small = regime.breadth({"A": [1, 2, 3]}, NOW)
    assert small["status"] == "missing" and small["value"] is None

def test_gap_scanner_and_relative_strength():
    session = dt.date(2026, 10, 30)
    ts = int(dt.datetime(2026, 10, 30, 12, 0, tzinfo=UTC).timestamp() * 1000)
    got = gap_scan.from_minutes([{"t": ts, "o": 110, "c": 111, "v": 500}], 100, None, session, NOW)
    assert got["value"]["gap"] == 0.1 and got["value"]["premarket_volume"] == 500 and got["value"]["rvol_vs_20d"] is None
    assert got["blocks_trade"] is False
    empty = gap_scan.from_minutes([], 100, 10, session, NOW)
    assert empty["status"] == "missing"

def test_detectors_are_candidates_with_r_levels_and_quality_skips_gaps():
    doc = _store()
    feats = doc["tickers"]["AAPL"]
    price = dict(entry=100.0, atr=2.0, atr_pct=0.02, gap=feats["gap"]["value"]["gap"], gap_hold=feats["gap"]["value"]["gap_hold"])
    found = {c["setup"]: c for c in catalysts.detect("AAPL", feats, price, NOW)}
    assert {"pead_beat_raise", "gap_and_go", "index_add", "insider_cluster_buy"} <= set(found)
    for c in found.values():
        assert c["stop_r"] == 1.0 and c["target_r"] == 2.0 and c["blocks_trade"] is False and c["applied_to_rail"] is False
        assert c["feeds_scorer"] == "candidate_only"
        if c["side"] == "LONG": assert c["stop"] == 98.0 and c["target"] == 104.0
    q = found["pead_beat_raise"]["quality"]["value"]
    assert "surprise" in q["components"] and "guidance" in q["components"] and q["score"] is not None
    bare = catalysts.quality({}, "LONG", NOW)
    assert bare["status"] == "missing" and bare["value"] is None
    assert catalysts.detect("AAPL", {}, price, NOW) == []

def test_earnings_flag_and_annotate_do_not_touch_rails():
    doc = _store()
    flags = earnings_guard.scan([dict(symbol="AAPL", role="candidate"), dict(symbol="MSFT", role="open")], doc["earnings_calendar"], NOW)
    by = {f["symbol"]: f for f in flags}
    assert by["AAPL"]["within_2d"] is True and by["AAPL"]["blocks_trade"] is False
    assert by["MSFT"]["status"] == "missing" and by["MSFT"]["within_2d"] is None and by["MSFT"]["blocks_trade"] is False
    row = dict(sym="AAPL", side="LONG", score=91, grade="A+", floors_failed=[], wake_ok=True, stop=9, k_atr=1.2, earnings_block=None, close=100, atr_pct=0.02)
    before = {k: row[k] for k in KEEP}
    tag = annotate_rows("energy", [row], store=doc, now=NOW)
    assert {k: row[k] for k in KEEP} == before
    assert tag["blocks_trade"] is False and row["regime_tag"]["tag"] == "risk-on"
    assert row["earnings_flag"]["within_2d"] is True and row["earnings_flag"]["blocks_trade"] is False
    assert row["catalyst_candidates"] and all(c["blocks_trade"] is False for c in row["catalyst_candidates"])
    assert row["relative_strength"]["blocks_trade"] is False

def test_closed_trades_shadow_ranks_and_weekly_attribution(tmp_path, monkeypatch):
    path = str(tmp_path / "closed_trades.jsonl")
    monkeypatch.setenv("TFO_HUNT_CLOSED", path)
    ledger = {"trades": [{"ticker": "AAA", "shadow_rank": 4, "shadow_entry": 10.5, "status": "SHADOW"},
                         {"ticker": "BBB", "shadow_rank": 10, "entry": 3.25, "status": "SHADOW"},
                         {"ticker": "CCC", "shadow_rank": 3, "entry": 1, "status": "SHADOW"},
                         {"ticker": "DDD", "shadow_rank": 11, "entry": 1, "status": "SHADOW"},
                         {"ticker": "EEE", "shadow_rank": 7, "status": "SHADOW"}]}
    win = dict(id=1, ticker="AAPL", entry=100, status="CLOSED", exit_reason="time exit", seat="energy", book="main",
               signals=["pead_beat_raise"], pnl_aud=250, exits=[dict(px=110)])
    loss = dict(id=2, ticker="XYZ", entry=20, status="STOPPED", exit_reason="stop", seat="energy", book="main",
                signals=["gap_and_go"], pnl_aud=-80, exits=[dict(px=22)])
    blank = dict(id=3, ticker="QQQ", entry=50, status="CLOSED", exit_reason="time exit", seat="banks", exits=[dict(px=50)])
    assert learning.record_close(win, ledger, NOW, ["time_exit"])["ok"]
    learning.record_close(loss, ledger, NOW, ["stopped"])
    learning.record_close(blank, ledger, NOW, ["time_exit"])
    assert learning.record_close(win, ledger, NOW, ["time_exit"])["duplicate"] is True
    rows = [json.loads(x) for x in open(path)]
    assert [r["ticker"] for r in rows] == ["AAPL", "XYZ", "QQQ"]
    marks = rows[0]["shadow_ranks_4_10"]
    assert [m["shadow_rank"] for m in marks] == [4, 7, 10]
    assert marks[0] == dict(shadow_rank=4, ticker="AAA", entry=10.5, entry_missing=False)
    assert marks[1]["ticker"] == "EEE" and marks[1]["entry"] is None and marks[1]["entry_missing"] is True
    assert marks[2]["ticker"] == "BBB" and marks[2]["entry"] == 3.25
    assert "shadow_ranks_4_10" not in rows[1]
    assert rows[2]["pnl_missing"] is True
    W = learning.weekly(path)
    assert W["auto_apply"] is False and W["blocks_trade"] is False and W["closed_trades"] == 3
    assert W["source"].endswith("closed_trades.jsonl") and W["shadow_marked"] == 2
    attr = W["attribution"]["energy"]
    assert attr["pead_beat_raise"]["winners"] == 1 and attr["gap_and_go"]["losers"] == 1
    assert W["attribution"]["banks"]["(no signal recorded)"]["unknown"] == 1
    assert W["attribution"]["banks"]["(no signal recorded)"]["losers"] == 0
    summary = learning.hunt_summary(path)
    assert summary["closed_trades"] == 3 and summary["blocks_trade"] is False

def test_weekly_self_tune_reads_the_hunt_log(tmp_path, monkeypatch):
    from veteran_desk import learning as L
    old = (L.LD, L.REP, L.WEEKLY, L.BOOK, L.trader_seats)
    path = str(tmp_path / "closed_trades.jsonl")
    monkeypatch.setenv("TFO_HUNT_CLOSED", path)
    try:
        L.LD = str(tmp_path / "learning"); L.REP = str(tmp_path / "rep"); L.WEEKLY = str(tmp_path / "rep" / "w.json")
        L.BOOK = str(tmp_path / "book.json"); L.trader_seats = lambda: ["energy"]
        json.dump(dict(seats=dict(energy=dict(positions={}, closed=[]))), open(L.BOOK, "w"))
        learning.record_close(dict(id=9, ticker="AAPL", entry=1, status="CLOSED", exit_reason="time exit", seat="energy",
                                   signals=["pead_beat_raise"], pnl_aud=10, exits=[dict(px=2)]), {"trades": []}, NOW, ["time_exit"], path)
        out = L.weekly(use_llm=False)
        assert out["auto_apply"] is False and out["hunt_scanner"]["closed_trades"] == 1
        assert out["hunt_scanner"]["blocks_trade"] is False
        assert out["hunt_scanner"]["attribution"]["energy"]["pead_beat_raise"]["winners"] == 1
    finally:
        L.LD, L.REP, L.WEEKLY, L.BOOK, L.trader_seats = old

def test_data_quality_file_counts_nulls(tmp_path):
    doc = _store()
    state, static = str(tmp_path / "state" / "data_quality.json"), str(tmp_path / "static" / "data_quality.json")
    data_quality.write(doc["data_quality"], state, static)
    saved = json.load(open(state))
    assert saved["blocks_trade"] is False and saved["advisory"] is True
    assert json.load(open(static))["feeds"]
    assert saved["feeds"]["fmp"]["fresh"] >= 1
    assert any(v["missing"] >= 1 for v in saved["feeds"].values())

def test_package_has_no_coinglass_yahoo_or_risk_plan():
    text = "\n".join(open(os.path.join(dp, f)).read() for dp, _, fs in os.walk(os.path.join(ROOT, "plan48")) for f in fs if f.endswith(".py"))
    low = text.lower()
    assert "coinglass" not in low and "yfinance" not in low and "finance.yahoo.com" not in low
    assert "risk_plan(" not in text

def _trade(**k):
    t = dict(direction="short", status="FILLED", entry=10.0, entry_utc="2026-10-26T18:00:00+00:00", stop=11.0, trigger=10.0,
             t1=9.0, t2=8.5, atr=0.4, open_qty=100, half_done=False, trail=None, lowest=10.0, ext_days=0, realised_usd=0.0,
             fees_usd=0.0, exits=[], last_px=10.5, time_exit_utc="2026-10-30T19:55:00+00:00", ticker="X", shares=100)
    t.update(k); return t

def test_chaos_every_vendor_dead_trades_and_exits_still_flow(tmp_path, monkeypatch):
    def dead(provider, url, headers, method, body):
        raise RuntimeError("%s down" % provider)
    doc = build(["AAPL", "KRAKEN:XBTUSD", "CL=F", "BHP.AX"], client=_client(dead), now=NOW)
    assert doc["blocks_trade"] is False

    def walk(node):
        if isinstance(node, dict):
            if "blocks_trade" in node: assert node["blocks_trade"] is False
            if "status" in node: assert node["status"] in ("missing", "stale")
            for v in node.values(): walk(v)
        elif isinstance(node, list):
            for v in node: walk(v)
    walk(doc["tickers"]); walk(doc["regime"]); walk(doc["energy_inventories"])
    assert doc["tickers"]["AAPL"]["earnings_surprises"]["value"] is None
    assert doc["tickers"]["KRAKEN:XBTUSD"]["crypto_derivs"]["status"] == "missing"
    assert "coinglass" not in json.dumps(doc).lower()
    row = dict(sym="AAPL", side="LONG", score=88, grade="A", floors_failed=["no seat signal (0/1)"], wake_ok=False, stop=9, close=10)
    before = {k: row[k] for k in ("score", "grade", "floors_failed", "wake_ok", "stop")}
    tag = annotate_rows("banks", [row], store=doc, now=NOW)
    assert {k: row[k] for k in before} == before
    assert tag["tag"] == "unknown" and tag["blocks_trade"] is False
    assert row["catalyst_candidates"] == [] and row["earnings_flag"]["blocks_trade"] is False
    stop = _trade(time_exit_utc="2026-11-20T20:00:00+00:00", last_px=10.0)
    assert "stopped" in H.manage(stop, [dict(start=dt.datetime(2026, 10, 27, 15, 0, tzinfo=UTC), o=10.9, h=11.2, l=10.8, c=11.1)], NOW)
    assert stop["status"] == "STOPPED" and stop["open_qty"] == 0
    timed = _trade()
    assert "time_exit" in H.manage(timed, [], dt.datetime(2026, 10, 31, 21, 0, tzinfo=UTC))
    assert timed["status"] == "CLOSED" and timed["open_qty"] == 0
    path = str(tmp_path / "closed_trades.jsonl")
    monkeypatch.setenv("TFO_HUNT_CLOSED", path)
    frozen = json.dumps(timed, sort_keys=True, default=str)
    H._learn_close(timed, {"trades": [{"ticker": "AAA", "shadow_rank": 4, "shadow_entry": 8, "status": "SHADOW"}]}, NOW, ["time_exit"])
    assert json.dumps(timed, sort_keys=True, default=str) == frozen
    logged = json.loads(open(path).readline())
    assert logged["ticker"] == "X" and logged["entry"] == 10.0 and logged["blocks_trade"] is False
    assert logged["shadow_ranks_4_10"] == [dict(shadow_rank=4, ticker="AAA", entry=8, entry_missing=False)]
