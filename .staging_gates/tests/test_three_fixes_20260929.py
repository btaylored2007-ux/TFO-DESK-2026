"""HT 2026-09-29 23:59 AEST fixes: (1) one fire list (staged plan), (2) quote_inconsistent spread gate, (3) GCL flow-aware
top-level peak. Offline: temp dirs, no live ledgers, no network."""
import contextlib, datetime as dt, io, json, os, shutil, sys, tempfile, types, unittest
from unittest import mock
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import quote_check as qc

SYD = ZoneInfo("Australia/Sydney")
WED_2340 = dt.datetime(2026, 9, 30, 23, 40, tzinfo=SYD)     # 09:40 EDT Wed 30 Sep: after first eligible (09:38)


class QuoteCheck(unittest.TestCase):
    def test_29_sep_open_cases(self):
        self.assertFalse(qc.check(339.74, 339.78, 332.89)[0])          # AAPL: tight quote 2% off the tape
        for b, a, l in ((0, 0, 675.43), (0, 0, 95.36), (0, 0, 148.22)):  # TMO/SHEL/PG
            ok, r, _ = qc.check(b, a, l); self.assertFalse(ok); self.assertTrue(r.startswith("quote_inconsistent"))
        ok, r, sp = qc.check(350.0, 408.0, 409.035); self.assertFalse(ok)   # ISRG: last outside, >0.5% from mid
    def test_locked_passes_at_zero_bp_when_tape_agrees(self):  # HT 2026-09-30 00:09
        self.assertEqual(qc.check(10.0, 10.0, 10.0), (True, None, 0.0))
        self.assertTrue(qc.check(10.0, 10.0, 10.04)[0])            # 0.4% away
        ok, r, _ = qc.check(10.0, 10.0, 10.06); self.assertFalse(ok); self.assertIn("locked", r)   # 0.6% away
        self.assertFalse(qc.check(10.0, 10.0, None)[0]); self.assertFalse(qc.check(10.0, 10.0, 0)[0])

    def test_locked_crossed_missing_fail(self):
        self.assertIn("crossed", qc.check(10.1, 10.0, 10.0)[1])
        self.assertIn("missing", qc.check(10.0, 0, 10.0)[1]); self.assertFalse(qc.check(10.0, 10.1, None)[0])
    def test_good_quotes_pass(self):
        ok, r, sp = qc.check(717.0, 717.25, 717.1); self.assertTrue(ok); self.assertAlmostEqual(sp, 3.49, places=1)
        self.assertTrue(qc.check(100.0, 100.1, 100.4)[0])                # outside but 0.35% from mid
        self.assertFalse(qc.check(100.0, 100.1, 100.7)[0])               # 0.65% from mid


class NasdaqParse(unittest.TestCase):  # HT 2026-09-30 primary US source
    import datetime as _d
    NOW = _d.datetime(2026, 9, 29, 14, 12, tzinfo=_d.timezone.utc)   # 10:12 ET
    def pl(self, ts="Sep 29, 2026 10:11 AM ET", rt=True, status="Open", bid="$333.06", ask="$333.09"):
        return {"data": {"marketStatus": status, "primaryData": {"isRealTime": rt, "lastTradeTimestamp": ts,
                "bidPrice": bid, "askPrice": ask, "lastSalePrice": "$333.07"}}}
    def test_fresh_parses(self):
        q = qc.parse_nasdaq(self.pl(), self.NOW); self.assertEqual((q["bid"], q["ask"], q["source"]), (333.06, 333.09, "nasdaq"))
    def test_stale_closed_delayed_or_junk_is_none(self):
        self.assertIsNone(qc.parse_nasdaq(self.pl(ts="Sep 29, 2026 10:00 AM ET"), self.NOW))
        self.assertIsNone(qc.parse_nasdaq(self.pl(status="Closed"), self.NOW))
        self.assertIsNone(qc.parse_nasdaq(self.pl(rt=False), self.NOW))
        self.assertIsNone(qc.parse_nasdaq({"data": None}, self.NOW)); self.assertIsNone(qc.parse_nasdaq(self.pl(ts="N/A"), self.NOW))
    def test_non_us_or_disabled_skips_network(self):
        with mock.patch.object(qc._ur, "urlopen", side_effect=AssertionError("network")):
            self.assertIsNone(qc.nasdaq_quote("CSL.AX")); self.assertIsNone(qc.nasdaq_quote("SOL/USD"))
            with mock.patch.dict(os.environ, {"QUOTE_PRIMARY": "yahoo"}): self.assertIsNone(qc.nasdaq_quote("AAPL"))


