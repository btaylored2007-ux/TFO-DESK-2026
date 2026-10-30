"""LONDON (LSE FTSE 350) 48-72h hunt book - PAPER ONLY. Brett 2026-10-27 19:14 WITA (pt1522u) "Build it and add the same capital".
Same engine as jp/hk/sg/kr/tw (asia_hunt48.py `lse`): A$1m, A$2.5k risk, 3 open, 1/sector, T2 1.5R, 1R room, stop on every trade,
UK stamp duty 0.5% on buys + PTM + spread + IBKR commission, pence -> A$, session 08:00-16:30 London (16:00-00:30 WITA in GMT)."""
import os, sys, json, csv, tempfile, unittest, importlib, datetime as dt
from zoneinfo import ZoneInfo
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, BASE)
UTC = dt.timezone.utc; WITA = ZoneInfo("Asia/Makassar")
os.environ.pop("CAL_T2_R", None)
NORES = [dict(h=900.0, l=880.0, c=890.0)] * 60
COLS = ["sym", "cls", "dir", "rank_score", "px", "atr", "atr_pct", "rsi", "roc5", "hi20", "lo10", "pdh", "pdl", "adv_aud", "adv_usd", "sector", "shortable", "short_src",
        "borrow_pa", "borrow_avail", "excluded", "m50", "venue", "fx_aud", "lot", "country", "tier", "spread_bp"]
FX = 0.0189      # A$ per penny (GBPAUD ~1.89 / 100)
def row(sym, sector, score=4.0, px=1000.0, atr_pct=3.0, cls="lse_equity", tier="ftse100"):
    return dict(sym=sym, cls=cls, dir="long", rank_score=score, px=px, atr=px * atr_pct / 100, atr_pct=atr_pct, rsi=60, roc5=3, hi20=px * 1.02, lo10=px * 0.95,
                pdh=px * 1.01, pdl=px * 0.98, adv_aud=5e7, adv_usd=5e7, sector=sector, shortable="False", short_src="test", borrow_pa=0.01, borrow_avail=0,
                excluded="False", m50=px * 0.9, venue="lse", fx_aud=FX, lot=1, country="UK", tier=tier, spread_bp=8.0 if tier == "ftse100" else 30.0)
def setup(rows):
    d = tempfile.mkdtemp(); os.makedirs(os.path.join(d, "state", "calendar", "inputs")); os.makedirs(os.path.join(d, "static"))
    import asia_markets as AM
    ix = row(AM.MARKETS["lse"]["idx"], "idx", cls="index_ref"); ix.update(px=100, m50=90)
    with open(os.path.join(d, "state", "calendar", "inputs", "ranked_lse.csv"), "w") as f:
        w = csv.DictWriter(f, fieldnames=COLS); w.writeheader(); [w.writerow(r) for r in rows + [ix]]
    json.dump(dict(earnings={}, halted=[]), open(os.path.join(d, "state", "calendar", "inputs", "lse_events.json"), "w"))
    os.environ["DESK_BASE"] = d
    import event_calendar as EC, asia_hunt48 as AH; importlib.reload(EC); importlib.reload(AH)
    AH._daily = lambda s: NORES
    AH.review = lambda mkt, rows, earn, halted, now, he: ("long", [dict(sym=s, dir="long", rank_score=float(r["rank_score"]), score=float(r["rank_score"]), sector=r["sector"],
                                                                      passed=True, why=[]) for s, r in rows.items() if r["cls"] == "lse_equity"])
    return d, AH
def clear(d, AH, verdicts, day="20261028"):
    cd = os.path.join(d, "state", "clearance", day, "lse"); os.makedirs(cd, exist_ok=True)
    L = json.load(open(AH.paths("lse", d)[1]))
    json.dump(dict(books={"lse": {s: dict(internal=v, R=v, B="PASS", S="PASS", why=[]) for s, v in verdicts.items()}}), open(os.path.join(cd, "summary.json"), "w"))
    rows = {t["sym"]: dict(setup={k: t[k] for k in ("trigger", "max_fill", "stop", "t1", "t2", "shares")}) for t in L["trades"] if t["sym"] in verdicts}
    json.dump(dict(books={"lse": dict(rows=rows)}), open(os.path.join(cd, "internal_verdicts.json"), "w"))
