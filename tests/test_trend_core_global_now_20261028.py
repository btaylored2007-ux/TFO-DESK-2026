"""TREND-CORE GLOBAL 'trade straight away' amendment (pt1580u, 28 Oct 2026). PAPER only, no network."""
import datetime as dt, json, os, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import trend_core_global as g, trend_core as tc

def ledger():
    return dict(cash_aud=50000.0, costs_paid_aud=0.0, holdings={"XBT/USD": 1.0, "DBMF": 1000.0}, mark_px_usd={}, entry_px_usd={}, fills=[])

class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.plan = os.path.join(self.d, "p.json")
        json.dump(dict(executed=False, due_date="2026-10-28", targets=[]), open(self.plan, "w"))
        self.p = [mock.patch.object(g, "PLAN", self.plan), mock.patch.object(g, "REBOOK_DONE", os.path.join(self.d, "done.json"))]
        for x in self.p: x.start()
    def tearDown(self):
        for x in self.p: x.stop()
    def test_due_tonight_and_card(self):
        self.assertEqual(g.DUE, "2026-10-28"); self.assertEqual(g.CARD, "Executes 28 Oct US open")
    def test_waits_for_rebook(self):  # 28 Oct 10:00 ET in session, rebook not done -> no-op
        with mock.patch.object(g, "plan") as pl, mock.patch.object(tc.trend18, "is_session", return_value=True):
            g.rebalance_if_due(now_utc=dt.datetime(2026, 10, 28, 14, 0, tzinfo=dt.timezone.utc)); pl.assert_not_called()
    def test_not_before_0945_et(self):  # 21:40 WITA = 09:40 ET
        open(g.REBOOK_DONE, "w").write("{}")
        with mock.patch.object(g, "plan") as pl, mock.patch.object(tc.trend18, "is_session", return_value=True):
            g.rebalance_if_due(now_utc=dt.datetime(2026, 10, 28, 13, 40, tzinfo=dt.timezone.utc)); pl.assert_not_called()
    def test_stale_crypto_refused(self):
        import tfo_crypto
        with mock.patch.object(tfo_crypto, "quote", return_value=dict(tape="STALE", trade_age_sec=3000, last=1, bid=1, ask=1.01, mid=1, utc="x", dollar_vol24=1e9, spread_bp=5)):
            with self.assertRaises(RuntimeError): g.crypto_quotes(["ETH/USD"])
    def test_book_sells_first_keeps_spare(self):
        L = ledger(); fx = 0.7
        tg = [dict(inst="XBT/USD", group="core", target_aud=20000), dict(inst="ETH/USD", group="spec", target_aud=500000)]
        px = {"XBT/USD": dict(px=70000, utc="u", src="s"), "ETH/USD": dict(px=3000, utc="u", src="s")}
        o = g._orders(L, tg, px, fx); self.assertEqual(o[0][0], "XBT/USD")
        with mock.patch.object(tc, "_append_fill"): g._book(L, o, px, fx, "t")
        self.assertGreaterEqual(L["cash_aud"], tc.SPARE_AUD - 1)
    def test_fill_csv_fields(self):
        L = ledger(); L["cash_aud"] = 100000.0   # above the A$50k spare so the buy is not clipped to 0
        px = {"ETH/USD": dict(px=3000, utc="u", src="s")}
        # patch the trend_core module g actually uses (another suite test may re-import trend_core)
        out = os.path.join(self.d, "f.csv")
        with mock.patch.object(g.tc, "FILLS", out):
            g._book(L, [("ETH/USD", 1.0, 3000, "crypto", "spec")], px, 0.7, "t")
        import csv
        rows = list(csv.DictReader(open(out)))
        self.assertEqual(len(rows), 1); self.assertEqual(rows[0]["inst"], "ETH/USD")
        self.assertNotIn("px_src", rows[0]); self.assertIn("px_src", L["fills"][0])
    def test_mf_zero_and_budgets(self):
        p = json.load(open(os.path.join(os.path.dirname(g.STATUS), "trend_core_global_status.json")))
        self.assertTrue(all(t["w"] == 0 for t in p["targets"] if t["group"] == "mf"))
        self.assertLessEqual(sum(t["w"] for t in p["targets"] if t["group"] == "spec"), g.SPEC_BUDGET + 1e-4)  # live status weights; 1e-4 covers float sum noise
    def test_kill_rule_unchanged(self):
        self.assertEqual(tc.KILL_DD, -0.30); self.assertEqual(tc.SPARE_AUD, 50000.0); self.assertEqual(g.SIGMA, 0.20)
if __name__ == "__main__": unittest.main()
