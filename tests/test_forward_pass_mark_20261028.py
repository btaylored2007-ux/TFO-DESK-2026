"""Forward-book pass mark (Brett 2026-10-28 04:50 WITA). Offline. PAPER only."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import forward_pass_mark as F
CFG = dict(checkpoints=[50, 100], p_max=0.10, n_perm=2000, audusd_fixed=0.70)
def tr(usd, i): return dict(status="CLOSED", entry=10.0, realised_usd=usd, fees_usd=0, borrow_usd=0, risk_cap_aud=2500, exits=[dict(utc=f"2026-11-{1+i%28:02d}T{i%24:02d}:{i%60:02d}")])
class PassMark(unittest.TestCase):
    def test_pending(self):
        o = F.evaluate([tr(100, i) for i in range(10)], CFG); self.assertEqual([c["status"] for c in o["checkpoints"]], ["PENDING", "PENDING"]); self.assertIsNone(o["flag"])
    def test_pass(self):
        o = F.evaluate([tr(500 if i % 3 else -300, i) for i in range(100)], CFG); self.assertEqual([c["status"] for c in o["checkpoints"]], ["PASS", "PASS"]); self.assertIsNone(o["flag"])
    def test_fail_flags_only(self):
        o = F.evaluate([tr(-200 if i % 2 else 150, i) for i in range(100)], CFG)
        self.assertEqual(o["checkpoints"][1]["status"], "FAIL"); self.assertIn("report only", o["flag"])
    def test_unfilled_ignored(self):
        o = F.evaluate([dict(status="CLOSED", entry=None, realised_usd=0)], CFG); self.assertEqual(o["n_closed"], 0)
if __name__ == "__main__": unittest.main()
