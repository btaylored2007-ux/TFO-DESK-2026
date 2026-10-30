#!/usr/bin/env python3
"""HT decision 2026-09-26 #4: War Room -- market-shut marks are not stale, fresh cached FX does not warn,
/api/all served from a background-built cache, retired crypto sleeves dropped from daily movers.
Offline: no network, no ledger writes (temp files only)."""
import datetime as dt, importlib, json, os, sys, tempfile, time, unittest
from unittest import mock
DESK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DESK)
UTC = dt.timezone.utc
SAT = dt.datetime(2026, 9, 26, 6, 0, tzinfo=UTC)
TUE_NY_11 = dt.datetime(2026, 9, 29, 15, 0, tzinfo=UTC)     # 11:00 New York
TUE_NY_17 = dt.datetime(2026, 9, 29, 21, 0, tzinfo=UTC)     # 17:00 New York

class WarRoom(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = importlib.import_module("command_server")

    def test_market_shut_uses_session_and_holiday_table(self):
        ms = self.s.market_shut
        self.assertTrue(ms("XLE", SAT)); self.assertTrue(ms("STO.AX", SAT)); self.assertTrue(ms("BAKKA.OL", SAT))
        self.assertFalse(ms("XLE", TUE_NY_11)); self.assertTrue(ms("XLE", TUE_NY_17))
        self.assertTrue(ms("XLE", dt.datetime(2026, 11, 26, 16, 0, tzinfo=UTC)))           # Thanksgiving
        self.assertFalse(ms("SOL/USD", SAT)); self.assertFalse(ms("PF_SOLUSD", SAT))
        self.assertTrue(ms("CBA.AX", dt.datetime(2026, 9, 28, 23, 0, tzinfo=UTC)))          # 09:00 Sydney Tue, pre-open
        self.assertFalse(ms("CBA.AX", dt.datetime(2026, 9, 29, 1, 0, tzinfo=UTC)))          # 11:00 Sydney

    def _health(self, rows, shut):
        with mock.patch.object(self.s, "market_shut", lambda sym, now=None: shut), \
             mock.patch.object(self.s, "quote_feed_status", lambda: {"ok": True, "error": None}), \
             mock.patch.object(self.s, "served_by", lambda: {}):
            return self.s.marks_health(rows)

    def test_closed_market_marks_are_not_stale(self):
        rows = [{"symbol": "XLE", "src": "stale after-hours quote", "mark": 60.0, "mark_age_sec": 30000, "floor": "F"},
                {"symbol": "STO.AX", "src": "market closed · last quote · QUOTE CCY NOT USD", "mark": 5.0, "mark_age_sec": 90000, "floor": "F"},
                {"symbol": "SOL/USD", "src": "live", "mark": 120.0}]
        h = self._health(rows, True)
        self.assertEqual((h["live"], h["closed"], h["stale"]), (1, 2, 0)); self.assertIsNone(h["warning"]); self.assertFalse(h["degraded"])
        h = self._health(rows, False)                                          # exchange open: same rows ARE stale
        self.assertEqual((h["closed"], h["stale"]), (0, 2)); self.assertTrue(h["degraded"])
        old = [dict(rows[0], mark_age_sec=6 * 86400)]                           # older than a long weekend: stale
        self.assertEqual(self._health(old, True)["stale"], 1)

    def test_fresh_cached_fx_is_flagged_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "last_marks.json")
            with open(p, "w") as f: json.dump({"AUDUSD=X": 0.70}, f)
            ter = __import__("types").SimpleNamespace(fx_cache_max_age=lambda when=None: 6 * 3600)
            with mock.patch.object(self.s, "_LAST_MARKS_FILE", p), mock.patch.object(self.s, "equity_marks", lambda t: {}), \
                 mock.patch.dict(sys.modules, {"tfo_equity_refresh": ter}):
                fx = self.s.fx_audusd()
                self.assertEqual((fx["status"], fx["fresh"], fx["rate"]), ("cached", True, 0.7))
                self.assertNotIn("not a live quote", fx["note"])
                os.utime(p, (time.time() - 7 * 3600,) * 2)
                fx = self.s.fx_audusd()
                self.assertEqual((fx["status"], fx["fresh"]), ("cached", False))

    def test_api_all_served_from_background_cache(self):
        calls = []
        def builder():
            calls.append(time.time()); time.sleep(0.3); return {"n": len(calls)}
        with mock.patch.dict(self.s._PAYLOAD_CACHE, {"t": [0.0, None]}), mock.patch.dict(self.s._PAYLOAD_LOCKS, {"t": __import__("threading").Lock()}):
            first = self.s._bg_cached("t", builder, period=0.2)                   # first ever: waits for warm-up
            self.assertEqual(first["n"], 1); self.assertIn("payload_age_s", first)
            t0 = time.time(); again = self.s._bg_cached("t", builder, period=0.2)
            self.assertLess(time.time() - t0, 0.1)                               # never waits on a rebuild
            time.sleep(1.2)
            self.assertGreaterEqual(len(calls), 2)                               # background thread keeps rebuilding
            self.assertGreaterEqual(self.s._bg_cached("t", builder, period=0.2)["n"], 2)

    def test_positions_background_idles_without_viewers(self):
        calls = []
        def builder():
            calls.append(1); return {"n": len(calls)}
        with mock.patch.dict(self.s._PAYLOAD_CACHE, {"u": [0.0, None]}), mock.patch.dict(self.s._PAYLOAD_LOCKS, {"u": __import__("threading").Lock()}):
            self.s._bg_cached("u", builder, period=0.1, idle=1.5)
            time.sleep(0.4); busy = len(calls); self.assertGreaterEqual(busy, 2)
            time.sleep(2.0); idle_n = len(calls); time.sleep(1.5)
            self.assertEqual(len(calls), idle_n)                                  # no rebuilds while nobody polls
            self.s._bg_cached("u", builder, period=0.1, idle=1.5); time.sleep(1.3)
            self.assertGreater(len(calls), idle_n)                                # a viewer wakes it up

    def test_retired_crypto_sleeves_dropped_from_daily_movers(self):
        a = importlib.import_module("tfo_asset_pnl")
        self.assertEqual(a.active_books(), ())
        r = a.retired_result()
        self.assertEqual((r["top"], r["covered_books"], r["scope_label"]), ([], [], "No current data"))
        self.assertEqual(set(r["retired_books"]), {"crypto_liq_beta_book", "crypto_clip_book", "crypto_uni_sui_rs", "crypto_mid_clip_book"})

    def test_dashboard_has_no_red_banner_markup(self):
        with open(os.path.join(DESK, "command_dashboard.html")) as f: html = f.read()
        self.assertNotIn("NO VALIDATED EDGE</span>", html)
        self.assertNotIn('+(st.headline||\'NO VALIDATED EDGE\')', html)
        self.assertIn("beta, not validated edge", html)
        self.assertIn(".banner,.banner.problem,.pill.problem{border-color:var(--line);color:var(--dim);background:transparent}", html)
        self.assertIn('id="p-hi"', html)

if __name__ == "__main__":
    unittest.main()
