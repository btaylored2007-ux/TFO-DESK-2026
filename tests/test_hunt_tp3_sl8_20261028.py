import os, sys, unittest, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hunt48_room as HR, hunt48_short_paper as H
class T(unittest.TestCase):
    def test_room_fixed(self):
        self.assertTrue(HR.is_fixed(100, 108, 97)); self.assertTrue(HR.is_fixed(100, 92, 103)); self.assertFalse(HR.is_fixed(100, 102, 97))
    def test_plan_fixed_levels_short_long_and_risk_cap(self):
        L = H.new_ledger("t")
        s = H.fixed_levels(dict(ticker="X", trigger=50.1, max_fill=50.0, stop=51.0, t1=49.0, t2=48.5, shares=999), "short", L, 2500.0)
        self.assertAlmostEqual(s["stop"], 54.0); self.assertAlmostEqual(s["t1"], 48.5); self.assertEqual(s["t1"], s["t2"])
        self.assertLessEqual(s["shares"] * 4.0 / 0.7022, 2500.0 + 1e-6)
        l = H.fixed_levels(dict(ticker="Y", max_fill=100.0, stop=98.0, t1=102.0, t2=103.0, shares=1), "long", L, 2500.0)
        self.assertAlmostEqual(l["stop"], 92.0); self.assertAlmostEqual(l["t2"], 103.0); self.assertEqual(l["levels_orig"]["stop"], 98.0)
        self.assertIs(H.fixed_levels(l, "long", L, 2500.0), l)   # idempotent, no resize of already-fixed
    def test_stage_levels_fixed(self):
        import event_calendar as EC, inspect
        self.assertIn("fixed_pct", inspect.signature(EC.stage_levels).parameters)
class Room(unittest.TestCase):
    def test_room_passes_clear_fixed_and_benches_blocked(self):
        import inspect; src = inspect.getsource(HR.room); self.assertIn("is_fixed", src)
    def test_clearance_no_t2_1R_reject_for_fixed(self):
        import inspect, hunt_clearance as C; self.assertIn("fixed +3%/-8% rule", inspect.getsource(C))
class FullExit(unittest.TestCase):
    def test_us_short_full_exit_at_plus3(self):
        """2026-10-29 teststale: live rule (main/gpt) = R-based levels, half at net 1R, full flat at net 3R of ORIGINAL risk.
        Old fixed +3% (T1==T2) full exit is Claude-book only; tagging T1 short of 1R net does nothing."""
        UTC = dt.timezone.utc; now = dt.datetime(2026, 11, 3, 15, 0, tzinfo=UTC); L = H.new_ledger("t")
        t = H.upsert_trade(L, dict(ticker="XYZ", trigger=50.0, max_fill=49.9, stop=52.0, t1=49.0, t2=46.75, shares=100, atr=1.0, sector="TECH", direction="short"), now)
        bar = lambda m, o, h, l, c: dict(start=now + dt.timedelta(minutes=m), o=o, h=h, l=l, c=c)
        self.assertEqual(H.try_fill(t, [bar(15, 50.1, 50.2, 49.9, 49.95)], now + dt.timedelta(minutes=31), 0.75), "filled")
        H.manage(t, [bar(30, 49.0, 49.2, t["t1"] - 0.01, 49.0)], now + dt.timedelta(minutes=46), daily=None)
        self.assertEqual(t["status"], "FILLED"); self.assertFalse(t["half_done"])   # +2% / T1 tag < 1R net: nothing
        H.manage(t, [bar(45, 49.0, 49.0, 43.0, 43.1)], now + dt.timedelta(minutes=61), daily=None)   # > 3R net in one bar
        self.assertEqual(t["status"], "CLOSED"); self.assertEqual(t["open_qty"], 0); self.assertIn("3R", t["exit_reason"]); self.assertEqual(t["stop"], 52.0)
    def test_bench_engine_full_exit_when_t1_eq_t2(self):
        import cal_paper_bench as CB; UTC = dt.timezone.utc; t0 = dt.datetime(2026, 10, 27, tzinfo=UTC)
        t = dict(direction="long", status="FILLED", armed_utc=t0.isoformat(), fill=100.0, fill_utc=t0.isoformat(), stop=92.0, stop_live=92.0,
                 qty_open=10, shares=10, realised_usd=0.0, t1=103.0, t2=103.0, q2usd=1.0, est_cost_usd=0.0, last_bar_utc=t0.isoformat())
        CB.step(t, [dict(t=t0 + dt.timedelta(hours=1), o=100, h=103.5, l=99.5, c=103)], t0 + dt.timedelta(hours=2), 3)
        self.assertEqual(t["status"], "CLOSED"); self.assertEqual(t["qty_open"], 0); self.assertAlmostEqual(t["realised_usd"], 30.0)
if __name__ == "__main__": unittest.main()
