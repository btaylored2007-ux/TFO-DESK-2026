"""hunt_orchestrator.hunt_us keeps a hand-staged (manual_stage) main-book setup instead of overwriting it (Brett 27 Oct 2026,
KNSA staged for the 28 Oct open). Never armed by the hunt; same slot/sector/direction rules; open gates still apply. No network."""
import os, sys, json, tempfile, unittest, datetime as dt
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UTC = dt.timezone.utc
import hunt_orchestrator as HO, hunt48_short_paper as H
NOW = dt.datetime(2026, 10, 28, 20, 0, tzinfo=HO.WITA).astimezone(UTC); D = "20261028"
SEC = dict(KNSA="Healthcare", FLG="Financial Services", ABC="Technology", XYZ="Healthcare", QQQ1="Energy")
def cand(t, d="short", sc=5.0): return dict(ticker=t, direction=d, trigger=10.0, max_fill=9.985, stop=10.5, t1=9.7, t2=9.2, shares=100, atr=0.4, score=sc, top_decile=True)
KNSA = dict(ticker="KNSA", direction="long", sector="Healthcare", trigger=88.4553, max_fill=88.588, stop=84.1652, t1=90.5782, t2=95.2221,
            shares=397, atr=4.2529, state="STAGED", manual_stage=True, rank="M1")
