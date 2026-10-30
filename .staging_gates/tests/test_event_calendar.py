import datetime as dt, json, os, sys, tempfile, csv, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import event_calendar as EC
UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 26, 18, 0, tzinfo=UTC)   # Tue 27 Oct 02:00 WITA
COLS = "sym,venue,px,atr,atr_pct,m20,m50,rsi,roc5,lo10,pdl,hi20,pdh,adv_usd,vol24_usd,excluded,dir,rank_score,country,unlock_7d_pct,funding".split(",")
def row(sym, **k):
    base = dict(sym=sym, venue="ibkr", px=50, atr=1.5, atr_pct=3, m20=52, m50=55, rsi=40, roc5=-3, lo10=49, pdl=49.5, hi20=56, pdh=51,
                adv_usd=1e8, vol24_usd="", excluded="False", dir="short", rank_score=6, country="United States", unlock_7d_pct="", funding="")
    base.update(k); return base
def mk(rows, cat):
    d = tempfile.mkdtemp(); ind = os.path.join(d, "state", "calendar", "inputs"); os.makedirs(ind)
    with open(os.path.join(ind, "ranked.csv"), "w") as f:
        w = csv.DictWriter(f, COLS); w.writeheader(); [w.writerow(r) for r in rows]
    json.dump(cat, open(os.path.join(ind, "catcache_2026-10-26.json"), "w"))
    json.dump({"events": [{"time_aedt": "x", "event": "FOMC", "touches": "all"}]}, open(os.path.join(ind, "calendar_2026-10-26_2247.json"), "w"))
    return d, ind
