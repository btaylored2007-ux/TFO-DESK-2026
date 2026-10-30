"""audit H12 (Brett 27 Oct 2026 19:35 WITA): tfo_crypto rails - protective stops are exempt from the 5-minute ticket cooldown;
planned exits (HALF / FLAT_5PCT) still wait. Offline, temp ledgers (CryptoEngine harness)."""
import datetime as dt, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_tfo_trail_rail import CryptoEngine, UTC


class StopCooldown(CryptoEngine):
    def setUp(self):
        super().setUp(); self.book()

    def _held(self, **kw):
        now = dt.datetime.now(UTC); old = (now - dt.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        recent = (now - dt.timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pos = {"qty": 1000.0, "entry_px": 10.0, "orig_entry": 10.0, "entry_utc": old, "last_ticket_utc": recent, **kw}
        b = self.tc.new_book(); b["positions"] = {"LINK/USD": pos}; b["mark_px_usd"] = {"LINK/USD": 10.0}; self.w("tfo_ht_crypto", b)

    def test_stop_fires_inside_cooldown(self):
        self._held(); self.bid["LINK/USD"] = 9.4; self.rails()
        b = self.r("tfo_ht_crypto"); self.assertNotIn("LINK/USD", b["positions"]); self.assertAlmostEqual(b["head_trader_tickets"][-1]["px_usd"], 9.4)

    def test_planned_half_still_waits_inside_cooldown(self):
        self._held(); self.bid["LINK/USD"] = 10.2; self.rails()
        b = self.r("tfo_ht_crypto"); self.assertAlmostEqual(b["positions"]["LINK/USD"]["qty"], 1000.0)


if __name__ == "__main__":
    unittest.main()
