import unittest
"""Review fixes 27 Oct 2026 (research/hunt_review_20261027/REPORT.md top-10, Brett-approved). Plain unittest, offline, no orders."""
import os, sys, unittest, datetime as dt, math, random, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.argv = [sys.argv[0]]
import hunt_clearance as HC

def _series(n, seed, start=100.0):
    r = random.Random(seed); px = [start]
    for _ in range(n - 1): px.append(px[-1] * (1 + r.gauss(0, 0.02)))
    return px

class CorrByDate(unittest.TestCase):
    """Review §2: correlation check aligned arrays by POSITION, not by date."""
    def test_identical_series_with_holiday_gap_stays_correlated(self):
        days = [(dt.date(2026, 7, 1) + dt.timedelta(d)).isoformat() for d in range(61)]
        a = _series(61, 1); da = list(days)
        # b = same prices but one session missing (holiday on b's exchange) -> positional alignment shifts every bar before it
        b = a[:20] + a[21:]; db = days[:20] + days[21:]
        self.assertAlmostEqual(HC.corr(a, b, da, db), 1.0, 6)
        self.assertLess(HC.corr(a, b), 0.95)            # legacy positional path (no dates) is NOT 1.0 -> the bug this fixes
    def test_disjoint_dates_give_no_corr(self):
        a = _series(40, 2); b = _series(40, 3)
        da = [f"2026-01-{i:02d}" for i in range(1, 32)] + [f"2026-02-{i:02d}" for i in range(1, 10)]
        db = [f"2025-01-{i:02d}" for i in range(1, 32)] + [f"2025-02-{i:02d}" for i in range(1, 10)]
        self.assertIsNone(HC.corr(a, b, da, db))
    def test_legacy_without_dates_unchanged_and_zero_close_safe(self):
        a = _series(50, 4); self.assertAlmostEqual(HC.corr(a, a), 1.0, 9)
        z = list(a); z[10] = 0.0; self.assertIsNone(HC.corr(z, a))
    def test_risk_review_uses_dates(self):
        days = [(dt.date(2026, 7, 1) + dt.timedelta(d)).isoformat() for d in range(61)]
        a = _series(61, 5); b = a[:20] + a[21:]
        BR = {"AAA": dict(sector_key="S1", _closes=a, _dates=days), "BBB": dict(sector_key="S2", _closes=b, _dates=days[:20] + days[21:])}
        mk = lambda tk: dict(ticker=tk, direction="long", trigger=10.0, max_fill=10.0, stop=9.5, shares=100)
        out, _ = HC.risk_review([mk("AAA"), mk("BBB")], BR, 0.65)
        self.assertEqual(out["BBB"]["verdict"], "FAIL"); self.assertTrue(any("corr" in r for r in out["BBB"]["reasons"]))

import tempfile, json as _json
import hunt48_room as HR
class DataFailNotRuleFail(unittest.TestCase):
    """Review fix #3: retry + disk cache for daily bars; DATA_FAIL != RULE_FAIL (Asia/ASX)."""
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.saved = (HR._fetch, HR._sleep, HR.CACHE_DIR, HR.FAIL_MEMO_S)
        HR.CACHE_DIR = self.d; HR._sleep = lambda s: None; HR._CACHE.clear(); HR._FAIL_AT.clear(); HR.DATA_STATUS.clear()
    def tearDown(self): HR._fetch, HR._sleep, HR.CACHE_DIR, HR.FAIL_MEMO_S = self.saved; HR._CACHE.clear(); HR._FAIL_AT.clear()
    def test_retry_then_success_writes_cache(self):
        calls = []
        def f(sym):
            calls.append(sym)
            if len(calls) < 3: raise RuntimeError("yf down")
            return [dict(h=2.0, l=1.0, c=1.5)] * 5
        HR._fetch = f
        self.assertEqual(len(HR.daily_fetch("AAA")), 5); self.assertEqual(len(calls), 3); self.assertEqual(HR.DATA_STATUS["AAA"], "ok")
        self.assertTrue(os.path.exists(HR._cache_path("AAA")))
    def test_total_failure_uses_fresh_cache_only(self):
        bars = [dict(h=2.0, l=1.0, c=1.5)] * 7
        now = dt.datetime.now(dt.timezone.utc)
        _json.dump(dict(sym="BBB", fetched_utc=(now - dt.timedelta(hours=3)).isoformat(), bars=bars), open(HR._cache_path("BBB"), "w"))
        _json.dump(dict(sym="CCC", fetched_utc=(now - dt.timedelta(hours=40)).isoformat(), bars=bars), open(HR._cache_path("CCC"), "w"))
        HR._fetch = lambda sym: []
        self.assertEqual(HR.daily_fetch("BBB"), bars); self.assertEqual(HR.DATA_STATUS["BBB"], "cache")
        self.assertEqual(HR.daily_fetch("CCC"), []); self.assertEqual(HR.DATA_STATUS["CCC"], "fail")     # stale cache never used
    def test_failure_not_memoised_for_whole_process(self):
        HR._fetch = lambda sym: []; HR.FAIL_MEMO_S = 0
        self.assertEqual(HR.daily_fetch("DDD"), [])
        HR._fetch = lambda sym: [dict(h=2.0, l=1.0, c=1.5)] * 3
        self.assertEqual(len(HR.daily_fetch("DDD")), 3)                                                   # recovered on the next call
    def test_is_data_fail(self):
        self.assertTrue(HR.is_data_fail("20d-low/5d-extension data missing -> BENCH (B1 fail closed)"))
        self.assertTrue(HR.is_data_fail("no daily bars: structure room unknown -> BENCH (fail closed)"))
        self.assertFalse(HR.is_data_fail("RSI 28 < 30: oversold short (B1)"))
    def test_asia_bench_data_fail_kept_rule_fail_disarmed(self):
        import test_asia_hunt48 as TA, asia_markets as AM
        d, AH = TA.setup("jp", [TA.row("jp", "1111.T", "Tech"), TA.row("jp", "2222.T", "Banks")])
        AH.run("jp", now=TA.PRE, fetch=lambda y, s: [], base=d, live=TA.NOLIVE)
        why = {"1111.T": ["20d-low/5d-extension data missing -> BENCH (B1 fail closed)"], "2222.T": ["RSI 28 < 30: oversold short (B1)"]}
        AH.review = lambda mkt, rows, earn, halted, now, he: ("long", [dict(sym=s, dir="long", rank_score=4.0, score=4.0, sector=r["sector"], passed=False, why=why[s]) for s, r in rows.items() if r["cls"].endswith("_equity")])
        st = AH.run("jp", now=TA.PRE + dt.timedelta(minutes=2), fetch=lambda y, s: [], base=d, live=TA.NOLIVE)
        L = {t["sym"]: t for t in _json.load(open(AH.paths("jp", d)[1]))["trades"]}
        self.assertEqual(L["1111.T"]["status"], "BENCH"); self.assertIn("data missing", L["1111.T"]["data_fail"])
        self.assertEqual(L["2222.T"]["status"], "DISARMED")
        self.assertEqual(st["counts"]["ARMED"], 0); self.assertEqual(st["data_fail"]["bench_waiting"], ["1111.T"])
    def test_asx_data_fail_kept_rule_fail_disarmed(self):
        import test_asx_hunt48 as TX
        d, A = TX.setup([TX.row("Q.AX", "Banks"), TX.row("R.AX", "Tech")])
        A.run(now=TX.TUE_PRE, fetch=lambda y, s: [], base=d)
        L0 = {t["sym"]: t["status"] for t in _json.load(open(A.paths(d)[1]))["trades"]}
        why = {"Q.AX": ["20d-low/5d-extension data missing -> BENCH (B1 fail closed)"], "R.AX": ["RSI 28 < 30: oversold short (B1)"]}
        real = A.review
        A.review = lambda rows, ann, earn, halted, now, he: (lambda reg, revs: (reg, [dict(v, passed=False, why=why[v["sym"]]) if v["sym"] in why else v for v in revs]))(*real(rows, ann, earn, halted, now, he))
        A.run(now=TX.TUE_PRE + dt.timedelta(minutes=2), fetch=lambda y, s: [], base=d)
        L = {t["sym"]: t for t in _json.load(open(A.paths(d)[1]))["trades"]}
        self.assertIn(L0["Q.AX"], ("ARMED", "BENCH"))
        self.assertEqual(L["Q.AX"]["status"], "BENCH"); self.assertIn("data missing", L["Q.AX"]["data_fail"])
        self.assertEqual(L["R.AX"]["status"], "DISARMED")