class T(unittest.TestCase):
    def run_review(self, rows, events, held=None, now=NOW):
        R = {r["sym"]: {k: str(v) for k, v in r.items()} for r in rows}
        return EC.review(events, R, {}, now, 0.70, held=held or {}, regime={"ibkr": "short", "kraken": "short"})
    def e(self, kind, sym, hours, past=False, **k):
        return EC.ev(kind, sym, NOW + dt.timedelta(hours=hours), "t", "test", past=past, **k)
    def test_earnings_inside_window_blocks(self):
        rv, st, fl = self.run_review([row("AAA")], [self.e("earnings", "AAA", 30)])
        self.assertFalse(rv[0]["passed"]); self.assertTrue(any("BLOCK" in w for w in rv[0]["why"])); self.assertEqual(st, [])
    def test_earnings_outside_window_not_reviewed(self):
        rv, st, fl = self.run_review([row("AAA")], [self.e("earnings", "AAA", 100)])
        self.assertEqual(rv, [])
    def test_post_earnings_signal_stages(self):
        rv, st, fl = self.run_review([row("AAA")], [self.e("post_earnings", "AAA", -30, past=True)])
        self.assertTrue(rv[0]["passed"]); self.assertEqual(st[0]["sym"], "AAA"); self.assertIn("not armed", st[0]["status"])
    def test_post_earnings_crash_short_blocked(self):
        rv, st, fl = self.run_review([row("AAA", roc5=-12)], [self.e("post_earnings", "AAA", -30, past=True)])
        self.assertEqual(st, []); self.assertTrue(any("reversal" in w for w in rv[0]["why"]))
    def test_rr_fix_and_breakeven(self):
        rv, st, fl = self.run_review([row("AAA")], [self.e("analyst", "AAA", 0, past=True, bias="short")])
        s = st[0]; r = s["stop"] - s["max_fill"]
        self.assertAlmostEqual((s["max_fill"] - s["t1"]) / r, EC.T1_R, places=3); self.assertAlmostEqual((s["max_fill"] - s["t2"]) / r, EC.T2_R, places=3)
        self.assertGreater(s["breakeven_range"][0], 0.55)   # honest (v1.2: realised-winner basis; range lower bound): engine-style targets need ~59% wins
    def test_risk_pre_fomc_half(self):
        rv, st, fl = self.run_review([row("AAA")], [self.e("analyst", "AAA", 0, past=True, bias="short")])
        self.assertEqual(st[0]["risk_aud"], 2500.0)
        later = NOW + dt.timedelta(days=3)
        rv, st, fl = self.run_review([row("AAA")], [EC.ev("analyst", "AAA", later, "t", "x", past=True, bias="short")], now=later)
        self.assertEqual(st[0]["risk_aud"], 2500.0)   # post-FOMC stays 2.5k (head trader 27 Oct)
    def test_size_risk_math(self):
        rv, st, fl = self.run_review([row("AAA")], [self.e("analyst", "AAA", 0, past=True)])
        s = st[0]; self.assertLessEqual(s["shares"] * (s["stop"] - s["max_fill"]), 2500 * 0.70 + 1e-6)
    def test_size_down_on_8k(self):
        rv, st, fl = self.run_review([row("AAA")], [self.e("sec_8k_material", "AAA", 5)])
        self.assertEqual(rv[0]["size_mult"], 0.5); self.assertEqual(st[0]["risk_aud"], 1250.0)
    def test_indonesia_and_pump_blocked(self):
        rv, st, fl = self.run_review([row("BBB", country="Indonesia"), row("CCC", dir="long", roc5=25, m20=40, m50=35)],
                                     [self.e("analyst", "BBB", 0, past=True), self.e("analyst", "CCC", 0, past=True)])
        self.assertEqual(st, [])
        self.assertTrue(any("Indonesian" in w for r in rv for w in r["why"])); self.assertTrue(any("pump" in w for r in rv for w in r["why"]))
    def test_regime_filter(self):
        rv, st, fl = self.run_review([row("CCC", dir="long")], [self.e("insider_buy", "CCC", 0, past=True, bias="long")])
        self.assertTrue(any("regime" in w for w in rv[0]["why"]))
    def test_held_name_flagged_and_not_restaged(self):
        rv, st, fl = self.run_review([row("AAA")], [self.e("earnings", "AAA", 20)], held={"AAA": "hunt48_short:ARMED"})
        self.assertEqual(len(fl), 1); self.assertIn("CUT", fl[0]["action"])
        rv, st, fl = self.run_review([row("AAA")], [self.e("analyst", "AAA", 0, past=True)], held={"AAA": "hunt48_short:ARMED"})
        self.assertEqual(st, []); self.assertTrue(any("already" in w for w in rv[0]["why"]))
    def test_extension_flag(self):
        fl = EC.extension_flags({"AAA": "b:ARMED", "BBB": "b:ARMED"}, {"AAA": {"next_earn": "2026-11-03"}, "BBB": {"next_earn": "2026-12-20"}}, NOW, set())
        self.assertEqual([f["sym"] for f in fl], ["AAA"]); self.assertIn("NO EXTENSION", fl[0]["action"])
    def fxrow(self, sym, **k):
        b = row(sym, venue="oanda", px=1.10, atr=0.006, atr_pct=0.55, lo10=1.095, pdl=1.098, hi20=1.12, pdh=1.105, adv_usd="", cost_pct=0.03, cls="fx_major", country="")
        b.update(k); return b
    def test_fx_sizing_quote_conversion_and_ccy_macro(self):
        rows = [self.fxrow("EUR_USD"), self.fxrow("USD_JPY", px=150.0, atr=0.9, atr_pct=0.6, lo10=149, pdl=149.5, hi20=152, pdh=150.5, cls="fx_major")]
        evs = [EC.ev("macro_high", "MACRO", NOW + dt.timedelta(hours=10), "USD CPI", "t", ccy="USD")]
        rv, st, fl = self.run_review(rows, evs)
        self.assertEqual({x["sym"] for x in st}, {"EUR_USD", "USD_JPY"})
        for x in st:
            self.assertEqual(x["risk_aud"], 1250.0)           # 50% size-down for ccy macro inside window
            q = 1.0 if x["sym"] == "EUR_USD" else 1 / 150.0
            self.assertLessEqual(x["shares"] * abs(x["stop"] - x["max_fill"]) * q, 1250 * 0.70 + 1e-6)
    def test_fx_spread_gate(self):
        rv, st, fl = self.run_review([self.fxrow("USD_TRY", cost_pct=0.5, cls="fx_exotic")], [EC.ev("macro_high", "MACRO", NOW + dt.timedelta(hours=5), "USD NFP", "t", ccy="USD")])
        self.assertEqual(st, []); self.assertTrue(any("spread" in w for w in rv[0]["why"]))
    def test_metals_cluster_cap_counts_held(self):
        rows = [self.fxrow("XAG_USD", cls="metal"), self.fxrow("XPT_USD", cls="metal"), self.fxrow("XAU_USD", cls="metal")]
        evs = [EC.ev("macro_high", "MACRO", NOW + dt.timedelta(hours=5), "USD GDP", "t", ccy="USD")]
        rv, st, fl = self.run_review(rows, evs, held={"SLV": "hunt48_short:ARMED"})
        self.assertEqual(len(st), 1)
    def test_fx_exotic_shadow_only(self):
        rv, st, fl = self.run_review([self.fxrow("USD_MXN", cls="fx_exotic", cost_pct=0.01)], [EC.ev("macro_high", "MACRO", NOW + dt.timedelta(hours=5), "USD NFP", "t", ccy="USD")])
        self.assertEqual(st, []); self.assertTrue(any("shadow-only" in w for w in rv[0]["why"]))
    def test_conflicting_bias_rejected(self):
        rv, st, fl = self.run_review([row("AAA")], [self.e("guidance", "AAA", 0, past=True, bias="long")])
        self.assertFalse(rv[0]["passed"])
    def test_top3_cap(self):
        rows = [row(f"S{i}", rank_score=5 + i) for i in range(6)]
        rv, st, fl = self.run_review(rows, [self.e("analyst", f"S{i}", 0, past=True) for i in range(6)])
        self.assertEqual(sum(x["top3"] for x in st), 3); self.assertEqual(st[0]["sym"], "S5")
    def test_build_offline_end_to_end(self):
        d, ind = mk([row("AAA"), row("SPY", px=700, m50=710)], {"AAA": {"next_earn": "2026-10-28", "cat_notes": []}})
        EC.CD = os.path.join(d, "state", "calendar"); EC.OUT_STATIC = os.path.join(d, "static", "event_calendar.json"); EC.BASE = d
        o = EC.build(now=NOW, indir=ind, online=False)
        self.assertTrue(os.path.exists(EC.OUT_STATIC)); self.assertTrue(o["paper_only"])
        self.assertTrue(any(e["sym"] == "AAA" and e["type"] == "earnings" for e in o["next72"]))
        self.assertTrue(any(e["sym"] == "MACRO" for e in o["next72"]))
        self.assertEqual(o["staged"], [])
    def test_nasdaq_parser_and_feed_failure(self):
        def f(url, **k):
            if "nasdaq" in url: return json.dumps({"data": {"rows": [{"symbol": "MSFT", "time": "time-after-hours", "epsForecast": "$4.71", "noOfEsts": "14"}]}})
            raise IOError("down")
        evs = EC.earnings_nasdaq([dt.date(2026, 10, 28)], f)
        self.assertEqual(evs[0]["session"], "AMC"); self.assertEqual(evs[0]["when_utc"][:13], "2026-10-28T20")
        EC.FEEDS.clear(); self.assertEqual(EC.macro_ff(f)[0]["source"], "desk.static"); self.assertFalse(EC.FEEDS["forexfactory.macro"]["ok"])
    def test_sec_atom_parser(self):
        atom = '<feed><entry><title>8-K - ACME CORP (0000123456) (Filer)</title><updated>2026-10-26T13:00:00-04:00</updated><summary type="html">Item 2.01: x Item 9.01</summary><link href="https://sec/x"/></entry></feed>'
        evs = EC.sec_8k({123456: "ACME"}, lambda u, **k: atom)
        self.assertEqual(evs[0]["sym"], "ACME"); self.assertEqual(evs[0]["type"], "mna")
    def test_official_anchors_v12(self):
        ff = [{"country":"EUR","impact":"High","title":"Main Refinancing Rate","date":"2026-10-29T09:15:00-04:00"}]
        evs = EC.macro_ff(lambda u, **k: json.dumps(ff))
        titles = [e["title"] for e in evs]
        self.assertEqual(sum("Refinancing" in x or "ECB monetary policy decision" in x for x in titles), 1)  # FF item kept, anchor deduped
        self.assertTrue(any("EIA" in x and e["when_utc"].startswith("2026-10-28T14:30") for x, e in zip(titles, evs)))
        self.assertTrue(any("BoJ" in x for x in titles)); self.assertTrue(any("BoC" in x and e["when_utc"].startswith("2026-10-28T13:45") for x, e in zip(titles, evs)))
    def test_recent_material_8k_halves_once(self):
        now = dt.datetime(2026, 10, 27, 0, 0, tzinfo=EC.UTC)
        r = {"sym":"AAA","venue":"ibkr","px":"50","atr":"1","atr_pct":"2","adv_usd":"5e8","rsi":"50","roc5":"0","rank_score":"5","dir":"long","hi20":"51","lo10":"45","pdh":"50.5","pdl":"49","excluded":""}
        evs = [EC.ev("sec_8k_material","AAA",now,"8-K 1.01","sec",past=True), EC.ev("guidance","AAA",now,"guide up","yf",past=True,bias="long")]
        rv, st, _ = EC.review(evs, {"AAA": r}, {}, now, 0.7, regime={"ibkr":"long","kraken":"long"})
        self.assertEqual(rv[0]["size_mult"], 0.5); self.assertEqual(st[0]["risk_aud"], 1250.0)
    def test_breakeven_uses_realised_winners(self):
        r = {"sym":"AAA","venue":"ibkr","px":"50","atr":"1","atr_pct":"2","adv_usd":"5e8","rsi":"50","roc5":"0","hi20":"51","lo10":"45","pdh":"50.5","pdl":"49"}
        EC.REALISED_WIN = dict(n_closed=0, n_win=0, avg_win_R=None); lv = EC.stage_levels(r, "long", 2500, 0.7)
        self.assertIsNone(lv["breakeven_winrate"]); self.assertGreater(lv["breakeven_range"][1], lv["breakeven_range"][0]); self.assertIn("unknown", lv["breakeven_text"])
        EC.REALISED_WIN = dict(n_closed=4, n_win=2, avg_win_R=0.3); lv = EC.stage_levels(r, "long", 2500, 0.7)
        self.assertAlmostEqual(lv["breakeven_winrate"], round((1 + lv["cost_R"]) / 1.3, 3), places=3); EC.REALISED_WIN = None
if __name__ == "__main__": unittest.main(verbosity=1)
