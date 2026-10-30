"""Offline tests for crypto exit variant C3 + the V0 shadow (2026-10-02). No network, temp ledgers, no live tickets."""
import contextlib, datetime as dt, json, os, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tfo_trail_rail as trl
import tfo_heat
import crypto_exit_ab as ab
from test_tfo_trail_rail import CryptoEngine, UTC

T0 = dt.datetime(2026, 10, 5, 3, 0, tzinfo=UTC)


def iso(t): return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class C3Rail(unittest.TestCase):
    def test_default_and_v7_unchanged(self):
        self.assertEqual(trl.decide(100, 102.0, "long", False, crypto=True)["rule"], "HALF_2PCT")
        self.assertEqual(trl.decide(100, 95.0, "long", False, crypto=True)["rule"], "STOP_ORIG")
        self.assertEqual(trl.decide(100, 104, "long", True, wm=104, held_age=31, crypto=True)["rule"], "MAX_HOLD")
        self.assertEqual(trl.decide(100, 110.0, "long", False, variant="V7", held_sessions=1)["rule"], "HALF_10PCT")
        self.assertEqual(trl.decide(100, 103, "long", False, variant="V7", held_sessions=40)["rule"], "MAX_HOLD_40S")
        self.assertEqual(trl.rule_text(None), trl.RULE_TEXT); self.assertEqual(trl.rule_text("V7"), trl.V7_RULE_TEXT)

    def test_c3_stop_dist(self):
        self.assertAlmostEqual(trl.c3_stop_dist(0.06), 0.12)
        for bad in (None, 0, -0.1, float("nan"), "x"): self.assertIsNone(trl.c3_stop_dist(bad))

    def test_c3_stop_at_own_atr_distance(self):
        self.assertIsNone(trl.decide(100, 90.0, "long", False, held_age=1, crypto=True, variant="C3", stop_dist=0.12)["act"])  # V0 would stop
        d = trl.decide(100, 88.0, "long", False, held_age=1, crypto=True, variant="C3", stop_dist=0.12)
        self.assertEqual((d["act"], d["rule"]), ("STOP", "STOP_ORIG")); self.assertAlmostEqual(d["level"], 88.0)

    def test_c3_missing_stop_falls_back_to_5pct(self):
        self.assertEqual(trl.decide(100, 95.0, "long", False, held_age=1, crypto=True, variant="C3", stop_dist=None)["act"], "STOP")

    def test_c3_half_at_15_not_2(self):
        self.assertIsNone(trl.decide(100, 102.0, "long", False, held_age=1, crypto=True, variant="C3", stop_dist=0.1)["act"])
        self.assertIsNone(trl.decide(100, 114.9, "long", False, held_age=1, crypto=True, variant="C3", stop_dist=0.1)["act"])
        d = trl.decide(100, 115.0, "long", False, held_age=1, crypto=True, variant="C3", stop_dist=0.1)
        self.assertEqual((d["act"], d["rule"]), ("HALF", "HALF_15PCT"))

    def test_c3_trail_be_floor_after_half(self):
        d = trl.decide(100, 112.0, "long", True, wm=120.0, atr_pct=0.03, held_age=5, crypto=True, variant="C3", stop_dist=0.1)  # 9% trail -> 109.2
        self.assertIsNone(d["act"]); self.assertAlmostEqual(d["level"], 109.2)
        d = trl.decide(100, 109.0, "long", True, wm=120.0, atr_pct=0.03, held_age=5, crypto=True, variant="C3", stop_dist=0.1)
        self.assertEqual(d["rule"], "TRAIL_3ATR")
        d = trl.decide(100, 99.9, "long", True, wm=103.0, atr_pct=0.05, held_age=5, crypto=True, variant="C3", stop_dist=0.1)
        self.assertEqual(d["rule"], "BE_FLOOR")

    def test_c3_max_hold_40_days_whole_position(self):
        self.assertIsNone(trl.decide(100, 103, "long", False, held_age=39.9, crypto=True, variant="C3", stop_dist=0.1)["act"])
        d = trl.decide(100, 103, "long", False, held_age=40, crypto=True, variant="C3", stop_dist=0.1)
        self.assertEqual((d["act"], d["rule"]), ("FLAT", "MAX_HOLD_40D"))
        self.assertIsNone(trl.decide(100, 125, "long", True, wm=125, atr_pct=0.02, held_age=35, crypto=True, variant="C3", stop_dist=0.1)["act"])  # V0 would MAX_HOLD at 30d
        self.assertEqual(trl.decide(100, 125, "long", True, wm=125, atr_pct=0.02, held_age=40, crypto=True, variant="C3", stop_dist=0.1)["rule"], "MAX_HOLD_40D")


