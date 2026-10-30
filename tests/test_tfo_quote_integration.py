"""Source-time freshness and execution gating; synthetic quotes only."""
import copy
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import command_server as server
import fundamental_book as book


class QuoteIntegration(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc).timestamp()
        self.q = dict(px=125, ccy='USD', quote_utc='2026-09-18T20:00:00Z',
                      fetched_at='2026-09-21T10:00:00Z', session='closed',
                      status='closed_last', quote_current=False, executable=False)

    def test_recent_fetch_does_not_make_friday_close_live(self):
        with patch.object(server, '_PX_CACHE', {'equity': [0, {}]}), \
             patch.object(server, '_remember') as remember, \
             patch.object(server, '_yf_fetch', return_value={'SOXL': self.q}), \
             patch('time.time', return_value=self.now):
            self.assertEqual(server.equity_marks(['SOXL']), {'SOXL': 125})
            label, age = server._equity_mark_details('SOXL')
            self.assertEqual(label, 'market closed · last quote')
            self.assertEqual(age, 223200)
            self.assertEqual(remember.call_args.args[1]['SOXL'], self.now - age)

    def test_other_symbol_refresh_does_not_rejuvenate_quote(self):
        meta = {'SOXL': self.q}
        other = dict(self.q, px=3, quote_utc='2026-09-21T10:00:00Z',
                     session='regular', quote_current=True, executable=True)
        with patch.object(server, '_PX_CACHE', {'equity': [self.now, {'SOXL': 125}],
                                               'equity_quotes': meta}), \
             patch.object(server, '_remember'), \
             patch.object(server, '_yf_fetch', return_value={'OTHER': other}), \
             patch('time.time', return_value=self.now):
            self.assertEqual(server.equity_marks(['OTHER']), {'OTHER': 3})
            self.assertEqual(server._equity_mark_details('SOXL')[1], 223200)

    def test_premarket_valuation_is_not_entry_eligible(self):
        q = dict(self.q, px=133, session='pre', quote_current=True,
                 quote_utc='2026-09-21T09:59:00Z')
        with patch('tfo_equity_refresh.fetch_quote', return_value=q), \
             patch.object(book, 'fx_to_usd', return_value=1):
            prices, failures = book.fetch_prices(['SOXL'])
            self.assertEqual(prices, {})
            self.assertIn('SOXL', failures)
            prices, failures = book.fetch_prices(['SOXL'], require_executable=False)
            self.assertEqual(prices['SOXL']['px_usd'], 133)
            self.assertTrue(book.quote_metadata(prices['SOXL'])['mark_stale'])
            self.assertEqual(book.quote_metadata(prices['SOXL'])['mark_utc'], q['quote_utc'])

    def test_missing_execution_metadata_fails_closed(self):
        self.assertTrue(book.quote_metadata({'px': 1})['mark_stale'])


if __name__ == '__main__': unittest.main()
