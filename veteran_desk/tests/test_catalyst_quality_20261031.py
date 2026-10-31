"""Story clustering, surprise, source scores, and Asia filings. Advisory only: a dead wire still lets a paper entry arm and a stop fire."""
import datetime as dt, inspect, json, os
import pytest
from veteran_desk import catalyst_quality as CQ
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
    monkeypatch.delenv("TFO_TRANSLATE_URL", raising=False)

def _empty(url, headers, timeout):
    if "company_tickers" in url: return 200, b"{}"
    if "browse-edgar" in url and "type=4" in url: return 200, b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    if any(s in url for s in ("asx.com.au", "investegate", "hkex", "yanoshin", "fdacalendar", "bbci", "federalreserve", "ecb.europa", "bankofengland", "rba.gov", "bankofcanada", "faireconomy")):
        return 200, b"<rss></rss>"
    return 200, b"[]"

def _cycle(get, keys=("fmp",)):
    def key(f, v):
        return SECRET if any(k in str(f) for k in keys) else None
    return CS.cycle(now=NOW, get=get, key_fn=key)

def _item(title, ticker, source, minute, kind="news"):
    t = (NOW - dt.timedelta(minutes=minute)).isoformat()
    it = dict(title=title, title_raw=title, tickers=[ticker] if ticker else [], source=source, source_utc=t, kind=kind, fetched_utc=NOW.isoformat())
    it["event"] = CQ.event_key(it)
    it["schedule"] = CQ.is_schedule(it)
    it["sig"] = CQ.signature(title)
    return it

def test_minhash_tracks_shingles_and_numbered_headlines_stay_apart():
    a = "exxon mobil corporation reported quarterly earnings that beat analyst estimates by a wide margin and raised its full year production outlook"
    b = "exxon mobil corp reported quarterly earnings that beat analyst estimates by a wide margin and raised full year production outlook"
    c = "the federal reserve left its policy rate unchanged and said inflation remains above target in the statement"
    near, far = CQ.minhash_jaccard(CQ.signature(a), CQ.signature(b)), CQ.minhash_jaccard(CQ.signature(a), CQ.signature(c))
    assert near > 0.45 and far < 0.15 and near > far
    assert CQ.title_similarity(a, b) >= CQ.J_TITLE
    assert CQ.distinct_series("XOM item 0", "XOM item 1")
    assert not CQ.same_story(_item("XOM item 0", "XOM", "fmp_news", 2), _item("XOM item 1", "XOM", "fmp_news", 2))

def test_same_story_clusters_wires_and_keeps_a_print_off_the_calendar():
    beat_a = _item("Exxon Mobil beats quarterly earnings estimates", "XOM", "fmp_news", 20)
    beat_b = _item("Exxon beats quarterly earnings estimates", "XOM", "eodhd_news", 5)
    halt_a = _item("XOM trading halt", "XOM", "fmp_news", 10)
    halt_b = _item("Exxon Mobil trading halt", "XOM", "massive_benzinga", 8)
    div = _item("XOM raises dividend", "XOM", "rss_bbc", 9)
    assert CQ.same_story(beat_a, beat_b)
    assert CQ.same_story(halt_a, halt_b)
    assert not CQ.same_story(halt_a, div)
    sched = _item("Earnings XOM BMO (FMP)", "XOM", "fmp_earnings", 30, kind="earnings")
    sched["schedule"] = True
    assert not CQ.same_story(sched, beat_a)
    late = _item("Exxon Mobil beats quarterly earnings estimates", "XOM", "uw_news", 400)
    assert not CQ.same_story(beat_a, late)
    filing = _item("8-K Exxon results", "XOM", "edgar_8k", 600, kind="8k")
    other = _item("8-K Exxon results", "XOM", "fmp_8k", 30, kind="8k")
    assert CQ.same_story(filing, other)

