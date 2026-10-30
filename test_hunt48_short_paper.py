import datetime as dt, hashlib, os, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import hunt48_short_paper as H
UTC = dt.timezone.utc
T0 = dt.datetime(2026, 10, 27, 13, 30, tzinfo=UTC)
def bar(i, o, h, l, c): return dict(start=T0 + dt.timedelta(minutes=15 * i), o=o, h=h, l=l, c=c)
def trade():
    t = H.arm_default()["trades"][0]  # GL
    t["time_exit_utc"] = H.TIME_EXIT_UTC   # audit C1 hardening: arm_default now stamps session_exit(now); these tests pin the legacy Thu 29 Oct exit
    t["armed_utc"] = (T0 - dt.timedelta(minutes=1)).isoformat(); return t
LONG_ROUTES = {"tfo_buy.py": None, "research/entry_engine/auto_fire.py": None}
# tforul 2026-10-30: logged, approved long-route changes since the hash file was cut (DEPLOY_LOG usbook 2026-10-30, Brett-approved US book cap)
APPROVED_ROUTE_HASHES = {"tfo_buy.py": {"1f54c3d3551d71d7711d0d7973a20cc31e09ac89424be7e5043febd9230fd500"},
                         "research/entry_engine/auto_fire.py": {"ff32b11f80ca3c8adec2447b40b00db7acba7b3d989ae69208811647d74b0c63"}}   # DEPLOY_LOG t18base 2026-10-29 14:27
