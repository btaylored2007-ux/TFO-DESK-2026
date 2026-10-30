"""hunt_orchestrator.py (PAPER) - schedule, ranking/slot rules, gate re-check, re-slot, plan files. No network."""
import os, sys, json, csv, tempfile, unittest, importlib, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UTC = dt.timezone.utc
import hunt_orchestrator as HO
def W(y, mo, d, h, mi): return dt.datetime(y, mo, d, h, mi, tzinfo=HO.WITA).astimezone(UTC)
def bars_flat(px, n=260): return [dict(h=px * 1.01, l=px * 0.99, c=px)] * n   # audit H2: room_check needs >=200 daily bars
class Tmp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.saved = {k: getattr(HO, k) for k in ("BASE", "ORCH", "PLAN_DIR", "STATUS", "EVENTS")}
        HO.BASE = self.d; HO.ORCH = os.path.join(self.d, "state", "hunt_orch"); HO.PLAN_DIR = os.path.join(self.d, "reports", "daily_plan")
        HO.STATUS = os.path.join(self.d, "static", "hunt_orchestrator_status.json"); HO.EVENTS = os.path.join(HO.ORCH, "events.jsonl")
    def tearDown(self):
        for k, v in self.saved.items(): setattr(HO, k, v)

class Schedule(unittest.TestCase):
    def test_us_dst_shift(self):
        t, o, k = HO.run_at("us", W(2026, 10, 27, 19, 0))
        self.assertEqual(o.astimezone(HO.WITA).strftime("%H:%M"), "21:30"); self.assertEqual(t.astimezone(HO.WITA).strftime("%H:%M"), "20:00")
        t, o, k = HO.run_at("us", W(2026, 11, 2, 12, 0))      # EST from Sun 1 Nov
        self.assertEqual(o.astimezone(HO.WITA).strftime("%a %H:%M"), "Mon 22:30"); self.assertEqual(t.astimezone(HO.WITA).strftime("%H:%M"), "21:00")
    def test_us_weekend_and_holiday(self):
        t, o, k = HO.run_at("us", W(2026, 10, 31, 12, 0))     # Saturday -> Monday
        self.assertEqual(o.astimezone(HO.WITA).strftime("%a %d"), "Mon 02")
        t, o, k = HO.run_at("us", W(2026, 11, 26, 12, 0))     # Thanksgiving -> Friday
        self.assertEqual(o.astimezone(HO.WITA).strftime("%a %d"), "Fri 27")
    def test_asx_asia_lse(self):
        n = W(2026, 10, 28, 3, 0)
        self.assertEqual(HO.run_at("asx", n)[1].astimezone(HO.WITA).strftime("%H:%M"), "07:00")
        self.assertEqual(HO.run_at("asx", n)[0].astimezone(HO.WITA).strftime("%H:%M"), "06:05")
        self.assertEqual(HO.run_at("jp", n)[1].astimezone(HO.WITA).strftime("%H:%M"), "08:00")
        self.assertEqual(HO.run_at("hk", n)[1].astimezone(HO.WITA).strftime("%H:%M"), "09:30")
        self.assertEqual(HO.run_at("lse", n)[1].astimezone(HO.WITA).strftime("%H:%M"), "16:00")
    def test_fx_crypto(self):
        t, dl, k = HO.run_at("fx", W(2026, 10, 28, 3, 0))
        self.assertEqual(t.astimezone(HO.WITA).strftime("%a %H:%M"), "Wed 05:40")
        t, dl, k = HO.run_at("crypto", W(2026, 10, 28, 3, 0))
        self.assertEqual(t.astimezone(HO.WITA).strftime("%a %H:%M"), "Wed 08:10")
        t, dl, k = HO.run_at("crypto", W(2026, 10, 28, 9, 0))   # inside the window -> today's run still
        self.assertEqual(t.astimezone(HO.WITA).strftime("%a %H:%M"), "Wed 08:10")
    def test_fx_skips_weekend(self):
        t, dl, k = HO.run_at("fx", W(2026, 10, 31, 9, 0))     # Sat WITA -> next run after Sun NY open
        self.assertEqual(t.astimezone(HO.WITA).strftime("%a"), "Mon")