class PromoteNeedsLivePrice(unittest.TestCase):
    """Review fix #2: promote() substituted scan_px / a synthetic midpoint when there was no live price, then ran recheck_us."""
    def setUp(self):
        import hunt_orchestrator as HO, hunt48_short_paper as H
        self.HO, self.H = HO, H
        self.d = tempfile.mkdtemp(); self.saved = {k: getattr(HO, k) for k in ("BASE", "ORCH", "EVENTS", "us_ledger", "apply_arm")}
        self.Hs = {k: getattr(H, k) for k in ("bars_15m", "bars_daily")}
        HO.BASE = self.d; HO.ORCH = os.path.join(self.d, "state", "hunt_orch"); os.makedirs(os.path.join(HO.ORCH, "done")); HO.EVENTS = os.path.join(HO.ORCH, "events.jsonl")
        HO.us_ledger = lambda b: {"trades": []}; HO.apply_arm = lambda *a, **k: True
        H.bars_daily = lambda s: [dict(h=12.0, l=11.5, c=11.8)] * 260
        self.b = dict(ticker="ZZZ", direction="short", trigger=10.0, max_fill=9.95, stop=10.5, t1=9.5, t2=9.0, shares=100, sector="Tech", scan_px=10.1, rank=1, state="READY", top_decile=True)
    def tearDown(self):
        for k, v in self.saved.items(): setattr(self.HO, k, v)
        for k, v in self.Hs.items(): setattr(self.H, k, v)
    def test_no_live_price_stays_ready_never_staged(self):
        self.H.bars_15m = lambda s: []
        st = {"main": [dict(self.b)]}
        out = self.HO.promote("main", "20261027", st, dt.datetime(2026, 10, 27, 13, 20, tzinfo=dt.timezone.utc), "pre", None, {}, [], pre_fn=lambda *a: dict(ok=True, why=[]))
        self.assertEqual(out, []); self.assertEqual(st["main"][0]["state"], "READY"); self.assertIn("no live price", st["main"][0]["last_block"])
    def test_real_price_through_stop_is_invalid(self):
        self.H.bars_15m = lambda s: [dict(c=10.6)]   # short already through its 10.5 stop: the old scan_px 10.1 substitute would have hidden this
        st = {"main": [dict(self.b)]}
        out = self.HO.promote("main", "20261027", st, dt.datetime(2026, 10, 27, 13, 20, tzinfo=dt.timezone.utc), "pre", None, {}, [], pre_fn=lambda *a: dict(ok=True, why=[]))
        self.assertEqual(out, []); self.assertEqual(st["main"][0]["state"], "INVALID")

