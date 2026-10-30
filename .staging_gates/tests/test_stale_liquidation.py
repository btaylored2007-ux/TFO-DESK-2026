"""2026-09-29 13:03 AEST: stale-position liquidation rules (tfo_rotation venue_retired/stale_flat, tfo_crypto sell). Offline, temp ledgers."""
import contextlib, datetime as dt, json, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_tfo_trail_rail import LedgerHarness, NOLOCK, UTC
from unittest.mock import patch
import tfo_rotation as rot

def row(**k):
    r = {"sym": "X", "side": "long", "half_banked": False, "status": "HOLD", "ret_orig_pct": -0.5, "held_days": 4.0}; r.update(k); return r

class RotationRules(unittest.TestCase):
    def test_venue_retired_london_rotates_us_keeps(self):
        self.assertEqual(rot.evaluate("BP.L", row(sym="BP.L"), "venue_retired")[0], "ROTATE")
        self.assertEqual(rot.evaluate("0939.HK", row(sym="0939.HK"), "venue_retired")[0], "KEEP")   # 16:22 override: Asia runs off on rails
        self.assertEqual(rot.evaluate("MSFT", row(sym="MSFT"), "venue_retired")[0], "KEEP")
        self.assertEqual(rot.evaluate("9984.T", row(sym="9984.T"), "venue_retired")[0], "KEEP")
    def test_banked_and_protected_never_sold(self):
        for rule in ("venue_retired", "stale_flat"):
            self.assertEqual(rot.evaluate("SHEL.L", row(sym="SHEL.L", half_banked=True), rule)[0], "KEEP")
            for s in ("CSL.AX", "XLK", "IBIT", "8766.T"): self.assertEqual(rot.evaluate(s, row(sym=s), rule)[0], "KEEP")
    def test_no_quote_keeps(self):
        self.assertEqual(rot.evaluate("BP.L", row(sym="BP.L", status="NO_QUOTE"), "venue_retired")[0], "KEEP")
    def test_stale_flat_thresholds(self):
        self.assertEqual(rot.evaluate("A", row(held_days=4.0, ret_orig_pct=-2.0), "stale_flat")[0], "ROTATE")
        self.assertEqual(rot.evaluate("A", row(held_days=4.0, ret_orig_pct=0.9), "stale_flat")[0], "ROTATE")
        self.assertEqual(rot.evaluate("A", row(held_days=4.0, ret_orig_pct=1.2), "stale_flat")[0], "KEEP")
        self.assertEqual(rot.evaluate("A", row(held_days=1.0, ret_orig_pct=-2.0), "stale_flat")[0], "KEEP")
        self.assertEqual(rot.evaluate("A", row(held_days=None), "stale_flat")[0], "KEEP")
        self.assertEqual(rot.evaluate("A", row(side="short"), "stale_flat")[0], "KEEP")
    def test_unknown_rule_raises(self):
        with self.assertRaises(ValueError): rot.evaluate_rule("A", row(), "nope")

class CryptoStaleSell(LedgerHarness):
    def setUp(self):
        super().setUp()
        import tfo_crypto as tc, tfo_ticket as tt
        self.tc = tc; self.bid = {}
        self.w("gap_close_levered_book", {"cash": 0.0, "seed_usd": 0.0, "start": 0.0})
        def quote(pair):
            b = self.bid[pair]
            return {"pair": pair, "bid": b, "ask": b * 1.0001, "last": b, "mid": b, "tape": "LIVE", "trade_age_sec": 1.0, "spread_bp": 1.0,
                    "utc": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
        def get(*a, **k): raise RuntimeError("offline")
        for p in (patch.object(tt, "R", self.tmp), patch.object(tc, "R", self.tmp), patch.object(tc, "LOG", os.path.join(self.tmp, "log.txt")),
                  patch.object(tc, "FACTS", os.path.join(self.tmp, "facts.jsonl")), patch.object(tc, "desk_lock", NOLOCK),
                  patch.object(tc, "quote", quote), patch.object(tc, "get", get)):
            p.start(); self.addCleanup(p.stop)
    def ago(self, d): return (dt.datetime.now(UTC) - dt.timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ")
    def book(self, **pos):
        b = self.tc.new_book(); b["positions"] = pos; b["mark_px_usd"] = {k: v["entry_px"] for k, v in pos.items()}; self.w("tfo_ht_crypto", b)
    def sell(self, pair, execute=True):
        with contextlib.redirect_stdout(open(os.devnull, "w")): self.tc.cmd_sell(pair, "stale_flat", "", execute)
    def test_stale_clip_sold_at_bid_net_fees_to_powder(self):
        self.book(**{"ETH/USD": {"qty": 10.0, "entry_px": 100.0, "orig_entry": 100.0, "entry_utc": self.ago(4)}})
        self.bid["ETH/USD"] = 98.0; self.sell("ETH/USD")
        b = self.r("tfo_ht_crypto"); g = self.r("gap_close_levered_book")
        self.assertNotIn("ETH/USD", b["positions"]); tk = b["head_trader_tickets"][-1]
        self.assertEqual((tk["action"], tk["exit_rule"], tk["px_usd"]), ("FLAT", "stale_flat", 98.0))
        self.assertGreater(tk["fees_usd"], 0); self.assertAlmostEqual(g["cash"], 980.0 - tk["fees_usd"], places=2)
        self.assertAlmostEqual(b["realised_usd"], -20.0 - tk["fees_usd"], places=2); self.assertAlmostEqual(b["cash"], 0.0, places=2)
        self.assertIn(" FLAT ETH/USD ", open(os.path.join(self.tmp, "log.txt")).read())
    def test_preview_changes_nothing(self):
        self.book(**{"ETH/USD": {"qty": 10.0, "entry_px": 100.0, "orig_entry": 100.0, "entry_utc": self.ago(4)}})
        self.bid["ETH/USD"] = 98.0; self.sell("ETH/USD", execute=False)
        self.assertIn("ETH/USD", self.r("tfo_ht_crypto")["positions"])
    def test_refusals(self):
        self.book(**{"A/USD": {"qty": 1.0, "entry_px": 100.0, "orig_entry": 100.0, "entry_utc": self.ago(1)},
                     "B/USD": {"qty": 1.0, "entry_px": 100.0, "orig_entry": 100.0, "entry_utc": self.ago(4), "rebase": 102.0},
                     "C/USD": {"qty": 1.0, "entry_px": 100.0, "orig_entry": 100.0, "entry_utc": self.ago(4)},
                     "D/USD": {"qty": 1.0, "entry_px": 100.0, "orig_entry": 100.0, "entry_utc": self.ago(4), "last_ticket_utc": self.ago(0.001)}})
        self.bid.update({"A/USD": 99.0, "B/USD": 103.0, "C/USD": 101.5, "D/USD": 99.0})
        for p in ("A/USD", "B/USD", "C/USD", "D/USD"): self.sell(p)
        self.assertEqual(sorted(self.r("tfo_ht_crypto")["positions"]), ["A/USD", "B/USD", "C/USD", "D/USD"])
    def test_non_live_tape_refused(self):
        self.book(**{"ETH/USD": {"qty": 10.0, "entry_px": 100.0, "orig_entry": 100.0, "entry_utc": self.ago(4)}})
        self.bid["ETH/USD"] = 98.0
        orig = self.tc.quote
        with patch.object(self.tc, "quote", lambda p: {**orig(p), "tape": "STALE"}): self.sell("ETH/USD")
        self.assertIn("ETH/USD", self.r("tfo_ht_crypto")["positions"])

if __name__ == "__main__":
    unittest.main()
