import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hunt48_room as HR, asx_hunt48 as A
class R(unittest.TestCase):
    def bars(self, hi): return [dict(o=10, h=hi, l=9.5, c=10) for _ in range(260)]
    def test_fixed_rule_not_1R(self):
        t = dict(direction="long", max_fill=10.0, stop=9.2, t2=10.3, cost_R=0.1)
        # 2026-10-29 teststale: fixed levels may set the levels but room is still measured in R; 0.375R T2 - cost < MIN_ROOM_R 1.0 -> BENCH
        self.assertIn("< 1.0R", HR.room(t, self.bars(10.0))); self.assertIn("room still >= 1.0R", t["room_rule"]); self.assertEqual(HR.MIN_ROOM_R, 1.0)
    def test_sr_inside_target_blocks(self):
        b = self.bars(10.0); b[100] = dict(o=10, h=10.15, l=9.5, c=10)
        self.assertIn("before +3% target", HR.room(dict(direction="long", max_fill=10.0, stop=9.2, t2=10.3, cost_R=0.1), b))
    def test_cost_eats_target(self):
        t = dict(direction="long", max_fill=10.0, stop=9.2, t2=10.3, cost_R=0.5)   # 2026-10-29: cost eats room -> negative room_R, BENCH
        self.assertIn("< 1.0R", HR.room(t, self.bars(10.0))); self.assertLess(t["room_R"], 0)
    def test_asx_legacy_bench_expired(self):
        self.assertIn("legacy levels", open(A.__file__).read()); self.assertEqual(A.MIN_ROOM_R, 1.0)   # 2026-10-29: R-based room, MIN_ROOM_R 1.0
if __name__ == "__main__": unittest.main()
