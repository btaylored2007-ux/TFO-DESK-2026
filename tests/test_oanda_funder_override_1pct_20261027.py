"""27 Oct 2026 18:57 WITA (Brett, PAPER): funder override of the frozen scale gate -> book risk 1%/trade. No network, temp state only."""
import os, sys, json, shutil, tempfile, unittest; ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
import oanda_sleeve as S, oanda_scale_gate as G
NOTE = "Brett approved 27 Oct 2026 18:57 WITA (test)"
class FunderOverride(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.old = (S.ST, S.CFG); S.ST = self.tmp; S.CFG = os.path.join(self.tmp, "config.json")
        src = os.path.join(ROOT, "state", "oanda", "SCALE_GATE_FROZEN.json")
        if os.path.exists(src): shutil.copy(src, self.tmp)
        else: G.write_frozen(self.tmp)   # same RULES the Mac file is hashed from
    def tearDown(self): S.ST, S.CFG = self.old; shutil.rmtree(self.tmp, ignore_errors=True)
    def cfg(self, **kw): json.dump(kw, open(S.CFG, "w")); return S.cfg()
    def test_gate_alone_is_half_pct(self): self.assertEqual(G.allowed_risk_pct(self.tmp), 0.005)
    def test_with_override_one_pct(self):
        c = self.cfg(risk_pct=0.01, funder_override_risk_pct=0.01, funder_override_note=NOTE)
        self.assertAlmostEqual(c["risk_pct"], 0.01)
        self.assertAlmostEqual(S.entry_risk_frac(dict(design="OXL1", risk_pct=0.005), c), 0.01)
        self.assertAlmostEqual(S.entry_risk_frac(dict(design="OXF1", risk_pct=0.005), c), 0.01)
        self.assertAlmostEqual(S.entry_risk_frac(dict(design="OXF1", risk_pct=0.0025), c), 0.005)   # XAU half size kept
    def test_without_override_half_pct(self):
        c = self.cfg(risk_pct=0.01)
        self.assertAlmostEqual(c["risk_pct"], 0.005); self.assertIsNone(c["funder_override_risk_pct"])
        self.assertAlmostEqual(S.entry_risk_frac(dict(design="OXL1", risk_pct=0.005), c), 0.005)
    def test_override_needs_note(self):
        self.assertAlmostEqual(self.cfg(risk_pct=0.01, funder_override_risk_pct=0.01)["risk_pct"], 0.005)
    def test_override_never_above_one_pct(self):
        c = self.cfg(risk_pct=0.05, funder_override_risk_pct=0.05, funder_override_note=NOTE)
        self.assertAlmostEqual(c["risk_pct"], 0.01); self.assertAlmostEqual(S.entry_risk_frac(dict(design="OXL2", risk_pct=0.02), c), 0.01)
    def test_exception_fallback(self):
        real = G.allowed_risk_pct
        def boom(st): raise RuntimeError("x")
        G.allowed_risk_pct = boom
        try:
            self.assertAlmostEqual(self.cfg(risk_pct=0.01, funder_override_risk_pct=0.01, funder_override_note=NOTE)["risk_pct"], 0.01)
            self.assertAlmostEqual(self.cfg(risk_pct=0.01)["risk_pct"], 0.005)
        finally: G.allowed_risk_pct = real
    def test_caps_and_cmdty_unchanged(self):
        c = self.cfg(risk_pct=0.01, funder_override_risk_pct=0.01, funder_override_note=NOTE)
        self.assertEqual((c["open_risk_cap_pct"], c["max_trades"], c["ccy_net_cap_pct"], c["trend_open_risk_cap_pct"], c["cmdty_risk_pct"]), (0.04, 4, 0.015, 0.02, 0.01))
        self.assertAlmostEqual(S.entry_risk_frac(dict(design="OXP1", risk_pct=0.0075), c), 0.0075)   # cmdty bucket not scaled
    def test_frozen_gate_untouched(self):
        import hashlib
        pin = "b6a6c71bfb261140b26ab147305782f2701a808efc030dda5891d2eda21d87d4"
        self.assertEqual(G.rules_hash(G.RULES), pin)
        p = os.path.join(ROOT, "state", "oanda", "SCALE_GATE_FROZEN.json")
        if os.path.exists(p):
            j = json.load(open(p))
            self.assertEqual(j["sha256"], pin)
            self.assertEqual(hashlib.sha256(json.dumps(j["rules"], sort_keys=True).encode()).hexdigest(), j["sha256"])
    def test_live_config_carries_override(self):
        p = os.path.join(ROOT, "state", "oanda", "config.json")
        if not os.path.exists(p): self.skipTest("live state/oanda/config.json is not in the code-only tree (TEST_TRIAGE.md)")
        j = json.load(open(p))
        self.assertEqual((j["risk_pct"], j["funder_override_risk_pct"]), (0.01, 0.01)); self.assertTrue(j["funder_override_note"].strip())
if __name__ == "__main__": unittest.main()
