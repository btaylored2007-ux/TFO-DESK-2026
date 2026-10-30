"""2026-10-28 Brett 09:59 change 2: relative-strength longs in short-regime books where shorts are blocked. PAPER only."""
import datetime as dt, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asia_hunt48 as AH

import importlib.util as _iu
_spec = _iu.spec_from_file_location("_ah_fresh_rs", AH.__file__); _AHF = _iu.module_from_spec(_spec); _spec.loader.exec_module(_AHF)   # private copy: immune to other tests stubbing AH.review
NOW = dt.datetime(2026, 10, 28, 2, 0, tzinfo=dt.timezone.utc)

def row(mkt, sym, rs20, score=4.0, short_score=4.0, dir_="short", shortable=False):
    r = dict(sym=sym, cls=f"{mkt}_equity", dir=dir_, score=score, short_score=short_score, rank_score=(score if dir_ == "long" else short_score),
             rs20=rs20, adv_aud=5e7, sector="S" + sym, shortable=shortable, short_src="x", px=10.0, rsi14=50)
    return r

class T(unittest.TestCase):
    def setUp(self):
        self._gate = _AHF.EC.gate
        _AHF.regime = lambda m, rows: "short"; _AHF.EC.gate = lambda r, d, max_atr_pct=None: []
    def tearDown(self):
        _AHF.EC.gate = self._gate
    def rev(self, mkt, rows):
        return {v["sym"]: v for v in _AHF.review(mkt, rows, {}, set(), NOW, NOW + dt.timedelta(days=3))[1]}
    def test_long_only_market_rs_long_passes_and_tagged(self):
        R = self.rev("kr", {"A": row("kr", "A", 8.0, dir_="long")})
        self.assertTrue(R["A"]["passed"], R["A"]["why"]); self.assertEqual(R["A"]["dir"], "long")
        self.assertEqual(R["A"]["strategy"], "rs_long_in_short_regime")
    def test_weak_rs_blocked(self):
        R = self.rev("sg", {"A": row("sg", "A", 2.0, dir_="long")})
        self.assertFalse(R["A"]["passed"]); self.assertIn("RS20", " ".join(R["A"]["why"]))
    def test_missing_rs_fails_closed(self):
        R = self.rev("tw", {"A": row("tw", "A", None, dir_="long")})
        self.assertFalse(R["A"]["passed"])
    def test_pump_chase_guard(self):
        R = self.rev("kr", {"A": row("kr", "A", 40.0, dir_="long")})
        self.assertFalse(R["A"]["passed"]); self.assertIn("pump", " ".join(R["A"]["why"]))
    def test_low_long_score_blocked(self):
        R = self.rev("kr", {"A": row("kr", "A", 8.0, score=2.0, dir_="long")})
        self.assertFalse(R["A"]["passed"])
    def test_shortable_market_with_empty_list_uses_long_side(self):
        R = self.rev("jp", {"A": row("jp", "A", 9.0, score=4.5, short_score=1.0)})
        self.assertTrue(R["A"]["passed"], R["A"]["why"]); self.assertEqual(R["A"]["dir"], "long"); self.assertEqual(R["A"]["score"], 4.5)
    def test_shortable_names_exist_no_rs_mode(self):
        rows = {"A": row("jp", "A", 9.0), "B": row("jp", "B", -9.0, shortable=True)}
        R = self.rev("jp", rows)
        self.assertIsNone(R["A"]["strategy"]); self.assertEqual(R["A"]["dir"], "short")
    def test_long_regime_untouched(self):
        _AHF.regime = lambda m, rows: "long"
        R = self.rev("kr", {"A": row("kr", "A", 1.0, dir_="long")})
        self.assertTrue(R["A"]["passed"]); self.assertIsNone(R["A"]["strategy"])

    def test_rs_max_30_allows_26(self):
        # 2026-10-28 14:58 funnel: RS_LONG_MAX raised 25 -> 30 so KR band is not empty
        self.assertEqual(_AHF.RS_LONG_MAX_RS20, 30.0)
        R = self.rev("kr", {"A": row("kr", "A", 26.0, dir_="long")})
        self.assertTrue(R["A"]["passed"], R["A"]["why"])
    def test_rs_mode_passes_atr_cap_8(self):
        seen = {}
        def g(r, d, max_atr_pct=None):
            seen["cap"] = max_atr_pct; return []
        _AHF.EC.gate = g
        self.rev("kr", {"A": row("kr", "A", 8.0, dir_="long")})
        self.assertEqual(seen.get("cap"), 8.0)
    def test_same_shape_and_risk(self):
        self.assertEqual(_AHF.RISK_AUD, 2500.0); self.assertEqual((_AHF.HR.FIXED_TP_PCT, AH.HR.FIXED_STOP_PCT), (0.03, 0.08)); self.assertEqual(_AHF.MAX_OPEN, 3)

class Tracker(unittest.TestCase):
    def test_rs_row_present(self):
        import strategy_tracker as S
        r = S.rs_long_row(); self.assertIn("rs_long_in_short_regime", r["strategy"])
    def test_warroom_counts(self):
        import warroom_markets as W
        led = {"trades": [dict(strategy="rs_long_in_short_regime", status="BENCH"), dict(status="BENCH")]}
        self.assertEqual(W.rs_long_info(led), {"BENCH": 1})

if __name__ == "__main__": unittest.main()
