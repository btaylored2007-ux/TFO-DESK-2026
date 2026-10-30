"""Offline tests for shark exit variant V7 + the V0 shadow tracker (2026-10-02). No network, no live ledgers, no tickets."""
import datetime as dt, json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tfo_trail_rail as trl
import shark_exit_ab as ab

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 10, 5, 14, 0, tzinfo=UTC)  # Monday, US session


class V7Rail(unittest.TestCase):
    def test_default_rail_unchanged(self):
        self.assertEqual(trl.decide(100, 102.0, "long", False)["rule"], "HALF_2PCT")
        self.assertEqual(trl.decide(100, 102.0, "long", False, variant=None)["rule"], "HALF_2PCT")
        self.assertEqual(trl.decide(100, 102.0, "long", False, variant="V0", held_sessions=99)["rule"], "HALF_2PCT")
        self.assertIsNone(trl.decide(100, 101.0, "long", False, held_age=400)["act"])  # default: no pre-half max hold
        self.assertEqual(trl.rule_text(None), trl.RULE_TEXT)

    def test_v7_no_half_at_two_half_at_ten(self):
        self.assertIsNone(trl.decide(100, 102.0, "long", False, variant="V7", held_sessions=1)["act"])
        self.assertIsNone(trl.decide(100, 109.9, "long", False, variant="V7", held_sessions=1)["act"])
        d = trl.decide(100, 110.0, "long", False, variant="V7", held_sessions=1)
        self.assertEqual((d["act"], d["rule"]), ("HALF", "HALF_10PCT"))

    def test_v7_stop(self):
        d = trl.decide(100, 95.0, "long", False, variant="V7", held_sessions=1)
        self.assertEqual((d["act"], d["rule"]), ("STOP", "STOP_ORIG"))
        self.assertIsNone(trl.decide(100, 95.1, "long", False, variant="V7", held_sessions=1)["act"])

    def test_v7_trail_and_be_floor_after_half(self):
        d = trl.decide(100, 111.5, "long", True, wm=115.0, atr_pct=0.005, variant="V7", held_sessions=5)  # 3% trail -> 111.55
        self.assertEqual((d["act"], d["rule"]), ("FLAT", "TRAIL_3ATR"))
        d = trl.decide(100, 112.0, "long", True, wm=115.0, atr_pct=0.005, variant="V7", held_sessions=5)
        self.assertIsNone(d["act"]); self.assertAlmostEqual(d["level"], 111.55)
        d = trl.decide(100, 99.9, "long", True, wm=101.0, atr_pct=0.05, variant="V7", held_sessions=5)
        self.assertEqual((d["act"], d["rule"]), ("FLAT", "BE_FLOOR"))

    def test_v7_max_hold_40_sessions_whole_position(self):
        self.assertIsNone(trl.decide(100, 103, "long", False, variant="V7", held_sessions=39)["act"])
        d = trl.decide(100, 103, "long", False, variant="V7", held_sessions=40)
        self.assertEqual((d["act"], d["rule"]), ("FLAT", "MAX_HOLD_40S"))
        d = trl.decide(100, 120, "long", True, wm=120, atr_pct=0.01, variant="V7", held_sessions=40)
        self.assertEqual((d["act"], d["rule"]), ("FLAT", "MAX_HOLD_40S"))
        # 30 calendar days no longer exits a V7 residual
        self.assertIsNone(trl.decide(100, 120, "long", True, wm=120, atr_pct=0.01, held_age=45, variant="V7", held_sessions=30)["act"])

    def test_sessions_after(self):
        self.assertEqual(trl.sessions_after("2026-10-05T14:00:00Z", T0 + dt.timedelta(days=7), "US"), 5)
        self.assertEqual(trl.sessions_after("2026-10-05T14:00:00Z", T0 + dt.timedelta(days=56), "US"), 40)
        self.assertIsNone(trl.sessions_after(None, T0, "US"))
        self.assertEqual(trl.sessions_after("2026-10-05T00:30:00Z", dt.datetime(2026, 10, 6, 0, 30, tzinfo=UTC), "AX"), 1)


