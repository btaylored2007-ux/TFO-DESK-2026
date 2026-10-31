"""Advisory catalyst stream. No live state, no network. A dead vendor must not stop a paper arm or a stop."""
import datetime as dt, inspect, json, os
import pytest
from veteran_desk import catalyst_stream as CS
import funder_gate as FG
from veteran_desk import book as BK

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 31, 2, 0, tzinfo=UTC)
SECRET = "supersecretkey"
T = dt.datetime(2026, 10, 28, 18, 45, tzinfo=UTC)

@pytest.fixture(autouse=True)
def _root(tmp_path, monkeypatch):
    monkeypatch.setattr(CS, "ROOT", str(tmp_path))
    monkeypatch.setenv("CATALYST_NO_NETWORK", "1")

def _key(fname, var, only="fmp"):
    return SECRET if only in str(fname) else None

def _cycle(get, **k):
    k.setdefault("now", NOW)
    k.setdefault("key_fn", lambda f, v: _key(f, v))
    return CS.cycle(get=get, **k)

def _empty(url, headers, timeout):
    if "company_tickers" in url: return 200, b"{}"
    if "browse-edgar" in url and "type=4" in url:
        return 200, b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    if any(s in url for s in ("asx.com.au", "investegate", "hkex", "yanoshin", "fdacalendar", "bbci", "federalreserve", "ecb.europa", "bankofengland", "rba.gov", "bankofcanada", "faireconomy")):
        return 200, b"<rss></rss>"
    return 200, b"[]"

def test_routes_ticker_and_wakes_on_halt():
    def get(url, headers, timeout):
        if "stock-latest" in url:
            return 200, json.dumps([
                {"symbol": "XOM", "publishedDate": "2026-10-31 01:59:30", "title": "XOM raises dividend", "url": "http://n/1"},
                {"symbol": "XOM", "publishedDate": "2026-10-31 01:59:40", "title": "XOM trading halt", "url": "http://n/2"},
            ]).encode()
        return _empty(url, headers, timeout)
    r = _cycle(get)
    assert r["ok"] and r["blocks_trade"] is False and r["advisory_only"] is True
    assert SECRET not in json.dumps(r)
    calm = [x for x in CS.tail(CS._p("queues", "energy.jsonl"), 20) if "dividend" in x["title"]]
    halt = [x for x in CS.tail(CS._p("queues", "energy.jsonl"), 20) if "halt" in x["title"]]
    assert calm and set(calm[0]["seats"]) == {"energy", "news_filings", "price_action", "trend"}
    assert calm[0]["urgency"] < CS.WAKE_AT and calm[0]["advisory_only"] and calm[0]["blocks_feed"] is False
    assert halt and halt[0]["urgency"] >= CS.WAKE_AT
    wakes = CS.tail(CS._p("wake", "energy.jsonl"), 10)
    assert any("halt" in w["title"] for w in wakes) and not any("dividend" in w["title"] for w in wakes)
    assert CS.for_seat("energy")["items"]

def test_dedupe_and_backoff_skip_the_next_cycle():
    calls = []
    def get(url, headers, timeout):
        calls.append(url)
        if "stock-latest" in url: return 429, b""
        return _empty(url, headers, timeout)
    _cycle(get)
    n1 = sum("stock-latest" in u for u in calls)
    r2 = _cycle(get)
    n2 = sum("stock-latest" in u for u in calls)
    assert n1 == 1 and n2 == 1 and r2["vendors"]["fmp_news"]["status"] == "backoff"
    assert "supersecretkey" not in json.dumps(CS.load_json(CS._p("backoff.json"), {}))

def test_free_wire_is_fallback_and_form4_still_runs():
    calls = []
    def get(url, headers, timeout):
        calls.append(url)
        if "stock-latest" in url or "press-releases" in url: return 429, b""
        if "bbci.co.uk" in url:
            return 200, b"<rss><channel><item><title>Markets open quietly</title><link>http://n/b</link><pubDate>Fri, 31 Oct 2026 01:50:00 GMT</pubDate></item></channel></rss>"
        if "type=4" in url:
            return 200, b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>4 - JPMORGAN CHASE (0000019617) (Issuer)</title><updated>2026-10-31T01:50:00Z</updated><summary>Form 4</summary></entry></feed>'
        if "type=8-K" in url: raise AssertionError("edgar 8-K must wait until FMP 8-K fails")
        return _empty(url, headers, timeout)
    r = _cycle(get)
    assert r["vendors"]["rss_newswire"]["status"] == "ok" and r["vendors"]["rss_newswire"]["n"] >= 1
    assert any("type=4" in u for u in calls)
    banks = CS.tail(CS._p("queues", "banks.jsonl"), 20)
    assert any(x["kind"] == "form4" and "JPM" in x["tickers"] for x in banks)

