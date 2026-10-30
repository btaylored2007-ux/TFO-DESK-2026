import unittest
import os, sys, json, csv, tempfile, unittest, importlib, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UTC = dt.timezone.utc
os.environ.pop("CAL_T2_R", None)   # 2026-10-27 pt1486u: hunt48 books use hunt48_room.T2_R = 1.5R (CAL_T2_R no longer reaches them); arming tests run on the live 1.5R geometry
NORES = [dict(h=900.0, l=880.0, c=890.0)] * 260    # 1y daily bars with no resistance above a ~1000 long entry -> room = T2 room - cost
COLS = ["sym", "cls", "dir", "rank_score", "px", "atr", "atr_pct", "rsi", "roc5", "hi20", "lo10", "pdh", "pdl", "adv_aud", "adv_usd", "sector", "shortable", "short_src",
        "borrow_pa", "borrow_avail", "excluded", "m50", "venue", "fx_aud", "lot", "country"]
def row(mkt, sym, sector, d="long", score=4.0, px=1000.0, fx=0.0102, short="False", avail=0, cls=None):
    return dict(sym=sym, cls=cls or f"{mkt}_equity", dir=d, rank_score=score, px=px, atr=px * 0.03, atr_pct=3.0, rsi=60 if d == "long" else 40, roc5=3 if d == "long" else -3,
                hi20=px * 1.02, lo10=px * 0.95, pdh=px * 1.01, pdl=px * 0.98, adv_aud=5e7, adv_usd=5e7, sector=sector, shortable=short, short_src="test", borrow_pa=0.01,
                borrow_avail=avail, excluded="False", m50=px * 0.9, venue=mkt, fx_aud=fx, lot=100, country="Japan")
def setup(mkt, rows, idx_up=True):
    d = tempfile.mkdtemp(); os.makedirs(os.path.join(d, "state", "calendar", "inputs")); os.makedirs(os.path.join(d, "static"))
    import asia_markets as AM
    ix = row(mkt, AM.MARKETS[mkt]["idx"], "idx", cls="index_ref"); ix.update(px=100, m50=90 if idx_up else 110)
    with open(os.path.join(d, "state", "calendar", "inputs", f"ranked_{mkt}.csv"), "w") as f:
        w = csv.DictWriter(f, fieldnames=COLS); w.writeheader(); [w.writerow(r) for r in rows + [ix]]
    json.dump(dict(earnings={}, halted=[]), open(os.path.join(d, "state", "calendar", "inputs", f"{mkt}_events.json"), "w"))
    os.environ["DESK_BASE"] = d
    import event_calendar as EC, asia_hunt48 as AH; importlib.reload(EC); importlib.reload(AH)
    AH._real_review = AH.review; AH._daily = lambda s: NORES
    AH.review = lambda mkt, rows, earn, halted, now, he: ("long", [dict(sym=s, dir=r["dir"], rank_score=float(r["rank_score"]), score=float(r["rank_score"]), sector=r["sector"],
                                                                      passed=(r["dir"] == "long" or AM.MARKETS[mkt]["shorts"]), why=[]) for s, r in rows.items() if r["cls"].endswith("_equity")])
    return d, AH
def clear(d, mkt, AH, verdicts, day="20261027"):
    """write a hunt_clearance-shaped summary + internal_verdicts using the ledger's current levels as the cleared setup"""
    cd = os.path.join(d, "state", "clearance", day, mkt); os.makedirs(cd, exist_ok=True)
    L = json.load(open(AH.paths(mkt, d)[1]))
    json.dump(dict(books={mkt: {s: dict(internal=v, R=v, B="PASS", S="PASS", why=[] if v == "PASS" else ["test fail"], final="CLEARED" if v == "PASS" else "BLOCKED") for s, v in verdicts.items()}}), open(os.path.join(cd, "summary.json"), "w"))
    json.dump({mkt: {s: dict(GPT="PASS", CLAUDE="PENDING_LOGIN") for s, v in verdicts.items() if v == "PASS"}}, open(os.path.join(cd, "external_reviews.json"), "w"))   # 2026-10-27 veto hook: GPT PASS
    rows = {t["sym"]: dict(setup={k: t[k] for k in ("trigger", "max_fill", "stop", "t1", "t2", "shares")}) for t in L["trades"] if t["sym"] in verdicts}
    json.dump(dict(books={mkt: dict(rows=rows)}), open(os.path.join(cd, "internal_verdicts.json"), "w"))
