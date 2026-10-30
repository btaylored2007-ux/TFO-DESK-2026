import os, sys, unittest, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import event_calendar as E
def row(**k):
    b = dict(sym="ABC.AX", venue="asx", px="1.50", atr="0.06", atr_pct="4.0", rsi="50", roc5="0", adv_aud="6000000", adv_usd=str(6e6*0.7022),
             lo10="1.45", pdl="1.46", hi20="1.60", pdh="1.55", asx_shortable="True", excluded="False", country="Australia")
    b.update(k); return b
class T(unittest.TestCase):
    def test_floor_aud_asx(self):
        self.assertFalse([x for x in E.gate(row(), "long") if "ADV" in x])
        self.assertFalse([x for x in E.gate(row(adv_usd=str(4e6*0.7022)), "long") if "ADV" in x])   # ASX floor A$2m (27 Oct 04:50 WITA)
        self.assertFalse([x for x in E.gate(row(adv_usd=str(2.1e6*0.7022)), "long") if "ADV" in x])
        self.assertTrue([x for x in E.gate(row(adv_usd=str(1.9e6*0.7022)), "long") if "ADV" in x])
    def test_asx_floor_does_not_leak_to_us(self):
        us = dict(sym="XYZ", venue="ibkr", px="10", atr_pct="3", rsi="50", roc5="0", excluded="False")
        self.assertTrue([x for x in E.gate(dict(us, adv_usd=str(3e6*0.7022)), "long") if "ADV" in x])   # A$3m US name still blocked at A$5m
    def test_floor_aud_us(self):
        us = dict(sym="XYZ", venue="ibkr", px="10", atr_pct="3", rsi="50", roc5="0", excluded="False")
        self.assertFalse([x for x in E.gate(dict(us, adv_usd="3600000"), "long") if "ADV" in x])   # US$3.6M > A$5m*0.7022
        self.assertTrue([x for x in E.gate(dict(us, adv_usd="3400000"), "long") if "ADV" in x])
    def test_short_list(self):
        self.assertTrue(any("short not permitted" in x for x in E.gate(row(asx_shortable="False"), "short")))
        self.assertFalse(any("short not permitted" in x for x in E.gate(row(asx_shortable="False"), "long")))
    def test_tick_cost_gate(self):
        self.assertTrue(any("tick" in x for x in E.gate(row(px="0.15", atr_pct="3"), "long")))   # 0.5c tick on 15c = 3.3%
    def test_stage_cost_aud(self):
        lv = E.stage_levels(row(), "long", 2500, 0.7022)
        self.assertGreater(lv["shares"], 0); self.assertAlmostEqual(lv["notional_usd"] / 0.7022 / lv["shares"], lv["max_fill"], places=1)
        self.assertGreater(lv["est_cost_usd"], 2 * 10 * 0.7022)
    def test_indonesian_excluded(self):
        self.assertTrue(E.is_indonesian(dict(sym="BBCA.JK")))
if __name__ == "__main__": unittest.main()