class BuyGateUsesQuoteCheck(unittest.TestCase):
    def gates(self, bid, ask, last, usd=100000, nasdaq=None):
        import tfo_buy as tb
        info = {"bid": bid, "ask": ask}
        yf = types.SimpleNamespace(Ticker=lambda s: types.SimpleNamespace(
            fast_info={"previousClose": last, "lastVolume": 5e6, "tenDayAverageVolume": 5e6}, info=info))
        q = {"px": last, "px_usd": last, "session": "regular", "quote_current": True, "age_sec": 5, "ccy": "USD"}
        book = {"positions": {}, "head_trader_tickets": []}
        with mock.patch.dict(sys.modules, {"yfinance": yf}), mock.patch.object(tb, "load", lambda k: {"cash": 1e6} if k == tb.GCL else book), \
             mock.patch.object(tb, "firm_state", lambda: (1.8e6, -1.0, 1.8e6)), mock.patch.object(tb, "time_block", lambda s, n: None), \
             mock.patch.object(tb, "eligible", lambda q: True), mock.patch.object(tb.fb, "fetch_prices", lambda s, require_executable=False: ({s[0]: dict(q)}, {})), \
             mock.patch.object(tb.tfo_heat, "clip_heat", lambda *a: (0.0, [])), mock.patch.object(tb, "SPREAD_RETRY_SLEEP", 0), \
             mock.patch.object(tb.quote_check, "nasdaq_quote", lambda sym, now=None: nasdaq):
            self.last_info = tb.gates("AAPL", usd, "mega-tech")[2]
            return tb.gates("AAPL", usd, "mega-tech")[0]
    def test_nasdaq_primary_beats_bad_yahoo(self):  # HT 2026-09-30: fresh Nasdaq first, Yahoo junk ignored
        fails = self.gates(339.74, 339.78, 333.07, nasdaq={"bid": 333.06, "ask": 333.09, "last": 333.07, "source": "nasdaq"})
        self.assertFalse([f for f in fails if "quote_inconsistent" in f], fails)
        self.assertEqual(self.last_info["quote_source"], "nasdaq"); self.assertAlmostEqual(self.last_info["spread_bp"], 0.9, places=1)
    def test_nasdaq_inconsistent_fails_closed_no_yahoo_shopping(self):
        fails = self.gates(333.06, 333.09, 333.07, nasdaq={"bid": 339.74, "ask": 339.78, "last": 339.76, "source": "nasdaq"})
        self.assertTrue(any("quote_inconsistent" in f for f in fails), fails)
    def test_yahoo_fallback_when_nasdaq_unavailable(self):
        fails = self.gates(333.06, 333.09, 333.07, nasdaq=None)
        self.assertFalse([f for f in fails if "quote_inconsistent" in f], fails); self.assertEqual(self.last_info["quote_source"], "yahoo")
    def test_locked_quote_passes_but_not_big_tier(self):  # HT 2026-09-30: locked = 0bp pass, never the >$300k <=5bp tier
        fails = self.gates(333.31, 333.31, 333.31)
        self.assertFalse([f for f in fails if "quote_inconsistent" in f or "spread" in f.lower() and "clip" not in f], fails)
        fails = self.gates(333.31, 333.31, 333.31, usd=400000)
        self.assertTrue(any("clip >$300k" in f for f in fails), fails)
    def test_inconsistent_quote_fails_closed(self):
        fails = self.gates(339.74, 339.78, 332.89)
        self.assertTrue(any(f.startswith("quote_inconsistent") for f in fails), fails)
    def test_consistent_quote_passes_spread(self):
        fails = self.gates(332.88, 332.90, 332.89)
        self.assertFalse(any("quote_inconsistent" in f or "spread" in f for f in fails), fails)


class CryptoGateUsesQuoteCheck(unittest.TestCase):
    def test_crypto_inconsistent_fails(self):
        import tfo_crypto as tc
        q = {"pair": "SOL/USD", "bid": 117.0, "ask": 117.02, "last": 125.0, "mid": 117.01, "spread_bp": 1.7, "dollar_vol24": 5e7,
             "open_utc_day": 117.0, "trade_age_sec": 1, "tape": "LIVE", "vol24": 1, "utc": "x"}
        with mock.patch.object(tc, "quote", lambda p: q), mock.patch.object(tc, "avg10_vol", lambda p: 5e7), \
             mock.patch.object(tc, "book", lambda: {"positions": {}, "mark_px_usd": {}, "head_trader_tickets": []}), \
             mock.patch.object(tc, "load", lambda k: {"cash": 1e6, "positions": {}}):
            try:
                fails = tc.gates("SOL/USD", 50000, "crypto-alt")[0]
            except Exception as e:
                self.skipTest("crypto gates need more desk state offline: %s" % e)
        self.assertTrue(any(str(f).startswith("quote_inconsistent") for f in fails), fails)


