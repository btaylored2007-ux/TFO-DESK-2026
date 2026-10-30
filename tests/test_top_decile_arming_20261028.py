"""Top-6 arming (Brett 2026-10-28 21:07 WITA; was top-decile 04:50). Offline. PAPER only."""
import os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hunt_orchestrator as HO
B = dict(direction="short", trigger=10.0, max_fill=9.95, stop=10.5, t1=9.5, t2=9.0, shares=100, sector="Tech", scan_px=10.1, rank=1, state="READY")
class TopDecile(unittest.TestCase):
    def test_eligibility(self):
        self.assertTrue(HO.arm_eligible(dict(top_decile=True)))
        self.assertFalse(HO.arm_eligible(dict(top_decile=False)))
        self.assertFalse(HO.arm_eligible({}))                       # unknown -> fail closed
        self.assertTrue(HO.arm_eligible(dict(manual_stage=True)))   # KNSA manual stage exempt
    def test_promote_skips_non_top_decile(self):
        st = {"main": [dict(B, ticker="LOW", top_decile=False)]}
        with mock.patch.object(HO, "_occupied", return_value=({}, [], [], [])), mock.patch.object(HO, "apply_arm") as arm:
            out = HO.promote("main", "20261028", st, HO.now_utc(), "pre", HO.now_utc(), {}, [])
        self.assertEqual(out, []); arm.assert_not_called(); self.assertEqual(st["main"][0]["state"], "READY")
        self.assertIn("not top 6", st["main"][0]["last_block"][0])  # Brett 2026-10-28 21:07: top 6 replaces top decile
    def test_candidate_list_basis(self):   # Brett 2026-10-28 21:07: top TOP_N=6 of night's candidate list (was 10%/ceil)
        self.assertEqual(HO.TOP_N, 6)
        c = [dict(ticker=f"T{i}", score=float(i)) for i in range(25)]
        self.assertEqual(HO.mark_top_decile(c), 6)
        self.assertEqual(sorted(x["ticker"] for x in c if x["top_decile"]), ["T19", "T20", "T21", "T22", "T23", "T24"])
        self.assertEqual(max(x["rank_n"] for x in c), 25)
        one = [dict(ticker="A", score=0.1), dict(ticker="B", score=0.5)]
        self.assertEqual(HO.mark_top_decile(one), 2); self.assertTrue(one[1]["top_decile"]); self.assertTrue(one[0]["top_decile"])  # both fit in top 6
        self.assertEqual(HO.mark_top_decile([]), 0)
        # frac= still supports legacy top-decile math when explicitly requested
        c2 = [dict(ticker=f"T{i}", score=float(i)) for i in range(25)]
        self.assertEqual(HO.mark_top_decile(c2, frac=0.10), 3)
if __name__ == "__main__": unittest.main()
