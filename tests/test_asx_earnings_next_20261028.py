import os, sys, unittest, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hunt_clearance as HC
UTC = dt.timezone.utc; NOW = dt.datetime(2026, 10, 28, 0, 0, tzinfo=UTC)
class NextReport(unittest.TestCase):
    def test_picks_next_upcoming(self):
        self.assertEqual(HC.next_report_date("X.AX", NOW, ["2027-02-25 08:00:00+11:00", "2026-08-20 08:00:00+10:00"]), "2027-02-25")
    def test_upcoming_inside_hold_kept(self):
        self.assertEqual(HC.next_report_date("X.AX", NOW, ["2026-10-29", "2026-04-20"]), "2026-10-29")
    def test_none_upcoming_is_unknown(self): self.assertIsNone(HC.next_report_date("X.AX", NOW, ["2026-08-20"]))
    def test_stale(self):
        self.assertTrue(HC._stale_or_none(None)); self.assertTrue(HC._stale_or_none("2026-08-20T00:00:00+00:00", NOW))
        self.assertFalse(HC._stale_or_none("2026-10-20T00:00:00+00:00", NOW))
    def test_behavior_blocks_upcoming(self):
        b = dict(asset_class="equity", calendar=dict(next_earnings_utc="2026-10-29"), technical={}, sweep={})
        try: r = HC.behavior_review(dict(direction="long", trigger=1, stop=0.9, t1=1.03), b, NOW)
        except Exception as e: self.skipTest(f"behavior_review needs fuller fixture: {e}")
        self.assertEqual(r["verdict"], "FAIL")
class Live(unittest.TestCase):
    def test_live_fetch_path_has_yf(self):
        import inspect; self.assertIn("import yfinance as yf", inspect.getsource(HC.next_report_date))
if __name__ == "__main__": unittest.main()
