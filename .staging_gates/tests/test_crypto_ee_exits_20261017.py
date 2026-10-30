"""2026-10-17 (Brett, weekly cut review): entry-engine crypto clips (theme entry_engine_crypto) exit +2% half / flat by +5%
(replaces the C3 +15% half), 1.5% minimum stop; maker entries / planned exits kept; stops stay taker. Offline, temp ledgers."""
import datetime as dt, os, sys, time, unittest
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tfo_trail_rail as trl
from test_tfo_trail_rail import CryptoEngine, UTC


class Rule(unittest.TestCase):
    def d(self, px, banked=False, sd=0.05, wm=None):
        return trl.decide(10.0, px, "long", banked, wm, None, 1, crypto=True, variant=trl.C3, stop_dist=sd, profile=trl.EE)

    def test_half_at_2pct_not_15(self):
        self.assertIsNone(self.d(10.19)["act"])
        r = self.d(10.2); self.assertEqual((r["act"], r["rule"]), ("HALF", "HALF_2PCT"))

    def test_flat_by_5pct_after_half(self):
        r = self.d(10.45, banked=True, wm=10.45); self.assertIsNone(r["act"])
        r = self.d(10.5, banked=True, wm=10.5); self.assertEqual((r["act"], r["rule"]), ("FLAT", "FLAT_5PCT"))

    def test_min_stop_1_5pct(self):
        r = self.d(9.86, sd=0.008); self.assertIsNone(r["act"]); self.assertAlmostEqual(r["level"], 9.85)   # 0.8% ATR stop -> 1.5%
        self.assertEqual(self.d(9.85, sd=0.008)["act"], "STOP")
        self.assertEqual(self.d(9.69, sd=0.03)["act"], "STOP")                                              # wider ATR stop kept
        self.assertIsNone(self.d(9.71, sd=0.03)["act"])

    def test_trail_and_be_still_apply_between(self):
        r = self.d(10.0, banked=True, wm=10.3); self.assertEqual((r["act"], r["rule"]), ("FLAT", "BE_FLOOR"))

    def test_plain_c3_unchanged(self):
        r = trl.decide(10.0, 10.2, "long", False, None, None, 1, crypto=True, variant=trl.C3, stop_dist=0.05)
        self.assertIsNone(r["act"])