class UnknownEarningsFailsSingleStock(unittest.TestCase):
    """Review fix #2: UNKNOWN / unparseable / stale-last-report earnings = FAIL for single stocks; ETFs note only."""
    @classmethod
    def setUpClass(cls):
        a = list(sys.argv); sys.argv = [sys.argv[0]]
        try:
            import hunt_clearance as HC
        finally: sys.argv = a
        cls.HC = HC
    NOW = dt.datetime(2026, 10, 27, 12, 0, tzinfo=dt.timezone.utc)
    def review(self, earn, ac="equity"):
        s = dict(ticker="XYZ", direction="long", trigger=50.0, stop=48.0, time_exit_utc=(self.NOW + dt.timedelta(days=3)).isoformat())
        b = dict(asset_class=ac, sweep=dict(rsi="50", roc5="0", atr_pct="3", venue="ibkr"), technical={}, calendar=dict(next_earnings_utc=earn) if earn is not False else None)
        return self.HC.behavior_review(s, b, self.NOW)
    def test_unknown_fails_equity(self):
        for earn in (None, "UNKNOWN", "not-a-date", False):
            v = self.review(earn); self.assertEqual(v["verdict"], "FAIL", (earn, v)); self.assertTrue(any("UNKNOWN" in r for r in v["reasons"]), v)
    def test_stale_last_report_fails_recent_report_passes(self):
        v = self.review("2026-08-11T20:00:00+00:00"); self.assertEqual(v["verdict"], "FAIL", v)
        v = self.review("2026-10-22T20:00:00+00:00"); self.assertEqual(v["verdict"], "PASS", v)
        v = self.review("2026-12-10T20:00:00+00:00"); self.assertEqual(v["verdict"], "PASS", v)
    def test_etf_unknown_is_note_only(self):
        for earn in (None, "UNKNOWN"):
            v = self.review(earn, "etf"); self.assertEqual(v["verdict"], "PASS", v); self.assertIn("earnings date UNKNOWN", v["reasons"])

class SetupHashSameDay(unittest.TestCase):
    """Review fix #2: clearance bound to the exact setup (hash) and same-day only."""
    def test_hash_changes_with_levels_not_shares(self):
        import setup_hash as SH
        a = dict(direction="short", trigger=10.0, max_fill=9.95, stop=10.5, t1=9.5, t2=9.0, shares=100)
        self.assertEqual(SH.setup_hash(a), SH.setup_hash(dict(a, shares=50)))
        self.assertNotEqual(SH.setup_hash(a), SH.setup_hash(dict(a, stop=10.6)))
        self.assertNotEqual(SH.setup_hash(a), SH.setup_hash(dict(a, direction="long")))
    def _ledger(self, H, tmp, cleared, armed):
        H.LEDGER = os.path.join(tmp, "ledger.json"); H.EVENTS = os.path.join(tmp, "events.jsonl"); H.STATUS = os.path.join(tmp, "status.json")
        L = H.new_ledger("t"); now = armed
        H.upsert_trade(L, dict(ticker="ZZZ", direction="short", trigger=10.0, max_fill=9.95, stop=10.5, t1=9.5, t2=9.0, shares=100, sector="Tech", cleared=cleared), now)
        H.atomic(H.LEDGER, L); return L
    def test_engine_cuts_armed_row_after_clearance_day(self):
        import hunt48_short_paper as H
        sv = {k: getattr(H, k) for k in ("LEDGER", "EVENTS", "STATUS", "now_utc", "bars_15m", "live_audusd", "write_status")}
        tmp = tempfile.mkdtemp()
        try:
            self._ledger(H, tmp, "20261027", dt.datetime(2026, 10, 27, 12, 0, tzinfo=dt.timezone.utc))
            H.bars_15m = lambda s: []; H.live_audusd = lambda f: 0.65; H.write_status = lambda *a: None
            H.now_utc = lambda: dt.datetime(2026, 10, 27, 23, 59, tzinfo=dt.timezone.utc); H.tick(force=True)
            self.assertEqual(json.load(open(H.LEDGER))["trades"][0]["status"], "ARMED")   # still inside its NY session
            H.now_utc = lambda: dt.datetime(2026, 10, 28, 0, 1, tzinfo=dt.timezone.utc); H.tick(force=True)
            t = json.load(open(H.LEDGER))["trades"][0]; self.assertEqual(t["status"], "CUT"); self.assertIn("expired", t["cut_reason"])
        finally:
            for k, v in sv.items(): setattr(H, k, v)
    def test_apply_plan_refuses_expired_clearance(self):
        import hunt48_short_paper as H
        sv = {k: getattr(H, k) for k in ("LEDGER", "EVENTS", "bars_15m")}
        tmp = tempfile.mkdtemp()
        try:
            H.LEDGER = os.path.join(tmp, "ledger.json"); H.EVENTS = os.path.join(tmp, "events.jsonl"); H.bars_15m = lambda s: []
            p = os.path.join(tmp, "plan.json")
            json.dump(dict(set=[dict(ticker="ZZZ", direction="short", trigger=10.0, max_fill=9.95, stop=10.5, t1=9.5, t2=9.0, shares=100, cleared="20261026"),
                                dict(ticker="YYY", direction="short", trigger=10.0, max_fill=9.95, stop=10.5, t1=9.5, t2=9.0, shares=100, cleared="20261027"),
                                dict(ticker="XXX", direction="short", trigger=10.0, max_fill=9.95, stop=10.5, t1=9.5, t2=9.0, shares=100)]), open(p, "w"))
            L = H.apply_plan(p, dt.datetime(2026, 10, 27, 13, 0, tzinfo=dt.timezone.utc))
            self.assertEqual(sorted(t["ticker"] for t in L["trades"]), ["XXX", "YYY"])
            self.assertIn("clearance 20261026 expired", open(H.EVENTS).read())
        finally:
            for k, v in sv.items(): setattr(H, k, v)
    def test_finalize_blocks_on_level_change(self):
        tmp = tempfile.mkdtemp(); a = list(sys.argv); sys.argv = [sys.argv[0]]
        try:
            import hunt_clearance as HC
        finally: sys.argv = a
        import setup_hash as SH
        sv = {k: getattr(HC, k) for k in ("BASE", "OUT", "BOOKS", "D")}
        try:
            HC.BASE = tmp; HC.OUT = os.path.join(tmp, "out"); HC.BOOKS = {"main": "hunt48_short"}; HC.D = "20261027"
            os.makedirs(HC.OUT); os.makedirs(os.path.join(tmp, "state", "hunt48_short"))
            json.dump(dict(trades=[]), open(os.path.join(tmp, "state", "hunt48_short", "ledger.json"), "w"))
            rev = dict(ticker="AAA", direction="short", trigger=10.0, max_fill=9.95, stop=10.5, t1=9.5, t2=9.0, shares=100)
            plan = dict(set=[dict(rev), dict(rev, ticker="BBB", stop=10.7)])   # BBB re-levelled after review
            json.dump(plan, open(os.path.join(tmp, "state", "hunt48_short", "plan_bench_20261027.json"), "w"))
            P = lambda: dict(verdict="PASS", reasons=[])
            rows = {tk: dict(internal="PASS", RISK=P(), BEHAVIOR=P(), SPECIALIST=P(), setup_hash=SH.setup_hash(rev)) for tk in ("AAA", "BBB")}
            json.dump(dict(meta={}, books=dict(main=dict(label="x", rows=rows))), open(os.path.join(HC.OUT, "internal_verdicts.json"), "w"))
            json.dump(dict(main={tk: dict(GPT="PASS", CLAUDE="PASS") for tk in ("AAA", "BBB")}), open(os.path.join(HC.OUT, "external_reviews.json"), "w"))
            S = HC.finalize()
            self.assertEqual(S["main"]["AAA"]["final"], "CLEARED"); self.assertEqual(S["main"]["BBB"]["final"], "BLOCKED")
            cp = json.load(open(os.path.join(HC.OUT, "cleared_plan.json"))); self.assertEqual([s["ticker"] for s in cp["set"]], ["AAA"])
            self.assertEqual(cp["set"][0]["setup_hash"], SH.setup_hash(rev)); self.assertEqual(cp["set"][0]["cleared"], "20261027")
        finally:
            for k, v in sv.items(): setattr(HC, k, v)

