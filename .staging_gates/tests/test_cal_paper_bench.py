import datetime as dt, os, sys, tempfile, json, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cal_paper_bench as B
UTC = dt.timezone.utc; T0 = dt.datetime(2026, 10, 27, 0, 0, tzinfo=UTC)
def tr(**k):
    d = dict(direction="short", status="ARMED", armed_utc=T0.isoformat(), trigger=100.0, max_fill=99.85, stop=101.5, t1=99.3, t2=98.4, shares=10, q2usd=1.0, est_cost_usd=1.0)
    d.update(k); return d
def bar(h, o, hi, lo, c): return dict(t=T0 + dt.timedelta(hours=h), o=o, h=hi, l=lo, c=c)
class T(unittest.TestCase):
    def test_fill_then_t1_then_be(self):
        t = tr(); s = B.step(t, [bar(1, 100.2, 100.3, 99.9, 99.9), bar(2, 99.9, 100, 99.2, 99.4), bar(3, 99.4, 100.0, 99.3, 99.95)], T0 + dt.timedelta(hours=5), 3)
        self.assertEqual(t["status"], "CLOSED"); self.assertEqual(t["exit"], "BE"); self.assertAlmostEqual(t["realised_usd"], (99.9 - 99.3) * 5)
        self.assertEqual(s, 2)
    def test_beyond_maxfill_missed(self):
        t = tr(); B.step(t, [bar(1, 100.2, 100.3, 99.0, 99.5)], T0 + dt.timedelta(hours=5), 3); self.assertEqual(t["status"], "MISSED")
    def test_full_slots_shadow(self):
        t = tr(); s = B.step(t, [bar(1, 100.2, 100.3, 99.9, 99.9)], T0 + dt.timedelta(hours=5), 0); self.assertEqual(t["status"], "SHADOW"); self.assertEqual(s, 0)
    def test_stop(self):
        t = tr(); B.step(t, [bar(1, 100.2, 100.3, 99.9, 99.9), bar(2, 100, 101.6, 99.8, 101)], T0 + dt.timedelta(hours=5), 3)
        self.assertEqual(t["exit"], "STOP"); self.assertAlmostEqual(t["net_usd"], round(-(101.5 - 99.9) * 10 - 1.0, 2))
    def test_time_exit_and_future_bars_ignored(self):
        t = tr(); bars = [bar(1, 100.2, 100.3, 99.9, 99.9)] + [bar(h, 99.8, 99.9, 99.7, 99.8) for h in range(2, 80)]
        B.step(t, bars, T0 + dt.timedelta(hours=40), 3); self.assertEqual(t["status"], "FILLED")
        B.step(t, bars, T0 + dt.timedelta(hours=90), 3); self.assertEqual(t["exit"], "TIME")
    def test_expiry(self):
        t = tr(); B.step(t, [bar(50, 100.5, 100.6, 100.4, 100.5)], T0 + dt.timedelta(hours=60), 3); self.assertEqual(t["status"], "EXPIRED")
    def test_run_only_oanda_top3_and_counts_hunt(self):
        d = tempfile.mkdtemp(); os.makedirs(os.path.join(d, "state", "hunt48_short")); os.makedirs(os.path.join(d, "state", "calendar", "inputs"))
        json.dump({"trades": [{"status": "FILLED"}, {"status": "FILLED"}, {"status": "FILLED"}]}, open(os.path.join(d, "state", "hunt48_short", "ledger.json"), "w"))
        open(os.path.join(d, "state", "calendar", "inputs", "ranked_oanda.csv"), "w").write("sym,yahoo\nEUR_USD,EURUSD=X\n")
        B.LEDGER = os.path.join(d, "state", "calendar", "paper_bench_ledger.json")
        cal = {"staged": [dict(sym="EUR_USD", venue="oanda", top3=True, direction="short", trigger=100, max_fill=99.85, stop=101.5, t1=99.3, t2=98.4, shares=10, notional_usd=998.5, est_cost_usd=1, risk_aud=1250),
                          dict(sym="AAPL", venue="ibkr", top3=True, direction="short", trigger=1, max_fill=1, stop=1, t1=1, t2=1, shares=1, notional_usd=1, est_cost_usd=0, risk_aud=1)]}
        B.run(now=T0 + dt.timedelta(hours=3), fetch=lambda y, s: [], cal=cal, base=d)
        st = B.run(now=T0 + dt.timedelta(hours=6), fetch=lambda y, s: [bar(4, 100.2, 100.3, 99.9, 99.9)], cal=cal, base=d)
        self.assertEqual([t["sym"] for t in st["trades"]], ["EUR_USD"]); self.assertEqual(st["trades"][0]["status"], "SHADOW")
if __name__ == "__main__": unittest.main(verbosity=1)
