#!/usr/bin/env python3
"""HT decision 2026-09-26 #5: ESTIMATED fees for pre-patch HT fills (setup report + V1-vs-V2 compare). Pure; no ledger I/O."""
import datetime as dt, importlib, os, sys, unittest
DESK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DESK)
import desk_costs as dc
UTC = dt.timezone.utc

TAG = {"ts": "2026-09-25T10:05:28Z", "symbol": "SOL/USD", "side": "long", "qty": 1000.0, "fill_px_usd": 100.0, "fees_usd": None, "ticket_id": "HT-1"}
HALF = {"ticket_id": "HT-2", "utc": "2026-09-25T12:00:00Z", "action": "HALF", "pair": "SOL/USD", "qty": 500.0, "px_usd": 104.0, "pnl_usd": 2000.0}
BOOK = {"positions": {"SOL/USD": {"qty": 500.0, "entry_px": 100.0, "entry_ticket": {"ticket_id": "HT-1"}}},
        "mark_px_usd": {"SOL/USD": 110.0}, "head_trader_tickets": [HALF]}

def ff(sym, act, qty, px, when):
    return dc.fill_fees(sym, dc.side_for(act, "long"), qty, px, when=dt.datetime.fromisoformat(when.replace("Z", "+00:00")))["fees_usd"]

class FeeEst(unittest.TestCase):
    def setUp(self): self.w = importlib.import_module("tfo_watchlist")

    def test_prepatch_fills_get_estimated_fee_net_stays_null(self):
        r = self.w.trade_pnl(dict(TAG), None, {"tfo_ht_crypto": BOOK})
        exp = ff("SOL/USD", "BUY", 1000, 100.0, TAG["ts"]) + ff("SOL/USD", "HALF", 500, 104.0, HALF["utc"])
        self.assertGreater(exp, 0)
        self.assertEqual(r["gross"], 2000.0 + 500 * 10.0)
        self.assertIsNone(r["net"])                                    # real net still needs recorded fees
        self.assertAlmostEqual(r["fees_est"], round(exp, 2), places=2)
        self.assertEqual(r["fills_fee_estimated"], 2)
        self.assertAlmostEqual(r["net_est"], round(r["gross"] - exp, 2), places=2)

    def test_recorded_fees_are_used_not_estimated(self):
        tag = dict(TAG, fees_usd=7.0); book = dict(BOOK, head_trader_tickets=[dict(HALF, fees_usd=3.0)])
        r = self.w.trade_pnl(tag, None, {"tfo_ht_crypto": book})
        self.assertEqual((r["fees_est"], r["fills_fee_estimated"]), (10.0, 0))
        self.assertEqual(r["net"], r["net_est"])

    def test_compare_est_fee_matches_desk_costs(self):
        c = importlib.import_module("tfo_compare")
        t = {"utc": "2026-09-25T13:55:57Z", "action": "BUY", "sym": "XLE", "side": "long", "qty": 3091.21, "px_usd": 62.0, "px_native": 62.0, "ccy": "USD"}
        self.assertAlmostEqual(c.est_fee(t), ff("XLE", "BUY", 3091.21, 62.0, t["utc"]))
        self.assertEqual(c.est_fee({"utc": "bad"}), 0.0)               # unpriceable never raises

if __name__ == "__main__":
    unittest.main()
