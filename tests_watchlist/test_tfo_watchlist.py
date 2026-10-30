#!/usr/bin/env python3
"""Tests for tfo_watchlist.py (stdlib unittest; offline; mocked quotes).

Runs against the REAL desk modules (tfo_buy, tfo_crypto, tfo_ticket, fundamental_book, desk_lock,
paper_execution_price, optional desk_costs) copied into a throw-away temp desk with synthetic ledgers.
Only modules missing from the V1 tarball (tfo_equity_refresh, exit_check_evidence) and yfinance/Kraken
are stood in. The real desk is never touched.

  python3 -m unittest discover -s tests_watchlist -v          (from the desk root)
  TFO_V1_SRC=/path/to/v1/src TFO_WL_SRC=/path/to/dir/with/tfo_watchlist.py python3 -m unittest ...
"""
import contextlib, datetime as dt, hashlib, io, json, os, shutil, subprocess, sys, tempfile, time, unittest
from unittest import mock as umock

HERE = os.path.dirname(os.path.abspath(__file__))
DESK = os.path.dirname(HERE)
V1_SRC = os.environ.get("TFO_V1_SRC", DESK)
WL_SRC = os.environ.get("TFO_WL_SRC", DESK)
DESK_FILES = ["tfo_buy.py", "tfo_crypto.py", "tfo_ticket.py", "tfo_trail_rail.py", "fundamental_book.py", "desk_lock.py", "paper_execution_price.py", "head_trader_gate.py", "tfo_heat.py"]
UTC = dt.timezone.utc
TMP = wl = tb = tc = None
NOW = dt.datetime.now(UTC)

def dump(path, obj, **kw):
    with open(path, "w") as f:
        json.dump(obj, f, **kw)

def Z(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))

# ------------------------------------------------------------------ fixtures
def ledgers(root):
    r = os.path.join(root, "reports"); os.makedirs(r, exist_ok=True)
    t1 = (NOW - dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"); t2 = (NOW - dt.timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(os.path.join(r, "firm_equity.csv"), "w") as f:
        f.write("utc,firm_equity_usd,firm_equity_live_usd,stale_equity_usd,firm_peak_usd,firm_drawdown_pct,level,books_readable,books_total,positions,capital_event\n")
        f.write("%s,1800000.00,1800000.00,0.00,1805000.00,-0.2770,OK,22,22,50,no\n" % t1)
        f.write("%s,1800000.00,1800000.00,0.00,1805000.00,-0.2770,OK,22,22,50,no\n" % t2)
    clip = lambda n, th: {"side": "long", "notional_usd": n, "units": 1, "entry_px_usd": 1, "ht_clip": True, "ht_theme": th}
    fbk = {"book": "fundamental_book", "positions": {"MSFT": clip(150000, "mega-tech"), "PG": clip(50000, "us-consumer"),
           "COST": clip(50000, "us-consumer"), "CBA.AX": {"side": "long", "notional_usd": 2631.58, "units": 1, "entry_px_usd": 1}},
           "head_trader_tickets": [], "seed_usd": 500000, "cash": 0, "equity": 500000}
    dump(os.path.join(r, "fundamental_book.json"), fbk)
    dump(os.path.join(r, "gap_close_levered_book.json"), {"cash": 900000.0, "reserved_usd": 0, "seed_usd": 900000, "start": 900000, "equity": 900000})
    cpos = {"LINK/USD": {"qty": 150000 / 13.5, "entry_px": 13.5, "orig_entry": 13.5, "theme": "alt-rs"},
            "ONDO/USD": {"qty": 100000 / 0.5, "entry_px": 0.5, "orig_entry": 0.5, "theme": "alt-rs"}}
    dump(os.path.join(r, "tfo_ht_crypto.json"), {"book": "tfo_ht_crypto", "positions": cpos, "mark_px_usd": {"LINK/USD": 13.5, "ONDO/USD": 0.5},
         "head_trader_tickets": [], "holdings": {}, "entry_px": {}, "cash": 0, "seed_usd": 250000, "start": 250000, "equity": 250000,
         "capital_events": [], "closed": []})

# (px native, ccy, fx, avg10 shares, bid-ask spread bp or None, premarket gap %, 20d drift %/day)
EQ = {"AAPL": (200, "USD", 1, 60e6, 1, 0.5, 0.2), "GOOGL": (180, "USD", 1, 30e6, 8, 0.2, 0.1), "JPM": (250, "USD", 1, 8e6, 3, 0.0, 0.05),
      "KO": (70, "USD", 1, 15e6, 2, 0.1, 0.0), "HD": (380, "USD", 1, 4e6, 3, 0.0, 0.0), "DIS": (110, "USD", 1, 9e6, None, 0.0, 0.0),
      "NFLX": (1200, "USD", 1, 3e6, 25, 0.0, 0.1), "NVDA": (180, "USD", 1, 200e6, 1, 1.0, 0.3), "MSFT": (500, "USD", 1, 20e6, 1, 0.0, 0.1),
      "CVX": (155, "USD", 1, 8e6, 3, 0.3, 0.1), "TSLA": (400, "USD", 1, 90e6, 2, -4.0, -0.1), "BA": (200, "USD", 1, 700, 4, 0.0, 0.0),
      "XLE": (90, "USD", 1, 15e6, 1, 0.4, 0.15), "RY.TO": (170, "CAD", 0.72, 5e6, 4, None, 0.05),
      "BHP.AX": (45, "AUD", 0.66, 9e6, 5, None, 0.1), "CBA.AX": (170, "AUD", 0.66, 2e6, 4, None, 0.0),
      "SHEL.L": (2700, "GBp", 0.0134, 12e6, 4, None, 0.05), "SAP.DE": (230, "EUR", 1.17, 2e6, 5, None, 0.0),
      "LLY": (150, "USD", 1, 8e6, 2, 0.0, -0.2), "ABBV": (300, "USD", 1, 4e6, 2, 0.0, 0.1)}   # LLY downtrend; ABBV short history (theme us-health: no clips)
HIST_LEN = {"ABBV": 30}                                                                        # others: 60 daily bars
DRIFT = {}                                                                                    # per-test drift overrides
NEWS = {}                                                                                     # per-test yfinance news/calendar overrides
HL = {}                                                                                       # per-test daily high/low half-range (ATR)

def news_item(title, hours_ago, ref=None):
    t = (ref or Z("2026-09-28T13:05:00Z")) - dt.timedelta(hours=hours_ago)
    return {"id": "n-%s" % abs(hash(title)), "content": {"title": title, "summary": "", "pubDate": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "contentType": "STORY"}}

def mock(session="pre", vol_mult=1.0):
    quotes, yf = {}, {}
    day0 = dt.date(2026, 7, 1)
    for s, (px, ccy, fx, avg10, sp, gap, drift) in EQ.items():
        drift = DRIFT.get(s, drift); n = HIST_LEN.get(s, 60)
        live = session == "regular"
        quotes[s] = {"px": px, "px_usd": px * fx, "ccy": ccy, "fx_usd": fx, "quote_utc": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "fetched_at": NOW.isoformat(), "age_sec": 5, "session": session, "quote_current": live, "executable": live,
                     "paper_executable": live, "paper_price_class": "CURRENT PAPER" if live else "CLOSED", "status": "mock"}
        hist = []
        for i in range(n):
            c = px / (1 + drift / 100) ** (n - 1 - i)
            hl = HL.get(s, 0.01)
            hist.append([(day0 + dt.timedelta(days=i)).isoformat(), c * 0.998, c * (1 + hl), c * (1 - hl), c, avg10])
        bid = ask = 0
        if sp is not None:
            bid = px * (1 - sp / 2e4); ask = px * (1 + sp / 2e4)
        info = {"bid": bid, "ask": ask, "regularMarketPrice": px}
        if gap is not None: info["preMarketPrice"] = px * (1 + gap / 100)
        yf[s] = {"fast_info": {"previousClose": px, "lastVolume": avg10 * vol_mult, "tenDayAverageVolume": avg10, "lastPrice": px},
                 "info": info, "hist": hist, "news": [news_item("%s shares steady ahead of the open" % s, 3)],
                 "calendar": {"Earnings Date": ["2026-10-29"]}}
        yf[s].update(NEWS.get(s, {}))
    return {"quotes": quotes, "yf": yf}

def write_mock(**kw):
    p = os.path.join(TMP, "mock.json"); dump(p, mock(**kw)); os.environ["TFO_WL_MOCK"] = p

KR = {"XXBTZUSD": ("XBT/USD", 100000.0, 2, 3000.0, 0.8), "XETHZUSD": ("ETH/USD", 4000.0, 3, 40000.0, 0.5),
      "SOLUSD": ("SOL/USD", 200.0, 5, 400000.0, 1.2), "LINKUSD": ("LINK/USD", 13.6, 5, 1.5e6, 0.3),
      "XDGUSD": ("XDG/USD", 0.25, 30, 4e8, 0.1), "ONDOUSD": ("ONDO/USD", 0.5, 7, 4e7, 0.2), "USDTZUSD": ("USDT/USD", 1.0, 1, 5e8, 0.0)}

def fake_kraken(path, **params):
    now = time.time()
    if path == "AssetPairs":
        return {k: {"wsname": v[0], "quote": "ZUSD", "status": "online"} for k, v in KR.items()}
    by_ws = {v[0].replace("/", ""): (k, v) for k, v in KR.items()}
    if path == "Ticker" and "pair" not in params:
        return {k: tick(v) for k, v in KR.items()}
    k, v = by_ws[params["pair"]]
    if path == "Ticker": return {k: tick(v)}
    if path == "Trades": return {k: [[str(v[1]), "0.1", now - 3, "b", "m", ""]], "last": "1"}
    if path == "OHLC":
        rows = []
        for i in range(61):
            c = v[1] / (1 + v[4] / 100) ** (60 - i)
            rows.append([int(now) - 86400 * (60 - i), str(c), str(c * 1.02), str(c * 0.98), str(c), str(c), str(v[3]), 100])
        return {k: rows, "last": int(now)}
    raise KeyError(path)

def tick(v):
    ws, px, sp, vol, _ = v
    bid, ask = px * (1 - sp / 2e4), px * (1 + sp / 2e4)
    return {"b": [str(bid), "1", "1"], "a": [str(ask), "1", "1"], "c": [str(px), "1"], "v": [str(vol), str(vol)],
            "p": [str(px), str(px)], "o": str(px * 0.995)}

def setUpModule():
    global TMP, wl, tb, tc
    TMP = tempfile.mkdtemp(prefix="tfo_wl_test_")
    for f in DESK_FILES:
        shutil.copy(os.path.join(V1_SRC, f), TMP)
    if os.path.exists(os.path.join(V1_SRC, "desk_costs.py")):
        shutil.copy(os.path.join(V1_SRC, "desk_costs.py"), TMP)
    shutil.copy(os.path.join(WL_SRC, "tfo_watchlist.py"), TMP)
    for f in os.listdir(os.path.join(HERE, "stubs")):
        if f.endswith(".py"): shutil.copy(os.path.join(HERE, "stubs", f), TMP)
    ledgers(TMP); write_mock()
    sys.path.insert(0, TMP)
    # purge EVERY module the temp desk provides (desk copies + stubs), so a real module imported by an
    # earlier test module in the same run (e.g. tfo_equity_refresh) can never shadow the offline stand-ins
    for m in {f[:-3] for f in os.listdir(TMP) if f.endswith(".py")} | {"yfinance", "tfo_watchlist", "desk_costs", "tfo_heat"}:
        sys.modules.pop(m, None)
    with contextlib.redirect_stdout(io.StringIO()):
        import yfinance  # noqa: F401  (the stand-in, first on sys.path)
        import tfo_watchlist as _wl
    wl = _wl; tb = wl.desk("tfo_buy"); tc = wl.desk("tfo_crypto")
    assert tb.__file__.startswith(TMP) and sys.modules["yfinance"].__version__.endswith("teststub")
    tc.get = fake_kraken

def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)

def quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        r = fn(*a, **k)
    return r, buf.getvalue()

def ledger_hashes():
    out = {}
    for dp, _, fs in os.walk(os.path.join(TMP, "reports")):
        for f in fs:
            if f.startswith(("ht_watchlist", ".ht_watchlist")): continue
            p = os.path.join(dp, f); out[p] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return out

US_PRE = Z("2026-09-28T13:05:00Z")   # Mon 09:05 NY (EDT) -> 25 min to open

# ------------------------------------------------------------------ 1. DST-correct open detection
class TestClock(unittest.TestCase):
    def test_open_utc_all_markets_across_dst(self):
        cases = [("ASX", "2026-10-02", "2026-10-02T00:00Z"),      # AEST (UTC+10)
                 ("ASX", "2026-10-05", "2026-10-04T23:00Z"),      # AEDT from Sun 4 Oct 2026
                 ("TOKYO", "2026-10-05", "2026-10-05T00:00Z"), ("HK", "2026-10-05", "2026-10-05T01:30Z"),
                 ("INDIA", "2026-10-05", "2026-10-05T03:45Z"), ("B3", "2026-10-05", "2026-10-05T13:00Z"),
                 ("EUROPE", "2026-10-23", "2026-10-23T07:00Z"),   # BST
                 ("EUROPE", "2026-10-26", "2026-10-26T08:00Z"),   # GMT from Sun 25 Oct
                 ("EUROPE", "2026-03-27", "2026-03-27T08:00Z"), ("EUROPE", "2026-03-30", "2026-03-30T07:00Z"),
                 ("US", "2026-10-30", "2026-10-30T13:30Z"),       # EDT
                 ("US", "2026-11-02", "2026-11-02T14:30Z"),       # EST from Sun 1 Nov
                 ("US", "2026-03-06", "2026-03-06T14:30Z"), ("US", "2026-03-09", "2026-03-09T13:30Z"),
                 ("B3", "2027-01-04", "2027-01-04T13:00Z"), ("TOKYO", "2027-01-04", "2027-01-04T00:00Z")]
        for mk, d, exp in cases:
            with self.subTest(mk=mk, d=d):
                self.assertEqual(wl.open_on(mk, dt.date.fromisoformat(d)).astimezone(UTC), Z(exp))

    def test_due_detection_dst_transitions(self):
        cases = [("2026-10-01T23:35Z", {"ASX", "TOKYO"}),   # Fri 2 Oct: AEST, ASX and Tokyo both open 00:00Z
                 ("2026-10-04T22:35Z", {"ASX"}),            # Mon 5 Oct: AEDT, ASX 23:00Z; Tokyo still 00:00Z
                 ("2026-10-04T23:35Z", {"TOKYO"}),          # Sunday in UTC, Monday in Tokyo
                 ("2026-10-05T01:05Z", {"HK"}), ("2026-10-05T03:20Z", {"INDIA"}),
                 ("2026-10-23T06:35Z", {"EUROPE"}), ("2026-10-26T07:35Z", {"EUROPE"}), ("2026-10-26T06:35Z", set()),
                 ("2026-10-26T12:35Z", {"B3"}), ("2026-10-26T13:05Z", {"US"}),   # London GMT, NY still EDT
                 ("2026-11-02T13:05Z", set()), ("2026-11-02T14:05Z", {"US"}), ("2026-11-02T12:35Z", set()), ("2026-11-03T12:35Z", {"B3"}),  # 2 Nov = Finados (B3 shut)
                 ("2026-03-09T13:05Z", {"US"}), ("2026-03-09T07:35Z", {"EUROPE"}),  # US DST, UK not yet
                 ("2026-03-30T06:35Z", {"EUROPE"})]
        # 2026-09-28 funder directive: from 29 Sep Sydney only US/TOKYO/ASX (+CRYPTO) are hunted; removed markets never wake.
        kept = {"US", "TOKYO", "ASX", "CRYPTO"}
        for t, exp in cases:
            with self.subTest(t=t):
                self.assertEqual(set(wl.markets_due(Z(t))), exp if Z(t) < Z("2026-09-28T14:00Z") else exp & kept)
        # The DST/holiday clock for every market is still correct with the allowlist switched off.
        with umock.patch.object(wl.v1_venues, "EFFECTIVE_SYD", dt.date(2099, 1, 1)):
            for t, exp in cases:
                with self.subTest(t=t, allowlist="off"):
                    self.assertEqual(set(wl.markets_due(Z(t))), exp)

    def test_window_edges(self):
        o = Z("2026-10-05T13:30Z")
        for mins, due in ((19, False), (20, True), (25, True), (30, True), (31, False)):
            self.assertEqual("US" in wl.markets_due(o - dt.timedelta(minutes=mins)), due, mins)

    def test_weekend_and_holidays(self):
        self.assertEqual(wl.markets_due(Z("2026-10-03T13:05Z")), [])                  # Saturday
        self.assertNotIn("TOKYO", wl.markets_due(Z("2026-10-11T23:35Z")))              # Sports Day Mon 12 Oct
        self.assertNotIn("B3", wl.markets_due(Z("2026-10-12T12:35Z")))                 # Aparecida
        self.assertIn("US", wl.markets_due(Z("2026-10-12T13:05Z")))                    # US open, TSX shut
        self.assertEqual(wl.open_venues("US", dt.date(2026, 10, 12)), ["US"])
        self.assertEqual(wl.open_venues("US", dt.date(2026, 11, 26)), ["TO"])          # Thanksgiving: Toronto only
        self.assertNotIn("HK", wl.markets_due(Z("2026-10-01T01:05Z")))
        self.assertNotIn("INDIA", wl.markets_due(Z("2026-10-02T03:20Z")))
        self.assertEqual(wl.open_venues("EUROPE", dt.date(2026, 12, 24)), ["AS", "L", "MC", "PA"])

    def test_next_open_skips_weekend_and_holiday(self):
        self.assertEqual(wl.next_open("US", Z("2026-10-02T21:00Z")), Z("2026-10-05T13:30Z"))
        self.assertEqual(wl.next_open("TOKYO", Z("2026-10-09T07:00Z")), Z("2026-10-13T00:00Z"))

