"""16 Oct 2026: AGGRO sleeve rails (no network)."""
import os, sys, unittest, datetime as dt; ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
import oanda_aggro as A
def row(ins, d, rf, owner="AGGRO"): return dict(id="1", ins=ins, dir=d, risk_frac=rf, owner=owner)
class T(unittest.TestCase):
    def setUp(self): self.c = A.cfg()
    def test_rails(self):
        self.assertLessEqual(self.c["risk_pct"], 0.03); self.assertLessEqual(self.c["open_risk_cap_pct"], 0.15)
        self.assertGreaterEqual(self.c["weekly_halt_pct"], -0.15); self.assertGreaterEqual(self.c["kill_nav_frac"], 0.60)
        self.assertEqual(set(self.c["designs"]), {"OXB1", "OXP1", "OXT1"})
    def test_total_cap(self):
        r = [row("XAU_USD", 1, .03), row("XAG_USD", -1, .03), row("XCU_USD", 1, .03), row("EUR_JPY", 1, .03, "TFO"), row("GBP_CHF", 1, .02, "TFO")]
        self.assertIn("open_risk_cap", A.caps(r, "BCO_USD", 1, .03, self.c) or "")
    def test_oil_one_bet(self):
        self.assertIn("OIL", A.caps([row("WTICO_USD", 1, .03)], "BCO_USD", 1, .03, self.c) or "")
        self.assertIsNone(A.caps([], "BCO_USD", 1, .03, self.c))
    def test_metals_group(self):
        self.assertIn("METALS", A.caps([row("XAU_USD", 1, .03), row("XAG_USD", 1, .03)], "XCU_USD", 1, .03, self.c) or "")
    def test_protected(self):
        self.assertFalse(A.is_aggro({"id": "61365", "clientExtensions": {"tag": "AGGRO"}}))
        with self.assertRaises(RuntimeError): A.close({"id": "61368", "instrument": "NAS100_USD", "clientExtensions": {}}, "x", False)
    def test_fx_week_rolls_sunday_17_ny(self):
        a = dt.datetime(2026, 10, 18, 20, 59, tzinfo=dt.timezone.utc); b = dt.datetime(2026, 10, 18, 21, 1, tzinfo=dt.timezone.utc)   # 16:59 / 17:01 NY (EDT)
        self.assertNotEqual(A.fx_week(a), A.fx_week(b))
if __name__ == "__main__": unittest.main(verbosity=1)
