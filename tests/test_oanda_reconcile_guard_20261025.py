"""25 Oct 2026 (Brett ~18:50 WITA, TFO Risk audited): OANDA reconcile guard. No network."""
import datetime as dt, json, os, sys, tempfile, shutil, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
import oanda_reconcile_guard as RG
from test_oanda_widen_caps_20261012 import Base

U = dt.timezone.utc
T0 = dt.datetime(2026, 10, 22, 2, 20, tzinfo=U)

class Guard(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.logs = []
        RG._notify = lambda msg: None
    def tearDown(self): shutil.rmtree(self.d, ignore_errors=True)
    def rec(self, ok, mins): return RG.record_pass(self.d, ok, None if ok else "DNS", self.logs.append, T0 + dt.timedelta(minutes=mins))
    def test_clean_by_default(self):
        self.assertEqual(RG.status(self.d), "CLEAN"); self.assertTrue(RG.verified(self.d)); self.assertFalse(RG.entries_blocked(self.d))
    def test_failing_is_unverified_but_not_blocked_within_2h(self):
        self.rec(False, 0); self.rec(False, 119)
        self.assertEqual(RG.status(self.d, T0 + dt.timedelta(minutes=119)), "FAILING")
        self.assertFalse(RG.verified(self.d, T0 + dt.timedelta(minutes=119)))
        self.assertFalse(RG.entries_blocked(self.d, T0 + dt.timedelta(minutes=119)))
        self.assertEqual(RG.board(self.d, T0 + dt.timedelta(minutes=119))["book_label"], "UNVERIFIED")
        self.assertEqual(self.logs, [])
    def test_blocks_after_2h_and_alerts_once(self):
        self.rec(False, 0); self.assertEqual(self.rec(False, 121), "BLOCKED"); self.rec(False, 136)
        self.assertEqual([l["event"] for l in self.logs], ["reconcile_alert"])
        self.assertTrue(os.path.exists(os.path.join(self.d, RG.ALERT)))
        self.assertTrue(RG.entries_blocked(self.d, T0 + dt.timedelta(minutes=136)))
    def test_time_based_block_without_new_run(self):
        self.rec(False, 0)
        self.assertTrue(RG.entries_blocked(self.d, T0 + dt.timedelta(hours=3)))
    def test_clears_only_after_one_full_clean_pass(self):
        self.rec(False, 0); self.rec(False, 130)
        self.assertEqual(self.rec(True, 145), "CLEAN")
        self.assertEqual(self.logs[-1]["event"], "reconcile_alert_cleared")
        self.assertFalse(os.path.exists(os.path.join(self.d, RG.ALERT)))
        self.assertTrue(RG.verified(self.d, T0 + dt.timedelta(minutes=146)))
    def test_new_failure_after_clear_restarts_clock(self):
        self.rec(False, 0); self.rec(True, 10); self.rec(False, 200)
        self.assertEqual(RG.status(self.d, T0 + dt.timedelta(minutes=300)), "FAILING")

class SleeveGate(Base):
    def test_place_entry_deferred_when_blocked(self):
        RG.record_pass(self.m.ST, False, "DNS", None, dt.datetime.now(U) - dt.timedelta(hours=3))
        self.assertEqual(self.m.place_entry(self.entry("EUR_USD"), live=True), "defer:reconcile_blocked")
    def test_place_entry_unaffected_when_clean(self):
        self.assertNotEqual(self.m.place_entry(self.entry("EUR_USD"), live=False), "defer:reconcile_blocked")

class FirmRisk(unittest.TestCase):
    def test_monitor_does_not_count_marks_when_unverified(self):
        import firm_risk as FR
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "stats.json"); json.dump({"nav": 50000, "open_risk_pct": 3.0, "open_risk_cap_pct": 6, "aggro_upl_aud": 120.0}, open(p, "w"))
            orig = RG.board
            RG.board = lambda st, now=None: {"verified": False, "reconcile_status": "BLOCKED", "entries_blocked": True, "reconcile_first_fail_utc": "x"}
            try: out = FR.oanda_aggro_monitor(p)
            finally: RG.board = orig
            self.assertFalse(out["verified"]); self.assertFalse(out["marks_counted"])
            self.assertIsNone(out["account_open_risk_pct"]); self.assertIsNone(out["aggro_upl_aud"])
            self.assertTrue(any("UNVERIFIED" in w for w in out["warnings"]))
            RG.board = lambda st, now=None: {"verified": True}
            try: out2 = FR.oanda_aggro_monitor(p)
            finally: RG.board = orig
            self.assertTrue(out2["marks_counted"]); self.assertEqual(out2["account_open_risk_pct"], 3.0)
        finally: shutil.rmtree(d, ignore_errors=True)

if __name__ == "__main__": unittest.main()