PRE = dt.datetime(2026, 10, 26, 21, 30, tzinfo=UTC)        # Tue 27 Oct 05:30 WITA = 06:30 Tokyo (before 09:00 JST open)
AFTER = dt.datetime(2026, 10, 27, 1, 0, tzinfo=UTC)        # Tue 27 Oct 09:00 WITA (Tokyo open)
NOLIVE = lambda s: None
class T(unittest.TestCase):
    def test_new_goes_bench_never_armed_without_clearance(self):
        d, AH = setup("jp", [row("jp", "1111.T", "Tech"), row("jp", "2222.T", "Banks")])
        st = AH.run("jp", now=PRE, fetch=lambda y, s: [], base=d, live=NOLIVE)
        self.assertEqual(st["counts"]["ARMED"], 0); self.assertEqual(st["counts"]["BENCH"], 2)
        self.assertTrue(all(t["bench_note"] == "awaiting same-day desk clearance" for t in st["bench"]))
    def test_clearance_pass_arms_before_open_with_lot_and_cap(self):
        d, AH = setup("jp", [row("jp", "1111.T", "Tech", score=5), row("jp", "2222.T", "Banks")])
        AH.run("jp", now=PRE, fetch=lambda y, s: [], base=d, live=NOLIVE); clear(d, "jp", AH, {"1111.T": "PASS", "2222.T": "FAIL"})
        st = AH.run("jp", now=PRE + dt.timedelta(minutes=2), fetch=lambda y, s: [], base=d, live=NOLIVE)
        self.assertEqual([t["sym"] for t in st["armed"]], ["1111.T"]); t = st["armed"][0]
        self.assertEqual(t["shares"] % 100, 0); self.assertLessEqual(t["notional_aud"], 333334); self.assertLess(t["stop"], t["trigger"])
        self.assertLessEqual(t["risk_aud_actual"], 2501); self.assertEqual(t["cleared_wita"], "20261027")
    def test_cleared_after_open_stays_flat_today(self):
        d, AH = setup("jp", [row("jp", "1111.T", "Tech")])
        AH.run("jp", now=PRE, fetch=lambda y, s: [], base=d, live=NOLIVE); clear(d, "jp", AH, {"1111.T": "PASS"})
        st = AH.run("jp", now=AFTER, fetch=lambda y, s: [], base=d, live=NOLIVE)
        self.assertEqual(st["counts"]["ARMED"], 0); self.assertIn("flat today", st["bench"][0]["bench_note"])
    def test_demoted_never_rearmed(self):
        d, AH = setup("jp", [row("jp", "1111.T", "Tech")])
        AH.run("jp", now=PRE, fetch=lambda y, s: [], base=d, live=NOLIVE); clear(d, "jp", AH, {"1111.T": "PASS"})
        AH.run("jp", now=PRE + dt.timedelta(minutes=2), fetch=lambda y, s: [], base=d, live=NOLIVE)
        clear(d, "jp", AH, {"1111.T": "FAIL"})
        st = AH.run("jp", now=PRE + dt.timedelta(minutes=4), fetch=lambda y, s: [], base=d, live=NOLIVE)
        self.assertEqual(st["counts"]["ARMED"], 0); self.assertTrue(st["bench"][0].get("demoted_utc"))
        clear(d, "jp", AH, {"1111.T": "PASS"})
        st = AH.run("jp", now=PRE + dt.timedelta(minutes=6), fetch=lambda y, s: [], base=d, live=NOLIVE)
        self.assertEqual(st["counts"]["ARMED"], 0, "demoted trade must never be re-armed")
    def test_preopen_live_through_stop_demotes(self):
        d, AH = setup("jp", [row("jp", "1111.T", "Tech")])
        AH.run("jp", now=PRE, fetch=lambda y, s: [], base=d, live=NOLIVE); clear(d, "jp", AH, {"1111.T": "PASS"})
        st = AH.run("jp", now=PRE + dt.timedelta(minutes=2), fetch=lambda y, s: [], base=d, live=NOLIVE); stop = st["armed"][0]["stop"]
        pre = dt.datetime(2026, 10, 26, 23, 40, tzinfo=UTC)       # 08:40 JST, inside 35-min pre-open window
        st = AH.run("jp", now=pre, fetch=lambda y, s: [], base=d, live=lambda s: stop * 0.99)
        self.assertEqual(st["counts"]["ARMED"], 0); self.assertIn("through stop", st["bench"][0]["bench_note"])
    def test_long_only_market_rejects_short(self):
        d, AH = setup("sg", [row("sg", "D05.SI", "Banks", d="short", px=40, fx=1.17)])
        st = AH.run("sg", now=PRE, fetch=lambda y, s: [], base=d, live=NOLIVE)
        self.assertEqual(st["counts"]["BENCH"] + st["counts"]["ARMED"], 0); self.assertTrue(st["long_only"])
    def test_short_needs_confirmed_borrow(self):
        d, AH = setup("hk", [row("hk", "0700.HK", "Tech", d="short", px=400, fx=0.195, short="True", avail=0)])
        st = AH.run("hk", now=PRE, fetch=lambda y, s: [], base=d, live=NOLIVE)
        self.assertEqual(st["counts"]["SHADOW"], 1)
    def test_cost_gate(self):
        d, AH = setup("jp", [row("jp", "1111.T", "Tech")])
        self.assertIn("> 0.2R", AH.cost_gate(dict(direction="long", max_fill=10, stop=9.2, t2=10.3, cost_R=0.25), []))   # 2026-10-28: +3%/-8% geometry, cap 0.20R
    @unittest.skip("superseded 2026-10-28 07:18 Brett: Asia cost cap 0.20R (tests/test_costcap_asia_asx_020_20261028.py)")
    def test_london_cost_cap_does_not_leak(self):
        """Brett 19:44 WITA: lse cost_R_max=0.30 must NOT raise the default / Asia book caps."""
        import asia_hunt48 as AH, asia_markets as AM, importlib
        importlib.reload(AM); importlib.reload(AH)
        self.assertEqual(AH.COST_R_MAX, 0.15)
        for m in ("jp", "hk", "sg", "kr", "tw"):
            self.assertEqual(AH.cost_R_cap(m), 0.15)
            self.assertNotIn("cost_R_max", AM.MARKETS[m])
        self.assertIn("> 0.15R", AH.cost_gate(dict(direction="long", max_fill=10, stop=9.5, t2=11, cost_R=0.20, market="hk"), []))
        self.assertNotIn("cost", (AH.cost_gate(dict(direction="long", max_fill=10, stop=9.5, t2=11.5, cost_R=0.20, market="lse"),
            [{"d":"2025-01-01","o":10,"h":12,"l":8,"c":10}]*60) or "").lower())  # cost ok under 0.30; room may still bind

    def test_costs_include_local_taxes(self):
        import asia_markets as AM
        self.assertGreater(AM.side_cost("hk", 1000, 50, "buy"), 50000 * 0.001)     # HK stamp 0.1%
        self.assertGreaterEqual(AM.side_cost("kr", 100, 50000, "sell") - AM.side_cost("kr", 100, 50000, "buy"), 5e6 * 0.0015)
        self.assertGreaterEqual(AM.side_cost("tw", 1000, 500, "sell") - AM.side_cost("tw", 1000, 500, "buy"), 5e5 * 0.003 - 1)
    def test_one_per_sector(self):
        d, AH = setup("jp", [row("jp", "1111.T", "Tech", score=5), row("jp", "3333.T", "Tech", score=4.5)])
        AH.run("jp", now=PRE, fetch=lambda y, s: [], base=d, live=NOLIVE); clear(d, "jp", AH, {"1111.T": "PASS", "3333.T": "PASS"})
        st = AH.run("jp", now=PRE + dt.timedelta(minutes=2), fetch=lambda y, s: [], base=d, live=NOLIVE)
        self.assertEqual(len(st["armed"]), 1); self.assertIn("sector cap", st["bench"][0]["bench_note"])
    # ---- 2026-10-27 Rules Auditor (08:40 WITA) room + B1 rules
    def test_fail_open_null_room_benches(self):
        d, AH = setup("jp", [row("jp", "1111.T", "Tech")]); AH._daily = lambda s: []      # daily bars missing
        AH.run("jp", now=PRE, fetch=lambda y, s: [], base=d, live=NOLIVE); clear(d, "jp", AH, {"1111.T": "PASS"})
        st = AH.run("jp", now=PRE + dt.timedelta(minutes=2), fetch=lambda y, s: [], base=d, live=NOLIVE)
        self.assertEqual(st["counts"]["ARMED"], 0); t = st["bench"][0]
        self.assertIsInstance(t["room_R"], float); self.assertIn("no daily bars", t["bench_note"])
    def test_room_written_no_nulls(self):
        d, AH = setup("jp", [row("jp", "1111.T", "Tech", score=5), row("jp", "2222.T", "Banks")])
        AH.run("jp", now=PRE, fetch=lambda y, s: [], base=d, live=NOLIVE); clear(d, "jp", AH, {"1111.T": "PASS", "2222.T": "PASS"})
        AH.run("jp", now=PRE + dt.timedelta(minutes=2), fetch=lambda y, s: [], base=d, live=NOLIVE)
        L = json.load(open(AH.paths("jp", d)[1]))
        self.assertTrue(all(isinstance(t.get("room_R"), float) for t in L["trades"] if t["status"] in ("ARMED", "BENCH")))
    @unittest.skip("superseded: Brett 2026-10-28 07:12/07:13 hunt rule = fixed +3%/-8% (old 1.5R/1R-room geometry); see test_hunt_tp3_sl8_20261028")
    def test_t2_is_15R_and_typical_name_passes_room(self):      # 2026-10-27 Brett pt1486u
        import hunt48_room as HR; self.assertEqual(HR.T2_R, 1.5); self.assertEqual(HR.MIN_ROOM_R, 1.0)
        d, AH = setup("jp", [row("jp", "1111.T", "Tech")])
        lv = AH.levels("jp", AH.load_rows("jp", d)["1111.T"], "long")
        R = abs(lv["max_fill"] - lv["stop"]); self.assertAlmostEqual((lv["t2"] - lv["max_fill"]) / R, 1.5, 2); self.assertEqual(lv["rr_t2"], 1.5)
        self.assertLess(lv["cost_R"], 0.5)
        t = dict(sym="1111.T", direction="long", **{k: lv[k] for k in ("max_fill", "stop", "t2", "cost_R")})
        self.assertIsNone(AH.cost_gate(t)); self.assertGreaterEqual(t["room_R"], 1.0)
    @unittest.skip("superseded: Brett 2026-10-28 07:12/07:13 hunt rule = fixed +3%/-8% (old 1.5R/1R-room geometry); see test_hunt_tp3_sl8_20261028")
    def test_no_structure_old_105_geometry_benches(self):
        import hunt48_room as HR; old = HR.T2_R; HR.T2_R = 1.05
        try:
            d, AH = setup("jp", [row("jp", "1111.T", "Tech")])
            AH.run("jp", now=PRE, fetch=lambda y, s: [], base=d, live=NOLIVE); clear(d, "jp", AH, {"1111.T": "PASS"})
            st = AH.run("jp", now=PRE + dt.timedelta(minutes=2), fetch=lambda y, s: [], base=d, live=NOLIVE)
        finally: HR.T2_R = old
        self.assertEqual(st["counts"]["ARMED"], 0); t = st["bench"][0]
        self.assertIn("no structure", t["room_basis"]); self.assertLess(t["room_R"], 1.0); self.assertIn("to T2", t["bench_note"])
    def test_b1_rsi_and_missing_rsi_and_exhausted_low(self):
        rows = [dict(row("jp", "1111.T", "Tech", d="short", short="True", avail=10**6), rsi=25),
                dict(row("jp", "2222.T", "Banks", d="short", short="True", avail=10**6), rsi=""),
                dict(row("jp", "3333.T", "Energy", d="short", short="True", avail=10**6), rsi=35, roc5=-7),
                dict(row("jp", "4444.T", "Retail", d="short", short="True", avail=10**6), rsi=35, roc5=-2)]
        d, AH = setup("jp", rows, idx_up=False)
        AH._daily = lambda s: [dict(h=1200.0, l=1100.0, c=1150.0)] * 240 + [dict(h=1010.0, l=990.0, c=1000.0)] * 20   # true 20d low 1000 = px
        reg, revs = AH._real_review("jp", AH.load_rows("jp", d), {}, set(), PRE, PRE); R = {v["sym"]: "; ".join(v["why"]) for v in revs}
        self.assertIn("oversold", R["1111.T"]); self.assertIn("RSI missing", R["2222.T"]); self.assertIn("exhausted low", R["3333.T"])
        self.assertNotIn("B1", R["4444.T"]); self.assertNotIn("RSI missing", R["4444.T"])
if __name__ == "__main__": unittest.main()
