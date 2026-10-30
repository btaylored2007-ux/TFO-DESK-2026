"""Regression locks for the 4 critical bugs named in strategy triage VERDICT (Brett 2026-10-28 07:10 WITA). Offline, PAPER.
C1 time exit not hardcoded; H1 gap-through stops book the real open; H3 no fill committed before gates; H6 extensions check earnings."""
import datetime as dt, json, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_audit_hardening_20261027 import Iso, H, _ny, _bar, SPEC, UTC
import cal_paper_bench as CB
class TriageBugs(Iso):
    def test_C1_time_exit_follows_fill_not_29_oct(self):
        for d in range(0, 400, 7):
            f = _ny(2026, 10, 30, 10, 0) + dt.timedelta(days=d); t = {}
            te = H.stamp_exit_at_fill(t, f)
            self.assertGreater(te, f); self.assertNotEqual(t["time_exit_utc"], H.TIME_EXIT_UTC); self.assertIn("NY sessions", t["time_exit_rule"])
    def test_H1_gap_through_books_real_open(self):
        self.assertAlmostEqual(CB.stop_fill(100.0, 95.0, 1), 95.0 * (1 - CB.STOP_SLIP))
        self.assertAlmostEqual(CB.stop_fill(100.0, 106.0, -1), 106.0 * (1 + CB.STOP_SLIP))
        x = CB.stop_fill(100.0, float("nan"), 1); self.assertEqual(x, x); self.assertAlmostEqual(x, 100.0 * (1 - CB.STOP_SLIP))
        t = self.filled(); e = dt.datetime.fromisoformat(t["entry_utc"])
        H.manage(t, [_bar(e + dt.timedelta(minutes=15), 55.0, 56.0, 54.5, 55.5)], e + dt.timedelta(hours=1), None, daily_fn=lambda: [1])
        self.assertEqual(t["status"], "STOPPED"); self.assertAlmostEqual(t["exits"][-1]["px"], round(55.0 * (1 + H.SLIP), 4))   # short gapped through 52 stop: real open 55, not 52
    def test_H3_gate_error_keeps_trade_armed(self):
        now = _ny(2026, 11, 4, 11, 0); L = H.new_ledger("r"); t = H.upsert_trade(L, dict(SPEC), now - dt.timedelta(hours=1))
        t["next_earnings_utc"] = "2030-06-28T20:00:00+00:00"; json.dump(L, open(H.LEDGER, "w"), default=str)
        self.patch(H, "now_utc", lambda: now); self.patch(H, "us_rth", lambda n: True)
        self.patch(H, "bars_15m", lambda s: [_bar(now - dt.timedelta(minutes=30), 50.1, 50.2, 49.9, 49.95)])
        self.patch(H, "bars_daily", lambda s: (_ for _ in ()).throw(RuntimeError("yfinance down")))
        try: H.tick()
        except Exception: pass
        d = json.load(open(H.LEDGER))["trades"][0]; self.assertNotEqual(d["status"], "FILLED"); self.assertFalse(d.get("open_qty"))
    def test_H6_extension_refused_when_earnings_inside_horizon(self):
        self.patch(H, "extension_ok", lambda d, long=False: True)
        t = self.filled(earnings_at_fill=dict(status="clear")); te = dt.datetime.fromisoformat(t["time_exit_utc"])
        t["next_earnings_utc"] = (te + dt.timedelta(days=3)).isoformat()
        ev = H.manage(t, [], te + dt.timedelta(minutes=1), None, daily_fn=lambda: [1])
        self.assertEqual(ev, ["time_exit"]); self.assertEqual(t["status"], "CLOSED")
if __name__ == "__main__": unittest.main()
