"""Funder directive 2026-09-28 20:04 AEST: V1 ENTRY venues US / Tokyo / ASX from 2026-09-29 Sydney.
Entries only; exits never gated; crypto never gated. Offline: no ledger, quote or lock I/O."""
import contextlib
import datetime as dt
import io
import os
import sys
import types
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

import v1_venues as vv

SYD = ZoneInfo("Australia/Sydney")
MON_2338 = dt.datetime(2026, 9, 28, 23, 38, tzinfo=SYD)   # tonight's US fire
TUE_1720 = dt.datetime(2026, 9, 29, 17, 20, tzinfo=SYD)   # first London open after the directive
TUE_1635 = dt.datetime(2026, 9, 29, 16, 35, tzinfo=SYD)   # 25 min before London open
TUE_0935 = dt.datetime(2026, 9, 29, 9, 35, tzinfo=SYD)    # 25 min before Tokyo open (10:00 AEST)
MON_1635 = dt.datetime(2026, 9, 28, 16, 35, tzinfo=SYD)
TUE_1621 = dt.datetime(2026, 9, 29, 16, 21, tzinfo=SYD)   # one minute before the 16:22 no-Asia override
TUE_1622 = dt.datetime(2026, 9, 29, 16, 22, tzinfo=SYD)
WED_0935 = dt.datetime(2026, 9, 30, 9, 35, tzinfo=SYD)    # tomorrow's pre-open hunt slot (Tokyo would open 10:00 AEST)
WED_1100 = dt.datetime(2026, 9, 30, 11, 0, tzinfo=SYD)


def fixed_dt(when):
    class _DT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return when.astimezone(tz) if tz else when.replace(tzinfo=None)
    return types.SimpleNamespace(datetime=_DT, timedelta=dt.timedelta, timezone=dt.timezone, date=dt.date)


class AllowlistRuleTests(unittest.TestCase):
    def test_allowed_venues_pass_from_29_sep(self):
        for sym in ("RCL", "SCHW", "GOOGL", "BRK-B", "XRO.AX", "CSL.AX"):
            self.assertIsNone(vv.entry_block_reason(sym, TUE_1720), sym)
        for sym in ("8035.T", "6501.T"):                                      # Tokyo allowed until the 16:22 override
            self.assertIsNone(vv.entry_block_reason(sym, TUE_0935), sym)

    def test_london_and_other_venues_rejected_from_29_sep(self):
        for sym in ("VOD.L", "BP.L", "SAP.DE", "MC.PA", "0939.HK", "2454.TW", "RY.TO", "BSANTANDER.SN", "NOVO-B.CO", "TCS.NS"):
            r = vv.entry_block_reason(sym, TUE_1720)
            self.assertTrue(r and r.startswith("VENUE_NOT_ALLOWED"), sym)
        self.assertIn(".L entries closed from 2026-09-29", vv.entry_block_reason("AZN.L", TUE_1720))

    def test_nothing_changes_on_28_sep_sydney(self):
        for sym in ("VOD.L", "RY.TO", "0939.HK", "RCL"):
            self.assertIsNone(vv.entry_block_reason(sym, MON_2338), sym)
        # 23:59 AEST Mon is still 28 Sep; 00:00 AEST Tue is 29 Sep (UTC date differs from Sydney date)
        self.assertIsNone(vv.entry_block_reason("VOD.L", dt.datetime(2026, 9, 28, 13, 59, tzinfo=dt.timezone.utc)))
        self.assertIsNotNone(vv.entry_block_reason("VOD.L", dt.datetime(2026, 9, 28, 14, 0, tzinfo=dt.timezone.utc)))

    def test_crypto_is_never_gated(self):
        for sym in ("XBT/USD", "SOL/USD", "BTC-USD", "PF_XBTUSD"):
            self.assertIsNone(vv.entry_block_reason(sym, TUE_1720), sym)
        self.assertIsNone(vv.market_block_reason("CRYPTO", TUE_1720))

    def test_watchlist_markets(self):
        for mk in ("US", "ASX", "CRYPTO"):
            self.assertIsNone(vv.market_block_reason(mk, TUE_1720))
        self.assertIsNone(vv.market_block_reason("TOKYO", TUE_0935))
        self.assertTrue(vv.market_block_reason("TOKYO", TUE_1720).startswith("VENUE_NOT_ALLOWED"))
        for mk in ("EUROPE", "HK", "INDIA", "B3"):
            self.assertTrue(vv.market_block_reason(mk, TUE_1720).startswith("VENUE_NOT_ALLOWED"))
            self.assertIsNone(vv.market_block_reason(mk, MON_2338))

    def test_naive_time_is_refused(self):
        with self.assertRaises(ValueError):
            vv.entry_block_reason("VOD.L", dt.datetime(2026, 9, 29, 12, 0))


