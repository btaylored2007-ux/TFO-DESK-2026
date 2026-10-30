import json
import tempfile
import unittest
from pathlib import Path
from private_wealth_views import fill_rows, ledger_history, position_rows


class ViewTests(unittest.TestCase):
    def test_basket_expands_without_repeating_parent_totals(self):
        ticket = {'ticket_id': 'one', 'principal_usd': 100, 'booked_pnl_usd': 10,
                  'sold': {'A': {'qty': 1, 'pnl_usd': 3}, 'B': {'qty': 2, 'pnl_usd': 7}}}
        rows = fill_rows([ticket])
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(r['booked_pnl_usd'] for r in rows), 10)
        self.assertIsNone(rows[0]['principal_usd'])
        self.assertEqual(len({r['fill_id'] for r in rows}), 2)

    def test_nested_history_excludes_transfers_and_preserves_currency(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'book.json'
            record = {'sym': 'ETH', 'pnl': 5, 't': 'undated local time'}
            path.write_text(json.dumps({'capital_events': [{'type': 'capital_transfer', 'usd': 99}],
                                        'desks': {'A': {'closed_trades': [record, record]}}}))
            result = ledger_history([{'key': 'b', 'path': str(path), 'ccy': 'AUD'}])
            self.assertEqual(len(result['rows']), 1)
            self.assertEqual(result['rows'][0]['pnl_currency'], 'AUD')
            self.assertEqual(result['rows'][0]['recorded_time'], 'undated local time')

    def test_unavailable_source_visible(self):
        result = ledger_history([{'key': 'missing', 'path': '/missing/file.json'}])
        self.assertEqual(result['coverage'][0]['status'], 'UNAVAILABLE')
        self.assertEqual(result['rows'], [])

    def test_retirement_preserves_native_proceeds_and_unknown_pnl(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'retired.json'
            path.write_text(json.dumps({'desks': {'A': {'retirement_sales': [{
                'sym': 'ETH', 'units': 2, 'exit_bid_aud': 100,
                'net_proceeds_aud': 199, 'fee_estimate_aud': 1,
                'pnl': None, 'pnl_verified': False,
                'ticket_id': 'retirement-test'}]}}}))
            result = ledger_history([{'key': 'coinspot_floor', 'path': str(path),
                                      'ccy': 'AUD', 'retired': True}])
            self.assertEqual(len(result['rows']), 1)
            row = result['rows'][0]
            self.assertIsNone(row['recorded_pnl'])
            self.assertEqual(row['pnl_currency'], 'AUD')
            self.assertEqual(row['recorded_proceeds'], 199)
            self.assertEqual(row['recorded_fees'], 1)
            self.assertEqual(row['price'], 100)
            self.assertEqual(result['coverage'][0]['status'], 'RETIRED / ARCHIVED')

    def test_position_entry_requires_matching_units(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'book.json'
            path.write_text(json.dumps({'positions': {'A': {'units': 2, 'entry_px_usd': 50}}}))
            books = [{'key': 'b', 'path': str(path), 'ccy': 'AUD'}]
            rows = position_rows([{'book': 'b', 'sym': 'A', 'units': 2}, {'book': 'b', 'sym': 'A', 'units': 3}], books)
            self.assertEqual(rows[0]['entry'], 50)
            self.assertEqual(rows[0]['entry_currency'], 'USD')
            self.assertNotIn('entry', rows[1])

    def test_position_exit_fills_are_included(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'book.json'
            path.write_text(json.dumps({'positions': {'A': {'exit_fills': [{'utc': '2026-09-24T01:00:00Z', 'px_usd': 50, 'realised_usd': 2}]}}}))
            rows = ledger_history([{'key': 'b', 'path': str(path), 'ccy': 'USD'}])['rows']
            self.assertEqual(rows[0]['symbol'], 'A')
            self.assertEqual(rows[0]['price'], 50)
            self.assertEqual(rows[0]['recorded_pnl'], 2)