class Due(Tmp):
    def test_due_once_then_marker(self):
        n = W(2026, 10, 28, 6, 10)
        self.assertIn("asx", HO.due(n)); self.assertNotIn("us", HO.due(n))
        os.makedirs(os.path.join(HO.ORCH, "done")); json.dump({}, open(HO.marker(HO.run_at("asx", n)[2]), "w"))
        self.assertNotIn("asx", HO.due(n))
    def test_lock_blocks(self):
        n = W(2026, 10, 28, 6, 10); os.makedirs(HO.ORCH); open(os.path.join(HO.ORCH, "lock_asx"), "w").write("1")
        self.assertNotIn("asx", HO.due(n))
    def test_not_due_after_open(self):
        self.assertNotIn("asx", HO.due(W(2026, 10, 28, 7, 5)))

class Screen(unittest.TestCase):
    base = dict(sym="ABC", venue="ibkr", px="50", adv_usd="5e8", atr_pct="3", rsi="45", roc5="-3", excluded="", country="United States", earn_days="", relvol="1")
    def test_ok(self): self.assertEqual(HO.quick_screen(dict(self.base), "short"), [])
    def test_indonesian(self):
        self.assertTrue(any("Indonesian" in w for w in HO.quick_screen(dict(self.base, country="Indonesia"), "short")))
        self.assertTrue(HO.is_indonesian(dict(sym="USD_IDR"))); self.assertTrue(HO.is_indonesian(dict(sym="BBCA.JK")))
    def test_pump_chase(self):
        self.assertTrue(any("pump" in w for w in HO.quick_screen(dict(self.base, roc5="18", rsi="60"), "long")))
        self.assertTrue(any("pump" in w for w in HO.quick_screen(dict(self.base, roc5="11", relvol="4", rsi="60"), "long")))
    def test_earnings_and_squeeze(self):
        self.assertTrue(any("earnings" in w for w in HO.quick_screen(dict(self.base, earn_days="2"), "short")))
        self.assertTrue(any("squeeze" in w for w in HO.quick_screen(dict(self.base, short_pct_float="30"), "short")))
    def test_rsi_missing_short_blocked(self):
        self.assertTrue(HO.quick_screen(dict(self.base, rsi=""), "short"))

class Pick(unittest.TestCase):
    sec = staticmethod(lambda tk: "S_" + tk)
    def c(self, tk, score, sector, d="short"): return dict(ticker=tk, score=score, sector=sector, direction=d)
    def test_slots_sector_and_backups(self):
        cands = [self.c("A", 9, "Tech"), self.c("B", 8, "Tech"), self.c("C", 7, "Energy"), self.c("D", 6, "Banks"), self.c("E", 5, "Energy")]
        prim, back = HO.pick(cands, [dict(ticker="X", direction="long", sector="Banks")], 2, self.sec)
        self.assertEqual([p["ticker"] for p in prim], ["A", "C"])          # B same sector as A; D clashes with held X
        self.assertEqual([b["ticker"] for b in back], ["B", "D", "E"])
        self.assertIn("re-checked", back[1].get("backup_note", ""))
    def test_no_slots_all_backups(self):
        prim, back = HO.pick([self.c("A", 9, "Tech"), self.c("B", 8, "Energy")], [], 0, self.sec)
        self.assertEqual(prim, []); self.assertEqual(len(back), 2)
    def test_direction_balance(self):
        hold = [dict(ticker="X", direction="short", sector="a"), dict(ticker="Y", direction="short", sector="b"), dict(ticker="Z", direction="short", sector="e")]   # 2026-10-29 20:17 cap5k: 4 slots -> last slot after 3 same-direction
        prim, back = HO.pick([self.c("A", 9, "c"), self.c("B", 5, "d", "long")], hold, 1, self.sec)
        self.assertEqual([p["ticker"] for p in prim], ["B"])
    def test_skips_held_ticker(self):
        prim, back = HO.pick([self.c("X", 9, "c")], [dict(ticker="X", direction="short", sector="z")], 1, self.sec)
        self.assertEqual(prim + back, [])

