"""Offline tests for the 12 Oct 2026 GPT-blueprint change to the OANDA practice sleeve (RULE_LOG.md). No network.
Run: python3 tests/test_oanda_gpt_blueprint_20261012.py -v"""
import datetime as dt, importlib.util, json, math, os, shutil, sys, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "research", "behaviour"))
import oanda_designs as D, oanda_risk as RK
UTC = dt.timezone.utc
def load():
    spec = importlib.util.spec_from_file_location("sleeve_gpt_under_test", os.path.join(ROOT, "oanda_sleeve.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def bars(closes, rng=0.01, start="2026-01-01"):
    t0 = dt.datetime.fromisoformat(start); out = []
    for i, c in enumerate(closes):
        out.append(dict(t=(t0 + dt.timedelta(days=i)).strftime("%Y-%m-%dT21:00:00.000000000Z"), o=c, h=c + rng / 2, l=c - rng / 2, c=c, sp=1e-4))
    return out

class TestWilderATR(unittest.TestCase):
    def test_seed_and_recursion(self):
        cs = bars([1.0] * 22, rng=0.01)                       # 21 TRs of 0.01
        self.assertAlmostEqual(RK.wilder_atr(cs, 20), 0.01)
        cs.append(dict(t="x", o=1, h=1.05, l=0.95, c=1.0, sp=0))  # TR 0.10
        self.assertAlmostEqual(RK.wilder_atr(cs, 20), (0.01 * 19 + 0.10) / 20)
    def test_too_short(self): self.assertIsNone(RK.wilder_atr(bars([1.0] * 20), 20))
    def test_sleeve_uses_wilder(self): self.assertIs(load().atr, RK.wilder_atr)

class TestMetaSizing(unittest.TestCase):
    META = dict(pipLocation=-4, tradeUnitsPrecision=0, minimumTradeSize=1, displayPrecision=5, marginRate=0.0333, financing={})
    def test_round_units_and_minimum(self):
        self.assertEqual(RK.round_units(1234.9, self.META), 1234.0)
        self.assertEqual(RK.round_units(0.5, self.META), 0.0)
        self.assertEqual(RK.round_units(0.0149, dict(self.META, tradeUnitsPrecision=2, minimumTradeSize=0.01)), 0.01)
        self.assertEqual(RK.round_units(5, dict(self.META, minimumTradeSize=10)), 0.0)
    def test_inst_meta_reads_fields(self):
        m = RK.inst_meta({"pipLocation": -2, "tradeUnitsPrecision": 0, "minimumTradeSize": "1", "displayPrecision": 3, "marginRate": "0.05"})
        self.assertEqual((m["pipLocation"], m["minimumTradeSize"]), (-2, 1.0)); self.assertAlmostEqual(RK.pip(m), 0.01)
    def test_risk_is_ceiling_vol_cap_binds(self):
        u, why, ur, uv = RK.size_units(50000, 0.01, 1.0, 1.5, 0.08)           # risk 250/(0.015)=16667 ; vol 1500/(1.5*0.08)=12500
        self.assertEqual(why, "vol_cap"); self.assertAlmostEqual(u, 12500); self.assertAlmostEqual(ur, 50000 * 0.005 / 0.015)
        u2, why2, _, _ = RK.size_units(50000, 0.01, 1.0, 1.5, 0.02)
        self.assertEqual(why2, "risk"); self.assertLessEqual(RK.r0_aud(u2, 0.01, 1.5), 250 + 1e-6)
    def test_c_quote_conservative(self): self.assertEqual(RK.c_quote({"positiveUnits": "1.50", "negativeUnits": "1.52"}), 1.52)

class TestR0NetR(unittest.TestCase):
    def test_net_r(self):
        self.assertAlmostEqual(RK.net_r(-250, -5, 250), -1.02); self.assertAlmostEqual(RK.net_r(500, 10, 250), 2.04); self.assertIsNone(RK.net_r(1, 0, 0))

class TestCostGate(unittest.TestCase):
    def test_spread_vs_atr(self):
        self.assertIn("ATR20", RK.cost_gate(1000, 0.0006, 0, 1.0, 1.5, 0, 30, 1000 * 0.02 * 1.5, 0.01))
        self.assertIsNone(RK.cost_gate(1000, 0.0004, 0.0002, 1.0, 1.5, 0, 30, 1000 * 0.02 * 1.5, 0.01))
    def test_financing_debit_rejects_credit_never_rescues(self):
        r0 = 10000 * 0.012 * 1.0                     # D = 1.2% of price
        self.assertIn("cost/R0", RK.cost_gate(10000, 0.0001, 0.00005, 1.0, 1.0, -0.04, 30, r0, 0.006))
        self.assertIsNone(RK.cost_gate(10000, 0.0001, 0.00005, 1.0, 1.0, +0.04, 30, r0, 0.006))
        self.assertEqual(RK.financing_debit_aud(10000, 1.0, 1.0, 0.04, 30), 0.0)   # credit counts as zero, not negative

class TestGptShadowCaps(unittest.TestCase):
    def test_tighter_net_and_total(self):
        pos = [dict(ins=i, dir=1, risk_frac=0.005, notional_frac=0.3) for i in ("AUD_JPY", "USD_JPY", "GBP_JPY")]
        br = RK.gpt_caps_check(pos)
        self.assertTrue(any(b.startswith("net JPY") for b in br)); self.assertFalse(any(b.startswith("gross JPY") for b in br))   # 1.5% = at cap
        br4 = RK.gpt_caps_check(pos + [dict(ins="CHF_JPY", dir=-1, risk_frac=0.005, notional_frac=0.3)])
        self.assertTrue(any(b.startswith("gross JPY") for b in br4))   # gross counts offsetting legs too
        five = [dict(ins=i, dir=1 if k % 2 else -1, risk_frac=0.005, notional_frac=0.1) for k, i in enumerate(("EUR_USD", "AUD_NZD", "GBP_CHF", "CAD_JPY", "EUR_GBP"))]
        self.assertTrue(any(b.startswith("total") for b in RK.gpt_caps_check(five)))
    def test_max_scale(self):
        ex = [dict(ins="AUD_JPY", dir=1, risk_frac=0.005, notional_frac=0.3)]
        a = RK.max_batch_scale(ex, [dict(ins="USD_JPY", dir=1, risk_frac=0.01, notional_frac=0.3)])
        self.assertAlmostEqual(a, 0.5, places=3)          # net JPY -(0.005+a*0.01) <= 1%  -> a = 0.5
        self.assertEqual(RK.max_batch_scale([], [dict(ins="EUR_USD", dir=1, risk_frac=0.005, notional_frac=0.2)]), 1.0)
    def test_ewma_vol_cap(self):
        import random; random.seed(1)
        rows = [[random.gauss(0, 0.01), random.gauss(0, 0.01)] for _ in range(300)]
        S = RK.ewma_cov(rows); self.assertAlmostEqual(S[0][1], S[1][0])
        v = RK.forecast_vol({"AUD": 0.5, "EUR": 0.0}, ["AUD", "EUR"], S); self.assertTrue(0.04 < v < 0.12)
        self.assertTrue(any(b.startswith("vol") for b in RK.gpt_caps_check([dict(ins="AUD_USD", dir=1, risk_frac=0.001, notional_frac=0.9)], ["AUD", "EUR"], S)))
        self.assertAlmostEqual(RK.LAMBDA, 0.988514); self.assertAlmostEqual(RK.GAMMA, 0.30)

class TestTrendSleeve(unittest.TestCase):
    def test_universe(self):
        self.assertEqual(len(D.F20), 20); self.assertEqual(len(set(D.F20)), 20)
        self.assertFalse(any(x in "".join(D.F20) for x in ("NOK", "SEK", "MXN", "ZAR", "TRY")))
        self.assertIn("XAU_USD", D.universe(["OXF1"])); self.assertEqual(D.RISK["OXF1_XAU"], 0.0025)
    def test_signal_and_cross(self):
        up = bars([1.0 + 0.001 * i for i in range(80)]); d, cross = D._trend("EUR_USD", {"EUR_USD": up}); self.assertEqual(d, 1)
        rev = bars([1.0 + 0.001 * i for i in range(80)] + [1.08 - 0.004 * i for i in range(40)]); d2, cross2 = D._trend("EUR_USD", {"EUR_USD": rev})
        self.assertEqual(d2, -1); self.assertIsNotNone(cross2)
        sig = [s for s in D.signals(["OXF1"], {"XAU_USD": up, "EUR_USD": up}) if s["design"] == "OXF1"]
        self.assertEqual(sig, [])   # 13 Oct 2026 audit #1: a standing EMA state (no fresh cross, < 300 bars) is NOT an entry
        self.assertIsNone(D.score_for("OXF1", {}, "EUR_USD"))     # unknown never closes
    def test_design_of_uses_comment(self):
        self.assertEqual(D.design_of({"instrument": "XAU_USD", "clientExtensions": {"comment": "OXF1"}}), "OXF1")
        self.assertEqual(D.design_of({"instrument": "XAU_USD"}), "OXL1")
    def test_trail_highest_close_minus_3atr_never_loosens(self):
        m = load(); c = m.cfg()
        cs = bars([1.0 + 0.001 * i for i in range(60)] + [1.05] * 5, rng=0.01)
        t = {"id": "9", "instrument": "EUR_USD", "currentUnits": "1000", "price": "1.0", "openTime": cs[10]["t"], "clientExtensions": {"tag": "TFO", "comment": "OXF1"}}
        ns, why = m.trail_stop(t, cs, c)
        self.assertAlmostEqual(ns, 1.059 - 3 * RK.wilder_atr(cs)); self.assertIn("OXF1", why)
        self.assertEqual(m._looser(1, ns - 0.01, ns), True)       # set_stop refuses looser stops
    def test_next_london(self):
        m = load(); n = dt.datetime(2026, 10, 12, 21, 5, tzinfo=UTC)          # 08:05 AEDT Tue = 22:05 BST Mon
        self.assertEqual(m.next_london("08:05", n), dt.datetime(2026, 10, 13, 7, 5, tzinfo=UTC))

class TestSleeveWiring(unittest.TestCase):
    def setUp(self):
        self.m = m = load(); self.tmp = tempfile.mkdtemp(); st = os.path.join(self.tmp, "state", "oanda"); os.makedirs(st)
        m.ST = st; m.LOG = os.path.join(st, "trades.jsonl"); m.CFG = os.path.join(st, "nocfg.json")
        m.api = lambda *a, **k: (_ for _ in ()).throw(AssertionError("network"))
        m.behaviour_ok = lambda *a, **k: True; m.open_trades = lambda: []
        m.instruments = lambda: {"EUR_USD": {"displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1", "marginRate": "0.0333", "pipLocation": -4,
                                             "financing": {"longRate": "-0.0400", "shortRate": "0.0150"}}}
        m.summary = lambda: {"NAV": "50000", "marginUsed": "0", "currency": "AUD"}
        self.sp = 0.0001
        m.pricing = lambda insts: {i: {"bid": 1.0, "ask": 1.0 + self.sp, "mid": 1.0, "sp": self.sp, "tradeable": True, "q2h": {"positiveUnits": "1.5", "negativeUnits": "1.5"}} for i in insts}
    def tearDown(self): shutil.rmtree(self.tmp)
    def lines(self): return [json.loads(l) for l in open(self.m.LOG)] if os.path.exists(self.m.LOG) else []
    def e(self, **k):
        d = dict(design="OXL2", ins="EUR_USD", dir=-1, score=0.02, risk_pct=0.005, reason="t", stop_dist=0.012, atr=0.006, sigma_ann=0.07, floor_sp=1e-4, close=1.0,
                 expires_utc="2026-12-31T00:00:00+00:00"); d.update(k); return d
    def test_entry_logs_r0_meta_and_vol_cap(self):
        self.assertEqual(self.m.place_entry(self.e(sigma_ann=0.20), live=False), "dry")
        r = self.lines()[-1]; self.assertEqual(r["sizing_binding"], "vol_cap"); self.assertIn("vol_cap_note", r)
        self.assertAlmostEqual(r["r0_aud"], r["units"] * 0.012 * 1.5, places=1); self.assertLess(r["r0_aud"], r["risk_aud"])
        self.assertEqual(r["instrument_meta"]["pipLocation"], -4); self.assertIsNotNone(r["cost_r"])
    def test_cost_gate_blocks_wide_spread_and_debit(self):
        self.sp = 0.0004                                            # > 0.05 x ATR 0.006 = 0.0003
        self.assertIn("cost_gate", self.m.place_entry(self.e(floor_sp=3e-4), live=False))
        self.sp = 0.0001
        self.assertIn("cost/R0", self.m.place_entry(self.e(dir=1), live=False))   # long pays 4%/yr for 30d on a 1.2% stop
        self.assertEqual(self.m.place_entry(self.e(dir=-1), live=False), "dry")   # short earns a credit: allowed, credit not counted
        self.assertTrue(any(l["event"] == "entry_cost_block" for l in self.lines()))
    def test_open_risk_prefers_r0(self):
        self.m.log(dict(event="entry_order", live=True, risk_aud=250.0, r0_aud=150.0, stop_dist=1.0, nav_aud=50000, response={"trade_id": "1"}))
        t = {"id": "1", "instrument": "EUR_USD", "currentUnits": "100", "price": "2.0", "clientExtensions": {"tag": "TFO"}, "stopLossOrder": {"price": "1.0"}}
        self.assertAlmostEqual(self.m.open_risk_rows([t], 50000, self.m.cfg())[0]["risk_frac"], 0.003)
    def test_not_before_defers(self):
        pf = os.path.join(self.m.ST, "pending_dry.json")
        json.dump({"queue": [self.e(design="OXF1", not_before_utc=(dt.datetime.now(UTC) + dt.timedelta(hours=3)).isoformat())]}, open(pf, "w"))
        self.m.halted = lambda s: False; self.m.reopen_blackout = lambda **k: None
        self.m.run_queue(False, {"tfo_upl_aud": 0, "tfo_realized_aud": 0})
        self.assertFalse(any(l["event"] == "entry_dry" for l in self.lines()))
    def test_cfg_rails(self):
        json.dump({"vol_cap_pct": 0.5, "max_cost_r": 1.0, "max_spread_atr": 1.0}, open(self.m.CFG, "w")); c = self.m.cfg()
        self.assertEqual((c["vol_cap_pct"], c["max_cost_r"], c["max_spread_atr"]), (0.03, 0.10, 0.05))
        self.assertEqual((c["ccy_net_cap_pct"], c["open_risk_cap_pct"], c["max_trades"], c["risk_pct"]), (0.015, 0.04, 8, 0.005))   # live caps unchanged
    def test_shadow_caps_never_block_and_count(self):
        data = {"EUR_USD": bars([1.0 + 0.001 * i for i in range(80)]), "AUD_USD": bars([0.66] * 80)}
        q = [self.e(ins="EUR_USD", dir=1)]
        rec = self.m.shadow_gpt_caps([], q, data, 50000, self.m.cfg(), "x", False)
        self.assertEqual(rec["candidates"], 1); self.assertIn("would_block", rec)
    def test_nav_once_per_day(self):
        snap = dict(nav=1, balance=1, upl=0, tfo_open=0, tfo_upl_aud=0, tfo_realized_aud=0)
        self.assertTrue(self.m.log_nav(snap)); self.assertFalse(self.m.log_nav(snap))
    def test_scale_gate_file_untouched(self):
        import hashlib
        j = json.load(open(os.path.join(ROOT, "state", "oanda", "SCALE_GATE_FROZEN.json")))
        self.assertEqual(hashlib.sha256(json.dumps(j["rules"], sort_keys=True).encode()).hexdigest(), j["sha256"])

if __name__ == "__main__": unittest.main()
