import os, sys, json, tempfile, unittest, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UTC = dt.timezone.utc
class T(unittest.TestCase):
    def test_fill_and_stop(self):
        d = tempfile.mkdtemp(); os.makedirs(os.path.join(d, "state", "calendar")); os.makedirs(os.path.join(d, "static"))
        os.environ["DESK_BASE"] = d
        import importlib, cal_paper_bench as CB; importlib.reload(CB); import asx_paper_bench as A; importlib.reload(A)
        cal = dict(staged=[dict(sym="ABC.AX", venue="asx", top3=True, direction="long", trigger=1.50, max_fill=1.51, stop=1.40, t1=1.55, t2=1.62, shares=10000,
                                notional_usd=10000 * 1.51 * 0.7, est_cost_usd=40, risk_aud=2500)])
        t0 = dt.datetime(2026, 10, 27, 0, 0, tzinfo=UTC)
        bars = [dict(t=t0 + dt.timedelta(hours=1), o=1.49, h=1.52, l=1.48, c=1.505), dict(t=t0 + dt.timedelta(hours=2), o=1.5, h=1.5, l=1.39, c=1.40)]
        A.run(now=t0, fetch=lambda y, s: [], cal=cal, base=d)                      # arm at t0 (causal: only later bars count)
        st = A.run(now=t0 + dt.timedelta(hours=3), fetch=lambda y, s: bars, cal=cal, base=d)
        tr = json.load(open(A.LEDGER))["trades"][0]
        self.assertEqual(tr["status"], "CLOSED"); self.assertEqual(tr["exit"], "STOP"); self.assertLess(tr["net_aud"], 0)
        self.assertAlmostEqual(tr["q2usd"], 0.7, places=6)
if __name__ == "__main__": unittest.main()