class HuntUsManualStage(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.saved = HO.ORCH; HO.ORCH = os.path.join(self.d, "state", "hunt_orch"); os.makedirs(HO.ORCH)
        self.arm = []
    def tearDown(self): HO.ORCH = self.saved
    def run_hunt(self, ledger, cleared, prev=None):
        if prev is not None: HO.atomic(os.path.join(HO.ORCH, f"us_backups_{D}.json"), prev)
        P = dict(set=[dict(c) for c in cleared]); an = {c["ticker"]: dict(score=c["score"]) for c in cleared}
        with mock.patch.object(HO, "us_scan", return_value=(True, "ok")), mock.patch.object(HO, "us_build_plan", return_value=(P, an)), \
             mock.patch.object(HO, "sh", return_value=(0, "")), mock.patch.object(HO, "clearance_status", return_value={c["ticker"]: dict(final="CLEARED", setup_hash=__import__("setup_hash").setup_hash(c)) for c in cleared}), \
             mock.patch.object(HO, "us_ledger", return_value=ledger), mock.patch.object(HO, "cfg", return_value=dict(HO.cfg(), open_gates=True)), \
             mock.patch.object(HO, "apply_arm", side_effect=lambda *a, **k: self.arm.append(a) or True), \
             mock.patch.object(H, "live_audusd", return_value=0.70), mock.patch.object(H, "sector_of", side_effect=lambda t, x=None: (x or {}).get("sector") or SEC[t]):
            HO.hunt_us(NOW, [])
        return json.load(open(os.path.join(HO.ORCH, f"us_backups_{D}.json")))["main"]
    def L(self, *open_):
        return dict(trades=[dict(ticker=t, direction=d, status="FILLED", sector=SEC[t]) for t, d in open_])
    def test_manual_stage_survives_hunt_and_takes_a_slot(self):
        st = self.run_hunt(self.L(("FLG", "short")), [cand("ABC", sc=9), cand("QQQ1", sc=8)], prev=dict(main=[KNSA]))
        k = [x for x in st if x["ticker"] == "KNSA"][0]
        self.assertEqual(k["state"], "STAGED"); self.assertTrue(k["manual_stage"]); self.assertEqual(k["trigger"], 88.4553)
        self.assertEqual([x["ticker"] for x in st if x["state"] == "STAGED"], ["KNSA", "ABC", "QQQ1"])   # FLG + KNSA + ABC + QQQ1 = 4 slots   # cap5k 2026-10-29 20:17: US main hunt 4 slots / A$5k
        self.assertEqual([x["ticker"] for x in st if x["state"] == "READY"], [])
        self.assertEqual(self.arm, [])   # hunt never arms while open gates are on
    def test_no_manual_stage_unchanged_behaviour(self):
        st = self.run_hunt(self.L(("FLG", "short")), [cand("ABC", sc=9), cand("QQQ1", d="long", sc=8)])
        self.assertEqual([x["ticker"] for x in st if x["state"] == "STAGED"], ["ABC", "QQQ1"])
    def test_manual_stage_sector_clash_cancelled(self):
        st = self.run_hunt(self.L(("XYZ", "short")), [cand("ABC", sc=9)], prev=dict(main=[KNSA]))
        k = [x for x in st if x["ticker"] == "KNSA"][0]
        self.assertEqual(k["state"], "CANCELLED"); self.assertIn("XYZ", k["last_block"][0])
    def test_manual_stage_no_slot_cancelled(self):
        st = self.run_hunt(self.L(("FLG", "short"), ("ABC", "long"), ("QQQ1", "short"), ("XYZ", "long")), [], prev=dict(main=[KNSA]))   # cap5k 2026-10-29 20:17: US main hunt 4 slots / A$5k
        self.assertEqual([x["state"] for x in st if x["ticker"] == "KNSA"], ["CANCELLED"])
    def test_non_manual_old_entries_still_replaced(self):
        old = dict(cand("ZZZ"), state="STAGED", rank=1)
        st = self.run_hunt(self.L(), [cand("ABC")], prev=dict(main=[old]))
        self.assertNotIn("ZZZ", [x["ticker"] for x in st])
    def test_manual_long_counts_for_direction_balance(self):
        # FLG short + KNSA long open/staged -> a 3rd slot may take either direction; two longs + FLG fine, but KNSA counted
        st = self.run_hunt(self.L(("FLG", "short")), [cand("ABC", d="long", sc=9)], prev=dict(main=[KNSA]))
        self.assertEqual([x["ticker"] for x in st if x["state"] == "STAGED"], ["KNSA", "ABC"])
        st = self.run_hunt(dict(trades=[dict(ticker="FLG", direction="long", status="FILLED", sector=SEC["FLG"])]), [cand("ABC", d="long", sc=9)], prev=dict(main=[KNSA]))
        self.assertEqual([x["ticker"] for x in st if x["state"] == "STAGED"], ["KNSA", "ABC"])   # 3 longs fit 4 slots; the 4th must be opposite   # cap5k 2026-10-29 20:17: US main hunt 4 slots / A$5k
class OpenGateSeesManualStage(unittest.TestCase):
    def test_pre_gate_runs_on_manual_stage_and_cancels_on_fail(self):
        d = tempfile.mkdtemp(); saved = HO.ORCH; HO.ORCH = os.path.join(d, "state", "hunt_orch"); os.makedirs(os.path.join(HO.ORCH, "done"))
        try:
            HO.atomic(os.path.join(HO.ORCH, f"us_backups_{D}.json"), dict(main=[dict(KNSA)]))
            o = dt.datetime(2026, 10, 28, 13, 30, tzinfo=UTC); seen = []
            HO.atomic(HO.marker("us_2026-10-28"), dict(done=True))   # the 20:00 WITA hunt ran
            def pre(t, ou, c): seen.append(t["ticker"]); return dict(ok=False, why=["gap past entry: 90 vs trigger 88.4553"])
            with mock.patch.object(HO, "us_ledger", return_value=dict(trades=[])), mock.patch.object(HO, "cfg", return_value=dict(HO.cfg(), open_gates=True, auto_arm_us_books=["main"])):
                r = HO.open_gate_us(o - dt.timedelta(minutes=10), [], pre_fn=pre, cut_fn=lambda *a: True, arm_fn=lambda *a: self.fail("armed"))
            self.assertEqual(r, "pre"); self.assertEqual(seen, ["KNSA"])
            st = json.load(open(os.path.join(HO.ORCH, f"us_backups_{D}.json")))["main"]
            self.assertEqual(st[0]["state"], "CANCELLED")
        finally: HO.ORCH = saved
if __name__ == "__main__": unittest.main()