class Engine(CryptoEngine):
    def setUp(self):
        super().setUp(); self.prints = []
        p = patch.object(self.tc, "trades_since", lambda pair, since: [x for x in self.prints if x[2] > since]); p.start(); self.addCleanup(p.stop)

    def held(self, theme="entry_engine_crypto", **kw):
        old = (dt.datetime.now(UTC) - dt.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pos = {"qty": 1000.0, "entry_px": 10.0, "orig_entry": 10.0, "entry_utc": old, "last_ticket_utc": old, "theme": theme,
               "exit_variant": "C3", "c3_stop_dist": 0.008, "maker_exits": True, **kw}
        b = self.tc.new_book(); b["positions"] = {"ENA/USD": pos}; b["mark_px_usd"] = {"ENA/USD": 10.0}; self.w("tfo_ht_crypto", b)

    def test_half_at_2pct_rests_post_only_then_fills_maker(self):
        self.held(); self.bid["ENA/USD"] = 10.2; self.rails()
        p = self.r("tfo_ht_crypto")["positions"]["ENA/USD"]; self.assertEqual(p["resting_exit"]["act"], "HALF"); self.assertEqual(p["resting_exit"]["rule"], "HALF_2PCT")
        lim = p["resting_exit"]["limit_px"]; self.prints = [(lim + 0.01, 1.0, time.time() + 1)]; self.rails()
        b = self.r("tfo_ht_crypto"); tk = b["head_trader_tickets"][-1]
        self.assertAlmostEqual(b["positions"]["ENA/USD"]["qty"], 500.0); self.assertEqual(tk["liquidity"], "maker"); self.assertEqual(tk["exit_rule"], "HALF_2PCT")

    def test_flat_at_5pct_is_planned_maker_exit(self):
        self.held(rebase=10.2, qty=500.0, trail={"half_banked": True, "hwm_usd": 10.5})
        self.bid["ENA/USD"] = 10.5; self.rails()
        p = self.r("tfo_ht_crypto")["positions"]["ENA/USD"]; self.assertEqual(p["resting_exit"]["act"], "FLAT"); self.assertEqual(p["resting_exit"]["rule"], "FLAT_5PCT")
        self.assertAlmostEqual(p["qty"], 500.0)
        lim = p["resting_exit"]["limit_px"]; self.prints = [(lim + 0.01, 1.0, time.time() + 1)]; self.rails()
        b = self.r("tfo_ht_crypto"); tk = b["head_trader_tickets"][-1]
        self.assertNotIn("ENA/USD", b["positions"]); self.assertEqual(tk["liquidity"], "maker"); self.assertEqual(tk["action"], "FLAT"); self.assertAlmostEqual(tk["qty"], 500.0)

    def test_stop_floor_1_5pct_and_stays_taker(self):
        self.held(); self.bid["ENA/USD"] = 9.88; self.rails()                      # -1.2%: inside the 1.5% floor (0.8% ATR stop ignored)
        self.assertAlmostEqual(self.r("tfo_ht_crypto")["positions"]["ENA/USD"]["qty"], 1000.0)
        self.bid["ENA/USD"] = 9.84; self.rails()
        b = self.r("tfo_ht_crypto"); tk = b["head_trader_tickets"][-1]
        self.assertNotIn("ENA/USD", b["positions"]); self.assertEqual(tk["liquidity"], "taker"); self.assertEqual(tk["exit_rule"], "STOP_ORIG")

    def test_other_theme_keeps_c3_15pct(self):
        self.held(theme="overnight2k", c3_stop_dist=0.05); self.bid["ENA/USD"] = 10.3; self.rails()
        p = self.r("tfo_ht_crypto")["positions"]["ENA/USD"]; self.assertAlmostEqual(p["qty"], 1000.0); self.assertNotIn("resting_exit", p)

    def _expired(self, act, rule, bid, **kw):
        now = dt.datetime.now(UTC); f = "%Y-%m-%dT%H:%M:%SZ"
        self.held(resting_exit={"placed_utc": (now - dt.timedelta(minutes=11)).strftime(f), "expires_utc": (now - dt.timedelta(minutes=1)).strftime(f),
                                "limit_px": bid * 1.0001, "rule": rule, "act": act, "post_only": True}, **kw)
        self.bid["ENA/USD"] = bid; self.prints = []

    def test_expired_half_reposts_next_run_no_taker(self):
        self._expired("HALF", "HALF_2PCT", 10.2); self.rails()
        b = self.r("tfo_ht_crypto"); p = b["positions"]["ENA/USD"]
        self.assertAlmostEqual(p["qty"], 1000.0); self.assertNotIn("resting_exit", p); self.assertEqual(b["head_trader_tickets"], [])
        self.rails()                                                    # next run: re-posted post-only
        p = self.r("tfo_ht_crypto")["positions"]["ENA/USD"]; self.assertEqual(p["resting_exit"]["rule"], "HALF_2PCT"); self.assertAlmostEqual(p["qty"], 1000.0)

    def test_expired_flat_reposts_next_run_no_taker(self):
        self._expired("FLAT", "FLAT_5PCT", 10.5, rebase=10.2, qty=500.0, trail={"half_banked": True, "hwm_usd": 10.5}); self.rails()
        b = self.r("tfo_ht_crypto"); self.assertAlmostEqual(b["positions"]["ENA/USD"]["qty"], 500.0); self.assertEqual(b["head_trader_tickets"], [])
        self.rails(); self.assertEqual(self.r("tfo_ht_crypto")["positions"]["ENA/USD"]["resting_exit"]["rule"], "FLAT_5PCT")

    def test_stop_with_expired_exit_still_sells_at_market(self):
        self._expired("HALF", "HALF_2PCT", 9.84); self.rails()
        b = self.r("tfo_ht_crypto"); tk = b["head_trader_tickets"][-1]
        self.assertNotIn("ENA/USD", b["positions"]); self.assertEqual(tk["liquidity"], "taker"); self.assertEqual(tk["exit_rule"], "STOP_ORIG")

    def test_be_floor_with_resting_exit_sells_at_market(self):
        self._expired("FLAT", "FLAT_5PCT", 10.0, rebase=10.2, qty=500.0, trail={"half_banked": True, "hwm_usd": 10.3}); self.rails()
        b = self.r("tfo_ht_crypto"); tk = b["head_trader_tickets"][-1]
        self.assertNotIn("ENA/USD", b["positions"]); self.assertEqual(tk["liquidity"], "taker"); self.assertEqual(tk["exit_rule"], "BE_FLOOR")

    def test_sizing_stop_floor(self):
        import risk_caps
        seen = {}
        def fake(usd, sd, **kw): seen["sd"] = sd; return {"usd": usd}
        with patch.object(risk_caps, "size_entry", fake), patch.object(risk_caps, "theme_params", lambda t: {}):
            self.tc.risk_size("ENA/USD", 100000, {"c3_stop_dist": 0.008, "c3_atr14_entry": 0.004}, presized=True, theme="entry_engine_crypto")
            self.assertAlmostEqual(seen["sd"], 0.015)
            self.tc.risk_size("ENA/USD", 100000, {"c3_stop_dist": 0.008, "c3_atr14_entry": 0.004}, presized=True, theme="overnight2k")
            self.assertAlmostEqual(seen["sd"], 0.008)


if __name__ == "__main__":
    unittest.main()