def test_cycle_keeps_one_canonical_story_and_wakes_once():
    def get(url, headers, timeout):
        if "stock-latest" in url:
            return 200, json.dumps([
                {"symbol": "XOM", "publishedDate": "2026-10-31 01:40:00", "title": "Exxon Mobil beats quarterly earnings estimates", "url": "http://n/f"},
                {"symbol": "XOM", "publishedDate": "2026-10-31 01:48:00", "title": "XOM trading halt", "url": "http://n/h"},
            ]).encode()
        if "eodhd.com/api/news" in url:
            return 200, json.dumps([{"title": "Exxon beats quarterly earnings estimates", "date": "2026-10-31T01:50:00Z", "symbols": ["XOM.US"], "link": "http://n/e", "sentiment": {"polarity": 0.4}}]).encode()
        if "benzinga" in url:
            return 200, json.dumps({"results": [{"title": "Exxon Mobil trading halt", "published_utc": "2026-10-31T01:49:00Z", "tickers": ["XOM"], "article_url": "http://n/b"}]}).encode()
        return _empty(url, headers, timeout)
    r = _cycle(get, keys=("fmp", "eodhd", "massive"))
    assert SECRET not in json.dumps(r)
    rows = CS.tail(CS._p("queues", "energy.jsonl"), 20)
    beats = [x for x in rows if "beat" in x["title"].lower()]
    halts = [x for x in rows if "halt" in x["title"].lower()]
    assert len(beats) == 1 and len(halts) == 1
    assert beats[0]["n_sources"] >= 2 and beats[0]["first_source_utc"].startswith("2026-10-31T01:40")
    assert {s["source"] for s in beats[0]["sources"]} >= {"fmp_news", "eodhd_news"}
    assert halts[0]["n_sources"] >= 2 and halts[0]["first_source"] == "fmp_news"
    wakes = [w for w in CS.tail(CS._p("wake", "energy.jsonl"), 10) if "halt" in w["title"].lower()]
    assert len(wakes) == 1 and not any("beat" in w["title"].lower() for w in CS.tail(CS._p("wake", "energy.jsonl"), 10))
    assert r["deduped"] >= 2 and beats[0]["advisory_only"] and beats[0]["blocks_trade"] is False
    _cycle(get, keys=("fmp", "eodhd", "massive"))
    assert len([x for x in CS.tail(CS._p("queues", "energy.jsonl"), 20) if "halt" in x["title"].lower()]) == 1
    assert len([w for w in CS.tail(CS._p("wake", "energy.jsonl"), 10) if "halt" in w["title"].lower()]) == 1

def test_surprise_lifts_a_real_miss_and_lets_a_routine_print_sleep():
    beat, _ = CQ.surprise(dict(title="XOM earnings", tickers=["XOM"], event="earnings", eps=2.4, eps_est=1.5, source="fmp_earnings"), NOW, {})
    inline, _ = CQ.surprise(dict(title="XOM earnings in line", tickers=["XOM"], event="earnings", eps=1.51, eps_est=1.50, source="fmp_news"), NOW, {})
    assert beat >= 75 and inline <= 40 and beat > inline
    halt = dict(kind="news", title="XOM trading halt", tickers=["XOM"], source="fmp_news", source_utc=NOW.isoformat(), event="trading_halt")
    div = dict(kind="news", title="XOM raises dividend", tickers=["XOM"], source="fmp_news", source_utc=NOW.isoformat(), event="dividend")
    halt["surprise"], _ = CQ.surprise(halt, NOW, {})
    div["surprise"], _ = CQ.surprise(div, NOW, {})
    assert CQ.event_prior(halt) > CQ.event_prior(div)
    assert CS.score_urgency(halt, NOW) >= CS.WAKE_AT
    assert CS.score_urgency(div, NOW) < CS.WAKE_AT
    sched = dict(kind="earnings", title="Earnings XOM BMO (FMP)", tickers=["XOM"], source="fmp_earnings", source_utc=NOW.isoformat(), schedule=True, event="earnings")
    sched["surprise"], _ = CQ.surprise(sched, NOW, {})
    assert CS.score_urgency(sched, NOW) < CS.WAKE_AT
    eight = dict(kind="8k", title="8-K EXXON", items=["2.02"], source="edgar_8k", source_utc=NOW.isoformat(), event="earnings", tickers=["XOM"])
    eight["surprise"], _ = CQ.surprise(eight, NOW, {})
    assert CS.score_urgency(eight, NOW) >= CS.WAKE_AT
    assert CQ.reaction_score([{"o": 100, "c": 100, "v": 100}] * 6 + [{"o": 100, "c": 104, "v": 900}] * 3) >= 90
    assert CQ.reaction_score([{"o": 10, "c": 10.01, "v": 100}] * 6) <= 25
    assert CQ.reaction_score([]) is None
    seen = {}
    novel, _ = CQ.surprise(dict(title="XOM brand new contract win", tickers=["XOM"], event="news", source="fmp_news"), NOW, {})
    CQ.remember(seen, dict(title="XOM brand new contract win", tickers=["XOM"], source_utc=(NOW - dt.timedelta(days=1)).isoformat()), NOW)
    again, parts = CQ.surprise(dict(title="XOM brand new contract win", tickers=["XOM"], event="news", source="rss_bbc"), NOW, seen)
    assert again < novel

