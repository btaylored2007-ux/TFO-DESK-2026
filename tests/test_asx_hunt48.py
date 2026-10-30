import unittest
import os, sys, json, csv, tempfile, unittest, importlib, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UTC = dt.timezone.utc
COLS = ["sym", "cls", "dir", "rank_score", "px", "atr", "atr_pct", "rsi", "roc5", "hi20", "lo10", "pdh", "pdl", "adv_aud", "sector", "asx_shortable", "asx_short_src", "excluded", "m50", "venue"]
def row(sym, sector, d="short", score=4.0, px=10.0, short="True", cls="asx_equity"):
    return dict(sym=sym, cls=cls, dir=d, rank_score=score, px=px, atr=0.3, atr_pct=3.0, rsi=40, roc5=-3, hi20=px * 1.05, lo10=px * 0.97, pdh=px * 1.01, pdl=px * 0.98,
                adv_aud=5e7, sector=sector, asx_shortable=short, asx_short_src="test", excluded="False", m50=px, venue="asx")
def verdicts(d, internal, gpt=None, claude=None, finalize=True, day="20261027", blocked=()):
    """2026-10-27 veto hook fixture: hunt_clearance-shaped summary.json (internal + finalize final) AND external_reviews.json (GPT/CLAUDE), same as live.
    Default: desk PASS + GPT PASS + CLAUDE PENDING_LOGIN (waived) -> final CLEARED."""
    cd = os.path.join(d, "state", "clearance", day, "asx"); os.makedirs(cd, exist_ok=True); gpt = gpt or {}; claude = claude or {}
    ext = {s: dict(GPT=gpt.get(s, "PASS"), CLAUDE=claude.get(s, "PENDING_LOGIN")) for s, v in internal.items() if v == "PASS"}
    fin = lambda s: ("CLEARED" if internal[s] == "PASS" and s not in blocked else "BLOCKED")   # 2026-10-28 07:36 Brett: GPT/Claude advisory -> finalize = desk
    json.dump(dict(books=dict(asx={s: dict(internal=v, R="PASS", B="PASS", S="PASS", why=[] if v == "PASS" else ["x"], **({"final": fin(s)} if finalize else {})) for s, v in internal.items()})),
              open(os.path.join(cd, "summary.json"), "w"))
    json.dump(dict(asx=ext), open(os.path.join(cd, "external_reviews.json"), "w"))
def setup(rows, earnings=None, hunt_filled=0, clear={}):
    d = tempfile.mkdtemp(); os.makedirs(os.path.join(d, "state", "calendar", "inputs")); os.makedirs(os.path.join(d, "static"))
    rows = rows + [dict(row("^AXJO", "idx", cls="index_ref"), px=9000, m50=9100)]       # regime short
    with open(os.path.join(d, "state", "calendar", "inputs", "ranked_asx.csv"), "w") as f:
        w = csv.DictWriter(f, fieldnames=COLS); w.writeheader(); [w.writerow(r) for r in rows]
    json.dump(dict(announcements=[], earnings=earnings or {}, halted=[]), open(os.path.join(d, "state", "calendar", "inputs", "asx_events.json"), "w"))
    if hunt_filled:
        os.makedirs(os.path.join(d, "state", "hunt48_short")); json.dump(dict(trades=[dict(status="FILLED")] * hunt_filled), open(os.path.join(d, "state", "hunt48_short", "ledger.json"), "w"))
    if clear is not None:     # desk clearance fixture (hunt_clearance --book asx summary) for Tue 27 Oct WITA
        verdicts(d, {r["sym"]: clear.get(r["sym"], "PASS") for r in rows})   # + GPT PASS / final CLEARED (veto hook 2026-10-27)
    os.environ["DESK_BASE"] = d
    import cal_paper_bench as CB, event_calendar as EC, asx_hunt48 as A
    importlib.reload(CB); importlib.reload(EC); importlib.reload(A)
    A.no_borrow = lambda t: False; A._daily = lambda sym: NOSUPPORT   # 2026-10-27: fake syms not in IBKR borrow file; no network in tests
    return d, A
