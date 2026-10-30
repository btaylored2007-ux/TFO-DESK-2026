"""2026-09-28: funder-watch priority + bench names are always deep-checked and always visible in rejects."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tfo_watchlist as w


class FunderMustCheck(unittest.TestCase):
    def test_us_funder_and_bench_names(self):
        self.assertEqual(w.funder_must_check("US"), {"RCL", "GOOGL", "ONON", "SCHW", "JPM"})
        uni = {s for s, _ in w.universe("US")}
        self.assertTrue(w.funder_must_check("US") <= uni)

    def test_top_rejects_keeps_funder_names_beyond_cut(self):
        rej = [{"symbol": "X%d" % i} for i in range(w.TOP_REJECTS + 5)] + [{"symbol": "ONON"}, {"symbol": "JPM"}]
        out = [r["symbol"] for r in w.top_rejects(rej, "US")]
        self.assertEqual(len(out), w.TOP_REJECTS + 2)
        self.assertIn("ONON", out); self.assertIn("JPM", out)
        self.assertEqual(len(w.top_rejects(rej, "HK")), w.TOP_REJECTS)


if __name__ == "__main__":
    unittest.main()
