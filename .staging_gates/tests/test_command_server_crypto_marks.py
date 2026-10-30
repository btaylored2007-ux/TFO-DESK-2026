"""Read-only quote freshness regressions; no network or ledger mutations."""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import command_server as server
import book_coverage

class CryptoMarkTests(unittest.TestCase):
    def setUp(self):
        self.cache = patch.object(server, '_PX_CACHE', {'crypto': [0, {}], 'equity': [0, {}]})
        self.cache.start()
        self.remember = patch.object(server, '_remember')
        self.remember.start()
    def tearDown(self):
        self.remember.stop()
        self.cache.stop()
    def quote(self, symbols, when, payload):
        response = io.StringIO(json.dumps(payload))
        with patch('time.time', return_value=when), patch.object(server.urllib.request, 'urlopen', return_value=response):
            return server.crypto_marks(symbols)
    def test_other_symbol_refresh_cannot_make_old_quote_live(self):
        self.assertEqual(self.quote(['UNI/USD'], 100, {'result': {'UNIUSD': {'c': ['8']}}}), {'UNI/USD': 8})
        self.assertEqual(self.quote(['AAVE/USD'], 116, {'result': {'AAVEUSD': {'c': ['140']}}}), {'AAVE/USD': 140})
        self.assertEqual(self.quote(['UNI/USD'], 117, {'error': ['feed unavailable']}), {})
    def test_exact_pair_matching_and_kraken_aliases(self):
        payload = {'result': {'UNIUSDT': {'c': ['999']}, 'UNIUSD': {'c': ['8']},
                              'XXDGZUSD': {'c': ['0.1']}, 'XXBTZUSD': {'c': ['80000']}}}
        self.assertEqual(self.quote(['UNI/USD', 'DOGE/USD', 'BTC/USD'], 100, payload),
                         {'UNI/USD': 8, 'DOGE/USD': .1, 'BTC/USD': 80000})
        self.assertEqual(self.quote(['AAVE/USD'], 120, {'result': {'AAVEUSDT': {'c': ['999']}}}), {})
    def test_partial_and_invalid_responses_never_return_old_marks(self):
        self.quote(['UNI/USD'], 100, {'result': {'UNIUSD': {'c': ['8']}}})
        payload = {'result': {'UNIUSD': {'c': ['NaN']}, 'AAVEUSD': {'c': ['0']}, 'SUIUSD': {'c': ['1']}}}
        self.assertEqual(self.quote(['UNI/USD', 'AAVE/USD', 'SUI/USD'], 120, payload), {'SUI/USD': 1})
    def test_unexpired_requested_quote_uses_cache_only(self):
        self.quote(['UNI/USD'], 100, {'result': {'UNIUSD': {'c': ['8']}}})
        with patch('time.time', return_value=110), patch.object(server.urllib.request, 'urlopen') as fetch:
            self.assertEqual(server.crypto_marks(['UNI/USD']), {'UNI/USD': 8})
            fetch.assert_not_called()
    def test_failed_live_feed_uses_canonical_ledger_with_visible_age(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory); (root/'reports').mkdir()
            path = root/'reports/crypto_uni_sui_rs.json'
            path.write_text(json.dumps({'equity': 30715.62, 'cash': 25, 'holdings': {'UNI/USD': 10},
                'deploy_basis_usd': 30885.59, 'seed_usd': 29385.37, 'status': 'OPEN',
                'last_mark_utc': '2026-01-01T00:00:00Z', 'last_mark': {'usd': 99999}}))
            stack.enter_context(patch.object(server, 'BASE', str(root)))
            stack.enter_context(patch('importlib.reload', side_effect=lambda module: module))
            stack.enter_context(patch.object(book_coverage, 'registry', return_value=[{
                'key': 'crypto_uni_sui_rs', 'path': str(path), 'equity_usd': 29064.94, 'start_usd': 29385.37}]))
            stack.enter_context(patch.object(server, 'crypto_marks', return_value={}))
            stack.enter_context(patch.object(server, '_coo_mandate_block', return_value={}))
            stack.enter_context(patch.object(server, '_sol_short_pilot_block', return_value={'readable': False}))
            result = server._tfo_hero_block()['crypto_sleeves'][0]
            self.assertEqual(result['equity_usd'], 30715.62)
            self.assertEqual(result['pnl_pct'], -.55)
            self.assertEqual(result['mark_source'], 'Desk check')
            self.assertTrue(result['stale'])

if __name__ == '__main__':
    unittest.main()
