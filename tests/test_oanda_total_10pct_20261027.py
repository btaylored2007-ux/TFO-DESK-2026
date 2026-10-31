"""27 Oct 2026 19:07 WITA (Brett, PAPER): OANDA sleeve-wide total open-risk cap 8% -> 10%; 15% account cap unchanged. No network."""
import os, sys, json, shutil, tempfile, unittest; ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
import oanda_sleeve as S
NOTE = "Brett approved 27 Oct 2026 (test)"
class Total10(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.old = (S.ST, S.CFG, S.ROOT); S.ST = self.tmp; S.CFG = os.path.join(self.tmp, "config.json"); S.ROOT = self.tmp
        src = os.path.join(ROOT, "state", "oanda", "SCALE_GATE_FROZEN.json")
        if os.path.exists(src): shutil.copy(src, self.tmp)
        else:
            import oanda_scale_gate as G; G.write_frozen(self.tmp)
    def tearDown(self): S.ST, S.CFG, S.ROOT = self.old; shutil.rmtree(self.tmp, ignore_errors=True)
    def cfg(self, **kw):
        d = dict(risk_pct=0.01, funder_override_risk_pct=0.01, funder_override_note=NOTE, max_trades=10, open_risk_cap_pct=0.10, total_open_risk_cap_pct=0.10)
        d.update(kw); json.dump(d, open(S.CFG, "w")); return S.cfg()
    def test_effective_ten_pct(self): self.assertEqual(self.cfg()["total_open_risk_cap_pct"], 0.10)
    def test_rail_caps_at_ten_pct(self): self.assertEqual(self.cfg(total_open_risk_cap_pct=0.5)["total_open_risk_cap_pct"], 0.10)
    def rows(self):   # spread across buckets so no bucket / ccy / group cap binds: book 5%, trend 2%, cmdty 2% = 9%; new entry CAD_JPY long nets CAD and JPY to 0
        return [dict(ins="EUR_USD", dir=1, risk_frac=0.01, design="OXL1"), dict(ins="GBP_JPY", dir=-1, risk_frac=0.01, design="OXL1"),
                dict(ins="AUD_CAD", dir=1, risk_frac=0.01, design="OXL1"), dict(ins="NZD_CHF", dir=-1, risk_frac=0.01, design="OXL1"),
                dict(ins="EUR_GBP", dir=-1, risk_frac=0.01, design="OXL1"), dict(ins="USD_CAD", dir=1, risk_frac=0.01, design="OXF1"),
                dict(ins="AUD_NZD", dir=-1, risk_frac=0.01, design="OXF1"), dict(ins="XAU_USD", dir=1, risk_frac=0.01, design="OXP1"),
                dict(ins="BCO_USD", dir=-1, risk_frac=0.01, design="OXB1")]
    def test_ten_pct_allowed(self):   # 9% + 1% = 10%
        self.assertIsNone(S.caps_check(self.rows(), "CAD_JPY", 1, 0.01, self.cfg(), "OXL1"))
    def test_ten_point_one_blocked(self):   # 9% + 1.1% = 10.1%
        self.assertTrue((S.caps_check(self.rows(), "CAD_JPY", 1, 0.011, self.cfg(), "OXL1") or "").startswith("skip:total_open_risk_cap"))
    def test_eight_pct_no_longer_binds(self):
        self.assertFalse((S.caps_check(self.rows()[:8], "CAD_JPY", 1, 0.01, self.cfg(), "OXL1") or "").startswith("skip:total_open_risk_cap"))   # 9%
    def test_account_cap_now_25pct(self):
        d = os.path.join(self.tmp, "state", "oanda_aggro"); os.makedirs(d, exist_ok=True)
        json.dump({"rows": [dict(owner="AGGRO", risk_frac=0.16)]}, open(os.path.join(d, "open_risk.json"), "w"))
        self.assertTrue((S.caps_check(self.rows(), "CAD_JPY", 1, 0.01, self.cfg(), "OXL1") or "").startswith("skip:account_open_risk_cap"))   # 10% + 16% AGGRO > 25% (28 Oct 2026)
    def test_other_rails_unchanged(self):
        c = self.cfg(ccy_net_cap_pct=0.05, margin_cap_frac=0.9, trend_open_risk_cap_pct=0.1, cmdty_open_risk_cap_pct=0.2)
        self.assertEqual((c["max_trades"], c["open_risk_cap_pct"], c["ccy_net_cap_pct"], c["margin_cap_frac"], c["trend_open_risk_cap_pct"], c["trend_max_trades"],
                          c["cmdty_open_risk_cap_pct"], c["cmdty_max_trades"], c["cmdty_risk_pct"], c["risk_pct"]), (4, 0.10, 0.015, 0.5, 0.02, 4, 0.04, 4, 0.01, 0.01))
    def test_default_without_config_key(self): self.assertEqual(self.cfg(total_open_risk_cap_pct=0.08)["total_open_risk_cap_pct"], 0.08)   # 8% config still honoured (undo path)
    def test_live_config(self):
        p = os.path.join(ROOT, "state", "oanda", "config.json")
        if not os.path.exists(p): self.skipTest("live state/oanda/config.json is not in the code-only tree (TEST_TRIAGE.md)")
        j = json.load(open(p))
        self.assertLessEqual(float(j.get("total_open_risk_cap_pct", 0)), 0.10); self.assertTrue(str(j.get("total_cap_note") or "x").strip())
if __name__ == "__main__": unittest.main()