# 2026-10-27 Rules Auditor room rule: 1y daily bars with NO support below a ~A$10 short entry (no structure -> T2 room).
NOSUPPORT = [dict(h=12.2, l=11.5, c=11.8)] * 260   # audit M4 hardening: >=200 daily bars
os.environ.pop("CAL_T2_R", None)   # 2026-10-27 pt1486u: hunt48 books use hunt48_room.T2_R = 1.5R (CAL_T2_R no longer reaches them); arming tests run on the live 1.5R geometry
TUE_PRE = dt.datetime(2026, 10, 26, 20, 40, tzinfo=UTC)      # Tue 27 Oct 04:40 WITA (pre-open)
class T(unittest.TestCase):
    def test_ibkr_cost_fields(self):
        d, A = setup([row("Q.AX", "Banks")])
        lv = A.ibkr_cost(dict(shares=1000, max_fill=50.0, stop=51.25), "Q.AX", "long", 50.0)
        self.assertEqual(lv["cost_side_aud"], 40.0)                                  # 0.08% of A$50k
        self.assertAlmostEqual(lv["est_cost_aud"], 2 * 40 + 2 * 0.001 * 50000 + 0.01 * 1000)
        self.assertEqual(A.ibkr_cost(dict(shares=100, max_fill=10.0, stop=10.5), "Q.AX", "long")["cost_side_aud"], 6.0)   # A$6 minimum
    def test_no_borrow_shadow_and_cost_gate_bench(self):
        d, A = setup([row("B.AX", "Banks", score=4.5)])
        A.no_borrow = lambda t: t["sym"] == "B.AX"
        st = A.run(now=TUE_PRE, fetch=lambda y, s: [], base=d)
        L = json.load(open(A.LEDGER)); self.assertEqual([(t["sym"], t["status"], t.get("shadow_reason")) for t in L["trades"]], [("B.AX", "SHADOW", "no borrow")])
        t = dict(sym="X.AX", direction="short", max_fill=10.0, stop=10.8, t2=9.7, cost_R=0.25); self.assertIn("> 0.2R", A.cost_gate(t, []))   # 2026-10-28: +3%/-8% geometry, cap 0.20R
        t = dict(sym="X.AX", direction="short", max_fill=10.0, stop=10.5, cost_R=0.05)
        daily = [dict(h=10.4, l=9.9 + (0.3 if i % 5 else 0), c=10.2) for i in range(260)]
        self.assertIn("room", A.cost_gate(t, daily))
    @unittest.skip("superseded 2026-10-28 07:18 Brett: ASX/Asia cost cap 0.20R (tests/test_costcap_asia_asx_020_20261028.py)")
    def test_asx_cost_cap_still_015(self):
        """Brett 19:44 WITA London-only lift: ASX book stays at cost_R <= 0.15R."""
        import asx_hunt48 as A, importlib; importlib.reload(A)
        self.assertEqual(A.COST_R_MAX, 0.15)
        self.assertIn("> 0.15R", A.cost_gate(dict(direction="long", max_fill=10, stop=9.5, t2=11, cost_R=0.20), []))
        self.assertIn("> 0.15R", A.cost_gate(dict(direction="long", max_fill=10, stop=9.5, t2=11, cost_R=0.28), []))

    def test_no_clearance_no_arm(self):
        d, A = setup([row("X.AX", "Materials"), row("Y.AX", "Banks")], clear=None)
        st = A.run(now=TUE_PRE, fetch=lambda y, s: [], base=d); self.assertEqual(st["counts"]["ARMED"], 0)
        self.assertTrue(all(t["bench_note"] == "awaiting desk clearance" for t in st["bench"]))
    def test_clearance_fail_armed_back_to_bench(self):
        d, A = setup([row("X.AX", "Materials", score=5), row("Y.AX", "Banks")])
        A.run(now=TUE_PRE, fetch=lambda y, s: [], base=d)
        json.dump(dict(books=dict(asx={"X.AX": dict(internal="FAIL", why=["RSI 22: oversold short"]), "Y.AX": dict(internal="PASS", final="CLEARED")})), open(os.path.join(d, "state", "clearance", "20261027", "asx", "summary.json"), "w"))
        st = A.run(now=TUE_PRE, fetch=lambda y, s: [], base=d)
        self.assertEqual([t["sym"] for t in st["armed"]], ["Y.AX"]); x = [t for t in st["bench"] if t["sym"] == "X.AX"][0]
        self.assertIn("desk clearance FAIL", x["bench_note"])
    def test_demoted_never_rearmed(self):        # 2026-10-27 regression: clearance-FAIL / audit-benched names must not be re-promoted
        d, A = setup([row("X.AX", "Materials", score=5), row("Y.AX", "Banks")])
        A.run(now=TUE_PRE, fetch=lambda y, s: [], base=d)
        p = os.path.join(d, "state", "clearance", "20261027", "asx", "summary.json")
        json.dump(dict(books=dict(asx={"X.AX": dict(internal="FAIL", why=["x"]), "Y.AX": dict(internal="PASS", final="CLEARED")})), open(p, "w"))
        A.run(now=TUE_PRE, fetch=lambda y, s: [], base=d)
        L = json.load(open(A.LEDGER)); y = [t for t in L["trades"] if t["sym"] == "Y.AX"][0]
        y.update(status="BENCH", audit_bench_reason="audit"); json.dump(L, open(A.LEDGER, "w"))
        json.dump(dict(books=dict(asx={"X.AX": dict(internal="PASS", final="CLEARED"), "Y.AX": dict(internal="PASS", final="CLEARED")})), open(p, "w"))
        st = A.run(now=TUE_PRE + dt.timedelta(minutes=2), fetch=lambda y, s: [], base=d)
        self.assertEqual(st["counts"]["ARMED"], 0)
        self.assertTrue(all(t.get("demoted_utc") or t.get("audit_bench_reason") for t in st["bench"]))
    def test_thin_adv_cap(self):
        d, A = setup([dict(row("T.AX", "Materials"), adv_aud=2.5e6)])
        lv = A.levels(A.load_rows(d)["T.AX"], "short"); self.assertLessEqual(lv["notional_aud"], 2.5e6 * 0.02 + 1); self.assertLess(lv["risk_aud_actual"], 2500)
    def test_sector_direction_bench_and_wed_open(self):
        rows = [row(f"M{i}.AX", "Materials", score=5 - i * 0.01) for i in range(20)] + [row("B.AX", "Banks", score=4.5), row("C.AX", "Energy", score=4.4)]
        d, A = setup(rows, hunt_filled=3)                        # 3 full US hunt slots must NOT block the ASX book
        st = A.run(now=TUE_PRE, fetch=lambda y, s: [], base=d)
        armed = st["armed"]; self.assertEqual(len(armed), 2)      # 2 shorts, 3rd slot must be a long (none pass in short regime)
        self.assertEqual(len({t["sector"] for t in armed}), 2)
        self.assertTrue(all(t["armed_wita"].startswith("Tue 27 Oct 07:00") for t in armed))   # cleared before today's open -> TODAY (parent 04:54 WITA)
        self.assertEqual(len(st["bench"]), 20)   # fix 4 (28 Oct): bench cap 40 (was 15) -> all 20 non-armed passers benched
        self.assertTrue(all(abs(t["risk_aud_actual"] - 2500) < 30 for t in armed))
        self.assertTrue(all(t["short_flag"] for t in armed))
    def test_not_shortable_blocked(self):
        d, A = setup([row("X.AX", "Materials", short="False")])
        self.assertEqual(A.run(now=TUE_PRE, fetch=lambda y, s: [], base=d)["counts"]["ARMED"], 0)
    def test_earnings_block(self):
        d, A = setup([row("X.AX", "Materials")], earnings={"X": "2026-10-29"})
        self.assertEqual(A.run(now=TUE_PRE, fetch=lambda y, s: [], base=d)["passed"], 0)
    def test_fill_then_prior_close_event_exit(self):
        d, A = setup([row("X.AX", "Materials")])
        A.run(now=TUE_PRE, fetch=lambda y, s: [], base=d)
        L = json.load(open(A.LEDGER)); t = [t for t in L["trades"] if t["status"] == "ARMED"][0]
        # 2026-10-27 audit (day arm => fill Tue, so the 72h cut is Thu close): earnings appears on Thu 29 Oct -> must exit at Wed 28 Oct session close (05:00 UTC)
        j = json.load(open(os.path.join(d, "state", "calendar", "inputs", "asx_events.json"))); j["earnings"] = {"X": "2026-10-29"}
        json.dump(j, open(os.path.join(d, "state", "calendar", "inputs", "asx_events.json"), "w"))
        trig = t["trigger"]; t0 = dt.datetime(2026, 10, 27, 0, 0, tzinfo=UTC)   # 2026-10-27 audit: fill inside the Tue arm session (day arm; was Wed 28 Oct)
        bars = [dict(t=t0, o=trig + .05, h=trig + .05, l=trig - .02, c=trig - .01)] + [dict(t=dt.datetime(2026, 10, 29, h, 0, tzinfo=UTC), o=trig, h=trig + .01, l=trig - .05, c=trig - .03) for h in (1, 2, 3, 4, 5)] \
             + [dict(t=dt.datetime(2026, 10, 30, 0, 0, tzinfo=UTC), o=trig - 2, h=trig - 2, l=trig - 3, c=trig - 3)]
        st = A.run(now=dt.datetime(2026, 10, 29, 5, 10, tzinfo=UTC), fetch=lambda y, s: bars, base=d, force=True)
        tr = [x for x in json.load(open(A.LEDGER))["trades"] if x["sym"] == "X.AX"][0]
        self.assertEqual(tr["status"], "CLOSED"); self.assertEqual(tr["exit"], "PRIOR_CLOSE_EVENT"); self.assertEqual(st["causal_violations_count"], 0)
    # ---- 2026-10-27 Rules Auditor (08:40 WITA) room + B1 rules
    def test_fail_open_null_room_benches(self):
        d, A = setup([row("X.AX", "Materials")])
        st = A.run(now=TUE_PRE, fetch=lambda y, s: [], base=d); self.assertEqual(st["counts"]["ARMED"], 1)
        A._daily = lambda sym: []                                                      # daily bars now missing
        t = dict(st["armed"][0]); self.assertIn("no daily bars", A.cost_gate(t)); self.assertIsInstance(t["room_R"], float)
        st = A.run(now=TUE_PRE + dt.timedelta(minutes=2), fetch=lambda y, s: [], base=d)
        self.assertEqual(st["counts"]["ARMED"], 0, "missing daily bars must never leave a name ARMED (no fail-open)")
        d, A = setup([row("Y.AX", "Banks")]); A._daily = lambda sym: []
        self.assertEqual(A.run(now=TUE_PRE, fetch=lambda y, s: [], base=d)["counts"]["ARMED"], 0)
    def test_room_written_no_nulls(self):
        d, A = setup([row("X.AX", "Materials"), row("Y.AX", "Banks")])
        A.run(now=TUE_PRE, fetch=lambda y, s: [], base=d)
        self.assertTrue(all(isinstance(t.get("room_R"), float) for t in json.load(open(A.LEDGER))["trades"] if t["status"] in ("ARMED", "BENCH")))
    @unittest.skip("superseded: Brett 2026-10-28 07:12/07:13 hunt rule = fixed +3%/-8% (old 1.5R/1R-room geometry); see test_hunt_tp3_sl8_20261028")
    def test_no_structure_old_105_geometry_benches(self):
        import hunt48_room as HR; old = HR.T2_R; HR.T2_R = 1.05                       # the pre-27-Oct 1.05R T2 can never clear 1R after costs
        try:
            d, A = setup([row("X.AX", "Materials")]); st = A.run(now=TUE_PRE, fetch=lambda y, s: [], base=d)
        finally: HR.T2_R = old
        self.assertEqual(st["counts"]["ARMED"], 0); t = st["bench"][0]
        self.assertIn("no structure", t["room_basis"]); self.assertAlmostEqual(t["room_R"], t["room_t2_R"] - t["cost_R"], 2); self.assertLess(t["room_R"], 1.0)
    @unittest.skip("superseded: Brett 2026-10-28 07:12/07:13 hunt rule = fixed +3%/-8% (old 1.5R/1R-room geometry); see test_hunt_tp3_sl8_20261028")
    def test_t2_is_15R_and_typical_name_passes_room(self):      # 2026-10-27 Brett pt1486u
        import hunt48_room as HR; self.assertEqual(HR.T2_R, 1.5); self.assertEqual(HR.MIN_ROOM_R, 1.0)
        d, A = setup([row("X.AX", "Materials")])
        lv = A.levels(A.load_rows(d)["X.AX"], "short")
        R = abs(lv["max_fill"] - lv["stop"]); self.assertAlmostEqual((lv["max_fill"] - lv["t2"]) / R, 1.5, 2); self.assertEqual(lv["rr_t2"], 1.5)
        self.assertLess(lv["cost_R"], 0.5)
        t = dict(sym="X.AX", direction="short", **{k: lv[k] for k in ("max_fill", "stop", "t2", "cost_R")})
        self.assertIsNone(A.cost_gate(t)); self.assertGreaterEqual(t["room_R"], 1.0); self.assertAlmostEqual(t["room_R"], t["room_t2_R"] - t["cost_R"], 2)
        st = A.run(now=TUE_PRE, fetch=lambda y, s: [], base=d); self.assertEqual(st["counts"]["ARMED"], 1)
    def test_15R_still_respects_structure_and_cost(self):
        import hunt48_room as HR
        e, st = 10.0, 10.3; R = 0.3; t2 = e - 1.5 * R
        near = [dict(h=10.5, l=10.4, c=10.45)] * 10 + [dict(h=10.0, l=9.73, c=9.8)] + [dict(h=10.5, l=10.4, c=10.45)] * 10   # support 0.9R below entry
        t = dict(direction="short", max_fill=e, stop=st, t2=t2, cost_R=0.1); self.assertIn("support", HR.room(t, near)); self.assertLess(t["room_R"], 1.0)
        t = dict(direction="short", max_fill=e, stop=st, t2=t2, cost_R=0.55); self.assertIn("to T2", HR.room(t, NOSUPPORT)); self.assertAlmostEqual(t["room_R"], 0.95, 3)   # cost > 0.5R still fails
        t = dict(direction="short", max_fill=e, stop=st, t2=t2, cost_R=0.49); self.assertIsNone(HR.room(t, NOSUPPORT))
        t = dict(direction="short", max_fill=e, stop=st, t2=t2, cost_R=0.1); self.assertIsNotNone(HR.room(t, []))                     # no bars still BENCH
    def test_us_crypto_fx_staging_t2_unchanged(self):
        import event_calendar as EC
        r = dict(sym="ABC", px=50.0, atr=1.0, hi20=51.0, lo10=48.0, pdh=50.5, pdl=49.5, adv_usd=1e8)
        lv = EC.stage_levels(r, "long", 2500, 0.7); R = lv["max_fill"] - lv["stop"]
        self.assertAlmostEqual((lv["t2"] - lv["max_fill"]) / R, EC.T2_R, 2); self.assertEqual(lv["rr_t2"], EC.T2_R)
    def test_b1_rsi_below_30_and_missing_rsi_blocked(self):
        d, A = setup([dict(row("X.AX", "Materials"), rsi=25), dict(row("Y.AX", "Banks"), rsi=""), row("Z.AX", "Energy")])
        st = A.run(now=TUE_PRE, fetch=lambda y, s: [], base=d)
        self.assertEqual(st["passed"], 1)
        rows = A.load_rows(d); reg, revs = A.review(rows, {}, {}, set(), TUE_PRE, TUE_PRE)
        R = {v["sym"]: v for v in revs}; self.assertIn("oversold", "; ".join(R["X.AX"]["why"])); self.assertIn("RSI missing", "; ".join(R["Y.AX"]["why"]))
    def test_b1_exhausted_true_20d_low(self):
        d, A = setup([dict(row("X.AX", "Materials"), roc5=-7, rsi=35)])              # 7%/3% = 2.3 ATR 5d drop
        A._daily = lambda sym: [dict(h=12, l=11, c=11.5)] * 240 + [dict(h=10.1, l=9.9, c=10.0)] * 20   # true 20d low 10.0, px 10.0
        rows = A.load_rows(d); reg, revs = A.review(rows, {}, {}, set(), TUE_PRE, TUE_PRE)
        self.assertIn("exhausted low", "; ".join(revs[0]["why"]))
    def test_shared_top3_excludes_asx(self):
        import event_calendar as EC
        src = open(EC.__file__).read(); self.assertIn('x.get("venue") != "asx"][:TOP_N]', src)
    def test_day_arm_expires_after_session(self):      # 2026-10-27 audit: arming lasts one session (same law as asia_hunt48 step 0)
        d, A = setup([row("X.AX", "Materials", score=5)])
        st = A.run(now=TUE_PRE, fetch=lambda y, s: [], base=d); self.assertEqual(st["counts"]["ARMED"], 1)
        mid = dt.datetime(2026, 10, 27, 3, 0, tzinfo=UTC)                           # Tue 14:00 AEDT, in session, unfilled
        self.assertEqual(A.run(now=mid, fetch=lambda y, s: [], base=d)["counts"]["ARMED"], 1)
        grace = dt.datetime(2026, 10, 27, 6, 25, tzinfo=UTC)                        # close 05:00Z + 85 min: still inside 90-min grace
        self.assertEqual(A.run(now=grace, fetch=lambda y, s: [], base=d)["counts"]["ARMED"], 1)
        after = dt.datetime(2026, 10, 27, 6, 35, tzinfo=UTC)                        # close + 95 min -> EXPIRED (kept, not deleted)
        st = A.run(now=after, fetch=lambda y, s: [], base=d); self.assertEqual(st["counts"]["ARMED"], 0)
        x = [t for t in json.load(open(A.LEDGER))["trades"] if t["sym"] == "X.AX" and t["status"] == "EXPIRED"]
        self.assertEqual(len(x), 1); self.assertIn("day arm", x[0]["note"])
    def test_pending_next_session_arm_not_expired(self):  # an ARMED trade valid from a FUTURE open must not expire early
        d, A = setup([row("X.AX", "Materials", score=5)])
        A.run(now=TUE_PRE, fetch=lambda y, s: [], base=d)
        L = json.load(open(A.LEDGER)); t = [t for t in L["trades"] if t["status"] == "ARMED"][0]
        wed_open = A.session_open(dt.date(2026, 10, 28)); t["armed_utc"] = wed_open.isoformat(); A.CB.atomic(A.LEDGER, L)
        st = A.run(now=dt.datetime(2026, 10, 27, 6, 35, tzinfo=UTC), fetch=lambda y, s: [], base=d)
        self.assertEqual(sum(t["status"] == "EXPIRED" for t in json.load(open(A.LEDGER))["trades"]), 0)


    def test_base_isolates_ledger(self):  # 2026-10-27 audit: base=tmp must not touch live ledger/status
        import asx_hunt48 as A0
        live_L = A0.LEDGER
        before = open(live_L).read() if os.path.exists(live_L) else None
        before_mtime = os.path.getmtime(live_L) if os.path.exists(live_L) else None
        d, A = setup([row("X.AX", "Materials", score=5)])
        A.run(now=TUE_PRE, fetch=lambda y, s: [], base=d)
        after = dt.datetime(2026, 10, 27, 6, 35, tzinfo=UTC)
        A.run(now=after, fetch=lambda y, s: [], base=d)
        tmp_L = json.load(open(os.path.join(d, "state", "hunt48_asx", "ledger.json")))
        self.assertTrue(any(t["status"] == "EXPIRED" for t in tmp_L["trades"]))
        self.assertTrue(os.path.exists(os.path.join(d, "static", "hunt48_asx_status.json")))
        if before is not None:
            self.assertEqual(open(live_L).read(), before)
            self.assertEqual(os.path.getmtime(live_L), before_mtime)

if __name__ == "__main__": unittest.main()
