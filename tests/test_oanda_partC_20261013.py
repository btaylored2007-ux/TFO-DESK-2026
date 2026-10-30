"""Offline tests for GPT blueprint Part C (13 Oct 2026): C2 a_star, C3 worked batch, C4 rounding/fallback, C1 quote health + idempotency,
requested vs filled units, C5 financing dedupe, C8 NAV return. No network. Run: python3 tests/test_oanda_partC_20261013.py -v"""
import importlib.util, json, os, shutil, sys, tempfile, unittest, datetime as dt
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "research", "behaviour"))
import oanda_risk as RK, oanda_designs as D
S = [[0.000036, 0.0000336], [0.0000336, 0.000064]]
def load():
    spec = importlib.util.spec_from_file_location("sleeve_partC", os.path.join(ROOT, "oanda_sleeve.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
class C2(unittest.TestCase):
    def test_c3_vol_root(self):
        self.assertAlmostEqual(RK.a_vol([0.5, 0.0], [0.0, 0.4], S), 0.769203100827, places=10)
    def test_c3_bounds_and_a_star(self):
        self.assertAlmostEqual(RK.a_gross(0.009, 0.015, 0.02), 0.733333333333, places=10)
        self.assertAlmostEqual(RK.a_signed(-0.006, -0.010, 0.01), 0.40, places=12)
        self.assertAlmostEqual(RK.a_signed(-10000 / 20000, -8000 / 20000, 1.0), 1.25, places=12)
        a = max(0, min(1, 0.769203100827, 0.7333333, 0.40, 1.25)); self.assertAlmostEqual(a, 0.40)
        w = [0.5, 0.4 * 0.4]; v = 252 * sum(w[i] * S[i][j] * w[j] for i in range(2) for j in range(2))
        self.assertAlmostEqual(v ** 0.5, 0.063526599153, places=10)
    def test_signed_example_allows_risk_reducing(self):
        self.assertAlmostEqual(RK.a_signed(0.008, -0.012, 0.01), 1.5)
        self.assertEqual(RK.a_signed(0.005, 0.0, 0.01), float("inf")); self.assertIsNone(RK.a_signed(0.02, 0.0, 0.01))
    def test_vol_edge_cases(self):
        self.assertEqual(RK.a_vol([0, 0], [0, 0], S), 1.0)
        self.assertIsNone(RK.a_vol([10, 0], [0, 0.1], S))                       # existing already breaches
        with self.assertRaises(ValueError): RK.a_vol([0, 0], [1, 0], [[-1e-4, 0], [0, 1e-4]])
    def test_batch_a_star_currency_binds_before_vol(self):
        ex = [dict(ins="AUD_JPY", dir=1, risk_frac=0.005, notional_frac=0.3)]
        r = RK.batch_a_star(ex, [dict(ins="USD_JPY", dir=1, risk_frac=0.01, notional_frac=0.3)])
        self.assertAlmostEqual(r["a_star"], 0.5); self.assertEqual(r["limiting"], "net_ccy_JPY")
        r2 = RK.batch_a_star([dict(ins="EUR_USD", dir=1, risk_frac=0.03)], [dict(ins="GBP_USD", dir=1, risk_frac=0.005)])
        self.assertEqual(r2["a_star"], 0.0); self.assertTrue(r2["limiting"].startswith("existing_book_breach"))
    def test_two_covariances_min_root(self):
        ccys = ["AUD", "EUR"]; S2 = [[S[0][0] * 2, 0], [0, S[1][1] * 2]]
        r = RK.batch_a_star([], [dict(ins="AUD_USD", dir=1, risk_frac=0.001, notional_frac=0.9)], ccys, {"raw": S, "shrunk": S2})
        self.assertLessEqual(r["a_star"], min(r["bounds"]["vol_raw"], r["bounds"]["vol_shrunk"]) + 1e-12)
class C4(unittest.TestCase):
    def test_stop_rounding_worked_example(self):
        s = RK.round_stop_outward(1.087996, 1, 0.00001); self.assertEqual(s, 1.08799)
        Dd = round(1.10000 - s, 5); u = RK.round_units_q(100 / (Dd * 1.5), 1); self.assertEqual(u, 5550)
        self.assertAlmostEqual(u * Dd * 1.5, 99.98325, places=6)
        self.assertEqual(RK.round_stop_outward(1.112004, -1, 0.00001), 1.11201)
        self.assertEqual(RK.round_units_q(-5550.9, 1), -5550)
    def test_min_commission_fails_gate(self):
        self.assertAlmostEqual((1.354109589 + 2) / 18, 0.186339422, places=8); self.assertGreater((1.354109589 + 2) / 18, 0.10)
    def test_fallback_halves_then_drops(self):
        c = [dict(ins="A", u=1000.0), dict(ins="B", u=10.0)]
        def chk(adm):
            for i, (cd, u) in enumerate(adm):
                if cd["ins"] == "B" and u < 8: return ("drop", i, "below_min")
            return "cap" if sum(abs(u) for _, u in adm) > 600 else None
        r = RK.rounding_fallback(c, 1.0, lambda cd, a: RK.round_units_q(a * cd["u"]), chk)
        self.assertEqual(r["halvings"], 1); self.assertEqual(r["dropped"], [("B", "below_min")]); self.assertEqual(r["admitted"][0][1], 500)
        r0 = RK.rounding_fallback(c, 1.0, lambda cd, a: RK.round_units_q(a * cd["u"]), lambda adm: "always")
        self.assertEqual(r0["admitted"], [])
class C8(unittest.TestCase):
    def test_nav_return_flows(self):
        self.assertAlmostEqual(RK.nav_return(20000, 25100, 5000, flow_at_start=True), 0.004)
        self.assertAlmostEqual(RK.nav_return(20000, 25100, 5000), 0.005)
    def test_versions_separate(self):
        self.assertNotEqual(D.VERSION["OXF1"], D.VERSION["SHADOW_PARTA"]); self.assertTrue(D.VERSION["OXF1"].startswith("partB"))
        self.assertNotIn("SHADOW_PARTA", D.STATUS)                               # Part A is never a tradable design
class Sleeve(unittest.TestCase):
    def setUp(self):
        self.m = m = load(); self.tmp = tempfile.mkdtemp(); st = os.path.join(self.tmp, "st"); os.makedirs(st)
        m.ST = st; m.LOG = os.path.join(st, "trades.jsonl"); m.CFG = os.path.join(st, "nocfg.json")
        m.summary = lambda: {"NAV": "50000", "marginUsed": "0", "currency": "AUD", "lastTransactionID": "100"}
        m.open_trades = lambda: []; m.behaviour_ok = lambda *a, **k: True
        m._inst = {"EUR_USD": {"name": "EUR_USD", "pipLocation": -4, "tradeUnitsPrecision": 0, "minimumTradeSize": 1, "displayPrecision": 5, "marginRate": 0.05, "financing": {"longRate": "0", "shortRate": "0"}}}
        self.c = m.cfg()
    def tearDown(self): shutil.rmtree(self.tmp)
    def e(self): return dict(design="OXF1", ins="EUR_USD", dir=1, score=1, risk_pct=0.005, reason="t", stop_dist=0.012004, atr=0.006, sigma_ann=0.07, floor_sp=1e-4,
                             close=1.1, expires_utc="2099-01-01T00:00:00+00:00")
    def px(self, age): return {"EUR_USD": dict(bid=1.0999, ask=1.1, mid=1.09995, sp=9e-5, tradeable=True, q2h={"positiveUnits": "1.5", "negativeUnits": "1.5"}, quote_time="x", quote_received="y", quote_age_s=age)}
    def test_quote_health_blocks_new_entry(self):
        self.m.pricing = lambda i: self.px(900); self.assertTrue(self.m.place_entry(self.e(), False).startswith("defer:quote_stale"))
        def boom(i): raise OSError("down")
        self.m.pricing = boom; self.assertEqual(self.m.place_entry(self.e(), False), "defer:quote_snapshot_failed")
        self.m.pricing = lambda i: dict((k, dict(v, quote_age_s=None)) for k, v in self.px(1).items()); self.assertEqual(self.m.place_entry(self.e(), False), "defer:quote_time_missing")
    def test_dry_entry_outward_stop_requested_units(self):
        self.m.pricing = lambda i: self.px(2); self.assertEqual(self.m.place_entry(self.e(), False), "dry")
        r = [json.loads(l) for l in open(self.m.LOG)][-1]
        self.assertEqual(r["stop_rounded"], 1.08799); self.assertAlmostEqual(r["stop_dist"], 0.01201, places=9)
        self.assertEqual(r["requested_units"], r["units"]); self.assertIsNone(r["filled_units"]); self.assertEqual(r["strategy_version"], D.VERSION["OXF1"])
    def test_live_fill_records_filled_units(self):
        self.m.pricing = lambda i: self.px(2)
        self.m.api = lambda *a, **k: {"orderFillTransaction": {"id": "9", "units": "3000", "price": "1.1", "time": "t", "tradeOpened": {"tradeID": "77"}}}
        self.assertEqual(self.m.place_entry(self.e(), True), "filled")
        r = [json.loads(l) for l in open(self.m.LOG)][-1]; self.assertEqual(r["filled_units"], 3000.0); self.assertNotEqual(r["requested_units"], 3000.0)
        self.assertEqual(r["broker_transaction_id"], "9")
    def test_quote_age_parse(self):
        t = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%S.123456789Z")
        self.assertAlmostEqual((self.m.now_utc() - self.m._ny_parse(t)).total_seconds(), 30, delta=2)
    def test_financing_booked_once(self):
        tx = {"lastTransactionID": "5", "transactions": [{"id": "3", "type": "DAILY_FINANCING", "financing": "-1.5"}]}
        self.m.api = lambda *a, **k: tx
        self.m.log_financing(); open(os.path.join(self.m.ST, "financing_state.json"), "w").write('{"last_id": "1"}'); self.m.log_financing()
        self.assertEqual(len(open(os.path.join(self.m.ST, "financing.jsonl")).readlines()), 1)
    def test_lpb_key_per_version(self):
        self.assertNotEqual(self.m.lpb_key("OXF1", "EUR_USD"), self.m.lpb_key("OXL2", "EUR_USD")); self.assertIn(D.VERSION["OXF1"], self.m.lpb_key("OXF1", "EUR_USD"))
    def test_nav_recon_flow_not_return(self):
        p = os.path.join(self.m.ST, "nav_recon.jsonl"); open(p, "w").write(json.dumps(dict(date="2000-01-01", nav=20000)) + "\n")
        self.m.api = lambda *a, **k: {"lastTransactionID": "9", "transactions": [{"id": "8", "type": "TRANSFER_FUNDS", "amount": "5000"}]}
        self.m.summary = lambda: {"lastTransactionID": "9", "NAV": "25100", "balance": "25000", "unrealizedPL": "100"}   # 13 Oct 2026: NAV + flows share one watermark
        self.m.nav_reconcile(dict(nav=25100, balance=25000, upl=100)); r = [json.loads(l) for l in open(p)][-1]
        self.assertAlmostEqual(r["r_nav"], 0.005); self.assertEqual(r["external_flow_aud"], 5000.0); self.assertEqual(r["cash_check_aud"], 0.0)
if __name__ == "__main__": unittest.main()