# ------------------------------------------------------------------ 2-4. gates, clip tiers, heat
class TestGates(unittest.TestCase):
    def setUp(self):
        write_mock(session="pre"); wl.now_utc = lambda: US_PRE
        wl.RISK_PCT = 1.0          # vol sizing neutralised here so tier/heat/stack logic is tested in isolation (see TestV1Improvements)

    def build(self, mk):
        return quiet(wl.build_market, mk, wl.now_utc())[0]

    def test_us_preopen_candidates_and_rejects(self):
        m = self.build("US"); self.assertEqual(m["status"], "BUILT", m.get("error"))
        c = {x["symbol"]: x for x in m["candidates"]}
        allrej = {x["symbol"]: " | ".join(x["reasons"]) for x in m["rejects"]}
        self.assertIn("spread unverifiable", allrej.get("DIS", ""))
        self.assertIn(">= 20bp", allrej.get("NFLX", ""))
        self.assertIn("semis theme full", allrej.get("NVDA", ""))
        self.assertIn("already held", allrej.get("MSFT", ""))
        self.assertIn("theme already has two clips", allrej.get("KO", ""))
        self.assertIn("pre-market gap", allrej.get("TSLA", ""))
        self.assertIn("< $10000 min", allrej.get("BA", ""))
        for s in ("AAPL", "GOOGL", "JPM", "CVX", "XLE", "RY.TO"):
            self.assertIn(s, c, allrej.get(s))
            self.assertEqual(c[s]["label"], "beta")
            self.assertTrue(any(d.startswith("not a regular-session") for d in c[s]["deferred_to_open"]))
        self.assertEqual(c["RY.TO"]["tape"], "DELAYED"); self.assertEqual(c["AAPL"]["tape"], "LIVE")
        self.assertEqual(c["GOOGL"]["clip_usd"], 300000)                 # 8bp spread -> base tier
        self.assertLessEqual(c["AAPL"]["clip_usd"], 600000)              # 600k tier; five-percent heat room no longer binds
        self.assertEqual(c["AAPL"]["clip_usd"], 600000)
        planned = sum(x["clip_usd"] for x in m["candidates"] if x["planned"])
        self.assertLessEqual(planned, m["heat"]["headroom_notional_usd"])
        for x in m["candidates"]:
            for k in ("symbol", "venue", "score", "clip_usd", "qty", "ref_price", "spread_bp", "adv10_usd", "gap_pct", "label", "why"):
                self.assertIn(k, x)

    def test_reasons_are_verbatim_tfo_buy_gates(self):
        m = quiet(wl.build_equity, "US", US_PRE)[0]
        for x in m["candidates"]:
            fails, _, _ = quiet(tb.gates, x["symbol"], x["clip_usd"], x["theme"])[0]
            self.assertEqual(sorted(fails), sorted(x["deferred_to_open"]), x["symbol"])
        for x in m["rejects"]:
            if x["reasons"][0].startswith(("not deep-checked", "sized clip", "pre-market gap")): continue
            fails, _, _ = quiet(tb.gates, x["symbol"], x.get("gate_usd", 10000), x["theme"])[0]
            hard, _ = wl.classify(fails)
            for r in x["reasons"]:
                if not r.startswith(("sized clip", "pre-market gap", "trend filter", "legacy book holds name")): self.assertIn(r.split(" (")[0], " | ".join(hard), x["symbol"])

    def test_in_session_nothing_is_deferred_and_tfo_buy_preview_agrees(self):
        write_mock(session="regular", vol_mult=10.0)
        m = quiet(wl.build_equity, "US", US_PRE)[0]
        us = [x for x in m["candidates"] if x["venue"] == "US"]
        self.assertTrue(us)
        for x in us:
            self.assertEqual(x["deferred_to_open"], [])
            r = wl.run_tool(wl.tool_argv("US", x, x["clip_usd"], False))
            self.assertTrue(r["passed"], (x["symbol"], r["fails"], r["stdout"][-400:]))
            self.assertNotIn("--execute", wl.tool_argv("US", x, x["clip_usd"], False))

    def test_crypto_gates_and_stack(self):
        m = self.build("CRYPTO"); self.assertEqual(m["status"], "BUILT", m.get("error"))
        c = {x["symbol"]: x for x in m["candidates"]}; rj = {x["symbol"]: " | ".join(x["reasons"]) for x in m["rejects"]}
        self.assertIn("already held", rj["LINK/USD"]); self.assertIn("spread >= 20bp", rj["XDG/USD"])
        self.assertNotIn("USDT/USD", c)
        c3max = c["XBT/USD"]["tfo_crypto_info"].get("c3_max_clip_usd")  # 2026-10-02 crypto C3: 1% firm risk to the 2xATR stop may bind first
        # 2026-10-02 Brett: stack cap $500k -> $1M, so stack room is 750k and the $300k clip cap (or C3 1% risk) binds first
        old_cap = 300000 if not c3max or c3max >= 300000 else float(int(c3max) - 1)  # stack 1M - 250k open
        # 2026-10-04 TFO Risk audit (HT adopted 21:30 AEDT): the $5k planned-loss cap at the C3 2xATR stop now binds first
        self.assertLessEqual(c["XBT/USD"]["clip_usd"], old_cap); self.assertIn("clip bound by planned_loss_$5k", c["XBT/USD"]["why"])
        self.assertEqual(c["XBT/USD"]["theme"], "crypto-major")
        self.assertLessEqual(sum(x["clip_usd"] for x in m["candidates"] if x["planned"]), 750000)  # stack room $1M - $250k open
        for x in m["candidates"]:
            fails, _, _ = tc.gates(x["symbol"], x["clip_usd"], x["theme"])
            self.assertEqual(fails, [], x["symbol"])

    def test_clip_tiers(self):
        self.assertEqual(wl.tier_cap("AAPL", 2e9, 5.0), 600000)
        self.assertEqual(wl.tier_cap("AAPL", 2e9, 5.1), 300000)
        self.assertEqual(wl.tier_cap("AAPL", 1.99e9, 1.0), 300000)
        self.assertEqual(wl.tier_cap("AAPL", 5e9, None), 300000)
        self.assertEqual(wl.tier_cap("SHEL.L", 9e9, 1.0), 300000)
        self.assertEqual(wl.tier_cap("7203.T", 9e9, 1.0), 300000)
        firm = {"headroom_notional_usd": 1e9, "unreserved_powder_usd": 1e9}
        self.assertEqual(wl.size_clip("KO", 300000, 0.05 * 3e6, firm, 70)[0:2], (150000, "5%_liquidity"))
        self.assertEqual(wl.size_clip("KO", 300000, 1e9, dict(firm, headroom_notional_usd=123456), 70)[0:2], (123000, "heat_headroom"))
        clip, bind, fee, _ = wl.size_clip("KO", 300000, 1e9, dict(firm, unreserved_powder_usd=200000.0), 70)
        self.assertEqual(bind, "powder_net_fees"); self.assertLessEqual(clip + fee, 200000)
        self.assertEqual(wl.size_clip("XBT/USD", 300000, 1e9, firm, 1e5, extra_cap=90000)[0:2], (90000, "crypto_stack"))

    def test_heat_cap_counts_existing_positions(self):
        f = wl.firm_snapshot()
        self.assertEqual(f["ht_equity_clip_notional_usd"], 250000)      # MSFT+PG+COST ht_clips; CBA.AX not a clip
        self.assertAlmostEqual(f["crypto_open_usd"], 250000, places=2)
        self.assertAlmostEqual(f["heat_at_stops_usd"], 25000, places=2)
        self.assertAlmostEqual(f["heat_cap_usd"], 90000, places=2)
        self.assertAlmostEqual(f["headroom_notional_usd"], 1300000, places=2)
        f0 = dict(f, headroom_notional_usd=0.0)
        self.assertEqual(wl.size_clip("AAPL", 600000, 1e9, f0, 200)[0:2], (0.0, "heat_headroom"))
        cands = [{"theme": "a", "clip_usd": 300000, "fees_est_usd": 1}, {"theme": "b", "clip_usd": 300000, "fees_est_usd": 1},
                 {"theme": "c", "clip_usd": 200000, "fees_est_usd": 1}]
        self.assertEqual([c["planned"] for c in wl.plan(cands, f)], [True, True, True])

    def test_daily_target_never_sizes(self):
        a = [(x["symbol"], x["clip_usd"]) for x in quiet(wl.build_equity, "US", US_PRE)[0]["candidates"]]
        old = tb.DAILY_TARGET_USD; tb.DAILY_TARGET_USD = 1e9
        try: b = [(x["symbol"], x["clip_usd"]) for x in quiet(wl.build_equity, "US", US_PRE)[0]["candidates"]]
        finally: tb.DAILY_TARGET_USD = old
        self.assertEqual(a, b)

    def test_trend_filter_unit(self):
        up = [100 + i for i in range(60)]
        self.assertEqual(wl.trend_check(up, 160), [])                                                 # pass
        self.assertEqual(wl.trend_check([200] * 29 + [90] + [100] * 20, 100), ["trend filter: below 50d SMA"])       # ret20 > 0
        self.assertEqual(wl.trend_check([100] * 29 + [130] + [125] * 20, 125), ["trend filter: 20d return <= 0"])    # > SMA
        self.assertEqual(wl.trend_check([100] * 49 + [100], 100), ["trend filter: below 50d SMA", "trend filter: 20d return <= 0"])
        self.assertEqual(wl.trend_check(up[:49], 200), ["trend filter: insufficient history"])
        self.assertTrue(wl.trend_check(up, None)[0].startswith("trend filter: insufficient history"))

    def test_trend_filter_in_build(self):
        m = quiet(wl.build_equity, "US", US_PRE)[0]
        rj = {x["symbol"]: x["reasons"] for x in m["rejects"]}
        self.assertEqual(rj["LLY"], ["trend filter: below 50d SMA", "trend filter: 20d return <= 0"])   # passes every tfo_buy gate
        self.assertEqual(rj["ABBV"], ["trend filter: insufficient history"])
        for x in m["candidates"]:
            self.assertEqual(x["trend"], "PASS"); self.assertGreater(x["ret20_pct"], 0)
            self.assertGreater(x["ref_price"], x["sma50"])
        DRIFT["AAPL"] = -0.1; write_mock(session="pre")
        try:
            rj = {x["symbol"]: x["reasons"] for x in quiet(wl.build_equity, "US", US_PRE)[0]["rejects"]}
            self.assertIn("trend filter: below 50d SMA", rj["AAPL"])
        finally:
            DRIFT.clear(); write_mock(session="pre")
        cr = quiet(wl.build_crypto, US_PRE)[0]
        self.assertTrue(cr["candidates"]) ; self.assertTrue(all(c["trend"] == "PASS" for c in cr["candidates"]))
        old = KR["SOLUSD"]; KR["SOLUSD"] = old[:4] + (-0.5,)
        try:
            rj = {x["symbol"]: x["reasons"] for x in quiet(wl.build_crypto, US_PRE)[0]["rejects"]}
            self.assertEqual(rj["SOL/USD"], ["trend filter: below 50d SMA", "trend filter: 20d return <= 0"])
        finally:
            KR["SOLUSD"] = old

    def news_build(self, overrides):
        NEWS.clear(); NEWS.update(overrides); write_mock(session="pre")
        try: m = quiet(wl.build_equity, "US", US_PRE)[0]
        finally: NEWS.clear(); write_mock(session="pre")
        return {x["symbol"]: x for x in m["candidates"]}, {x["symbol"]: x for x in m["rejects"]}

    def test_news_gate_earnings_reject(self):
        c, rj = self.news_build({"AAPL": {"calendar": {"Earnings Date": ["2026-09-28"]}},      # session day (Mon)
                                 "GOOGL": {"calendar": {"Earnings Date": ["2026-09-29"]}},     # next trading day
                                 "JPM": {"calendar": {"Earnings Date": ["2026-09-30"]}}})      # 2 trading days out: pass
        for s, d in (("AAPL", "2026-09-28"), ("GOOGL", "2026-09-29")):
            self.assertEqual(rj[s]["reasons"], ["news gate: earnings within 1 trading day"]); self.assertEqual(rj[s]["earnings_date"], d)
        self.assertIn("JPM", c)
        day = dt.date(2026, 10, 9)                                  # Fri before Tokyo's Sports Day Mon 12 Oct -> next is Tue 13
        self.assertEqual(wl.news_gate("TOKYO", [news_item("x", 1)], {"Earnings Date": [dt.date(2026, 10, 13)]}, [], US_PRE, day)[0],
                         ["news gate: earnings within 1 trading day"])

    def test_news_gate_keyword_reject(self):
        c, rj = self.news_build({"GOOGL": {"news": [news_item("Alphabet slides after analyst DOWNGRADE on cloud margins", 5)]},
                                 "XLE": {"news": [news_item("Refiner halted on Tuesday", 40)]},          # > 36h old: ignored
                                 "CVX": {"news": [{"title": "Chevron faces SEC charges", "providerPublishTime": US_PRE.timestamp() - 3600}]}})
        self.assertEqual(rj["GOOGL"]["reasons"], ["news gate: negative headline"])
        self.assertEqual(rj["GOOGL"]["news_headline"], "Alphabet slides after analyst DOWNGRADE on cloud margins")
        self.assertEqual(rj["GOOGL"]["news_keyword"], "downgrade")
        self.assertEqual(rj["CVX"]["news_keyword"], "sec charges")                                    # legacy flat item shape
        self.assertIn("XLE", c)
        self.assertEqual(wl.news_gate("US", [news_item("Record quarter, no recalls expected", 1)], {}, [], US_PRE, US_PRE.date())[0],
                         ["news gate: negative headline"])

    def test_news_gate_clean_pass(self):
        c, rj = self.news_build({})
        for s in ("AAPL", "GOOGL", "JPM", "CVX", "XLE", "RY.TO"):
            self.assertIn(s, c); self.assertEqual(c[s]["flags"], [])
            self.assertFalse(any(r.startswith("news gate") for r in rj.get(s, {}).get("reasons", [])))

    def test_news_gate_fetch_error_passes_with_flag(self):
        c, rj = self.news_build({"JPM": {"news_error": True}, "CVX": {"cal_error": True}, "XLE": {"news": []}})
        for s in ("JPM", "CVX", "XLE"):
            self.assertIn(s, c, rj.get(s)); self.assertEqual(c[s]["flags"], ["news_unchecked"])
        self.assertIn("news: RuntimeError", c["JPM"]["news_errors"][0])
        self.assertEqual(wl.news_gate("US", None, None, ["news: boom"], US_PRE, US_PRE.date())[0:2], ([], ["news_unchecked"]))
        self.assertEqual(wl.news_gate("US", [{"content": 7}], "garbage", [], US_PRE, US_PRE.date())[0], [])
        self.assertEqual(wl.news_gate("US", [news_item("fine", 1)], {}, [], US_PRE, US_PRE.date())[0:2], ([], ["news_unchecked"]))   # silent {} calendar

    def test_news_gate_skips_crypto_and_is_one_fetch_per_candidate(self):
        real_gate, real_fetch = wl.news_gate, wl.fetch_news_cal
        def boom(*a, **k): raise AssertionError("news gate must not run for crypto")
        wl.news_gate = wl.fetch_news_cal = boom
        try:
            cr = quiet(wl.build_crypto, US_PRE)[0]
            self.assertEqual(cr["status"], "BUILT"); self.assertTrue(cr["candidates"])
            self.assertTrue(all("flags" not in c for c in cr["candidates"]))
            self.assertNotIn("news_rule", cr)
        finally:
            wl.news_gate, wl.fetch_news_cal = real_gate, real_fetch
        seen = []
        def counting(t):
            seen.append(t.ticker); return real_fetch(t)
        wl.fetch_news_cal = counting
        try: m = quiet(wl.build_equity, "US", US_PRE)[0]
        finally: wl.fetch_news_cal = real_fetch
        self.assertEqual(sorted(seen), sorted(set(seen)))
        self.assertEqual(sorted(seen), sorted(c["symbol"] for c in m["candidates"]))

    def test_classify_and_resize(self):
        pre = ["not a regular-session executable print", "US print not under 60s", "spread 25.0bp >= 20bp", "size > 5% of day dollar volume (0)"]
        self.assertEqual(wl.classify(pre), (["spread 25.0bp >= 20bp"], pre[:2] + pre[3:]))
        live = ["size > 5% of day dollar volume (100)", "down >3% on session"]
        self.assertEqual(wl.classify(live), (live, []))
        self.assertEqual(wl.resize(600000, ["clip >$300k needs 10d avg $vol >= $2B and spread <= 5bp"], {}), 300000)
        self.assertEqual(wl.resize(300000, ["size > 5% of day dollar volume (4000000)"], {"day_dollar_vol": 4e6}), 196000)
        self.assertIsNone(wl.resize(300000, ["size > 5% of day dollar volume (1)", "spread 25.0bp >= 20bp"], {"day_dollar_vol": 4e6}))

