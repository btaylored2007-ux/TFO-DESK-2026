import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategy_tracker as ST
class T(unittest.TestCase):
    def test_rows_sorted_every_strategy_running_verdict_info(self):
        R = ST.rows(); names = [r["strategy"] for r in R]
        for n in ("Hunt US main", "Hunt US Claude", "Hunt US GPT", "Hunt ASX", "Hunt Japan", "Hunt London", "Trend18 / DEEP18 R2", "CSP put sleeve", "Crypto entry engine (Kraken breakout)"): self.assertIn(n, names)
        p = [r["pnl_aud"] for r in R if r["pnl_aud"] is not None]; self.assertEqual(p, sorted(p, reverse=True))
        self.assertTrue(all(r["status"] in ("Running", "Test", "Under review", "Staged 2 Nov", "Executes 28 Oct US open", "Empty", "HALTED") for r in R))
        self.assertTrue(all("info" in r["verdict"] or r["status"] == "Test" for r in R))
        h = [r for r in R if r["strategy"].startswith("Hunt ")]; self.assertTrue(all("Target +3%" in r["rule"] and r["be_win_rate"] >= 0.727 for r in h))
        c = [r for r in R if r["strategy"].startswith("Crypto entry")][0]; self.assertIn("27 Nov 2026", c["pass_mark"])
        self.assertIn("<table", ST.html(dict(rows=R)))
if __name__ == "__main__": unittest.main()