class T(unittest.TestCase):
    def test_trigger_fill(self):
        t = trade(); now = T0 + dt.timedelta(hours=1)
        self.assertIsNone(H.try_fill(t, [bar(0, 155, 155.5, 154.5, 154.8)], now, 0.75))           # close above trigger
        self.assertEqual(H.try_fill(t, [bar(1, 154.5, 154.6, 153.6, 153.8)], now, 0.75), "filled")  # 153.705 <= 153.8 < 153.94
        self.assertEqual(t["status"], "FILLED"); self.assertGreaterEqual(t["entry"], t["max_fill"]); self.assertEqual(t["open_qty"], 336)
    def test_no_fill_below_max_fill_and_incomplete_bar(self):
        t = trade(); self.assertIsNone(H.try_fill(t, [bar(0, 154, 154, 153.0, 153.5)], T0 + dt.timedelta(hours=1), 0.75))
        self.assertEqual(t["status"], "ARMED")
        self.assertIsNone(H.try_fill(t, [bar(1, 154, 154, 153.7, 153.8)], T0 + dt.timedelta(minutes=20), 0.75))  # bar not complete
    def test_missed(self):
        t = trade(); self.assertEqual(H.try_fill(t, [bar(0, 152, 152, 150, 151.0)], T0 + dt.timedelta(hours=1), 0.75), "missed")
    def filled(self):
        t = trade(); H.try_fill(t, [bar(0, 154.5, 154.6, 153.6, 153.8)], T0 + dt.timedelta(hours=1), 0.75); return t
    def test_stop(self):
        t = self.filled(); ev = H.manage(t, [bar(1, 155, 159.5, 154.9, 159)], T0 + dt.timedelta(hours=2))
        self.assertEqual(ev, ["stopped"]); self.assertEqual(t["open_qty"], 0); self.assertLess(t["realised_usd"], 0)
        self.assertAlmostEqual(t["exits"][0]["px"], 158.92 * (1 + H.SLIP), 3)
    def test_target_half_then_trail(self):
        t = self.filled(); ev = H.manage(t, [bar(1, 153, 153.2, 148.0, 148.3)], T0 + dt.timedelta(hours=2))   # tforul: half-bank now at net 1R after costs (Brett 2026-10-29 1R rule), not raw T1
        self.assertEqual(ev, ["t1"]); self.assertEqual(t["open_qty"], 168); self.assertTrue(t["half_done"])
        self.assertLessEqual(t["trail"], t["entry"]); self.assertGreater(t["realised_usd"], 0)
    def test_time_exit(self):
        t = self.filled(); t["time_exit_utc"] = (T0 + dt.timedelta(minutes=45)).isoformat()
        ev = H.manage(t, [bar(1, 154, 154.5, 153.5, 153.9)], T0 + dt.timedelta(hours=1), daily=None)
        self.assertEqual(ev, ["time_exit"]); self.assertEqual(t["status"], "CLOSED"); self.assertEqual(t["open_qty"], 0)
    def test_mark_aud_after_costs(self):
        t = self.filled(); m = H.mark(t, t["entry"], 0.70, T0 + dt.timedelta(hours=24)); self.assertLess(m["pnl_aud"], 0)  # flat price -> costs+borrow
    def test_long_routes_untouched_and_isolated(self):
        src = open(os.path.join(HERE, "hunt48_short_paper.py")).read()
        for bad in ("import tfo_buy", "import auto_fire", "import swing_paper", "oandapyV20", "ib_insync", "api.kraken.com/0/private", "--execute"):
            self.assertNotIn(bad, src)
        ref = os.path.join(HERE, "state", "hunt48_short", "long_route_hashes.txt")
        if os.path.exists(ref):
            for line in open(ref):
                h, p = line.split(None, 1); p = p.strip()
                cur = hashlib.sha256(open(p, "rb").read()).hexdigest()
                if cur in APPROVED_ROUTE_HASHES.get(p, ()): continue   # tforul: approved, logged change
                self.assertEqual(cur, h, p + " changed")
    def test_cut_relevel_add_and_missed_precheck(self):
        now = dt.datetime(2026,10,26,15,0,tzinfo=dt.timezone.utc)
        L = H.arm_default()
        H.cut_trade(L, "GL", "cut per verdict", now)
        with self.assertRaises(SystemExit): H.cut_trade(L, "GL", "again", now)
        t = H.upsert_trade(L, dict(ticker="SLV", trigger=47.82, max_fill=47.75, stop=50.13, t1=46.80, t2=45.36, shares=734), now)
        self.assertEqual((t["stop"], t["shares"], t["status"]), (50.13, 734, "ARMED")); self.assertEqual(len(t["level_history"]), 1)
        f = H.upsert_trade(L, dict(ticker="FLG", trigger=10.24, max_fill=10.22, stop=10.73, t1=10.02, t2=9.71, shares=3375, atr=0.3464), now)
        self.assertEqual(f["id"], 4)
        self.assertFalse(H.pre_check_missed(dict(f), 10.10))
        self.assertTrue(H.pre_check_missed(dict(f), 9.99))
        L["trades"][1]["status"] = "FILLED"
        with self.assertRaises(SystemExit): H.upsert_trade(L, dict(ticker="SLV", trigger=1, max_fill=1, stop=2, t1=0.9, t2=0.8, shares=1), now)
        saved = H.atomic; H.atomic = lambda p, o: None
        try:
            st = H.write_status(L, 0.70, now)
        finally: H.atomic = saved
        self.assertEqual([c["ticker"] for c in st["cut"]], ["GL"])
        self.assertNotIn("GL", [r["ticker"] for r in st["trades"]])
        self.assertIn("IBKR", st["borrow_note"])  # 2026-10-27 real IBKR borrow (was 3% assumption)


