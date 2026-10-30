"""2026-10-27 20:56 WITA Brett: GPT/Claude verdict veto hook for the ASX + Asia hunt48 books (hunt_verdict_veto.py). PAPER. stdlib unittest."""
import os, sys, json, time, tempfile, unittest, importlib, datetime as dt
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
UTC = dt.timezone.utc
import hunt_verdict_veto as VV
import test_asx_hunt48 as TA          # reuse the ASX book fixtures (row/setup/verdicts/TUE_PRE)
NOW = dt.datetime(2026, 10, 27, 13, 0, tzinfo=UTC)   # Tue 27 Oct 21:00 WITA
def write(d, book, summ, ext, day="20261027"):
    cd = os.path.join(d, "state", "clearance", day, book); os.makedirs(cd, exist_ok=True)
    if summ is not None: json.dump(dict(books={book: summ}), open(os.path.join(cd, "summary.json"), "w"))
    if ext is not None: json.dump({book: ext}, open(os.path.join(cd, "external_reviews.json"), "w"))
    return cd
class Module(unittest.TestCase):
    def test_missing_files_veto_all(self):
        d = tempfile.mkdtemp(); V = VV.load("asx", NOW, d); self.assertFalse(V["ok"]); self.assertIn("missing", VV.veto("ARB.AX", V))
        write(d, "asx", {"X.AX": dict(internal="PASS", final="CLEARED")}, None)      # summary only -> still missing external_reviews
        self.assertIn("external_reviews.json", VV.veto("X.AX", VV.load("asx", NOW, d)))
    def test_stale_files_veto_all(self):
        now = dt.datetime.now(UTC)   # 2026-10-28: files written for TODAY's WITA date (was hard-coded 20261027 -> failed after WITA midnight)
        d = tempfile.mkdtemp(); cd = write(d, "asx", {"X.AX": dict(internal="PASS", final="CLEARED")}, {"X.AX": dict(GPT="PASS", CLAUDE="PENDING_LOGIN")}, day=VV.wday(now))
        self.assertIsNone(VV.veto("X.AX", VV.load("asx", now, d)))
        old = time.time() - 30 * 3600; os.utime(os.path.join(cd, "external_reviews.json"), (old, old))
        self.assertIn("stale", VV.veto("X.AX", VV.load("asx", now, d)))
    def test_yesterdays_verdicts_do_not_count_for_new_arms(self):
        d = tempfile.mkdtemp(); write(d, "asx", {"X.AX": dict(internal="PASS", final="CLEARED")}, {"X.AX": dict(GPT="PASS", CLAUDE="PENDING_LOGIN")}, day="20261026")
        self.assertIn("missing", VV.veto("X.AX", VV.load("asx", NOW, d)))
        self.assertIsNone(VV.veto("X.AX", VV.for_armed("asx", NOW, d, "20261026")))       # an already-ARMED trade keeps its own fresh arm-day verdict
        self.assertIn("missing", VV.veto("X.AX", VV.for_armed("asx", NOW, d, "20261025")))
    def test_unreadable_veto_all(self):
        d = tempfile.mkdtemp(); cd = write(d, "asx", {"X.AX": dict(internal="PASS", final="CLEARED")}, {"X.AX": dict(GPT="PASS")})
        open(os.path.join(cd, "summary.json"), "w").write("{not json"); self.assertIn("unreadable", VV.veto("X.AX", VV.load("asx", NOW, d)))
    def test_rules(self):
        """2026-10-28 07:36 Brett OVERRIDE: GPT/Claude ADVISORY. Only finalize CLEARED + fresh files gate; AI FAIL never blocks."""
        d = tempfile.mkdtemp()
        write(d, "asx", {"A.AX": dict(internal="PASS", final="CLEARED"), "B.AX": dict(internal="PASS", final="BLOCKED"), "G.AX": dict(internal="PASS", final="CLEARED"),
                         "C.AX": dict(internal="PASS", final="CLEARED"), "N.AX": dict(internal="PASS"), "F.AX": dict(internal="FAIL", final="BLOCKED")},
              {"A.AX": dict(GPT="PASS", CLAUDE="PENDING_LOGIN"), "B.AX": dict(GPT="PASS", CLAUDE="PASS"), "G.AX": dict(GPT="FAIL", GPT_reason="weak thesis", CLAUDE="PENDING_LOGIN"),
               "C.AX": dict(GPT="PASS", CLAUDE="FAIL"), "N.AX": dict(GPT="PASS", CLAUDE="PASS")})
        V = VV.load("asx", NOW, d)
        self.assertIsNone(VV.veto("A.AX", V)); self.assertIsNone(VV.veto("G.AX", V)); self.assertIsNone(VV.veto("C.AX", V))   # AI FAIL advisory
        self.assertIn("BLOCKED", VV.veto("B.AX", V)); self.assertIn("NOT FINALIZED", VV.veto("N.AX", V))
        self.assertIn("no verdict", VV.veto("F.AX", V)); self.assertIn("no verdict", VV.veto("Z.AX", V))
        self.assertEqual(VV.ai_tags("G.AX", V)["ai_gpt"], dict(verdict="FAIL", reason="weak thesis"))
        s = VV.summary(V); self.assertEqual(s["gpt_fail"], ["G.AX"]); self.assertEqual(s["claude_fail"], ["C.AX"]); self.assertIn("G.AX", s["cleared"])
    def test_live_arb_verdict_is_vetoed(self):
        """ARB.AX stays disarmed (Brett) even with the AI veto advisory: asx_hunt48.HARD_DISARMED."""
        import asx_hunt48 as A; self.assertIn("ARB.AX", A.HARD_DISARMED)