# ------------------------------------------------------------------ 5-6. atomic write, no side effects, fire
class TestSideEffects(unittest.TestCase):
    def setUp(self):
        write_mock(session="pre"); wl.now_utc = lambda: US_PRE
        wl.RISK_PCT = 1.0          # vol sizing neutralised here so tier/heat/stack logic is tested in isolation (see TestV1Improvements)

    def test_atomic_write_keeps_old_file_on_failure(self):
        p = os.path.join(TMP, "reports", "atomic_probe.json")
        wl.atomic_write_json(p, {"v": 1})
        real = wl.json.dump
        def boom(*a, **k): raise IOError("disk full mid-write")
        wl.json.dump = boom
        try:
            with self.assertRaises(IOError): wl.atomic_write_json(p, {"v": 2})
        finally:
            wl.json.dump = real
        self.assertEqual(json.load(open(p)), {"v": 1})
        self.assertFalse([f for f in os.listdir(os.path.dirname(p)) if f.endswith(".tmp")])
        os.unlink(p)

    def test_tick_build_show_fire_preview_never_touch_ledgers(self):
        before = ledger_hashes()
        _, out = quiet(wl.main, ["tick"])
        self.assertIn("due=[US]", out)
        _, eu = quiet(wl.main, ["build", "EUROPE"])      # 2026-09-28 directive: EUROPE removed from 29 Sep -> no build
        self.assertIn("VENUE_NOT_ALLOWED", eu)
        quiet(wl.main, ["build", "ASX"]); quiet(wl.main, ["show"])
        doc = json.load(open(wl.OUT)); hb = json.load(open(wl.HB))
        self.assertTrue({"US", "CRYPTO", "ASX"} <= set(doc["markets"])); self.assertNotIn("EUROPE", doc["markets"])
        self.assertTrue(doc["paper_only"] and doc["never_tickets"])
        self.assertIn("sydney", doc["markets"]["US"]["built_at"]); self.assertIn("utc", doc["markets"]["US"]["open"])
        self.assertEqual(hb["mode"], "build ASX"); self.assertTrue(hb["next_opens"]); self.assertFalse(hb["errors"])
        write_mock(session="regular", vol_mult=10.0)
        wl.now_utc = lambda: Z("2026-09-28T13:40:00Z")
        _, out = quiet(wl.main, ["fire", "US"])                          # preview: real tfo_buy.py subprocess, no --execute
        self.assertIn("PREVIEW PASS", out); self.assertIn("LOG none", out)
        self.assertEqual(before, ledger_hashes())
        self.assertFalse(os.path.exists(os.path.join(TMP, "reports", "tfo_head_trader_log.txt")))
        self.assertFalse(os.path.exists(os.path.join(TMP, "reports", "tfo_head_trader_facts.jsonl")))

    def test_guard_refuses_ledger_writes(self):
        with wl.readonly_guard():
            with self.assertRaises(wl.LedgerWriteRefused): tb.save("fundamental_book", {})
            with self.assertRaises(wl.LedgerWriteRefused): tc.save("tfo_ht_crypto", {})
            with self.assertRaises(wl.LedgerWriteRefused): tb.execute("AAPL", 1, "t", "beta", "", {})
        self.assertNotEqual(tb.save.__name__, "refuse")

    def test_fire_execute_waits_then_calls_tools_with_execute(self):
        quiet(wl.cmd_build, "US")
        calls = []
        def runner(argv):
            calls.append(argv); ex = "--execute" in argv
            return {"rc": 0, "info": {"px_usd": 200.0, "gate_fails": []}, "fails": [], "passed": True, "stdout": "",
                    "logs": ["LOG 2026-09-28 23:40 BUY %s 1 200 LIVE fundamental_book 1 0 0" % argv[2]] if ex else []}
        wl.now_utc = lambda: Z("2026-09-28T13:35:00Z")                    # 09:35 NY < 09:38 first eligible
        out = quiet(wl.cmd_fire, "US", True, 3, runner)[0]
        self.assertEqual((out, calls), ([], []))
        wl.now_utc = lambda: Z("2026-09-28T13:39:00Z")
        out = quiet(wl.cmd_fire, "US", True, 2, runner)[0]
        self.assertEqual(len(out), 2)
        self.assertEqual([("--execute" in a) for a in calls], [False, True, False, True])
        self.assertTrue(all(a[1].endswith("tfo_buy.py") for a in calls))
        room = wl.firm_snapshot()["headroom_notional_usd"]
        self.assertTrue(all(float(a[3]) <= room for a in calls))
        wl.now_utc = lambda: Z("2026-09-28T16:00:00Z")                    # > 2h after open: stale list refused
        calls.clear(); self.assertEqual(quiet(wl.cmd_fire, "US", True, 2, runner)[0], []); self.assertEqual(calls, [])

    def test_fire_rechecks_trend_before_calling_tools(self):
        quiet(wl.cmd_build, "US")
        top = json.load(open(wl.OUT))["markets"]["US"]["candidates"][0]["symbol"]
        calls = []
        def runner(argv):
            calls.append(argv)
            return {"rc": 0, "info": {"px_usd": 1.0, "gate_fails": []}, "fails": [], "passed": True, "stdout": "", "logs": []}
        DRIFT[top] = -0.3; write_mock(session="regular", vol_mult=10.0)
        wl.now_utc = lambda: Z("2026-09-28T13:39:00Z")
        try:
            _, out = quiet(wl.cmd_fire, "US", True, 1, runner)
        finally:
            DRIFT.clear(); write_mock(session="pre")
        self.assertEqual(calls, []); self.assertIn("SKIP %s: trend filter: below 50d SMA" % top, out); self.assertIn("LOG none", out)
        old = KR["XXBTZUSD"]; KR["XXBTZUSD"] = old[:4] + (-0.5,)
        try:
            calls.clear(); _, out = quiet(wl.cmd_fire, "CRYPTO", False, 20, runner)
        finally:
            KR["XXBTZUSD"] = old
        self.assertNotIn("XBT/USD", [a[3] for a in calls])

    def test_fire_rechecks_news_before_calling_tools(self):
        quiet(wl.cmd_build, "US")
        top = json.load(open(wl.OUT))["markets"]["US"]["candidates"][0]["symbol"]
        calls = []
        def runner(argv):
            calls.append(argv)
            return {"rc": 0, "info": {"px_usd": 1.0, "gate_fails": []}, "fails": [], "passed": True, "stdout": "", "logs": []}
        fire_t = Z("2026-09-28T13:39:00Z")
        NEWS[top] = {"news": [news_item("Trading halt pending news", 0.2, ref=fire_t)]}; write_mock(session="regular", vol_mult=10.0)
        wl.now_utc = lambda: fire_t
        try:
            _, out = quiet(wl.cmd_fire, "US", True, 1, runner)
        finally:
            NEWS.clear(); write_mock(session="pre")
        self.assertEqual(calls, []); self.assertIn("SKIP %s: news gate: negative headline" % top, out)
        self.assertIn("Trading halt pending news", out)
        NEWS[top] = {"news_error": True}; write_mock(session="regular", vol_mult=10.0)
        try:
            _, out = quiet(wl.cmd_fire, "US", False, 1, runner)
        finally:
            NEWS.clear(); write_mock(session="pre")
        self.assertIn("NOTE %s: news_unchecked" % top, out); self.assertEqual(len(calls), 1)

    def test_fire_crypto_uses_tfo_crypto_buy(self):
        calls = []
        def runner(argv):
            calls.append(argv)
            return {"rc": 0, "info": {"ask": 100000.0, "gate_fails": []}, "fails": [], "passed": True, "stdout": "", "logs": []}
        quiet(wl.cmd_fire, "CRYPTO", False, 1, runner)
        self.assertEqual(calls[0][1:3], [os.path.join(TMP, "tfo_crypto.py"), "buy"]); self.assertNotIn("--execute", calls[0])

    def test_zz_sample_json(self):
        quiet(wl.main, ["tick"]); quiet(wl.main, ["build", "ASX"]); quiet(wl.main, ["build", "EUROPE"])
        doc = json.load(open(wl.OUT))
        self.assertEqual(doc["schema"], wl.VERSION)
        if os.environ.get("TFO_WL_WRITE_SAMPLE"):
            dump(os.environ["TFO_WL_WRITE_SAMPLE"], doc, indent=1, default=str)

