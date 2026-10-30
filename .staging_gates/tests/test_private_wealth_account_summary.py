import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from private_wealth_monitor import account_summary


class AccountSummaryTests(unittest.TestCase):
    def setUp(self):
        # 17 Oct 2026: isolate from LIVE archived-book receipts (book_archive.retained_basis_usd reads production receipts, -27,809.04 today)
        import book_archive
        p = patch.object(book_archive, 'retained_basis_usd', return_value=0.0); p.start(); self.addCleanup(p.stop)

    def test_total_cash_includes_all_books_not_position_equity(self):
        result=self.summary([{'key':'gap_close_levered_book','start_usd':100,'equity_usd':100,'cash_usd':100},
                             {'key':'other','start_usd':80,'equity_usd':120,'cash_usd':20}])
        self.assertEqual(result['total_cash_usd'],120)
        self.assertEqual(result['warehouse_cash_usd'],100)
        self.assertEqual(result['other_books_cash_usd'],20)
        self.assertEqual(result['books'][1]['cash_usd'],20)
        self.assertEqual(result['remaining_powder_usd'],120)
        self.assertEqual(result['deployed_powder_usd'],100)
        self.assertEqual(result['books'][1]['deployed_powder_usd'],100)
        self.assertEqual(result['deployed_powder_usd'] + result['remaining_powder_usd'], result['equity_usd'])

    def test_incomplete_cash_is_unknown_not_zero(self):
        result=self.summary([{'key':'a','start_usd':100,'equity_usd':100,'cash_usd':100},
                             {'key':'b','start_usd':80,'equity_usd':120}])
        self.assertIsNone(result['total_cash_usd'])
        self.assertIsNone(result['remaining_powder_usd'])
        self.assertIsNone(result['deployed_powder_usd'])

    def test_short_liability_is_not_clamped_to_zero(self):
        result=self.summary([{'key':'short','start_usd':100,'equity_usd':110,'cash_usd':200}])
        self.assertEqual(result['deployed_powder_usd'],-90)
        self.assertEqual(result['remaining_powder_usd'],200)

    def test_registry_gap_makes_powder_unknown(self):
        result=self.summary([{'key':'a','start_usd':100,'equity_usd':110,'cash_usd':20}], ['a','b'])
        self.assertIsNone(result['deployed_powder_usd'])
        self.assertIsNone(result['remaining_powder_usd'])

    def summary(self, books, keys=None, tickets=None):
        now = datetime(2026, 9, 24, 7, tzinfo=timezone.utc)
        cycle = {'boards': {'Books': {'verified_utc': now.isoformat(), 'data': {'books': books}}}}
        risk = {'book_keys': keys or [b['key'] for b in books], 'capital_accounting_valid': True}
        with tempfile.TemporaryDirectory() as directory:
            return account_summary(Path(directory), cycle, risk, {'tickets': tickets or []}, now)

    def test_known_receipt_sum_does_not_turn_unknown_retirement_pnl_to_zero(self):
        result = self.summary([{'key': 'a', 'start_usd': 100, 'equity_usd': 130}],
                              tickets=[{'utc': '2026-09-24T01:00:00Z', 'booked_pnl_usd': 5},
                                       {'utc': '2026-09-24T01:00:00Z', 'booked_pnl_usd': None}])
        self.assertIsNone(result['booked_today_receipted_usd'])
        self.assertEqual(result['booked_today_known_usd'], 5)
        self.assertEqual(result['booked_today_unknown_count'], 1)

    def test_negative_funding_residual_is_included(self):
        result = self.summary([{'key': 'a', 'start_usd': 120, 'equity_usd': 130},
                               {'key': 'b', 'start_usd': -20, 'equity_usd': 0}])
        self.assertEqual(result['seed_usd'], 100)
        self.assertEqual(result['pnl_since_seed_usd'], 30)
        self.assertEqual(result['return_since_seed_pct'], 30)
        self.assertEqual(result['daily_target_usd'], 15000)  # 17 Oct 2026: stale 10k; Brett 25 Sep mandate = $15k/day
        self.assertIsNone(result['daily_pnl_usd'])
        self.assertIsNone(result['booked_today_receipted_usd'])

    def test_missing_funding_not_zero(self):
        result = self.summary([{'key': 'a', 'equity_usd': 130}])
        self.assertIsNone(result['seed_usd'])
        self.assertIsNone(result['pnl_since_seed_usd'])

    def test_verified_retired_residual_stays_in_funding_denominator(self):
        with patch('coinspot_retirement.verified_retirement', return_value={
                'retained_funding_basis_usd': -20.}):
            result = self.summary([{'key': 'a', 'start_usd': 120., 'equity_usd': 130.}])
        self.assertEqual(result['seed_usd'], 100.)
        self.assertEqual(result['pnl_since_seed_usd'], 30.)

    def test_scope_mismatch_not_profit(self):
        result = self.summary([{'key': 'a', 'start_usd': 100, 'equity_usd': 130}], ['a', 'b'])
        self.assertIsNone(result['pnl_since_seed_usd'])
