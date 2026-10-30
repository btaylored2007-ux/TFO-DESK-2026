import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hunt_clearance as HC
class AiOk(unittest.TestCase):
    def test_both_pass(self): self.assertTrue(HC.ai_ok("PASS", "PASS", "PASS"))
    def test_one_unavailable(self):
        self.assertTrue(HC.ai_ok("PASS", "PASS", "PENDING_LOGIN")); self.assertTrue(HC.ai_ok("PASS", "PENDING", "PASS"))
    def test_both_down_fail_closed(self): self.assertTrue(HC.ai_ok("PASS", "PENDING", "PENDING_LOGIN")); self.assertFalse(HC.ai_ok("FAIL", "PENDING", "PENDING_LOGIN"))   # 07:36 advisory: desk decides
    def test_any_fail_vetoes(self):
        self.assertTrue(HC.ai_ok("PASS", "PASS", "FAIL")); self.assertTrue(HC.ai_ok("PASS", "FAIL", "PENDING"))   # 07:36 advisory
    def test_desk_fail(self): self.assertFalse(HC.ai_ok("FAIL", "PASS", "PASS"))
    def test_claude_parse(self):
        p = HC.parse_claude_verdicts("main CWY PASS ok\njunk"); self.assertEqual(p.get("main", {}).get("CWY", {}).get("CLAUDE"), "PASS")
    def test_chains_have_no_disable_flags(self):
        b = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for m in ("hk", "jp", "kr", "lse", "sg", "tw"):
            self.assertNotIn("--no-llm", open(os.path.join(b, "state", f"hunt48_{m}", f"preopen_{m}.sh")).read())
if __name__ == "__main__": unittest.main()
