import copy
import unittest

import profitability_controls as pc


def main_ledger():
    return {
        "seed_usd": 1000.0,
        "contributed_capital_usd": 1000.0,
        "equity": 990.0,
        "equity_identity_usd": 990.0,
        "realised_usd": -3.0,
        "costs_paid_usd": 2.0,
        "borrow_accrued_total_usd": 2.0,
        "trade_rule": {
            "entry_cost_bps": 10.0,
            "exit_cost_bps": 10.0,
            "slippage_bps_per_side": 5.0,
        },
        "positions": {
            "LONG": {
                "side": "long",
                "sector": "consumer",
                "notional_usd": 250.0,
                "entry_px_usd": 10.0,
                "entry_utc": "2026-09-20T00:00:00Z",
                "units": 25.0,
                "mark_usd": 10.0,
                "mark_stale": False,
                "exit_state": {"orig_units": 25.0},
            },
            "SHORT": {
                "side": "short",
                "sector": "technology",
                "notional_usd": 100.0,
                "entry_px_usd": 10.0,
                "entry_utc": "2026-09-20T00:00:00Z",
                "units": 10.0,
                "mark_usd": 11.0,
                "mark_stale": False,
                "borrow_accrued_usd": 1.25,
                "borrow_released_usd": 0.50,
                "exit_state": {"orig_units": 12.0},
            },
        },
        "closed": {
            "DONE": {
                "side": "short",
                "sector": "consumer",
                "notional_usd": 0.0,
                "entry_px_usd": 10.0,
                "entry_utc": "2026-09-20T00:00:00Z",
                "units": 0.0,
                "mark_usd": 9.0,
                "borrow_released_usd": 0.25,
                "exit_state": {"orig_units": 5.0},
            }
        },
    }


