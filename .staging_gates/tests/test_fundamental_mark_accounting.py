"""Same-tick accounting regressions using the frozen exit rules, no live I/O."""
import copy
import csv
import datetime as dt
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fundamental_book as main  # noqa: E402
import fundamental_spec_book as spec  # noqa: E402


def position(side="long", units=40.0, catalyst_days=30):
    return {
        "side": side, "units": units, "notional_usd": units * 100.0,
        "entry_px_usd": 100.0, "mark": 100.0, "mark_usd": 100.0,
        "mark_stale": False, "unrealised_usd": 0.0,
        "catalyst_date": (dt.datetime.now(dt.timezone.utc).date()
                          + dt.timedelta(days=catalyst_days)).isoformat(),
    }


def quote(price):
    return {"px": price, "px_usd": price, "fx_usd": 1.0, "executable": True,
            "quote_current": True, "session": "regular", "status": "regular_current",
            "quote_utc": "2026-09-21T14:00:00Z", "fetched_at": "2026-09-21T14:00:01Z"}


class MarkAccounting(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        # These production helpers can touch registries/rates. None may run in
        # this isolated test; the real exit arithmetic is intentionally used.
        self.stack.enter_context(mock.patch.dict(sys.modules, {
            "fundamental_distributions": SimpleNamespace(
                apply_pending=mock.Mock(return_value=None)),
            "cash_yield_books": SimpleNamespace(accrue_book=mock.Mock(return_value=0)),
        }))
        # 17 Oct 2026: isolate from the LIVE head-trader flag file (head_trader_gate.FLAG exists in production and makes
        # _mark_locked skip book exits). These tests exercise the book's own exit arithmetic, so the gate is forced off.
        import head_trader_gate
        self.stack.enter_context(mock.patch.object(head_trader_gate, "active", return_value=False))
        self.risk = mock.Mock()
        self.stack.enter_context(mock.patch.object(main, "_kill", self.risk))
        self.stack.enter_context(mock.patch.object(
            main, "_borrow", SimpleNamespace(accrue=mock.Mock(return_value=0))))

    def setup_main(self, pos, price, interest=0.0):
        self.ledger = {
            "seed_usd": 10000.0, "cash": 10000.0 - pos["notional_usd"] + interest,
            "cash_interest_accrued": interest,
            "realised_usd": 0.0, "costs_paid_usd": 0.0,
            "positions": {"TEST": pos},
        }
        self.csv_path = os.path.join(self.temp, "main.csv")
        self.stack.enter_context(mock.patch.object(main, "EQUITY_CSV", self.csv_path))
        self.stack.enter_context(mock.patch.object(main, "load_ledger", return_value=self.ledger))
        self.saved = self.stack.enter_context(mock.patch.object(main, "save_ledger"))
        self.prices = self.stack.enter_context(mock.patch.object(
            main, "fetch_prices", return_value=({"TEST": quote(price)}, {})))

    def setup_spec(self, pos, price):
        self.ledger = {
            "seed_usd": 10000.0, "cash": 10000.0 - pos["units"] * 100.0,
            "realised_usd": 0.0, "costs_paid_usd": 0.0,
            "positions": {"TEST": pos}, "sleeve_risk": {"peak_equity": 10000.0},
        }
        self.csv_path = os.path.join(self.temp, "spec.csv")
        self.stack.enter_context(mock.patch.object(spec, "EQUITY_CSV", self.csv_path))
        self.stack.enter_context(mock.patch.object(spec, "load", return_value=self.ledger))
        self.saved = self.stack.enter_context(mock.patch.object(spec, "save"))
        self.prices = self.stack.enter_context(mock.patch.object(
            spec.fb, "fetch_prices", return_value=({"TEST": quote(price)}, {})))

    def csv_rows(self):
        with open(self.csv_path) as handle:
            return list(csv.DictReader(handle))

    def assert_main_totals(self, equity, cash, mv, short_mv, unreal):
        self.assertEqual(self.ledger["equity"], equity)
        self.assertEqual(self.ledger["cash"], cash)
        self.assertEqual(self.ledger["long_mv_usd"], mv)
        self.assertEqual(self.ledger["short_mv_usd"], short_mv)
        self.assertEqual(self.ledger["unrealised_usd"], unreal)
        self.assertEqual(self.ledger["equity_identity_usd"], equity)
        self.assertEqual(self.ledger["equity_identity_gap_usd"], 0.0)
        self.assertAlmostEqual(self.risk.evaluate.call_args.args[1], equity)
        row = self.csv_rows()[-1]
        self.assertEqual(float(row["equity_usd"]), equity)
        self.assertEqual(float(row["unrealised_usd"]), unreal)

    def test_long_partial_uses_remaining_units_before_risk_and_history(self):
        self.setup_main(position(), 106.0)
        main._mark_locked(False)
        self.assertEqual(self.ledger["positions"]["TEST"]["units"], 30.0)
        self.assertEqual(self.ledger["positions"]["TEST"]["unrealised_usd"], 180.0)
        self.assertEqual(self.ledger["realised_usd"], 58.41)
        self.assert_main_totals(10238.41, 7058.41, 3180.0, 0.0, 180.0)
        # With unchanged prices and no second rung, the next tick must not
        # reverse a phantom gain or change the remaining position's P&L.
        main._mark_locked(False)
        self.assertEqual(self.ledger["trade_fills_this_tick"], [])
        self.assert_main_totals(10238.41, 7058.41, 3180.0, 0.0, 180.0)
        self.assertEqual(len(self.csv_rows()), 2)

    def test_delayed_main_exit_books_costs_and_cannot_replay_bar(self):
        p = position()
        p['entry_utc'] = '2026-09-01T00:00:00Z'
        self.setup_main(p, 106.0)
        q = quote(106.0)
        q.update(executable=False, paper_executable=True, quote_current=False,
                 paper_price_class='DELAYED PAPER', fetched_at='2026-09-21T14:15:00Z')
        self.prices.return_value = ({'TEST': q}, {})
        main._mark_locked(False)
        self.assertEqual(self.ledger['cash'], 7058.41)
        self.assertEqual(self.ledger['equity_identity_gap_usd'], 0)
        ticket = self.ledger['exit_checks']['legs']['TEST']['ticket']
        self.assertEqual(ticket['price_class'], 'DELAYED PAPER')
        self.assertEqual(ticket['price_source_utc'], q['quote_utc'])
        self.assertEqual(ticket['booked_pnl_usd'], 58.41)
        main._mark_locked(False)
        self.assertEqual(self.ledger['cash'], 7058.41)
        self.assertEqual(self.ledger['trade_fills_this_tick'], [])

    def test_delayed_spec_exit_and_pre_entry_bar_guard(self):
        p = position(units=10)
        p['entry_utc'] = '2026-09-21T14:01:00Z'
        self.setup_spec(p, 120)
        q = quote(120)
        q.update(executable=False, paper_executable=True, quote_current=False,
                 paper_price_class='DELAYED PAPER', fetched_at='2026-09-21T14:15:00Z')
        self.prices.return_value = ({'TEST': q}, {})
        spec._mark_locked(False)
        self.assertEqual(self.ledger['trade_fills_this_tick'], [])
        p['entry_utc'] = '2026-09-01T00:00:00Z'
        spec._mark_locked(False)
        self.assertEqual(self.ledger['cash'], 9389.07)
        self.assertEqual(self.ledger['exit_checks']['legs']['TEST']['ticket']['price_class'], 'DELAYED PAPER')

    def test_short_borrow_already_paid_is_not_debited_again_on_exit(self):
        pos = position('short')
        pos['borrow_accrued_usd'] = 20.0
        self.setup_main(pos, 94.0)
        self.ledger['cash'] -= 20.0
        self.ledger['costs_paid_usd'] = 20.0
        main._mark_locked(False)
        self.assertEqual(self.ledger['cash'], 7038.59)
        self.assertEqual(self.ledger['realised_usd'], 53.59)
        self.assertEqual(pos['borrow_released_cash_correct_usd'], 5.0)
        self.assertEqual(self.ledger['equity'], 10218.59)
        self.assertEqual(self.ledger['equity_identity_gap_usd'], 0.0)
        ticket = self.ledger['exit_checks']['legs']['TEST']['ticket']
        self.assertEqual(ticket['ticket_id'], pos['exit_fills'][-1]['ticket_id'])
        self.assertAlmostEqual(ticket['proceeds_usd'], 1058.59)

    def test_short_partial_releases_pnl_and_collateral_once(self):
        self.setup_main(position("short"), 94.0)
        main._mark_locked(False)
        self.assertEqual(self.ledger["positions"]["TEST"]["units"], 30.0)
        self.assertEqual(self.ledger["positions"]["TEST"]["unrealised_usd"], 180.0)
        self.assertEqual(self.ledger["short_notional_usd"], 3000.0)
        self.assertEqual(self.ledger["realised_usd"], 58.59)
        self.assert_main_totals(10238.59, 7058.59, 0.0, 2820.0, 180.0)
        main._mark_locked(False)
        self.assertEqual(self.ledger["trade_fills_this_tick"], [])
        self.assert_main_totals(10238.59, 7058.59, 0.0, 2820.0, 180.0)

    def test_long_expiry_full_release_has_no_residual_market_value(self):
        self.setup_main(position(catalyst_days=-14), 90.0)
        main._mark_locked(False)
        self.assertEqual(self.ledger["positions"], {})
        self.assertIn("TEST", self.ledger["closed"])
        self.assertEqual(self.ledger["trade_fills_this_tick"][0]["action"], "catalyst_expired")
        self.assert_main_totals(9594.6, 9594.6, 0.0, 0.0, 0.0)

    def test_short_stop_full_release_has_no_residual_unrealised(self):
        self.setup_main(position("short"), 130.0)
        main._mark_locked(False)
        self.assertEqual(self.ledger["positions"], {})
        self.assertEqual(self.ledger["trade_fills_this_tick"][0]["action"], "stop_out")
        self.assert_main_totals(8792.2, 8792.2, 0.0, 0.0, 0.0)

    def test_recorded_cash_interest_is_part_of_identity_not_trading_profit(self):
        self.setup_main(position(), 100.0, interest=3.25)
        main._mark_locked(False)
        self.assertEqual(self.ledger["realised_usd"], 0.0)
        self.assert_main_totals(10003.25, 6003.25, 4000.0, 0.0, 0.0)

    def test_stale_mark_carries_last_good_value_without_triggering_exit(self):
        self.setup_main(position(catalyst_days=-14), 90.0)
        self.prices.return_value = ({}, {"TEST": "synthetic quote failure"})
        main._mark_locked(False)
        self.assertTrue(self.ledger["positions"]["TEST"]["mark_stale"])
        self.assertEqual(self.ledger["trade_fills_this_tick"], [])
        self.assertEqual(self.ledger["n_unmarked"], 1)
        self.assert_main_totals(10000.0, 6000.0, 4000.0, 0.0, 0.0)

    def test_spec_partial_unrealised_reconciles_on_same_tick(self):
        self.setup_spec(position(units=10.0), 120.0)
        spec._mark_locked(False)
        self.assertAlmostEqual(self.ledger["positions"]["TEST"]["units"], 6.7)
        self.assertEqual(self.ledger["realised_usd"], 59.07)
        self.assertEqual(self.ledger["cash"], 9389.07)
        self.assertEqual(self.ledger["long_mv_usd"], 804.0)
        self.assertEqual(self.ledger["equity"], 10193.07)
        self.assertEqual(self.ledger["unrealised_usd"], 134.0)
        self.assertEqual(self.ledger["positions"]["TEST"]["unrealised_usd"], 134.0)
        self.assertEqual(float(self.csv_rows()[-1]["unrealised_usd"]), 134.0)
        spec._mark_locked(False)
        self.assertEqual(self.ledger["trade_fills_this_tick"], [])
        self.assertEqual(self.ledger["equity"], 10193.07)
        self.assertEqual(self.ledger["unrealised_usd"], 134.0)

    def test_spec_registered_expiry_closes_at_loss_below_profit_gate(self):
        self.setup_spec(position(units=10.0, catalyst_days=-14), 90.0)
        spec._mark_locked(False)
        self.assertEqual(self.ledger["positions"], {})
        self.assertIn("TEST", self.ledger["closed"])
        self.assertEqual(self.ledger["trade_fills_this_tick"][0]["action"], "catalyst_expired")
        self.assertEqual(self.ledger["cash"], 9884.25)
        self.assertEqual(self.ledger["equity"], 9884.25)
        self.assertEqual(self.ledger["realised_usd"], -115.75)
        self.assertEqual(self.ledger["unrealised_usd"], 0.0)
        self.assertEqual(self.ledger["long_mv_usd"], 0.0)
        self.assertEqual(float(self.csv_rows()[-1]["equity_usd"]), 9884.25)

    def test_spec_expiry_with_stale_mark_still_does_not_execute(self):
        self.setup_spec(position(units=10.0, catalyst_days=-14), 90.0)
        self.prices.return_value = ({}, {"TEST": "synthetic quote failure"})
        original = copy.deepcopy(self.ledger["positions"]["TEST"])
        spec._mark_locked(False)
        self.assertEqual(self.ledger["trade_fills_this_tick"], [])
        self.assertEqual(self.ledger["positions"]["TEST"]["units"], original["units"])
        self.assertTrue(self.ledger["positions"]["TEST"]["mark_stale"])
        self.assertEqual(self.ledger["equity"], 10000.0)

    def test_spec_valuation_only_quotes_preserve_trade_state(self):
        conditions = [
            ("stale", "regular", False, False),
            ("delayed", "regular", False, False),
            ("closed", "closed", False, True),
            ("pre_current", "pre", False, True),
            ("post_current", "post", False, True),
            ("unknown_eligibility", "regular", None, True),
        ]
        # Exercise each registered exit shape, including the evaluator's
        # peak mutation. Valuation-only quotes must neither fill nor advance
        # the runner's execution reference even when their prices would exit.
        for status, session, executable, current in conditions:
            for action, price, catalyst_days, released, peak in [
                ("scale_out", 140.0, 30, 0.0, 0.0),
                ("catalyst_expired", 90.0, -14, 0.0, 0.0),
                ("close_runner", 125.0, 30, 0.66, 0.5),
            ]:
                with self.subTest(status=status, action=action):
                    p = position(units=10.0, catalyst_days=catalyst_days)
                    p["exit_state"] = {
                        "released": released, "rungs_hit": [0.15, 0.30] if released else [],
                        "peak_net_gain": peak, "orig_units": 10.0,
                    }
                    self.setup_spec(p, price)
                    self.ledger["closed"] = {"OLD": {"units": 0, "exit_utc": "historical"}}
                    self.ledger["capital_events"] = [{"amount_usd": 10000.0}]
                    self.ledger["trade_history"] = [{"sym": "OLD", "action": "close"}]
                    before = copy.deepcopy(self.ledger)
                    q = quote(price)
                    q.update(session=session, price_session=session, status=status,
                             quote_current=current)
                    if executable is None:
                        q.pop("executable")
                    else:
                        q["executable"] = executable
                    self.prices.return_value = ({"TEST": q}, {})
                    # Confirm the chosen mark exercises a real exit rule, so
                    # this test cannot pass just because its fixture holds.
                    candidate = copy.deepcopy(p)
                    candidate["mark_usd"] = price
                    self.assertEqual(spec.ex.evaluate(candidate, "TEST")["action"], action)
                    with mock.patch.object(spec.ex, "evaluate", wraps=spec.ex.evaluate) as evaluate:
                        spec._mark_locked(False)
                    evaluate.assert_not_called()
                    marked = self.ledger["positions"]["TEST"]
                    self.assertEqual(marked["mark_usd"], price)
                    self.assertEqual(marked["mark_utc"], q["quote_utc"])
                    self.assertTrue(marked["mark_stale"])
                    self.assertEqual(marked["unrealised_usd"], (price - 100.0) * 10.0)
                    self.assertEqual(marked["units"], before["positions"]["TEST"]["units"])
                    self.assertEqual(marked["exit_state"], before["positions"]["TEST"]["exit_state"])
                    for key in ("cash", "seed_usd", "realised_usd", "costs_paid_usd",
                                "closed", "capital_events", "trade_history"):
                        self.assertEqual(self.ledger[key], before[key], key)
                    self.assertEqual(self.ledger["trade_fills_this_tick"], [])
                    self.assertEqual(self.ledger["equity"], before["cash"] + price * 10.0)
                    self.assertEqual(float(self.csv_rows()[-1]["equity_usd"]),
                                     self.ledger["equity"])

    def test_spec_deferred_quote_can_exit_when_regular_quote_arrives(self):
        self.setup_spec(position(units=10.0), 120.0)
        self.prices.return_value[0]["TEST"].update(
            executable=False, session="post", status="post_current")
        spec._mark_locked(False)
        self.assertEqual(self.ledger["trade_fills_this_tick"], [])
        self.assertEqual(self.ledger["positions"]["TEST"]["units"], 10.0)
        self.prices.return_value = ({"TEST": quote(120.0)}, {})
        spec._mark_locked(False)
        self.assertFalse(self.ledger["positions"]["TEST"]["mark_stale"])
        self.assertEqual(self.ledger["trade_fills_this_tick"][0]["action"], "scale_out")
        self.assertAlmostEqual(self.ledger["positions"]["TEST"]["units"], 6.7)
        self.assertEqual(self.ledger["cash"], 9389.07)
        self.assertEqual(self.ledger["equity"], 10193.07)


if __name__ == "__main__":
    unittest.main()
