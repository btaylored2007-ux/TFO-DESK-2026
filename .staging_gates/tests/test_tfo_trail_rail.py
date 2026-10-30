"""Offline tests for the V1 variant-C exit rail (Head Trader 2026-09-25). No network, no live ledgers."""
import contextlib, datetime as dt, json, os, shutil, sys, tempfile, types, unittest
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tfo_trail_rail as trl

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 25, 14, 0, tzinfo=UTC)
NOLOCK = types.SimpleNamespace(ledger_lock=lambda *a, **k: contextlib.nullcontext())


class PureRail(unittest.TestCase):
    def test_pre_half_stop_long_and_short(self):
        self.assertEqual(trl.decide(100, 95.0, "long", False)["act"], "STOP")
        self.assertIsNone(trl.decide(100, 95.2, "long", False)["act"])
        self.assertEqual(trl.decide(100, 104.0, "short", False)["act"], "STOP")      # shorts stop at -4%
        self.assertIsNone(trl.decide(100, 103.9, "short", False)["act"])

    def test_half_bank_at_plus_two(self):
        d = trl.decide(100, 102.0, "long", False)
        self.assertEqual((d["act"], d["rule"]), ("HALF", "HALF_2PCT"))
        self.assertIsNone(trl.decide(100, 101.9, "long", False)["act"])

    def test_no_plus_five_cap(self):
        self.assertEqual(trl.decide(100, 109.0, "long", False)["act"], "HALF")      # unbanked +9% banks HALF, never FLAT
        d = trl.decide(100, 108.0, "long", True, wm=108.0, atr_pct=0.01, held_age=3)
        self.assertIsNone(d["act"])                                                  # banked +8% at its high: hold
        self.assertIsNone(trl.decide(100, 125.0, "long", True, wm=120.0, atr_pct=0.01, held_age=3)["act"])

    def test_no_second_half_after_bank(self):
        self.assertIsNone(trl.decide(100, 104.5, "long", True, wm=104.5, atr_pct=0.01, held_age=1)["act"])

    def test_trail_exit_three_atr(self):
        d = trl.decide(100, 103.5, "long", True, wm=110.0, atr_pct=0.02, held_age=5)  # trail 6% -> 103.4
        self.assertAlmostEqual(d["level"], 103.4); self.assertIsNone(d["act"])
        d = trl.decide(100, 103.39, "long", True, wm=110.0, atr_pct=0.02, held_age=5)
        self.assertEqual((d["act"], d["rule"]), ("FLAT", "TRAIL_3ATR"))

    def test_trail_floor_three_pct(self):
        self.assertAlmostEqual(trl.trail_pct("long", 0.005), 0.03)
        self.assertAlmostEqual(trl.trail_pct("long", None), 0.03)
        self.assertAlmostEqual(trl.trail_pct("short", 0.005), 0.04)
        self.assertAlmostEqual(trl.trail_pct("long", 0.02), 0.06)

    def test_breakeven_floor(self):
        d = trl.decide(100, 100.5, "long", True, wm=101.0, atr_pct=0.02, held_age=2)
        self.assertEqual(d["level"], 100.0); self.assertIsNone(d["act"])
        d = trl.decide(100, 99.9, "long", True, wm=101.0, atr_pct=0.02, held_age=2)
        self.assertEqual((d["act"], d["rule"]), ("FLAT", "BE_FLOOR"))

    def test_seed_from_current_price_when_already_banked(self):
        d = trl.decide(100, 102.6, "long", True, wm=None, atr_pct=0.05, held_age=1)  # 15% trail -> BE floor
        self.assertEqual(d["wm"], 102.6); self.assertEqual(d["level"], 100.0); self.assertIsNone(d["act"])

    def test_gap_through_original_stop_after_bank(self):
        self.assertEqual(trl.decide(100, 94.0, "long", True, wm=103, atr_pct=0.01, held_age=1)["act"], "STOP")

    def test_max_hold_equity_and_crypto(self):
        self.assertIsNone(trl.decide(100, 104, "long", True, wm=104, atr_pct=0.01, held_age=29)["act"])
        d = trl.decide(100, 104, "long", True, wm=104, atr_pct=0.01, held_age=30)
        self.assertEqual((d["act"], d["rule"]), ("FLAT", "MAX_HOLD_30D"))            # HT 2026-09-28 equity label
        self.assertAlmostEqual(trl.held("2026-08-27T12:00:00Z", NOW, False), 29 + 2 / 24.0)  # calendar days from fill
        self.assertAlmostEqual(trl.held("2026-08-26T14:00:00Z", NOW, True), 30.0)   # crypto: calendar days
        self.assertEqual(trl.decide(100, 104, "long", True, wm=104, atr_pct=0.01, held_age=30.0, crypto=True)["rule"], "MAX_HOLD")
        self.assertIsNone(trl.decide(100, 101, "long", False, held_age=99)["act"])   # max hold is on the residual only

    def test_max_hold_is_30_calendar_days_from_fill_not_weekdays(self):
        # HT ruling 2026-09-28: 30 CALENDAR days from the original fill time (was 30 weekdays after the entry day)
        e = "2026-08-27T12:14:49Z"
        self.assertLess(trl.held(e, "2026-09-26T12:14:48Z"), 30)
        self.assertAlmostEqual(trl.held(e, "2026-09-26T12:14:49Z"), 30.0)
        self.assertIsNone(trl.decide(100, 104, "long", True, wm=104, atr_pct=0.01, held_age=trl.held(e, "2026-09-26T12:14:48Z"))["act"])
        d = trl.decide(100, 104, "long", True, wm=104, atr_pct=0.01, held_age=trl.held(e, "2026-09-26T12:14:49Z"))
        self.assertEqual((d["act"], d["rule"]), ("FLAT", "MAX_HOLD_30D"))
        self.assertEqual(trl.weekdays_after(dt.date(2026, 8, 27), dt.date(2026, 9, 26)), 21)   # old unit would still be holding
        self.assertEqual(trl.max_hold_due_utc(e), dt.datetime(2026, 9, 26, 12, 14, 49, tzinfo=UTC))
        d = trl.decide(100, 96, "short", True, wm=96, atr_pct=0.01, held_age=31)                 # shorts too
        self.assertEqual((d["act"], d["rule"]), ("FLAT", "MAX_HOLD_30D"))
        self.assertEqual(trl.decide(100, 104, "long", True, wm=104, atr_pct=0.01, held_age=31, crypto=True)["rule"], "MAX_HOLD")  # crypto label unchanged
        self.assertIsNone(trl.held(None, NOW)); self.assertIsNone(trl.max_hold_due_utc(None))

    def test_entry_time_reads_entry_or_opened_utc(self):
        self.assertEqual(trl.entry_time({"entry_utc": "2026-09-01T00:00:00Z", "opened_utc": "2026-08-01T00:00:00Z"}), "2026-09-01T00:00:00Z")
        self.assertEqual(trl.entry_time({"opened_utc": "2026-09-06T08:33:46Z"}), "2026-09-06T08:33:46Z")   # convexity_sleeve
        self.assertIsNone(trl.entry_time({})); self.assertIsNone(trl.entry_time(None))

    def test_short_mirror(self):
        self.assertEqual(trl.decide(100, 98.0, "short", False)["act"], "HALF")
        d = trl.decide(100, 93.5, "short", True, wm=90.0, atr_pct=0.01, held_age=3)  # 4% floor -> 93.6
        self.assertAlmostEqual(d["level"], 93.6); self.assertIsNone(d["act"])
        d = trl.decide(100, 93.7, "short", True, wm=90.0, atr_pct=0.01, held_age=3)
        self.assertEqual((d["act"], d["rule"]), ("FLAT", "TRAIL_3ATR"))
        d = trl.decide(100, 100.1, "short", True, wm=99.0, atr_pct=0.01, held_age=3)
        self.assertEqual((d["act"], d["rule"]), ("FLAT", "BE_FLOOR"))
        self.assertEqual(trl.decide(100, 89.0, "short", True, wm=90.0, atr_pct=0.01)["wm"], 89.0)  # LWM ratchets down

    def test_weekdays_and_atr(self):
        self.assertEqual(trl.weekdays_after(dt.date(2026, 9, 25), dt.date(2026, 9, 28)), 1)   # Fri -> Mon
        self.assertEqual(trl.weekdays_after(dt.date(2026, 9, 21), dt.date(2026, 10, 5)), 10)
        bars = [(101.0, 99.0, 100.0)] * 30
        self.assertAlmostEqual(trl.atr_pct_from_bars(bars), 0.02)
        self.assertIsNone(trl.atr_pct_from_bars(bars[:10]))
        st = trl.refresh_atr({}, lambda: (_ for _ in ()).throw(RuntimeError("offline")), "2026-09-25")
        self.assertNotIn("atr14_pct", st); self.assertIn("atr_error", st)


class LedgerHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def w(self, k, d):
        json.dump(d, open(os.path.join(self.tmp, k + ".json"), "w"))

    def r(self, k):
        return json.load(open(os.path.join(self.tmp, k + ".json")))


class EquityEngine(LedgerHarness):
    def setUp(self):
        super().setUp()
        import tfo_ticket as tt
        self.tt = tt
        for k in ("fundamental_spec_book", "convexity_sleeve"): self.w(k, {"positions": {}, "cash": 0, "seed_usd": 0})
        self.w("gap_close_levered_book", {"cash": 0.0, "seed_usd": 0.0, "start": 0.0, "holdings": {}})
        self.px = {}
        for p in (patch.object(tt, "R", self.tmp), patch.object(tt, "LOG", os.path.join(self.tmp, "log.txt")),
                  patch.object(tt, "FACTS", os.path.join(self.tmp, "facts.jsonl")), patch.object(tt, "desk_lock", NOLOCK),
                  patch.object(tt.fb, "fetch_prices", self.quotes), patch.object(trl, "equity_atr_pct", lambda s: 0.01)):
            p.start(); self.addCleanup(p.stop)
        import rail_watchdog  # 2026-09-28: keep the live watchdog state out of offline tests
        for p in (patch.object(rail_watchdog, "PATH", os.path.join(self.tmp, "rail_watchdog.json")),
                  patch.object(rail_watchdog, "EVENTS", os.path.join(self.tmp, "rail_watchdog_events.jsonl"))):
            p.start(); self.addCleanup(p.stop)
        if hasattr(tt, "MARK_REFRESH_ENABLED"):  # the post-cycle mark refresh never touches live ledgers from tests
            p = patch.object(tt, "MARK_REFRESH_ENABLED", False); p.start(); self.addCleanup(p.stop)
        if hasattr(tt, "CORP_ACTIONS_PRECHECK"):  # 2026-09-29: nor the corporate-actions pre-check (live Yahoo + ledgers)
            p = patch.object(tt, "CORP_ACTIONS_PRECHECK", False); p.start(); self.addCleanup(p.stop)

    def quotes(self, syms, require_executable=False):
        now = dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {s: {"px_usd": self.px[s], "px": self.px[s], "ccy": "USD", "quote_current": True, "age_sec": 5, "session": "regular",
                    "executable": True, "quote_utc": now, "paper_price_class": "CURRENT PAPER", "status": "regular_current"} for s in syms}, {}

    def book(self, **pos):
        self.w("fundamental_book", {"positions": pos, "cash": 0.0, "seed_usd": 100000.0, "start": 100000.0, "realised_usd": 0.0})

    def pos(self, side="long", **kw):
        p = {"side": side, "units": 100.0, "entry_px_usd": 100.0, "notional_usd": 10000.0, "rail_orig_entry_usd": 100.0,
             "entry_utc": (dt.datetime.now(UTC) - dt.timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ"), "mark_usd": 100.0}
        p.update(kw); return p

    def run_cycle(self):
        with patch.object(sys, "argv", ["tfo_ticket.py", "--execute"]), contextlib.redirect_stdout(open(os.devnull, "w")):
            self.tt.main()

    def test_plus_six_unbanked_banks_half_not_flat_and_seeds_trail(self):
        self.book(AAA=self.pos()); self.px["AAA"] = 106.0
        self.run_cycle()
        p = self.r("fundamental_book")["positions"]["AAA"]
        self.assertAlmostEqual(p["units"], 50.0)
        self.assertEqual(p["rail_trail"]["hwm_usd"], 106.0); self.assertEqual(p["rail_trail"]["seeded"], "half_fill")
        line = open(os.path.join(self.tmp, "log.txt")).read().split()
        self.assertEqual(line[0:5:4], ["LOG", "AAA"]); self.assertEqual(line[3], "HALF")

    def test_already_banked_seeds_at_current_then_trails_out_as_flat(self):
        self.book(BBB=self.pos(units=50.0, notional_usd=5000.0, rail_half_count=1, rail_rebase_usd=102.0)); self.px["BBB"] = 110.0
        self.run_cycle()
        p = self.r("fundamental_book")["positions"]["BBB"]
        self.assertEqual(p["rail_trail"]["hwm_usd"], 110.0); self.assertEqual(p["rail_trail"]["seeded"], "current_px_at_switch")
        self.assertAlmostEqual(p["rail_trail"]["level_usd"], 110.0 * 0.97)
        self.px["BBB"] = 106.5                                                     # below 106.7 trail
        self.run_cycle()
        d = self.r("fundamental_book")
        self.assertNotIn("BBB", d["positions"])
        tk = d["head_trader_tickets"][-1]
        self.assertEqual((tk["action"], tk["exit_rule"]), ("FLAT", "TRAIL_3ATR"))
        self.assertIn(" FLAT BBB ", open(os.path.join(self.tmp, "log.txt")).read())

    def test_preview_writes_nothing(self):
        self.book(CCC=self.pos(units=50.0, rail_half_count=1, rail_rebase_usd=102.0)); self.px["CCC"] = 104.0
        before = open(os.path.join(self.tmp, "fundamental_book.json")).read()
        with patch.object(sys, "argv", ["tfo_ticket.py"]), contextlib.redirect_stdout(open(os.devnull, "w")):
            self.tt.main()
        self.assertEqual(before, open(os.path.join(self.tmp, "fundamental_book.json")).read())
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "log.txt")))

    def test_short_banked_breakeven_exit(self):
        self.book(SSS=self.pos(side="short", units=50.0, rail_half_count=1, rail_rebase_usd=97.0,
                               rail_trail={"half_banked": True, "lwm_usd": 97.5, "atr14_pct": 0.01, "atr_date": "2099-01-01"})); self.px["SSS"] = 100.2
        self.run_cycle()
        d = self.r("fundamental_book")
        self.assertNotIn("SSS", d["positions"])
        self.assertEqual(d["head_trader_tickets"][-1]["exit_rule"], "BE_FLOOR")


    # --- 2026-09-28 HT: 30-calendar-day max hold through the normal rail path -------------------------------
    def old_utc(self, days):
        return (dt.datetime.now(UTC) - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_max_hold_30d_fires_as_flat_with_exit_costs(self):
        self.book(MMM=self.pos(units=50.0, notional_usd=5000.0, rail_half_count=1, rail_rebase_usd=102.0, entry_utc=self.old_utc(30.2),
                               rail_trail={"half_banked": True, "hwm_usd": 104.0, "atr14_pct": 0.01, "atr_date": "2099-01-01"})); self.px["MMM"] = 103.5
        self.run_cycle()
        d = self.r("fundamental_book")
        self.assertNotIn("MMM", d["positions"]); self.assertIn("MMM", d["closed"])
        tk = d["head_trader_tickets"][-1]
        self.assertEqual((tk["action"], tk["exit_rule"]), ("FLAT", "MAX_HOLD_30D"))
        self.assertGreater(tk["fees_usd"], 0); self.assertGreater(d["costs_paid_usd"], 0)            # exit costs carried
        self.assertAlmostEqual(tk["net_proceeds_usd"], tk["proceeds_usd"] - tk["fees_usd"], places=2)
        self.assertIn(" FLAT MMM ", open(os.path.join(self.tmp, "log.txt")).read())

    def test_max_hold_short_residual_fires(self):
        self.book(SHO=self.pos(side="short", units=50.0, notional_usd=5000.0, rail_half_count=1, rail_rebase_usd=97.0, entry_utc=self.old_utc(32),
                               rail_trail={"half_banked": True, "lwm_usd": 95.0, "atr14_pct": 0.01, "atr_date": "2099-01-01"})); self.px["SHO"] = 96.0
        self.run_cycle()
        d = self.r("fundamental_book"); self.assertNotIn("SHO", d["positions"])
        self.assertEqual(d["head_trader_tickets"][-1]["exit_rule"], "MAX_HOLD_30D")

    def test_max_hold_not_before_30_calendar_days(self):
        self.book(YNG=self.pos(units=50.0, notional_usd=5000.0, rail_half_count=1, rail_rebase_usd=102.0, entry_utc=self.old_utc(29.9),
                               rail_trail={"half_banked": True, "hwm_usd": 104.0, "atr14_pct": 0.01, "atr_date": "2099-01-01"})); self.px["YNG"] = 103.5
        self.run_cycle()
        self.assertIn("YNG", self.r("fundamental_book")["positions"])

    def test_max_hold_never_fills_on_closed_or_stale_print(self):
        self.book(STL=self.pos(units=50.0, notional_usd=5000.0, rail_half_count=1, rail_rebase_usd=102.0, entry_utc=self.old_utc(40),
                               rail_trail={"half_banked": True, "hwm_usd": 104.0, "atr14_pct": 0.01, "atr_date": "2099-01-01"})); self.px["STL"] = 103.5
        closed = lambda syms, require_executable=False: ({s: {"px_usd": self.px[s], "px": self.px[s], "ccy": "USD", "quote_current": False, "age_sec": 200000,
                                                              "session": "closed", "executable": False, "paper_executable": False,
                                                              "quote_utc": self.old_utc(2), "paper_price_class": None} for s in syms}, {})
        with patch.object(self.tt.fb, "fetch_prices", closed):
            rows = self.tt.scan()
            self.run_cycle()
        self.assertEqual((rows[0]["rail"], rows[0]["exit_rule"], rows[0]["status"]), ("FLAT", "MAX_HOLD_30D", "BLOCKED"))
        self.assertIn("STL", self.r("fundamental_book")["positions"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "log.txt")))

    def test_convexity_opened_utc_is_read_for_max_hold_and_age(self):
        self.book()
        pos = {"units": 10.0, "premium_usd": 1000.0, "mark_usd": 1030.0, "instrument": "equity", "opened_utc": self.old_utc(23),
               "rail_orig_entry_usd": 100.0, "rail_half_count": 1, "rail_rebase_usd": 102.0,
               "rail_trail": {"half_banked": True, "hwm_usd": 104.0, "atr14_pct": 0.01, "atr_date": "2099-01-01"}}
        self.w("convexity_sleeve", {"positions": {"CVX1": pos}, "cash": 0.0, "seed_usd": 0.0}); self.px["CVX1"] = 103.5
        rows = [r for r in self.tt.scan() if r["sym"] == "CVX1"]
        self.assertAlmostEqual(rows[0]["held_days"], 23.0, places=1); self.assertEqual(rows[0]["entry_time_utc"], pos["opened_utc"])
        self.assertIsNone(rows[0]["rail"])
        pos["opened_utc"] = self.old_utc(31)
        self.w("convexity_sleeve", {"positions": {"CVX1": pos}, "cash": 0.0, "seed_usd": 0.0})
        self.run_cycle()
        d = self.r("convexity_sleeve"); self.assertNotIn("CVX1", d["positions"])
        self.assertEqual(d["head_trader_tickets"][-1]["exit_rule"], "MAX_HOLD_30D")
        self.assertEqual(d["closed"][-1]["position"]["opened_utc"], pos["opened_utc"])                # original fill time kept


    # --- 2026-09-28 HT #3: mark refresh rides the rail cycle -----------------------------------------------
    def test_mark_refresh_runs_on_rail_cycle_reusing_quotes(self):
        import tfo_equity_refresh as ter, desk_lock
        self.book(MKT=self.pos(mark_usd=100.0)); self.px["MKT"] = 101.0
        net = []
        for p in (patch.object(self.tt, "MARK_REFRESH_ENABLED", True), patch.object(ter, "REPORTS", __import__("pathlib").Path(self.tmp)),
                  patch.object(desk_lock, "LOCKDIR", self.tmp), patch.object(ter, "fetch_quote", lambda s: net.append(s))):
            p.start(); self.addCleanup(p.stop)
        self.run_cycle()
        p = self.r("fundamental_book")["positions"]["MKT"]
        self.assertEqual(p["mark_usd"], 101.0); self.assertIn("mark_checked_utc", p)
        self.assertEqual((p["entry_px_usd"], p["units"]), (100.0, 100.0))                      # valuation only
        self.assertEqual(net, [])                                                                # no duplicate quote fetch
        self.assertTrue([d for d in os.listdir(os.path.join(self.tmp, "revisions", "rollback")) if d.startswith("equity-refresh-auto-")])

    def test_mark_refresh_failure_never_breaks_rail_cycle(self):
        import tfo_equity_refresh as ter
        self.book(ERR=self.pos(units=50.0, notional_usd=5000.0, rail_half_count=1, rail_rebase_usd=102.0, entry_utc=self.old_utc(31),
                               rail_trail={"half_banked": True, "hwm_usd": 104.0, "atr14_pct": 0.01, "atr_date": "2099-01-01"})); self.px["ERR"] = 103.5
        with patch.object(self.tt, "MARK_REFRESH_ENABLED", True), patch.object(ter, "refresh", lambda **k: (_ for _ in ()).throw(RuntimeError("desk tick is running"))):
            self.assertIn("skipped", self.tt.refresh_marks({}))
            self.run_cycle()
        self.assertNotIn("ERR", self.r("fundamental_book")["positions"])                           # the exit still fired

    def test_prune_only_auto_backups(self):
        import tfo_equity_refresh as ter
        root = os.path.join(self.tmp, "revisions", "rollback")
        for n in ("equity-refresh-auto-1", "equity-refresh-auto-2", "equity-refresh-auto-3", "equity-refresh-20260101T000000Z"):
            os.makedirs(os.path.join(root, n))
        self.assertEqual(ter.prune_backups("equity-refresh-auto-", 1, reports=self.tmp), 2)
        self.assertEqual(sorted(os.listdir(root)), ["equity-refresh-20260101T000000Z", "equity-refresh-auto-3"])
        self.assertEqual(ter.prune_backups("equity-refresh-", 1, reports=self.tmp), 0)            # manual backups never pruned


