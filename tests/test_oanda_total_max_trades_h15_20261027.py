"""OANDA open-count rail: at most 4 TFO trades across ALL buckets (book + trend + cmdty). No network, temp state only."""
import importlib.util, json, os, shutil, sys, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "research", "behaviour"))
def load():
    spec = importlib.util.spec_from_file_location("sleeve_h15_under_test", os.path.join(ROOT, "oanda_sleeve.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def T(i, ins, design):
    return {"id": str(i), "instrument": ins, "currentUnits": "1", "price": "1.0", "clientExtensions": {"tag": "TFO", "comment": design}}
BOOK = ["EUR_GBP", "AUD_NZD", "EUR_AUD", "GBP_AUD", "CAD_CHF", "EUR_CAD", "GBP_CAD", "AUD_CHF"]
TREND = ["NZD_CAD", "GBP_NZD"]
class H15(unittest.TestCase):
    def setUp(self):
        self.m = m = load(); self.tmp = tempfile.mkdtemp(); st = os.path.join(self.tmp, "state", "oanda"); os.makedirs(st)
        m.ST = st; m.LOG = os.path.join(st, "trades.jsonl"); m.CFG = os.path.join(st, "nocfg.json")
        def no_network(*a, **k): raise AssertionError("unexpected OANDA API call")
        m.api = no_network; m.behaviour_ok = lambda *a, **k: True
        m.summary = lambda: {"NAV": "50000", "marginUsed": "0", "currency": "AUD"}
        self.open = []; m.open_trades = lambda: list(self.open); m.pricing = lambda insts: {}
        m.ledger_entry_risk = lambda: {str(i): dict(risk_aud=0.001 * 50000) for i in range(12)}
    def tearDown(self): shutil.rmtree(self.tmp)
    def e(self, ins, design): return dict(design=design, ins=ins, dir=1, risk_pct=0.005, stop_dist=0.01, floor_sp=1e-4, close=1.0)
    def test_rail_is_4_and_cannot_be_raised(self):
        self.assertEqual(self.m.cfg()["total_max_trades"], 4)
        json.dump({"total_max_trades": 25}, open(self.m.CFG, "w")); self.assertEqual(self.m.cfg()["total_max_trades"], 4)
    def test_four_across_buckets_blocks_every_bucket(self):
        self.open = [T(i, p, "OXL2") for i, p in enumerate(BOOK[:2])] + [T(10 + i, p, "OXF1") for i, p in enumerate(TREND)]   # 2 book + 2 trend = 4
        self.assertEqual(self.m.place_entry(self.e("EUR_JPY", "OXL2"), False), "skip:total_max_trades")
        self.assertEqual(self.m.place_entry(self.e("EUR_JPY", "OXF1"), False), "skip:total_max_trades")
    def test_three_open_still_allows(self):
        self.open = [T(i, p, "OXL2") for i, p in enumerate(BOOK[:2])] + [T(10, TREND[0], "OXF1")]   # 3 open
        self.assertEqual(self.m.place_entry(self.e("EUR_JPY", "OXF1"), False), "defer:not_tradeable")   # passed the count gates
if __name__ == "__main__": unittest.main()