class AsxEngine(unittest.TestCase):
    def run2(self, A, d, now=TA.TUE_PRE): return A.run(now=now, fetch=lambda y, s: [], base=d)
    def test_cleared_name_still_arms(self):
        d, A = TA.setup([TA.row("X.AX", "Materials", score=5)])
        self.assertEqual([t["sym"] for t in self.run2(A, d)["armed"]], ["X.AX"])
    def test_gpt_fail_never_arms(self):
        """renamed rule 2026-10-28: GPT FAIL is advisory -> desk-cleared name ARMS, verdict recorded as ai_gpt; ARB.AX never arms (hard disarm)."""
        d, A = TA.setup([TA.row("X.AX", "Materials", score=5)]); TA.verdicts(d, {"X.AX": "PASS"}, gpt={"X.AX": "FAIL"})
        st = self.run2(A, d); self.assertEqual(st["counts"]["ARMED"], 1)
        t = [t for t in json.load(open(A.LEDGER))["trades"] if t["sym"] == "X.AX"][0]; self.assertEqual(t["ai_gpt"]["verdict"], "FAIL")
        d, A = TA.setup([TA.row("ARB.AX", "Consumer", score=5)]); TA.verdicts(d, {"ARB.AX": "PASS"})
        self.assertEqual(self.run2(A, d)["counts"]["ARMED"], 0)
    def test_claude_fail_never_arms(self):
        """2026-10-28: Claude FAIL advisory -> still arms."""
        d, A = TA.setup([TA.row("X.AX", "Materials", score=5)]); TA.verdicts(d, {"X.AX": "PASS"}, claude={"X.AX": "FAIL"})
        self.assertEqual(self.run2(A, d)["counts"]["ARMED"], 1)
    def test_finalize_blocked_never_arms(self):
        d, A = TA.setup([TA.row("X.AX", "Materials", score=5)]); TA.verdicts(d, {"X.AX": "PASS"}, blocked={"X.AX"})
        st = self.run2(A, d); self.assertEqual(st["counts"]["ARMED"], 0); self.assertIn("BLOCKED", st["bench"][0]["bench_note"])
    def test_missing_verdict_file_stays_out(self):
        d, A = TA.setup([TA.row("X.AX", "Materials", score=5)]); os.remove(os.path.join(d, "state", "clearance", "20261027", "asx", "external_reviews.json"))
        st = self.run2(A, d); self.assertEqual(st["counts"]["ARMED"], 0); self.assertIn("missing", st["bench"][0]["bench_note"])
    def test_stale_verdict_file_stays_out(self):
        d, A = TA.setup([TA.row("X.AX", "Materials", score=5)]); p = os.path.join(d, "state", "clearance", "20261027", "asx", "summary.json")
        old = TA.TUE_PRE.timestamp() - 30 * 3600; os.utime(p, (old, old))
        st = self.run2(A, d); self.assertEqual(st["counts"]["ARMED"], 0); self.assertIn("stale", st["bench"][0]["bench_note"])
    def test_armed_then_gpt_fail_demoted_and_never_rearmed(self):
        """2026-10-28: a GPT FAIL landing after arming is advisory -> stays ARMED; a finalize BLOCKED (desk) still demotes."""
        d, A = TA.setup([TA.row("X.AX", "Materials", score=5)])
        self.assertEqual(self.run2(A, d)["counts"]["ARMED"], 1)
        TA.verdicts(d, {"X.AX": "PASS"}, gpt={"X.AX": "FAIL"})
        self.assertEqual(self.run2(A, d, TA.TUE_PRE + dt.timedelta(minutes=2))["counts"]["ARMED"], 1)
        TA.verdicts(d, {"X.AX": "PASS"}, blocked={"X.AX"})
        st = self.run2(A, d, TA.TUE_PRE + dt.timedelta(minutes=4)); self.assertEqual(st["counts"]["ARMED"], 0)
        self.assertEqual(len([t for t in json.load(open(A.LEDGER))["trades"] if t["sym"] == "X.AX"]), 1)
    def test_evening_arm_survives_midnight_on_its_own_fresh_verdict(self):
        """armed Tue 21:00 WITA for Wed open on Tue's CLEARED verdict: at Wed 00:30 WITA (no Wed verdict yet) it stays ARMED; if Tue's file goes stale -> demoted."""
        d, A = TA.setup([TA.row("X.AX", "Materials", score=5)])
        tue_eve = dt.datetime(2026, 10, 27, 13, 0, tzinfo=UTC); st = A.run(now=tue_eve, fetch=lambda y, s: [], base=d); self.assertEqual(st["counts"]["ARMED"], 1)
        st = A.run(now=dt.datetime(2026, 10, 27, 16, 30, tzinfo=UTC), fetch=lambda y, s: [], base=d); self.assertEqual(st["counts"]["ARMED"], 1)
        cd = os.path.join(d, "state", "clearance", "20261027", "asx"); old = dt.datetime(2026, 10, 27, 16, 35, tzinfo=UTC).timestamp() - 30 * 3600   # 2026-10-28: stale vs the SIMULATED clock (was wall clock)
        for f in ("summary.json", "external_reviews.json"): os.utime(os.path.join(cd, f), (old, old))
        st = A.run(now=dt.datetime(2026, 10, 27, 16, 35, tzinfo=UTC), fetch=lambda y, s: [], base=d); self.assertEqual(st["counts"]["ARMED"], 0)
    def test_manual_disarm_arb_not_rearmed(self):
        """ARB.AX case: manually DISARMED record + GPT FAIL -> engine re-benches a fresh record but never arms it; DISARMED record kept."""
        d, A = TA.setup([TA.row("ARB.AX", "Consumer", score=5)]); TA.verdicts(d, {"ARB.AX": "PASS"}, gpt={"ARB.AX": "FAIL"})
        L = dict(book="HUNT48_ASX", paper_only=True, capital_aud=A.CAPITAL_AUD, ccy="AUD", trades=[dict(id=98, sym="ARB.AX", yahoo="ARB.AX", direction="short", status="DISARMED",
                 disarm_reason="GPT veto, Brett disarm", armed_utc="2026-10-27T23:00:00+00:00", sector="Consumer", score=5, trigger=16.1529, stop=16.9363, shares=3095)])
        os.makedirs(os.path.dirname(A.paths(d)[1]), exist_ok=True); json.dump(L, open(A.paths(d)[1], "w"))
        st = A.run(now=dt.datetime(2026, 10, 27, 13, 0, tzinfo=UTC), fetch=lambda y, s: [], base=d)      # Tue 21:00 WITA, out of hours
        self.assertEqual(st["counts"]["ARMED"], 0)
        T = json.load(open(A.paths(d)[1]))["trades"]
        self.assertEqual([t["status"] for t in T if t["id"] == 98], ["DISARMED"])
        self.assertFalse([t for t in T if t["sym"] == "ARB.AX" and t["status"] in ("ARMED", "FILLED")])
if __name__ == "__main__": unittest.main()
