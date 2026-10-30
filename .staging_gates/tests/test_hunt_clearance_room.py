import os, sys, unittest, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.argv = [sys.argv[0]]
import hunt_clearance as HC, hunt48_room as HR
def b(venue="asx", rsi="45"): return dict(asset_class="equity", yf_symbol="TST.AX", sweep=dict(venue=venue, rsi=rsi, roc5="1", atr_pct="3", adv_aud="5e7"), technical={}, liquidity={}, calendar={})
def s(cost_R=0.03, t2=11.6): return dict(ticker="TST.AX", direction="long", trigger=10.0, max_fill=10.0, stop=9.0, t1=10.45, t2=t2, shares=2500, atr=0.8, cost_R=cost_R)
class T(unittest.TestCase):
    def setUp(self): HR._CACHE.clear()
    def test_specialist_uses_single_room_and_fails_closed(self):
        HR.daily = lambda sym: [dict(h=9.5, l=9.0, c=9.2)] * 60                      # no resistance above -> T2 room
        v = HC.specialist_review(s(), b()); self.assertAlmostEqual(v["room_R"], 1.57, 2); self.assertFalse(any("ROOM GATE" in r for r in v["reasons"]))
        v = HC.specialist_review(s(cost_R=0.09, t2=11.05), b()); self.assertEqual(v["verdict"], "FAIL"); self.assertTrue(any("ROOM GATE" in r and "to T2" in r for r in v["reasons"]))
        HR.daily = lambda sym: []                                                       # missing daily bars -> FAIL
        v = HC.specialist_review(s(), b()); self.assertEqual(v["verdict"], "FAIL"); self.assertTrue(any("no daily bars" in r for r in v["reasons"]))
        HR.daily = lambda sym: [dict(h=9.5, l=9.0, c=9.2)] * 60
        v = HC.specialist_review(s(cost_R=None), b()); self.assertEqual(v["verdict"], "FAIL"); self.assertIsNotNone(v["room_R"])   # no cost_R -> FAIL
    def test_specialist_structure_binds(self):
        bars = [dict(h=9.5, l=9.0, c=9.2)] * 60; bars[30] = dict(h=10.5, l=10.3, c=10.4); HR.daily = lambda sym: bars   # resistance 0.5R above
        v = HC.specialist_review(s(), b()); self.assertEqual(v["verdict"], "FAIL"); self.assertAlmostEqual(v["room_R"], 0.47, 2)
    def test_behavior_missing_rsi_short_fails_for_hunt48(self):
        sh = dict(s(), direction="short", stop=11.0, t1=9.55, t2=8.4)
        v = HC.behavior_review(sh, b(rsi=None), dt.datetime.now(dt.timezone.utc)); self.assertEqual(v["verdict"], "FAIL"); self.assertTrue(any("RSI UNKNOWN" in r for r in v["reasons"]))
        v = HC.behavior_review(sh, b(rsi="25"), dt.datetime.now(dt.timezone.utc)); self.assertEqual(v["verdict"], "FAIL")
    def test_us_books_unchanged(self):
        v = HC.specialist_review(dict(s(), ticker="TST"), dict(b(venue="ibkr"), yf_symbol="TST")); self.assertIsNone(v["room_R"])
if __name__ == "__main__": unittest.main()
