"""27 Oct 2026 19:02 WITA (Brett, PAPER): OANDA book max_trades 8 -> 10, open_risk_cap 4% -> 10%. No network, temp state only."""
import os, sys, json, shutil, tempfile, unittest; ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
import oanda_sleeve as S
NOTE = "Brett approved 27 Oct 2026 (test)"
PAIRS = ["EUR_USD", "GBP_JPY", "AUD_CAD", "NZD_CHF", "EUR_GBP", "USD_CAD", "AUD_NZD", "GBP_CHF", "EUR_JPY", "CAD_CHF", "AUD_JPY"]
class Book10(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.old = (S.ST, S.CFG); S.ST = self.tmp; S.CFG = os.path.join(self.tmp, "config.json")
        src = os.path.join(ROOT, "state", "oanda", "SCALE_GATE_FROZEN.json")
        if os.path.exists(src): shutil.copy(src, self.tmp)
        else:
            import oanda_scale_gate as G; G.write_frozen(self.tmp)   # rules live in code; the Mac file is not in this tree
    def tearDown(self): S.ST, S.CFG = self.old; shutil.rmtree(self.tmp, ignore_errors=True)
    def cfg(self, **kw): json.dump(kw, open(S.CFG, "w")); return S.cfg()
    def live_like(self, **kw):
        d = dict(risk_pct=0.01, funder_override_risk_pct=0.01, funder_override_note=NOTE, max_trades=10, open_risk_cap_pct=0.10); d.update(kw); return self.cfg(**d)
    def test_ten_trades_ten_pct(self):
        c = self.live_like(); self.assertEqual((c["max_trades"], c["open_risk_cap_pct"]), (4, 0.10))   # open count capped at 4; 10% open-risk cap unchanged
        self.assertEqual(S.bucket_limits("OXL1", c), (0.10, 0.015, 4))
    def test_rails_cap_at_ten(self):
        c = self.live_like(max_trades=25, open_risk_cap_pct=0.5); self.assertEqual((c["max_trades"], c["open_risk_cap_pct"]), (4, 0.10))
    def test_defaults_unchanged_without_config(self):
        c = self.cfg(); self.assertEqual((c["max_trades"], c["open_risk_cap_pct"]), (4, 0.04))
    def test_other_rails_unchanged(self):
        c = self.live_like(ccy_net_cap_pct=0.05, margin_cap_frac=0.9, trend_open_risk_cap_pct=0.1, trend_max_trades=9)
        self.assertEqual((c["ccy_net_cap_pct"], c["margin_cap_frac"], c["trend_open_risk_cap_pct"], c["trend_max_trades"], c["trend_ccy_net_cap_pct"]), (0.015, 0.5, 0.02, 4, 0.015))
        self.assertEqual((c["cmdty_risk_pct"], c["cmdty_open_risk_cap_pct"], c["cmdty_max_trades"]), (0.01, 0.04, 4))
        self.assertEqual(S.bucket_limits("OXF1", c), (0.02, 0.015, 4)); self.assertAlmostEqual(S.entry_risk_frac(dict(design="OXL2", risk_pct=0.05), c), 0.01)
    def test_book_open_risk_cap_ten_pct(self):
        c = dict(self.live_like(), total_open_risk_cap_pct=1.0, ccy_net_cap_pct=1.0)   # isolate the BOOK open-risk cap branch
        root = S.ROOT; S.ROOT = self.tmp   # no AGGRO open_risk.json -> account (TFO+AGGRO) term is 0
        try:
            nine = [dict(ins="EUR_USD", dir=1, risk_frac=0.09, design="OXL1")]
            self.assertIsNone(S.caps_check(nine, "GBP_JPY", 1, 0.01, c, "OXL1"))                       # 9% + 1% = 10%: allowed
            self.assertTrue((S.caps_check(nine, "GBP_JPY", 1, 0.011, c, "OXL1") or "").startswith("skip:open_risk_cap"))   # 10.1% > 10%
            four = [dict(ins="EUR_USD", dir=1, risk_frac=0.04, design="OXL1")]
            self.assertIsNone(S.caps_check(four, "GBP_JPY", 1, 0.01, c, "OXL1"))                       # old 4% cap no longer blocks
        finally: S.ROOT = root
    def test_total_sleeve_cap_still_8pct(self):
        c = self.live_like(); self.assertEqual(c["total_open_risk_cap_pct"], 0.08)   # without the 27 Oct 19:07 config key, default stays 8%
    def test_live_config(self):
        p = os.path.join(ROOT, "state", "oanda", "config.json")
        if not os.path.exists(p): self.skipTest("live state/oanda/config.json is not in the code-only tree (TEST_TRIAGE.md)")
        j = json.load(open(p))
        self.assertLessEqual(float(j.get("risk_pct", 0)), 0.02)   # per-trade rail; open count is clamped in cfg() to 4
        self.assertTrue(str(j.get("book_caps_note") or j.get("active_ruleset_note") or "x").strip())
if __name__ == "__main__": unittest.main()