class CryptoEngine(LedgerHarness):
    def setUp(self):
        super().setUp()
        import tfo_crypto as tc, tfo_ticket as tt
        self.tc = tc; self.bid = {}
        self.w("gap_close_levered_book", {"cash": 0.0, "seed_usd": 0.0, "start": 0.0})
        def quote(pair):
            b = self.bid[pair]
            return {"pair": pair, "bid": b, "ask": b * 1.0001, "last": b, "mid": b, "tape": "LIVE", "trade_age_sec": 1.0,
                    "utc": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
        def get(*a, **k): raise RuntimeError("offline")
        for p in (patch.object(tt, "R", self.tmp), patch.object(tc, "R", self.tmp), patch.object(tc, "LOG", os.path.join(self.tmp, "log.txt")),
                  patch.object(tc, "FACTS", os.path.join(self.tmp, "facts.jsonl")), patch.object(tc, "desk_lock", NOLOCK),
                  patch.object(tc, "quote", quote), patch.object(tc, "get", get)):
            p.start(); self.addCleanup(p.stop)

    def book(self, **pos):
        b = self.tc.new_book(); b["positions"] = pos; b["mark_px_usd"] = {k: v["entry_px"] for k, v in pos.items()}; self.w("tfo_ht_crypto", b)

    def rails(self, execute=True, dry=False):
        with contextlib.redirect_stdout(open(os.devnull, "w")): self.tc.cmd_rails(execute, dry)

    def test_crypto_half_then_trail_no_cap_then_exit(self):
        old = (dt.datetime.now(UTC) - dt.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.book(**{"LINK/USD": {"qty": 1000.0, "entry_px": 10.0, "orig_entry": 10.0, "entry_utc": old}})
        self.bid["LINK/USD"] = 10.2; self.rails()
        p = self.r("tfo_ht_crypto")["positions"]["LINK/USD"]; self.assertAlmostEqual(p["qty"], 500.0)
        p["last_ticket_utc"] = old; b = self.r("tfo_ht_crypto"); b["positions"]["LINK/USD"] = p; self.w("tfo_ht_crypto", b)
        self.bid["LINK/USD"] = 11.0; self.rails()                                   # +10%: no +5% flat
        p = self.r("tfo_ht_crypto")["positions"]["LINK/USD"]; self.assertAlmostEqual(p["qty"], 500.0); self.assertEqual(p["trail"]["hwm_usd"], 11.0)
        self.bid["LINK/USD"] = 10.6; self.rails()                                   # 3% floor trail (ATR offline) -> 10.67
        b = self.r("tfo_ht_crypto"); self.assertNotIn("LINK/USD", b["positions"])
        self.assertEqual(b["head_trader_tickets"][-1]["exit_rule"], "TRAIL_3ATR")
        self.assertIn(" FLAT LINK/USD ", open(os.path.join(self.tmp, "log.txt")).read())

    def test_crypto_max_hold_30_calendar_days(self):
        old = (dt.datetime.now(UTC) - dt.timedelta(days=31)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.book(**{"ETH/USD": {"qty": 5.0, "entry_px": 100.0, "orig_entry": 100.0, "entry_utc": old, "rebase": 102.0, "last_ticket_utc": old}})
        self.bid["ETH/USD"] = 104.0; self.rails()
        b = self.r("tfo_ht_crypto"); self.assertNotIn("ETH/USD", b["positions"]); self.assertEqual(b["head_trader_tickets"][-1]["exit_rule"], "MAX_HOLD")

    def test_crypto_dry_run_saves_nothing(self):
        old = (dt.datetime.now(UTC) - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.book(**{"SOL/USD": {"qty": 5.0, "entry_px": 100.0, "orig_entry": 100.0, "entry_utc": old}})
        before = open(os.path.join(self.tmp, "tfo_ht_crypto.json")).read(); self.bid["SOL/USD"] = 94.0
        self.rails(execute=False, dry=True)
        self.assertEqual(before, open(os.path.join(self.tmp, "tfo_ht_crypto.json")).read())


if __name__ == "__main__":
    unittest.main()