def test_paid_headline_suppresses_rss_and_failed_8k_uses_edgar():
    calls = []
    def get(url, headers, timeout):
        calls.append(url)
        if "stock-latest" in url:
            return 200, json.dumps([{"symbol": "JPM", "publishedDate": "2026-10-31 01:59:00", "title": "JPM sets buyback", "url": "http://n/j"}]).encode()
        if "sec-filings" in url: return 500, b""
        if "type=8-K" in url:
            return 200, b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>8-K - EXXON (0000034088) (Filer)</title><updated>2026-10-31T01:40:00Z</updated><summary>Item 2.02 Results</summary></entry></feed>'
        if "bbci.co.uk" in url: raise AssertionError("rss is not the fallback while a paid headline vendor is up")
        return _empty(url, headers, timeout)
    r = _cycle(get)
    assert r["vendors"]["fmp_news"]["status"] == "ok"
    assert "rss_newswire" not in r["vendors"]
    assert r["vendors"]["edgar_8k"]["status"] == "ok"
    q = CS.tail(CS._p("queues", "energy.jsonl"), 20)
    assert any(x["kind"] == "8k" and "2.02" in x["items"] and x["urgency"] >= CS.WAKE_AT for x in q)

def test_disclosures_cb_speakers_and_pdufa():
    def get(url, headers, timeout):
        if "investegate" in url:
            return 200, b"<rss><channel><item><title>BP.L - Director shareholding</title><link>http://r/l</link><pubDate>Fri, 31 Oct 2026 01:40:00 GMT</pubDate></item></channel></rss>"
        if "hkex" in url:
            return 200, b"<rss><channel><item><title>TENCENT (00700) results</title><pubDate>Fri, 31 Oct 2026 01:40:00 GMT</pubDate></item></channel></rss>"
        if "yanoshin" in url:
            return 200, b"<rss><channel><item><title>7203 Toyota production update</title><pubDate>Fri, 31 Oct 2026 01:40:00 GMT</pubDate></item></channel></rss>"
        if "fdacalendar" in url:
            return 200, b"(|LLY|NYSE)|Example drug name|11/15/2026|notes|pending"
        if "economic-calendar" in url:
            return 200, json.dumps([
                {"date": "2026-10-31 01:30:00", "event": "Fed Interest Rate Decision", "impact": "High", "currency": "USD"},
                {"date": "2026-11-02 16:00:00", "event": "FOMC Member Waller Speaks", "impact": "Medium", "currency": "USD"},
            ]).encode()
        return _empty(url, headers, timeout)
    r = _cycle(get, key_fn=lambda f, v: SECRET if "fmp" in str(f) else None)
    news = CS.tail(CS._p("queues", "news_filings.jsonl"), 30)
    assert any(x["kind"] == "rns" and "BP.L" in x["tickers"] for x in news)
    assert any(x["kind"] == "hkex" and "700.HK" in x["tickers"] for x in news)
    assert any(x["kind"] == "tdnet" and "7203.T" in x["tickers"] for x in news)
    health = CS.tail(CS._p("queues", "healthcare.jsonl"), 20)
    assert any(x["kind"] == "pdufa" and x["tickers"] == ["LLY"] for x in health)
    macro = CS.tail(CS._p("queues", "macro.jsonl"), 20)
    assert any(x["kind"] == "cb_decision" for x in macro) and any(x["kind"] == "cb_speaker" for x in macro)
    assert r["vendors"]["fmp_economic"]["status"] == "ok"
    assert "cb_calendar" not in r["vendors"]

def test_sentiment_and_p95_alert():
    assert CS.score_sentiment({"title": "shares plunge on bankruptcy"}) < 0
    assert CS.score_sentiment({"title": "shares plunge", "vendor_sentiment": 1.0}) > CS.score_sentiment({"title": "shares plunge"})
    def get(url, headers, timeout):
        if "stock-latest" in url:
            rows = [{"symbol": "XOM", "publishedDate": "2026-10-31 01:58:00", "title": "XOM item %d" % i, "url": "http://n/%d" % i} for i in range(8)]
            return 200, json.dumps(rows).encode()
        return _empty(url, headers, timeout)
    r = _cycle(get)
    assert r["latency"]["alert"] is True and r["latency"]["p95_e2e_s"] > 60
    alert = CS.load_json(CS._p("latency_alert.json"), {})
    assert alert["alert"] is True and alert["blocks_trade"] is False
    row = next(x for x in CS.tail(CS._p("queues", "energy.jsonl"), 20) if str(x.get("title", "")).startswith("XOM item"))
    assert row["e2e_s"] is not None and row["e2e_s"] > 60 and row["hop_s"] == 0 and "seat_utc" in row

def test_eia_release_skips_a_holiday_week():
    plain = CS.next_eia(dt.datetime(2026, 11, 3, 12, 0, tzinfo=UTC))
    assert plain == dt.datetime(2026, 11, 4, 15, 30, tzinfo=UTC)
    shifted = CS.next_eia(dt.datetime(2027, 1, 19, 15, 0, tzinfo=UTC))
    assert shifted == dt.datetime(2027, 1, 21, 15, 30, tzinfo=UTC)

