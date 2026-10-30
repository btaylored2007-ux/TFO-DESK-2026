"""Viewer accounting regressions: signed short P&L and funding invariance."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch
from contextlib import ExitStack

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import command_server as server


class ShortViewerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'reports').mkdir()
        self.base = patch.object(server, 'BASE', str(self.root))
        self.base.start()
        # 17 Oct 2026: isolate from LIVE archived-book receipts (book_archive.retained_basis_usd reads production receipts, -27,809.04 today)
        import book_archive
        self._arch = patch.object(book_archive, 'retained_basis_usd', return_value=0.0); self._arch.start(); self.addCleanup(self._arch.stop)
        self.ledger = dict(paper_only=True, side='short', symbol='PF_SOLUSD',
            quantity=1000., entry_price=100., mark_price=99., cash=50.,
            collateral_usd=100000., equity=100990.50, seed_usd=100100., start=100100.,
            deploy_basis_usd=100100., unrealised_usd=1000., realised_usd=0.,
            costs_paid_usd=50., funding_accrued_usd=-10., funding_total_usd=-10.,
            realised_funding_usd=0., fee_bps=5., estimated_exit_fee_usd=49.50,
            mark_utc=datetime.now(timezone.utc).isoformat(), status='OPEN',
            fills=[{'private_journal': 'must not be in sanitized view'}],
            capital_events=[{'not_public': 'must not be exposed'}])
        self.write()

    def tearDown(self):
        self.base.stop()
        self.tmp.cleanup()

    def write(self):
        (self.root/'reports/crypto_sol_short_pilot.json').write_text(json.dumps(self.ledger))

    def test_short_price_profit_separate_from_cost_net_profit(self):
        block = server._sol_short_pilot_block()
        self.assertTrue(block['readable'], block)
        self.assertAlmostEqual(block['net_pnl_usd'], 890.50)
        row = server._sol_short_position_row(block)
        self.assertEqual(row['side'], 'short')
        self.assertEqual(row['symbol'], 'PF_SOLUSD')
        self.assertEqual(row['units'], 1000)
        self.assertEqual(row['value'], 99000)
        self.assertEqual(row['upnl'], 1000)
        self.assertEqual(row['upnl_pct'], 1)
        self.assertAlmostEqual(row['net_pnl_usd'], 890.50)
        self.assertFalse(row['verified_account_access'])
        self.assertTrue(row['simulated_only'])
        self.assertNotIn('fills', block)
        self.assertNotIn('capital_events', block)

    def test_short_loss_direction(self):
        self.ledger.update(mark_price=101., unrealised_usd=-1000., estimated_exit_fee_usd=50.5,
                           equity=98989.5)
        self.write()
        row = server._sol_short_position_row(server._sol_short_pilot_block())
        self.assertEqual(row['upnl'], -1000)
        self.assertEqual(row['upnl_pct'], -1)
        self.assertAlmostEqual(row['net_pnl_usd'], -1110.5)

    def test_funding_transfer_cannot_create_profit(self):
        before = server._sol_short_pilot_block()['net_pnl_usd']
        for name in ('cash', 'equity', 'seed_usd', 'start'):
            self.ledger[name] += 500
        self.write()
        after = server._sol_short_pilot_block()
        self.assertTrue(after['readable'], after)
        self.assertEqual(before, after['net_pnl_usd'])

    def test_inconsistent_ledger_is_visible_not_zero(self):
        self.ledger['equity'] += 1
        self.write()
        block = server._sol_short_pilot_block()
        self.assertTrue(block['available'])
        self.assertFalse(block['readable'])
        self.assertIn('reconcile', block['error'])
        self.assertIsNone(block['equity_usd'])
        self.assertIsNone(block['net_pnl_usd'])
        self.assertIsNone(server._sol_short_position_row(block))

    def test_stale_mark_is_preserved_and_labelled(self):
        self.ledger['mark_utc'] = '2026-01-01T00:00:00Z'
        self.write()
        block = server._sol_short_pilot_block()
        self.assertTrue(block['readable'])
        self.assertTrue(block['stale'])
        row = server._sol_short_position_row(block)
        self.assertGreater(row['mark_age_sec'], 1800)
        self.assertTrue(row['src'].startswith('ledger'))

    def test_all_funding_includes_negative_bank_residual(self):
        risk = {'capital_accounting_valid': True}
        before = [{'key': 'powder', 'start_usd': 10000}, {'key': 'clip', 'start_usd': 5000}]
        after = [{'key': 'powder', 'start_usd': 17000}, {'key': 'clip', 'start_usd': -2000}]
        self.assertEqual(server._firm_funding_block(before, risk)['total_usd'], 15000)
        self.assertEqual(server._firm_funding_block(after, risk)['total_usd'], 15000)

    def test_missing_invalid_or_unreadable_funding_is_unverified(self):
        risk = {'capital_accounting_valid': True}
        for value in (None, True, float('nan'), float('inf'), '100'):
            with self.subTest(value=value):
                result = server._firm_funding_block([{'key': 'bad', 'start_usd': value}], risk)
                self.assertFalse(result['complete'])
                self.assertIsNone(result['total_usd'])
                self.assertTrue(result['errors'])
        result = server._firm_funding_block([{'key': 'bad', 'start_usd': 1, 'error': 'unreadable'}], risk)
        self.assertFalse(result['complete'])
        result = server._firm_funding_block([{'key': 'good', 'start_usd': 0}], {})
        self.assertFalse(result['complete'])
        result = server._firm_funding_block([
            {'key': 'a', 'start_usd': 1e308}, {'key': 'b', 'start_usd': 1e308}], risk)
        self.assertFalse(result['complete'])
        self.assertIsNone(result['total_usd'])
        self.assertIn('nonfinite', '; '.join(result['errors']))

    def test_positions_payload_includes_short_and_cost_net_desk_once(self):
        import book_coverage
        def read_fixture(path, fallback):
            if Path(path).name == 'firm_risk_state.json':
                return {'capital_accounting_valid': True, 'firm_equity_usd': 100990.5}
            return fallback
        fx = {'rate': 1, 'status': 'test', 'source': 'fixture', 'age_human': '0s', 'note': 'test'}
        with ExitStack() as stack:
            stack.enter_context(patch.object(server, '_json_file', side_effect=read_fixture))
            stack.enter_context(patch.object(server, 'engine_state', return_value={'_unreachable': True}))
            stack.enter_context(patch.object(server, 'fx_audusd', return_value=fx))
            stack.enter_context(patch.object(server, 'quote_feed_status', return_value={'ok': True}))
            stack.enter_context(patch.object(server, '_liquidity_volmap', return_value={}))
            stack.enter_context(patch.object(server, 'capital_base',
                side_effect=AssertionError('legacy capital must never feed complete firm return')))
            for helper in ('_rally_rider_block', '_capacity_probe_block', '_punt_block', '_fundamental_block'):
                stack.enter_context(patch.object(server, helper, return_value={}))
            stack.enter_context(patch('importlib.reload', side_effect=lambda module: module))
            stack.enter_context(patch.object(book_coverage, 'registry', return_value=[
                {'key': 'pilot', 'start_usd': 100100}]))
            payload = server.positions_payload()
        short_rows = [r for r in payload['rows'] if r['symbol'] == 'PF_SOLUSD']
        self.assertEqual(len(short_rows), 1)
        short_desks = [r for r in payload['pnl_by_desk'] if r['floor'].startswith('SOL short')]
        self.assertEqual(len(short_desks), 1)
        self.assertEqual(short_desks[0]['realized'], -50)
        self.assertEqual(short_desks[0]['unrealized'], 940.5)
        self.assertEqual(short_desks[0]['total'], 890.5)
        self.assertEqual(short_desks[0]['open'], 1)
        self.assertTrue(payload['pnl']['firm_funding_complete'])
        self.assertEqual(payload['pnl']['firm_vs_seed_usd'], 890.5)
        self.assertEqual(payload['capital']['total_usd'], 100100)
        self.assertEqual(payload['pnl']['capital_usd'], 100100)
        self.assertEqual(payload['capital']['n_books'], 1)
        self.assertEqual(payload['capital']['components'][0]['book'], 'pilot')
        self.assertIsNone(payload['pnl']['return_on_capital_pct'])
        self.assertIn('desk-sum', payload['pnl']['return_unavailable'])
        self.assertEqual(payload['pnl']['firm_return_on_capital_pct'], round(890.5/100100*100, 3))
        self.assertEqual(payload['coverage_errors'], [])


if __name__ == '__main__':
    unittest.main()