class C3Heat(unittest.TestCase):
    def test_pre_half_heat_uses_own_stop(self):
        self.assertAlmostEqual(tfo_heat.crypto_position_heat({"qty": 1000, "entry_px": 100}), 5000)
        self.assertAlmostEqual(tfo_heat.crypto_position_heat({"qty": 1000, "entry_px": 100, "exit_variant": "C3", "c3_stop_dist": 0.12}), 12000)
        self.assertAlmostEqual(tfo_heat.crypto_position_heat({"qty": 1000, "entry_px": 100, "exit_variant": "C3"}), 5000)


class C3Engine(CryptoEngine):
    def _old(self, d): return iso(dt.datetime.now(UTC) - dt.timedelta(days=d))

    def test_untagged_clip_unchanged_and_tagged_uses_c3(self):
        old = self._old(2)
        self.book(**{"HBAR/USD": {"qty": 1000.0, "entry_px": 10.0, "orig_entry": 10.0, "entry_utc": old},
                     "ETH/USD": {"qty": 10.0, "entry_px": 100.0, "orig_entry": 100.0, "entry_utc": old, "exit_variant": "C3", "c3_stop_dist": 0.12}})
        self.bid.update({"HBAR/USD": 10.25, "ETH/USD": 102.5})
        self.rails()
        b = self.r("tfo_ht_crypto")
        self.assertAlmostEqual(b["positions"]["HBAR/USD"]["qty"], 500.0)   # V0 +2% half
        self.assertAlmostEqual(b["positions"]["ETH/USD"]["qty"], 10.0)     # C3 holds at +2.5%
        self.bid.update({"ETH/USD": 91.0}); self.rails()                     # -9%: V0 would stop, C3 (-12%) holds
        self.assertIn("ETH/USD", self.r("tfo_ht_crypto")["positions"])
        self.bid.update({"ETH/USD": 87.9}); self.rails()
        b = self.r("tfo_ht_crypto"); self.assertNotIn("ETH/USD", b["positions"])
        tk = b["head_trader_tickets"][-1]; self.assertEqual((tk["exit_rule"], tk["exit_variant"]), ("STOP_ORIG", "C3"))

    def test_c3_half_records_c3_rule_text(self):
        self.book(**{"SOL/USD": {"qty": 10.0, "entry_px": 100.0, "orig_entry": 100.0, "entry_utc": self._old(2), "exit_variant": "C3", "c3_stop_dist": 0.1}})
        self.bid["SOL/USD"] = 115.5; self.rails()
        p = self.r("tfo_ht_crypto")["positions"]["SOL/USD"]
        self.assertAlmostEqual(p["qty"], 5.0); self.assertEqual(p["trail"]["rule"], trl.C3_RULE_TEXT)
        self.assertEqual(self.r("tfo_ht_crypto")["head_trader_tickets"][-1]["exit_rule"], "HALF_15PCT")

    def test_ab_hook_not_called_off_live_desk(self):
        self.book(**{"SOL/USD": {"qty": 10.0, "entry_px": 100.0, "orig_entry": 100.0, "entry_utc": self._old(2), "exit_variant": "C3", "c3_stop_dist": 0.1}})
        self.bid["SOL/USD"] = 101.0; self.rails()
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "crypto_exit_ab_state.json")))

    def _gates(self, usd, atr, eq=1.8e6):
        tc = self.tc
        q = {"pair": "ETH/USD", "bid": 100.0, "ask": 100.01, "last": 100.0, "mid": 100.005, "spread_bp": 1.0, "dollar_vol24": 5e9,
             "open_utc_day": 100.0, "trade_age_sec": 1, "tape": "LIVE", "vol24": 1, "utc": "x"}
        import tfo_buy
        with mock.patch.object(tc, "quote", lambda p: q), mock.patch.object(tc, "avg10_vol", lambda p: 1), \
             mock.patch.object(tc, "clip_heat", lambda: (0.0, 0.0)), mock.patch.object(tfo_buy, "firm_state", lambda: (eq, -1.0, eq)), \
             mock.patch.object(tc.trl, "kraken_atr_pct", lambda g, k: atr), \
             mock.patch.object(tc, "load", lambda k, _o=tc.load: {"cash": 1e7, "positions": {}} if k == tc.GCL else _o(k)):
            return tc.gates("ETH/USD", usd, "crypto-major")

    def test_gates_c3_risk_and_fail_closed(self):
        self.book()
        fails, q, info = self._gates(150000, 0.05)   # stop 10% -> risk 15k <= 18k
        self.assertEqual(info["c3_stop_dist"], 0.1); self.assertAlmostEqual(info["c3_max_clip_usd"], 180000)
        self.assertFalse([f for f in fails if "C3" in f or "heat" in f], fails)
        fails, _, _ = self._gates(250000, 0.05)      # risk 25k > 18k
        self.assertTrue(any("C3 risk" in f for f in fails), fails)
        fails, _, _ = self._gates(100000, None)
        self.assertTrue(any("fail closed" in f for f in fails), fails)

    def test_buy_tags_c3(self):
        self.book(); self.w("tfo_gcl_powder", {"cash": 1e7, "positions": {}})
        tc = self.tc
        fails, q, info = self._gates(100000, 0.05)
        with mock.patch.object(tc, "gates", lambda *a: ([], q, info)), mock.patch.object(tc, "xfer", lambda *a, **k: None), \
             contextlib.redirect_stdout(open(os.devnull, "w")):
            tc.cmd_buy("ETH/USD", 100000, "crypto-major", "edge", "test", True)
        p = self.r("tfo_ht_crypto")["positions"]["ETH/USD"]
        self.assertEqual((p["exit_variant"], p["c3_stop_dist"], p["c3_atr14_entry"]), ("C3", 0.1, 0.05))
        self.assertEqual(p["entry_ticket"]["exit_variant"], "C3")