def test_attach_swallows_a_broken_reader(monkeypatch):
    real = CS.for_seat
    monkeypatch.setattr(CS, "for_seat", lambda s: (_ for _ in ()).throw(RuntimeError("queue unreadable")))
    assert CS.attach({}, "energy")["catalyst_stream_note"] == "unavailable"
    monkeypatch.setattr(CS, "for_seat", real)
    CS._append(CS._p("queues", "macro.jsonl"), {"title": "Fed speaks", "advisory_only": True})
    got = CS.attach({}, "macro")
    assert got["catalyst_stream"]["advisory_only"] and got["catalyst_stream"]["items"][0]["title"] == "Fed speaks"

def test_daemon_one_cycle_survives_dead_vendors():
    def boom(url, headers, timeout): raise RuntimeError("down")
    CS.daemon(cycles=1, sleep=lambda s: (_ for _ in ()).throw(AssertionError("should not sleep")))
    # daemon() calls cycle() with the pytest network guard, which fails vendors and still writes a heartbeat
    hb = CS.status()
    assert hb["ok"] is True and hb["blocks_trade"] is False

def test_source_has_no_trade_path():
    src = inspect.getsource(CS)
    assert "book.tick" not in src and "vd_engine" not in src and "funder_queue" not in src

def test_chaos_vendors_down_trades_and_exits_still_flow(tmp_path, monkeypatch):
    secret = SECRET
    calls = []
    def boom(url, headers, timeout):
        calls.append(url)
        raise RuntimeError("apikey=%s vendor dead" % secret)
    r = CS.cycle(now=NOW, get=boom, key_fn=lambda f, v: secret)
    blob = json.dumps(r) + json.dumps(CS.load_json(CS._p("heartbeat.json"), {})) + json.dumps(CS.load_json(CS._p("backoff.json"), {}))
    assert r["ok"] and r["blocks_trade"] is False and r["blocks_feed"] is False
    assert secret not in blob
    for name, v in r["vendors"].items():
        if name in ("eia_rule", "calendar_earnings", "calendar_macro"): continue
        assert v["status"] in ("fail", "backoff", "inactive"), (name, v)
    n_calls = len(calls)
    path = str(tmp_path / "book.json"); q = str(tmp_path / "q.json")
    D = dict(seats={s: BK._seat_ledger() for s in BK.TRADERS}, capital_events=[], hedge={"disabled": True},
             kill_switch={"state": "OK"}, peak_equity_aud=None, seed_aud=BK.SEED_AUD, paper_only=True)
    json.dump(D, open(path, "w"))
    entry = dict(id="A", book="veteran", ticker="BAC", side="LONG", entry=40.0, stop=39.0, target=44.0, state=FG.WAIT,
                 decision="approve", grade="A", tags=dict(strategy="veteran:banks grade A"))
    json.dump({"entries": {"A": entry}}, open(q, "w"))
    old = (BK.BOOK, BK.firm_ok, BK.session_open, BK.posture, BK.now, BK.NEWS_FN, BK.fallback_quote, BK.ATR_FN, BK.BARS_FN)
    try:
        BK.BOOK = path
        BK.firm_ok = lambda: (True, "OK")
        BK.session_open = lambda v, t: False
        BK.posture = lambda: ("calm", "risk-off")
        BK.now = lambda: T
        BK.NEWS_FN = lambda p: (_ for _ in ()).throw(RuntimeError("news reader dead"))
        BK.fallback_quote = lambda s: None
        BK.ATR_FN = lambda s: None
        BK.BARS_FN = lambda s, c=None: None
        armed = BK.tick(price_fn=lambda s: dict(last=40.1, hi=40.2, lo=40.0, asof=T.isoformat()), au=0.65, queue_path=q)
        assert armed["seats"]["banks"]["positions"]["A"]["status"] == "armed"
        D2 = json.load(open(path))
        D2["seats"]["banks"]["positions"] = {}
        D2["seats"]["energy"]["positions"]["P1"] = dict(id="P1", seat="energy", ticker="XLE", side="LONG", entry=100.0, fill=100.0,
            stop=98.0, target=110.0, shares=100, risk_aud=200.0, status="open", venue="US", sleeve="core", cost_aud=10.0, grade="A",
            filled_utc=(T - dt.timedelta(hours=1)).isoformat(), off_hours_checked=True)
        json.dump(D2, open(path, "w"))
        json.dump({"entries": {}}, open(q, "w"))
        stopped = BK.tick(price_fn=lambda s: dict(last=97.0, hi=97.5, lo=96.5, asof=T.isoformat()), au=0.70, queue_path=q)
        assert any(c.get("exit_reason") == "stop" and c.get("id") == "P1" for c in stopped["seats"]["energy"]["closed"])
    finally:
        BK.BOOK, BK.firm_ok, BK.session_open, BK.posture, BK.now, BK.NEWS_FN, BK.fallback_quote, BK.ATR_FN, BK.BARS_FN = old
    assert len(calls) == n_calls
