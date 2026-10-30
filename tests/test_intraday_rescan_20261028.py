"""2026-10-28 09:59 WITA Brett: intraday rescans every 45 min while open (PAPER). Schedule, gating, engine intraday arm, 4 checks."""
import os, sys, datetime as dt, unittest, importlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_asia_hunt48 as TA
UTC = dt.timezone.utc
_DB = os.environ.get("DESK_BASE")
def tearDownModule():
    if _DB is None: os.environ.pop("DESK_BASE", None)
    else: os.environ["DESK_BASE"] = _DB
MID = dt.datetime(2026, 10, 27, 2, 0, tzinfo=UTC)        # Tue 27 Oct 11:00 JST (in session, before lunch)
class Schedule(unittest.TestCase):
    def setUp(self):
        import intraday_rescan as IR; self.IR = importlib.reload(IR)
    def test_jp_slots_every_45_skip_lunch_stop_30_before_close(self):
        s = self.IR.slots_for_day("jp", dt.date(2026, 10, 28)); jst = [x.astimezone(self.IR.ZoneInfo("Asia/Tokyo")).strftime("%H:%M") for x in s]
        self.assertEqual(jst[0], "09:45"); self.assertNotIn("12:00", jst)                     # 12:00 is inside the 11:30-12:30 lunch
        self.assertTrue(all(b > a for a, b in zip(s, s[1:])))
        self.assertLessEqual(jst[-1], "15:00")
    def test_closed_day_has_no_slots(self):
        self.assertEqual(self.IR.slots_for_day("jp", dt.date(2026, 11, 3)), [])               # JP holiday
        self.assertEqual(self.IR.slots_for_day("hk", dt.date(2026, 10, 31)), [])              # Saturday
    def test_due_once_per_slot(self):
        now = dt.datetime(2026, 10, 28, 1, 50, tzinfo=UTC)    # 10:50 JST -> slot 10:30
        self.assertTrue(self.IR.due("jp", now, {}))
        slot = self.IR.current_slot("jp", now)
        self.assertFalse(self.IR.due("jp", now, {"jp": {"slot_utc": slot.isoformat()}}))
    def test_us_next_slot_in_session(self):
        n = self.IR.next_slot("us", dt.datetime(2026, 10, 28, 2, 0, tzinfo=UTC))
        self.assertEqual(n.astimezone(self.IR.WITA).strftime("%H:%M"), "22:15")
    def test_open_time_checks_fail_closed_on_no_data(self):
        ok, why = self.IR.open_time_checks("jp", dict(sym="1111.T", direction="long", trigger=100, stop=92), MID, bars_fn=lambda s: [])
        self.assertFalse(ok); self.assertIn("fail closed", why[0])
    def test_open_time_checks_rs_blocks_long_lagging_index(self):
        o = self.IR.session_open_utc("jp", MID); mk = lambda t, p, v=1000: dict(start=t, o=p, h=p, l=p, c=p, v=v)
        prior = [mk(o - dt.timedelta(days=1, minutes=-5 * i), 100) for i in range(12)]
        today = [mk(o + dt.timedelta(minutes=5 * i), 99, 5000) for i in range(12)]
        ix = [mk(o - dt.timedelta(days=1), 100), mk(o + dt.timedelta(minutes=55), 102)]
        ok, why = self.IR.open_time_checks("jp", dict(sym="1111.T", direction="long", trigger=101, stop=93), MID, bars_fn=lambda s: prior + today,
                                           idx_fn=lambda: ix, spread_fn=lambda s: 5.0)
        self.assertFalse(ok); self.assertTrue(any(w.startswith("RS:") for w in why))
class Engine(unittest.TestCase):
    def _cleared(self, env):
        d, AH = TA.setup("jp", [TA.row("jp", "1111.T", "Tech", score=5)])
        AH.run("jp", now=TA.PRE, fetch=lambda y, s: [], base=d, live=TA.NOLIVE); TA.clear(d, "jp", AH, {"1111.T": "PASS"})
        os.utime(os.path.join(d, "state", "clearance", "20261027", "jp", "summary.json"), (MID.timestamp() - 600,) * 2)
        if env: os.environ["HUNT48_INTRADAY"] = "1"
        else: os.environ.pop("HUNT48_INTRADAY", None)
        return d, AH
    def tearDown(self): os.environ.pop("HUNT48_INTRADAY", None)
    def test_no_env_stays_flat_in_session(self):
        d, AH = self._cleared(False)
        st = AH.run("jp", now=MID, fetch=lambda y, s: [], base=d, live=TA.NOLIVE)
        self.assertEqual(st["counts"]["ARMED"], 0)
    def test_intraday_rescan_arms_at_now_after_checks(self):
        d, AH = self._cleared(True); AH.IR_checks = lambda m, t, n: (True, [])
        st = AH.run("jp", now=MID, fetch=lambda y, s: [], base=d, live=TA.NOLIVE)
        self.assertEqual([t["sym"] for t in st["armed"]], ["1111.T"])
        self.assertEqual(dt.datetime.fromisoformat(st["armed"][0]["armed_utc"]), MID)
    def test_intraday_checks_fail_keeps_bench(self):
        d, AH = self._cleared(True); AH.IR_checks = lambda m, t, n: (False, ["RS: long not beating index"])
        st = AH.run("jp", now=MID, fetch=lambda y, s: [], base=d, live=TA.NOLIVE)
        self.assertEqual(st["counts"]["ARMED"], 0); self.assertIn("open-time checks", st["bench"][0]["bench_note"])
    def test_stale_clearance_no_intraday_arm(self):
        d, AH = self._cleared(True); AH.IR_checks = lambda m, t, n: (True, [])
        os.utime(os.path.join(d, "state", "clearance", "20261027", "jp", "summary.json"), (MID.timestamp() - 7200,) * 2)
        st = AH.run("jp", now=MID, fetch=lambda y, s: [], base=d, live=TA.NOLIVE)
        self.assertEqual(st["counts"]["ARMED"], 0)
    def test_last_30_min_no_arm(self):
        d, AH = self._cleared(True); AH.IR_checks = lambda m, t, n: (True, [])
        late = dt.datetime(2026, 10, 27, 6, 10, tzinfo=UTC)   # 15:10 JST, close 15:30
        os.utime(os.path.join(d, "state", "clearance", "20261027", "jp", "summary.json"), (late.timestamp() - 60,) * 2)
        st = AH.run("jp", now=late, fetch=lambda y, s: [], base=d, live=TA.NOLIVE)
        self.assertEqual(st["counts"]["ARMED"], 0)
    def test_asx_intraday_ok_needs_env(self):
        import asx_hunt48 as A; importlib.reload(A)
        os.environ.pop("HUNT48_INTRADAY", None)
        self.assertFalse(A.intraday_ok(dt.datetime(2026, 10, 28, 1, 0, tzinfo=UTC)))
if __name__ == "__main__": unittest.main()
