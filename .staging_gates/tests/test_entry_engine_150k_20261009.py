"""Offline tests: Brett (funder) 2026-10-09 21:10 AEDT -- entry-engine auto-fire clips target $150k notional.
Themes entry_engine_equity / entry_engine_crypto: clip = min(request, $150k, $7,500 / actual stop). Other themes unchanged.
No network, temp ledgers, no live tickets/logs.  Run:  cd tests && python3 -m unittest test_entry_engine_150k_20261009 -v"""
import contextlib, datetime as dt, io, json, os, sys, tempfile, unittest
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EE = os.path.join(ROOT, "research", "entry_engine")
sys.path.insert(0, ROOT); sys.path.insert(0, EE)
import risk_caps as rc
from test_tfo_trail_rail import CryptoEngine

QUIET = lambda: contextlib.redirect_stdout(io.StringIO())
EQ, CR = "entry_engine_equity", "entry_engine_crypto"


class RiskCapsEntryEngine(unittest.TestCase):
    def test_constants_and_themes(self):
        self.assertEqual((rc.ENTRY_ENGINE_TARGET_USD, rc.ENTRY_ENGINE_MAX_LOSS_USD), (150000.0, 7500.0))
        self.assertTrue(rc.is_entry_engine(EQ) and rc.is_entry_engine(CR))
        for t in (None, "", "crypto-major", "entry_engine", "ENTRY_ENGINE_EQUITY"):
            self.assertFalse(rc.is_entry_engine(t)); self.assertEqual(rc.theme_params(t), {})

    def test_equity_150k_at_minus5(self):
        d = rc.size_entry(150000, 0.05, **rc.theme_params(EQ))
        self.assertEqual(d["usd"], 150000.0); self.assertFalse(d["bound"])
        self.assertEqual(d["planned_loss_at_stop_usd"], 7500.0); self.assertEqual(d["planned_loss_usd"], 8250.0)   # slip reported
        self.assertEqual(d["rule"], rc.ENTRY_ENGINE_RULE_ID)
        d = rc.size_entry(300000, 0.05, **rc.theme_params(EQ))
        self.assertEqual(d["usd"], 150000.0); self.assertTrue(d["bound"])            # never $149,900 from float floor
        self.assertEqual(rc.planned_loss_cap_usd(0.05, **rc.theme_params(EQ)), 150000.0)

    def test_other_callers_unchanged(self):
        self.assertAlmostEqual(rc.planned_loss_cap_usd(0.05), 5000 / 0.055)
        d = rc.size_entry(300000, 0.05)
        self.assertEqual(d["usd"], 90900.0); self.assertEqual(d["rule"], rc.RULE_ID)
        self.assertNotIn("planned_loss_at_stop_usd", d); self.assertNotIn("target_usd", d)

    def test_crypto_wide_stop_sizes_down_capped_at_7500(self):
        d = rc.size_entry(150000, 0.06, atr_pct=0.03, apply_atr_rule=True, min_clip=5000, **rc.theme_params(CR))
        self.assertEqual(d["usd"], 125000.0); self.assertLessEqual(d["planned_loss_at_stop_usd"], 7500.0)
        d = rc.size_entry(150000, 0.04, atr_pct=0.02, apply_atr_rule=True, min_clip=5000, **rc.theme_params(CR))
        self.assertEqual(d["usd"], 150000.0); self.assertEqual(d["planned_loss_at_stop_usd"], 6000.0)   # risk that makes $150k
        d = rc.size_entry(150000, 0.20, atr_pct=0.10, apply_atr_rule=True, min_clip=5000, **rc.theme_params(CR))
        self.assertEqual(d["usd"], 37500.0)
        d = rc.size_entry(150000, 0.05, atr_pct=0.20, apply_atr_rule=True, min_clip=5000, **rc.theme_params(CR))   # ATR rule kept
        self.assertIn("atr_above_stop", d["binds"]); self.assertEqual(d["usd"], 37500.0)
        d = rc.size_entry(150000, 0.05, atr_pct=3.0, apply_atr_rule=True, min_clip=5000, **rc.theme_params(CR))
        self.assertTrue(d["skip"])                                                     # below $5k minimum still skips
        self.assertIn("entry engine", rc.describe(rc.size_entry(150000, 0.05, **rc.theme_params(CR))))