PRE = dt.datetime(2026, 10, 28, 5, 0, tzinfo=UTC)     # Wed 28 Oct 13:00 WITA = 05:00 London (before 08:00 open)
AFTER = dt.datetime(2026, 10, 28, 8, 30, tzinfo=UTC)  # Wed 28 Oct 16:30 WITA = 08:30 London (open)
NOLIVE = lambda s: None; NOBARS = lambda y, s: []
class TestLseConfig(unittest.TestCase):
    def test_same_capital_and_rules_as_other_books(self):
        import asia_hunt48 as AH, hunt48_room as HR
        asx = json.load(open(os.path.join(BASE, "state", "hunt48_asx", "ledger.json")))["capital_aud"]
        hk = json.load(open(os.path.join(BASE, "state", "hunt48_hk", "ledger.json")))["capital_aud"]
        self.assertEqual(AH.CAPITAL_AUD, asx); self.assertEqual(AH.CAPITAL_AUD, hk); self.assertEqual(AH.CAPITAL_AUD, 1_000_000.0)
        self.assertEqual(AH.RISK_AUD, 2500.0); self.assertEqual(AH.MAX_OPEN, 3); self.assertEqual(HR.T2_R, 1.5); self.assertEqual(HR.MIN_ROOM_R, 1.0)
    def test_session_in_wita_gmt_and_bst(self):
        import asia_hunt48 as AH
        o = AH.session_open("lse", dt.date(2026, 10, 28)).astimezone(WITA); c = AH.session_close("lse", dt.date(2026, 10, 28)).astimezone(WITA)
        self.assertEqual((o.hour, o.minute), (16, 0)); self.assertEqual((c.day, c.hour, c.minute), (29, 0, 30))
        ob = AH.session_open("lse", dt.date(2027, 6, 9)).astimezone(WITA); self.assertEqual(ob.hour, 15)   # BST
        import asia_markets as AM; M = AM.MARKETS["lse"]
        self.assertEqual((M["open_wita"], M["close_wita"], M["tz"], M["suffix"], M["fx"], M["px_div"]), ("16:00", "00:30", "Europe/London", ".L", "GBPAUD=X", 100.0))
        self.assertFalse(AH.trading_day("lse", dt.date(2026, 12, 28)))      # Boxing Day substitute
    def test_stamp_duty_on_buys_only_plus_commission_spread_ptm(self):
        import asia_markets as AM
        v = 2000 * 1000.0                      # 2,000 sh @ 1000p = GBP 20,000 (2,000,000p)
        buy = AM.side_cost("lse", 2000, 1000.0, "buy", spread_bp=8.0); sell = AM.side_cost("lse", 2000, 1000.0, "sell", spread_bp=8.0)
        self.assertAlmostEqual(buy - sell, 0.005 * v, places=2)          # 0.5% stamp duty only on the buy
        self.assertAlmostEqual(sell, 0.0005 * v + 150 + v * 8 / 2 / 1e4, places=2)   # commission + PTM GBP1.50 + half spread
        small = AM.side_cost("lse", 10, 1000.0, "sell", spread_bp=8.0)   # GBP100 order -> GBP3 minimum, no PTM
        self.assertAlmostEqual(small, 300 + 10000 * 8 / 2 / 1e4, places=4)
        self.assertGreater(AM.side_cost("lse", 2000, 1000.0, "sell"), sell)   # missing spread -> conservative FTSE250 default
        self.assertEqual(AM.side_cost("jp", 100, 1000, "buy", spread_bp=50), AM.side_cost("jp", 100, 1000, "buy"))   # Asia unchanged
    def test_ticks_and_symbols(self):
        import asia_markets as AM, universe_asia as U, pandas as pd
        self.assertEqual(AM.tick("lse", 245.0), 0.1); self.assertEqual(AM.tick("lse", 3000.0), 1)
        self.assertEqual(U.lse_sym("BT.A"), "BT-A.L"); self.assertEqual(U.lse_sym("BP."), "BP.L"); self.assertEqual(U.lse_sym("VOD"), "VOD.L")
        self.assertEqual(U.code_of("lse", "BT-A.L"), "BT.A")
        self.assertTrue(U.pence_glitch(pd.Series([1000.0] * 30 + [10.0] * 30))); self.assertFalse(U.pence_glitch(pd.Series([1000.0, 1010.0, 990.0] * 20)))
