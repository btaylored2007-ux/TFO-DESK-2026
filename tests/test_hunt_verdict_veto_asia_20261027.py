"""2026-10-27 20:56 WITA Brett: GPT/Claude verdict veto hook wired into asia_hunt48 (jp/hk/sg/kr/tw/lse). PAPER. stdlib unittest."""
import os, sys, json, time, unittest, datetime as dt
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
UTC = dt.timezone.utc
import test_asia_hunt48 as TA        # reuse the Asia fixtures (row/setup/clear/PRE/NOLIVE)
def ext(d, mkt, m, day="20261027"):
    json.dump({mkt: m}, open(os.path.join(d, "state", "clearance", day, mkt, "external_reviews.json"), "w"))
def final(d, mkt, sym, v, day="20261027"):
    p = os.path.join(d, "state", "clearance", day, mkt, "summary.json"); j = json.load(open(p)); j["books"][mkt][sym]["final"] = v; json.dump(j, open(p, "w"))
class AsiaVeto(unittest.TestCase):
    def go(self, AH, d, mins=2): return AH.run("jp", now=TA.PRE + dt.timedelta(minutes=mins), fetch=lambda y, s: [], base=d, live=TA.NOLIVE)
    def base(self):
        d, AH = TA.setup("jp", [TA.row("jp", "1111.T", "Tech", score=5)]); AH.run("jp", now=TA.PRE, fetch=lambda y, s: [], base=d, live=TA.NOLIVE)
        TA.clear(d, "jp", AH, {"1111.T": "PASS"}); return d, AH
    def test_cleared_still_arms(self):
        d, AH = self.base(); self.assertEqual([t["sym"] for t in self.go(AH, d)["armed"]], ["1111.T"])
    # 2026-10-28 Brett 07:36 override: GPT/Claude veto is ADVISORY (recorded, never blocks). Desk finalize still blocks.
    def test_gpt_fail_never_arms(self):  # name kept for history: now "GPT FAIL still arms, recorded"
        d, AH = self.base(); ext(d, "jp", {"1111.T": dict(GPT="FAIL", GPT_reason="no catalyst", CLAUDE="PENDING_LOGIN")})
        st = self.go(AH, d); self.assertEqual(st["counts"]["ARMED"], 1)
        self.assertEqual((st["armed"][0].get("ai_gpt") or {}).get("verdict"), "FAIL")
    def test_claude_fail_never_arms(self):  # advisory: Claude FAIL still arms, recorded
        d, AH = self.base(); ext(d, "jp", {"1111.T": dict(GPT="PASS", CLAUDE="FAIL")})
        st = self.go(AH, d); self.assertEqual(st["counts"]["ARMED"], 1)
        self.assertEqual((st["armed"][0].get("ai_claude") or {}).get("verdict"), "FAIL")
    def test_finalize_blocked_never_arms(self):
        d, AH = self.base(); final(d, "jp", "1111.T", "BLOCKED")
        st = self.go(AH, d); self.assertEqual(st["counts"]["ARMED"], 0); self.assertIn("BLOCKED", st["bench"][0]["bench_note"])
    def test_missing_verdict_file_stays_out(self):
        d, AH = self.base(); os.remove(os.path.join(d, "state", "clearance", "20261027", "jp", "external_reviews.json"))
        st = self.go(AH, d); self.assertEqual(st["counts"]["ARMED"], 0); self.assertIn("missing", st["bench"][0]["bench_note"])
        self.assertEqual(st["clearance"]["gpt"], "NO VERDICT FILE")
    def test_stale_verdict_file_stays_out(self):
        d, AH = self.base(); p = os.path.join(d, "state", "clearance", "20261027", "jp", "external_reviews.json")
        old = TA.PRE.timestamp() - 30 * 3600; os.utime(p, (old, old))
        st = self.go(AH, d); self.assertEqual(st["counts"]["ARMED"], 0); self.assertIn("stale", st["bench"][0]["bench_note"])
    def test_armed_then_gpt_fail_demoted_never_rearmed(self):  # advisory: a later GPT FAIL does NOT demote
        d, AH = self.base(); self.assertEqual(self.go(AH, d)["counts"]["ARMED"], 1)
        ext(d, "jp", {"1111.T": dict(GPT="FAIL", CLAUDE="PENDING_LOGIN")})
        st = self.go(AH, d, 4); self.assertEqual(st["counts"]["ARMED"], 1)
        self.assertFalse(st["armed"][0].get("demoted_utc"))
        final(d, "jp", "1111.T", "BLOCKED")  # desk BLOCKED still demotes and never re-arms
        st = self.go(AH, d, 6); self.assertEqual(st["counts"]["ARMED"], 0)
    def test_lse_book_same_hook(self):
        import importlib, test_lse_hunt48 as TL
        d, AH = TL.setup([TL.row("AAA.L", "Banks", score=5)]) if hasattr(TL, "row") else (None, None)
        if d is None: self.skipTest("lse fixture shape changed")
        AH.run("lse", now=TL.PRE, fetch=TL.NOBARS, base=d, live=TL.NOLIVE); TL.clear(d, AH, {"AAA.L": "PASS"})
        ext(d, "lse", {"AAA.L": dict(GPT="FAIL", CLAUDE="PENDING_LOGIN")}, day="20261028")
        st = AH.run("lse", now=TL.PRE + dt.timedelta(minutes=2), fetch=TL.NOBARS, base=d, live=TL.NOLIVE)
        self.assertEqual(st["counts"]["ARMED"], 1)  # advisory: GPT FAIL recorded, does not block
        self.assertEqual((st["armed"][0].get("ai_gpt") or {}).get("verdict"), "FAIL")
if __name__ == "__main__": unittest.main()