class T1BeforeThesisExit(unittest.TestCase):
    """Review §2: a bar that tags T1 intrabar and then closes back through the level used to thesis-exit everything, skipping T1."""
    def _t(self, d):
        import hunt48_short_paper as H
        e = dt.datetime(2026, 10, 27, 14, 0, tzinfo=dt.timezone.utc)
        sh = d == "short"
        t = dict(id=1, ticker="XYZ", status="FILLED", direction=d, trigger=50.5 if sh else 49.5, max_fill=49.9 if sh else 50.1, entry=50.0, entry_utc=e.isoformat(),
                 shares=100, open_qty=100, half_done=False, trail=None, stop=52.0 if sh else 48.0, t1=48.0 if sh else 52.0, t2=46.0 if sh else 54.0,
                 lowest=50.0, atr=1.0, last_px=50.0, ext_days=0, time_exit_utc=(e + dt.timedelta(days=3)).isoformat(), exits=[], realised_usd=0.0,
                 fees_usd=1.0, borrow_usd=0.0, armed_utc=(e - dt.timedelta(hours=1)).isoformat(), risk_cap_aud=2500.0, sector="TECH", _last_bar=e.isoformat())
        bar = dict(start=e + dt.timedelta(minutes=15), o=50.0, h=51.2 if sh else 52.1, l=47.9 if sh else 48.8, c=51.1 if sh else 48.9)   # fix1 2026-10-28: beyond level +/- 0.5 ATR
        return H, t, bar, e
    def test_short_t1_then_thesis(self):
        H, t, bar, e = self._t("short")
        ev = H.manage(t, [bar], e + dt.timedelta(hours=1))
        self.assertEqual(ev, ["t1", "thesis_exit"]); self.assertEqual([x["why"] for x in t["exits"]], ["1R half", "thesis exit"]); self.assertEqual(t["status"], "CLOSED")
    def test_long_t1_then_thesis(self):
        H, t, bar, e = self._t("long")
        ev = H.manage(t, [bar], e + dt.timedelta(hours=1))
        self.assertEqual(ev, ["t1", "thesis_exit"]); self.assertEqual(t["exits"][0]["qty"], 50); self.assertEqual(t["status"], "CLOSED")
    def test_thesis_without_t1_unchanged(self):
        H, t, bar, e = self._t("short"); bar = dict(bar, l=49.8)
        self.assertEqual(H.manage(t, [bar], e + dt.timedelta(hours=1)), ["thesis_exit"]); self.assertEqual(t["open_qty"], 0)

class RegimeLabel(unittest.TestCase):
    def test_write_status_regime_label_exact(self):
        import hunt48_short_paper as H
        sv = H.STATUS; H.STATUS = os.path.join(tempfile.mkdtemp(), "s.json")
        try: st = H.write_status(H.new_ledger("x"), 0.7, dt.datetime(2026, 10, 27, 15, 0, tzinfo=dt.timezone.utc))
        finally: H.STATUS = sv
        self.assertEqual(st["regime"], "US: longs + shorts per name (own trend/RS/catalyst); SPY vs 50-day = rank input only, counter-regime names ranked lower")

class OneRoomDefinition(unittest.TestCase):
    """Review fix #5: US build_candidate uses arming's room caps (cost_R <= 0.15, >= 200 daily bars)."""
    def row(self, **k):
        r = dict(sym="ABC", venue="ibkr", px="100", atr="2", atr_pct="2", lo10="99", pdl="99.5", hi20="105", pdh="101", rsi="45", roc5="-1", adv_usd="5e8", rank_score="5", sector="Tech")
        r.update(k); return r
    def flat(self, n): return [dict(h=101.0, l=100.0, c=100.5) for _ in range(n)]
    def test_us_room_needs_200_bars(self):
        import hunt_orchestrator as HO
        c, why = HO.build_candidate(self.row(), "short", 0.66, self.flat(140), us_room=True)
        self.assertTrue(any("200" in w or "bars" in w for w in why), why)
        c, why = HO.build_candidate(self.row(), "short", 0.66, self.flat(260), us_room=True); self.assertEqual(why, [], why)
    def test_us_hunt_call_site_uses_us_room(self):
        import hunt_orchestrator as HO, inspect
        self.assertIn("us_room=True", inspect.getsource(HO.us_build_plan))
    def test_default_unchanged_for_other_callers(self):
        import hunt_orchestrator as HO
        c, why = HO.build_candidate(self.row(), "short", 0.66, self.flat(140)); self.assertEqual(why, [])