# ------------------------------------------------------------------ V1 improvements: vol sizing, legacy no-rebuy, setup tags
FIRE_T = Z("2026-09-28T13:39:00Z")

class TestV1Improvements(unittest.TestCase):
    def setUp(self):
        write_mock(session="pre"); wl.now_utc = lambda: US_PRE; wl.RISK_PCT = 0.0025
        self.rep = os.path.join(TMP, "reports"); self.bak = tempfile.mkdtemp()
        for f in os.listdir(self.rep):
            if f.endswith((".json", ".csv")): shutil.copy(os.path.join(self.rep, f), self.bak)

    def tearDown(self):
        HL.clear(); wl.RISK_PCT = 0.0025
        for f in ("convexity_sleeve.json", "ht_fills_tagged.jsonl", "tfo_head_trader_facts.jsonl"):
            with contextlib.suppress(OSError): os.unlink(os.path.join(self.rep, f))
        for f in os.listdir(self.bak): shutil.copy(os.path.join(self.bak, f), self.rep)
        shutil.rmtree(self.bak, ignore_errors=True); write_mock(session="pre")

    def runner(self, calls, fees=None):
        def run(argv):
            calls.append(argv); ex = "--execute" in argv
            if not ex:
                return {"rc": 0, "info": {"gate_fails": []}, "fails": [], "passed": True, "stdout": "", "logs": []}   # no px: sanity n/a
            sym, usd = (argv[3], float(argv[4])) if argv[2] == "buy" else (argv[2], float(argv[3]))
            syd = FIRE_T.astimezone(wl.SYD).strftime("%Y-%m-%d %H:%M"); tid = "HT-test%s" % len(calls)
            with open(os.path.join(TMP, "reports", "tfo_head_trader_facts.jsonl"), "a") as f:
                f.write(json.dumps({"syd": syd, "fact": "BUY %s $%.0f LIVE (beta) theme t: w" % (sym, usd), "ticket": tid}) + "\n")
            line = "LOG %s BUY %s %.6g %.6g LIVE fundamental_book %.2f 0.00 0.00" % (syd, sym, usd / 200.0, 200.0, usd)
            if fees is not None: line += " %.2f" % fees
            return {"rc": 0, "info": {}, "fails": [], "passed": True, "stdout": line, "logs": [line]}
        return run

    # 1. volatility sizing
    def test_vol_size_unit(self):
        self.assertEqual(wl.vol_size(1.8e6, 1.0), (90000.0, 0.05))                 # 2*ATR 2% < 5% -> mandate 5%
        v, st = wl.vol_size(1.8e6, 4.0); self.assertAlmostEqual(v, 56250.0); self.assertAlmostEqual(st, 0.08)   # wider ATR shrinks size
        self.assertEqual(wl.vol_size(1.8e6, None), (90000.0, 0.05))
        self.assertAlmostEqual(wl.vol_size(1.8e6, 10.0)[0] * 0.20, 0.0025 * 1.8e6)  # loss at sizing stop = 0.25% firm
        self.assertEqual(wl.size_clip("KO", 300000, 1e9, {"headroom_notional_usd": 1e9, "unreserved_powder_usd": 1e9}, 70, vol_cap=56250)[0:2],
                         (56000, "vol_0.25%_risk"))

    def test_vol_sizing_in_build(self):
        HL["XLE"] = 0.05; write_mock(session="pre")
        m = quiet(wl.build_equity, "US", US_PRE)[0]; c = {x["symbol"]: x for x in m["candidates"]}
        self.assertEqual(c["AAPL"]["vol_size_usd"], 90000.0); self.assertEqual(c["AAPL"]["clip_usd"], 90000)
        self.assertEqual(c["AAPL"]["sizing_stop_pct"], 5.0); self.assertEqual(c["AAPL"]["tier_cap_usd"], 600000)
        self.assertGreater(c["XLE"]["sizing_stop_pct"], 5.0); self.assertLess(c["XLE"]["clip_usd"], 30000)
        for x in m["candidates"]:
            self.assertLessEqual(x["clip_usd"], x["vol_size_usd"]); self.assertEqual(x["risk_usd"], round(x["clip_usd"] * 0.05, 2))
            self.assertLessEqual(x["vol_risk_usd"], 0.0025 * 1.8e6 + 1)
        self.assertIn("vol sizing", m["vol_sizing"])
        cr = quiet(wl.build_crypto, US_PRE)[0]
        for x in cr["candidates"]:
            self.assertLessEqual(x["clip_usd"], x["vol_size_usd"]); self.assertIn("risk_usd", x); self.assertEqual(x["tier_cap_usd"], 300000)

    def test_vol_sizing_in_fire(self):
        p = umock.patch.object(wl, "planned_loss_cap", lambda mk, atr: None); p.start(); self.addCleanup(p.stop)  # isolate vol sizing from the 2026-10-04 planned-loss cap (tested in tests/test_risk_audit_20261004.py)
        wl.RISK_PCT = 1.0; quiet(wl.cmd_build, "US")                                # list built with big clips
        self.assertGreater(json.load(open(wl.OUT))["markets"]["US"]["candidates"][0]["clip_usd"], 90000)
        wl.RISK_PCT = 0.0025; write_mock(session="regular", vol_mult=10.0); wl.now_utc = lambda: FIRE_T
        calls = []; quiet(wl.cmd_fire, "US", False, 3, self.runner(calls))
        self.assertTrue(calls); self.assertTrue(all(float(a[3]) <= 90000 for a in calls))

    # 2. legacy no-rebuy
    def test_legacy_no_rebuy_build_and_fire(self):
        quiet(wl.cmd_build, "US")
        dump(os.path.join(self.rep, "convexity_sleeve.json"), {"positions": {"JPM 2026-10-16 C250": {"underlying": "JPM", "units": 1, "premium_usd": 900},
                                                                         "AAPL": {"instrument": "call", "units": 1, "premium_usd": 500}}})
        wl.now_utc = lambda: FIRE_T; write_mock(session="regular", vol_mult=10.0)
        calls = []; _, out = quiet(wl.cmd_fire, "US", False, 10, self.runner(calls))
        self.assertIn("SKIP AAPL: legacy book holds name", out); self.assertIn("SKIP JPM: legacy book holds name", out)
        self.assertFalse([a for a in calls if a[2] in ("AAPL", "JPM")])
        wl.now_utc = lambda: US_PRE; write_mock(session="pre")
        m = quiet(wl.build_equity, "US", US_PRE)[0]; rj = {x["symbol"]: x["reasons"] for x in m["rejects"]}
        self.assertNotIn("AAPL", [x["symbol"] for x in m["candidates"]])
        self.assertIn("legacy book holds name", rj["AAPL"]); self.assertIn("legacy book holds name", rj["JPM"])
        self.assertIn("legacy book holds name", rj["MSFT"])                         # fundamental_book holding (tfo_buy also says held)
        self.assertEqual(m["legacy_sources"], {"fundamental_book": 4, "convexity_sleeve": 2})
        held, src = wl.legacy_held(); self.assertTrue({"JPM", "AAPL", "MSFT", "CBA.AX"} <= held)

    # 3. setup tags + report
    def test_setup_tags_written_per_fill(self):
        quiet(wl.cmd_build, "US"); wl.now_utc = lambda: FIRE_T; write_mock(session="regular", vol_mult=10.0)
        calls = []; quiet(wl.cmd_fire, "US", False, 2, self.runner(calls))
        self.assertEqual(wl.read_tags(), [])                                          # preview never tags
        calls = []; out = quiet(wl.cmd_fire, "US", True, 2, self.runner(calls, fees=1.25))[0]
        tags = wl.read_tags(); self.assertEqual(len(tags), 2); self.assertEqual(len(out), 2)
        t = tags[0]
        for k in ("ts", "market", "symbol", "side", "qty", "fill_px_usd", "notional_usd", "risk_usd", "setup", "ticket_id", "log_line"):
            self.assertIn(k, t)
        self.assertEqual((t["market"], t["side"], t["setup"], t["fill_px_usd"], t["fees_usd"]), ("US", "long", "watchlist_momentum_equity", 200.0, 1.25))
        self.assertTrue(t["ticket_id"].startswith("HT-test")); self.assertEqual(t["risk_usd"], round(t["notional_usd"] * 0.05, 2))
        self.assertEqual(t["log_line"], out[0])
        calls = []; self.assertEqual(quiet(wl.cmd_fire, "US", True, 1, self.runner(calls), "crypto_hunt")[0], []); self.assertEqual(calls, [])
        calls = []; quiet(wl.cmd_fire, "CRYPTO", True, 1, self.runner(calls), "crypto_hunt")
        self.assertEqual(wl.read_tags()[-1]["setup"], "crypto_hunt"); self.assertEqual(wl.read_tags()[-1]["market"], "CRYPTO")
        calls = []; quiet(wl.cmd_fire, "CRYPTO", True, 1, self.runner(calls))
        self.assertEqual(wl.read_tags()[-1]["setup"], "watchlist_momentum_crypto")
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()): wl.main(["fire", "US", "--setup", "bogus"])

    def test_report_per_setup(self):
        tags = [{"ts": "2026-09-28T13:40:00Z", "market": "US", "symbol": "AAPL", "setup": "watchlist_momentum_equity", "ticket_id": "E1", "notional_usd": 90000, "fees_usd": None},
                {"ts": "2026-09-28T13:41:00Z", "market": "US", "symbol": "CVX", "setup": "watchlist_momentum_equity", "ticket_id": "E2", "notional_usd": 90000, "fees_usd": None},
                {"ts": "2026-09-28T13:42:00Z", "market": "CRYPTO", "symbol": "ETH/USD", "setup": "crypto_hunt", "ticket_id": "C1", "notional_usd": 50000, "fees_usd": None},
                {"ts": "2026-09-28T13:43:00Z", "market": "US", "symbol": "XLE", "setup": "watchlist_momentum_equity", "ticket_id": "E3", "notional_usd": 50000, "fees_usd": None}]
        with open(wl.TAGS, "w") as f:
            for t in tags: f.write(json.dumps(t) + "\n")
        fb = json.load(open(os.path.join(self.rep, "fundamental_book.json")))
        fb["head_trader_tickets"] = [{"sym": "AAPL", "action": "FLAT", "utc": "2026-09-29T15:00:00Z", "pnl_usd": 4500.0},
                                     {"sym": "CVX", "action": "HALF", "utc": "2026-09-29T15:00:00Z", "pnl_usd": 900.0},
                                     {"sym": "CVX", "action": "STOP", "utc": "2026-09-30T15:00:00Z", "pnl_usd": -2250.0}]
        dump(os.path.join(self.rep, "fundamental_book.json"), fb)
        cb = json.load(open(os.path.join(self.rep, "tfo_ht_crypto.json")))
        cb["positions"]["ETH/USD"] = {"qty": 12.5, "entry_px": 4000.0, "orig_entry": 4000.0, "theme": "crypto-major", "entry_ticket": {"ticket_id": "C1"}}
        cb["mark_px_usd"]["ETH/USD"] = 4100.0
        dump(os.path.join(self.rep, "tfo_ht_crypto.json"), cb)
        r = quiet(wl.cmd_report)[0]
        e = r["watchlist_momentum_equity"]
        self.assertEqual((e["trades"], e["closed"], e["priced"], e["unknown"]), (3, 2, 2, 1))       # XLE: no exits/position -> unknown
        self.assertEqual((e["win_rate"], e["gross_pnl_usd"], e["mean_gross_per_trade_usd"]), (0.5, 3150.0, 1575.0))
        self.assertIsNone(e["net_pnl_usd"])                                                         # fee field absent -> null
        c = r["crypto_hunt"]; self.assertEqual((c["open"], c["gross_pnl_usd"], c["win_rate"]), (1, 1250.0, 1.0)); self.assertIsNone(c["net_pnl_usd"])
        for t in tags: t["fees_usd"] = 5.0                                                          # after the fee patch
        with open(wl.TAGS, "w") as f:
            for t in tags: f.write(json.dumps(t) + "\n")
        for tk in fb["head_trader_tickets"]: tk["fees_usd"] = 2.0
        dump(os.path.join(self.rep, "fundamental_book.json"), fb)
        r = quiet(wl.cmd_report)[0]
        self.assertIsNone(r["watchlist_momentum_equity"]["net_pnl_usd"])                            # XLE unpriced -> still incomplete
        self.assertEqual(r["crypto_hunt"]["net_pnl_usd"], 1245.0)
        self.assertEqual(r["crypto_hunt"]["mean_net_per_trade_usd"], 1245.0)
        tags.pop()
        with open(wl.TAGS, "w") as f:
            for t in tags: f.write(json.dumps(t) + "\n")
        e = quiet(wl.cmd_report)[0]["watchlist_momentum_equity"]
        self.assertEqual(e["net_pnl_usd"], 3150.0 - 5 - 2 - 5 - 2 - 2)
        _, txt = quiet(wl.main, ["report"]); self.assertIn("crypto_hunt", txt)

    def test_zz_sample_json_with_vol_sizing(self):
        quiet(wl.main, ["tick"]); quiet(wl.main, ["build", "ASX"]); quiet(wl.main, ["build", "EUROPE"])
        doc = json.load(open(wl.OUT))
        self.assertTrue(all("vol_size_usd" in c for m in doc["markets"].values() for c in m["candidates"]))
        if os.environ.get("TFO_WL_WRITE_SAMPLE"):
            dump(os.environ["TFO_WL_WRITE_SAMPLE"], doc, indent=1, default=str)

