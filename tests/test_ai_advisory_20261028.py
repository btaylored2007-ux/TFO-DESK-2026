import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hunt_clearance as HC, hunt_verdict_veto as VV, strategy_tracker as ST
class A(unittest.TestCase):
    def test_desk_pass_clears_despite_ai_fail(self):
        self.assertTrue(HC.ai_ok("PASS", "FAIL", "FAIL")); self.assertTrue(HC.ai_ok("PASS", "PENDING", "PENDING"))
        self.assertFalse(HC.ai_ok("FAIL", "PASS", "PASS"))
    def test_veto_advisory(self):
        V = dict(ok=True, date="20261028", rows={"X": dict(final="CLEARED", GPT="FAIL", CLAUDE="FAIL", GPT_reason="downtrend", in_summary=True, in_external=True),
                                                 "Y": dict(final="BLOCKED", GPT="PASS", CLAUDE="PASS", in_summary=True, in_external=True)})
        self.assertIsNone(VV.veto("X", V)); self.assertIn("BLOCKED", VV.veto("Y", V))
        self.assertIn("stale", VV.veto("X", dict(ok=False, why="verdict file stale")))
        tg = VV.ai_tags("X", V); self.assertEqual(tg["ai_gpt"], dict(verdict="FAIL", reason="downtrend")); self.assertEqual(tg["ai_claude"]["verdict"], "FAIL")
    def test_tracker_split(self):
        cl = [dict(ai_gpt=dict(verdict="PASS"), ai_claude=dict(verdict="PASS")), dict(ai_gpt=dict(verdict="FAIL"), ai_claude=dict(verdict="PASS")), dict()]
        a = ST.ai_split(cl, [100.0, -50.0, 10.0])
        self.assertEqual((a["PASS"]["n"], a["PASS"]["pnl_aud"], a["FAIL"]["n"], a["FAIL"]["pnl_aud"]), (1, 100.0, 1, -50.0))
        self.assertIn("AI-PASS vs AI-FAIL", ST.html(dict(rows=[dict(ST.hunt_row("Hunt ASX", "state/hunt48_asx/ledger.json", "aud", "CUT"))])))
    def test_engines_record_tags(self):
        for f in ("asx_hunt48.py", "asia_hunt48.py", "hunt48_short_paper.py"): self.assertIn("ai_gpt", open(os.path.join(os.path.dirname(HC.__file__), f)).read())
    def test_arb_hard_disarmed(self):
        import asx_hunt48 as A; self.assertIn("ARB.AX", A.HARD_DISARMED)
if __name__ == "__main__": unittest.main()