class EdgeCohort(unittest.TestCase):
    """Review fix #1: frozen-version shadow cohort (record + daily-bar replay score). Offline."""
    def setUp(self):
        import edge_cohort as EC, setup_hash as SH; self.EC, self.SH = EC, SH
        self.b = tempfile.mkdtemp(); os.makedirs(os.path.join(self.b, "state", "hunt48_short")); os.makedirs(os.path.join(self.b, "state", "clearance", "20261027"))
        self.rows = [dict(ticker="AAA", direction="short", trigger=10.0, max_fill=9.9, stop=10.5, t1=9.5, t2=9.0, cost_R=0.05),
                     dict(ticker="BBB", direction="long", trigger=20.0, max_fill=20.2, stop=19.0, t1=21.0, t2=22.0)]
        json.dump(dict(set=self.rows), open(os.path.join(self.b, "state", "hunt48_short", "plan_bench_20261027.json"), "w"))
        json.dump(dict(books=dict(main=dict(AAA=dict(final="CLEARED", setup_hash=SH.setup_hash(self.rows[0])), BBB=dict(final="CLEARED", setup_hash="stale")))),
                  open(os.path.join(self.b, "state", "clearance", "20261027", "summary.json"), "w"))
        open(os.path.join(self.b, "hunt_orchestrator.py"), "w").write("x = 1\n")
    def test_record_is_frozen_and_hash_bound(self):
        p = self.EC.record("20261027", self.b); C = json.load(open(p))
        self.assertEqual(C["n"], 2); self.assertTrue(C["code_version"])
        self.assertEqual([r["cleared"] for r in C["rows"]], [True, False])   # BBB's CLEARED was for other levels
        json.dump(dict(set=[]), open(os.path.join(self.b, "state", "hunt48_short", "plan_bench_20261027.json"), "w"))
        self.EC.record("20261027", self.b); self.assertEqual(json.load(open(p))["n"], 2)   # first record of the day wins
    def test_simulate(self):
        r = self.rows[0]
        self.assertEqual(self.EC.simulate(r, [dict(o=10.2, h=10.3, l=10.1, c=10.2)] * 3)["filled"], False)            # never triggered
        self.assertEqual(self.EC.simulate(r, [dict(o=9.8, h=9.85, l=9.0, c=9.1)] * 3)["filled"], False)               # gapped past max_fill
        s = self.EC.simulate(r, [dict(o=10.1, h=10.6, l=9.9, c=10.4)] * 3); self.assertEqual((s["why"], s["R"]), ("stop", -1.0))   # stop-first
        s = self.EC.simulate(r, [dict(o=10.1, h=10.2, l=9.4, c=9.6), dict(o=9.6, h=9.7, l=8.9, c=9.0), dict(o=9, h=9, l=9, c=9)]); self.assertEqual((s["why"], s["R"]), ("T2", 1.5))
        self.assertIsNone(self.EC.simulate(r, [dict(o=10.1, h=10.2, l=9.95, c=10.0)])["R"])                          # hold not complete
    def test_score_net_R_and_ci(self):
        self.EC.record("20261027", self.b)
        bars = {"AAA": [dict(d="2026-10-27", o=10.1, h=10.2, l=9.4, c=9.6), dict(d="2026-10-28", o=9.6, h=9.7, l=8.9, c=9.0), dict(d="2026-10-29", o=9, h=9, l=9, c=9)],
                "BBB": [dict(d="2026-10-27", o=19.5, h=19.8, l=19.4, c=19.6)] * 3}
        rep = self.EC.score(self.b, fetch=lambda s, d: bars[s])
        self.assertEqual(rep["all"]["fills"], 1); self.assertEqual(rep["all"]["fill_rate"], 0.5)
        self.assertAlmostEqual(rep["cleared"]["net"]["mean_R"], 1.45, 4); self.assertTrue(os.path.exists(os.path.join(self.b, "state", "edge_cohort", "report.json")))
    def test_hunt_us_records_cohort(self):
        import hunt_orchestrator as HO, inspect
        self.assertIn("edge_cohort.record(D, os.path.dirname(os.path.dirname(ORCH)))", inspect.getsource(HO.hunt_us))   # follows a patched ORCH (tests)
    def test_no_plan_records_nothing(self):
        self.assertIsNone(self.EC.record("20261030", self.b)); self.assertFalse(os.path.exists(os.path.join(self.b, "state", "edge_cohort", "20261030.json")))