def plan_doc(day="2026-09-30", market="US"):
    c = lambda r, s, role, usd, theme, alt=None: {"rank": r, "symbol": s, "role": role, "planned_usd": usd, "theme": theme, "label": "beta",
                                                  "setup": "preopen_hunt_momentum_equity", "why": "x", "ref_price": 100.0, "alt_usd_if_substituted": alt}
    return {"market": market, "date_local": day, "plan": {"primaries": ["ISRG", "TMO", "SHEL", "PG"]},
            "candidates": [c(1, "ISRG", "primary", 135000.0, "us-health"), c(2, "TMO", "primary", 135000.0, "us-health"),
                           c(3, "SHEL", "primary", 135000.0, "adr-europe"), c(4, "PG", "primary", 135000.0, "us-consumer"),
                           c(5, "DE", "alternate", 0.0, "us-industrial", 135000.0), c(6, "AAPL", "alternate", 0.0, "mega-tech", 135000.0)]}


class StagedPlanFire(unittest.TestCase):
    def setUp(self):
        import tfo_watchlist as wl
        self.wl = wl; self.tmp = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, self.tmp)
        self.calls = []; self.fail = set(); self.held_today = set()
        firm = {"firm_equity_usd": 1.8e6, "headroom_notional_usd": 5e6, "unreserved_powder_usd": 5e6, "crypto_stack_room_usd": 0}
        for p in (mock.patch.object(wl, "R", self.tmp), mock.patch.object(wl, "FIRE_SOURCE_LOG", os.path.join(self.tmp, "src.jsonl")),
                  mock.patch.object(wl, "now_utc", lambda: WED_2340.astimezone(dt.timezone.utc)),
                  mock.patch.object(wl, "wl_lock", lambda *a, **k: contextlib.nullcontext()), mock.patch.object(wl, "read_doc", lambda: {}),
                  mock.patch.object(wl, "legacy_held", lambda: (set(), {})), mock.patch.object(wl, "fire_trend", lambda mk, c, now: ([], 1.0)),
                  mock.patch.object(wl, "firm_snapshot", lambda: dict(firm)), mock.patch.object(wl, "fire_news", lambda mk, c, now: ([], [], {})),
                  mock.patch.object(wl, "tag_fill", lambda *a: None), mock.patch.object(wl, "_held_today", lambda s, now: s in self.held_today),
                  mock.patch.object(wl, "fee_est", lambda *a, **k: (5.0, "t"))):
            p.start(); self.addCleanup(p.stop)
    def write(self, doc, name="preopen_watchlist_US_20260930.json"):
        json.dump(doc, open(os.path.join(self.tmp, name), "w"))
    def runner(self, argv):
        sym, usd, ex = argv[2], float(argv[3]), "--execute" in argv
        self.calls.append((sym, usd, ex))
        ok = sym not in self.fail
        logs = ["LOG 2026-09-30 23:40 BUY %s 1 100 LIVE fundamental_book %.2f 0.00 0.00 1.00" % (sym, usd)] if ok and ex else []
        return {"passed": ok, "fails": [] if ok else ["quote_inconsistent: missing side"], "info": {"px_usd": 100.0}, "logs": logs, "stdout": ""}
    def fire(self, execute=True):
        out = io.StringIO()
        with contextlib.redirect_stdout(out): logs = self.wl.cmd_fire("US", execute, 5, runner=self.runner)
        return logs, out.getvalue()
    def executed(self): return [(s, u) for s, u, ex in self.calls if ex]

    def test_plan_used_primaries_in_order_capped_at_plan(self):
        self.write(plan_doc()); logs, out = self.fire()
        self.assertIn("SOURCE US: staged plan", out)
        # 2026-10-04 TFO Risk audit (HT adopted 21:30 AEDT): $135k plan slots are capped by the $5k planned-loss cap at the -5% stop -> $90k
        self.assertEqual(self.executed(), [("ISRG", 90000.0), ("TMO", 90000.0), ("SHEL", 90000.0), ("PG", 90000.0)])
        self.assertEqual(json.loads(open(os.path.join(self.tmp, "src.jsonl")).readline())["source"], "staged_plan")
    def test_never_upsizes(self):
        d = plan_doc(); d["candidates"][0]["planned_usd"] = 50000.0; self.write(d); self.fire()
        self.assertEqual(self.executed()[0], ("ISRG", 50000.0))
    def test_alternate_only_for_failed_primary(self):
        self.fail = {"TMO"}; self.write(plan_doc()); self.fire()
        self.assertEqual([s for s, _ in self.executed()], ["ISRG", "DE", "SHEL", "PG"])
        self.assertEqual(dict(self.executed())["DE"], 90000.0)   # 2026-10-04 planned-loss cap (was 135000.0)
    def test_retry_is_idempotent_no_double_buy(self):
        self.fail = {"TMO"}; self.write(plan_doc()); self.fire()
        self.calls.clear(); self.fail = set(); self.held_today = {"ISRG", "DE", "SHEL", "PG"}
        logs, out = self.fire()
        self.assertEqual(self.executed(), []); self.assertIn("DONE TMO slot filled earlier by DE", out)
    def test_held_today_primary_is_not_rebought_nor_substituted(self):
        self.held_today = {"ISRG"}; self.write(plan_doc()); self.fire()
        self.assertEqual([s for s, _ in self.executed()], ["TMO", "SHEL", "PG"])
    def test_preview_executes_nothing(self):
        self.write(plan_doc()); self.fire(execute=False)
        self.assertEqual(self.executed(), []); self.assertFalse(os.path.exists(os.path.join(self.tmp, "preopen_fire_state_US_20260930.json")))
    def test_no_plan_or_wrong_date_falls_back_to_auto(self):
        self.write(plan_doc(day="2026-09-29"))           # yesterday's plan under today's name: rejected
        logs, out = self.fire()
        self.assertNotIn("staged plan", out); self.assertEqual(self.calls, [])
        self.assertIn("no watchlist built", out)
    def test_alternate_role_prefix_and_failed_alt_not_retried(self):
        d = plan_doc(); d["candidates"].append({"rank": 7, "symbol": "ABBV", "role": "alternate (only if ISRG or TMO drops)", "planned_usd": 0.0,
                                                "theme": "us-health", "label": "beta", "why": "x", "ref_price": 100.0, "alt_usd_if_substituted": 135000.0})
        self.fail = {"ISRG", "TMO", "DE", "AAPL"}; self.write(d); self.fire()
        tried = [s for s, u, ex in self.calls if not ex]
        self.assertEqual(tried.count("DE"), 1); self.assertIn("ABBV", tried)
        self.assertEqual([s for s, _ in self.executed()], ["ABBV", "SHEL", "PG"])
    def test_all_fail_buys_nothing(self):
        self.fail = {"ISRG", "TMO", "SHEL", "PG", "DE", "AAPL"}; self.write(plan_doc()); logs, _ = self.fire()
        self.assertEqual(logs, []); self.assertEqual(self.executed(), [])


