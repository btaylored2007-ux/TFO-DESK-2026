"""2026-10-02 19:44 AEST Head Trader call (Brett delegated): NEW shark entries risk 0.5% firm equity to the -5% stop; crypto C3 stays 1%."""
import inspect, os, sys, unittest
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import tfo_buy, tfo_crypto, tfo_watchlist


class SharkHalf(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(tfo_buy.SHARK_RISK_PCT, 0.005)
        self.assertEqual(tfo_buy.SHARK_SIZING_STOP, 0.05)
        self.assertEqual(tfo_buy.SHARK_EXIT_VARIANT, "V7")         # exits unchanged
        self.assertEqual(tfo_crypto.C3_MAX_RISK_PCT, 0.01)         # crypto stays 1%

    def test_max_clip(self):
        self.assertAlmostEqual(tfo_buy.shark_max_clip(1.8e6), 180000.0)
        self.assertAlmostEqual(tfo_buy.shark_max_clip(1797160.76), 179716.076)

    def test_gate_present_before_quote(self):
        src = inspect.getsource(tfo_buy.gates)
        self.assertIn("shark_max_clip(seq)", src)   # sizes off firm equity ex veteran_desk, not the raw eq local
        self.assertLess(src.index("shark_max_clip(seq)"), src.index("fetch_prices"))

    def test_watchlist_caps_equity_not_crypto(self):
        self.assertAlmostEqual(tfo_watchlist.shark_cap(1.8e6), 180000.0)
        src = inspect.getsource(tfo_watchlist)
        self.assertEqual(src.count("vsize = min(vsize, shark_cap("), 3)
        self.assertEqual(src.count('if mk != "CRYPTO": vsize = min(vsize, shark_cap('), 2)


if __name__ == "__main__":
    unittest.main()
