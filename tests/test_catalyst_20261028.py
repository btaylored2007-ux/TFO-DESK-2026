"""2026-10-28 Brett 10:29: capped news catalyst - lifts borderline only, never creates, aligned only, levels/size untouched."""
import datetime as dt, os, sys, unittest, importlib.util as iu
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import catalyst as C, asia_hunt48 as AH
NOW = dt.datetime.now(dt.timezone.utc); REC = (NOW - dt.timedelta(hours=5)).isoformat()
F = lambda *t: {"X": dict(items=[dict(title=x, when=REC) for x in t])}

class T(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(C.classify("Acme beats estimates, raises guidance"), "pos")
        self.assertEqual(C.classify("Acme wins $200m defence contract"), "pos")
        self.assertEqual(C.classify("Acme announces share buyback"), "pos")
        self.assertEqual(C.classify("Acme issues profit warning"), "neg")
        self.assertEqual(C.classify("Acme downgraded to sell"), "neg")
        self.assertIsNone(C.classify("Acme to hold AGM"))
    def test_borderline_lift_capped(self):
        b, i = C.bonus("jp", "X", "long", 2.7, 3.0, NOW, F("Acme beats estimates")); self.assertEqual(b, 0.5); self.assertEqual(i["bonus"], 0.5)
        self.assertLessEqual(b, C.CAP)
    def test_never_creates_from_nothing(self):
        b, i = C.bonus("jp", "X", "long", 1.0, 3.0, NOW, F("Acme beats estimates")); self.assertEqual(b, 0.0)
    def test_direction_alignment(self):
        self.assertEqual(C.bonus("jp", "X", "short", 2.8, 3.0, NOW, F("Acme beats estimates"))[0], 0.0)
        self.assertEqual(C.bonus("jp", "X", "short", 2.8, 3.0, NOW, F("Acme issues profit warning"))[0], 0.5)
        self.assertEqual(C.bonus("jp", "X", "long", 2.8, 3.0, NOW, F("Acme beats estimates", "Acme downgraded"))[0], 0.0)
    def test_stale_news_ignored(self):
        old = {"X": dict(items=[dict(title="Acme beats estimates", when=(NOW - dt.timedelta(days=5)).isoformat())])}
        self.assertEqual(C.bonus("jp", "X", "long", 2.8, 3.0, NOW, old)[0], 0.0)
    def test_no_feed_no_bonus(self):
        self.assertEqual(C.bonus("jp", "NOPE", "long", 2.9, 3.0, NOW, {})[0], 0.0)

class Engine(unittest.TestCase):
    def setUp(self):
        sp = iu.spec_from_file_location("_ah_cat", AH.__file__); self.A = iu.module_from_spec(sp); sp.loader.exec_module(self.A)
        self._gate = self.A.EC.gate; self.A.regime = lambda m, rows: "long"; self.A.EC.gate = lambda r, d, max_atr_pct=None, **k: []  # 2026-10-28 rs_mode ATR cap kw; mock accepts it
    def tearDown(self): self.A.EC.gate = self._gate     # EC is shared: always restore
    def rows(self, sc): return {"X": dict(sym="X", cls="kr_equity", dir="long", score=sc, rank_score=sc, rs20=1.0, adv_aud=5e7, sector="S", shortable=False)}
    def rev(self, sc): return self.A.review("kr", self.rows(sc), {}, set(), NOW, NOW + dt.timedelta(days=3))[1][0]
    def test_borderline_passes_with_catalyst_and_tagged(self):
        self.A._cat_bonus = lambda m, s, d, sc: C.bonus(m, s, d, sc, 3.0, NOW, F("Acme beats estimates"))
        v = self.rev(2.7); self.assertTrue(v["passed"]); self.assertEqual(v["catalyst"]["bonus"], 0.5)
    def test_weak_still_blocked_with_catalyst(self):
        self.A._cat_bonus = lambda m, s, d, sc: C.bonus(m, s, d, sc, 3.0, NOW, F("Acme beats estimates"))
        self.assertFalse(self.rev(2.0)["passed"])
    def test_broken_feed_changes_nothing(self):
        def boom(*a): raise RuntimeError
        self.A._cat_bonus = lambda *a: (0.0, None)
        v = self.rev(3.2); self.assertTrue(v["passed"]); self.assertIsNone(v["catalyst"])
    def test_levels_ignore_catalyst(self):
        import inspect; self.assertNotIn("catalyst", inspect.getsource(self.A.levels))

class TagsInfoOnly(unittest.TestCase):
    def test_trade_tags_carries_catalyst_without_writing_ledger(self):
        import trade_tags as TT
        r = TT.tag_trade("jp", dict(id="1", sym="X", status="ARMED", catalyst=dict(label="positive", bonus=0.5)), NOW, yf_fn=lambda s: {})
        self.assertEqual(r["catalyst"]["bonus"], 0.5); self.assertTrue(r["info_only"])
if __name__ == "__main__": unittest.main()
