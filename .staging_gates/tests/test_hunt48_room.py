import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hunt48_room as H
def flat(n=60, px=10.0): return [dict(h=px + 0.1, l=px - 0.1, c=px) for _ in range(n)]
def short_t(cost=0.03, t2=8.9): return dict(direction="short", max_fill=10.0, stop=11.0, t2=t2, cost_R=cost)   # R=1.0
class T(unittest.TestCase):
    def test_fail_open_missing_bars_benches_and_writes_number(self):
        t = short_t(); why = H.room(t, [])
        self.assertIn("no daily bars", why); self.assertIsNotNone(t["room_R"]); self.assertIsInstance(t["room_R"], float)
    def test_missing_cost_or_levels_benches(self):
        for k in ("cost_R", "t2", "stop"):
            t = short_t(); t[k] = None; self.assertIn("uncomputable", H.room(t, flat())); self.assertEqual(t["room_R"], 0.0)
    def test_no_structure_uses_T2(self):
        bars = [dict(h=12 + i * 0.01, l=11.5 + i * 0.01, c=11.8 + i * 0.01) for i in range(60)]   # every level ABOVE a 10.0 short entry
        t = short_t(cost=0.05, t2=8.9); self.assertIsNone(H.room(t, bars)); self.assertAlmostEqual(t["room_R"], 1.05, 3); self.assertIn("no structure", t["room_basis"])
        t = short_t(cost=0.09, t2=8.95); self.assertIn("to T2", H.room(t, bars)); self.assertAlmostEqual(t["room_R"], 0.96, 3)   # NEU.AX-type 0.96R -> BENCH
    def test_min_structure_T2(self):
        bars = flat(60, 12.0); bars[30] = dict(h=9.6, l=9.5, c=9.55)                                # swing low 9.5 = 0.5R below entry
        t = short_t(cost=0.0, t2=8.0); self.assertIn("support", H.room(t, bars)); self.assertAlmostEqual(t["room_R"], 0.5, 3)
        t = short_t(cost=0.0, t2=9.0); bars[30] = dict(h=7.1, l=7.0, c=7.05)                       # structure 3R away, T2 1.0R binds
        self.assertIsNone(H.room(t, bars)); self.assertAlmostEqual(t["room_R"], 1.0, 3); self.assertIn("T2 binds", t["room_basis"])
    def test_long_side(self):
        t = dict(direction="long", max_fill=10.0, stop=9.0, t2=11.2, cost_R=0.1)
        self.assertIsNone(H.room(t, [dict(h=9.5, l=9.0, c=9.2)] * 60)); self.assertAlmostEqual(t["room_R"], 1.1, 3)
    def test_b1_rsi(self):
        self.assertIn("RSI 25", H.short_screen(dict(rsi=25, roc5=-1, atr_pct=3, px=10), "X", lambda s: flat())[0])
        self.assertIn("RSI missing", H.short_screen(dict(rsi="", roc5=-1, atr_pct=3, px=10), "X", lambda s: flat())[0])
        self.assertIn("RSI missing", H.short_screen(dict(roc5=-1, atr_pct=3, px=10), "X", lambda s: flat())[0])
        self.assertEqual(H.short_screen(dict(rsi=35, roc5=-1, atr_pct=3, px=10), "X", lambda s: flat()), [])
    def test_b1_true_20d_low_exhaustion(self):
        bars = [dict(h=12, l=11, c=11.5)] * 40 + [dict(h=10.2, l=9.9, c=10.0)] * 20                # lo10 proxy irrelevant: true 20d low of closes = 10.0
        self.assertIn("exhausted low", H.short_screen(dict(rsi=35, roc5=-6, atr_pct=3, px=10.05), "X", lambda s: bars)[0])   # 2.0 ATR drop at the low
        self.assertEqual(H.short_screen(dict(rsi=35, roc5=-3, atr_pct=3, px=10.05), "X", lambda s: bars), [])                # 1.0 ATR: ok
        self.assertEqual(H.short_screen(dict(rsi=35, roc5=-6, atr_pct=3, px=10.5), "X", lambda s: bars), [])                 # 5% above the low: ok
        self.assertIn("missing", H.short_screen(dict(rsi=35, roc5=-6, atr_pct=3, px=10.05), "X", lambda s: [])[0])          # no bars -> bench
    def test_lookback_1y(self): self.assertEqual(H.LOOKBACK, "1y"); self.assertEqual(H.MIN_ROOM_R, 1.0)
if __name__ == "__main__": unittest.main()