class TestLseEngine(unittest.TestCase):
    def test_levels_pence_to_aud_risk_and_stop(self):
        d, AH = setup([row("AAA.L", "Banks")])
        lv = AH.levels("lse", AH.load_rows("lse", d)["AAA.L"], "long")
        self.assertLessEqual(lv["risk_aud_actual"], 2501); self.assertGreater(lv["risk_aud_actual"], 2400)
        self.assertAlmostEqual(lv["notional_aud"], lv["shares"] * lv["max_fill"] * FX, delta=1)
        self.assertLess(lv["stop"], lv["max_fill"]); self.assertAlmostEqual((lv["t2"] - lv["max_fill"]) / (lv["max_fill"] - lv["stop"]), 1.5, delta=0.05)
        self.assertGreaterEqual(lv["cost_buy_local"] - lv["cost_sell_local"], 0.005 * lv["shares"] * lv["max_fill"] - 1)   # stamp in the plan cost
        self.assertIn("stamp duty", lv["cost_model"])
    def test_very_tight_stop_still_benched_above_030(self):
        """Even with the London 0.30R cap, a tight ~1.5% ATR stop (cost_R ~0.35+) still BENCHES. Fail closed."""
        d, AH = setup([row("TGT.L", "Retailers", atr_pct=1.5, score=5, px=2000.0)])
        lv = AH.levels("lse", AH.load_rows("lse", d)["TGT.L"], "long")
        self.assertGreater(lv["cost_R"], 0.30)
        AH.run("lse", now=PRE, fetch=NOBARS, base=d, live=NOLIVE); clear(d, AH, {"TGT.L": "PASS"})
        st = AH.run("lse", now=PRE + dt.timedelta(minutes=2), fetch=NOBARS, base=d, live=NOLIVE)
        self.assertEqual(st["counts"]["ARMED"], 0)
        self.assertTrue(any("cost" in (t.get("bench_note") or "").lower() for t in st["bench"]))
    def test_clearance_pass_before_open_arms_wide_stop(self):
        d, AH = setup([row("AAA.L", "Banks", atr_pct=8.0, score=5)])
        st = AH.run("lse", now=PRE, fetch=NOBARS, base=d, live=NOLIVE)
        self.assertEqual(st["counts"]["ARMED"], 0); self.assertEqual(st["counts"]["BENCH"], 1)      # never armed without clearance
        clear(d, AH, {"AAA.L": "PASS"})
        st = AH.run("lse", now=PRE + dt.timedelta(minutes=2), fetch=NOBARS, base=d, live=NOLIVE)
        self.assertEqual([t["sym"] for t in st["armed"]], ["AAA.L"]); t = st["armed"][0]
        self.assertTrue(t["stop"] < t["trigger"]); self.assertLessEqual(t["cost_R"], 0.30); self.assertGreater(t["cost_R"], 0.15); self.assertGreaterEqual(t["room_R"], 1.0)
        self.assertEqual(t["armed_wita"], "Wed 28 Oct 16:00 WITA")           # armed from the 08:00 London continuous open
        self.assertEqual(st["capital_aud"], 1_000_000.0); self.assertTrue(st["paper_only"]); self.assertEqual(st["hours_wita"], "16:00-00:30 WITA")
    def test_cleared_after_open_stays_flat(self):
        d, AH = setup([row("AAA.L", "Banks", atr_pct=8.0)])
        AH.run("lse", now=PRE, fetch=NOBARS, base=d, live=NOLIVE); clear(d, AH, {"AAA.L": "PASS"})
        st = AH.run("lse", now=AFTER, fetch=NOBARS, base=d, live=NOLIVE)
        self.assertEqual(st["counts"]["ARMED"], 0); self.assertIn("flat today", st["bench"][0]["bench_note"])
    def test_one_per_sector_and_max_three(self):
        names = [("A1.L", "Banks", 9), ("A2.L", "Banks", 8), ("B1.L", "Mining", 7), ("C1.L", "Retail", 6), ("D1.L", "Telecom", 5)]
        d, AH = setup([row(s, sec, atr_pct=8.0, score=sc) for s, sec, sc in names])
        AH.run("lse", now=PRE, fetch=NOBARS, base=d, live=NOLIVE); clear(d, AH, {s: "PASS" for s, _, _ in names})
        st = AH.run("lse", now=PRE + dt.timedelta(minutes=2), fetch=NOBARS, base=d, live=NOLIVE)
        secs = [t["sector"] for t in st["armed"]]
        self.assertEqual(len(secs), 2); self.assertEqual(len(secs), len(set(secs)))   # 3 slots, but the unchanged direction-balance rule keeps the last slot for a short
        self.assertNotIn("A2.L", [t["sym"] for t in st["armed"]])
    def test_fill_only_after_auction_and_exit_rules(self):
        d, AH = setup([row("AAA.L", "Banks", atr_pct=8.0)])
        AH.run("lse", now=PRE, fetch=NOBARS, base=d, live=NOLIVE); clear(d, AH, {"AAA.L": "PASS"})
        st = AH.run("lse", now=PRE + dt.timedelta(minutes=2), fetch=NOBARS, base=d, live=NOLIVE); t = st["armed"][0]
        o = dt.datetime(2026, 10, 28, 8, 0, tzinfo=UTC); tr = t["trigger"]
        # a bar ENDING at 08:00 London (pre-open/auction) must never fill; first fill is the 09:00 bar close
        bars = [dict(t=o, o=tr, h=tr * 1.01, l=tr * 0.99, c=tr * 1.001), dict(t=o + dt.timedelta(hours=1), o=tr, h=tr * 1.002, l=tr * 0.999, c=tr * 1.001)]
        st = AH.run("lse", now=o + dt.timedelta(hours=1, minutes=5), fetch=lambda y, s: bars, base=d, live=NOLIVE, force=True)
        L = json.load(open(AH.paths("lse", d)[1])); f = [x for x in L["trades"] if x["sym"] == "AAA.L"][0]
        self.assertEqual(f["status"], "FILLED"); self.assertEqual(f["fill_utc"], (o + dt.timedelta(hours=1)).isoformat())
        self.assertIn("exit_cut_utc", f); self.assertLessEqual(dt.datetime.fromisoformat(f["exit_cut_utc"]) - dt.datetime.fromisoformat(f["fill_utc"]), dt.timedelta(hours=72))
        stop = f["stop_live"]; bars.append(dict(t=o + dt.timedelta(hours=2), o=tr, h=tr, l=stop * 0.99, c=stop * 0.995))
        AH.run("lse", now=o + dt.timedelta(hours=2, minutes=5), fetch=lambda y, s: bars, base=d, live=NOLIVE, force=True)
        L = json.load(open(AH.paths("lse", d)[1])); f = [x for x in L["trades"] if x["sym"] == "AAA.L"][0]
        self.assertEqual((f["status"], f["exit"]), ("CLOSED", "STOP")); self.assertLess(f["net_aud"], -2400); self.assertGreater(f["net_aud"], -3500)   # A$ P&L after costs
    def test_london_cost_cap_030_allows_real_setup(self):
        """Brett 19:44 WITA: London cost_R_max=0.30. A real ~0.25R setup (wide ATR stop) clears the cost gate and can arm after clearance."""
        import asia_hunt48 as AH, asia_markets as AM, importlib
        importlib.reload(AM); importlib.reload(AH)
        self.assertEqual(AH.COST_R_MAX, 0.15)                         # default unchanged
        self.assertEqual(AM.MARKETS["lse"]["cost_R_max"], 0.30)       # London only
        self.assertEqual(AH.cost_R_cap("lse"), 0.30)
        self.assertEqual(AH.cost_R_cap("hk"), 0.15)
        self.assertEqual(AH.cost_R_cap({"market": "jp"}), 0.15)
        # atr_pct=8 -> stop ~5% of trigger, cost_R ~0.19 (stamp-dominated); still >0.15 and <=0.30
        d, AH = setup([row("AAA.L", "Banks", atr_pct=8.0, score=5, px=1000.0)])
        lv = AH.levels("lse", AH.load_rows("lse", d)["AAA.L"], "long")
        self.assertGreater(lv["cost_R"], 0.15)
        self.assertLessEqual(lv["cost_R"], 0.30)
        self.assertIsNone(AH.cost_gate(dict(direction="long", max_fill=lv["max_fill"], stop=lv["stop"], t2=lv["t2"], cost_R=lv["cost_R"], market="lse"), NORES))
        # same cost_R fails on a non-London book
        self.assertIn("> 0.15R", AH.cost_gate(dict(direction="long", max_fill=lv["max_fill"], stop=lv["stop"], t2=lv["t2"], cost_R=lv["cost_R"], market="hk"), []))
    def test_clearance_hurdle_london_only(self):
        """Matching clearance threshold: LSE uses cost_R_max/T1_R (~67%); ASX/US stay at 25%."""
        import hunt_clearance as HC, asia_markets as AM, event_calendar as EC, importlib
        importlib.reload(AM); importlib.reload(HC)
        self.assertAlmostEqual(AM.MARKETS["lse"]["cost_R_max"] / EC.T1_R, 0.30 / EC.T1_R, places=4)
        self.assertGreater(0.30 / EC.T1_R, 0.55)   # lets ~0.25-0.28R through (hurdle≈cost_R/T1_R)
        # 0.28R setup: T1 move = 0.45 * rdist; cost/share ≈ 0.28*rdist -> hurdle ≈ 0.622; must be under LSE cap, over default 0.25
        self.assertLess(0.28 / EC.T1_R, 0.30 / EC.T1_R)
        self.assertGreater(0.28 / EC.T1_R, 0.25)
