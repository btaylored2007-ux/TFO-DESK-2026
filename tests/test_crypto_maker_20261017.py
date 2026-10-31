"""2026-10-17 entry-engine crypto post-only maker entries + planned +half exit; stops stay taker. Offline, temp ledgers."""
import contextlib, datetime as dt, io, json, os, sys, time, unittest
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_tfo_trail_rail import CryptoEngine, UTC


class Maker(CryptoEngine):
    def setUp(self):
        super().setUp()
        # Archive lock stays on for the live book (test_tfo_ht_crypto_archive_lock). This class
        # covers maker fill, expiry and stop-cancel on the temp book only.
        p0 = patch.object(self.tc, "OPENS_DISABLED", False); p0.start(); self.addCleanup(p0.stop)
        self.prints = []
        p = patch.object(self.tc, "trades_since", lambda pair, since: [x for x in self.prints if x[2] > since]); p.start(); self.addCleanup(p.stop)
        self.book()

    def post(self, pair="TON/USD", usd=100000.0, bid=5.0):
        self.bid[pair] = bid
        q = self.tc.quote(pair); out = io.StringIO()
        with contextlib.redirect_stdout(out): self.tc._post_entry(pair, usd, "entry_engine_crypto", "beta", "test", q, {"c3_stop_dist": 0.05})
        return out.getvalue()

    def test_entry_rests_at_bid_and_reserves_powder(self):
        out = self.post(); b = self.r("tfo_ht_crypto"); g = self.r("gap_close_levered_book")
        r = b["resting_entries"]["TON/USD"]
        self.assertIn("RESTING ", out); self.assertIn(" post-only BUY ", out); self.assertNotIn("LOG ", out)
        self.assertEqual(r["limit_px"], 5.0); self.assertEqual(b["positions"], {})
        self.assertAlmostEqual(g["cash"], -r["reserved_usd"]); self.assertAlmostEqual(r["reserved_usd"], 100000 * 1.0025, places=2)

    def test_fill_only_on_print_through_at_limit_maker_fee(self):
        self.post(); t = time.time()
        self.prints = [(5.0, 1e6, t + 1)]; self.rails()                       # print AT the limit: no fill (queue unknown)
        self.assertIn("TON/USD", self.r("tfo_ht_crypto")["resting_entries"])
        self.prints.append((4.99, 1.0, t + 2)); self.rails()
        b = self.r("tfo_ht_crypto"); p = b["positions"]["TON/USD"]
        self.assertNotIn("TON/USD", b["resting_entries"]); self.assertEqual(p["entry_px"], 5.0); self.assertTrue(p["maker_exits"])
        self.assertEqual(p["entry_ticket"]["liquidity"], "maker"); self.assertAlmostEqual(p["entry_fees_usd"], 250.0, places=2)   # 0.25% vs 0.40% taker
        self.assertAlmostEqual(b["cash"], 0.0, places=2)

    def test_expiry_returns_powder_no_taker_fallback(self):
        self.post(); b = self.r("tfo_ht_crypto")
        b["resting_entries"]["TON/USD"]["expires_utc"] = (dt.datetime.now(UTC) - dt.timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        b["resting_entries"]["TON/USD"]["placed_utc"] = (dt.datetime.now(UTC) - dt.timedelta(seconds=901)).strftime("%Y-%m-%dT%H:%M:%SZ"); self.w("tfo_ht_crypto", b)
        self.rails(); b = self.r("tfo_ht_crypto"); g = self.r("gap_close_levered_book")
        self.assertEqual(b["positions"], {}); self.assertEqual(b["resting_entries"], {}); self.assertAlmostEqual(g["cash"], 0.0, places=2)
        self.assertEqual(b["resting_log"][-1]["outcome"], "expired")

    def test_stop_print_first_cancels(self):
        self.post(); t = time.time(); self.prints = [(4.70, 1.0, t + 1), (4.99, 1.0, t + 2)]
        self.rails(); b = self.r("tfo_ht_crypto")
        self.assertEqual(b["positions"], {}); self.assertEqual(b["resting_log"][-1]["outcome"], "stop_cancel")

    def _held(self, **kw):
        old = (dt.datetime.now(UTC) - dt.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pos = {"qty": 1000.0, "entry_px": 10.0, "orig_entry": 10.0, "entry_utc": old, "last_ticket_utc": old, "maker_exits": True, **kw}
        b = self.tc.new_book(); b["positions"] = {"LINK/USD": pos}; b["mark_px_usd"] = {"LINK/USD": 10.0}; self.w("tfo_ht_crypto", b)

    def test_planned_half_posts_then_fills_maker(self):
        self._held(); self.bid["LINK/USD"] = 10.2; self.rails()
        p = self.r("tfo_ht_crypto")["positions"]["LINK/USD"]
        self.assertAlmostEqual(p["qty"], 1000.0); self.assertAlmostEqual(p["resting_exit"]["limit_px"], 10.2 * 1.0001)
        lim = p["resting_exit"]["limit_px"]; self.prints = [(lim + 0.01, 1.0, time.time() + 1)]; self.rails()
        b = self.r("tfo_ht_crypto"); p = b["positions"]["LINK/USD"]; tk = b["head_trader_tickets"][-1]
        self.assertAlmostEqual(p["qty"], 500.0); self.assertNotIn("resting_exit", p)
        self.assertEqual(tk["liquidity"], "maker"); self.assertAlmostEqual(tk["px_usd"], lim); self.assertAlmostEqual(tk["fees_usd"], round(500 * lim * 0.0025, 2), places=2)

    def test_stop_stays_taker_and_cancels_resting_exit(self):
        self._held(resting_exit={"placed_utc": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "expires_utc": (dt.datetime.now(UTC) + dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"), "limit_px": 10.3})
        self.bid["LINK/USD"] = 9.4; self.rails()
        b = self.r("tfo_ht_crypto"); tk = b["head_trader_tickets"][-1]
        self.assertNotIn("LINK/USD", b["positions"]); self.assertEqual(tk["liquidity"], "taker"); self.assertAlmostEqual(tk["px_usd"], 9.4)

    def test_untagged_clip_half_stays_taker(self):
        self._held(maker_exits=False); self.bid["LINK/USD"] = 10.2; self.rails()
        b = self.r("tfo_ht_crypto"); self.assertAlmostEqual(b["positions"]["LINK/USD"]["qty"], 500.0); self.assertEqual(b["head_trader_tickets"][-1]["liquidity"], "taker")


if __name__ == "__main__":
    unittest.main()