class TfoBuyEntryEngine(unittest.TestCase):
    def test_risk_size_by_theme(self):
        import tfo_buy
        self.assertEqual(tfo_buy.risk_size(300000, EQ)["usd"], 150000.0)
        self.assertEqual(tfo_buy.risk_size(150000, EQ)["usd"], 150000.0)
        self.assertEqual(tfo_buy.risk_size(300000)["usd"], 90900.0)
        self.assertEqual(tfo_buy.risk_size(300000, "t")["usd"], 90900.0)
        self.assertEqual(tfo_buy.risk_size(300000, CR)["usd"], 150000.0)

    def test_execute_guard_by_theme(self):
        import tfo_buy
        q = {"px_usd": 100.0, "tape": "LIVE"}
        with self.assertRaises(SystemExit): tfo_buy.execute("AAPL", 150000.0, "t", "beta", "", q)        # other themes: $90.9k
        with self.assertRaises(SystemExit): tfo_buy.execute("AAPL", 150100.0, EQ, "beta", "", q)         # engine: over $150k

    def _main(self, usd, theme):
        import tfo_buy
        seen = {}
        def gates(sym, u, th): seen["gates"] = u; return [], {"px_usd": 100.0, "tape": "LIVE"}, {}
        def execute(sym, u, th, label, why, q, risk_cap=None): seen["exec"] = (u, risk_cap["bound"]); return "LOG x"
        with mock.patch.object(tfo_buy, "gates", gates), mock.patch.object(tfo_buy, "execute", execute), \
             mock.patch.object(tfo_buy, "_cap_log_path", lambda: os.path.join(tempfile.mkdtemp(), "l.jsonl")), \
             mock.patch.object(sys, "argv", ["tfo_buy.py", "CAT", str(usd), "--theme", theme, "--execute"]), QUIET():
            tfo_buy.main()
        return seen

    def test_main_engine_theme_150k_other_90k(self):
        self.assertEqual(self._main(200000, EQ), {"gates": 150000.0, "exec": (150000.0, True)})
        self.assertEqual(self._main(150000, EQ), {"gates": 150000.0, "exec": (150000.0, False)})
        self.assertEqual(self._main(200000, "industrials"), {"gates": 90900.0, "exec": (90900.0, True)})

    def test_shark_max_clip_gate_still_present(self):
        import tfo_buy, inspect
        self.assertIn("shark_max_clip(eq)", inspect.getsource(tfo_buy.gates))
        self.assertIn("0.05 * dv_usd", inspect.getsource(tfo_buy.gates))
        self.assertIn("HEAT_CAP_PCT * eq", inspect.getsource(tfo_buy.gates))


class TfoCryptoEntryEngine(CryptoEngine):
    def _run(self, usd, info, theme=CR):
        tc = self.tc; calls = []
        q = {"pair": "ETH/USD", "bid": 100.0, "ask": 100.0, "last": 100.0, "mid": 100.0, "spread_bp": 1.0, "tape": "LIVE"}
        def gates(pair, u, th): calls.append(u); return [], q, dict(info)
        self.book(); self.w("tfo_gcl_powder", {"cash": 1e7, "positions": {}})
        with mock.patch.object(tc, "gates", gates), mock.patch.object(tc, "xfer", lambda *a, **k: None), QUIET():
            tc.cmd_buy("ETH/USD", usd, theme, "beta", "t", True)
        return calls

    def test_tight_c3_stop_150k(self):
        self.assertEqual(self._run(150000, {"c3_stop_dist": 0.04, "c3_atr14_entry": 0.02}), [150000])
        tk = list(self.r("tfo_ht_crypto")["resting_entries"].values())[-1]      # 2026-10-17: entry_engine_crypto rests post-only (maker)
        self.assertEqual(tk["usd"], 150000); self.assertEqual(tk["risk_cap"]["planned_loss_at_stop_usd"], 6000.0)

    def test_wide_c3_stop_sizes_down(self):
        self.assertEqual(self._run(150000, {"c3_stop_dist": 0.06, "c3_atr14_entry": 0.03}), [150000, 125000.0])
        self.assertEqual(list(self.r("tfo_ht_crypto")["resting_entries"].values())[-1]["usd"], 125000.0)   # 2026-10-17 post-only resting

    def test_other_theme_keeps_5k_cap(self):
        self.assertEqual(self._run(150000, {"c3_stop_dist": 0.04, "c3_atr14_entry": 0.02}, theme="crypto-major"), [150000, 111100.0])


