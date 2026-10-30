#!/usr/bin/env python3
"""2026-10-29 14:12 WITA TFO head-trader (PAPER): ASX/JP/HK/KR (+ SG/TW from 14:23) hunt48 books use R-based targets (no fixed +3%/-8%).
Asserts: (1) T2 sits >= 1.0R from max_fill, measured in the stop distance R; (2) levels are NOT the fixed +3%/-8% shape;
(3) hunt48_room.room() measures room in that real R (room_t2_R == (t2-max_fill)/R, no fixed-rule note), MIN_ROOM_R still 1.0;
(4) 14:23 WITA TFO: sg + tw now R-based too; scope: lse keeps the fixed +3%/-8% levels (non-vacuous). Python 3.9 compatible. No network, no ledger writes."""
import os, sys, unittest
sys.path.insert(0, os.environ.get("TEST_CODE_DIR") or os.path.dirname(os.path.abspath(__file__)))
import hunt48_room as HR, asx_hunt48 as AX, asia_hunt48 as AH

def row(sym, venue, px=10.0, atr=0.25, **kw):
    r = dict(sym=sym, venue=venue, px=px, atr=atr, hi20=px, pdh=px, lo10=px * 0.95, pdl=px * 0.98, adv_aud=50e6, adv_usd=33e6,
             fx_aud=1.0, lot=1, cls=venue + "_equity")
    r.update(kw); return r

def flat_bars(px, n=260):   # no opposing structure above/below -> room = T2 room - cost
    return [dict(h=px * 0.5, l=px * 0.49, c=px * 0.495) for _ in range(n)]

class RBased(unittest.TestCase):
    def check_rbased(self, lv, direction="long"):
        self.assertIsNotNone(lv)
        mf, st, t2 = lv["max_fill"], lv["stop"], lv["t2"]
        R = abs(mf - st); self.assertGreater(R, 0)
        self.assertGreaterEqual(abs(t2 - mf) / R, 1.0 - 1e-6, "T2 must sit >= 1.0R from max_fill (R = stop distance)")
        self.assertAlmostEqual(abs(t2 - mf) / R, HR.T2_R, places=2)
        self.assertFalse(HR.is_fixed(mf, st, t2), "levels must not be the fixed +3%/-8% shape")
        t = dict(direction=direction, max_fill=mf, stop=st, t2=t2, cost_R=0.05)
        why = HR.room(t, flat_bars(mf) if direction == "long" else [dict(h=mf * 2, l=mf * 1.99, c=mf * 1.995)] * 260)
        self.assertAlmostEqual(t["room_t2_R"], abs(t2 - mf) / R, places=2)   # room is in REAL R
        self.assertNotIn("room_rule", t)                                      # no fixed-rule path / exemption
        self.assertAlmostEqual(t["room_R"], round(abs(t2 - mf) / R - 0.05, 3), places=2)
        self.assertIsNone(why)                                                # 1.5R - 0.05 >= 1.0R passes
        t2b = dict(t, cost_R=0.6); self.assertIsNotNone(HR.room(t2b, flat_bars(mf)))   # 1.5 - 0.6 < 1.0 -> still BENCH
        return lv

    def test_min_room_unchanged(self):
        self.assertEqual(HR.MIN_ROOM_R, 1.0)

    def test_asx_long(self):
        self.check_rbased(AX.levels(row("TST", "asx"), "long"))

    def test_asia_rbased_markets(self):
        for m in ("jp", "hk", "kr", "sg", "tw"):
            with self.subTest(m=m):
                self.check_rbased(AH.levels(m, row("TST." + m, m), "long"))

    def test_sg_tw_now_rbased(self):   # 14:23 WITA TFO: Brett's decision covers all Asia
        self.assertIn("sg", AH.R_BASED_MKTS); self.assertIn("tw", AH.R_BASED_MKTS)
        for m in ("sg", "tw"):
            with self.subTest(m=m):
                self.check_rbased(AH.levels(m, row("TST." + m, m), "long"))

    def test_lse_now_rbased(self):   # 2026-10-29 16:08 WITA: Brett 16:05 switched LSE to R-based
        self.assertIn("lse", AH.R_BASED_MKTS)
        self.check_rbased(AH.levels("lse", row("TST.L", "lse"), "long"))

    def test_us_now_rbased(self):   # US orchestrator path: R-based with 1-ATR floor kept
        import hunt_orchestrator as HO, inspect
        src = inspect.getsource(HO.build_candidate)
        self.assertIn("fixed_pct=None", src); self.assertIn("atr_floor=(EC.STOP_ATR_FLOOR if us_room else None)", src)
        import event_calendar as EC
        r = dict(sym="TST", venue="us", px=100.0, atr=2.0, hi20=100.0, pdh=100.0, lo10=95.0, pdl=98.0, adv_usd=5e8, adv_aud=7e8, fx_aud=1.0, lot=1, cls="us_equity")
        lv = EC.stage_levels(r, "long", 2500.0, 0.66, t2_r=HR.T2_R, atr_floor=EC.STOP_ATR_FLOOR, fixed_pct=None)
        self.check_rbased(lv)

    def test_us_short_books_keep_staged_levels(self):   # main + gpt no longer forced to fixed; claude (retired) untouched
        import hunt48_short_paper as SP, inspect
        self.assertIn('if BOOK == "claude": sp = fixed_levels', inspect.getsource(SP.apply_plan))

if __name__ == "__main__":
    unittest.main(verbosity=2)
