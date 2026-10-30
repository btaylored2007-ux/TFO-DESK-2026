"""Daily asset attribution must reconcile quantities and net cash receipts."""
import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tfo_asset_pnl as assets


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.opening = {key: {'holdings': {'BTC/USD': 10},
            'mark_px_usd': {'BTC/USD': 100}, 'capital_events': [],
            'last_mark_utc': '2026-09-20T13:50:00Z'} for key in assets.BOOKS}
        self.current = copy.deepcopy(self.opening)
        for book in self.current.values():
            book['last_mark_utc'] = '2026-09-21T10:00:00Z'
        self.daily = {'status': 'PROXY', 'book_contributions': [
            {'book': key, 'pnl_usd': 0} for key in assets.BOOKS]}

    def result(self):
        return assets.calculate(self.opening, self.current, self.daily)

    def test_partial_sale_uses_net_receipts_not_principal_as_profit(self):
        book = self.current[assets.BOOKS[0]]
        book['holdings']['BTC/USD'] = 6
        book['mark_px_usd']['BTC/USD'] = 110
        book['capital_events'] = [{'type': 'HALF_BANK', 'sold': {
            'BTC/USD': {'qty': 4, 'usd': 438}}}]
        self.daily['book_contributions'][0]['pnl_usd'] = 98
        got = self.result()
        self.assertEqual(got['status'], 'PROXY')
        self.assertEqual(got['top'][0]['pnl_usd'], 98)
        # Same asset aggregated across all four opening positions.
        self.assertEqual(got['top'][0]['pnl_pct'], 2.45)

    def test_rebalance_receipt_counted_once_and_full_exit_needs_no_current_mark(self):
        book = self.current[assets.BOOKS[0]]
        fill = {'pair': 'BTC/USD', 'quantity': 10, 'net_usd': 950}
        book.update(holdings={}, mark_px_usd={}, rebalance_fills=[fill],
                    capital_events=[{'action': 'REBALANCE_TRIM', 'fills': [fill]}])
        self.daily['book_contributions'][0]['pnl_usd'] = -50
        got = self.result()
        self.assertEqual(got['status'], 'PROXY')
        self.assertEqual(got['top'][0]['pnl_usd'], -50)
        self.assertEqual(got['top'][0]['pnl_pct'], -1.25)

    def test_unreceipted_quantity_change_purchase_and_rewritten_history_withheld(self):
        for problem in ('quantity', 'purchase', 'history'):
            with self.subTest(problem=problem):
                self.setUp()
                book = self.current[assets.BOOKS[0]]
                if problem == 'quantity': book['holdings']['BTC/USD'] = 9
                if problem == 'purchase': book['holdings']['ETH/USD'] = 1
                if problem == 'history':
                    self.opening[assets.BOOKS[0]]['capital_events'] = [{'id': 'existing'}]
                got = self.result()
                self.assertEqual(got['status'], 'UNVERIFIED')
                self.assertEqual(got['top'], [])

    def test_book_reconciliation_and_missing_coverage_withhold_ranking(self):
        self.daily['book_contributions'][0]['pnl_usd'] = 12
        self.assertEqual(self.result()['status'], 'UNVERIFIED')
        del self.current[assets.BOOKS[0]]
        self.assertEqual(self.result()['status'], 'UNVERIFIED')

    def test_rank_is_absolute_dollar_impact_and_retains_negative_sign(self):
        pairs = ['BTC/USD', 'ETH/USD', 'XRP/USD', 'SOL/USD', 'SUI/USD', 'NEAR/USD']
        opening = self.opening[assets.BOOKS[0]]
        opening.update(holdings={p: 10 for p in pairs}, mark_px_usd={p: 100 for p in pairs})
        current = self.current[assets.BOOKS[0]]
        current['holdings'] = copy.deepcopy(opening['holdings'])
        deltas = [-20, 15, 10, 5, 2, 1]
        current['mark_px_usd'] = {p: 100+d for p, d in zip(pairs, deltas)}
        self.daily['book_contributions'][0]['pnl_usd'] = 130
        got = self.result()
        self.assertEqual(got['status'], 'PROXY')
        self.assertEqual([r['symbol'] for r in got['top']], pairs[:5])
        self.assertEqual(got['top'][0]['pnl_usd'], -200)


if __name__ == '__main__': unittest.main()
