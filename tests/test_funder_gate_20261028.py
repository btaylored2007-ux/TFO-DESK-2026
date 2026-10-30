"""2026-10-28 Brett 10:37: funder approval gate - nothing fires without a tick; approve re-checks price; reject drops; missed_shadow."""
import datetime as dt, json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import funder_gate as FG, funder_approve as FA
NOW = dt.datetime(2026, 10, 28, 2, 30, tzinfo=dt.timezone.utc)
def L(): return dict(trades=[dict(id=7, sym="9147.T", yahoo="9147.T", name="Nippon Express", sector="Industrials", direction="long", status="ARMED", trigger=100.0,
                              max_fill=100.5, stop=92.0, t1=103.0, t2=103.0, shares=500, fx_aud=0.01, score=4.2, armed_utc="2026-10-28T02:22:00+00:00"),
                         dict(id=8, sym="FLG", direction="short", status="FILLED", trigger=10, max_fill=10, stop=11, t2=9.7, shares=1, armed_utc="2026-10-27T00:00:00+00:00")])
NOEXP = lambda t, n: False
class T(unittest.TestCase):
    def setUp(self):
        self.o = (FG.QUEUE, FG.CFG); d = tempfile.mkdtemp(); FG.QUEUE = os.path.join(d, "q.json"); FG.CFG = os.path.join(d, "cfg.json")
        import desk_lock; self.ol = desk_lock.LOCKDIR; desk_lock.LOCKDIR = d
    def tearDown(self):
        FG.QUEUE, FG.CFG = self.o; import desk_lock; desk_lock.LOCKDIR = self.ol
    def q(self, L_, px=98.0, exp=NOEXP, now=NOW): return FG.gate_tick("jp", L_, now, lambda t: px, exp)
    def test_armed_goes_to_queue_levels_kept_filled_untouched(self):
        l = L(); self.q(l); t, f = l["trades"]
        self.assertEqual(t["status"], "awaiting_funder"); self.assertEqual((t["trigger"], t["stop"], t["shares"]), (100.0, 92.0, 500))
        self.assertEqual(f["status"], "FILLED")
        e = FG.load()["entries"]["JP-9147T-7"]
        for k in ("id", "market", "ticker", "side", "entry", "stop", "target", "shares", "risk_aud", "score", "tags", "eli10"): self.assertIn(k, e)
        self.assertEqual(e["risk_aud"], 42); self.assertEqual(e["win_aud"], 12); self.assertGreaterEqual(e["eli10"].count("."), 3)
    def test_no_tick_never_arms(self):
        l = L(); self.q(l); self.q(l); self.q(l); self.assertEqual(l["trades"][0]["status"], "awaiting_funder")
    def test_approve_valid_arms_at_approval_time(self):
        l = L(); self.q(l); FA.decide(["JP-9147T-7"], "approve"); later = NOW + dt.timedelta(minutes=5)
        self.q(l, 98.0, now=later); t = l["trades"][0]
        self.assertEqual(t["status"], "ARMED"); self.assertTrue(t["funder_approved"]); self.assertEqual(t["armed_utc"], later.isoformat())
        self.q(l, 98.0, now=later); self.assertEqual(t["status"], "ARMED")             # approved -> not re-queued
        self.assertEqual(FG.load()["entries"]["JP-9147T-7"]["state"], "armed")
    def test_approve_past_trigger_expires_no_fire(self):
        l = L(); self.q(l); FA.decide(["JP-9147T-7"], "approve"); self.q(l, 101.0)
        self.assertEqual(l["trades"][0]["status"], "EXPIRED"); self.assertEqual(l["missed_shadow"][0]["kind"], "funder_gate")
    def test_approve_below_stop_expires(self):
        l = L(); self.q(l); FA.decide(["JP-9147T-7"], "approve"); self.q(l, 91.0); self.assertEqual(l["trades"][0]["status"], "EXPIRED")
    def test_approve_no_price_stays_pending(self):
        l = L(); self.q(l); FA.decide(["JP-9147T-7"], "approve"); self.q(l, None); self.assertEqual(l["trades"][0]["status"], "awaiting_funder")
    def test_reject_drops_with_shadow(self):
        l = L(); self.q(l); FA.decide(["JP-9147T-7"], "reject"); self.q(l)
        self.assertEqual(l["trades"][0]["status"], "REJECTED_FUNDER"); self.assertEqual(len(l["missed_shadow"]), 1)
    def test_unapproved_expires_with_shadow(self):
        l = L(); self.q(l); self.q(l, exp=lambda t, n: True)
        self.assertEqual(l["trades"][0]["status"], "EXPIRED"); self.assertIn("not given", l["missed_shadow"][0]["reason"])
    def test_unknown_and_double_decision(self):
        l = L(); self.q(l); self.assertEqual(FA.decide(["NOPE"], "approve")["NOPE"], "unknown id")
        FA.decide(["JP-9147T-7"], "reject"); self.q(l); self.assertIn("not pending", FA.decide(["JP-9147T-7"], "approve")["JP-9147T-7"])
    def test_short_validity(self):
        t = dict(direction="short", trigger=10.0, stop=11.0)
        self.assertTrue(FG.still_valid(t, 10.5)); self.assertFalse(FG.still_valid(t, 9.9)); self.assertFalse(FG.still_valid(t, 11.2))
    def test_scope(self):
        for b in ("us_main", "us_gpt", "us_claude", "asx", "jp", "hk", "sg", "kr", "tw", "lse"): self.assertTrue(FG.enabled(b))
        for b in ("oanda", "crypto", "trend_core", "essentials", "trend18"): self.assertFalse(FG.enabled(b))
        l = dict(trades=[dict(id=1, sym="BTC", status="ARMED", direction="long", trigger=1, stop=0.5, shares=1)])
        FG.gate_tick("crypto", l, NOW, lambda t: 1, NOEXP); self.assertEqual(l["trades"][0]["status"], "ARMED")
    def test_engines_wired(self):
        base = os.path.dirname(FG.__file__)
        for f in ("asia_hunt48.py", "asx_hunt48.py", "hunt48_short_paper.py"): self.assertIn("FG.gate_tick(", open(os.path.join(base, f)).read(), f)
class Engine(unittest.TestCase):
    def test_asia_engine_queues_instead_of_arming_when_gate_on(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("_tah", os.path.join(os.path.dirname(__file__), "test_asia_hunt48.py")); M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)
        d, AH = M.setup("jp", [M.row("jp", "1111.T", "Tech", score=5)])
        os.makedirs(os.path.join(d, "state"), exist_ok=True); json.dump({"enabled": True}, open(os.path.join(d, "state", "funder_gate.json"), "w"))
        AH.run("jp", now=M.PRE, fetch=lambda y, s: [], base=d, live=M.NOLIVE); M.clear(d, "jp", AH, {"1111.T": "PASS"})
        st = AH.run("jp", now=M.PRE + dt.timedelta(minutes=2), fetch=lambda y, s: [], base=d, live=M.NOLIVE)
        self.assertEqual(st["counts"]["ARMED"], 0); self.assertEqual([t["sym"] for t in st["awaiting_funder"]], ["1111.T"])
        q = json.load(open(os.path.join(d, "state", "funder_queue.json")))["entries"]; self.assertEqual(len(q), 1)
        self.assertNotEqual(os.path.abspath(FG.QUEUE), os.path.abspath(os.path.join(d, "state", "funder_queue.json")))
if __name__ == "__main__": unittest.main()
