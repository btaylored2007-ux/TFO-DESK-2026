"""2026-10-28 Brett 10:37: US hunt scanner gets the same capped catalyst bonus."""
import datetime as dt, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hunt_orchestrator as HO
NOW = dt.datetime.now(dt.timezone.utc); REC = (NOW - dt.timedelta(hours=3)).isoformat()
F = lambda t: {"AAA": dict(items=[dict(title=t, when=REC)])}
class T(unittest.TestCase):
    def c(self, d="long", flag=False): return dict(ticker="AAA", direction=d, score=3.0, catalyst_flag=flag, shares=100, stop=9.0)
    def test_aligned_long(self):
        c = self.c(); self.assertEqual(HO.us_news_lift(c, F("AAA beats estimates"), NOW), 0.5); self.assertEqual(c["score"], 3.5); self.assertEqual(c["catalyst"]["bonus"], 0.5)
    def test_aligned_short(self):
        c = self.c("short"); self.assertEqual(HO.us_news_lift(c, F("AAA cuts guidance"), NOW), 0.5)
    def test_misaligned_none(self):
        c = self.c("short"); self.assertEqual(HO.us_news_lift(c, F("AAA beats estimates"), NOW), 0.0); self.assertEqual(c["score"], 3.0)
    def test_no_stacking_with_capture_flag(self):
        c = self.c(flag=True); HO.us_news_lift(c, F("AAA beats estimates"), NOW); self.assertEqual(c["score"], 3.5)
        c = self.c(flag=True); HO.us_news_lift(c, {}, NOW); self.assertEqual(c["score"], 3.25)
    def test_size_and_stop_untouched(self):
        c = self.c(); HO.us_news_lift(c, F("AAA wins contract award"), NOW); self.assertEqual((c["shares"], c["stop"]), (100, 9.0))
if __name__ == "__main__": unittest.main()