class Candidate(unittest.TestCase):
    def row(self, **k):
        r = dict(sym="ABC", venue="ibkr", px="100", atr="2", atr_pct="2", lo10="99", pdl="99.5", hi20="105", pdh="101", rsi="45", roc5="-1", adv_usd="5e8", rank_score="5", sector="Tech")
        r.update(k); return r
    def test_levels_t2_15R_and_room(self):
        c, why = HO.build_candidate(self.row(), "short", 0.66, bars_flat(140))     # no support below -> T2 room 1.5R - cost
        self.assertIsNotNone(c); self.assertEqual(why, [])
        R = c["stop"] - c["max_fill"]; self.assertAlmostEqual((c["max_fill"] - c["t2"]) / R, 1.5, places=2)
        self.assertGreater(c["stop"], c["max_fill"]); self.assertLessEqual(c["shares"] * R, 5000 * 0.66 + 1)   # cap5k: A$5k US main hunt cap
        self.assertGreaterEqual(c["room_R"], 1.0); self.assertIn("analysis", c)
    def test_room_block(self):
        bars = [dict(h=101, l=100, c=100.5)] * 30 + [dict(h=99, l=97.5, c=98), dict(h=99, l=97.6, c=98)] + [dict(h=101, l=100, c=100.5)] * 30
        bars = [dict(h=b["h"], l=b["l"], c=b["c"]) for b in bars]
        bars[40] = dict(h=97.6, l=97.0, c=97.2)     # swing low just under trigger -> < 1R room
        c, why = HO.build_candidate(self.row(), "short", 0.66, bars)
        self.assertTrue(any("room" in w for w in why))
    def test_no_bars_fail_closed(self):
        c, why = HO.build_candidate(self.row(), "short", 0.66, [])
        self.assertTrue(why)

class Recheck(Tmp):
    def setUp(self):
        super().setUp(); import hunt48_short_paper as H; self.H = H
        self._sec = H.sector_of; H.sector_of = lambda tk, t=None: (t or {}).get("sector") or {"X": "Banks", "Y": "Energy"}.get(tk, "S_" + tk)
    def tearDown(self): self.H.sector_of = self._sec; super().tearDown()
    def b(self, **k):
        d = dict(ticker="ABC", direction="short", trigger=100.0, max_fill=99.85, stop=103.0, t1=98.5, t2=95.1, shares=500, sector="Tech"); d.update(k); return d
    def L(self, trades): return dict(trades=trades)
    def test_ok(self): self.assertEqual(HO.recheck_us(self.b(), self.L([]), 100.5, bars_flat(140), None), [])
    def test_missed(self): self.assertTrue(HO.recheck_us(self.b(), self.L([]), 97.0, bars_flat(140), None)[0].startswith("missed"))
    def test_through_stop(self): self.assertTrue(HO.recheck_us(self.b(), self.L([]), 103.5, bars_flat(140), None)[0].startswith("invalidated"))
    def test_sector_clash(self):
        why = HO.recheck_us(self.b(sector="Banks"), self.L([dict(ticker="X", status="FILLED", direction="short", sector="Banks")]), 100.5, bars_flat(140), None)
        self.assertTrue(any("sector cap" in w for w in why))
    def test_full_book(self):
        T = [dict(ticker=t, status="FILLED", direction="short", sector=t) for t in ("P", "Q")] + [dict(ticker=t, status="ARMED", direction="long", sector=t) for t in ("R", "S")]   # cap5k: 4 slots
        self.assertIn("no open slot", HO.recheck_us(self.b(), self.L(T), 100.5, bars_flat(140), None))
    def test_no_bars(self): self.assertIn("no daily bars (fail closed)", HO.recheck_us(self.b(), self.L([]), 100.5, [], None))