class FullyLoadedNavTests(unittest.TestCase):
    def test_compact_soxl_report_uses_exact_archival_proof_without_mutation(self):
        from unittest.mock import patch
        from pathlib import Path
        import json
        archive = {'positions': {'SOXL': {'entry_px_usd': 10, 'units': 2,
                    'entry_ccy': 'USD', 'entry_fx_usd': 1,
                    'exit_state': {'orig_units': 3}}}}
        record = {'sym': 'SOXL', 'entry': 10, 'units': 2,
                  'utc': '2026-09-18T20:13:57Z'}
        before = copy.deepcopy(record)
        with patch.object(Path, 'read_bytes', return_value=json.dumps(archive).encode()), \
             patch.object(Path, 'is_symlink', return_value=False), \
             patch.object(pc.hashlib, 'sha256') as sha:
            sha.return_value.hexdigest.return_value = '3483bb9603ba9edc63699bd0423a129b68a56cee9899e4f420fd618a32c6c1b9'
            restored = pc._restore_soxl_cost_schema(record)
            self.assertEqual(restored['entry_px_usd'], 10)
            self.assertEqual(restored['units'], 2)
            self.assertEqual(restored['exit_state']['orig_units'], 3)
            self.assertEqual(record, before)
            with self.assertRaises(pc.MonitorError):
                pc._restore_soxl_cost_schema(dict(record, units=4))
            sha.return_value.hexdigest.return_value = 'changed'
            with self.assertRaises(pc.MonitorError):
                pc._restore_soxl_cost_schema(record)

    # 2026-09-28: Head Trader tickets record sold size as `qty`, engine fills as `units`.
    def test_head_trader_flat_closure_reconstructs_original_units_from_qty(self):
        ledger = main_ledger()
        ledger["closed"]["IBIT"] = {"side": "long", "entry_px_usd": 47.4352, "entry_utc": "2026-09-25T13:52:00Z", "units": 0.0, "notional_usd": 0.0, "mark_usd": 47.57,
                                    "exit_fills": [{"ticket_id": "HT-abc", "action": "FLAT", "qty": 4595.76, "px_usd": 47.57}]}
        self.assertAlmostEqual(pc._original_units(ledger["closed"]["IBIT"], "IBIT"), 4595.76)
        report = pc.build_fully_loaded_nav(ledger)                                   # no MonitorError -> tick step exits 0
        self.assertIn(report["status"], ("OK", "ATTENTION", "PROBLEM"))

    def test_head_trader_half_then_flat_counts_both_tickets(self):
        pos = {"units": 0.0, "entry_px_usd": 10.0, "notional_usd": 0.0,
               "exit_fills": [{"action": "HALF", "qty": 50.0}, {"action": "FLAT", "qty": 50.0}]}
        self.assertAlmostEqual(pc._original_units(pos, "X"), 100.0)
        pos = {"units": 50.0, "entry_px_usd": 10.0, "notional_usd": 500.0, "exit_fills": [{"action": "HALF", "qty": 50.0}]}
        self.assertAlmostEqual(pc._original_units(pos, "Y"), 100.0)                  # open half-banked residual

    def test_mixed_fill_schemas_never_double_count(self):
        pos = {"units": 0.0, "entry_px_usd": 10.0,
               "exit_fills": [{"action": "TRIM", "units": 30.0}, {"action": "FLAT", "qty": 70.0}, {"action": "X", "units": 5.0, "qty": 5.0}]}
        self.assertAlmostEqual(pc._original_units(pos, "Z"), 105.0)

    def test_exit_state_orig_units_still_wins(self):
        pos = {"units": 0.0, "entry_px_usd": 10.0, "exit_state": {"orig_units": 12.0}, "exit_fills": [{"action": "FLAT", "qty": 99.0}]}
        self.assertAlmostEqual(pc._original_units(pos, "W"), 12.0)

    def test_unreconstructable_position_still_fails_loudly(self):
        with self.assertRaises(pc.MonitorError):
            pc._original_units({"units": 0.0, "entry_px_usd": 10.0, "notional_usd": 0.0, "exit_fills": [{"action": "FLAT"}]}, "BAD")

    def test_cost_and_financing_bridge(self):
        report = pc.build_fully_loaded_nav(main_ledger())
        # desk_costs (the desk's one fill-cost model), per trade, at original units:
        #   LONG  buy 25 US sh: $1.00 minimum commission
        #   SHORT sell 12 sh @10: $1.00 + SEC 0.002472 + TAF 0.00234
        #   DONE  sell 5 sh @10: 1% cap $0.50 + SEC 0.00103 + TAF 0.000975
        self.assertAlmostEqual(report["cost_evidence"]["expected_entry_cost_usd"], 2.51)
        # Nothing recorded on any trade -> every trade is a shortfall.
        self.assertEqual(report["adjustment_bridge"]["entry_cost_true_up_usd"], -2.51)
        self.assertEqual({r["symbol"] for r in report["cost_evidence"]["entry_cost_shortfalls"]},
                         {"LONG", "SHORT", "DONE"})
        # The old trade_rule 15 bps figure stays visible as a benchmark only.
        self.assertAlmostEqual(report["cost_evidence"]["trade_rule_bps_entry_benchmark_usd"], 0.63)
        # Open marked gross: 250 + 110 = 360; 15 bps = 0.54.
        self.assertAlmostEqual(report["cost_evidence"]["exit_cost_reserve_usd"], 0.54)
        # Released borrow was already in daily cash costs, so add back once.
        self.assertAlmostEqual(
            report["adjustment_bridge"]["released_borrow_double_charge_correction_usd"],
            0.75,
        )
        self.assertAlmostEqual(report["adjustment_from_reported_usd"], -2.30)
        self.assertAlmostEqual(report["fully_loaded_liquidation_nav_usd"], 987.70)
        self.assertEqual(report["status"], "ATTENTION")

    def test_explicit_entry_cost_is_not_double_charged(self):
        ledger = main_ledger()
        ledger["entry_costs_paid_usd"] = 2.51
        report = pc.build_fully_loaded_nav(ledger)
        self.assertEqual(report["adjustment_bridge"]["entry_cost_true_up_usd"], 0.0)

    # 2026-09-28: ENTRY_COST_NOT_DEBITED read no per-trade fee field and compared a
    # bps benchmark against $0, although every V1 trade carries entry_fees_usd.
    @staticmethod
    def _with_fees(ledger, **fees):
        for group in ("positions", "closed"):
            for sym, pos in ledger[group].items():
                if sym in fees:
                    pos["entry_fees_usd"] = fees[sym]
        return ledger

    def test_recorded_desk_costs_entry_fees_clear_the_finding(self):
        ledger = self._with_fees(main_ledger(), LONG=1.0, SHORT=1.0, DONE=0.5)
        ledger["costs_paid_usd"] = 4.5                       # 2.00 borrow + 2.50 entry fees, as booked live
        report = pc.build_fully_loaded_nav(ledger)
        codes = {a["code"] for a in report["alerts"]}
        self.assertNotIn("ENTRY_COST_NOT_DEBITED", codes)
        self.assertNotIn("ENTRY_FEES_NOT_IN_CASH", codes)
        self.assertEqual(report["adjustment_bridge"]["entry_cost_true_up_usd"], 0.0)
        self.assertEqual(report["cost_evidence"]["recorded_entry_cost_source"], "per-position entry_fees_usd")

    def test_missing_fee_on_one_trade_is_not_netted_against_larger_fees(self):
        # LONG over-records (e.g. UK stamp) by more than SHORT's whole fee: still flagged.
        ledger = self._with_fees(main_ledger(), LONG=50.0, DONE=0.5)
        report = pc.build_fully_loaded_nav(ledger)
        alert = next(a for a in report["alerts"] if a["code"] == "ENTRY_COST_NOT_DEBITED")
        self.assertEqual(alert["symbols"], ["SHORT"])
        self.assertAlmostEqual(alert["amount_usd"], 1.0)

    def test_underbooked_fee_is_flagged_for_the_shortfall_only(self):
        ledger = self._with_fees(main_ledger(), LONG=0.40, SHORT=1.0, DONE=0.5)
        report = pc.build_fully_loaded_nav(ledger)
        alert = next(a for a in report["alerts"] if a["code"] == "ENTRY_COST_NOT_DEBITED")
        self.assertEqual(alert["symbols"], ["LONG"])
        self.assertAlmostEqual(alert["amount_usd"], 0.60)

    def test_recorded_fee_that_never_reached_cash_is_flagged(self):
        ledger = self._with_fees(main_ledger(), LONG=1.0, SHORT=1.0, DONE=0.5)
        ledger["costs_paid_usd"] = 1.0                       # fees recorded 2.50, only 1.00 debited
        report = pc.build_fully_loaded_nav(ledger)
        alert = next(a for a in report["alerts"] if a["code"] == "ENTRY_FEES_NOT_IN_CASH")
        self.assertAlmostEqual(alert["amount_usd"], 1.50)

    def test_compact_archival_record_combined_fee_counts_once(self):
        fee, source = pc._recorded_entry_fee({"sym": "SOXL", "fees_usd": 12.03,
                                              "fee_backfill": {"tag": "V1_FEE_BACKFILL_20260926"}})
        self.assertEqual(fee, 12.03)
        self.assertIn("archival", source)
        # A normal position's exit-ticket fees_usd is never mistaken for an entry fee.
        self.assertEqual(pc._recorded_entry_fee({"fees_usd": 5.0, "fee_backfill": {},
                                                 "exit_fills": [{"qty": 1}]})[0], None)

    def test_entry_fee_model_is_the_desk_cost_model(self):
        import desk_costs
        pos = {"side": "long", "entry_px_usd": 5.8, "entry_px": 8.06, "entry_ccy": "AUD",
               "entry_utc": "2026-08-27T12:14:49Z"}
        want = desk_costs.fill_fees("STO.AX", "buy", 1000.0, 5.8, 8.06, "AUD")["fees_usd"]
        self.assertAlmostEqual(pc._entry_fee_model("STO.AX", pos, 1000.0), want)
        with self.assertRaises(pc.MonitorError):
            pc._entry_fee_model("X", {"side": "long", "entry_px_usd": 1.0}, 1.0)

    def test_forward_corrected_borrow_is_not_added_back_again(self):
        ledger = main_ledger()
        ledger['positions']['SHORT']['borrow_released_cash_correct_usd'] = 0.50
        report = pc.build_fully_loaded_nav(ledger)
        self.assertEqual(report['adjustment_bridge']['released_borrow_double_charge_correction_usd'], 0.25)

    def test_stale_mark_is_loud_but_uses_last_good_mark(self):
        ledger = main_ledger()
        ledger["positions"]["LONG"]["mark_stale"] = True
        report = pc.build_fully_loaded_nav(ledger)
        self.assertEqual(report["mark_quality"]["stale_symbols"], ["LONG"])
        self.assertIn("STALE_MARK_RESERVE", {a["code"] for a in report["alerts"]})

    def test_missing_cost_model_fails(self):
        ledger = main_ledger()
        del ledger["trade_rule"]["entry_cost_bps"]
        with self.assertRaises(pc.MonitorError):
            pc.build_fully_loaded_nav(ledger)


