import json, os, sys, unittest, datetime as dt
B = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, B); os.chdir(B)
import essentials_book as eb
class T(unittest.TestCase):
    def setUp(self): self.s = json.load(open("static/essentials_status.json"))
    def test_spare_never_deployed(self):
        self.assertGreaterEqual(self.s["cash_aud"], eb.SPARE - 1)
        self.assertIn("no room above spare", open("essentials_book.py").read())
    def test_budget(self):   # 2026-10-28: 0.05% drift tolerance (marks move after fills; stager sizes against the cap at fill time)
        cap = eb.NAV0 - eb.SPARE; self.assertLessEqual(self.s["deployed_aud"] + self.s["staged_aud"], cap * 1.0005 + 1)
    def test_fill_sizes_against_cap_at_fill_time(self):
        L = dict(cash_aud=eb.NAV0, positions={"A": dict(qty=1, mark_aud=eb.NAV0 - eb.SPARE - 1000.0, entry_px_aud=1.0, sector="Utilities")},
                 staged=[dict(tk="X", mkt="ASX", target_aud=500.0)], fills=[], costs_paid_aud=0.0)
        o = dict(tk="Y", mkt="ASX", sleeve="core", target_aud=5000.0, ccy="AUD")
        fills, lg = eb.FILLS, eb.log
        eb.FILLS = os.path.join(__import__("tempfile").mkdtemp(), "f.csv"); eb.log = lambda m: None
        import theme_exposure as _te; _cb = _te.check_buy; _te.check_buy = lambda *a, **k: (True, "")   # sizing test: theme cap stubbed
        try: f = eb._fill(L, o, 10.0, {"AUD": 1.0}, "test")
        finally: eb.FILLS, eb.log = fills, lg; _te.check_buy = _cb
        self.assertIsNotNone(f); self.assertLessEqual(f["notional_aud"] + f["cost_aud"], 500.0 + 1e-6)
    def test_name_cap(self):
        for h in self.s["holdings"]:
            v = h.get("value_aud") or h.get("target_aud") or 0
            self.assertLessEqual(v, (eb.SPEC_NAME_CAP if h["sleeve"] == "spec" else eb.NAME_CAP) * eb.NAV0 * 1.03, h["inst"])
    def test_market_cap(self):
        m = {}
        for h in self.s["holdings"]: m[h["mkt"]] = m.get(h["mkt"], 0) + (h.get("value_aud") or h.get("target_aud") or 0)
        for k, v in m.items(): self.assertLessEqual(v, eb.MKT_CAP * eb.NAV0 * 1.03, k)
    def test_spec_cap(self):
        sv = sum((h.get("value_aud") or h.get("target_aud") or 0) for h in self.s["holdings"] if h["sleeve"] == "spec")
        self.assertLessEqual(sv, eb.SPEC_CAP * 1.03)
    def test_no_indonesia_no_ai(self):
        for h in self.s["holdings"]:
            self.assertFalse(h["inst"].endswith(".JK")); self.assertNotIn(h["inst"].split(".")[0], eb.EXCLUDE_AI)
    def test_fills_only_when_open(self):
        L = json.load(open("state/essentials.json"))
        for f in L["fills"]:
            t = dt.datetime.strptime(f["utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
            self.assertTrue(eb.is_open(f["mkt"], t), f["tk"])
    def test_session_clock(self):
        t = dt.datetime(2026, 10, 28, 1, 45, tzinfo=dt.timezone.utc)
        self.assertTrue(eb.is_open("HK", t)); self.assertFalse(eb.is_open("US", t)); self.assertFalse(eb.is_open("LSE", t))
class G(unittest.TestCase):
    def test_trend_global_plan(self):
        import trend_core_global as g
        p = json.load(open(g.PLAN))
        # Plan was due 2026-10-28 and has since executed (health-fix 29 Oct: assert post-deploy state, not pre-fire).
        self.assertEqual(p["due_date"], "2026-10-28"); self.assertTrue(p["executed"])
        cg = sum(t["w"] for t in p["targets"] if t["group"] in ("core", "global", "mf"))
        sp = sum(t["w"] for t in p["targets"] if t["group"] == "spec")
        self.assertLessEqual(cg, g.CORE_BUDGET + 1e-4); self.assertLessEqual(sp, g.SPEC_BUDGET + 1e-4)  # live plan weights; float sum noise
        if not p["signals"]["duration_gate"].get("open", True):
            self.assertEqual(next(t["w"] for t in p["targets"] if t["inst"] == "TMF"), 0.0)
    def test_not_due_noop(self):
        import trend_core_global as g, trend_core as tc
        before = json.load(open(tc.LEDGER))["holdings"]; g.rebalance_if_due()
        self.assertEqual(before, json.load(open(tc.LEDGER))["holdings"])
if __name__ == "__main__": unittest.main()
