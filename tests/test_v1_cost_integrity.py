"""2026-09-28 V1 cost integrity: borrow duplicate reversal + source paths that debit costs once."""
import contextlib
import copy
import json
import os
import tempfile
import unittest
from unittest import mock

import v1_borrow_dup_reversal_20260928 as rev
import profitability_controls as pc


def ledger():
    return {
        "cash": 100.0, "equity": 500.0, "equity_identity_usd": 500.0, "costs_paid_usd": 3.0,
        "positions": {
            "LEGACY": {"side": "short", "borrow_released_usd": 1.2345,
                       "exit_fills": [{"utc": "2026-09-10T00:00:00Z", "borrow_charged_usd": 1.23}]},
            "FIXED": {"side": "short", "borrow_released_usd": 2.0, "borrow_released_cash_correct_usd": 2.0,
                      "exit_fills": [{"utc": "2026-09-24T00:00:00Z", "borrow_charged_usd": 2.0,
                                      "borrow_already_debited_from_cash": True}]},
            "LONG": {"side": "long"},
        },
        "closed": {
            "MIXED": {"side": "short", "borrow_released_usd": 2.5, "borrow_released_cash_correct_usd": 1.5,
                      "exit_fills": [{"utc": "2026-09-15T00:00:00Z", "borrow_charged_usd": 1.0},
                                     {"utc": "2026-09-24T00:00:00Z", "borrow_charged_usd": 1.5,
                                      "borrow_already_debited_from_cash": True}]},
        },
    }


def duplicate(d):
    return sum(max(0.0, float(p.get("borrow_released_usd") or 0) - float(p.get("borrow_released_cash_correct_usd") or 0))
               for g in ("positions", "closed") for p in d[g].values())


class BorrowDuplicateReversalTests(unittest.TestCase):
    def test_reverses_only_the_legacy_duplicate_once(self):
        d = ledger()
        credit, rows = rev.reverse(d, "2026-09-28T08:20:00Z")
        self.assertEqual({r["symbol"] for r in rows}, {"LEGACY", "MIXED"})     # FIXED untouched
        self.assertAlmostEqual(credit, 2.23)                                    # 1.2345 + 1.0
        self.assertAlmostEqual(d["cash"], 102.23)
        self.assertAlmostEqual(duplicate(d), 0.0)
        self.assertEqual(d["closed"]["MIXED"]["borrow_dup_reversal"]["legacy_fill_utcs"], ["2026-09-15T00:00:00Z"])
        self.assertEqual(d["ledger_corrections"][-1]["tag"], rev.TAG)

    def test_equity_and_identity_move_together(self):
        d = ledger()
        rev.reverse(d, "u")
        self.assertAlmostEqual(d["equity"], 502.23)
        self.assertAlmostEqual(d["equity_identity_usd"], 502.23)
        self.assertAlmostEqual(d["equity_identity_gap_usd"], 0.0)

    def test_idempotent(self):
        d = ledger()
        rev.reverse(d, "u")
        after = copy.deepcopy(d)
        credit, rows = rev.reverse(d, "u2")
        self.assertEqual((credit, rows), (0.0, []))
        self.assertEqual(d, after)

    def test_monitor_finding_clears_after_reversal(self):
        d = ledger()
        rev.reverse(d, "u")
        # profitability_controls' own released-borrow measure
        released = sum(max(0.0, float(p.get("borrow_released_usd") or 0) - float(p.get("borrow_released_cash_correct_usd") or 0))
                       for _, p, _ in pc._all_positions(d))
        self.assertLess(released, 0.01)

    def test_realised_and_costs_are_not_touched(self):
        d = ledger(); d["realised_usd"] = -7.0
        rev.reverse(d, "u")
        self.assertEqual(d["realised_usd"], -7.0)
        self.assertEqual(d["costs_paid_usd"], 3.0)


class ForwardExitBorrowIsChargedOnceTests(unittest.TestCase):
    """The source fix (fundamental_exits, 2026-09-24) must keep borrow out of cash_delta twice."""

    def test_short_release_adds_borrow_back_to_cash_and_records_it(self):
        import fundamental_exits as fx
        pos = {"side": "short", "units": 100.0, "notional_usd": 1000.0, "entry_px_usd": 10.0,
               "mark_usd": 9.0, "borrow_accrued_usd": 4.0,
               "exit_state": {"released": 0.0, "rungs_hit": [], "peak_net_gain": None, "orig_units": 100.0}}
        decision = {"action": "scale_out", "release_fraction": 0.25, "net_gain_pct": 9.0, "reason": "t", "rung": 0.05}
        realised, cash_delta = fx.apply(pos, decision)
        fill = pos["exit_fills"][-1]
        self.assertAlmostEqual(fill["borrow_charged_usd"], 1.0)                 # 25% of 4.00
        self.assertTrue(fill["borrow_already_debited_from_cash"])
        self.assertAlmostEqual(pos["borrow_released_cash_correct_usd"], 1.0)
        # cash gets collateral + realised + the borrow add-back: borrow is not taken from cash again
        self.assertAlmostEqual(cash_delta, 250.0 + realised + 1.0)


class HeadTraderBuyDebitsEntryFeeTests(unittest.TestCase):
    """HT/watchlist BUYs (tfo_buy.execute) must debit the desk_costs entry fee from cash once."""

    def test_buy_funds_fee_from_powder_and_books_it_in_costs_paid(self):
        import tfo_buy, desk_costs
        book = {"seed_usd": 1000.0, "cash": 10.0, "costs_paid_usd": 5.0, "equity": 1010.0, "positions": {}}
        gcl = {"cash": 100000.0, "seed_usd": 100000.0, "start": 100000.0, "equity": 100000.0}
        store = {tfo_buy.BOOK: book, tfo_buy.GCL: gcl}
        q = {"px_usd": 5.0, "px": 8.0, "ccy": "AUD", "fx_usd": 0.625, "tape": "LIVE", "quote_utc": "2026-09-28T00:00:00Z"}
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(tfo_buy, "load", side_effect=lambda k: store[k]), \
             mock.patch.object(tfo_buy, "save", side_effect=lambda k, v: store.__setitem__(k, v)), \
             mock.patch.object(tfo_buy, "LOG", os.path.join(tmp, "log")), \
             mock.patch.object(tfo_buy, "FACTS", os.path.join(tmp, "facts")), \
             mock.patch.object(tfo_buy, "receipt", return_value={}), \
             mock.patch.object(tfo_buy.desk_lock, "ledger_lock", side_effect=lambda *a, **k: contextlib.nullcontext()):
            tfo_buy.execute("STO.AX", 10000.0, "energy", "alpha", "test", q)
        fee = round(desk_costs.fill_fees("STO.AX", "buy", 2000.0, 5.0, 8.0, "AUD")["fees_usd"], 2)
        self.assertGreater(fee, 0)
        self.assertAlmostEqual(store[tfo_buy.BOOK]["positions"]["STO.AX"]["entry_fees_usd"], fee)
        self.assertAlmostEqual(store[tfo_buy.BOOK]["costs_paid_usd"], 5.0 + fee)
        self.assertAlmostEqual(store[tfo_buy.GCL]["cash"], 100000.0 - 10000.0 - fee)   # firm cash pays the fee
        self.assertAlmostEqual(store[tfo_buy.BOOK]["seed_usd"], 1000.0 + 10000.0 + fee)  # identity nets costs_paid


if __name__ == "__main__":
    unittest.main()
