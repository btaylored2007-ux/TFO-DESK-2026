"""Catalyst capture flags (Brett 2026-10-28 04:50 WITA). Offline. PAPER only."""
import os, sys, unittest, datetime as dt, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import catalyst_capture as CC
NOW = dt.datetime(2026, 10, 28, 12, tzinfo=dt.timezone.utc)
class Flags(unittest.TestCase):
    def test_none(self): self.assertFalse(CC.flags(dict(relvol=1.2), NOW)["catalyst_flag"])
    def test_relvol(self): self.assertTrue(CC.flags(dict(relvol=2.0), NOW)["catalyst_flag"])
    def test_surprise_recent_only(self):
        self.assertTrue(CC.flags(dict(surprise_pct=12, report_age_d=2), NOW)["catalyst_flag"])
        self.assertFalse(CC.flags(dict(surprise_pct=12, report_age_d=40), NOW)["catalyst_flag"])
        self.assertFalse(CC.flags(dict(surprise_pct=2, report_age_d=1), NOW)["catalyst_flag"])
    def test_analyst(self): self.assertTrue(CC.flags(dict(actions_5d=[dict(firm="X", action="up", to="Buy")]), NOW)["catalyst_flag"])
    def test_8k_and_guidance(self):
        f = CC.flags({"8k_5d": [dict(date="2026-10-27", items="2.02,9.01")]}, NOW)
        self.assertTrue(f["catalyst_flag"]); self.assertTrue(f["guidance_8k"])
        self.assertFalse(CC.flags({"8k_5d": [dict(date="2026-10-23", items="5.02")]}, NOW)["catalyst_flag"])
    def test_load_flags_missing(self): self.assertEqual(CC.load_flags("19990101", base=tempfile.mkdtemp()), {})
if __name__ == "__main__": unittest.main()
