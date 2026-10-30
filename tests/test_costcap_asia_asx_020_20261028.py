import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asx_hunt48 as A, asia_hunt48 as S, event_calendar as EC, hunt48_room as HR
class CostCap(unittest.TestCase):
    def test_asx(self): self.assertEqual(A.COST_R_MAX, 0.20); self.assertEqual(EC.ASX_COST_R_MAX, 0.20)
    def test_asia(self):
        for m in ("jp", "hk", "sg", "kr", "tw"): self.assertEqual(S.cost_R_cap(m), 0.20)
    def test_london_us_unchanged(self): self.assertEqual(S.cost_R_cap("lse"), 0.30); self.assertEqual(HR.US_MAX_COST_R, 0.15)
    def test_asx_gate_018_passes(self):
        # px 3.22, tick 0.005 -> cp 0.355%; px 3.22 tick 0.01 -> cp 0.51%; atr 1.87% -> stop 2.8% -> 0.18R : blocked at 0.15, passes at 0.20
        self.assertFalse([w for w in EC.asx_gate(dict(px=3.22, atr_pct=1.87, asx_shortable="True"), "long") if "slippage" in w])
if __name__ == "__main__": unittest.main()
