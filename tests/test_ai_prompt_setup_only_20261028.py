import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hunt_clearance as HC
class P(unittest.TestCase):
    def test_rules_text(self):
        r = HC.SETUP_ONLY_RULES
        for w in ("trend", "catalyst", "volume", "liquidity", "chart structure", "event risk", "FIXED BY THE FUNDER", "R-multiple", "break-even", "must cite"): self.assertIn(w, r)
    def test_used_in_all_three_prompt_sites(self):
        self.assertEqual(open(HC.__file__).read().count("SETUP_ONLY_RULES"), 4)  # def + external prompt + codex + claude
    def test_shape_only_fail_ignored(self):
        p = HC.parse_gpt_verdicts("main AAA FAIL 0.31R reward vs 8% risk, break-even 73%\nmain BBB FAIL downtrend below 50dma, thin volume")
        self.assertEqual(p["main"]["AAA"]["GPT"], "UNKNOWN"); self.assertEqual(p["main"]["BBB"]["GPT"], "FAIL")
if __name__ == "__main__": unittest.main()
