"""Offline tests for the 5 Oct 2026 OANDA practice-sleeve entry-quality rails (execution bot fill-quality review, NZD_CHF trade 61587):
(a) no new entry from the Friday 17:00 NY close until 60 min after the Sunday 17:00 NY reopen; (b) entry spread <= min(3x median spread, 10bp).
No network: api() raises; temp state dir. Run (repo root): python3 tests/test_oanda_entry_quality_20261005.py -v."""
import datetime as dt, importlib.util, json, os, shutil, sys, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "research", "behaviour"))
UTC = dt.timezone.utc
def U(*a): return dt.datetime(*a, tzinfo=UTC)
def load():
    spec = importlib.util.spec_from_file_location("sleeve_eq_under_test", os.path.join(ROOT, "oanda_sleeve.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

class Base(unittest.TestCase):
    def setUp(self):
        self.m = m = load(); self.tmp = tempfile.mkdtemp(); st = os.path.join(self.tmp, "state", "oanda"); os.makedirs(st)
        m.ST = st; m.LOG = os.path.join(st, "trades.jsonl"); m.SEEN = os.path.join(st, "closed_seen.json"); m.CFG = os.path.join(st, "nocfg.json")
        m.BEH_BASE = self.tmp
        def no_network(*a, **k): raise AssertionError("unexpected OANDA API call in test: %r" % (a,))
        m.api = no_network
        m.instruments = lambda: {k: {"displayPrecision": 5, "tradeUnitsPrecision": 0, "marginRate": "0.0333", "financing": {"longRate": "0", "shortRate": "0"}} for k in ("NZD_CHF", "GBP_CHF", "USD_CHF", "XAU_USD")}
        m.summary = lambda: {"NAV": "50000", "marginUsed": "100", "currency": "AUD"}
        m.open_trades = lambda: []
        self.sp = 1e-4
        m.pricing = lambda insts: {i: {"bid": 1.0, "ask": 1.0001, "mid": 1.00005, "sp": self.sp, "tradeable": True, "q2h": {"positiveUnits": "1.5"}} for i in insts}
        m.behaviour_ok = lambda *a, **k: True        # behaviour gate has its own tests; isolate the new rails
    def tearDown(self): shutil.rmtree(self.tmp)
    def lines(self):
        if not os.path.exists(self.m.LOG): return []
        with open(self.m.LOG) as f: return [json.loads(l) for l in f]
    def entry(self, **k):
        e = dict(design="OXL2", ins="NZD_CHF", dir=1, score=0.03, risk_pct=0.005, reason="t", stop_dist=0.0064, floor_sp=0.0012449, close=0.4653,
                 expires_utc="2026-12-31T00:00:00+00:00", ref_level=0.4653, day_ret_pct=-0.12, atr_pct=0.69)
        e.update(k); return e

class TestReopenBlackout(Base):
    def test_edt_sunday_reopen_window(self):
        rb = self.m.reopen_blackout
        self.assertIn("fx_weekend_closed", rb(U(2026, 10, 4, 20, 59)))       # Sun 16:59 EDT: still closed
        self.assertIn("reopen_blackout", rb(U(2026, 10, 4, 21, 0)))          # Sun 17:00 EDT reopen
        self.assertIn("reopen_blackout", rb(U(2026, 10, 4, 21, 5, 20)))      # trade 61587's fill time (17:05 NY)
        self.assertIn("reopen_blackout", rb(U(2026, 10, 4, 21, 59, 59)))
        self.assertIsNone(rb(U(2026, 10, 4, 22, 0)))                         # Sun 18:00 EDT = Mon 09:00 AEDT: allowed
        self.assertIsNone(rb(U(2026, 10, 5, 13, 0)))                         # Monday session
    def test_est_after_dst_ends(self):
        rb = self.m.reopen_blackout                                          # 8 Nov 2026: NY on EST (UTC-5)
        self.assertIn("fx_weekend_closed", rb(U(2026, 11, 8, 21, 30)))       # 16:30 EST
        self.assertIn("reopen_blackout", rb(U(2026, 11, 8, 22, 30)))         # 17:30 EST
        self.assertIsNone(rb(U(2026, 11, 8, 23, 0)))                         # 18:00 EST
    def test_weekend_closed(self):
        rb = self.m.reopen_blackout
        self.assertIsNone(rb(U(2026, 10, 9, 20, 59)))                        # Fri 16:59 EDT
        self.assertIn("fx_weekend_closed", rb(U(2026, 10, 9, 21, 0)))        # Fri 17:00 EDT close
        self.assertIn("fx_weekend_closed", rb(U(2026, 10, 10, 12, 0)))       # Saturday
    def test_minutes_floor_is_60(self):
        rb = self.m.reopen_blackout
        self.assertIn("reopen_blackout", rb(U(2026, 10, 4, 21, 30), minutes=15))   # cannot be shortened below 60
        self.assertIn("reopen_blackout", rb(U(2026, 10, 4, 22, 29), minutes=90))   # can be lengthened (18:29 NY < 18:30)
        self.assertIsNone(rb(U(2026, 10, 4, 22, 30), minutes=60))
    def test_fallback_without_zoneinfo_matches(self):
        saved = sys.modules.get("zoneinfo"); sys.modules["zoneinfo"] = None   # import zoneinfo -> ImportError
        try:
            for t in (U(2026, 10, 4, 21, 5), U(2026, 10, 4, 22, 0), U(2026, 11, 8, 22, 30), U(2026, 11, 8, 23, 0), U(2026, 3, 8, 21, 30), U(2026, 3, 8, 22, 0)):
                got = self.m.reopen_blackout(t)
                if saved is not None: sys.modules["zoneinfo"] = saved
                want = self.m.reopen_blackout(t); sys.modules["zoneinfo"] = None
                self.assertEqual(got is None, want is None, t)
        finally:
            if saved is not None: sys.modules["zoneinfo"] = saved
            else: sys.modules.pop("zoneinfo", None)

class TestRunQueue(Base):
    def queue(self, *es):
        with open(os.path.join(self.m.ST, "pending_dry.json"), "w") as f: json.dump({"queue": list(es)}, f)
    def test_nzdchf_replay_is_deferred_and_kept(self):
        self.m.now_utc = lambda: U(2026, 10, 4, 21, 5, 20)                    # 17:05 NY Sunday, as trade 61587
        called = []; self.m.place_entry = lambda e, live: called.append(e["ins"]) or "dry"
        self.queue(self.entry(), self.entry(ins="GBP_CHF"))
        self.m.run_queue(False, {"tfo_upl_aud": 0, "tfo_realized_aud": 0})
        self.assertEqual(called, [])                                         # no order path (and no behaviour gate) during blackout
        ev = [r for r in self.lines() if r["event"] == "entry_deferred"]
        self.assertEqual([r["instrument"] for r in ev], ["NZD_CHF", "GBP_CHF"]); self.assertIn("reopen_blackout", ev[0]["reason"])
    def test_after_window_entries_proceed(self):
        self.m.now_utc = lambda: U(2026, 10, 4, 22, 1)
        called = []; self.m.place_entry = lambda e, live: called.append(e["ins"]) or "dry"
        self.queue(self.entry())
        self.m.run_queue(False, {"tfo_upl_aud": 0, "tfo_realized_aud": 0})
        self.assertEqual(called, ["NZD_CHF"])
    def test_kill_still_wins(self):
        self.m.now_utc = lambda: U(2026, 10, 5, 13, 0)
        open(os.path.join(self.m.ST, "KILL"), "w").close(); self.queue(self.entry())
        self.m.run_queue(False, {"tfo_upl_aud": 0, "tfo_realized_aud": 0})
        self.assertEqual([r["event"] for r in self.lines()], ["entry_blocked"])

class TestSpreadCap(Base):
    def test_nzdchf_26bp_now_deferred(self):
        self.sp = 0.0026                                                     # old limit 3 x 12.45bp = 37.3bp would have passed
        r = self.m.place_entry(self.entry(), False)
        self.assertTrue(r.startswith("defer:spread 26.0bp > limit 10.0bp"), r)
    def test_under_cap_passes(self):
        self.sp = 0.00099; self.assertEqual(self.m.place_entry(self.entry(), False), "dry")
    def test_three_x_rule_tighter_than_cap(self):
        self.sp = 0.00035                                                    # 3.5bp > 3 x 1bp, though under 10bp
        r = self.m.place_entry(self.entry(ins="USD_CHF", floor_sp=1e-4), False); self.assertTrue(r.startswith("defer:spread"), r)
        self.sp = 0.00029; self.assertEqual(self.m.place_entry(self.entry(ins="USD_CHF", floor_sp=1e-4), False), "dry")
    def test_spread_limit_values(self):
        c = self.m.cfg()
        self.assertAlmostEqual(self.m.spread_limit(0.0012449, c), 0.0010)    # NZD_CHF: cap binds
        self.assertAlmostEqual(self.m.spread_limit(0.00023, c), 0.00069)     # XAU_USD: 3x binds
    def test_config_cannot_loosen_rails(self):
        with open(self.m.CFG, "w") as f: json.dump({"spread_cap_bp": 40, "reopen_blackout_min": 5}, f)
        c = self.m.cfg(); self.assertEqual((c["spread_cap_bp"], c["reopen_blackout_min"]), (10.0, 60))
        with open(self.m.CFG, "w") as f: json.dump({"spread_cap_bp": 6, "reopen_blackout_min": 120}, f)
        c = self.m.cfg(); self.assertEqual((c["spread_cap_bp"], c["reopen_blackout_min"]), (6.0, 120))   # tightening allowed
    def test_exits_and_stops_untouched(self):
        t = {"id": "777", "instrument": "NZD_CHF", "currentUnits": "1000", "price": "0.465", "clientExtensions": {"tag": "TFO", "comment": "OXL2"}}
        self.m.now_utc = lambda: U(2026, 10, 4, 21, 5)
        self.m.close_trade(t, "flip", False); self.m.set_stop(t, 0.46, "trail", False)
        self.assertEqual([r["event"] for r in self.lines()], ["close_dry", "stop_dry"])   # 5 Oct 2026: dry stop changes log stop_dry
        for tid in ("61365", "61368"):
            with self.assertRaises(RuntimeError): self.m.set_stop(dict(t, id=tid), 1.0, "x", False)

if __name__ == "__main__": unittest.main()