class EntryPathTests(unittest.TestCase):
    def test_tfo_buy_execute_refuses_london_before_touching_a_ledger(self):
        import tfo_buy
        boom = mock.Mock(side_effect=AssertionError("ledger touched"))
        with mock.patch.object(tfo_buy, "dt", fixed_dt(TUE_1720)), mock.patch.object(tfo_buy, "load", boom), \
             mock.patch.object(tfo_buy, "save", boom):
            with self.assertRaises(SystemExit) as cm:
                tfo_buy.execute("VOD.L", 50000.0, "uk", "beta", "t", {"px_usd": 1.0, "tape": "DELAYED"})
        self.assertIn("VENUE_NOT_ALLOWED", str(cm.exception))
        boom.assert_not_called()

    def test_tfo_buy_gates_list_the_venue_reason(self):
        import tfo_buy
        stop = RuntimeError("stop after venue check")
        with mock.patch.object(tfo_buy, "dt", fixed_dt(TUE_1720)), mock.patch.object(tfo_buy, "load", side_effect=stop), \
             mock.patch.object(tfo_buy.v1_venues, "entry_block_reason", wraps=tfo_buy.v1_venues.entry_block_reason) as spy:
            with self.assertRaises(RuntimeError):
                tfo_buy.gates("BP.L", 50000.0, "uk")
        self.assertTrue(spy.call_args and spy.call_args[0][0] == "BP.L")

    def test_engine_and_sleeve_openers_refuse_london_from_29_sep(self):
        import fundamental_spec_book as sb, convexity_sleeve as cs, fundamental_open_new as fon
        with mock.patch.object(vv, "_syd_date", return_value=dt.date(2026, 9, 29)):
            with self.assertRaises(SystemExit) as a:
                sb.open_position("BARC.L", "GBP", 10000.0, "t", "2026-12-01", 12, 0.1, 0.001, 1e7)
            self.assertIn("VENUE_NOT_ALLOWED", str(a.exception))
            with self.assertRaises(SystemExit) as b:
                fon.open_new("SHEL.L", "long", 10000.0, "t", "2026-12-01", dry_run=True)
            self.assertIn("VENUE_NOT_ALLOWED", str(b.exception))
            fields = {f: "x" for f in cs.REQUIRED_FIELDS}; fields.update(symbol="ULVR.L", max_loss_usd=100, premium_usd=100)
            with mock.patch.object(cs, "load", return_value={"positions": {}, "cash": 1e6}):
                with self.assertRaises(SystemExit) as c:
                    cs.open_position(**fields)
            self.assertIn("VENUE_NOT_ALLOWED", str(c.exception))


class NoAsiaOverrideTests(unittest.TestCase):
    """Funder override 2026-09-29 16:22 AEST: no more Tokyo or Asia markets; US + ASX only; crypto unchanged."""
    ASIA = ("8766.T", "9984.T", "6501.T", "8001.T", "6857.T", "8035.T", "0939.HK", "2330.TW", "6488.TWO", "005930.KS",
            "035720.KQ", "600519.SS", "000858.SZ", "D05.SI", "TCS.NS", "500325.BO", "BBCA.JK", "PTT.BK", "1155.KL")

    def test_cutoff_is_16_22_aest(self):
        self.assertIsNone(vv.entry_block_reason("8001.T", TUE_1621))
        r = vv.entry_block_reason("8001.T", TUE_1622)
        self.assertTrue(r.startswith("VENUE_NOT_ALLOWED")); self.assertIn("16:22", r); self.assertIn("no more Tokyo or Asia", r)

    def test_all_asia_blocked_after_cutoff(self):
        for when in (TUE_1622, TUE_1720, WED_0935, WED_1100, dt.date(2026, 9, 30)):
            for sym in self.ASIA:
                self.assertTrue((vv.entry_block_reason(sym, when) or "").startswith("VENUE_NOT_ALLOWED"), (sym, when))

    def test_us_asx_crypto_still_allowed(self):
        for sym in ("RCL", "GOOGL", "BRK-B", "CSL.AX", "ANZ.AX", "XBT/USD", "SOL/USD"):
            self.assertIsNone(vv.entry_block_reason(sym, WED_1100), sym)
        self.assertEqual(vv.allowed_equity_venues(WED_1100), frozenset({"US", "AX"}))
        self.assertEqual(vv.allowed_watchlist_markets(WED_1100), frozenset({"US", "ASX", "CRYPTO"}))

    def test_asia_markets_not_hunted(self):
        for mk in ("TOKYO", "HK", "INDIA"):
            self.assertTrue(vv.market_block_reason(mk, WED_0935).startswith("VENUE_NOT_ALLOWED"), mk)
        for mk in ("US", "ASX", "CRYPTO"):
            self.assertIsNone(vv.market_block_reason(mk, WED_0935), mk)

    def test_require_entry_allowed_hard_stops_tokyo(self):
        with mock.patch.object(vv, "asia_in_force", return_value=True):
            with self.assertRaises(SystemExit): vv.require_entry_allowed("8001.T")

    def test_asia_leftovers_are_liquidation_exempt(self):
        for sym in self.ASIA: self.assertTrue(vv.liquidation_exempt(sym), sym)
        for sym in ("VOD.L", "BP.L", "RCL", "CSL.AX"): self.assertFalse(vv.liquidation_exempt(sym), sym)

    def test_rotation_venue_retired_keeps_tokyo_but_sweeps_london(self):
        import tfo_rotation as rot
        row = lambda s: {"sym": s, "side": "long", "half_banked": False, "status": "HOLD", "ret_orig_pct": -1.0, "held_days": 2.0}
        for s in ("9984.T", "6501.T", "8001.T", "0939.HK"): self.assertEqual(rot.evaluate(s, row(s), "venue_retired")[0], "KEEP", s)
        self.assertEqual(rot.evaluate("BP.L", row("BP.L"), "venue_retired")[0], "ROTATE")

    def test_funder_watch_has_no_tokyo(self):
        import tfo_watchlist as wl
        self.assertNotIn("TOKYO", wl.FUNDER_WATCH_PRIORITY)
        for s in ("6857.T", "8035.T", "6501.T"): self.assertNotIn(s, wl.FUNDER_WATCH_NAMES)