class TicketWiring(unittest.TestCase):
    def test_rail_detail_tagged_vs_untagged(self):
        import tfo_ticket as tt
        base = {"entry_utc": "2026-10-05T14:00:00Z", "entry_px_usd": 100.0}
        dec, _ = tt.rail_detail(100.0, 103.0, "long", dict(base), T0 + dt.timedelta(hours=1), None, "XYZ")
        self.assertEqual(dec["act"], "HALF")                                   # untagged: +2% half as before
        dec, _ = tt.rail_detail(100.0, 103.0, "long", dict(base, exit_variant="V7"), T0 + dt.timedelta(hours=1), None, "XYZ")
        self.assertIsNone(dec["act"])                                          # V7: holds through +3%
        dec, _ = tt.rail_detail(100.0, 103.0, "long", dict(base, exit_variant="V7"), T0 + dt.timedelta(days=60), None, "XYZ")
        self.assertEqual(dec["rule"], "MAX_HOLD_40S")
        dec, st = tt.rail_detail(100.0, 112.0, "long", dict(base, exit_variant="V7", rail_half_count=1, rail_trail={"half_banked": True, "hwm_usd": 111.0, "atr14_pct": 0.01}),
                                 T0 + dt.timedelta(days=2), None, "XYZ")
        self.assertIsNone(dec["act"]); self.assertTrue(st["rule"].startswith("V7"))


    def test_asx_and_tokyo_clips_use_v7(self):
        import tfo_ticket as tt
        for sym, entry, tzname in (("BHP.AX", "2026-10-05T00:25:00Z", "AX"), ("8035.T", "2026-10-05T01:00:00Z", "T")):
            base = {"entry_utc": entry, "entry_px_usd": 100.0, "exit_variant": "V7"}
            dec, _ = tt.rail_detail(100.0, 103.0, "long", dict(base), T0, None, sym)
            self.assertIsNone(dec["act"], sym)                                  # no +2% half under V7
            dec, _ = tt.rail_detail(100.0, 110.0, "long", dict(base), T0, None, sym)
            self.assertEqual(dec["rule"], "HALF_10PCT", sym)
            dec, _ = tt.rail_detail(100.0, 103.0, "long", dict(base), T0 + dt.timedelta(days=56), None, sym)
            self.assertEqual(dec["rule"], "MAX_HOLD_40S", sym)                  # 40 local weekdays -> whole-position exit
            self.assertEqual(trl.sessions_after(entry, trl._parse(entry) + dt.timedelta(days=7), tzname), 5)

    def test_tfo_buy_tags_every_venue(self):
        import inspect, tfo_buy
        src = inspect.getsource(tfo_buy.execute)
        self.assertIn("SHARK_EXIT_VARIANT", src); self.assertEqual(tfo_buy.SHARK_EXIT_VARIANT, "V7")
        self.assertNotIn("venue(sym) == \"US\" and SHARK_EXIT_VARIANT", src)   # tag is venue-agnostic


class Shadow(unittest.TestCase):
    def run_path(self, path, close_actual_at=None):
        tmp = tempfile.mkdtemp(); open(os.path.join(tmp, "shark_scorecard.jsonl"), "w").close()
        p = {"side": "long", "entry_px_usd": 100.0, "rail_orig_entry_usd": 100.0, "entry_utc": "2026-10-05T14:00:00Z", "units": 1000.0,
             "notional_usd": 100000.0, "exit_variant": "V7", "entry_fees_usd": 10.0, "entry_ticket": {"principal_usd": 100000.0}}
        book = {"positions": {"XYZ": p}, "closed": {}}
        for i, px in enumerate(path):
            now = T0 + dt.timedelta(minutes=10 * (i + 1))
            if close_actual_at is not None and i == close_actual_at:
                c = dict(p, exit_fills=[{"pnl_usd": 6000.0, "fees_usd": 5.0, "exit_rule": "HALF_10PCT"}, {"pnl_usd": 4500.0, "fees_usd": 5.0, "exit_rule": "TRAIL_3ATR", "utc": "x"}])
                book = {"positions": {}, "closed": {"XYZ": c}}
            rows = [{"book": "fundamental_book", "sym": "XYZ", "px_usd": px, "_trail_ok": True}] if book["positions"] else []
            ab.update(rows, now=now, reports=tmp, book=book, quote_fn=lambda *a, _px=px: _px, atr_fn=lambda s: 0.005)
        st = json.load(open(os.path.join(tmp, "shark_exit_ab_state.json")))
        ev = [json.loads(l) for l in open(os.path.join(tmp, "shark_exit_ab.jsonl"))]
        sc = [json.loads(l) for l in open(os.path.join(tmp, "shark_scorecard.jsonl"))]
        return st, ev, sc

    def test_shadow_v0_half_then_trail(self):
        st, ev, sc = self.run_path([101, 103, 108, 112, 115, 109], close_actual_at=5)
        t = list(st["trades"].values())[0]
        self.assertTrue(t["shadow"]["half_banked"]); self.assertTrue(t["shadow"]["closed"])
        self.assertAlmostEqual(t["shadow"]["realized"], 0.5 * 0.03 + 0.5 * 0.09)       # V0: half @103, rest trailed out @109
        self.assertEqual(t["shadow"]["exit_rule"], "TRAIL_3ATR")
        self.assertTrue(t["complete"])
        done = [e for e in ev if e["event"] == "trade_complete"][0]
        self.assertAlmostEqual(done["v7_actual_gross_ret_pct"], 10.5); self.assertAlmostEqual(done["v0_shadow_gross_ret_pct"], 6.0)
        self.assertAlmostEqual(done["delta_v7_minus_v0_gross_pct"], 4.5)
        self.assertEqual(sc[-1]["action"], "exit_ab_complete")

    def test_shadow_stop_and_no_book_writes(self):
        st, ev, sc = self.run_path([99, 97, 94.9])
        t = list(st["trades"].values())[0]
        self.assertEqual(t["shadow"]["exit_rule"], "STOP_ORIG"); self.assertAlmostEqual(t["shadow"]["realized"], -0.051)
        self.assertFalse(t["complete"]); self.assertEqual(sc, [])

    def test_shadow_continues_after_actual_closed(self):
        # V7 times out / closes while V0 is still pre-half: shadow keeps stepping on fetched quotes
        st, ev, sc = self.run_path([101, 101.5, 101.8, 102.5], close_actual_at=2)
        t = list(st["trades"].values())[0]
        self.assertTrue(t["actual"]["closed"]); self.assertTrue(t["shadow"]["half_banked"])


if __name__ == "__main__":
    unittest.main()