def test_scores_propose_a_down_weight_only_after_thirty_days():
    def trade(source, net, hour):
        return dict(ticker="XOM", side="LONG", entry=100, stop=95, exit=110, risk_aud=500, net_aud=net, armed_utc="2026-10-31T02:00:00+00:00",
                    tags={"catalyst": {"items": [{"id": "c", "event": "earnings", "first_source": source, "first_source_utc": "2026-10-31T0%d:00:00+00:00" % hour,
                                                   "sources": [{"source": source}]}], "sources": [{"source": source}]}})
    weak = [trade("rss_bbc", -400, 1) for _ in range(10)]
    strong = [trade("fmp_news", 400, 1) for _ in range(10)]
    early = CQ.score_sources(weak + strong, NOW, ["2026-10-%02d" % d for d in range(1, 11)])
    assert early["proposals"] == [] and early["auto_apply"] is False
    days = ["2026-09-%02d" % d for d in range(1, 31)]
    late = CQ.score_sources(weak + strong, NOW, days)
    names = {p["name"] for p in late["proposals"]}
    assert "rss_bbc" in names and "fmp_news" not in names
    assert all(p["auto_apply"] is False and p["apply"] is False for p in late["proposals"])
    assert late["sources"]["fmp_news"]["hit_rate"] == 1.0
    assert late["sources"]["rss_bbc"]["avg_r"] < 0
    assert late["sources"]["fmp_news"]["median_lead_s"] > 0
    card = CQ.warroom_card(late)
    assert card["blocks_trade"] is False and card["auto_apply"] is False and card["rows"]
    out = CQ.nightly(trades=weak, now=NOW, root=CS.base(), days=days)
    saved = json.load(open(CS._p("source_scores.json")))
    assert saved["proposals"] and saved["auto_apply"] is False and out["blocks_trade"] is False
    assert not os.path.exists(os.path.join(CS.base(), "ledger.json"))

def test_a_proposed_down_weight_still_delivers_the_story():
    CQ._write(CS._p("source_scores.json"), {"proposals": [{"name": "fmp_news", "action": "down_weight", "auto_apply": False}], "auto_apply": False, "days_observed": 40})
    def get(url, headers, timeout):
        if "stock-latest" in url:
            return 200, json.dumps([{"symbol": "XOM", "publishedDate": "2026-10-31 01:50:00", "title": "XOM trading halt", "url": "http://n/h"}]).encode()
        return _empty(url, headers, timeout)
    r = _cycle(get)
    assert any("halt" in x["title"].lower() and x["urgency"] >= CS.WAKE_AT for x in CS.tail(CS._p("queues", "energy.jsonl"), 10))
    assert r["blocks_trade"] is False