class Bench(unittest.TestCase):
    """Brett 2026-10-27 01:24 WITA: bench of armed trades, max 3 open; extra triggers logged SHADOW, never filled."""
    def test_slot_free_fills(self):
        t = trade(); self.assertEqual(H.try_fill(t, [bar(0, 154.5, 154.6, 153.6, 153.8)], T0 + dt.timedelta(hours=1), 0.75), "filled")
        self.assertEqual(H.slot_gate(t, 2), "filled"); self.assertEqual(t["status"], "FILLED"); self.assertEqual(t["open_qty"], 336)
    def test_full_book_shadows(self):
        t = trade(); t["bench"] = True; H.try_fill(t, [bar(0, 154.5, 154.6, 153.6, 153.8)], T0 + dt.timedelta(hours=1), 0.75)
        self.assertEqual(H.slot_gate(t, H.MAX_OPEN), "shadow"); self.assertEqual(t["status"], "SHADOW")   # tforul: main book max 4
        self.assertEqual(t["open_qty"], 0); self.assertIsNone(t["entry"]); self.assertIsNotNone(t["shadow_entry"]); self.assertEqual(t["fees_usd"], 0)
    def test_max_open_is_3(self): self.assertEqual(H.MAX_OPEN, 4 if H.MAIN_BOOK else 3)   # tforul: Brett 2026-10-29 20:17 4th main slot
    def test_status_counts_bench_separately(self):
        import tempfile; H.STATUS = os.path.join(tempfile.mkdtemp(), "s.json")
        L = H.arm_default(); L["trades"][1]["bench"] = True; L["trades"][2].update(status="SHADOW", shadow_entry=80.0)
        st = H.write_status(L, 0.70, T0)
        self.assertEqual((st["armed"], st["bench_armed"], st["shadow"], st["max_open"]), (1, 1, 1, H.MAX_OPEN))
        self.assertTrue(st["trades"][1]["bench"])


class LongComparison(unittest.TestCase):
    """GPT comparison book: minimal LONG mirror (comparison books only)."""
    def lt(self):
        L = H.new_ledger("GPT picks"); saved = H.BOOK; H.BOOK = "gpt"
        try: t = H.upsert_trade(L, dict(ticker="IBKR", direction="LONG", trigger=81.70, max_fill=81.80, stop=79.25, t1=84.35, t2=86.90, shares=700, atr=3.0), T0)
        finally: H.BOOK = saved
        t["armed_utc"] = (T0 - dt.timedelta(minutes=1)).isoformat(); return t
    def test_long_allowed_in_main_book(self):
        """Brett 2026-10-27 20:56 WITA: main book (no --book) arms longs; risk cap + direction balance unchanged."""
        saved = H.BOOK; H.BOOK = ""
        try:
            L = H.new_ledger("x")
            t = H.upsert_trade(L, dict(ticker="IBKR", direction="long", trigger=1, max_fill=1.1, stop=0.9, t1=1.2, t2=1.3, shares=1), T0)
            self.assertEqual((t["direction"], t["status"], t["risk_cap_aud"]), ("long", "ARMED", 5000.0))   # tforul: cap A$5k (Brett 2026-10-29 20:17)
            with self.assertRaises(SystemExit): H.upsert_trade(L, dict(ticker="ZZZ", direction="sideways", trigger=1, max_fill=1.1, stop=0.9, t1=1.2, t2=1.3, shares=1), T0)
            shorts = [dict(ticker="P", direction="short", sector="SecP"), dict(ticker="Q", direction="short", sector="SecQ"), dict(ticker="R", direction="short", sector="SecR")]   # tforul: main max_open 4 -> balance bites at 3 same-direction
            self.assertIsNone(H.mix_gate(dict(ticker="L1", direction="long", sector="SecL"), shorts))
            self.assertIn("direction balance", H.mix_gate(dict(ticker="S4", direction="short", sector="SecS"), shorts))
            longs = [dict(o, direction="long") for o in shorts]
            self.assertIn("direction balance", H.mix_gate(dict(ticker="L5", direction="long", sector="SecL"), longs))
            self.assertIn("sector cap", H.mix_gate(dict(ticker="L4", direction="long", sector="SecP"), shorts))
        finally: H.BOOK = saved
    def test_long_fill_rules(self):
        t = self.lt(); now = T0 + dt.timedelta(hours=2); self.assertEqual(t["direction"], "long")
        self.assertIsNone(H.try_fill(t, [bar(0, 81, 81.9, 80.9, 81.5)], now, 0.75))      # close below trigger
        self.assertIsNone(H.try_fill(t, [bar(1, 81.6, 82.0, 81.5, 81.9)], now, 0.75))    # close above max buy -> no chase
        self.assertIsNone(H.try_fill(t, [bar(2, 81.6, 81.8, 81.5, 81.75)], T0 + dt.timedelta(minutes=40), 0.75))  # bar unfinished
        self.assertEqual(H.try_fill(t, [bar(2, 81.6, 81.8, 81.5, 81.75)], now, 0.75), "filled")
        self.assertLessEqual(t["entry"], 81.80); self.assertGreater(t["entry"], 81.70)
    def test_long_missed(self):
        t = self.lt(); self.assertEqual(H.try_fill(t, [bar(0, 82, 83.2, 82, 83.0)], T0 + dt.timedelta(hours=1), 0.75), "missed")  # > 81.70+1.225
        t2 = self.lt(); self.assertTrue(H.pre_check_missed(t2, 83.0)); t3 = self.lt(); self.assertFalse(H.pre_check_missed(t3, 82.5))
    def test_long_stop_target_mark(self):
        t = self.lt(); H.try_fill(t, [bar(0, 81.6, 81.8, 81.5, 81.75)], T0 + dt.timedelta(hours=1), 0.75)
        ev = H.manage(t, [bar(1, 82, 84.5, 81.9, 84.2)], T0 + dt.timedelta(hours=2))
        self.assertEqual(ev, ["t1"]); self.assertEqual(t["open_qty"], 350); self.assertGreater(t["realised_usd"], 0); self.assertGreaterEqual(t["trail"], t["entry"])
        self.assertGreater(H.mark(t, 85.0, 0.70, T0 + dt.timedelta(hours=3))["pnl_aud"], 0); self.assertEqual(t["borrow_usd"], 0)
        t = self.lt(); H.try_fill(t, [bar(0, 81.6, 81.8, 81.5, 81.75)], T0 + dt.timedelta(hours=1), 0.75)
        ev = H.manage(t, [bar(1, 81, 81.2, 79.0, 79.1)], T0 + dt.timedelta(hours=2))
        self.assertEqual(ev, ["stopped"]); self.assertLess(t["realised_usd"], 0); self.assertAlmostEqual(t["exits"][0]["px"], 79.25 * (1 - H.SLIP), 3)
    def test_short_default_unchanged(self):
        self.assertEqual(trade()["direction"], "short")

