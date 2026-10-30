"""Scan-cleanup data sanity guards (Brett 2026-10-28 04:45 WITA). PAPER only."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hunt_orchestrator as HO

class ScanSanity(unittest.TestCase):
    def test_atr_blowup_rejected(self):   # SXTC 27 Oct: ATR% 2300
        self.assertTrue(any("ATR%" in w for w in HO.sanity_guard(dict(px="3.54", atr_pct="2300.57", venue="ibkr"))))
    def test_atr_uncomputable_rejected(self):
        for a in ("", "nan", "0", None):
            self.assertIn("sanity: ATR% uncomputable", HO.sanity_guard(dict(px="50", atr_pct=a, venue="ibkr")))
    def test_sub_5_rejected(self):        # FMC 4.61, INTR 4.68
        for px in ("4.61", "4.68"):
            self.assertTrue(any("< $5" in w for w in HO.sanity_guard(dict(px=px, atr_pct="12", venue="ibkr"))))
    def test_clean_row_passes(self):
        self.assertEqual(HO.sanity_guard(dict(px="85.5", atr_pct="13.0", venue="ibkr")), [])
        self.assertEqual(HO.sanity_guard(dict(px="5.0", atr_pct="25.0", venue="ibkr")), [])
    def test_trigger_distance(self):
        self.assertEqual(HO.trigger_sanity(88.4553, 87.085), [])            # KNSA manual stage: 1.6%
        self.assertTrue(HO.trigger_sanity(70.0, 85.5))                       # >15% away
        self.assertTrue(HO.trigger_sanity(None, 85.5))
    def test_quick_screen_wires_guard(self):
        why = HO.quick_screen(dict(sym="SXTC", px="3.54", atr_pct="2300", venue="ibkr", dir="short"), "short")
        self.assertTrue(any(w.startswith("sanity:") for w in why))

if __name__ == "__main__": unittest.main()