def test_english_titles_and_asia_routing_fail_open():
    jp = CS.parse_tdnet(json.dumps({"items": [{"company_code": "7203", "title": "決算短信", "pubdate": "2026-10-31 10:00:00", "document_url": "http://t/1"}]}).encode())
    stamped = CS._stamp(jp[0], {}, NOW, NOW.isoformat())
    assert jp[0]["tickers"] == ["7203.T"] and "earnings" in stamped["title"].lower() and stamped["title_lang"] == "glossary"
    assert "news_filings" in stamped["seats"] and "asia_hunt_jp" in stamped["seats"]
    raw = "7203 あいうえお未登録見出し"
    title, how = CQ.english_title(raw, translate=lambda s: (_ for _ in ()).throw(RuntimeError("translator down")))
    assert title == raw and how == "untranslated"
    assert CQ.remote_translate("決算短信") is None
    hk = CS.parse_hkex(json.dumps({"result": json.dumps([{"STOCK_CODE": "00700", "TITLE": "Inside Information - Share Buyback", "DATE_TIME": "31/10/2026 16:30", "FILE_LINK": "/listedco/x.pdf"}])}).encode())
    hk_s = CS._stamp(hk[0], {}, NOW, NOW.isoformat())
    assert hk[0]["tickers"] == ["700.HK"] and hk[0]["link"].startswith("https://www1.hkexnews.hk/")
    assert {"news_filings", "asia_hunt_hk", "tech_ai_infra"} <= set(hk_s["seats"])
    zh = CS._stamp(dict(kind="hkex", title="停牌", tickers=["5.HK"], source="hkexnews", source_utc=NOW.isoformat()), {}, NOW, NOW.isoformat())
    assert "halt" in zh["title"].lower() and "macro" in zh["seats"] and "banks" in zh["seats"] and "asia_hunt_hk" in zh["seats"]
    calls = []
    def get(url, headers, timeout):
        calls.append(url)
        if "yanoshin" in url and url.endswith("json2?limit=40") or ("yanoshin" in url and "json2" in url):
            return 200, json.dumps({"items": [{"company_code": "8306", "title": "日銀 金融政策に関するお知らせ", "pubdate": "2026-10-31T01:10:00Z"}]}).encode()
        if "titleSearchServlet" in url:
            return 200, json.dumps([{"STOCK_CODE": "00883", "TITLE": "Profit warning on crude output", "DATE_TIME": "31/10/2026 09:00"}]).encode()
        return _empty(url, headers, timeout)
    r = _cycle(get)
    assert r["vendors"]["tdnet"]["status"] == "ok" and r["vendors"]["hkex_disclosure"]["status"] == "ok"
    jp_rows = CS.tail(CS._p("queues", "asia_hunt_jp.jsonl"), 10)
    assert jp_rows and "8306.T" in jp_rows[0]["tickers"] and "macro" in jp_rows[0]["seats"]
    assert any("883.HK" in x["tickers"] and "energy" in x["seats"] for x in CS.tail(CS._p("queues", "asia_hunt_hk.jsonl"), 10))
    asia = [u for u in calls if "yanoshin" in u or "titleSearchServlet" in u or "ncfeed" in u]
    _cycle(get)
    asia2 = [u for u in calls if "yanoshin" in u or "titleSearchServlet" in u or "ncfeed" in u]
    assert asia and len(asia2) == len(asia)

def test_attribute_tags_the_paper_entry(tmp_path):
    item = dict(title="XOM trading halt", title_raw="XOM trading halt", tickers=["XOM"], source="fmp_news", source_utc="2026-10-28T18:00:00+00:00",
                kind="news", event="trading_halt", surprise=90, urgency=93, seats=["energy"], fetched_utc="2026-10-28T18:00:00+00:00")
    c = CQ.new_cluster(item)
    c.pop("sig", None)
    CQ._write(CS._p("clusters.json"), {c["id"]: c})
    tag = CQ.attribute("XOM", T, "energy")
    assert tag["blocks_trade"] is False and tag["sources"][0]["source"] == "fmp_news" and tag["items"][0]["first_source_utc"].startswith("2026-10-28T18:00")
    path, q = str(tmp_path / "book.json"), str(tmp_path / "q.json")
    D = dict(seats={s: BK._seat_ledger() for s in BK.TRADERS}, capital_events=[], hedge={"disabled": True}, kill_switch={"state": "OK"}, peak_equity_aud=None, seed_aud=BK.SEED_AUD, paper_only=True)
    json.dump(D, open(path, "w"))
    entry = dict(id="A", book="veteran", ticker="XOM", side="LONG", entry=100.0, stop=98.0, target=110.0, state=FG.WAIT, decision="approve", grade="A", tags=dict(strategy="veteran:energy grade A"))
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
        armed = BK.tick(price_fn=lambda s: dict(last=100.1, hi=100.2, lo=100.0, asof=T.isoformat()), au=0.65, queue_path=q)
        pos = armed["seats"]["energy"]["positions"]["A"]
        assert pos["status"] == "armed" and pos["tags"]["catalyst"]["sources"][0]["source"] == "fmp_news"
    finally:
        BK.BOOK, BK.firm_ok, BK.session_open, BK.posture, BK.now, BK.NEWS_FN, BK.fallback_quote, BK.ATR_FN, BK.BARS_FN = old

