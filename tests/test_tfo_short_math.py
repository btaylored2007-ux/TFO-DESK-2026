import copy
import math
import unittest
from tfo_short_math import close_short, depth_vwap, mark_short, open_short, rail_action, round_quantity

UTC = "2026-09-20T07:00:00Z"


class ShortMathTests(unittest.TestCase):
    def powder(self):
        return {"book_key": "powder", "cash": 200000., "equity": 200000.,
                "seed_usd": 180000., "start": 180000., "capital_events": []}

    def opened(self):
        return open_short(self.powder(), book_key="new_short", symbol="PF_SOLUSD",
                          funding_usd=100000., quantity=999., entry_price=100.,
                          cover_price=100.1, utc=UTC, lot=0.1)

    def test_depth_sorted_and_full_fill_both_sides(self):
        book = {"bids": [[99, 4], [100, 2]], "asks": [[102, 4], [101, 2]]}
        self.assertAlmostEqual(depth_vwap(book, "sell", 3)["vwap"], 299 / 3)
        self.assertAlmostEqual(depth_vwap(book, "cover", 3)["vwap"], 304 / 3)
        with self.assertRaises(ValueError):
            depth_vwap(book, "sell", 6.001)

    def test_bad_depth_rejected(self):
        for book in ({"bids": [], "asks": [[101, 2]]},
                     {"bids": [[101, 2]], "asks": [[101, 2]]},
                     {"bids": [[102, 2]], "asks": [[101, 2]]},
                     {"bids": [[100, float("nan")]], "asks": [[101, 2]]},
                     {"bids": [[0, 2]], "asks": [[101, 2]]},
                     {"bids": [[100, 2]], "asks": [[101, -1]]},
                     {"bids": [[1e308, 2]], "asks": [[1.1e308, 2]]}):
            with self.subTest(book=book), self.assertRaises(ValueError):
                depth_vwap(book, "sell", 2)

    def test_lots_floor_decimal_without_oversizing(self):
        self.assertEqual(round_quantity(0.3, 0.1), 0.3)
        self.assertEqual(round_quantity(12.345, 0.01), 12.34)
        for q, lot in ((0.01, 0.1), (0, 1), (1, 0), (math.inf, 1), (True, 1)):
            with self.assertRaises(ValueError):
                round_quantity(q, lot)

    def test_open_conserves_capital_and_has_two_fees_in_liquidation(self):
        original = self.powder()
        book, powder = self.opened()
        self.assertEqual(original, self.powder())
        self.assertEqual(book["seed_usd"] + powder["seed_usd"], original["seed_usd"])
        self.assertAlmostEqual(book["cash"], 50.05)
        self.assertAlmostEqual(book["equity"] + powder["equity"],
                               200000 - 49.95 - 99.9 - 999 * 100.1 * .0005)
        self.assertEqual(book["capital_events"][0]["usd"], -powder["capital_events"][0]["usd"])

    def test_funding_negative_and_no_input_mutation(self):
        book, _ = self.opened()
        before = copy.deepcopy(book)
        marked = mark_short(book, cover_price=98., funding_delta_usd=-25.)
        self.assertEqual(book, before)
        self.assertAlmostEqual(marked["equity"], 100000 - 49.95 + 999 * 2 - 25 - 999 * 98 * .0005)
        self.assertEqual(marked["funding_total_usd"], -25.)

    def test_half_bank_then_full_close_conserves_economic_profit(self):
        book, powder = self.opened()
        book = mark_short(book, cover_price=97., funding_delta_usd=-20.)
        before, powder_before = copy.deepcopy(book), copy.deepcopy(powder)
        total_before = book["equity"] + powder["equity"]
        book, powder = close_short(book, powder, quantity=499.5, exit_price=97.,
                                   remaining_cover_price=97., utc=UTC)
        self.assertEqual(before["quantity"], 999.)
        self.assertEqual(powder_before["cash"], 100000.)
        self.assertAlmostEqual(book["equity"] + powder["equity"], total_before)
        self.assertAlmostEqual(book["deploy_basis_usd"], book["equity"])
        self.assertAlmostEqual(book["funding_accrued_usd"], -10.)
        self.assertEqual(book["capital_events"][-1]["usd"], -powder["capital_events"][-1]["usd"])
        book, powder = close_short(book, powder, quantity=499.5, exit_price=95.,
                                   remaining_cover_price=95., utc=UTC)
        expected_pnl = 499.5 * 3 + 499.5 * 5 - 20 - 49.95 - 499.5 * (97 + 95) * .0005
        self.assertEqual(book["equity"], 0)
        self.assertEqual(book["quantity"], 0)
        self.assertAlmostEqual(powder["equity"], 200000 + expected_pnl)
        self.assertAlmostEqual(-book["seed_usd"], expected_pnl)
        self.assertAlmostEqual(book["seed_usd"] + powder["seed_usd"], 180000)
        self.assertEqual(book["funding_total_usd"], -20)
        self.assertEqual(book["realised_funding_usd"], -20)

    def test_loss_stays_visible_after_close_and_transfer(self):
        book, powder = self.opened()
        book, powder = close_short(book, powder, quantity=999., exit_price=104.,
                                   remaining_cover_price=104., utc=UTC)
        loss = 999 * 4 + 49.95 + 999 * 104 * .0005
        self.assertAlmostEqual(book["seed_usd"], loss)
        self.assertAlmostEqual(powder["equity"], 200000 - loss)
        self.assertEqual(book["realised_usd"], -3996)
        self.assertEqual(rail_action(book), "CLOSED")

    def test_odd_lot_half_bank_preserves_exact_closeable_residual(self):
        book, powder = open_short(self.powder(), book_key="new_short", symbol="PF_SOLUSD",
                                  funding_usd=75000., quantity=680.3, entry_price=100.,
                                  cover_price=100.1, utc=UTC, lot=.1)
        book, powder = close_short(book, powder, quantity=340.1, exit_price=97.,
                                   remaining_cover_price=97., utc="2026-09-20T07:01:00Z")
        self.assertEqual(book["quantity"], 340.2)
        self.assertEqual(round_quantity(book["quantity"], .1), 340.2)
        book, powder = close_short(book, powder, quantity=book["quantity"], exit_price=96.,
                                   remaining_cover_price=96., utc="2026-09-20T07:02:00Z")
        expected_profit = (340.1 * 3 + 340.2 * 4
                           - .0005 * (680.3 * 100 + 340.1 * 97 + 340.2 * 96))
        self.assertEqual(book["quantity"], 0.)
        self.assertEqual(book["equity"], 0.)
        self.assertEqual(book["status"], "CLOSED")
        self.assertAlmostEqual(powder["equity"], 200000. + expected_profit)
        self.assertAlmostEqual(book["seed_usd"] + powder["seed_usd"], 180000.)

    def test_rails_and_absolute_thesis_stop(self):
        book, _ = self.opened()
        for eq, action in ((105000, "FULL_BANK"), (102000, "HALF_BANK"),
                           (95000, "STOP"), (100000, "HOLD")):
            self.assertEqual(rail_action({**book, "equity": eq}), action)
        self.assertEqual(rail_action(book, thesis_stop_price=100.05), "STOP")

    def test_caps_collateral_and_close_rejections_are_atomic(self):
        powder = self.powder()
        args = dict(book_key="new_short", symbol="PF_SOLUSD", funding_usd=100000.,
                    quantity=999., entry_price=100., cover_price=100.1, utc=UTC, lot=.1)
        for changes in ({"funding_usd": 150001}, {"quantity": 1000},
                        {"quantity": 999.01}, {"cover_price": float("nan")}):
            with self.assertRaises(ValueError):
                open_short(powder, **{**args, **changes})
            self.assertEqual(powder, self.powder())
        book, powder = self.opened()
        saved = copy.deepcopy((book, powder))
        for quantity in (1000, 0, 499.51):
            with self.assertRaises(ValueError):
                close_short(book, powder, quantity=quantity, exit_price=100.,
                            remaining_cover_price=100., utc=UTC)
            self.assertEqual((book, powder), saved)

    def test_gap_insolvency_is_not_erased_or_charged_to_powder(self):
        book, powder = self.opened()
        book, after = close_short(book, powder, quantity=999., exit_price=210.,
                                  remaining_cover_price=210., utc=UTC)
        self.assertEqual(after, powder)
        self.assertLess(book["equity"], 0)
        self.assertTrue(book["insolvent"])
        self.assertEqual(book["seed_usd"], 100000.)


if __name__ == "__main__":
    unittest.main()