class Shadow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.book = {"positions": {"ETH/USD": {"qty": 10.0, "entry_px": 100.0, "orig_entry": 100.0, "entry_utc": iso(T0), "exit_variant": "C3",
                                               "c3_stop_dist": 0.12, "entry_ticket": {"principal_usd": 1000.0}, "entry_fees_usd": 2.6}},
                     "closed": []}

    def step(self, px, mins, quote=None):
        rows = [{"pair": "ETH/USD", "px": px, "tape": "LIVE", "last_mark": 100.0}] if px is not None and "ETH/USD" in self.book["positions"] else []
        with contextlib.redirect_stdout(open(os.devnull, "w")):
            return ab.update(rows, self.book, now=T0 + dt.timedelta(minutes=mins), reports=self.tmp,
                             quote_fn=quote or (lambda p, l: None), atr_fn=lambda p: 0.01)

    def test_untagged_ignored(self):
        self.book["positions"]["ETH/USD"].pop("exit_variant")
        st = self.step(100.0, 10); self.assertEqual(st["trades"], {})

    def test_shadow_v0_stops_at_minus5_while_c3_holds_then_completes(self):
        self.step(100.0, 1)
        st = self.step(94.9, 10)
        t = list(st["trades"].values())[0]
        self.assertTrue(t["shadow"]["closed"]); self.assertEqual(t["shadow"]["exit_rule"], "STOP_ORIG")
        self.assertFalse(t["actual"]["closed"])
        p = self.book["positions"].pop("ETH/USD")   # C3 later exits at the 12% stop
        p["exit_fills"] = [{"pnl_usd": -120.0, "fees_usd": 2.3, "exit_rule": "STOP_ORIG", "utc": iso(T0), "px_usd": 88.0}]
        self.book["closed"].append({"pair": "ETH/USD", "closed_utc": iso(T0), "reason": "Head Trader STOP", "position": p})
        st = self.step(None, 600)
        t = list(st["trades"].values())[0]
        self.assertTrue(t["complete"]); self.assertAlmostEqual(t["actual"]["gross_ret"], -0.12)
        last = json.loads(open(os.path.join(self.tmp, "crypto_exit_ab.jsonl")).read().splitlines()[-1])
        self.assertEqual(last["event"], "trade_complete"); self.assertAlmostEqual(last["c3_R"], -1.0); self.assertAlmostEqual(last["v0_R"], -1.02)
        sc = json.loads(open(os.path.join(self.tmp, "shark_scorecard.jsonl")).read().splitlines()[-1])
        self.assertEqual(sc["source"], "crypto_exit_ab")

    def test_guard_and_half(self):
        self.step(102.5, 2)      # inside the 5-min guard from entry -> no shadow half
        t = list(self.step(102.5, 2)["trades"].values())[0]; self.assertFalse(t["shadow"]["half_banked"])
        t = list(self.step(102.5, 6)["trades"].values())[0]; self.assertTrue(t["shadow"]["half_banked"])

    def test_writes_only_own_files(self):
        self.step(100.0, 10)
        self.assertEqual(sorted(os.listdir(self.tmp)), ["crypto_exit_ab.jsonl", "crypto_exit_ab_state.json", "crypto_exit_ab_summary.json"])


if __name__ == "__main__":
    unittest.main()
