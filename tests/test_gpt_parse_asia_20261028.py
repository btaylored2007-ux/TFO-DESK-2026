import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hunt_clearance as HC
class Parse(unittest.TestCase):
    def test_plain(self): self.assertEqual(HC.parse_gpt_verdicts("main CWY PASS ok")["main"]["CWY"]["GPT"], "PASS")
    def test_markdown(self):
        p = HC.parse_gpt_verdicts("- `main AAA FAIL thin`\n**main BBB PASS** good\n1. main CCC PASS x\nmain | DDD | FAIL | weak trend")
        self.assertEqual({k: v["GPT"] for k, v in p["main"].items()}, {"AAA": "FAIL", "BBB": "PASS", "CCC": "PASS", "DDD": "FAIL"})
    def test_prompt_lists_desk_fail_rows_for_asia_asx(self):
        src = open(HC.__file__).read(); self.assertIn('not (ASX_ONLY or ASIA_MKT): continue', src)
if __name__ == "__main__": unittest.main()
