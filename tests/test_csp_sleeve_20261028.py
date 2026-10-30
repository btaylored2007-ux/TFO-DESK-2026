"""CSP sleeve forward (Brett 2026-10-28 05:27 WITA). Offline. PAPER only."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import csp_sleeve_fwd as C
class CSP(unittest.TestCase):
    def test_gate(self):
        self.assertTrue(C.gate("SPY", 60, 20, 18)[0]); self.assertFalse(C.gate("SPY", 25, 30, 18)[0]); self.assertTrue(C.gate("SPY", 30, 15, 18)[0])
        self.assertTrue(C.gate("SPY", None, 20, 18)[0]); self.assertFalse(C.gate("SPY", None, 15, 18)[0])
    def test_choose_put_delta_band(self):
        rows = [dict(strike=k, bid=1.0, ask=1.1, iv=0.2) for k in range(80, 101)]
        r, d = C.choose_put(100, rows, 30 / 365, 0.0); self.assertTrue(0.20 <= d <= 0.30)
        self.assertIsNone(C.choose_put(100, [dict(strike=100, bid=0, ask=1, iv=.2)], 30 / 365, 0))
    def test_size_respects_cash_and_crash(self):
        n, per = C.size(500, 520, 5.0, "SPY", 0.7, []); self.assertGreaterEqual(n, 0)
        self.assertLessEqual(n * 500 * 100, C.SLEEVE_AUD * 0.7); self.assertLessEqual(n * per, C.CRASH_BUDGET_AUD)
    def test_manage(self):
        p = dict(exp="2026-11-27", credit=2.0, K=100)
        self.assertEqual(C.manage(p, 1.0, 105, "2026-11-01"), "tp"); self.assertEqual(C.manage(p, 6.0, 90, "2026-11-01"), "stop")
        self.assertIsNone(C.manage(p, 2.5, 100, "2026-11-01")); self.assertEqual(C.manage(p, None, 95, "2026-11-27"), "assigned")
    def test_no_order_paths(self):
        src = open(C.__file__).read().lower()
        for bad in ("place_order", "placeorder", "ib_insync", "kdesk", "betfair", "8798"): self.assertNotIn(bad, src)
    def test_summary_line_and_report(self):
        self.assertIn("CSP sleeve (PAPER", C.summary_line())
        self.assertIn("csp_sleeve_fwd.summary_line()", open(os.path.join(os.path.dirname(C.__file__), "make_desk_report.py")).read())
if __name__ == "__main__": unittest.main()
