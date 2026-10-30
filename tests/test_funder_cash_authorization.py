import datetime as dt
import unittest
from unittest.mock import patch
import funder_cash_authorization as cash


class CashAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 9, 24, 9, tzinfo=dt.timezone.utc)
        self.auth = {'paper_only': True, 'source_book': 'gap_close_levered_book',
                     'effective_utc': '2026-09-24T08:36:01Z', 'expires_utc': '2026-09-25T08:16:11Z',
                     'overridden_legacy_reason': 'sleeve kill -35%',
                     'maximum_source_cash_usd': 100, 'waives_entry_checks': False,
                     'waives_new_freezes': False}
        self.ledger = {'cash': 150, 'holdings': {}, 'frozen': True,
                       'frozen_reason': 'sleeve kill -35%'}

    def test_authority_is_capped_and_does_not_mutate_ledger(self):
        result = cash.assess(self.auth, self.ledger, self.now)
        self.assertTrue(result['funding_authorized'])
        self.assertEqual(result['authorized_cash_usd'], 100)
        self.assertTrue(self.ledger['frozen'])
        self.assertFalse(result['entry_checks_waived'])

    def test_expiry_and_before_start_fail_closed(self):
        for instant in ('2026-09-24T08:36:00+00:00','2026-09-25T08:16:11+00:00'):
            self.assertFalse(cash.assess(self.auth, self.ledger, dt.datetime.fromisoformat(instant))['funding_authorized'])

    def test_other_freeze_and_non_cash_holdings_block(self):
        for change in ({'frozen_reason': 'new loss'}, {'book_risk': {'frozen': True}},
                       {'frozen_at_utc': '2026-09-24T08:40:00Z'},
                       {'holdings': {'X': 1}}, {'cash': float('nan')}):
            self.assertFalse(cash.assess(self.auth, {**self.ledger, **change}, self.now)['funding_authorized'])

    def test_live_or_entry_waiver_not_authorized(self):
        for change in ({'paper_only': False}, {'waives_entry_checks': True}, {'source_book': 'other'}):
            self.assertFalse(cash.assess({**self.auth, **change}, self.ledger, self.now)['funding_authorized'])

    def test_transfer_amount_cannot_exceed_cash_or_use_invalid_numbers(self):
        with patch.object(cash, 'current', return_value=cash.assess(self.auth, self.ledger, self.now)):
            self.assertTrue(cash.require_funding(100)['funding_authorized'])
            for amount in (101, 0, -1, True, float('nan')):
                with self.assertRaises(ValueError):
                    cash.require_funding(amount)