class FeedAlarms(unittest.TestCase):
    """Review fix #3: scan crash, kraken_pairs 0, empty cat_notes, dead feeds raise local alarms. Offline."""
    def mk(self, kraken=0, notes=("", ""), feeds=None):
        o = tempfile.mkdtemp(); d = os.path.join(o, "us_scan", "20261027"); os.makedirs(d)
        json.dump(dict(universe=dict(kraken_pairs=kraken), feeds=feeds or {"kraken.public": dict(ok=False, note="dns"), "yf.info": dict(ok=True)}), open(os.path.join(d, "scan_report.json"), "w"))
        with open(os.path.join(d, "ranked.csv"), "w") as f: f.write("sym,cat_notes\n" + "".join(f"S{i},{n}\n" for i, n in enumerate(notes)))
        return o
    def test_tonight_case_alarms(self):
        import feed_alarms as FA; o = self.mk()
        c = {a["code"] for a in FA.check_us_scan("20261027", True, "", o)}
        self.assertEqual(c, {"kraken_pairs_zero", "feed_down", "cat_notes_empty"})
        self.assertIn("scan_crash", {a["code"] for a in FA.check_us_scan("20261027", False, "scan rc=1 rows=0", o)})
    def test_healthy_scan_no_alarm_and_record_clears(self):
        import feed_alarms as FA; o = self.mk(kraken=40, notes=("upgrade", ""), feeds={"yf.info": dict(ok=True)})
        self.assertEqual(FA.check_us_scan("20261027", True, "", o), [])
        FA.raise_alarms(o, "us", "20261027", [dict(code="x", msg="y")]); FA.raise_alarms(o, "us", "20261027", [])
        S = json.load(open(os.path.join(o, "feed_alarms.json"))); self.assertEqual(S["us"]["n"], 0)
        self.assertEqual(len(open(os.path.join(o, "feed_alarms.jsonl")).read().splitlines()), 1)
    def test_missing_report_alarms(self):
        import feed_alarms as FA; o = tempfile.mkdtemp()
        self.assertEqual([a["code"] for a in FA.check_us_scan("20261027", True, "", o)], ["scan_report_missing"])
    def test_hunt_crash_raises_alarm(self):
        import hunt_orchestrator as HO
        from unittest import mock
        o = tempfile.mkdtemp(); sv = HO.ORCH; HO.ORCH = o
        try:
            with mock.patch.object(HO, "hunt_us", side_effect=RuntimeError("boom")), mock.patch.object(HO, "event"), \
                 mock.patch.object(HO, "plan"), mock.patch.object(HO, "marker", side_effect=lambda k: os.path.join(o, "m_" + str(k))):
                HO.hunt("us", dt.datetime(2026, 10, 27, 12, 0, tzinfo=dt.timezone.utc))
        finally: HO.ORCH = sv
        S = json.load(open(os.path.join(o, "feed_alarms.json"))); self.assertEqual(S["us"]["alarms"][0]["code"], "hunt_crash")

class StopRule(unittest.TestCase):
    """Review fix #4 (US hunt only): stop >= 1.0 ATR (5% may only trim 1.5 ATR down to 1 ATR); A$ risk via shares; US ATR cap 8%."""
    def row(self, px, atrp, venue="ibkr"):
        a = px * atrp / 100
        return dict(sym="ABC", venue=venue, px=px, atr=a, atr_pct=atrp, lo10=px, pdl=px, hi20=px, pdh=px, adv_usd=1e9, rsi=50, roc5=0)
    def test_floor_holds_and_risk_via_shares(self):
        import event_calendar as EC
        for atrp in (3.0, 4.0, 6.0, 7.5):
            r = self.row(100.0, atrp); lv = EC.stage_levels(r, "short", 2500, 0.70, atr_floor=EC.STOP_ATR_FLOOR)
            dist = lv["stop"] - lv["max_fill"]; self.assertGreaterEqual(dist + 1e-3, r["atr"], atrp); self.assertLessEqual(dist, 1.5 * r["atr"] + 1e-3)
            self.assertLessEqual(lv["shares"] * dist, 2500 * 0.70 + 1e-6); self.assertGreater(lv["shares"] * dist, 2500 * 0.70 - dist - 1e-6)
    def test_default_unchanged_for_other_books(self):
        import event_calendar as EC
        r = self.row(100.0, 6.0); lv = EC.stage_levels(r, "short", 2500, 0.70)
        self.assertAlmostEqual(lv["stop"] - lv["max_fill"], 0.05 * lv["trigger"], 3)             # ASX/Asia/LSE/OANDA keep min(1.5ATR, 5%)
        self.assertIn("ATR 7.0%>6%", EC.gate(self.row(100.0, 7.0, "asx"), "long"))
    def test_us_atr_cap_8_and_build_candidate_floor(self):
        import hunt_orchestrator as HO, event_calendar as EC, inspect
        self.assertFalse([w for w in HO.quick_screen(self.row(100.0, 7.5), "short") if "ATR" in w])
        self.assertIn("ATR 8.5%>8%", HO.quick_screen(self.row(100.0, 8.5), "short"))
        self.assertIn("atr_floor=(EC.STOP_ATR_FLOOR if us_room else None)", inspect.getsource(HO.build_candidate))

class FirmLevelBook(unittest.TestCase):
    """Review fix #6: one book per ticker firm-wide; net direction <= 70% of open risk once >= 3 open (US books)."""
    def setUp(self):
        import hunt48_short_paper as H; self.H = H
    def p(self, tk, d="short", book="hunt48_short_gpt"): return dict(ticker=tk, direction=d, status="FILLED", _book=book)
    def test_one_book_per_ticker(self):
        self.assertIn("already held in hunt48_short_gpt", self.H.firm_gate(dict(ticker="FLG", direction="short"), [], [self.p("FLG")]))
    def test_no_quota_on_first_positions(self):
        self.assertIsNone(self.H.firm_gate(dict(ticker="A", direction="short"), [], [self.p("B")]))          # 2 open, both short: allowed
    def test_net_direction_cap(self):
        H = self.H; oth = [self.p("B"), self.p("C", book="hunt48_short_claude")]
        self.assertIn("firm net direction", H.firm_gate(dict(ticker="A", direction="short"), [], oth))      # 3/3 short = 100%
        self.assertIsNone(H.firm_gate(dict(ticker="A", direction="long"), [], oth))                           # long rebalances (67% short)
        oth = [self.p(x, "short") for x in "BCDEFG"] + [self.p(x, "long") for x in "HIJ"]                    # 6S/3L: +1S = 70% -> ok, 7S/3L+1S = 73%
        self.assertIsNone(H.firm_gate(dict(ticker="A", direction="short"), [], oth))
        self.assertIn("firm net direction", H.firm_gate(dict(ticker="A", direction="short"), [self.p("K", book="own")], oth))
    def test_sibling_ledgers(self):
        H = self.H; root = tempfile.mkdtemp()
        for d in H.FIRM_BOOK_DIRS: os.makedirs(os.path.join(root, d))
        own = os.path.join(root, "hunt48_short", "ledger.json")
        json.dump(dict(trades=[dict(ticker="X", status="FILLED"), dict(ticker="Y", status="ARMED")]), open(os.path.join(root, "hunt48_short_gpt", "ledger.json"), "w"))
        json.dump(dict(trades=[dict(ticker="Z", status="FILLED")]), open(own, "w"))
        self.assertEqual([o["ticker"] for o in H.firm_other_open(own)], ["X"])                               # own book + ARMED excluded
        self.assertNotIn("hunt48_short_claude", H.FIRM_BOOK_DIRS)                                            # claude retired 20261029
        open(os.path.join(root, "hunt48_short_gpt", "ledger.json"), "w").write("{bad")
        with self.assertRaises(RuntimeError): H.firm_other_open(own)                                          # unreadable -> fail closed (tick keeps ARMED)
    def test_undo_and_wired_into_tick(self):
        import inspect
        t = dict(ticker="A", status="FILLED", entry=10.0, entry_utc="x", open_qty=5, fees_usd=2.0, fee_in_usd=1.0)
        self.assertEqual(self.H.firm_undo(t, "firm: test"), "shadow"); self.assertEqual((t["status"], t["shadow_reason"], t["entry"]), ("SHADOW", "firm: test", None))
        self.assertIn("firm_gate(t,", inspect.getsource(self.H.tick))