class Reslot(Tmp):
    def setUp(self):
        super().setUp(); import hunt48_short_paper as H; self.H = H; self._sec = H.sector_of
        H.sector_of = lambda tk, t=None: (t or {}).get("sector") or "S_" + tk
        self.calls = []; self._apply = HO.apply_arm
        HO.apply_arm = lambda book, specs, D, why, log, wait=True: self.calls.append((book, [s["ticker"] for s in specs])) or True
        os.makedirs(os.path.join(self.d, "state", "hunt48_short"))
        HO.atomic(os.path.join(HO.ORCH, "config.json"), dict(open_gates=False))   # legacy re-slot path; gated path in test_open_gates.py
    def tearDown(self): HO.apply_arm = self._apply; self.H.sector_of = self._sec; super().tearDown()
    def write(self, trades, backups, now):
        json.dump(dict(trades=trades), open(os.path.join(self.d, "state", "hunt48_short", "ledger.json"), "w"))
        os.makedirs(HO.ORCH, exist_ok=True); json.dump(dict(main=backups), open(os.path.join(HO.ORCH, f"us_backups_{HO.dkey(now)}.json"), "w"))
    def bk(self, tk, rank, **k):
        d = dict(ticker=tk, rank=rank, state="READY", direction="short", trigger=100.0, max_fill=99.85, stop=103.0, t1=98.5, t2=95.1, shares=500, sector="Sec" + tk, top_decile=True); d.update(k); return d
    def test_slots_next_valid_backup(self):
        now = W(2026, 10, 27, 22, 0)
        T = [dict(ticker="P", status="FILLED", direction="long", sector="P"), dict(ticker="Q", status="FILLED", direction="short", sector="Q")]
        self.write(T, [self.bk("GONE", 1), self.bk("NEXT", 2), self.bk("LATER", 3)], now)
        px = {"GONE": 96.0, "NEXT": 100.4, "LATER": 100.4}
        done = HO.reslot(now, [], px_fn=lambda s: px[s], daily_fn=lambda s: bars_flat(140))
        self.assertEqual(done, [("main", "NEXT")]); self.assertEqual(self.calls, [("main", ["NEXT"])])
        st = json.load(open(os.path.join(HO.ORCH, f"us_backups_{HO.dkey(now)}.json")))["main"]
        self.assertEqual([b["state"] for b in st], ["INVALID", "ARMED", "READY"])
    def test_direction_balance_blocks_third_short(self):
        now = W(2026, 10, 27, 22, 0)
        T = [dict(ticker=t, status="FILLED", direction="short", sector=t) for t in ("P", "Q", "O")]   # cap5k: 4 slots -> 4th same-direction blocked
        self.write(T, [self.bk("NEXT", 1)], now)
        self.assertEqual(HO.reslot(now, [], px_fn=lambda s: 100.4, daily_fn=lambda s: bars_flat(140)), [])
        st = json.load(open(os.path.join(HO.ORCH, f"us_backups_{HO.dkey(now)}.json")))["main"]
        self.assertEqual(st[0]["state"], "READY"); self.assertTrue(any("direction balance" in w for w in st[0]["last_block"]))
    def test_full_book_no_arm(self):
        now = W(2026, 10, 27, 22, 0)
        T = [dict(ticker=t, status="FILLED", direction="short", sector=t) for t in ("P", "Q", "R")]
        self.write(T, [self.bk("NEXT", 1)], now)
        self.assertEqual(HO.reslot(now, [], px_fn=lambda s: 100.4, daily_fn=lambda s: bars_flat(140)), []); self.assertEqual(self.calls, [])
    def test_armed_primary_counts_as_slot(self):
        now = W(2026, 10, 27, 22, 0)
        T = [dict(ticker="P", status="FILLED", direction="short", sector="P"), dict(ticker="Q", status="ARMED", direction="long", sector="Q"),
             dict(ticker="Z", status="ARMED", bench=True, direction="short", sector="Z")]
        self.write(T, [self.bk("NEXT", 1)], now)
        self.assertEqual(HO.reslot(now, [], px_fn=lambda s: 100.4, daily_fn=lambda s: bars_flat(140)), [("main", "NEXT")])
    def test_comparison_books_not_auto_armed(self):
        self.assertEqual(HO.cfg()["auto_arm_us_books"], ["main"])