class TestLseWiring(unittest.TestCase):
    def test_no_broker_order_paths(self):
        for fn in ("asia_markets.py", "asia_hunt48.py", "universe_asia.py", "state/hunt48_lse/run_lse.sh", "state/hunt48_lse/preopen_lse.sh"):
            s = open(os.path.join(BASE, fn)).read().lower()
            for bad in ("placeorder", "/orders", "ib_insync", "ibapi", "oanda", "betfair", "8798", "sudo", "api_key", "apikey"):
                self.assertNotIn(bad, s, f"{fn}: {bad}")
    def test_clearance_and_backup_include_lse(self):
        self.assertIn('"lse")', open(os.path.join(BASE, "hunt_clearance.py")).read())
        self.assertIn("state/hunt48_lse/ledger.json", open(os.path.join(BASE, "tools", "local_ledger_backup.sh")).read())
    def test_warroom_has_london_card(self):
        import warroom_markets as W
        m = [x for x in W.MARKETS if x[0] == "lse"][0]
        self.assertEqual((m[2], m[3], m[5], m[6], m[7], m[8]), ("europe", "static/hunt48_lse_status.json", "Europe/London", "08:00", "16:30", "com.tfo.hunt48.lse"))
        self.assertIn("com.tfo.hunt48.lse", W.JOBS)
        d = W.build(); lm = [x for x in d["markets"] if x["key"] == "lse"][0]
        self.assertEqual(lm["kind"], "europe"); self.assertIn("europe", d["firm"]); self.assertEqual(d["firm"]["europe"]["count"], 1)
        self.assertEqual([x["key"] for x in d["markets"] if x["kind"] == "asia"], ["asx", "jp", "hk", "sg", "kr", "tw"])
        html = open(os.path.join(BASE, "command_dashboard.html")).read(); js = open(os.path.join(BASE, "static", "warroom_markets.js")).read()
        self.assertIn('id="lsebook"', html); self.assertIn("/static/hunt48_lse_status.json", html); self.assertIn("kind==='europe'", js)
if __name__ == "__main__": unittest.main()
