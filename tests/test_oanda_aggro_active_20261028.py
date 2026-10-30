"""28 Oct 2026 13:14 WITA (Brett 'Change the oanda to riskier version'): AGGRO is the active OANDA paper ruleset. No network."""
import json, os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_oanda_widen_caps_20261012 import Base
import oanda_aggro, warroom_markets


class AggroActive(Base):
    def test_live_config_active_ruleset_aggro(self):
        c = json.load(open(os.path.join(ROOT, "state", "oanda", "config.json")))
        self.assertEqual(c["active_ruleset"], "AGGRO"); self.assertIn("Brett", c["active_ruleset_note"])

    def test_design_sleeve_opens_nothing_and_logs(self):
        json.dump({"active_ruleset": "AGGRO"}, open(self.m.CFG, "w"))
        self.assertEqual(self.m.place_entry(self.entry("EUR_USD"), live=True), "skip:ruleset_aggro_active")
        ev = [l for l in self.lines() if l.get("event") == "ruleset_skip"]; self.assertEqual(len(ev), 1)

    def test_design_ruleset_still_enters(self):
        json.dump({"active_ruleset": "DESIGN"}, open(self.m.CFG, "w"))
        self.assertEqual(self.m.place_entry(self.entry("EUR_USD"), live=False), "dry")

    def test_aggro_rules_stop_and_risk(self):
        c = oanda_aggro.cfg(); self.assertLessEqual(c["risk_pct"], 0.03); self.assertLessEqual(c["open_risk_cap_pct"], 0.25)
        src = open(os.path.join(ROOT, "oanda_aggro.py")).read(); self.assertIn("stopLossOnFill", src); self.assertIn('"tag": TAG', src)
        self.assertTrue(c["live"])

    def test_warroom_card_shows_aggro(self):
        self.assertEqual(warroom_markets.oanda_ruleset(), "AGGRO")


if __name__ == "__main__":
    unittest.main()