class Plans(Tmp):
    def test_engine_plan_backups_ranked_and_demoted_excluded(self):
        os.makedirs(os.path.join(self.d, "state", "hunt48_jp"))
        T = [dict(sym="1", status="FILLED", direction="short", sector="a", trigger=1, stop=2),
             dict(sym="2", status="BENCH", direction="short", sector="b", score=3, room_R=1.4, clearance=dict(desk="PASS")),
             dict(sym="3", status="BENCH", direction="short", sector="c", score=5, room_R=1.2, clearance=dict(desk="PENDING")),
             dict(sym="4", status="BENCH", direction="short", sector="d", score=9, room_R=1.5, clearance=dict(desk="PASS"), bench_note="DEMOTED x"),
             dict(sym="5", status="BENCH", direction="short", sector="e", score=4, room_R=0.4, clearance=dict(desk="PASS"))]
        json.dump(dict(trades=T), open(os.path.join(self.d, "state", "hunt48_jp", "ledger.json"), "w"))
        p = HO.plan_engine("jp", W(2026, 10, 28, 7, 0))
        self.assertEqual([b["sym"] for b in p["backups"]], ["2", "3", "5"]); self.assertEqual(p["backups"][0]["state"], "READY")
        self.assertEqual(len(p["open"]), 1)
    def test_plan_all_writes_status_and_md(self):
        S = HO.plan("all", W(2026, 10, 28, 7, 0))
        for k in ("us_main", "us_gpt", "us_claude", "asx", "jp", "hk", "sg", "kr", "tw", "lse", "oanda", "crypto"): self.assertIn(k, S["books"])
        self.assertTrue(os.path.exists(os.path.join(HO.PLAN_DIR, "20261028", "asx.md")))
        self.assertEqual(S["books"]["asx"]["open_slots"], 3); self.assertTrue(S["books"]["asx"]["thin_bench"]); self.assertTrue(S["paper_only"])
    def test_fx_plan_slots_max10_and_no_shadow_classes(self):
        inp = os.path.join(self.d, "state", "calendar", "inputs"); os.makedirs(inp); os.makedirs(os.path.join(self.d, "state", "oanda"))
        cols = ["sym", "venue", "cls", "dir", "px", "atr", "atr_pct", "lo10", "pdl", "hi20", "pdh", "rsi", "roc5", "rank_score", "cost_pct", "yahoo", "excluded", "country", "adv_usd"]
        rows = [dict(sym="EUR_USD", venue="oanda", cls="fx_major", dir="long", px=1.10, atr=0.008, atr_pct=0.7, lo10=1.08, pdl=1.09, hi20=1.11, pdh=1.105, rsi=60, roc5=1, rank_score=5, cost_pct=0.01, yahoo="EURUSD=X", excluded="", country="", adv_usd=1e12),
                dict(sym="USD_TRY", venue="oanda", cls="fx_exotic", dir="long", px=40, atr=0.2, atr_pct=0.5, lo10=39, pdl=39.5, hi20=41, pdh=40.5, rsi=60, roc5=1, rank_score=9, cost_pct=0.01, yahoo="TRY=X", excluded="", country="", adv_usd=1e12),
                dict(sym="AUD_USD", venue="oanda", cls="fx_major", dir="short", px=0.66, atr=0.005, atr_pct=0.7, lo10=0.65, pdl=0.655, hi20=0.67, pdh=0.665, rsi=50, roc5=0, rank_score=1, cost_pct=0.01, yahoo="AUDUSD=X", excluded="", country="", adv_usd=1e12)]
        with open(os.path.join(inp, "ranked_oanda.csv"), "w") as f:
            w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); [w.writerow(r) for r in rows]
        json.dump(dict(nav=50000, trades=[dict(instrument="XAU_USD", owner="TFO", units=1), dict(instrument="NAS100_USD", owner="Brett (pre-existing, untouched)", units=1)]), open(os.path.join(self.d, "state", "oanda", "snapshot.json"), "w"))
        json.dump(dict(queue=[dict(ins="XPT_USD", dir=-1, trigger=1)]), open(os.path.join(self.d, "state", "oanda", "pending.json"), "w"))
        json.dump(dict(max_trades=10), open(os.path.join(self.d, "state", "oanda", "config.json"), "w"))
        p = HO.plan_fx(W(2026, 10, 28, 7, 0), bars_fn=lambda s: bars_flat(1.0))
        syms = [b["sym"] for b in p["backups"]]
        self.assertNotIn("USD_TRY", syms); self.assertEqual(p["max_open"], 10); self.assertEqual(len(p["open"]), 1); self.assertEqual(len(p["armed"]), 1)
        self.assertEqual(p["risk_pct"], 1.0)

class Safety(unittest.TestCase):
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hunt_orchestrator.py")).read()
    def test_never_calls_order_paths(self):
        for bad in ("oanda_sleeve.py", "entry_engine.py", "api-fxtrade", "api-fxpractice", "sudo ", "OPENAI_API_KEY", "/orders"):
            self.assertNotIn(bad, self.src.replace("oanda_sleeve.py owns", "").replace("oanda_sleeve.py)", ""), bad)
    def test_no_ledger_writes(self):
        self.assertNotIn('atomic(os.path.join(BASE, "state", US_BOOKS', self.src); self.assertNotIn("ledger.json\"), L", self.src)
    def test_untouchables(self):
        for bad in ("8798", "kdesk", "betfair", "HEAD BETTER"): self.assertNotIn(bad.lower(), self.src.lower())
if __name__ == "__main__": unittest.main()