class GclTopLevelPeak(unittest.TestCase):
    def book(self, cash, flow, peak=418723.8, pf=295280.7):
        return {"name": "gap_close_levered_book", "book_key": "gap_close_levered_book", "cash": cash, "holdings": {},
                "peak_equity": peak, "peak_net_flow_usd": pf, "frozen": False,
                "capital_events": [{"type": "capital_transfer", "usd": flow}]}
    def test_outflow_is_not_a_drawdown(self):
        import tfo_equity_refresh as ter, gap_close_levered_book as G
        b = self.book(240813.88, 155268.81)
        ter._risk_fields(b, 240813.88, "2026-09-29T14:00:00Z")
        self.assertEqual(b["peak_equity"], 418723.8); self.assertEqual(b["peak_net_flow_usd"], 295280.7)
        self.assertAlmostEqual(b["drawdown_pct"], -9.05, places=1)
        G._mark(b, {}); self.assertFalse(b["frozen"])
    def test_inflow_does_not_inflate_peak_then_outflow_freeze(self):
        import tfo_equity_refresh as ter, gap_close_levered_book as G
        b = self.book(666819.02, 581273.95)                   # London proceeds in: raw equity 666.8k
        ter._risk_fields(b, 666819.02, "t1"); self.assertEqual(b["peak_equity"], 418723.8)   # no raw-peak bump
        b["cash"] = 240813.88; b["capital_events"] = [{"type": "capital_transfer", "usd": 155268.81}]   # META/AAPL out
        for _ in range(2):
            ter._risk_fields(b, 240813.88, "t2"); G._mark(b, {})
        self.assertFalse(b["frozen"]); self.assertAlmostEqual(b["drawdown_pct"], -9.05, places=1)
    def test_real_gain_moves_peak_and_flow_together(self):
        import tfo_equity_refresh as ter
        b = self.book(500000.0, 295280.7, peak=400000.0, pf=295280.7)
        ter._risk_fields(b, 500000.0, "t"); self.assertEqual((b["peak_equity"], b["peak_net_flow_usd"]), (500000.0, 295280.7))
    def test_books_without_flow_pair_keep_raw_peak(self):
        import tfo_equity_refresh as ter
        b = {"cash": 10.0, "peak_equity": 5.0}; ter._risk_fields(b, 10.0, "t"); self.assertEqual(b["peak_equity"], 10.0)


if __name__ == "__main__":
    unittest.main()