class ShadowRank(unittest.TestCase):
    """Review fix #7: catalyst x relvol shadow rank, point-in-time, never used for selection."""
    def test_build_frozen_and_ranked(self):
        import shadow_rank as SR
        d = tempfile.mkdtemp(); p = os.path.join(d, "ranked.csv")
        with open(p, "w") as fh:
            fh.write("sym,venue,dir,rank_score,relvol,roc5,atr_pct,cat_score,cat_notes\n")
            fh.write("A,ibkr,long,9,1.0,1,3,0,\nB,ibkr,short,1,4.0,-9,3,1,downgrade\nC,kraken,long,5,9,9,3,0,\nD,ibkr,long,2,nan,,3,,\n")
        q = SR.build("20261027", p, os.path.join(d, "out")); R = json.load(open(q))
        self.assertEqual([r["sym"] for r in R["rows"]], ["B", "A", "D"])                 # kraken excluded; B: 4 x 3ATR x (1+1)
        self.assertEqual((R["rows"][0]["live_rank"], R["researched"], R["mode"][:6]), (3, 1, "SHADOW"))
        open(p, "w").write("sym,venue\n"); SR.build("20261027", p, os.path.join(d, "out")); self.assertEqual(json.load(open(q))["n"], 3)   # frozen
    def test_hunt_us_builds_shadow_only(self):
        import hunt_orchestrator as HO, inspect
        src = inspect.getsource(HO.hunt_us); self.assertIn("SR.build(D,", src)
        self.assertNotIn("shadow_rank", inspect.getsource(HO.us_build_plan))

class CostModel(unittest.TestCase):
    """Review fix #8: SI unit fix + SI/DTC blocks, 0.5x size at DTC>5, per-name borrow in plan cost_R, short dividend cost in clearance."""
    def row(self, **k):
        r = dict(sym="ABC", venue="ibkr", px=50.0, atr=1.5, atr_pct=3.0, lo10=50, pdl=50, hi20=50, pdh=50, adv_usd=1e9, rsi=50, roc5=0, short_pct_float="0.25", days_to_cover="3")
        r.update(k); return r
    def test_si_fraction_and_dtc_blocks(self):
        import hunt_orchestrator as HO
        self.assertEqual(HO.si_pct("0.25"), 25.0); self.assertEqual(HO.si_pct("25"), 25.0); self.assertIsNone(HO.si_pct(""))
        self.assertTrue([w for w in HO.quick_screen(self.row(), "short") if "SI 25%" in w])                 # 0.25 fraction now blocks
        self.assertFalse([w for w in HO.quick_screen(self.row(short_pct_float="0.15"), "short") if "squeeze" in w])
        self.assertTrue([w for w in HO.quick_screen(self.row(short_pct_float="0.1", days_to_cover="11"), "short") if "days to cover" in w])
        self.assertFalse([w for w in HO.quick_screen(self.row(), "long") if "squeeze" in w])                 # longs not blocked
    def test_dtc_half_size(self):
        import hunt_orchestrator as HO
        from unittest import mock
        bars = [dict(o=50, h=51, l=49, c=50)] * 60
        with mock.patch("hunt48_room.exhaustion_block", return_value=[]), mock.patch("hunt48_room.room", return_value=None), \
             mock.patch.object(HO, "event_size_mult", return_value=(1.0, []), create=True):
            c0, _ = HO.build_candidate(self.row(short_pct_float="0.1", days_to_cover="3"), "short", 0.70, bars, us_room=True)
            c1, _ = HO.build_candidate(self.row(short_pct_float="0.1", days_to_cover="7"), "short", 0.70, bars, us_room=True)
            c2, _ = HO.build_candidate(self.row(short_pct_float="0.1", days_to_cover="7"), "long", 0.70, bars, us_room=True)
        self.assertEqual(c1["shares"], int(c0["shares"] * 0.5)); self.assertEqual(c1["size_mult"], 0.5); self.assertNotIn("size_mult", c2)
    def test_per_name_borrow(self):
        import event_calendar as EC
        from unittest import mock
        with mock.patch("ibkr_costs.borrow", return_value=dict(fee_pa=0.50, available=1e6, ok=True)):
            hi = EC.stage_levels(self.row(), "short", 2500, 0.70)["cost_R"]
        with mock.patch("ibkr_costs.borrow", return_value=dict(fee_pa=0.0025, available=1e6, ok=True)):
            lo = EC.stage_levels(self.row(), "short", 2500, 0.70)["cost_R"]
        with mock.patch("ibkr_costs.borrow", side_effect=RuntimeError("cache")):
            self.assertEqual(EC.us_borrow_pa("ABC"), EC.COSTS["borrow_pa"])
        self.assertGreater(hi, lo + 0.02)
    def test_short_dividend_cost(self):
        import hunt_clearance as HC
        now = dt.datetime(2026, 10, 28, tzinfo=dt.timezone.utc); ts = lambda d: (now + dt.timedelta(days=d)).timestamp()
        self.assertEqual(HC.short_dividend_cost(dict(calendar=dict(ex_dividend=ts(2), dividend_ps=0.30)), now)[0], 0.30)
        self.assertEqual(HC.short_dividend_cost(dict(calendar=dict(ex_dividend=ts(20), dividend_ps=0.30)), now)[0], 0.0)    # outside hold
        self.assertEqual(HC.short_dividend_cost(dict(calendar=dict(ex_dividend=ts(-30), dividend_ps=0.30)), now)[0], 0.0)   # past ex-date
        self.assertEqual(HC.short_dividend_cost(dict(calendar=dict(ex_dividend="UNKNOWN")), now)[0], 0.0)
        self.assertIsNone(HC.short_dividend_cost(dict(calendar=dict(ex_dividend=ts(1), dividend_ps="UNKNOWN")), now)[0])  # amount unknown -> FAIL

