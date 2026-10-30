"""16 Oct 2026: commodity bucket (OXP1/OXB1) caps, risk rail, total 8% ceiling, exits. No network."""
import os, sys, unittest; ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
import oanda_sleeve as S, oanda_designs as D, oanda_setups as X
def row(ins, d, design, rf): return dict(ins=ins, dir=d, risk_frac=rf, design=design)
class T(unittest.TestCase):
    def setUp(self): self.c = S.cfg()
    def test_bucket(self): self.assertEqual(S.bucket("OXP1"), "cmdty"); self.assertEqual(S.bucket("OXL2"), "book")
    def test_risk(self):
        self.assertEqual(S.entry_risk_frac(dict(design="OXP1", risk_pct=0.02), self.c), 0.01)
        self.assertLessEqual(S.entry_risk_frac(dict(design="OXL2", risk_pct=0.0075), self.c), self.c["risk_pct"] + 1e-12)   # 27 Oct 2026: book ceiling = live risk_pct (1% under Brett funder override, else 0.5% gate)
        self.assertLessEqual(self.c["risk_pct"], 0.01 + 1e-12)
    def test_oil_one_bet(self):
        r = [row("WTICO_USD", 1, "OXP1", 0.01)]
        self.assertIn("OIL", S.caps_check(r, "BCO_USD", 1, 0.0075, self.c, "OXB1") or "")
        self.assertIsNone(S.caps_check([], "WTICO_USD", 1, 0.0075, self.c, "OXP1"))
    def test_metals_counts_oxl1(self):
        r = [row("XAU_USD", 1, "OXL1", 0.005), row("XAG_USD", 1, "OXP1", 0.0075)]
        self.assertIn("METALS", S.caps_check(r, "XCU_USD", 1, 0.0075, self.c, "OXB1") or "")
    def test_total_cap(self):
        # 27 Oct 2026 (Brett 19:07 WITA): sleeve-wide cap 8% -> 10%; book row raised 4% -> 6% so the new total (10.25%) still breaches
        r = [row("EUR_USD", 1, "OXL2", 0.06), row("GBP_JPY", 1, "OXF1", 0.02), row("XAU_USD", 1, "OXP1", 0.0075), row("BCO_USD", 1, "OXB1", 0.0075)]
        import tempfile; root = S.ROOT; S.ROOT = tempfile.mkdtemp()   # no AGGRO open_risk.json -> account 15% term is 0
        try: self.assertIn("skip:total_open_risk_cap", S.caps_check(r, "XCU_USD", -1, 0.0075, self.c, "OXP1") or "")
        finally: S.ROOT = root
    def test_rails(self):
        self.assertLessEqual(self.c["total_open_risk_cap_pct"], 0.10);   # 27 Oct 2026: 8% -> 10%
        self.assertLessEqual(self.c["cmdty_risk_pct"], 0.01)
    def test_exit_score(self):
        cs = [dict(t="2020-01-%02d" % (i % 28 + 1), o=100 + i, h=101 + i, l=99 + i, c=100 + i, sp=1e-4) for i in range(320)]
        self.assertEqual(D.score_for("OXP1", {"XAU_USD": cs}, "XAU_USD", 1), 1)    # uptrend: hold long
        self.assertEqual(D.score_for("OXP1", {"XAU_USD": cs}, "XAU_USD", -1), 0)   # short in uptrend: exit
        self.assertIsNone(D.score_for("OXB1", {}, "XAU_USD", 1))
if __name__ == "__main__": unittest.main(verbosity=1)
