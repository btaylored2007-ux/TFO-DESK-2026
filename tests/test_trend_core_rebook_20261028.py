"""TREND-CORE one-shot rebook: session guard, cash/spare unchanged, BTC untouched. PAPER, no writes."""
import os, sys, copy, unittest, datetime as dt
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import trend_core as tc, trend_core_rebook as rb
UTC = dt.timezone.utc


class T(unittest.TestCase):
    def test_guard(self):
        self.assertTrue(rb.session_open(dt.datetime(2026, 10, 28, 13, 35, tzinfo=UTC))[0])   # 09:35 ET
        self.assertFalse(rb.session_open(dt.datetime(2026, 10, 28, 13, 25, tzinfo=UTC))[0])  # 09:25 ET
        self.assertFalse(rb.session_open(dt.datetime(2026, 10, 27, 14, 0, tzinfo=UTC))[0])   # before earliest
        self.assertFalse(rb.session_open(dt.datetime(2026, 10, 31, 14, 0, tzinfo=UTC))[0])   # Saturday

    def test_apply_keeps_cash_and_btc(self):
        L = copy.deepcopy(tc.load())
        if L.get("entry_rebooked_utc"):
            self.skipTest("already rebooked")
        syms = [f["inst"] for f in L["fills"] if f["reason"] == "entry" and f["inst"] != "XBT/USD"]
        prices = {s: {"px": float(L["entry_px_usd"][s]) * 1.01, "bar_utc": "2026-10-28T13:35:00Z"} for s in syms}
        rows = rb.plan(L, prices)
        self.assertEqual(sorted(r["inst"] for r in rows), sorted(syms))
        btc0 = L["holdings"]["XBT/USD"]; cash0 = L["cash_aud"]
        orig = tc._append_fill; tc._append_fill = lambda f: None
        try:
            before, after = rb.apply(L, rows, "2026-10-28T13:35:00Z")
        finally:
            tc._append_fill = orig
        self.assertEqual(L["holdings"]["XBT/USD"], btc0)
        self.assertAlmostEqual(L["cash_aud"], cash0, places=2)
        self.assertGreaterEqual(L["cash_aud"], tc.SPARE_AUD - 1)
        self.assertAlmostEqual(after["equity_aud"], before["equity_aud"], delta=50)  # same A$ notional at entry
        for r in rows:
            self.assertLess(r["new_shares"], r["old_shares"])  # 1% higher price -> fewer shares

    def test_plist_present(self):
        p = os.path.expanduser("~/Library/LaunchAgents/com.tfo.trend-core-rebook.plist")
        self.assertTrue(os.path.exists(p))


if __name__ == "__main__":
    unittest.main()