class EventSizing(unittest.TestCase):
    """Review fix #9: FOMC x0.5, tier-2 macro x0.75 inside the base hold; sizing, never a block; fail safe."""
    NOW = dt.datetime(2026, 10, 27, 12, 0, tzinfo=dt.timezone.utc); END = dt.datetime(2026, 10, 30, 20, 0, tzinfo=dt.timezone.utc)
    def test_mult(self):
        import hunt_orchestrator as HO
        fomc = [dt.datetime(2026, 10, 28, 18, 0, tzinfo=dt.timezone.utc)]; t2 = [(dt.datetime(2026, 10, 29, 12, 30, tzinfo=dt.timezone.utc), "GDP")]
        self.assertEqual(HO.event_size_mult(self.NOW, fomc, t2, self.END)[0], 0.5)
        self.assertEqual(HO.event_size_mult(self.NOW, [], t2, self.END)[0], 0.75)
        self.assertEqual(HO.event_size_mult(self.NOW, [dt.datetime(2026, 12, 9, 19, 0, tzinfo=dt.timezone.utc)], [], self.END)[0], 1.0)
    def test_unreadable_fomc_list_fails_safe(self):
        import hunt_orchestrator as HO
        from unittest import mock
        with mock.patch("builtins.open", side_effect=OSError("nope")):
            self.assertEqual(HO.event_size_mult(self.NOW, None, [], self.END)[0], 0.5)
    @unittest.skip("superseded: Brett 2026-10-28 07:12/07:13 hunt rule = fixed +3%/-8% (old 1.5R/1R-room geometry); see test_hunt_tp3_sl8_20261028")
    def test_build_candidate_shrinks_not_blocks(self):
        import hunt_orchestrator as HO
        from unittest import mock
        r = dict(sym="ABC", venue="ibkr", px=50.0, atr=1.5, atr_pct=3.0, lo10=50, pdl=50, hi20=50, pdh=50, adv_usd=1e9, rsi=50, roc5=0, days_to_cover="1")
        bars = [dict(o=50, h=51, l=49, c=50)] * 60
        with mock.patch("hunt48_room.exhaustion_block", return_value=[]), mock.patch("hunt48_room.room", return_value=None):
            with mock.patch.object(HO, "event_size_mult", return_value=(1.0, [])): c0, w0 = HO.build_candidate(r, "long", 0.70, bars, us_room=True)
            with mock.patch.object(HO, "event_size_mult", return_value=(0.5, ["FOMC"])): c1, w1 = HO.build_candidate(r, "long", 0.70, bars, us_room=True)
            with mock.patch.object(HO, "event_size_mult", return_value=(0.5, ["FOMC"])): c2, _ = HO.build_candidate(r, "long", 0.70, bars, us_room=False)
        self.assertEqual((w0, w1), ([], [])); self.assertEqual(c1["shares"], int(c0["shares"] * 0.5)); self.assertEqual(c1["size_mult"], 0.5)
        self.assertEqual(c2["shares"], c0["shares"])                                       # OANDA/non-US path untouched

class GlobalPrep(unittest.TestCase):
    """Review fix #10 (part): 04:00 global bar prep - symbol list from cache names + latest plans; summary written; failures counted."""
    def test_names_and_run(self):
        import tempfile, json as _j, global_prep as G
        self.assertEqual([G.cache_name_to_sym(x) for x in ("AUDUSD_X.json", "CL_F.json", "_GSPC.json", "7011.T.json")], ["AUDUSD=X", "CL=F", "^GSPC", "7011.T"])
        with tempfile.TemporaryDirectory() as b:
            os.makedirs(os.path.join(b, "state", "hunt48_daily_cache")); open(os.path.join(b, "state", "hunt48_daily_cache", "_AXJO.json"), "w").write("{}")
            os.makedirs(os.path.join(b, "reports", "daily_plan", "20261028"))
            _j.dump(dict(market="jp", backups=[dict(sym="7011.T")]), open(os.path.join(b, "reports", "daily_plan", "20261028", "jp.json"), "w"))
            _j.dump(dict(market="fx", backups=[dict(sym="EUR_USD")]), open(os.path.join(b, "reports", "daily_plan", "20261028", "oanda.json"), "w"))
            self.assertEqual(G.symbols(b), ["7011.T", "^AXJO"])
            r = G.run(b, daily_fn=lambda s: [] if s == "^AXJO" else [1])
            self.assertEqual((r["ok"], r["failed"], r["paper_only"]), (1, ["^AXJO"], True))
            self.assertTrue(os.path.exists(os.path.join(b, "state", "hunt_orch", "global_prep", "last.json")))

if __name__ == "__main__": unittest.main()
