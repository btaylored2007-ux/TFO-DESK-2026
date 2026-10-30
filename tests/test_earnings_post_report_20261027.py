"""Earnings gate, POST-REPORT case (Brett approved 27 Oct 2026 ~23:20 WITA, US 48-72h hunt).
A report already PUBLISHED (exact timestamp >= REPORT_GRACE_H in the past, inside the old 12h look-back) no longer blocks: the name may arm/fill.
Earnings still UPCOMING inside the hold window keep blocking; inside the 2h grace or date-only (session unknown) also still block (fail closed).
Plain unittest, stdlib only, OFFLINE + hermetic (engine paths -> tmp; no network, no broker, no orders).
Run: python3 -m unittest discover -s tests -t tests -p test_earnings_post_report_20261027.py -v"""
import datetime as dt, json, os, shutil, sys, tempfile, unittest
from zoneinfo import ZoneInfo
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
_ARGV = list(sys.argv); sys.argv = [sys.argv[0]]
import hunt48_short_paper as H
sys.argv = _ARGV
UTC = dt.timezone.utc; NY = ZoneInfo("America/New_York")
def _ny(y, m, d, hh, mm): return dt.datetime(y, m, d, hh, mm, tzinfo=NY).astimezone(UTC)
def _bar(start, o, h, l, c): return dict(start=start, o=o, h=h, l=l, c=c)
SPEC = dict(ticker="XYZ", direction="short", trigger=50.0, max_fill=49.9, stop=52.0, t1=49.0, t2=47.8, shares=800, atr=1.0, sector="TECH")
NOW = _ny(2026, 11, 4, 11, 0)            # Wed 4 Nov 2026 11:00 ET, regular session

class Iso(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="earn_post_"); self._undo = []
        def p(o, n, v): self._undo.append((o, n, getattr(o, n))); setattr(o, n, v)
        self.patch = p
        for n, f in (("SD", ""), ("LEDGER", "ledger.json"), ("EVENTS", "events.jsonl"), ("STATUS", "status.json"),
                     ("SECTOR_CACHE", "sector_cache.json"), ("EARN_DIR", "clearance")):
            p(H, n, os.path.join(self.tmp, f) if f else self.tmp)
        p(H.IC, "has_borrow", lambda *a, **k: True); p(H.IC, "borrow_pa", lambda *a, **k: 0.03)
        p(H, "live_audusd", lambda fb: 0.70); p(H, "hi_events", lambda *a, **k: [])
    def tearDown(self):
        for o, n, v in reversed(self._undo): setattr(o, n, v)
        shutil.rmtree(self.tmp, ignore_errors=True)
    def t(self, earn): return dict(ticker="XYZ", next_earnings_utc=earn, time_exit_utc=H.session_exit(NOW).isoformat())
    def tick(self, earn):
        L = H.new_ledger("r"); t = H.upsert_trade(L, dict(SPEC), NOW - dt.timedelta(hours=1)); t["next_earnings_utc"] = earn
        json.dump(L, open(H.LEDGER, "w"), default=str)
        self.patch(H, "now_utc", lambda: NOW); self.patch(H, "us_rth", lambda n: True)
        self.patch(H, "bars_15m", lambda s: [_bar(NOW - dt.timedelta(minutes=30), 50.1, 50.2, 49.9, 49.95)])
        self.patch(H, "bars_daily", lambda s: [dict(h=60.0, l=40.0, c=50.0)] * 260)
        self.patch(H, "room_check", lambda t, daily, entry=None: dict(ok=True, room_R=2.0, level=None, cost_R=0.05, why=None))
        H.tick(); return json.load(open(H.LEDGER))["trades"][0]