class CapitalShadowTests(unittest.TestCase):
    def test_verified_net_withdrawn_basis_preserves_negative_residual(self):
        rows = self.rows()
        rows.append({'key': 'closed_clip', 'start_usd': -10, 'equity_usd': 0,
                     'net_funding_basis_verified': True})
        report = pc.build_capital_shadow(rows, pc.build_fully_loaded_nav(main_ledger()),
                                         main_ledger(), self.spec(), self.tournament())
        self.assertEqual(report['capital_denominators']['all_capital']['contributed_capital_usd'], 1160)

    def rows(self):
        return [
            {"key": "fundamental_book", "label": "main", "start_usd": 1000.0, "equity_usd": 990.0, "stale": False},
            {"key": "fundamental_spec_book", "label": "spec", "start_usd": 100.0, "equity_usd": 95.0, "stale": False},
            {"key": "carry_paper_book", "label": "carry", "start_usd": 50.0, "equity_usd": 51.0, "stale": False},
            {"key": "punt_24h", "label": "dead", "start_usd": 10.0, "equity_usd": 0.0, "stale": False},
            {"key": "tour_probe_agri", "label": "control", "start_usd": 10.0, "equity_usd": 11.0, "stale": False},
        ]

    def spec(self):
        return {
            "seed_usd": 100.0,
            "positions": {
                "SPEC": {
                    "side": "long",
                    "notional_usd": 20.0,
                    "entry_px_usd": 2.0,
                    "units": 10.0,
                    "mark_usd": 1.9,
                }
            },
        }

    def tournament(self):
        return {"controls": [{"name": "probe_agri", "role": "control"}]}

    def test_dead_capital_stays_in_canonical_denominator(self):
        nav = pc.build_fully_loaded_nav(main_ledger())
        report = pc.build_capital_shadow(
            self.rows(), nav, main_ledger(), self.spec(), self.tournament()
        )
        all_capital = report["capital_denominators"]["all_capital"]
        active = report["capital_denominators"]["active_only_diagnostic"]
        self.assertEqual(all_capital["contributed_capital_usd"], 1170.0)
        self.assertEqual(active["contributed_capital_usd"], 1160.0)
        self.assertEqual(report["capital_denominators"]["dead_capital"]["pnl_usd"], -10.0)
        self.assertNotIn("validated_mechanism", report["evidence_capital"])
        self.assertIn("carry_paper_book", report["evidence_capital"]["unvalidated"]["book_keys"])
        self.assertEqual(report["evidence_capital"]["control"]["book_keys"], ["tour_probe_agri"])

    def test_sector_breach_and_missing_spec_sector_are_visible(self):
        nav = pc.build_fully_loaded_nav(main_ledger())
        report = pc.build_capital_shadow(
            self.rows(), nav, main_ledger(), self.spec(), self.tournament()
        )
        breaches = report["sector_concentration"]["main"]["sector_breaches"]
        self.assertEqual(breaches[0]["sector"], "consumer")
        self.assertEqual(breaches[0]["breach_usd"], 50.0)
        self.assertEqual(
            report["sector_concentration"]["speculative"]["sector_coverage"]["unclassified"],
            1,
        )

    def test_missing_start_fails_loudly(self):
        rows = copy.deepcopy(self.rows())
        rows[0]["start_usd"] = None
        nav = pc.build_fully_loaded_nav(main_ledger())
        with self.assertRaises(pc.MonitorError):
            pc.build_capital_shadow(rows, nav, main_ledger(), self.spec(), self.tournament())

    def test_registered_zero_shadow_preserves_capital_and_equity_totals(self):
        nav = pc.build_fully_loaded_nav(main_ledger())
        ordinary = pc.build_capital_shadow(self.rows(), nav, main_ledger(), self.spec(), self.tournament())
        rows = self.rows() + [{"key": "floor_exit_shadow", "kind": "shadow",
                              "start_usd": 0, "equity_usd": 0, "stale": False}]
        report = pc.build_capital_shadow(rows, nav, main_ledger(), self.spec(), self.tournament())
        self.assertEqual(report["capital_denominators"], ordinary["capital_denominators"])
        shadow = next(row for row in report["books"] if row["key"] == "floor_exit_shadow")
        self.assertEqual(shadow["fully_loaded_equity_usd"], 0)
        self.assertEqual(shadow["evidence_class"], "measurement_shadow")
        self.assertTrue(shadow["zero_capital_shadow"])

    def test_only_exact_registered_shadow_can_have_zero_capital(self):
        nav = pc.build_fully_loaded_nav(main_ledger())
        for value in (0, -1, None, float("nan"), True):
            rows = self.rows(); rows[0]["start_usd"] = value
            with self.assertRaises(pc.MonitorError):
                pc.build_capital_shadow(rows, nav, main_ledger(), self.spec(), self.tournament())
        for kind, start, equity in (("book", 0, 0), ("shadow", 1, 0), ("shadow", 0, 1), ("shadow", -1, 0)):
            rows = self.rows() + [{"key": "floor_exit_shadow", "kind": kind,
                                  "start_usd": start, "equity_usd": equity}]
            with self.assertRaises(pc.MonitorError):
                pc.build_capital_shadow(rows, nav, main_ledger(), self.spec(), self.tournament())

    def test_carry_is_unvalidated_and_ratios_are_not_inception_performance(self):
        nav = pc.build_fully_loaded_nav(main_ledger())
        report = pc.build_capital_shadow(self.rows(), nav, main_ledger(), self.spec(), self.tournament())
        carry = next(row for row in report["books"] if row["key"] == "carry_paper_book")
        self.assertEqual(carry["evidence_class"], "unvalidated")
        self.assertIn("historical funding/cost reconciliation", carry["evidence_reason"])
        for payload in (nav, report, report["capital_denominators"]["all_capital"],
                        report["capital_denominators"]["active_only_diagnostic"]):
            self.assertIn("not inception return", payload["return_basis_note"])
            self.assertIn("TWR", payload["return_basis_note"])

    def test_stale_registry_book_is_problem(self):
        rows = copy.deepcopy(self.rows())
        rows[0]["stale"] = True
        nav = pc.build_fully_loaded_nav(main_ledger())
        report = pc.build_capital_shadow(
            rows, nav, main_ledger(), self.spec(), self.tournament()
        )
        self.assertEqual(report["status"], "PROBLEM")
        self.assertIn("STALE_BOOKS", {a["code"] for a in report["alerts"]})


if __name__ == "__main__":
    unittest.main()