class WatchlistTests(unittest.TestCase):
    def setUp(self):
        import tfo_watchlist as wl
        self.wl = wl

    def test_removed_markets_never_wake_on_tick_from_29_sep(self):
        self.assertIn("EUROPE", self.wl.markets_due(MON_1635.astimezone(dt.timezone.utc)))      # 28 Sep: unchanged
        self.assertNotIn("EUROPE", self.wl.markets_due(TUE_1635.astimezone(dt.timezone.utc)))   # 29 Sep: dead slot
        self.assertIn("TOKYO", self.wl.markets_due(TUE_0935.astimezone(dt.timezone.utc)))       # kept until 16:22 29 Sep
        self.assertNotIn("TOKYO", self.wl.markets_due(WED_0935.astimezone(dt.timezone.utc)))    # 16:22 override: never again
        self.assertIn("ASX", self.wl.markets_due(WED_0935.astimezone(dt.timezone.utc)))

    def test_tokyo_fire_and_build_refused_after_override(self):
        boom = mock.Mock(side_effect=AssertionError("should exit before the lock"))
        out = io.StringIO()
        with mock.patch.object(self.wl, "now_utc", return_value=WED_1100.astimezone(dt.timezone.utc)), \
             mock.patch.object(self.wl, "wl_lock", boom), mock.patch.object(self.wl, "read_doc", boom), contextlib.redirect_stdout(out):
            self.assertEqual(self.wl.cmd_fire("TOKYO", True, 5), [])
        self.assertIn("VENUE_NOT_ALLOWED", out.getvalue()); boom.assert_not_called()
        with mock.patch.object(self.wl, "desk", mock.Mock(side_effect=AssertionError("quote fetch"))):
            e = self.wl.build_equity("TOKYO", WED_0935.astimezone(dt.timezone.utc))
        self.assertEqual(e["status"], "SKIPPED"); self.assertIn("VENUE_NOT_ALLOWED", e["reason"])

    def test_removed_market_fire_exits_before_lock_or_read(self):
        boom = mock.Mock(side_effect=AssertionError("should exit before the lock"))
        out = io.StringIO()
        with mock.patch.object(self.wl, "now_utc", return_value=TUE_1720.astimezone(dt.timezone.utc)), \
             mock.patch.object(self.wl, "wl_lock", boom), mock.patch.object(self.wl, "read_doc", boom), \
             contextlib.redirect_stdout(out):
            self.assertEqual(self.wl.cmd_fire("LSE", True, 5), [])
        self.assertIn("VENUE_NOT_ALLOWED", out.getvalue()); self.assertIn("LOG none", out.getvalue())
        boom.assert_not_called()

    def test_removed_market_build_is_skipped_without_quotes(self):
        boom = mock.Mock(side_effect=AssertionError("desk import / quote fetch"))
        with mock.patch.object(self.wl, "desk", boom):
            e = self.wl.build_equity("EUROPE", TUE_1635.astimezone(dt.timezone.utc))
        self.assertEqual(e["status"], "SKIPPED"); self.assertIn("VENUE_NOT_ALLOWED", e["reason"])

    def test_28_sep_us_fire_is_unaffected(self):
        # Tonight's US fire must get past the venue gate to the normal lock/read path.
        out = io.StringIO()
        with mock.patch.object(self.wl, "now_utc", return_value=MON_2338.astimezone(dt.timezone.utc)), \
             mock.patch.object(self.wl, "wl_lock", side_effect=OSError("busy")), contextlib.redirect_stdout(out):
            self.assertEqual(self.wl.cmd_fire("US", True, 5), [])
        self.assertIn("another fire is running", out.getvalue())
        self.assertNotIn("VENUE_NOT_ALLOWED", out.getvalue())

    def test_us_list_drops_toronto_names_only_from_29_sep(self):
        us = [s for s, _ in self.wl.universe("US")]
        tue = [s for s in us if vv.entry_block_reason(s, TUE_1720) is None]
        mon = [s for s in us if vv.entry_block_reason(s, MON_2338) is None]
        self.assertEqual(mon, us)
        self.assertEqual(sorted(set(us) - set(tue)), sorted(s for s in us if s.endswith(".TO")))
        self.assertIn("SHEL", tue); self.assertIn("RCL", tue)                       # US listings/ADRs stay

    def test_crypto_fire_is_not_venue_gated(self):
        self.assertIsNone(vv.market_block_reason("CRYPTO", TUE_1720))