if __name__ == "__main__": unittest.main()


class ComparisonBench(unittest.TestCase):
    """Brett 2026-10-27 01:40 WITA: Claude/GPT books get 15-20 bench trades, same max-3-open rule."""
    def _apply(self, book, spec_list):
        import tempfile, json
        d = tempfile.mkdtemp(); saved = (H.BOOK, H.LEDGER, H.EVENTS, H.bars_15m)
        H.BOOK = book; H.LEDGER = os.path.join(d, "ledger.json"); H.EVENTS = os.path.join(d, "ev.jsonl")
        H.bars_15m = lambda s: [bar(0, 100, 100, 100, 100)]
        p = os.path.join(d, "plan.json"); json.dump(dict(label=book, set=spec_list), open(p, "w"))
        try: return H.apply_plan(p, T0)
        finally: H.BOOK, H.LEDGER, H.EVENTS, H.bars_15m = saved
    def test_bench_flag_kept_in_named_book_incl_long(self):
        L = self._apply("gpt", [dict(ticker="AAA", direction="short", trigger=98, max_fill=97, stop=102, t1=95, t2=92, shares=10, bench=True),
                                   dict(ticker="BBB", direction="long", trigger=101, max_fill=102, stop=97, t1=105, t2=108, shares=10, bench=True)])
        self.assertEqual([(t["ticker"], t["direction"], t["bench"], t["status"]) for t in L["trades"]],
                         [("AAA", "short", True, "ARMED"), ("BBB", "long", True, "ARMED")])
    def test_bench_past_half_stop_at_arm_is_missed(self):
        L = self._apply("gpt", [dict(ticker="CCC", direction="short", trigger=104, max_fill=103, stop=108, t1=100, t2=96, shares=10, bench=True)])
        self.assertEqual(L["trades"][0]["status"], "MISSED")
    def test_event_safe_exit_ecb_gdp(self):
        evs = [(dt.datetime.fromisoformat(u), n) for u, n in H.HI_EVENTS_STATIC]
        te = dt.datetime.fromisoformat("2026-10-29T14:00:00+00:00")
        nte, why = H.event_safe_exit(te, evs)
        self.assertTrue(all(abs((nte - t).total_seconds()) > 1800 for t, _ in evs)); self.assertLessEqual(nte, te - dt.timedelta(minutes=45 + 15))
        self.assertEqual(H.event_safe_exit(dt.datetime.fromisoformat("2026-10-30T14:00:00+00:00"), evs)[1], [])   # Fri exit untouched
    def test_apply_event_exit_once_and_skips_closed(self):
        evs = [(dt.datetime.fromisoformat(u), n) for u, n in H.HI_EVENTS_STATIC]
        a = trade(); a["status"] = "ARMED"; c = trade(); c["status"] = "CLOSED"; L = dict(trades=[a, c])
        r = H.apply_event_exit(L, evs, dt.datetime.fromisoformat("2026-10-27T00:00:00+00:00"))
        self.assertEqual(len(r), 1); self.assertEqual(a["time_exit_orig_utc"], "2026-10-29T14:00:00+00:00"); self.assertEqual(c["time_exit_utc"], "2026-10-29T14:00:00+00:00")
        self.assertEqual(H.apply_event_exit(L, evs, dt.datetime.fromisoformat("2026-10-27T00:00:00+00:00")), [])
    def test_causal_guard(self):
        import causal_guard as G
        a = dict(armed_utc="2026-10-27T14:00:00+00:00", entry_utc="2026-10-27T13:45:00+00:00", exits=[])
        self.assertEqual(len(G.check(a, dt.datetime(2026,10,28,tzinfo=dt.timezone.utc))), 1)
        b = dict(armed_utc="2026-10-27T13:00:00+00:00", entry_utc="2026-10-27T13:45:00+00:00", exits=[dict(why="stop", utc="2026-10-27T13:30:00+00:00")])
        self.assertEqual(len(G.check(b, dt.datetime(2026,10,28,tzinfo=dt.timezone.utc))), 1)
        c = dict(armed_utc="2026-10-27T13:00:00+00:00", entry_utc="2026-10-27T13:45:00+00:00", exits=[dict(why="t1", utc="2026-10-27T14:00:00+00:00")])
        self.assertEqual(G.check(c, dt.datetime(2026,10,28,tzinfo=dt.timezone.utc)), [])
    def test_sector_cap_and_direction_balance(self):
        mk = lambda tk, d, sec: dict(ticker=tk, direction=d, sector=sec, status="FILLED")
        a = trade(); a.update(ticker="LOW", sector="Consumer Cyclical", entry=1, entry_utc="x", fees_usd=1.0)
        self.assertEqual(H.slot_gate(a, 1, 3, [mk("HD", "short", "Consumer Cyclical")]), "shadow"); self.assertIn("sector cap", a["shadow_reason"])
        b = trade(); b.update(ticker="NVDA", direction="short", sector="Technology", entry=1, entry_utc="x", fees_usd=1.0)
        self.assertEqual(H.slot_gate(b, 2, 3, [mk("HD", "short", "Consumer Cyclical"), mk("JPM", "short", "Financial Services")]), "shadow"); self.assertIn("direction", b["shadow_reason"])
        c = trade(); c.update(ticker="NVDA", direction="long", sector="Technology", entry=1, entry_utc="x", fees_usd=1.0)
        self.assertEqual(H.slot_gate(c, 2, 3, [mk("HD", "short", "Consumer Cyclical"), mk("JPM", "short", "Financial Services")]), "filled")
        self.assertEqual(H.sector_of("EUR_CHF"), "FX"); self.assertEqual(H.sector_of("XAU_USD"), "COMMODITIES"); self.assertEqual(H.sector_of("SLV"), "COMMODITIES"); self.assertEqual(H.sector_of("XBT/USD"), "CRYPTO")
    def test_prior_close_exit_thu_gdp(self):
        evs = [(dt.datetime.fromisoformat(u), n) for u, n in H.HI_EVENTS_STATIC]
        te = dt.datetime.fromisoformat("2026-10-29T14:00:00+00:00"); nte, why = H.prior_close_exit(te, evs)
        self.assertEqual(nte.isoformat(), "2026-10-28T19:55:00+00:00"); self.assertTrue(why)   # Wed 15:55 ET = Thu 03:55 WITA
        a = trade(); a["status"] = "ARMED"; L = dict(trades=[a])
        H.apply_event_exit(L, evs, dt.datetime.fromisoformat("2026-10-27T00:00:00+00:00")); self.assertEqual(a["time_exit_utc"], "2026-10-28T19:55:00+00:00")
    def test_room_1R_rule(self):
        lows = [112, 111, 105, 111, 112, 113, 114, 115]; daily = [dict(h=x + 3, l=x, c=x + 2) for x in lows] + [dict(h=118, l=115, c=116)]
        daily = [dict(h=130, l=120, c=125)] * 240 + daily   # audit H2+M4: >= 200 daily bars required (1y history)
        t = trade(); t.update(direction="short", max_fill=106.0, stop=108.0, shares=100, t2=100.0)
        rc = H.room_check(t, daily); self.assertFalse(rc["ok"]); self.assertEqual(rc["level"], 105); self.assertLess(rc["room_R"], 1)
        t.update(max_fill=110.0, stop=112.0, t2=104.0); rc = H.room_check(t, daily); self.assertTrue(rc["ok"]); self.assertGreater(rc["room_R"], 2)   # audit H2+M4: R=2 keeps cost_R under the 0.15 cap
    def test_thesis_exit_short_close_above_level(self):
        t = trade(); t.update(direction="short", status="FILLED", entry=99.0, entry_utc="2026-10-27T14:00:00+00:00", trigger=99.5, stop=102.0,
                              open_qty=100, shares=100, lowest=99.0, trail=None, half_done=False, t1=95.0, atr=1.0, exits=[], realised_usd=0.0,
                              time_exit_utc="2026-10-29T19:55:00+00:00", ext_days=0)
        st = H.dt.datetime(2026, 10, 27, 14, 15, tzinfo=H.UTC); bar = dict(start=st, o=99.2, h=100.5, l=99.1, c=100.1)   # fix1: close must clear level 99.5 + 0.5 ATR
        ev = H.manage(t, [bar], st + H.dt.timedelta(minutes=16)); self.assertIn("thesis_exit", ev); self.assertEqual(t["status"], "CLOSED")
        self.assertEqual(t["stop"], 102.0); self.assertEqual(t["open_qty"], 0)
        t2 = trade(); t2.update({k: v for k, v in t.items() if k not in ("status", "open_qty", "exits")}, status="FILLED", open_qty=100, exits=[])
        t2.pop("_last_bar", None); bar2 = dict(bar, c=99.3, h=99.6)   # wick above, close below -> stay in
        self.assertNotIn("thesis_exit", H.manage(t2, [bar2], st + H.dt.timedelta(minutes=16))); self.assertEqual(t2["status"], "FILLED")
    def test_named_book_fourth_fill_shadows(self):
        t = trade(); t["bench"] = True; H.try_fill(t, [bar(0, 154.5, 154.6, 153.6, 153.8)], T0 + dt.timedelta(hours=1), 0.75)
        saved = H.BOOK; H.BOOK = "claude"
        try: self.assertEqual(H.slot_gate(t, 3, 3), "shadow")   # tforul: named books keep max 3 (MAX_OPEN binds at import)
        finally: H.BOOK = saved
