"""2026-10-30 archive lock: tfo_ht_crypto never opens a new clip; exits still work. Offline, temp reports dir only."""
import contextlib, datetime as dt, io, json, os, sys, time, unittest
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_tfo_trail_rail import CryptoEngine, UTC


class ArchiveLock(CryptoEngine):
    def setUp(self):
        super().setUp()
        self.assertNotEqual(os.path.abspath(self.tc.R), os.path.abspath(os.path.join(self.tc.BASE, "reports")))
        self.assertTrue(self.tc.OPENS_DISABLED)

    def snap(self):
        return {f: open(os.path.join(self.tmp, f)).read() for f in sorted(os.listdir(self.tmp)) if os.path.isfile(os.path.join(self.tmp, f))}

    def test_buy_blocked_writes_nothing(self):
        self.book(); before = self.snap(); out = io.StringIO()
        with contextlib.redirect_stdout(out): rc = self.tc.cmd_buy("LINK/USD", 50000.0, "t", "beta", "x", True)
        self.assertEqual(rc, "tfo_ht_crypto_archived_no_opens"); self.assertIn("tfo_ht_crypto_archived_no_opens", out.getvalue())
        self.assertEqual(self.snap(), before)

    def test_post_only_blocked_writes_nothing(self):
        self.book(); self.bid["TON/USD"] = 5.0; q = self.tc.quote("TON/USD"); before = self.snap()
        with contextlib.redirect_stdout(io.StringIO()):
            rc = self.tc._post_entry("TON/USD", 1e5, "entry_engine_crypto", "beta", "x", q, {"c3_stop_dist": 0.05})
            rc2 = self.tc.cmd_buy("TON/USD", 1e5, "entry_engine_crypto", "beta", "x", True, post_only=True)
        self.assertEqual(rc, "tfo_ht_crypto_archived_no_opens"); self.assertEqual(rc2, rc); self.assertEqual(self.snap(), before)

    def test_no_ledger_rails_does_not_create(self):
        self.rails(); self.assertFalse(os.path.exists(os.path.join(self.tmp, "tfo_ht_crypto.json")))
        self.tc.book(); self.assertFalse(os.path.exists(os.path.join(self.tmp, "tfo_ht_crypto.json")))

    def test_resting_fill_never_opens(self):
        b = self.tc.new_book(); now = dt.datetime.now(UTC)
        b["resting_entries"] = {"TON/USD": {"ticket_id": "HT-x", "placed_utc": (now - dt.timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_utc": (now + dt.timedelta(seconds=600)).strftime("%Y-%m-%dT%H:%M:%SZ"), "limit_px": 5.0, "qty": 10.0, "usd": 50.0, "reserved_usd": 50.1,
            "theme": "t", "label": "beta", "why": "", "quote": {}, "c3_stop_dist": 0.05}}
        self.w("tfo_ht_crypto", b)
        with patch.object(self.tc, "trades_since", lambda p, s: [(4.9, 1.0, time.time())]): self.rails()
        b = self.r("tfo_ht_crypto"); self.assertEqual(b["positions"], {}); self.assertEqual(b["resting_log"][-1]["outcome"], "archived_cancel")

    def test_exits_still_work_on_existing_position(self):
        old = (dt.datetime.now(UTC) - dt.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.book(**{"LINK/USD": {"qty": 1000.0, "entry_px": 10.0, "orig_entry": 10.0, "entry_utc": old}})
        self.bid["LINK/USD"] = 9.4; self.rails()   # -6%: stop
        b = self.r("tfo_ht_crypto"); self.assertNotIn("LINK/USD", b["positions"]); self.assertEqual(b["head_trader_tickets"][-1]["action"], "STOP")

    def test_half_bank_still_works(self):
        old = (dt.datetime.now(UTC) - dt.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.book(**{"LINK/USD": {"qty": 1000.0, "entry_px": 10.0, "orig_entry": 10.0, "entry_utc": old}})
        self.bid["LINK/USD"] = 10.2; self.rails()
        self.assertAlmostEqual(self.r("tfo_ht_crypto")["positions"]["LINK/USD"]["qty"], 500.0)


if __name__ == "__main__":
    unittest.main()
