"""HARDENING locks for the 27 Oct 2026 audit fixes (VERIFY.md WEAK items; Brett 27 Oct 2026 21:11 WITA: 'every fix 100% permanent').
Plain unittest, stdlib only, OFFLINE + hermetic (all engine paths -> tmp; no network, no broker, no orders).
Run: python3 -m unittest tests.test_audit_hardening_20261027 -v"""
import copy, datetime as dt, json, os, shutil, sys, tempfile, unittest
from zoneinfo import ZoneInfo
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
_ARGV = list(sys.argv); sys.argv = [sys.argv[0]]
import hunt48_short_paper as H
import ibkr_costs as IC
sys.argv = _ARGV
_REAL_HAS_BORROW = IC.has_borrow
UTC = dt.timezone.utc; NY = ZoneInfo("America/New_York")
def _ny(y, m, d, hh, mm): return dt.datetime(y, m, d, hh, mm, tzinfo=NY).astimezone(UTC)
def _bar(start, o, h, l, c): return dict(start=start, o=o, h=h, l=l, c=c)
SPEC = dict(ticker="XYZ", direction="short", trigger=50.0, max_fill=49.9, stop=52.0, t1=49.0, t2=47.8, shares=800, atr=1.0, sector="TECH")

class Iso(unittest.TestCase):
    """Every engine path to a private tmp dir; no borrow/FX/news network."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="audit_hard_"); self._undo = []
        p = lambda o, n, v: (self._undo.append((o, n, getattr(o, n))), setattr(o, n, v))
        self.patch = p
        for n, f in (("SD", ""), ("LEDGER", "ledger.json"), ("EVENTS", "events.jsonl"), ("STATUS", "status.json"),
                     ("SECTOR_CACHE", "sector_cache.json"), ("EARN_DIR", "clearance")):
            p(H, n, os.path.join(self.tmp, f) if f else self.tmp)
        p(H.IC, "has_borrow", lambda *a, **k: True); p(H.IC, "borrow_pa", lambda *a, **k: 0.03)
        p(H, "live_audusd", lambda fb: 0.70); p(H, "hi_events", lambda *a, **k: [])
    def tearDown(self):
        for o, n, v in reversed(self._undo): setattr(o, n, v)
        shutil.rmtree(self.tmp, ignore_errors=True)
    def filled(self, **kw):
        e = kw.pop("entry_utc", _ny(2026, 11, 2, 10, 0))
        t = dict(id=1, ticker="XYZ", status="FILLED", direction="short", trigger=50.5, max_fill=49.9, entry=50.0, entry_utc=e.isoformat(), shares=100,
                 open_qty=100, half_done=False, trail=None, stop=52.0, t1=48.0, t2=46.0, lowest=49.0, atr=1.0, last_px=49.0, ext_days=0,
                 time_exit_utc=H.session_exit(e).isoformat(), time_exit_rule="fill + 3 NY sessions (audit C1)", exits=[], realised_usd=0.0,
                 fees_usd=1.0, borrow_usd=0.0, armed_utc=(e - dt.timedelta(hours=1)).isoformat(), risk_cap_aud=2500.0, sector="TECH",
                 next_earnings_utc="2030-06-28T20:00:00+00:00", _last_bar=(e - dt.timedelta(minutes=15)).isoformat())
        t.update(kw); return t
    def events(self): return [json.loads(x) for x in open(H.EVENTS)] if os.path.exists(H.EVENTS) else []

# ============================================================ C1 hardening: extensions + event guard + arm-default on the NY calendar
class C1Hardening(Iso):
    def setUp(self):
        super().setUp(); self.patch(H, "extension_ok", lambda d, long=False: True)
    def _next_session(self, d):
        d += dt.timedelta(days=1)
        while not H.ny_session(d): d += dt.timedelta(days=1)
        return d
    def test_extension_is_next_ny_session_close_sweep_2026_2028(self):
        d = dt.date(2026, 10, 26); n = 0
        while d <= dt.date(2028, 12, 20):
            if H.ny_session(d):
                for lg in (False, True):
                    t = self.filled(direction="long" if lg else "short", stop=48.0 if lg else 52.0, trigger=49.5 if lg else 50.5,
                                    t1=60.0 if lg else 40.0, last_px=51.0 if lg else 49.0, lowest=51.0 if lg else 49.0,
                                    entry_utc=H.ny_close_utc(d) - dt.timedelta(hours=1))
                    te = H.ny_close_utc(d); t["time_exit_utc"] = te.isoformat()
                    self.assertEqual(H.manage(t, [], te + dt.timedelta(minutes=1), None, daily_fn=lambda: [1]), ["extended"])
                    nte = dt.datetime.fromisoformat(t["time_exit_utc"]); nd = self._next_session(d)
                    self.assertEqual(nte, H.ny_close_utc(nd), (d, lg)); n += 1
                    nny = nte.astimezone(NY); self.assertTrue(nny.weekday() < 5 and nny.date().isoformat() not in H.NYSE_HOLIDAYS)
            d += dt.timedelta(days=1)
        self.assertGreater(n, 1000)
    def test_live_positions_this_week_never_land_on_a_weekend(self):
        te = dt.datetime.fromisoformat("2026-10-28T19:55:00+00:00")   # the 8 open positions: Wed 28 Oct 15:55 ET
        t = self.filled(entry_utc=_ny(2026, 10, 26, 10, 0), time_exit_utc=te.isoformat(), time_exit_rule=None)
        want = [_ny(2026, 10, 29, 15, 55), _ny(2026, 10, 30, 15, 55), _ny(2026, 11, 2, 15, 55), _ny(2026, 11, 3, 15, 55)]
        for w in want:
            cur = dt.datetime.fromisoformat(t["time_exit_utc"])
            self.assertEqual(H.manage(t, [], cur + dt.timedelta(minutes=1), None, daily_fn=lambda: [1]), ["extended"])
            self.assertEqual(dt.datetime.fromisoformat(t["time_exit_utc"]), w)
    def test_thanksgiving_and_early_close(self):
        t = self.filled(entry_utc=_ny(2026, 11, 20, 10, 0)); t["time_exit_utc"] = _ny(2026, 11, 25, 15, 55).isoformat()
        H.manage(t, [], _ny(2026, 11, 25, 15, 56), None, daily_fn=lambda: [1])
        self.assertEqual(dt.datetime.fromisoformat(t["time_exit_utc"]), _ny(2026, 11, 27, 12, 55))   # skips Thu 26 (holiday), Fri early close
    def test_extension_source_has_no_calendar_day_arithmetic(self):
        import inspect
        for f in (H.manage, H.manage_long):
            src = inspect.getsource(f); self.assertIn("session_exit(te, 1)", src); self.assertNotIn("weekday() == 4", src)
    def test_prior_close_exit_skips_holidays_and_uses_early_close(self):
        ev = lambda y, m, d: [(_ny(y, m, d, 8, 30), "X")]
        nte, why = H.prior_close_exit(_ny(2026, 11, 27, 12, 55), ev(2026, 11, 27))   # Fri after Thanksgiving -> Wed 25 (Thu 26 holiday)
        self.assertEqual(nte, _ny(2026, 11, 25, 15, 55)); self.assertTrue(why)
        nte, _ = H.prior_close_exit(_ny(2026, 12, 28, 15, 55), ev(2026, 12, 28))    # Mon 28 Dec -> Thu 24 Dec early close (Fri 25 holiday)
        self.assertEqual(nte, _ny(2026, 12, 24, 12, 55))
        nte, _ = H.prior_close_exit(_ny(2027, 3, 29, 15, 55), ev(2027, 3, 29))      # Mon after Good Friday -> Thu 25 Mar
        self.assertEqual(nte, _ny(2027, 3, 25, 15, 55))
    def test_prior_close_exit_always_a_session_close_sweep(self):
        d = dt.date(2026, 10, 26)
        while d <= dt.date(2028, 12, 20):
            if H.ny_session(d):
                nte, _ = H.prior_close_exit(H.ny_close_utc(d), [(dt.datetime.combine(d, dt.time(8, 30), NY).astimezone(UTC), "X")])
                p = nte.astimezone(NY).date(); self.assertTrue(H.ny_session(p) and p < d, d); self.assertEqual(nte, H.ny_close_utc(p))
            d += dt.timedelta(days=1)
    def test_arm_default_uses_ny_calendar_and_is_never_in_the_past(self):
        for now in (_ny(2026, 10, 30, 10, 0), _ny(2026, 11, 26, 10, 0), _ny(2027, 7, 3, 12, 0)):
            for t in H.arm_default(now=now)["trades"]:
                te = dt.datetime.fromisoformat(t["time_exit_utc"]); self.assertGreater(te, now); self.assertEqual(te, H.session_exit(now))
        import inspect; self.assertNotIn("TIME_EXIT_UTC", inspect.getsource(H.arm_default))

# ============================================================ H3 hardening: a non-causal fill restores the pre-fill ARMED snapshot
class H3Hardening(Iso):
    def _env(self, now, bars):
        self.patch(H, "now_utc", lambda: now); self.patch(H, "us_rth", lambda n: True); self.patch(H, "bars_15m", lambda s: bars.get(s, []))
        self.patch(H, "bars_daily", lambda s: [dict(h=60.0, l=40.0, c=50.0)] * 260)
        self.patch(H, "room_check", lambda t, daily, entry=None: dict(ok=True, room_R=2.0, level=None, cost_R=0.05, why=None))
    def _armed(self, now, **kw):
        L = H.new_ledger("r"); t = H.upsert_trade(L, dict(SPEC, **kw), now - dt.timedelta(hours=1))
        t["next_earnings_utc"] = "2030-06-28T20:00:00+00:00"; json.dump(L, open(H.LEDGER, "w"), default=str); return copy.deepcopy(t)
    def test_causal_reject_restores_snapshot_exactly_over_repeated_ticks(self):
        now = _ny(2026, 11, 4, 11, 0); before = self._armed(now, shares=5000)   # oversize -> H5 trims shares at fill
        self._env(now, {"XYZ": [_bar(now - dt.timedelta(minutes=30), 50.1, 50.2, 49.9, 49.95)]})
        self.patch(H.causal_guard, "check", lambda t, now=None: ["forced non-causal (test)"] if t.get("entry_utc") else [])
        for i in range(3):
            H.tick(); d = json.load(open(H.LEDGER))["trades"][0]
            tick_keys = {"last_px", "last_px_utc", "_te_checked", "_te_rule"}   # written by the tick BEFORE the fill snapshot
            extra = {k for k in d if k not in before} - tick_keys; self.assertEqual(extra, {"last_causal_reject"}, extra)
            b = json.loads(json.dumps(before, default=str))
            self.assertEqual({k: d[k] for k in b if k not in tick_keys}, {k: v for k, v in b.items() if k not in tick_keys})   # fees, shares, exit, _last_bar as armed
            self.assertEqual((d["status"], d["entry"], d["open_qty"], d["shares"], d["fees_usd"]), ("ARMED", None, 0, 5000, before["fees_usd"]))
            self.assertNotIn("_last_bar", d); self.assertNotIn("shares_planned", d)
            self.assertEqual(json.load(open(H.LEDGER))["causal_rejects"], i + 1)
        self.assertEqual(sum(e["kind"] == "causal_reject" for e in self.events()), 3)
    def test_causal_fill_still_commits(self):
        now = _ny(2026, 11, 4, 11, 0); self._armed(now)
        self._env(now, {"XYZ": [_bar(now - dt.timedelta(minutes=30), 50.1, 50.2, 49.9, 49.95)]})
        H.tick(); d = json.load(open(H.LEDGER))["trades"][0]; self.assertEqual(d["status"], "FILLED"); self.assertNotIn("last_causal_reject", d)

# ============================================================ H5 hardening: the fill-time A$ risk recheck fails CLOSED
class H5Hardening(Iso):
    def _t(self, **kw):
        now = _ny(2026, 11, 4, 10, 0)
        t = dict(ticker="Z", direction="short", status="ARMED", armed_utc=(now - dt.timedelta(hours=1)).isoformat(), trigger=50.0,
                 max_fill=49.9, stop=52.0, t1=49.0, t2=47.8, shares=800, atr=1.0, risk_cap_aud=2500.0, fees_usd=0.0, exits=[])
        t.update(kw); return t, now
    def _fill(self, t, now, c, fx): return H.try_fill(t, [_bar(now - dt.timedelta(minutes=30), c, c, c, c)], now, fx)
    def test_missing_or_bad_audusd_is_shadow_never_a_position(self):
        for fx in (None, 0, 0.0, -0.7, float("nan"), float("inf"), "0.70", True):
            t, now = self._t(); self.assertEqual(self._fill(t, now, 49.95, fx), "shadow", fx)
            self.assertEqual((t["status"], t["entry"], t["open_qty"]), ("SHADOW", None, 0)); self.assertIn("AUDUSD", t["shadow_reason"])
            self.assertEqual(t["fees_usd"], 0.0)
    def test_not_one_share_inside_cap_is_shadow(self):
        t, now = self._t(trigger=5000.0, max_fill=4990.0, stop=9000.0, t1=4900.0, t2=4800.0, shares=10)
        self.assertEqual(self._fill(t, now, 4995.0, 0.70), "shadow"); self.assertIn("risk > cap", t["shadow_reason"]); self.assertEqual(t["open_qty"], 0)
        t, now = self._t(risk_cap_aud=0.0); self.assertEqual(self._fill(t, now, 49.95, 0.70), "shadow")   # a 0 cap is not silently 2500
    def test_risk_on_rounded_entry_never_exceeds_cap_sweep(self):
        import random; rnd = random.Random(20261027); n = 0
        for _ in range(3000):
            lg = rnd.random() < 0.5; px = rnd.uniform(1.0, 400.0); stopd = px * rnd.uniform(0.003, 0.08); fx = rnd.uniform(0.55, 0.80)
            if lg: t, now = self._t(direction="long", trigger=px, max_fill=px * 1.004, stop=px - stopd, t1=px + stopd, t2=px + 2 * stopd, shares=rnd.randint(1, 60000))
            else: t, now = self._t(trigger=px, max_fill=px * 0.996, stop=px + stopd, t1=px - stopd, t2=px - 2 * stopd, shares=rnd.randint(1, 60000))
            c = px + (t["max_fill"] - px) * rnd.uniform(0.05, 0.95)
            k = self._fill(t, now, c, fx)
            if k == "filled":
                n += 1; self.assertLessEqual(abs(t["entry"] - t["stop"]) * t["shares"] / fx, 2500.0 + 1e-9, t)
                self.assertEqual(t["entry"], round(t["entry"], 4)); self.assertEqual(t["open_qty"], t["shares"])
            else: self.assertIn(k, ("shadow", "missed", None))
        self.assertGreater(n, 1000)
    def test_cheap_name_many_shares_rounding_case(self):
        t, now = self._t(trigger=2.0, max_fill=1.99, stop=2.16, t1=1.9, t2=1.8, shares=10972)   # the A$2,500.6 example class
        self.assertEqual(self._fill(t, now, 1.99512, 0.70), "filled")
        self.assertLessEqual(abs(t["entry"] - t["stop"]) * t["shares"] / 0.70, 2500.0)
    def test_trim_emits_risk_trim_event_in_tick(self):
        now = _ny(2026, 11, 4, 11, 0); L = H.new_ledger("r"); t = H.upsert_trade(L, dict(SPEC, shares=5000), now - dt.timedelta(hours=1))
        t["next_earnings_utc"] = "2030-06-28T20:00:00+00:00"; json.dump(L, open(H.LEDGER, "w"), default=str)
        self.patch(H, "now_utc", lambda: now); self.patch(H, "us_rth", lambda n: True)
        self.patch(H, "bars_15m", lambda s: [_bar(now - dt.timedelta(minutes=30), 50.1, 50.2, 49.9, 49.95)])
        self.patch(H, "bars_daily", lambda s: [dict(h=60.0, l=40.0, c=50.0)] * 260)
        self.patch(H, "room_check", lambda t, daily, entry=None: dict(ok=True, room_R=2.0, level=None, cost_R=0.05, why=None))
        H.tick(); d = json.load(open(H.LEDGER))["trades"][0]
        self.assertEqual(d["status"], "FILLED"); self.assertLess(d["shares"], 5000); self.assertIn("risk_trim", [e["kind"] for e in self.events()])

# ============================================================ H6 hardening: past date = unknown; unknown/error = no extension for new (fill-stamped) equities
class H6Hardening(Iso):
    def _clear(self, day, tk, v):
        p = os.path.join(H.EARN_DIR, day); os.makedirs(p, exist_ok=True)
        with open(os.path.join(p, tk + ".json"), "w") as f: f.write(v if isinstance(v, str) and v.startswith("{bad") else json.dumps(dict(calendar=dict(next_earnings_utc=v))))
    def test_past_date_is_not_next_earnings_and_older_files_are_scanned(self):
        now = _ny(2026, 11, 4, 10, 0); t = dict(ticker="XYZ", next_earnings_utc="2026-10-22T20:00:00+00:00")
        self.assertIsNone(H.next_earnings(t, now))
        self._clear("20261103", "XYZ", "2026-10-22T20:00:00+00:00")   # newest file: last report (past)
        self._clear("20261102", "XYZ", None)                           # empty calendar
        self._clear("20261101", "XYZ", "{bad json")                    # corrupt
        self.assertIsNone(H.next_earnings(t, now))
        self._clear("20261030", "XYZ", "2026-11-12T21:00:00+00:00")
        self.assertEqual(H.next_earnings(t, now), dt.datetime(2026, 11, 12, 21, 0, tzinfo=UTC))
        self.assertEqual(H.earnings_status(dict(t, time_exit_utc=H.session_exit(now).isoformat()), now)[0], "block")
    def test_status_unknown_when_only_past(self):
        now = _ny(2026, 11, 4, 10, 0); t = dict(ticker="XYZ", next_earnings_utc="2026-10-22", time_exit_utc=H.session_exit(now).isoformat())
        self.assertEqual(H.earnings_status(t, now)[0], "unknown"); self.assertIsNone(H.earnings_block(t, now))
    def _ext(self, **kw):
        self.patch(H, "extension_ok", lambda d, long=False: True)
        t = self.filled(**kw); te = dt.datetime.fromisoformat(t["time_exit_utc"])
        return H.manage(t, [], te + dt.timedelta(minutes=1), None, daily_fn=lambda: [1]), t
    def test_stamped_equity_unknown_earnings_no_extension(self):
        for d in ("short", "long"):
            kw = dict(direction=d, next_earnings_utc=None, earnings_at_fill=dict(status="unknown"))
            if d == "long": kw.update(stop=48.0, t1=52.0, t2=54.0, highest=51.0, last_px=51.0, entry=50.0)
            ev, t = self._ext(**kw); self.assertEqual(ev, ["time_exit"], d); self.assertEqual(t["status"], "CLOSED")
            ev, t = self._ext(**dict(kw, next_earnings_utc="2026-10-01T20:00:00+00:00")); self.assertEqual(ev, ["time_exit"], d)   # past = unknown
    def test_stamped_equity_check_error_no_extension(self):
        self.patch(H, "earnings_status", lambda t, now: (_ for _ in ()).throw(RuntimeError("boom")))
        ev, t = self._ext(next_earnings_utc=None, earnings_at_fill=dict(status="clear")); self.assertEqual(ev, ["time_exit"])
    def test_stamped_known_clear_still_extends_and_etfs_exempt(self):
        ev, t = self._ext(earnings_at_fill=dict(status="clear")); self.assertEqual(ev, ["extended"])
        for kw in (dict(ticker="GLD", sector=None), dict(sector="ETF:Large Blend"), dict(sector="COMMODITIES")):
            ev, t = self._ext(**dict(kw, next_earnings_utc=None, earnings_at_fill=dict(status="unknown"))); self.assertEqual(ev, ["extended"], kw)
    def test_legacy_open_positions_untouched(self):
        # the 8 open positions carry no C1 'NY sessions' rule and no fill stamp: unknown earnings or errors never change their extension
        ev, t = self._ext(time_exit_rule=None, next_earnings_utc=None); self.assertEqual(ev, ["extended"])
        ev, t = self._ext(next_earnings_utc=None); self.assertEqual(ev, ["extended"])   # hand-built, unstamped: unchanged (verifier lock)
    def test_fill_stamps_earnings_status_and_check_error_keeps_armed(self):
        now = _ny(2026, 11, 4, 11, 0)
        def run(earn, exc=None):
            L = H.new_ledger("r"); t = H.upsert_trade(L, dict(SPEC), now - dt.timedelta(hours=1)); t["next_earnings_utc"] = earn
            json.dump(L, open(H.LEDGER, "w"), default=str)
            self.patch(H, "now_utc", lambda: now); self.patch(H, "us_rth", lambda n: True)
            self.patch(H, "bars_15m", lambda s: [_bar(now - dt.timedelta(minutes=30), 50.1, 50.2, 49.9, 49.95)])
            self.patch(H, "bars_daily", lambda s: [dict(h=60.0, l=40.0, c=50.0)] * 260)
            self.patch(H, "room_check", lambda t, daily, entry=None: dict(ok=True, room_R=2.0, level=None, cost_R=0.05, why=None))
            if exc: self.patch(H, "next_earnings", lambda t, now=None: (_ for _ in ()).throw(exc))
            H.tick(); return json.load(open(H.LEDGER))["trades"][0]
        d = run("2030-06-28T20:00:00+00:00"); self.assertEqual((d["status"], d["earnings_at_fill"]["status"]), ("FILLED", "clear"))
        d = run(None); self.assertEqual((d["status"], d["earnings_at_fill"]["status"]), ("FILLED", "unknown"))
        d = run("2026-10-20"); self.assertEqual(d["earnings_at_fill"]["status"], "unknown")
        d = run(None, RuntimeError("calendar down")); self.assertEqual(d["status"], "ARMED"); self.assertIn("gate error", d["data_error"])

# ============================================================ M4 hardening: ASX + Asia room need 200 VALID daily bars; NaN rows never count
class M4Hardening(Iso):
    def _t(self, mkt=None): 
        t = dict(direction="short", max_fill=10.0, stop=10.3, t2=9.55, cost_R=0.05)
        if mkt: t["market"] = mkt
        return t
    FLAT = [dict(h=10.4, l=10.2, c=10.3)]
    def test_asx_and_asia_bench_below_200_bars(self):
        import asx_hunt48 as A, asia_hunt48 as AH
        for gate, mkt in ((A.cost_gate, None), (AH.cost_gate, "jp"), (AH.cost_gate, "hk")):
            for n in (0, 60, 199):
                why = gate(self._t(mkt), self.FLAT * n if n else []); self.assertTrue(why and ("daily bars" in why), (mkt, n, why))
            self.assertIsNone(gate(self._t(mkt), self.FLAT * 200), mkt)
    def test_london_unchanged_by_m4(self):
        import asia_hunt48 as AH
        t = self._t("lse"); why = AH.cost_gate(t, self.FLAT * 60); self.assertFalse(why and "(<200)" in why, why)
    def test_nan_rows_never_count_toward_history(self):
        import hunt48_room as HR, asx_hunt48 as A
        nan = float("nan")
        bad = [dict(h=nan, l=nan, c=nan)] * 250 + [dict(h=10.4, l=nan, c=10.3)] * 5 + [dict(h=10.4, l=10.2, c=None)] * 5 + [dict(h=10.4, l=10.2)] * 5 + [dict(h=0.0, l=0.0, c=0.0)] * 5
        for gate in (lambda t, b: HR.room(t, b, min_bars=HR.US_MIN_BARS), A.cost_gate):
            why = gate(self._t(), bad + self.FLAT * 10); self.assertIn("only 10 daily bars", why)
            self.assertIsNone(gate(self._t(), bad + self.FLAT * 200))
        rc = H.room_check(dict(ticker="Z", direction="short", max_fill=49.9, stop=52.0, t2=46.0, shares=100), [dict(h=nan, l=nan, c=nan)] * 250, 49.9)
        self.assertFalse(rc["ok"])

# ============================================================ retry edges: data outages keep the trade ARMED; future-dated borrow file is stale
class RetryEdges(Iso):
    def _cache(self, age_h, names=None):
        import ibkr_costs as IC
        p = os.path.join(self.tmp, "borrow.json"); u = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=age_h)).isoformat()
        C = dict(updated_utc=u, us=dict(names=names or {"XYZ": dict(fee_pa=0.01, available=10**6, ok=True)}))
        json.dump(C, open(p, "w")); self.patch(IC, "CACHE", p)
    def test_future_dated_borrow_file_is_stale(self):
        import ibkr_costs as IC
        for age, ok in ((-240, False), (-1, False), (-0.2, False), (-0.01, True), (1, True), (23.9, True), (24.1, False)):
            self._cache(age); self.assertIs(_REAL_HAS_BORROW("XYZ", 1), ok, age); self.assertIs(IC.cache_fresh(), ok, age)
    def test_refresh_if_stale_only_when_stale(self):
        import ibkr_costs as IC, subprocess, sys as _s
        calls = []; self.patch(IC, "refresh", lambda g=None: calls.append(1) or dict(us=dict(n=1), asx=dict(n=1)))
        for age, want in ((2, 0), (21, 1), (-3, 1)):
            self._cache(age); calls.clear(); a = IC.cache_age_h()
            if not IC.cache_fresh(a, IC.BORROW_RETRY_AGE_H): IC.refresh()
            self.assertEqual(len(calls), want, age)
        src = open(IC.__file__).read(); self.assertIn('"refresh-if-stale"', src)
    def _armed(self, now, daily):
        L = H.new_ledger("r"); t = H.upsert_trade(L, dict(SPEC), now - dt.timedelta(hours=1)); t["next_earnings_utc"] = "2030-06-28T20:00:00+00:00"
        json.dump(L, open(H.LEDGER, "w"), default=str)
        self.patch(H, "now_utc", lambda: now); self.patch(H, "us_rth", lambda n: True)
        self.patch(H, "bars_15m", lambda s: [_bar(now - dt.timedelta(minutes=30), 50.1, 50.2, 49.9, 49.95)])
        self.patch(H, "bars_daily", lambda s: daily)
    def test_empty_or_all_nan_daily_bars_at_fill_keep_armed_then_retry_fills(self):
        now = _ny(2026, 11, 4, 11, 0); nan = float("nan"); real = H.room_check
        for daily in ([], [dict(h=nan, l=nan, c=nan)] * 250):
            self.patch(H, "room_check", real); self._armed(now, daily); H.tick(); d = json.load(open(H.LEDGER))["trades"][0]
            self.assertEqual(d["status"], "ARMED"); self.assertIn("retry", d["data_error"]); self.assertIsNone(d.get("entry"))
            self.assertIn("gate_error_kept_armed", [e["kind"] for e in self.events()])
            flat = [dict(h=60.0, l=58.0 + (i % 3), c=59.0) for i in range(260)]
            self.patch(H, "bars_daily", lambda s: flat)
            self.patch(H, "room_check", lambda t, daily, entry=None: dict(ok=bool(daily), room_R=2.0, level=None, cost_R=0.05, why=None, no_data=not daily))
            H.tick(); self.assertEqual(json.load(open(H.LEDGER))["trades"][0]["status"], "FILLED")   # retried on the next tick
    def test_thin_but_real_history_is_still_a_verdict(self):
        now = _ny(2026, 11, 4, 11, 0); self._armed(now, [dict(h=60.0, l=55.0, c=58.0)] * 50); H.tick()
        d = json.load(open(H.LEDGER))["trades"][0]; self.assertEqual(d["status"], "SHADOW"); self.assertIn("daily bars", d["shadow_reason"])