# ------------------------------------------------------------------ 2026-09-28 entry-path fixes (locked quote, fresh post-open rebuild)
class TestEntryFixes20260928(unittest.TestCase):
    def setUp(self):
        self.saved = dict(EQ); write_mock(session="pre"); wl.now_utc = lambda: US_PRE; wl.RISK_PCT = 1.0

    def tearDown(self):
        EQ.clear(); EQ.update(self.saved); wl.now_utc = lambda: US_PRE; write_mock(session="pre")

    def test_locked_quote_is_verified_spread_but_not_mega_tier(self):
        px, ccy, fx, avg10, _sp, gap, drift = EQ["KO"]
        EQ["KO"] = (px, ccy, fx, 60e6, 0, gap, drift)            # bid == ask > 0 (locked), 10d $vol $4.2B
        write_mock(session="regular", vol_mult=10.0)
        fails, q, info = tb.gates("KO", 50000, "t-locked")
        self.assertFalse([f for f in fails if f.startswith("spread")], fails)
        self.assertEqual((info.get("spread_bp"), info.get("spread_locked")), (0.0, True))
        fails, q, info = tb.gates("KO", 400000, "t-locked")
        self.assertTrue(any(f.startswith("clip >$300k needs") for f in fails), fails)
        EQ["KO"] = (px, ccy, fx, 60e6, None, gap, drift)         # truly missing bid/ask still fails closed
        write_mock(session="regular", vol_mult=10.0)
        fails, _, _ = tb.gates("KO", 50000, "t-locked")
        self.assertIn("spread unverifiable (no bid/ask)", fails)

    def test_crossed_snapshot_is_reread_then_fails_closed(self):
        px, ccy, fx, avg10, _sp, gap, drift = EQ["KO"]
        EQ["KO"] = (px, ccy, fx, 60e6, -4, gap, drift)           # ask < bid (torn snapshot) on every read
        write_mock(session="regular", vol_mult=10.0)
        real = tb.SPREAD_RETRY_SLEEP; tb.SPREAD_RETRY_SLEEP = 0.0
        try:
            fails, _, _ = tb.gates("KO", 50000, "t-crossed")
        finally:
            tb.SPREAD_RETRY_SLEEP = real
        self.assertTrue(any(f.startswith("spread unverifiable (crossed") for f in fails), fails)

    def test_fire_late_refuses_preopen_list_but_accepts_fresh_rebuild(self):
        quiet(wl.cmd_build, "US")                                   # pre-open list (13:05Z)
        calls = []
        def runner(argv):
            calls.append(argv); ex = "--execute" in argv
            return {"rc": 0, "info": {"px_usd": 200.0, "gate_fails": []}, "fails": [], "passed": True, "stdout": "",
                    "logs": ["LOG 2026-09-29 02:05 BUY %s 1 200 LIVE fundamental_book 1 0 0" % argv[2]] if ex else []}
        wl.now_utc = lambda: Z("2026-09-28T16:00:00Z")
        self.assertEqual(quiet(wl.cmd_fire, "US", True, 1, runner)[0], []); self.assertEqual(calls, [])
        write_mock(session="regular", vol_mult=10.0)
        quiet(wl.cmd_build, "US")                                   # in-session rebuild at 16:00Z
        wl.now_utc = lambda: Z("2026-09-28T16:05:00Z")
        out = quiet(wl.cmd_fire, "US", True, 1, runner)[0]
        self.assertEqual(len(out), 1); self.assertEqual([("--execute" in a) for a in calls], [False, True])
        calls.clear(); wl.now_utc = lambda: Z("2026-09-28T16:20:00Z")   # rebuild now 20 min old -> stale again
        self.assertEqual(quiet(wl.cmd_fire, "US", True, 1, runner)[0], []); self.assertEqual(calls, [])

if __name__ == "__main__":
    unittest.main()
