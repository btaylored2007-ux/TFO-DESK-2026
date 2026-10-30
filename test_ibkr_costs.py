import datetime as dt, unittest, sys
sys.argv = ["x"]
import ibkr_costs as IC, hunt48_short_paper as H
class T(unittest.TestCase):
    def test_fixed_us(self):
        self.assertAlmostEqual(IC.us_commission(100, 25), 1.0)          # IBKR page example
        self.assertAlmostEqual(IC.us_commission(1000, 25), 5.0)
        self.assertAlmostEqual(IC.us_commission(1000, 0.25), 2.5)
        self.assertAlmostEqual(IC.us_commission(10, 0.20), 0.02)         # 1% cap beats $1 min
        d = dt.date(2026, 10, 27)
        self.assertAlmostEqual(IC.us_reg(1000, 25, "sell", d), 0.0000206 * 25000 + 0.003)  # TAF holiday
        self.assertAlmostEqual(IC.us_reg(1000, 25, "sell", dt.date(2027, 1, 5)), 0.0000206 * 25000 + 0.003 + 0.195)
        self.assertAlmostEqual(IC.us_reg(1000, 25, "buy", d), 0.003)
    def test_asx(self):
        self.assertAlmostEqual(IC.asx_side_aud(400, 50), 16.0)           # IBKR page example
        self.assertAlmostEqual(IC.asx_side_aud(100, 10), 6.0)
    def test_no_borrow_shadow(self):
        now = dt.datetime(2026, 10, 27, 15, 0, tzinfo=dt.timezone.utc); s = dt.datetime(2026, 10, 27, 14, 0, tzinfo=dt.timezone.utc)
        t = dict(ticker="ZZZNOBORROW", direction="short", status="ARMED", armed_utc=(s - dt.timedelta(hours=1)).isoformat(), trigger=10, max_fill=9.9,
                 stop=11, shares=100, fees_usd=0.0, entry=None, open_qty=0)
        orig = IC.borrow; IC.borrow = lambda sym, venue=None: dict(fee_pa=None, available=0, ok=False)
        try: k = H.try_fill(t, [dict(start=s, o=10.1, h=10.1, l=9.95, c=9.96)], now, 0.75)
        finally: IC.borrow = orig
        self.assertEqual((k, t["status"], t["shadow_reason"], t["fees_usd"]), ("shadow", "SHADOW", "no borrow", 0.0))
if __name__ == "__main__": unittest.main()
