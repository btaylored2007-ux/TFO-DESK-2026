"""2026-10-28 Brett 10:28: news-risk + quality tags are INFO ONLY - ledgers (arming, timing, size) untouched when tags fire."""
import datetime as dt, json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import trade_tags as TT
NOW = dt.datetime(2026, 10, 28, 2, 0, tzinfo=dt.timezone.utc)
BAD = lambda s: dict(earnings=["2026-10-29"], news=[dict(title="Company enters trading halt pending takeover proposal", when="2026-10-28T00:00:00Z")], de=3.5, cover=1.2, ocf=-5.0)
NONE = lambda s: dict(earnings=None, news=[], de=None, cover=None, ocf=None)

class T(unittest.TestCase):
    def setUp(self):
        self.old = (TT.BASE, TT.OUT, TT.TAGS, TT.LOG); d = tempfile.mkdtemp()
        TT.BASE = d; TT.OUT = os.path.join(d, "state", "trade_tags"); TT.TAGS = os.path.join(TT.OUT, "tags.json"); TT.LOG = os.path.join(TT.OUT, "trades.jsonl")
        os.makedirs(os.path.join(d, "state", "hunt48_jp")); self.lp = os.path.join(d, "state", "hunt48_jp", "ledger.json")
        json.dump(dict(trades=[dict(id="1", sym="9147.T", status="ARMED", direction="long", shares=500, armed_utc="2026-10-28T02:22:00+00:00", trigger=6039.5)]), open(self.lp, "w"))
        self.before = open(self.lp, "rb").read()
    def tearDown(self): TT.BASE, TT.OUT, TT.TAGS, TT.LOG = self.old
    def test_flags_fire_ledger_unchanged(self):
        R = TT.run(NOW, yf_fn=BAD)["jp:9147.T"]
        self.assertTrue(R["news_flag"]); self.assertTrue(R["quality_flag"]); self.assertTrue(R["info_only"])
        self.assertTrue(any("earnings" in x for x in R["news_risk"])); self.assertTrue(any("halt" in x or "takeover" in x for x in R["news_risk"]))
        self.assertEqual(open(self.lp, "rb").read(), self.before)          # status, armed time, shares, trigger all identical
        self.assertEqual(len(open(TT.LOG).read().splitlines()), 1)
    def test_missing_data_is_na(self):
        q = TT.run(NOW, yf_fn=NONE)["jp:9147.T"]["quality"]
        self.assertEqual((q["verdict"], q["de"], q["interest_cover"], q["ocf_positive"]), ("n/a", "n/a", "n/a", "n/a"))
        self.assertEqual(open(self.lp, "rb").read(), self.before)
    def test_yf_failure_never_raises(self):
        def boom(s): raise RuntimeError("feed down")
        R = TT.run(NOW, yf_fn=boom)["jp:9147.T"]; self.assertIn("error", R)
        self.assertEqual(open(self.lp, "rb").read(), self.before)
    def test_engines_do_not_import_tags(self):
        for f in ("asia_hunt48.py", "asx_hunt48.py", "hunt48_short_paper.py", "intraday_rescan.py"):
            p = os.path.join(os.path.dirname(TT.__file__), f)
            if os.path.exists(p): self.assertNotIn("import trade_tags", open(p).read(), f)
if __name__ == "__main__": unittest.main()