class EnginePostReport(Iso):
    def test_published_report_in_last_12h_may_arm_and_fill(self):
        earn = (NOW - dt.timedelta(hours=3, minutes=30)).isoformat()          # BMO 07:30 ET today: published
        s, why = H.earnings_status(self.t(earn), NOW)
        self.assertEqual(s, "clear"); self.assertIn("post-earnings", why)
        self.assertIsNone(H.earnings_block(self.t(earn), NOW)); self.assertIsNone(H.next_earnings(self.t(earn), NOW))
        self.assertIsNotNone(H.recent_report(self.t(earn), NOW))
        d = self.tick(earn)
        self.assertEqual(d["status"], "FILLED"); self.assertEqual(d["earnings_at_fill"]["status"], "clear")
        self.assertIn("post-earnings", d["earnings_at_fill"]["why"])
    def test_upcoming_earnings_inside_hold_still_blocks(self):
        for earn in ((NOW + dt.timedelta(hours=5)).isoformat(),                 # AMC tonight (inside the old 12h band, still pending)
                     (NOW + dt.timedelta(days=2)).isoformat(),                  # inside the 3-session hold
                     "2026-11-17T21:05:00+00:00"):                              # inside the +10 session extension
            self.assertEqual(H.earnings_status(self.t(earn), NOW)[0], "block", earn)
            self.assertTrue(H.earnings_block(self.t(earn), NOW), earn)
        d = self.tick((NOW + dt.timedelta(days=2)).isoformat())
        self.assertEqual(d["status"], "SHADOW"); self.assertIn("earnings", d["shadow_reason"]); self.assertIsNone(d["entry"])
    def test_inside_grace_and_date_only_still_block(self):
        self.assertEqual(H.earnings_status(self.t((NOW - dt.timedelta(minutes=30)).isoformat()), NOW)[0], "block")   # < 2h: not confirmed
        self.assertEqual(H.earnings_status(self.t("2026-11-04"), NOW)[0], "block")                                   # date-only today: session unknown
        self.assertEqual(self.tick("2026-11-04")["status"], "SHADOW")
    def test_published_report_does_not_hide_a_pending_date_on_file(self):
        p = os.path.join(H.EARN_DIR, "20261103"); os.makedirs(p)
        json.dump(dict(calendar=dict(next_earnings_utc=(NOW + dt.timedelta(days=1)).isoformat())), open(os.path.join(p, "XYZ.json"), "w"))
        self.assertEqual(H.earnings_status(self.t((NOW - dt.timedelta(hours=4)).isoformat()), NOW)[0], "block")
    def test_post_report_fill_stamped_trade_can_still_extend(self):
        self.patch(H, "extension_ok", lambda d, long=False: True)
        e = _ny(2026, 11, 2, 10, 0); te = H.session_exit(e)
        t = dict(id=1, ticker="XYZ", status="FILLED", direction="short", trigger=50.5, max_fill=49.9, entry=50.0, entry_utc=e.isoformat(), shares=100,
                 open_qty=100, half_done=False, trail=None, stop=52.0, t1=48.0, t2=46.0, lowest=49.0, atr=1.0, last_px=49.0, ext_days=0,
                 time_exit_utc=te.isoformat(), time_exit_rule="fill + 3 NY sessions (audit C1)", exits=[], realised_usd=0.0, fees_usd=1.0, borrow_usd=0.0,
                 armed_utc=(e - dt.timedelta(hours=1)).isoformat(), risk_cap_aud=2500.0, sector="TECH", earnings_at_fill=dict(status="clear"),
                 next_earnings_utc=(te + dt.timedelta(minutes=1) - dt.timedelta(hours=5)).isoformat(), _last_bar=(e - dt.timedelta(minutes=15)).isoformat())
        self.assertEqual(H.manage(t, [], te + dt.timedelta(minutes=1), None, daily_fn=lambda: [1]), ["extended"])

class ClearancePostReport(unittest.TestCase):
    """hunt_clearance BEHAVIOR reviewer: same rule as the engine."""
    @classmethod
    def setUpClass(cls):
        a = list(sys.argv); sys.argv = [sys.argv[0]]
        try:
            import hunt_clearance as HC
        finally: sys.argv = a
        cls.HC = HC
    def review(self, earn):
        s = dict(ticker="XYZ", direction="long", trigger=50.0, stop=48.0, time_exit_utc=H.session_exit(NOW).isoformat())
        b = dict(asset_class="equity", sweep=dict(rsi="50", roc5="0", atr_pct="3", venue="ibkr"), technical={}, calendar=dict(next_earnings_utc=earn))
        return self.HC.behavior_review(s, b, NOW)
    def test_published_report_passes(self):
        v = self.review((NOW - dt.timedelta(hours=3, minutes=30)).isoformat())
        self.assertEqual(v["verdict"], "PASS", v); self.assertTrue(any("post-earnings" in r for r in v["reasons"]), v)
    def test_upcoming_grace_and_date_only_still_fail(self):
        for earn in ((NOW + dt.timedelta(hours=5)).isoformat(), (NOW + dt.timedelta(days=2)).isoformat(),
                     (NOW - dt.timedelta(minutes=30)).isoformat(), "2026-11-04"):
            v = self.review(earn); self.assertEqual(v["verdict"], "FAIL", earn); self.assertTrue(any("inside hold window" in r for r in v["reasons"]), earn)

if __name__ == "__main__": unittest.main()
