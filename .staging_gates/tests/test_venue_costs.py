#!/usr/bin/env python3
"""Unit tests for the multi-venue cost model and universe mapping."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import venue_costs as ec       # noqa: E402
import venue_universe as eu    # noqa: E402


class TestCostModel(unittest.TestCase):

    def test_long_spot_pays_no_financing(self):
        """A non-leveraged long is a spot position: no overnight fee."""
        r = ec.round_trip("crypto", 10000.0, hold_days=30, side="long", leverage=1.0,
                          venue_name="etoro")
        self.assertEqual(r["overnight"], 0.0)
        self.assertFalse(r["is_cfd"])

    def test_short_accrues_financing(self):
        """Any short is a CFD, so financing accrues with hold length."""
        short7 = ec.round_trip("crypto", 10000.0, 7, "short", venue_name="etoro")
        short30 = ec.round_trip("crypto", 10000.0, 30, "short", venue_name="etoro")
        self.assertTrue(short7["is_cfd"])
        self.assertGreater(short7["overnight"], 0.0)
        self.assertGreater(short30["overnight"], short7["overnight"])

    def test_leveraged_long_is_cfd(self):
        self.assertTrue(ec.is_cfd("long", leverage=2.0))
        self.assertFalse(ec.is_cfd("long", leverage=1.0))

    def test_entry_plus_exit_equals_round_trip_when_flat(self):
        """Per-side helpers must reconcile with the round-trip total (no financing)."""
        n = 10000.0
        rt = ec.round_trip("crypto", n, hold_days=0, side="long")
        per_side = ec.entry_cost("crypto", n) + ec.exit_cost("crypto", n)
        self.assertAlmostEqual(rt["total"], per_side, places=6)

    def test_etoro_costs_exceed_kraken(self):
        """eToro (retired) remains the reference extreme: ~12x the Kraken wall."""
        e = ec.round_trip("crypto", 4000.0, 7, "short", venue_name="etoro")
        k = ec.kraken_round_trip(4000.0, 7, "short")
        self.assertGreater(e["total"], k["total"] * 5)

    def test_weekend_multiplier_adds_days(self):
        extra = ec.weekend_charges(14.0, weekend_day=4, weekend_mult=3.0)
        self.assertAlmostEqual(extra, 4.0, places=6)   # 2 crossings x 2 extra days
        self.assertEqual(ec.weekend_charges(14.0, None, 3.0), 0.0)

    def test_zero_notional_does_not_divide_by_zero(self):
        r = ec.round_trip("crypto", 0.0, 7, "short")
        self.assertEqual(r["pct_of_notional"], 0.0)

    def test_unknown_class_raises(self):
        with self.assertRaises(KeyError):
            ec.round_trip("not_a_real_class", 1000.0)

    def test_venue_without_class_raises_rather_than_falling_back(self):
        """Robinhood lists no equities. Pricing a stock there must fail loudly,
        not silently substitute the crypto schedule."""
        with self.assertRaises(KeyError):
            ec.round_trip("stock", 1000.0, venue_name="robinhood")
        # eToro did list stock, so it must still price
        self.assertGreater(ec.round_trip("stock", 1000.0, venue_name="etoro")["total"], 0)

    def test_active_venue_can_be_swapped(self):
        original = ec.ACTIVE_VENUE
        try:
            ec.set_active("kraken")
            self.assertEqual(ec.active()["label"], "Kraken spot")
        finally:
            ec.set_active(original)

    def test_eligibility_records_operator_jurisdictions(self):
        """The constraint that actually decides usability."""
        self.assertFalse(ec.eligibility("robinhood", "AU")["available"])
        self.assertTrue(ec.eligibility("kraken", "UK")["available"])

    def test_breakeven_hurdle_is_positive(self):
        self.assertGreater(ec.breakeven_move_pct("crypto", 0, "long"), 0.0)
        self.assertGreater(ec.breakeven_move_pct("crypto", 0, "long", venue_name="etoro"),
                           ec.breakeven_move_pct("crypto", 0, "long", venue_name="kraken"))

    def test_account_drag_is_charged_before_any_trade(self):
        """Where a venue converts currency, that cost is paid before any strategy runs."""
        d = ec.account_drag(40000.0, venue_name="etoro")
        self.assertGreater(d["total"], 0.0)
        self.assertGreater(d["pct_of_deposit"], 0.0)

    def test_robinhood_fx_conversion_is_flagged_unmodelled(self):
        """Robinhood drag reads 0 only because non-USD funding cost is UNMODELLED.

        Guards against that zero being mistaken for a real absence of cost.
        """
        af = ec.account_fees("robinhood")
        self.assertEqual(af.get("fx_conversion_pct"), 0.0)
        self.assertTrue(af.get("fx_conversion_unmodelled_for_non_usd_funder"))

    def test_defaults_are_flagged_unverified(self):
        """Guard rail: nothing should silently claim verified status."""
        if not os.path.exists(ec.OVERRIDE_FILE):
            self.assertFalse(ec.VERIFIED_AGAINST_LIVE_ACCOUNT)
            self.assertIn("MODELLED", ec.verification_notice())


class TestUniverse(unittest.TestCase):

    def test_kraken_legacy_tickers_map(self):
        self.assertEqual(eu.base_ticker("XBT/USD"), "BTC")
        self.assertEqual(eu.base_ticker("XDG/USD"), "DOGE")

    def test_majors_route_to_tight_schedule(self):
        tick, cls = eu.resolve("XBT/USD")
        self.assertEqual(tick, "BTC")
        self.assertEqual(cls, "crypto")

    def test_unlisted_symbol_is_blocked(self):
        tick, why = eu.resolve("BLESS/USD")
        self.assertIsNone(tick)
        self.assertEqual(why, "not-listed-at-%s" % ec.ACTIVE_VENUE)

    def test_delisted_symbol_is_blocked(self):
        tick, why = eu.resolve("XMR/USD")
        self.assertIsNone(tick)
        self.assertEqual(why, "delisted-or-unavailable")

    def test_coverage_counts_add_up(self):
        c = eu.coverage(["XBT/USD", "ETH/USD", "BLESS/USD", "PUMP/USD"])
        self.assertEqual(c["total"], 4)
        self.assertEqual(c["tradeable"] + c["missing"], 4)
        self.assertEqual(c["tradeable"], 2)

    def test_empty_universe_does_not_divide_by_zero(self):
        c = eu.coverage([])
        self.assertEqual(c["coverage_pct"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