def test_attribute_failure_still_arms(tmp_path, monkeypatch):
    monkeypatch.setattr(CQ, "attribute", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("lookup down")))
    path, q = str(tmp_path / "book.json"), str(tmp_path / "q.json")
    D = dict(seats={s: BK._seat_ledger() for s in BK.TRADERS}, capital_events=[], hedge={"disabled": True, "state": "OK"}, kill_switch={"state": "OK"}, peak_equity_aud=None, seed_aud=BK.SEED_AUD, paper_only=True)
    json.dump(D, open(path, "w"))
    entry = dict(id="B", book="veteran", ticker="BAC", side="LONG", entry=40.0, stop=39.0, target=44.0, state=FG.WAIT, decision="approve", grade="A", tags=dict(strategy="veteran:banks grade A"))
    json.dump({"entries": {"B": entry}}, open(q, "w"))
    old = (BK.BOOK, BK.firm_ok, BK.session_open, BK.posture, BK.now, BK.NEWS_FN, BK.fallback_quote, BK.ATR_FN, BK.BARS_FN)
    try:
        BK.BOOK = path
        BK.firm_ok = lambda: (True, "OK")
        BK.session_open = lambda v, t: False
        BK.posture = lambda: ("calm", "risk-off")
        BK.now = lambda: T
        BK.NEWS_FN = lambda p: None
        BK.fallback_quote = lambda s: None
        BK.ATR_FN = lambda s: None
        BK.BARS_FN = lambda s, c=None: None
        armed = BK.tick(price_fn=lambda s: dict(last=40.1, hi=40.2, lo=40.0, asof=T.isoformat()), au=0.65, queue_path=q)
        pos = armed["seats"]["banks"]["positions"]["B"]
        assert pos["status"] == "armed" and "catalyst" not in (pos.get("tags") or {})
    finally:
        BK.BOOK, BK.firm_ok, BK.session_open, BK.posture, BK.now, BK.NEWS_FN, BK.fallback_quote, BK.ATR_FN, BK.BARS_FN = old

def test_quality_module_has_no_trade_path():
    src = inspect.getsource(CQ) + inspect.getsource(CS)
    assert "book.tick" not in src and "vd_engine" not in src and "funder_queue" not in src

def test_chaos_vendors_down_trades_and_exits_still_flow(tmp_path):
    calls = []
    def boom(url, headers, timeout):
        calls.append(url)
        raise RuntimeError("apikey=%s vendor dead" % SECRET)
    r = CS.cycle(now=NOW, get=boom, key_fn=lambda f, v: SECRET)
    blob = json.dumps(r) + json.dumps(CS.load_json(CS._p("heartbeat.json"), {})) + json.dumps(CS.load_json(CS._p("backoff.json"), {}))
    assert r["ok"] and r["blocks_trade"] is False and r["blocks_feed"] is False and SECRET not in blob
    for name in ("fmp_news", "eodhd_news", "massive_news", "uw_news", "tdnet", "hkex_disclosure", "asx_announcements", "rss_newswire"):
        assert r["vendors"][name]["status"] in ("fail", "backoff", "inactive")
    n_calls = len(calls)
    path, q = str(tmp_path / "book.json"), str(tmp_path / "q.json")
    D = dict(seats={s: BK._seat_ledger() for s in BK.TRADERS}, capital_events=[], hedge={"disabled": True}, kill_switch={"state": "OK"}, peak_equity_aud=None, seed_aud=BK.SEED_AUD, paper_only=True)
    json.dump(D, open(path, "w"))
    entry = dict(id="A", book="veteran", ticker="BAC", side="LONG", entry=40.0, stop=39.0, target=44.0, state=FG.WAIT, decision="approve", grade="A", tags=dict(strategy="veteran:banks grade A"))
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
