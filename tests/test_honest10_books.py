import datetime as dt, os, sys, tempfile, unittest, math, sqlite3
import numpy as np, pandas as pd
D = tempfile.mkdtemp(); os.environ["HONEST10_STATE_DIR"] = D
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import honest10_books as H
UTC = dt.timezone.utc

def synth(seed=1, drop=None):
    rng = np.random.default_rng(seed)
    days = pd.date_range("2024-06-03", "2026-11-13", freq="D").date
    us = [d for d in days if H.is_us_day(d) and d != drop]
    etf = pd.DataFrame({s: 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, len(us)))) for s in H.RP + H.SEC}, index=us)
    etf["^IRX"] = 4.0; etf["AUDUSD=X"] = 0.65
    cry = pd.DataFrame({s: 50000 * np.exp(np.cumsum(rng.normal(0.002, 0.03, len(days)))) for s in H.CR}, index=list(days))
    return etf, cry
def at(y, m, d, h=2): return dt.datetime(y, m, d, h, tzinfo=UTC)
def q(b, sql): c = sqlite3.connect(os.path.join(D, f"{b}.sqlite")); r = c.execute(sql).fetchall(); c.close(); return r

class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.etf, cls.cry = synth()
    def test_1_no_weekend_or_early_fill(self):
        H.run(self.etf, self.cry, at(2026, 10, 24))           # Sat 24 Oct
        H.run(self.etf, self.cry, at(2026, 10, 26, 22))       # Mon 26 Oct 22:00 UTC: US closed but bar not final per rule
        for b in H.BOOKS: self.assertEqual(q(b, "select count(*) from fills")[0][0], 0)
    def test_2_initial_fill_mon_close(self):
        H.run(self.etf, self.cry, at(2026, 10, 27))
        for b in H.BOOKS:
            ds = {r[0] for r in q(b, "select distinct d from fills")}; self.assertEqual(ds, {"2026-10-26"})
        syms = {r[0] for r in q("BOOKA", "select sym from fills")}
        self.assertTrue({"SPY", "TLT", "GLD"} <= syms)
        self.assertEqual({r[0] for r in q("RP10", "select sym from fills")}, {"SPY", "TLT", "GLD"})
    def test_3_idempotent(self):
        n = {b: q(b, "select count(*),sum(cost_aud) from fills")[0] for b in H.BOOKS}
        e = {b: q(b, "select equity_aud from processed order by d desc limit 1")[0][0] for b in H.BOOKS}
        H.run(self.etf, self.cry, at(2026, 10, 27, 3)); H.run(self.etf, self.cry, at(2026, 10, 27, 4))
        for b in H.BOOKS:
            self.assertEqual(q(b, "select count(*),sum(cost_aud) from fills")[0], n[b])
            self.assertEqual(q(b, "select equity_aud from processed order by d desc limit 1")[0][0], e[b])
    def test_4_costs(self):
        for b in H.BOOKS:
            for sym, notional, cost in q(b, "select sym,notional_aud,cost_aud from fills"):
                self.assertAlmostEqual(cost, notional * H.COST_BP[sym] / 1e4, 6)
        r = q("RP10", "select equity_aud,cost_aud from equity_daily where d='2026-10-26'")[0]
        self.assertAlmostEqual(r[0], H.BOOK0 - r[1], 4)
    def test_5_month_end_and_weekend(self):
        H.run(self.etf, self.cry, at(2026, 11, 3))
        self.assertTrue(q("RP10", "select count(*) from fills where d='2026-10-30' and reason='month_end_rebalance'")[0][0] > 0)
        for b in H.BOOKS:
            self.assertEqual(q(b, "select count(*) from fills where d in ('2026-10-31','2026-11-01') and sym not in ('BTC','ETH')")[0][0], 0)
        self.assertEqual(q("RP10", "select count(*) from equity_daily where d in ('2026-10-31','2026-11-01')")[0][0], 0)
        self.assertEqual(q("BOOKA", "select count(*) from equity_daily where d in ('2026-10-31','2026-11-01')")[0][0], 2)
        self.assertTrue(os.path.exists(os.path.join(D, "BOOKA_equity.csv")))
    def test_6_waits_for_missing_close(self):
        d2 = tempfile.mkdtemp(); old = H.STATE; H.STATE = d2
        try:
            etf, cry = synth(drop=dt.date(2026, 10, 28))
            H.run(etf, cry, at(2026, 10, 30))
            self.assertEqual(q.__call__ and sqlite3.connect(os.path.join(d2, "RP10.sqlite")).execute("select max(d) from processed").fetchone()[0], "2026-10-27")
        finally: H.STATE = old
    def test_7_rp_matches_research(self):
        px = self.etf[H.RP]; r = px.pct_change().fillna(0)
        iv = 1 / r.rolling(60).std(); raw = iv.div(iv.sum(1), axis=0)
        port = (raw.shift(1) * r).sum(1); vol = port.rolling(60).std() * np.sqrt(252); k = (0.10 / vol).clip(upper=3.0)
        ref = raw.mul(k, axis=0).iloc[-1]; got = H.rp_weights(px)
        for s in H.RP: self.assertAlmostEqual(got[s], ref[s], 3)
    def test_8_ctrend_matches_research(self):
        c = self.cry; r = c.pct_change()
        sig = (c.rolling(20).mean() > c.rolling(100).mean()).astype(float)
        ref = (sig / (r.rolling(30).std() * np.sqrt(365)) / 2 * 0.3).clip(upper=0.5).iloc[-1]
        got = H.ctrend_target(c)
        for s in H.CR: self.assertAlmostEqual(got[s], ref[s], 9)
    def test_9_scorecard(self):
        s = H.status_all()["books"]["RP10"]["scorecard"]
        self.assertEqual(s["started"], "2026-10-26"); self.assertEqual(s["review_date"], "2027-10-26")
if __name__ == "__main__": unittest.main(verbosity=2)
