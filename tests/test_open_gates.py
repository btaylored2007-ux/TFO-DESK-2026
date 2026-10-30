"""open_gates.py + hunt_orchestrator open-gate layer (PAPER). No network: every data function is injected."""
import os, sys, json, tempfile, unittest, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UTC = dt.timezone.utc
import open_gates as OG, hunt_orchestrator as HO, hunt48_short_paper as H
SH = dict(ticker="XYZ", direction="short", trigger=10.0, max_fill=9.985, stop=11.0, t1=9.5, t2=8.5, shares=100, sector="Tech", top_decile=True)
LG = dict(ticker="ABC", direction="long", trigger=20.0, max_fill=20.03, stop=19.0, t1=20.5, t2=21.5, shares=100, sector="Energy")
O = dt.datetime(2026, 10, 27, 13, 30, tzinfo=UTC)       # 09:30 ET = 21:30 WITA
def bar(t, h, l, c, v, m=5): return dict(start=t, o=c, h=h, l=l, c=c, v=v, mins=m)

class Gap(unittest.TestCase):
    def test_short(self):
        self.assertTrue(OG.gap_gate(SH, 10.4)[0])
        ok, why, gR = OG.gap_gate(SH, 9.9); self.assertFalse(ok); self.assertIn("past entry", why)
        ok, why, gR = OG.gap_gate(SH, 11.2); self.assertFalse(ok); self.assertIn("past stop", why)
        self.assertAlmostEqual(OG.gap_gate(SH, 10.5)[2], 0.5)
    def test_long(self):
        self.assertTrue(OG.gap_gate(LG, 19.6)[0])
        self.assertIn("past entry", OG.gap_gate(LG, 20.1)[1]); self.assertIn("past stop", OG.gap_gate(LG, 18.9)[1])
    def test_no_print(self):
        ok, why, gR = OG.gap_gate(SH, None); self.assertTrue(ok); self.assertIn("unverified", why)
class RS(unittest.TestCase):
    def test_rules(self):
        self.assertTrue(OG.rs_gate(SH, -1.0, 0.2)[0]); self.assertFalse(OG.rs_gate(SH, 0.5, 0.2)[0])
        self.assertTrue(OG.rs_gate(LG, 1.0, 0.2)[0]); self.assertFalse(OG.rs_gate(LG, 0.1, 0.2)[0])
        self.assertFalse(OG.rs_gate(SH, -1.0, None)[0])            # index unknown -> fail closed
        self.assertTrue(OG.rs_gate(SH, None, 0.3)[0])              # no stock print = flat; lagging a rising index
