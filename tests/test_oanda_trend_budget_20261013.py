"""Offline tests for the 13 Oct 2026 04:31 AEDT change (Brett): OXF1 trend sleeve gets its own 2% total / 4 trades / 1.5% per-ccy budget on
top of the book's 4% / 8 / 1.5%; combined per-ccy net logged with a > 2.5% warning; one position per pair across the desk is kept.
No network. Run: python3 tests/test_oanda_trend_budget_20261013.py -v"""
import importlib.util, json, os, shutil, sys, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "research", "behaviour"))
import oanda_designs as D
def load():
    spec = importlib.util.spec_from_file_location("sleeve_trend_budget_under_test", os.path.join(ROOT, "oanda_sleeve.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def T(i, ins, units, design, price=1.0, stop=None):
    t = {"id": str(i), "instrument": ins, "currentUnits": str(units), "price": str(price), "clientExtensions": {"tag": "TFO", "comment": design}}
    if stop is not None: t["stopLossOrder"] = {"price": str(stop)}
    return t
def row(ins, d, design, rf=0.005): return dict(ins=ins, dir=d, risk_frac=rf, design=design)

class Base(unittest.TestCase):
    def setUp(self):
        self.m = m = load(); self.tmp = tempfile.mkdtemp(); st = os.path.join(self.tmp, "state", "oanda"); os.makedirs(st)
        m.ST = st; m.LOG = os.path.join(st, "trades.jsonl"); m.CFG = os.path.join(st, "nocfg.json")
        def no_network(*a, **k): raise AssertionError("unexpected OANDA API call")
        m.api = no_network; m.behaviour_ok = lambda *a, **k: True
        m.summary = lambda: {"NAV": "50000", "marginUsed": "0", "currency": "AUD"}
        self.open = []; m.open_trades = lambda: list(self.open)
        m.pricing = lambda insts: {}          # entry stops at 'defer:not_tradeable' AFTER the caps (never places)
        self.c = m.cfg()
    def tearDown(self): shutil.rmtree(self.tmp)
    def lines(self):
        out = []
        for f in (self.m.LOG, os.path.join(self.m.ST, "combined_ccy.jsonl")):
            if os.path.exists(f): out += [json.loads(l) for l in open(f)]
        return out
    def e(self, ins, design, d=1): return dict(design=design, ins=ins, dir=d, risk_pct=0.005, stop_dist=0.01, floor_sp=1e-4, close=1.0)

class TestConfig(Base):
    def test_defaults(self):
        c = self.c
        self.assertEqual((c["open_risk_cap_pct"], c["ccy_net_cap_pct"], c["max_trades"]), (0.04, 0.015, 4))
        self.assertEqual((c["trend_open_risk_cap_pct"], c["trend_ccy_net_cap_pct"], c["trend_max_trades"], c["combined_ccy_warn_pct"]), (0.02, 0.015, 4, 0.025))
        self.assertEqual(D.MAXSLOTS["OXF1"], 4); self.assertEqual(D.RISK["OXF1"], 0.005); self.assertEqual(D.RISK["OXF1_XAU"], 0.0025)
    def test_rails(self):
        json.dump({"trend_open_risk_cap_pct": 0.1, "trend_max_trades": 10, "trend_ccy_net_cap_pct": 0.05, "combined_ccy_warn_pct": 0.1,
                   "open_risk_cap_pct": 0.1, "max_trades": 20}, open(self.m.CFG, "w"))
        c = self.m.cfg()
        self.assertEqual((c["trend_open_risk_cap_pct"], c["trend_max_trades"], c["trend_ccy_net_cap_pct"], c["combined_ccy_warn_pct"]), (0.02, 4, 0.015, 0.025))
        self.assertEqual((c["open_risk_cap_pct"], c["max_trades"]), (0.10, 4))   # open-risk cap 10%; open count 4
    def test_live_config_file(self):
        p = os.path.join(ROOT, "state", "oanda", "config.json")
        if not os.path.exists(p): self.skipTest("live state/oanda/config.json is not in the code-only tree (TEST_TRIAGE.md)")
        c = json.load(open(p))
        self.assertEqual((c["trend_open_risk_cap_pct"], c["trend_max_trades"]), (0.02, 4))
        self.assertLessEqual(float(c.get("risk_pct", 0.02)), 0.02)

class TestBuckets(Base):
    def full_book(self):   # 4% book incl. 1.5% net short USD
        return [row("EUR_USD", 1, "OXL2"), row("GBP_USD", 1, "OXL2"), row("AUD_USD", 1, "OXL2"), row("EUR_CHF", 1, "OXL2", 0.01),
                row("NZD_JPY", 1, "OXL2"), row("CAD_JPY", 1, "OXL2"), row("EUR_GBP", 1, "OXL1", 0.005)]
    def test_book_full_blocks_book_not_trend(self):
        rows = self.full_book(); self.assertAlmostEqual(sum(r["risk_frac"] for r in rows), 0.04)
        self.assertIn("open_risk_cap", self.m.caps_check(rows, "CHF_JPY", 1, 0.005, self.c, "OXL2"))
        self.assertIn("open_risk_cap", self.m.caps_check(rows, "CHF_JPY", 1, 0.005, self.c))      # design None = book (legacy callers)
        self.assertIsNone(self.m.caps_check(rows, "CHF_JPY", 1, 0.005, self.c, "OXF1"))
    def test_book_usd_cap_does_not_block_trend(self):
        rows = self.full_book()
        self.assertIn("ccy_cap USD", self.m.caps_check(rows[:3], "NZD_USD", 1, 0.005, self.c, "OXL2"))
        self.assertIsNone(self.m.caps_check(rows, "NZD_USD", 1, 0.005, self.c, "OXF1"))
    def test_trend_2pct_total(self):
        tr = [row("EUR_JPY", 1, "OXF1"), row("GBP_CHF", -1, "OXF1"), row("AUD_CAD", 1, "OXF1"), row("NZD_CHF", 1, "OXF1")]
        why = self.m.caps_check(tr, "USD_CAD", 1, 0.005, self.c, "OXF1"); self.assertIn("trend_open_risk_cap", why)
        self.assertIsNone(self.m.caps_check(tr[:3], "USD_CAD", 1, 0.005, self.c, "OXF1"))
        self.assertIsNone(self.m.caps_check(tr, "USD_CAD", 1, 0.005, self.c, "OXL2"))   # trend rows never count toward the book
    def test_trend_own_ccy_cap(self):
        tr = [row("EUR_USD", 1, "OXF1"), row("GBP_USD", 1, "OXF1"), row("AUD_USD", 1, "OXF1")]
        self.assertIn("trend_ccy_cap USD", self.m.caps_check(tr, "NZD_USD", 1, 0.005, self.c, "OXF1"))
        self.assertIsNone(self.m.caps_check(tr, "USD_JPY", 1, 0.005, self.c, "OXF1"))   # long USD reduces net short USD
    def test_gold_quarter_counts(self):
        tr = [row("EUR_JPY", 1, "OXF1"), row("GBP_CHF", -1, "OXF1"), row("AUD_CAD", 1, "OXF1")]
        self.assertIsNone(self.m.caps_check(tr, "XAU_USD", 1, 0.0025, self.c, "OXF1"))
    def test_rows_carry_design(self):
        rows = self.m.open_risk_rows([T(1, "EUR_USD", 1000, "OXF1"), T(2, "GBP_USD", 1000, "OXL2"), {"id": "3", "instrument": "XAU_USD", "currentUnits": "1",
                                       "price": "1", "clientExtensions": {"tag": "TFO"}}], 50000.0, self.c)
        self.assertEqual([r["design"] for r in rows], ["OXF1", "OXL2", "OXL1"])

class TestCombinedLog(Base):
    def test_logs_and_warns(self):
        rows = [row("EUR_USD", 1, "OXL2", 0.0125), row("GBP_USD", 1, "OXL2"), row("AUD_USD", 1, "OXF1"), row("NZD_USD", 1, "OXF1")]
        over = self.m.combined_ccy_check(rows, self.c, "t")
        self.assertEqual(set(over), {"USD"}); self.assertAlmostEqual(over["USD"], -2.75)
        ev = [l["event"] for l in self.lines()]; self.assertIn("combined_ccy_net", ev); self.assertIn("combined_ccy_warning", ev)
    def test_no_warn_at_or_below(self):
        rows = [row("EUR_USD", 1, "OXL2", 0.015), row("AUD_USD", 1, "OXF1", 0.01)]
        self.assertEqual(self.m.combined_ccy_check(rows, self.c, "t"), {})
        ev = [l["event"] for l in self.lines()]; self.assertIn("combined_ccy_net", ev); self.assertNotIn("combined_ccy_warning", ev)
    def test_never_raises(self):
        self.assertEqual(self.m.combined_ccy_check([dict(bad=1)], self.c, "t"), {})

class TestPlaceEntry(Base):
    def test_four_open_blocks_book_and_trend(self):
        self.open = [T(i, p, 1, "OXL2") for i, p in enumerate(["EUR_GBP", "AUD_NZD", "EUR_AUD", "GBP_AUD"])]
        self.m.ledger_entry_risk = lambda: {str(i): dict(risk_aud=0.004 * 50000) for i in range(4)}
        self.assertEqual(self.m.place_entry(self.e("EUR_JPY", "OXL2"), False), "skip:total_max_trades")
        self.assertEqual(self.m.place_entry(self.e("EUR_JPY", "OXF1"), False), "skip:total_max_trades")
    def test_trend_max_4(self):
        self.open = [T(i, p, 1, "OXF1") for i, p in enumerate(["EUR_GBP", "AUD_NZD", "CAD_CHF", "GBP_CAD"])]
        self.m.ledger_entry_risk = lambda: {str(i): dict(risk_aud=0.001 * 50000) for i in range(4)}
        self.assertEqual(self.m.cfg()["trend_max_trades"], 4)
        self.assertEqual(self.m.place_entry(self.e("EUR_JPY", "OXF1"), False), "skip:total_max_trades")
        self.assertEqual(self.m.place_entry(self.e("EUR_JPY", "OXL2"), False), "skip:total_max_trades")
    def test_one_per_pair_across_desk_even_same_direction(self):
        self.open = [T(1, "EUR_USD", 1000, "OXL2")]; self.m.ledger_entry_risk = lambda: {}
        self.assertEqual(self.m.place_entry(self.e("EUR_USD", "OXF1", 1), False), "skip:already_open")
        self.assertEqual(self.m.place_entry(self.e("EUR_USD", "OXF1", -1), False), "skip:already_open")
    def test_place_logs_combined(self):
        self.m.ledger_entry_risk = lambda: {}
        self.m.place_entry(self.e("EUR_JPY", "OXF1"), False)
        self.assertIn("combined_ccy_net", [l["event"] for l in self.lines()])

class TestShadowKept(Base):
    def test_shadow_functions_present(self):
        for f in ("shadow_gpt_caps", "shadow_scores", "net_r_report"): self.assertTrue(callable(getattr(self.m, f)))
        src = open(os.path.join(ROOT, "oanda_sleeve.py")).read()
        self.assertIn("shadow_gpt_caps(cur, queue", src); self.assertIn("shadow_pnl_05_aud", src)

if __name__ == "__main__": unittest.main()