class EngineTicketAndAutoFire(unittest.TestCase):
    def setUp(self):
        import entry_engine as ee
        self.ee = ee; self.cfg = ee.load_config()
        self.now = dt.datetime(2026, 10, 9, 10, 10, tzinfo=dt.timezone.utc)

    def a(self, stop_pct):
        return {"name": "TEST", "pair": "TESTUSD", "score": 80, "stop_pct": stop_pct, "trigger_usd": 100.0, "stop_usd": 100 * (1 - stop_pct / 100),
                "half_usd": 102.0, "flat_usd": 105.0, "live_px": 99.9}

    def test_constants_from_risk_caps(self):
        c = self.ee.desk_risk_constants(ROOT, self.cfg)
        self.assertEqual((c["planned_loss_usd"], c["target_usd"], c["slip_in_cap"]), (7500.0, 150000.0, False))

    def test_crypto_ticket_A_150k_B_half(self):
        c = self.ee.desk_risk_constants(ROOT, self.cfg)
        t = self.ee.make_ticket(self.a(1.2), True, 0.65, 1e9, c, self.cfg, self.now)   # held + score 80 -> A
        self.assertEqual(t["grade"], "A"); self.assertEqual(t["clip_usd"], 150000.0)
        self.assertIn("$150k target", t["clip_binds"]); self.assertIn("$150,000 target", t["sizing_rule"])
        t = self.ee.make_ticket(self.a(1.2), False, 0.65, 1e9, c, self.cfg, self.now)  # not held -> B = half
        self.assertEqual(t["grade"], "B"); self.assertEqual(t["clip_usd"], 75000.0)
        self.assertIn("$75k target", t["clip_binds"]); self.assertIn("grade B = half clip", t["sizing_rule"])
        self.assertEqual(t["planned_loss_usd"], 900.0)  # 75k * 1.2% coil stop
        t = self.ee.make_ticket(self.a(1.2), True, 0.65, 2e6, c, self.cfg, self.now)   # A: 5% of 24h $vol binds
        self.assertEqual(t["grade"], "A"); self.assertEqual(t["clip_usd"], 100000.0)
        t = self.ee.make_ticket(self.a(1.2), False, 0.65, 2e6, c, self.cfg, self.now)  # B: target $75k binds before liq $100k
        self.assertEqual(t["grade"], "B"); self.assertEqual(t["clip_usd"], 75000.0)

    def test_equity_ticket_sized_at_rail_A_150k_B_half(self):
        c = self.ee.desk_risk_constants(ROOT, self.cfg)
        E = self.cfg["equity"]["ticket"]
        c.update(clip_cap_usd=E["clip_cap_usd"], max_pct_day_volume=E["max_pct_adv"], min_clip_usd=E["min_clip_usd"], sizing_stop_pct=E["sizing_stop_pct"])
        t = self.ee.make_ticket(self.a(5.8), True, None, 5e9, c, self.cfg, self.now, asset_class="equity", symbol="CAT")   # A at rail 5%
        self.assertEqual(t["grade"], "A"); self.assertEqual(t["clip_usd"], 150000.0); self.assertEqual(t["planned_loss_usd"], 7500.0)
        t = self.ee.make_ticket(self.a(5.8), False, None, 5e9, c, self.cfg, self.now, asset_class="equity", symbol="CAT")  # B = half
        self.assertEqual(t["grade"], "B"); self.assertEqual(t["clip_usd"], 75000.0); self.assertEqual(t["planned_loss_usd"], 3750.0)
        t = self.ee.make_ticket(self.a(1.0), True, None, 1e7, c, self.cfg, self.now, asset_class="equity", symbol="CAT")   # A: 1% ADV binds
        self.assertEqual(t["clip_usd"], 100000.0)
        t = self.ee.make_ticket(self.a(1.0), False, None, 1e7, c, self.cfg, self.now, asset_class="equity", symbol="CAT")  # B: $75k target < $100k ADV
        self.assertEqual(t["clip_usd"], 75000.0)

    def test_auto_fire_equity_presize_uses_engine_theme(self):
        import auto_fire as af, tfo_buy
        self.assertEqual((af.MAX_PER_DAY, af.MIN_GAP_MIN), (3, 30))
        with mock.patch.object(tfo_buy, "firm_state", lambda: (1800000.0, -1.7, 1800000.0)):
            self.assertEqual(af.LiveDeps(ROOT).equity_presize(150000.0), (150000.0, ""))
            usd, note = af.LiveDeps(ROOT).equity_presize(300000.0)
            self.assertEqual(usd, 150000.0); self.assertIn("risk_size", note)
        with mock.patch.object(tfo_buy, "firm_state", lambda: (1000000.0, 0.0, 1000000.0)):        # shark max clip (10% eq) still binds
            self.assertEqual(af.LiveDeps(ROOT).equity_presize(150000.0)[0], 100000.0)


if __name__ == "__main__":
    unittest.main()
