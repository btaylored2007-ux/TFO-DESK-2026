"""Blocked-rail watchdog (2026-09-28): alert after >2 consecutive blocked cycles; shut markets pending only."""
import datetime as dt, json, os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rail_watchdog as wd

T0 = dt.datetime(2026, 9, 28, 0, 15, tzinfo=dt.timezone.utc)
BLOCKED = {"book": "fundamental_book", "sym": "STO.AX", "rail": "HALF", "exit_rule": "HALF_2PCT", "rail_level_usd": 5.506,
           "status": "BLOCKED", "session": "regular", "why": "not a regular-session executable print (STALE/SHUT)"}


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.p = os.path.join(self.d, "rail_watchdog.json"); self.e = os.path.join(self.d, "ev.jsonl"); self.logs = []

    def run_cycles(self, rows_per_cycle, runner="equity", step=300):
        out = None
        for i, rows in enumerate(rows_per_cycle):
            out = wd.update(runner, rows, now=T0 + dt.timedelta(seconds=step * i), path=self.p, events_path=self.e, log=self.logs.append)
        return out, json.load(open(self.p))

    def test_alert_only_after_more_than_two_cycles(self):
        _, doc = self.run_cycles([[BLOCKED], [BLOCKED]])
        self.assertEqual(doc["status"], "OK"); self.assertEqual(doc["alert_count"], 0)
        alerts = wd.update("equity", [BLOCKED], now=T0 + dt.timedelta(seconds=600), path=self.p, events_path=self.e, log=self.logs.append)
        doc = json.load(open(self.p))
        self.assertEqual(doc["status"], "ALERT")
        a = doc["alerts"][0]
        for k in ("symbol", "rule", "level_usd", "reason", "first_blocked_utc", "count"):
            self.assertIn(k, a)
        self.assertEqual((a["symbol"], a["rule"], a["count"]), ("STO.AX", "HALF_2PCT", 3))
        self.assertEqual(a["first_blocked_utc"], "2026-09-28T00:15:00Z")
        self.assertTrue(any("RAIL-WATCHDOG ALERT" in l for l in self.logs))
        self.assertIn("RAISED", open(self.e).read())

    def test_recovery_clears_and_resets(self):
        self.run_cycles([[BLOCKED]] * 3)
        _, doc = self.run_cycles([[{**BLOCKED, "status": "HOLD", "rail": None}]])
        self.assertEqual(doc["status"], "OK"); self.assertEqual(doc["tracking"], {})
        self.assertIn("CLEARED", open(self.e).read())

    def test_executed_ready_is_not_blocked_but_unexecuted_ready_is(self):
        _, doc = self.run_cycles([[{**BLOCKED, "status": "READY", "executed": True}]] * 3)
        self.assertEqual(doc["alert_count"], 0)
        _, doc = self.run_cycles([[{**BLOCKED, "status": "READY", "executed": False}]] * 3)
        self.assertEqual(doc["alert_count"], 1)

    def test_market_shut_is_pending_not_alert(self):
        shut = {**BLOCKED, "sym": "BITTI.HE", "side": "short", "session": "closed"}
        _, doc = self.run_cycles([[shut]] * 5)
        self.assertEqual(doc["alert_count"], 0)
        self.assertEqual(doc["pending_market_shut"][0]["symbol"], "BITTI.HE")

    def test_no_quote_alerts_and_runners_are_independent(self):
        nq = {"book": "fundamental_book", "sym": "BSANTANDER.SN", "status": "NO_QUOTE", "why": "FX unavailable"}
        self.run_cycles([[nq]] * 3)
        _, doc = self.run_cycles([[]], runner="crypto", step=60)  # a crypto cycle must not clear equity entries
        self.assertEqual(doc["alert_count"], 1); self.assertEqual(doc["alerts"][0]["kind"], "no_quote")
        self.assertIn("crypto", doc["runners"]); self.assertIn("equity", doc["runners"])


    def test_no_tape_is_pending_not_alert(self):
        thin = {**BLOCKED, "sym": "GL1.AX", "rail": "FLAT", "exit_rule": "MAX_HOLD_30D",
                "why": "awaiting first regular-session print (no tape yet)"}
        _, doc = self.run_cycles([[thin]] * 5)
        self.assertEqual(doc["alert_count"], 0)
        self.assertEqual(doc["status"], "OK")
        self.assertEqual(doc["pending_no_tape"][0]["symbol"], "GL1.AX")
        # once a real STALE/SHUT block returns (print existed / went bad), alert path still works
        stale = {**BLOCKED, "sym": "GL1.AX", "rail": "FLAT", "exit_rule": "MAX_HOLD_30D"}
        _, doc = self.run_cycles([[stale]] * 3)
        self.assertEqual(doc["alert_count"], 1)
        self.assertEqual(doc["alerts"][0]["kind"], "blocked")

    def test_never_raises(self):
        self.assertEqual(wd.update("equity", [BLOCKED], now=T0, path="/nonexistent_dir/x.json", events_path=self.e, log=self.logs.append), [])
        self.assertTrue(any("error (ignored)" in l for l in self.logs))

    def test_summary_flags_stale_runner(self):
        self.run_cycles([[BLOCKED]])
        s = wd.summary(self.p, now=T0 + dt.timedelta(hours=1))
        self.assertEqual(s["stale_runners"][0]["runner"], "equity")


if __name__ == "__main__":
    unittest.main()