class ORange(unittest.TestCase):
    def bars(self, vol=1000, today_vol=3000):
        out = []
        for d in range(1, 21):      # 20 prior sessions, 6 bars each from the open
            o = O - dt.timedelta(days=d)
            out += [bar(o + dt.timedelta(minutes=5 * i), 10.6, 10.4, 10.5, vol / 6) for i in range(6)]
        out.sort(key=lambda b: b["start"])
        out += [bar(O + dt.timedelta(minutes=5 * i), 10.5 + 0.01 * i, 10.2, 10.3, today_vol / 6) for i in range(6)]
        return out
    def test_range_and_volume(self):
        b = self.bars(); r = OG.opening_range(b, O, 30)
        self.assertTrue(r["complete"]); self.assertAlmostEqual(r["lo"], 10.2); self.assertAlmostEqual(r["hi"], 10.55)
        self.assertFalse(OG.opening_range(b, O, 45)["complete"])
        firsts = [b0["start"] for i, b0 in enumerate(b) if i == 0 or (b0["start"] - b[i - 1]["start"]).total_seconds() > 3600]
        base, n = OG.or_volume_baseline(b, [f for f in firsts if f < O], 30); self.assertEqual(n, 20); self.assertAlmostEqual(base, 1000)
        self.assertTrue(OG.volume_gate(3000, base)[0]); self.assertFalse(OG.volume_gate(1000, base)[0]); self.assertFalse(OG.volume_gate(1000, None)[0])
    def test_or_entry_short_relevels(self):
        n, why = OG.or_entry(SH, dict(hi=10.6, lo=9.8), 1000)       # range low below plan trigger -> entry = break of range low
        self.assertIsNone(why); self.assertLess(n["trigger"], 9.8); self.assertEqual(n["stop"], 11.0)
        R = n["stop"] - n["max_fill"]; self.assertAlmostEqual(n["t2"], round(n["max_fill"] - 1.5 * R, 4), 3); self.assertEqual(n["shares"], int(1000 // R))
        n2, _ = OG.or_entry(SH, dict(hi=10.6, lo=10.3), 1000); self.assertEqual(n2["trigger"], 10.0)   # plan trigger stricter
    def test_or_entry_cancels(self):
        self.assertIn("through stop", OG.or_entry(SH, dict(hi=11.1, lo=10.0), 1000)[1])
        self.assertIn("stretches", OG.or_entry(SH, dict(hi=10.6, lo=9.0), 1000)[1])
        self.assertIn("through stop", OG.or_entry(LG, dict(hi=20.2, lo=18.9), 1000)[1])
    def test_open_check_end_to_end(self):
        b = self.bars(); r = OG.open_check(SH, O, 1000, 30, bars_fn=lambda s: b, spread_fn=lambda s: 5.0)
        self.assertTrue(r["ok"], r["why"]); self.assertGreater(r["vol_ratio"], 1.2)
        r = OG.open_check(SH, O, 1000, 30, bars_fn=lambda s: self.bars(today_vol=900), spread_fn=lambda s: 5.0)
        self.assertFalse(r["ok"]); self.assertTrue(any("volume" in w for w in r["why"]))
        r = OG.open_check(SH, O + dt.timedelta(minutes=5), 1000, 30, bars_fn=lambda s: b, spread_fn=lambda s: 5.0)
        self.assertTrue(r.get("wait"))
class Spread(unittest.TestCase):
    def test_gate(self):
        self.assertTrue(OG.spread_gate(10, 6)[0]); self.assertFalse(OG.spread_gate(13, 6)[0])
        ok, w = OG.spread_gate(None, 6); self.assertTrue(ok); self.assertIn("UNVERIFIED", w)
    def test_normal_floor(self):
        self.assertEqual(OG.normal_spread_bp("X", 2.9, 10.0, hist=[]), 10.0)           # one tick on a $10 name
        self.assertEqual(OG.normal_spread_bp("X", 2.9, 500.0, hist=[4, 6, 8]), 6)       # own history median
        self.assertIsNone(OG.normal_spread_bp("X", None, None, hist=[]))
    def test_cs(self):
        b = [bar(O, 10.05, 9.95, 10, 1)] * 10; self.assertIsNotNone(OG.corwin_schultz_bp(b)); self.assertIsNone(OG.corwin_schultz_bp([]))

class EarningsWindow(unittest.TestCase):
    def test_h6_window(self):
        base = dict(sym="ABC", venue="ibkr", px="50", adv_usd="5e8", atr_pct="3", rsi="45", roc5="-3", excluded="", country="United States", earn_days="", relvol="1")
        self.assertTrue(any("earnings" in w for w in HO.quick_screen(dict(base, earn_days="12"), "short")))
        self.assertFalse(any("earnings" in w for w in HO.quick_screen(dict(base, earn_days="30"), "short")))

class Layer(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.saved = {k: getattr(HO, k) for k in ("BASE", "ORCH", "PLAN_DIR", "STATUS", "EVENTS", "us_ledger", "recheck_us", "apply_arm")}
        HO.BASE = self.d; HO.ORCH = os.path.join(self.d, "state", "hunt_orch"); HO.PLAN_DIR = os.path.join(self.d, "reports"); HO.STATUS = os.path.join(self.d, "st.json")
        HO.EVENTS = os.path.join(HO.ORCH, "events.jsonl"); os.makedirs(os.path.join(HO.ORCH, "done"))
        self.led = {b: {"trades": []} for b in HO.US_BOOKS}; HO.us_ledger = lambda b: self.led[b]
        HO.recheck_us = lambda b, L, px, daily, now: []
        self.armed = []; HO.apply_arm = lambda book, specs, D, why, log, wait=True: self.armed.append((book, [s["ticker"] for s in specs])) or True
        self.Hs = {k: getattr(H, k) for k in ("live_audusd", "bars_15m", "bars_daily")}
        H.live_audusd = lambda d=0.7: 0.65; H.bars_15m = lambda s: [dict(c=10.4)]; H.bars_daily = lambda s: [1]
        self.D = HO.dkey(O); HO.atomic(HO.marker(f"us_{O.date().isoformat()}"), {})
        st = {"main": [dict(SH, ticker="P1", rank=1, state="PRIMARY"), dict(SH, ticker="B1", rank=2, state="READY", sector="Health"),
                       dict(SH, ticker="B2", rank=3, state="READY", sector="Energy")]}
        HO.atomic(os.path.join(HO.ORCH, f"us_backups_{self.D}.json"), st)
    def tearDown(self):
        for k, v in self.saved.items(): setattr(HO, k, v)
        for k, v in self.Hs.items(): setattr(H, k, v)
    def st(self): return HO.us_backup_state(self.D)
    def test_pre_cancels_and_promotes(self):
        cuts = []
        pre = lambda t, o, c: dict(ok=t["ticker"] != "P1", why=[] if t["ticker"] != "P1" else ["gap past entry"])
        r = HO.open_gate_us(O - dt.timedelta(minutes=10), [], pre_fn=pre, cut_fn=lambda *a: cuts.append(a) or True)
        self.assertEqual(r, "pre"); S = {b["ticker"]: b["state"] for b in self.st()["main"]}
        self.assertEqual(S["P1"], "CANCELLED"); self.assertEqual(S["B1"], "STAGED"); self.assertEqual(S["B2"], "STAGED")   # 3 free slots -> 2 backups promoted
        self.assertEqual(self.armed, [])                     # nothing armed before the opening range
        self.assertEqual(HO.open_gate_us(O - dt.timedelta(minutes=5), [], pre_fn=pre), "idle")   # once only
    def test_pre_waits_for_hunt(self):
        os.remove(HO.marker(f"us_{O.date().isoformat()}"))
        self.assertEqual(HO.open_gate_us(O - dt.timedelta(minutes=10), [], pre_fn=lambda *a: dict(ok=True, why=[])), "idle")
    def test_pre_holds_armed_trades_every_book(self):
        self.led["gpt"]["trades"] = [dict(SH, ticker="G1", status="ARMED"), dict(SH, ticker="G2", status="ARMED")]
        cuts = []
        pre = lambda t, o, c: dict(ok=t["ticker"] != "G2", why=[] if t["ticker"] != "G2" else ["short not lagging index"])
        HO.open_gate_us(O - dt.timedelta(minutes=10), [], pre_fn=pre, cut_fn=lambda book, tk, D, why, log: cuts.append((book, tuple(tk), why)) or True)
        self.assertIn(("gpt", ("G2",), "open gates: pre-market gap/RS fail"), cuts)
        self.assertTrue(any(c[0] == "gpt" and c[1] == ("G1",) and "opening range" in c[2] for c in cuts))
        G = HO.gates_state(self.D); self.assertEqual([h["t"]["ticker"] for h in G["held"]], ["G1"])
    def test_or_phase_arms_relevelled_and_promotes(self):
        pre = lambda t, o, c: dict(ok=True, why=[])
        HO.open_gate_us(O - dt.timedelta(minutes=10), [], pre_fn=pre)
        self.assertEqual(HO.open_gate_us(O + dt.timedelta(minutes=20), [], open_fn=lambda *a: dict(wait=True)), "idle")   # inside the range: wait
        def oc(t, o, risk, m):
            if t["ticker"] == "B1": return dict(ok=False, why=["opening volume 0.80x < 1.2x normal"])
            return dict(ok=True, why=[], new_t=dict(t, trigger=9.7, max_fill=9.69))
        r = HO.open_gate_us(O + dt.timedelta(minutes=33), [], open_fn=oc, post_fn=lambda b, now, C, o=None: dict(ok=False, why=["x"], summary={}))
        self.assertEqual(r, "or"); S = {b["ticker"]: b for b in self.st()["main"]}
        self.assertEqual(S["P1"]["state"], "ARMED"); self.assertEqual(S["P1"]["trigger"], 9.7); self.assertEqual(S["B1"]["state"], "CANCELLED")
        self.assertEqual(self.armed[0], ("main", ["P1", "B2"]))
    def test_or_wait_retries_when_bars_late(self):
        HO.open_gate_us(O - dt.timedelta(minutes=10), [], pre_fn=lambda *a: dict(ok=True, why=[]))
        self.assertEqual(HO.open_gate_us(O + dt.timedelta(minutes=33), [], open_fn=lambda *a: dict(wait=True)), "or-wait")
        self.assertEqual(self.armed, [])
    def test_or_promotes_backup_after_cancel(self):
        st = self.st(); st["main"].append(dict(SH, ticker="B3", rank=4, state="READY", sector="Media")); HO.atomic(os.path.join(HO.ORCH, f"us_backups_{self.D}.json"), st)
        HO.open_gate_us(O - dt.timedelta(minutes=10), [], pre_fn=lambda *a: dict(ok=True, why=[]))     # P1,B1,B2 staged
        oc = lambda t, o, r, m: dict(ok=t["ticker"] != "P1", why=["spread 30bp > 2x normal 5bp"] if t["ticker"] == "P1" else [], new_t=dict(t))
        post = lambda b, now, C, o=None: dict(ok=True, why=[], new_t=dict(b), summary={})
        HO.open_gate_us(O + dt.timedelta(minutes=33), [], open_fn=oc, post_fn=post)
        S = {b["ticker"]: b["state"] for b in self.st()["main"]}
        self.assertEqual(S["P1"], "CANCELLED"); self.assertEqual(S["B3"], "ARMED"); self.assertTrue(any("B3" in a[1] for a in self.armed))   # cap5k 2026-10-29 20:17: US main hunt 4 slots / A$5k
    def test_engine_gate_advisory_veto(self):
        o = HO.next_open("jp", dt.datetime(2026, 10, 28, 0, 0, tzinfo=UTC)); now = o + dt.timedelta(minutes=35)
        HO.atomic(os.path.join(self.d, "state", "hunt48_jp", "ledger.json"), dict(trades=[dict(SH, sym="7203.T", status="ARMED"), dict(SH, sym="6758.T", status="FILLED")]))
        orig = OG.intraday; OG.intraday = lambda *a, **k: [bar(o, 10.6, 10.4, 10.5, 1)]
        try: res = HO.engine_gate("jp", now, open_fn=lambda *a, **k: dict(why=["opening volume 0.5x < 1.2x normal"]), rs_fn=lambda t: (True, None))
        finally: OG.intraday = orig
        self.assertEqual(len(res), 1); self.assertTrue(res[0]["veto"])
        p = os.path.join(HO.ORCH, f"open_gate_jp_{HO.dkey(o)}.json"); self.assertTrue(json.load(open(p))["advisory"])
        self.assertIsNone(HO.engine_gate("jp", now))         # once per session
    def test_disabled(self):
        HO.atomic(os.path.join(HO.ORCH, "config.json"), dict(open_gates=False))
        self.assertEqual(HO.open_gate_us(O - dt.timedelta(minutes=10), []), "disabled")
    def test_reslot_waits_for_gates(self):
        self.assertEqual(HO.reslot(O + dt.timedelta(minutes=5), [], px_fn=lambda s: 10.4, daily_fn=lambda s: [1]), [])
        self.assertEqual(self.armed, [])
    def test_safety(self):
        og = open(OG.__file__).read().lower(); ho = open(HO.__file__).read().lower()
        for bad in ("oanda_sleeve", "entry_engine", "sudo", "8798", "kdesk", "betfair", "place_order", "submit_order", "requests.post"):
            self.assertNotIn(bad, og)
        for bad in ("import oanda_sleeve", "sudo ", "8798", "place_order", "submit_order"):
            self.assertNotIn(bad, ho)
    def test_reslot_after_gates_uses_post_open_gates(self):
        HO.atomic(HO.gates_marker(self.D, "or"), {}); seen = []
        orig = HO.post_open_gates
        HO.post_open_gates = lambda b, now, C, o=None: seen.append(b["ticker"]) or dict(ok=b["ticker"] == "B2", why=[] if b["ticker"] == "B2" else ["RS fail"], new_t=dict(b, trigger=9.6), summary={})
        try: done = HO.reslot(O + dt.timedelta(hours=1), [], px_fn=lambda s: 10.4, daily_fn=lambda s: [1])
        finally: HO.post_open_gates = orig
        self.assertEqual(done, [("main", "B2")]); self.assertEqual(seen, ["B1", "B2"])
        S = {b["ticker"]: b for b in self.st()["main"]}; self.assertEqual(S["B1"]["state"], "CANCELLED"); self.assertEqual(S["B2"]["trigger"], 9.6)

if __name__ == "__main__": unittest.main()
