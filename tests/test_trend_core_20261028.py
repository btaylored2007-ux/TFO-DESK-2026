"""TREND-CORE A$1m paper book — registration + accounting path B checks. PAPER ONLY."""
import json, os, sys, unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

class TestTrendCore(unittest.TestCase):
    def test_module_imports(self):
        import trend_core as tc
        self.assertEqual(tc.NAV0, 1_000_000.0)
        self.assertEqual(tc.SPARE_AUD, 50_000.0)
        self.assertTrue(tc.SPARE_AUD + tc.NAV0 - tc.SPARE_AUD == tc.NAV0)

    def test_ledger_exists_and_spare(self):
        import trend_core as tc
        L = tc.load()
        self.assertTrue(L.get("paper_only"))
        self.assertFalse(L.get("in_firm_equity"))
        self.assertEqual(L.get("accounting_path"), "B")
        self.assertEqual(L.get("ccy"), "AUD")
        self.assertGreaterEqual(float(L.get("cash_aud") or 0), tc.SPARE_AUD - 0.01)
        self.assertLessEqual(float(L.get("cash_aud") or 0) - tc.SPARE_AUD + 1e-6,
                             float(L.get("equity_aud") or 0))  # free cash is non-negative path
        # spare never breached
        self.assertGreaterEqual(float(L["cash_aud"]), tc.SPARE_AUD - 1.0)
        # deployed under cap
        st = tc.write_status(L)
        self.assertLessEqual(st["deployed_aud"] + 1.0, tc.NAV0 - tc.SPARE_AUD + 5000)  # small slack for FX
        self.assertAlmostEqual(st["spare_aud"], 50_000.0)
        # NAV near 1m after costs
        self.assertGreater(st["nav_aud"], 990_000)
        self.assertLess(st["nav_aud"], 1_001_000)

    def test_status_json(self):
        p = os.path.join(BASE, "static", "trend_core_status.json")
        self.assertTrue(os.path.exists(p), p)
        s = json.load(open(p))
        for k in ("nav_aud", "spare_aud", "deployed_aud", "holdings", "next_rebalance",
                  "accounting_path", "in_firm_equity", "paper_only"):
            self.assertIn(k, s)
        self.assertEqual(s["accounting_path"], "B")
        self.assertFalse(s["in_firm_equity"])
        self.assertTrue(s["paper_only"])

    def test_not_in_book_coverage(self):
        import book_coverage as bc
        names = []
        for b in bc.registry():
            # registry returns tuples or dicts depending on version
            if isinstance(b, (list, tuple)):
                names.append(b[0])
            elif isinstance(b, dict):
                names.append(b.get("key") or b.get("name") or b.get("id"))
            else:
                names.append(str(b))
        self.assertNotIn("trend_core", names)
        self.assertNotIn("TREND-CORE", names)

    def test_enter_is_noop(self):
        # A code-only tree has no entered book. load() would create an empty one and enter()
        # would try to mark BTC. This locks the already-entered path on a temp ledger.
        import tempfile, trend_core as tc
        d = tempfile.mkdtemp(); path = os.path.join(d, "trend_core.json"); status = os.path.join(d, "status.json")
        L = tc.new_ledger(); L["scale_current"] = 1.0; L["equity_aud"] = 1_000_000.0
        L["holdings"]["UPRO"] = 10.0
        tc.atomic_json(path, L)
        old_l, old_s = tc.LEDGER, tc.STATUS
        tc.LEDGER, tc.STATUS = path, status
        try:
            L1 = tc.load(); eq1 = L1["equity_aud"]
            L2, fills = tc.enter(dry_run=False)
        finally:
            tc.LEDGER, tc.STATUS = old_l, old_s
        self.assertEqual(fills, [])
        self.assertAlmostEqual(float(L2["equity_aud"]), float(eq1), places=2)

    def test_dashboard_card_present(self):
        html = open(os.path.join(BASE, "command_dashboard.html")).read()
        self.assertIn('id="trendcore"', html)
        self.assertIn("/static/trend_core_status.json", html)

    def test_tracker_row(self):
        import strategy_tracker as st
        rows = st.rows()
        names = [r["strategy"] for r in rows]
        self.assertIn("TREND-CORE A$1m", names)

    def test_warroom_block(self):
        import warroom_markets as wm
        b = wm.trend_core_block(wm.now_utc() if hasattr(wm, "now_utc") else __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
        self.assertIsNotNone(b.get("equity_aud"))
        self.assertFalse(b.get("in_firm_equity"))

if __name__ == "__main__":
    unittest.main()