sys.path.insert(0, os.path.dirname(__file__))
import test_tfo_trail_rail as _ttr  # noqa: E402  (offline ledger harness; module import so its own tests are not re-collected)


class LondonExitStillWorksTests(_ttr.EquityEngine):
    def setUp(self):
        super().setUp()   # 2026-09-29: pin the London session window (10:00 London) so these no longer fail outside London hours
        _orig = self.tt.time_block; _at = dt.datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Europe/London"))
        p = mock.patch.object(self.tt, "time_block", lambda sym, now: _orig(sym, _at)); p.start(); self.addCleanup(p.stop)

    def test_london_half_bank_fires_on_29_sep(self):
        self.book(**{"VOD.L": self.pos()}); self.px["VOD.L"] = 103.0            # +3% -> +2% half-bank
        with mock.patch.object(vv, "_syd_date", return_value=dt.date(2026, 9, 29)):
            self.assertIsNotNone(vv.entry_block_reason("VOD.L"))                 # entries ARE blocked...
            self.run_cycle()                                                     # ...the exit still runs
        p = self.r("fundamental_book")["positions"]["VOD.L"]
        self.assertAlmostEqual(p["units"], 50.0)
        self.assertIn(" HALF VOD.L ", open(os.path.join(self.tmp, "log.txt")).read())

    def test_london_stop_fires_on_29_sep(self):
        self.book(**{"BP.L": self.pos()}); self.px["BP.L"] = 94.0                # -6% -> -5% stop
        with mock.patch.object(vv, "_syd_date", return_value=dt.date(2026, 9, 29)):
            self.run_cycle()
        self.assertNotIn("BP.L", self.r("fundamental_book")["positions"])


class TokyoExitStillWorksTests(_ttr.EquityEngine):
    """16:22 override: Tokyo ENTRIES are blocked, Tokyo EXITS (stop / +2% half) still run in the Tokyo session."""
    def setUp(self):
        super().setUp()   # session windows evaluated at a fixed in-session Tokyo time (10:30 JST Wed 30 Sep) so the test is clock-independent
        _orig = self.tt.time_block; _at = dt.datetime(2026, 9, 30, 10, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
        p = mock.patch.object(self.tt, "time_block", lambda sym, now: _orig(sym, _at)); p.start(); self.addCleanup(p.stop)

    def _after_cutoff(self):
        return mock.patch.object(vv, "asia_in_force", return_value=True)

    def test_tokyo_half_bank_fires_after_override(self):
        self.book(**{"8001.T": self.pos()}); self.px["8001.T"] = 103.0
        with self._after_cutoff():
            self.assertIsNotNone(vv.entry_block_reason("8001.T")); self.run_cycle()
        self.assertAlmostEqual(self.r("fundamental_book")["positions"]["8001.T"]["units"], 50.0)
        self.assertIn(" HALF 8001.T ", open(os.path.join(self.tmp, "log.txt")).read())

    def test_tokyo_stop_fires_after_override(self):
        self.book(**{"9984.T": self.pos()}); self.px["9984.T"] = 94.0
        with self._after_cutoff(): self.run_cycle()
        self.assertNotIn("9984.T", self.r("fundamental_book")["positions"])


# Reuse only the harness: the inherited rail tests already run in test_tfo_trail_rail.
for _cls in (LondonExitStillWorksTests, TokyoExitStillWorksTests):
    for _name in [n for n in dir(_ttr.EquityEngine) if n.startswith("test_") and n not in vars(_cls)]:
        setattr(_cls, _name, None)


if __name__ == "__main__":
    unittest.main()
