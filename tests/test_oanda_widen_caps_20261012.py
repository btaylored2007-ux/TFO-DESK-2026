"""Offline tests for the 12 Oct 2026 OANDA practice-sleeve change (Brett 21:41 AEDT): 18-pair universe, 0.5% risk, per-currency net cap 1.5%,
portfolio open-risk cap 4%, None exit score never closes, frozen scale gate. No network. Run: python3 tests/test_oanda_widen_caps_20261012.py -v"""
import datetime as dt, importlib.util, json, os, shutil, sys, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "research", "behaviour"))
import oanda_designs as D, oanda_scale_gate as G
def load():
    spec = importlib.util.spec_from_file_location("sleeve_caps_under_test", os.path.join(ROOT, "oanda_sleeve.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def T(i, ins, units, price, stop): return {"id": str(i), "instrument": ins, "currentUnits": str(units), "price": str(price),
                                          "clientExtensions": {"tag": "TFO", "comment": "OXL2"}, "stopLossOrder": {"price": str(stop)}}
R = {"AUD": [("2026-08-01", 4.51)], "JPY": [("2026-07-01", 1.458)], "USD": [("2026-08-01", 3.82)], "GBP": [("2026-01-01", 3.71)],
     "CHF": [("2026-08-01", -0.045)], "EUR": [("2026-01-01", 2.03)]}
CS = [dict(t="2026-10-08T21:00:00.000000000Z", o=1, h=1, l=1, c=1, sp=1e-4)]

class Base(unittest.TestCase):
    def setUp(self):
        self.m = m = load(); self.tmp = tempfile.mkdtemp(); st = os.path.join(self.tmp, "state", "oanda"); os.makedirs(st)
        m.ST = st; m.LOG = os.path.join(st, "trades.jsonl"); m.SEEN = os.path.join(st, "closed_seen.json"); m.CFG = os.path.join(st, "nocfg.json")
        def no_network(*a, **k): raise AssertionError("unexpected OANDA API call: %r" % (a,))
        m.api = no_network; m.behaviour_ok = lambda *a, **k: True
        m.instruments = lambda: {k: {"displayPrecision": 3, "tradeUnitsPrecision": 0, "marginRate": "0.0333", "financing": {"longRate": "0", "shortRate": "0"}} for k in D.P32 + ["XAU_USD"]}
        m.summary = lambda: {"NAV": "50000", "marginUsed": "0", "currency": "AUD"}
        m.pricing = lambda insts: {i: {"bid": 1.0, "ask": 1.0001, "mid": 1.00005, "sp": 1e-4, "tradeable": True, "q2h": {"positiveUnits": "1"}} for i in insts}
        self.open = []; m.open_trades = lambda: list(self.open)
    def tearDown(self): shutil.rmtree(self.tmp)
    def lines(self): return [json.loads(l) for l in open(self.m.LOG)] if os.path.exists(self.m.LOG) else []
    def entry(self, ins, d=1): return dict(design="OXL2", ins=ins, dir=d, score=0.03, risk_pct=0.005, reason="t", stop_dist=0.01, floor_sp=1e-4, close=1.0,
                                            expires_utc="2026-12-31T00:00:00+00:00")

class TestUniverseAndRisk(Base):
    def test_universe_18_liquid_pairs(self):
        self.assertEqual(len(D.P18), 18); self.assertEqual(len(set(D.P18)), 18)
        self.assertTrue(set(D.P18) <= set(D.P32))
        for x in ("EUR_USD", "GBP_USD", "USD_JPY", "USD_CHF", "AUD_USD", "NZD_USD", "USD_CAD"): self.assertIn(x, D.P18)
        self.assertFalse(any("NOK" in x or "SEK" in x for x in D.P18))
        self.assertEqual(D.universe(["OXL1", "OXL2"]), ["XAU_USD"] + D.P18)
    def test_risk_half_percent_and_rails(self):
        self.assertEqual({k: v for k, v in D.RISK.items() if k not in ("OXP1", "OXB1", "OXT1")}, {"OXL1": 0.005, "OXL2": 0.005, "OXF1": 0.005, "OXF1_XAU": 0.0025})   # 16 Oct: OXP1/OXB1 own 0.75% bucket (test_oanda_cmdty_20261016)   # OXF1 added 12 Oct (GPT blueprint)
        json.dump({"risk_pct": 0.02, "max_trades": 20, "ccy_net_cap_pct": 0.05, "open_risk_cap_pct": 0.1}, open(self.m.CFG, "w"))
        c = self.m.cfg()
        self.assertEqual(c["risk_pct"], 0.005)            # no passed scale gate in temp dir -> 0.5%
        self.assertEqual(c["max_trades"], 4); self.assertEqual(c["ccy_net_cap_pct"], 0.015); self.assertEqual(c["open_risk_cap_pct"], 0.10)   # open count 4; open-risk cap stays 10%

class TestCaps(Base):
    def test_three_short_yen_then_fourth_blocked(self):
        c = self.m.cfg(); rows = []
        for ins in ("AUD_JPY", "USD_JPY", "GBP_JPY"):
            self.assertIsNone(self.m.caps_check(rows, ins, 1, 0.005, c)); rows.append(dict(ins=ins, dir=1, risk_frac=0.005))
        why = self.m.caps_check(rows, "EUR_JPY", 1, 0.005, c); self.assertIn("ccy_cap JPY", why)
        self.assertIsNone(self.m.caps_check(rows, "CHF_JPY", -1, 0.005, c))   # long JPY reduces net JPY exposure
    def test_offsetting_legs_net(self):
        net = self.m.ccy_net([dict(ins="AUD_USD", dir=1, risk_frac=0.005), dict(ins="USD_JPY", dir=1, risk_frac=0.005)])
        self.assertAlmostEqual(net["USD"], 0.0); self.assertAlmostEqual(net["AUD"], 0.005); self.assertAlmostEqual(net["JPY"], -0.005)
    def test_portfolio_open_risk_cap(self):
        c = self.m.cfg(); rows = [dict(ins=x, dir=1 if i % 2 else -1, risk_frac=0.005) for i, x in enumerate(D.P18[:8])]
        self.assertIn("open_risk_cap", self.m.caps_check(rows, "EUR_CHF", 1, 0.005, c))
    def test_open_risk_uses_current_stop(self):
        self.m.log(dict(event="entry_order", live=True, risk_aud=250.0, stop_dist=1.0, response={"trade_id": "1"}))
        self.m.log(dict(event="entry_order", live=True, risk_aud=250.0, stop_dist=1.0, response={"trade_id": "2"}))
        rows = self.m.open_risk_rows([T(1, "USD_JPY", 1000, 158.0, 157.5), T(2, "GBP_JPY", 1000, 209.0, 209.5)], 50000.0, self.m.cfg())
        # 13 Oct 2026 audit (GPT N01, Brett): caps charge the R frozen at entry; a trailed stop no longer releases budget
        self.assertAlmostEqual(rows[0]["risk_frac"], 0.005); self.assertAlmostEqual(rows[1]["risk_frac"], 0.005)
    def test_place_entry_blocked_and_logged(self):
        self.open = [T(i, x, 1000, 1.0, 0.99) for i, x in enumerate(("AUD_JPY", "USD_JPY", "GBP_JPY"))]
        r = self.m.place_entry(self.entry("EUR_JPY"), live=True)
        self.assertTrue(r.startswith("skip:ccy_cap")); self.assertEqual(self.lines()[-1]["event"], "entry_cap_block")
    def test_place_entry_ok_dry(self):
        self.assertEqual(self.m.place_entry(self.entry("EUR_USD"), live=False), "dry")
        self.assertEqual(self.lines()[-1]["risk_aud"], 250.0)

class TestNoneNeverCloses(Base):
    def test_exit_score_ignores_financing_guard(self):
        D.LIVE_FIN = {"AUD_JPY": {"longRate": "0", "shortRate": "0"}}     # OANDA's intermittent 0/0 financing quote
        self.assertIsNone(D._carry("AUD_JPY", {"AUD_JPY": CS}, R))          # entry filter still applies
        self.assertGreater(D._carry("AUD_JPY", {"AUD_JPY": CS}, R, for_exit=True), 0.015)
        D.LIVE_FIN = {}
    def test_exit_none_only_when_data_missing(self):
        self.assertIsNone(D._carry("AUD_JPY", {}, R, for_exit=True))
        self.assertEqual(D._carry("EUR_GBP", {"EUR_GBP": CS}, {"EUR": [("2026-01-01", 2.0)], "GBP": [("2026-01-01", 2.5)]}, for_exit=True), 0.0)
    def _daily(self, score):
        m = self.m; self.open = [T(61636, "AUD_JPY", 16295, 109.9, 108.2)]
        m.reconcile_stop_fills = lambda: []; m.reconcile_stops = lambda *a: []
        m.snapshot = lambda extra=None: ({"nav": 50000.0, "tfo_upl_aud": 0, "tfo_realized_aud": 0}, list(self.open))
        m.candles = lambda ins, n=320: CS * 30
        m.designs.signals = lambda names, data: []; m.designs.score_for = lambda design, data, ins: score
        m.designs.LIVE_FIN = {}; closed = []
        m.close_trade = lambda t, reason, live: closed.append(reason)
        m.set_stop = lambda *a, **k: True; m.run_queue = lambda *a, **k: None
        m.daily(live=False); return closed
    def test_none_score_holds(self):
        self.assertEqual(self._daily(None), [])
        self.assertTrue(any(l["event"] == "signal_unknown" for l in self.lines()))
    def test_real_flip_closes(self):
        self.assertEqual(len(self._daily(-0.02)), 1); self.assertEqual(len(self._daily(0.0)), 1)

class TestScaleGate(unittest.TestCase):
    def mk(self, n, pl=10.0, ccys=("EUR_USD", "USD_JPY")): return [dict(instrument=ccys[i % len(ccys)], net_aud=pl) for i in range(n)]
    def test_too_few(self): self.assertFalse(G.evaluate(self.mk(29))["passed"])
    def test_currencies(self):
        self.assertFalse(G.evaluate(self.mk(30))["passed"])                         # EUR, USD, JPY only = 3
        self.assertTrue(G.evaluate(self.mk(30, ccys=("EUR_USD", "GBP_JPY")))["passed"])
    def test_net_and_ex_best(self):
        t = self.mk(29, -1.0, ("EUR_USD", "GBP_JPY")) + [dict(instrument="AUD_CHF", net_aud=100.0)]
        e = G.evaluate(t); self.assertGreater(e["net_aud"], 0); self.assertFalse(e["passed"]); self.assertIn("ex best", e["reasons"][0])
    def test_steps(self):
        good = self.mk(30, ccys=("EUR_USD", "GBP_JPY"))
        self.assertEqual(G.decide(good[:5], [])["allowed_risk_pct"], 0.005)
        self.assertEqual(G.decide(good, [])["allowed_risk_pct"], 0.0075)
        self.assertEqual(G.decide(good, good)["allowed_risk_pct"], 0.01)
    def test_frozen_file_and_tamper(self):
        st = tempfile.mkdtemp()
        try:
            self.assertEqual(G.allowed_risk_pct(st), 0.005)
            G.write_frozen(st); self.assertIsNotNone(G.load_frozen(st))
            json.dump(dict(allowed_risk_pct=0.0075, rules_sha256=G.rules_hash(G.RULES)), open(G.status_path(st), "w"))
            self.assertEqual(G.allowed_risk_pct(st), 0.0075)
            with self.assertRaises(SystemExit): G.write_frozen(st)                  # never rewritten
            p = G.frozen_path(st); os.chmod(p, 0o644); j = json.load(open(p)); j["rules"]["min_closed_trades"] = 5; json.dump(j, open(p, "w"))
            self.assertIsNone(G.load_frozen(st)); self.assertEqual(G.allowed_risk_pct(st), 0.005)
        finally: shutil.rmtree(st)
    def test_live_frozen_file_intact(self):
        if os.path.exists(G.frozen_path(G.ST_DEFAULT)):
            self.assertIsNotNone(G.load_frozen())
        else:
            st = tempfile.mkdtemp()
            try:
                G.write_frozen(st); self.assertIsNotNone(G.load_frozen(st))
            finally: shutil.rmtree(st, ignore_errors=True)
if __name__ == "__main__": unittest.main()
