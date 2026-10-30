import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tfo_floor_price_refresh as refresh


NOW = 1_789_987_000.0


def quote(symbol='XBT/USD', currency='USD', venue='kraken', **kw):
    return {'symbol': symbol, 'currency': currency, 'venue': venue,
            'bid': 109., 'ask': 111., 'last': 110., 'retrieved_epoch': NOW,
            'retrieved_at': refresh.utc(NOW), 'source': 'public-test', **kw}


def ledger(symbol='XBT/USD', side='long'):
    return {'desks': {'Breakout': {'cash': 50., 'realized_pnl': -3.,
                                   'unrealized': 7., 'positions': {
        symbol: {'side': side, 'entry': 100., 'units': 2., 'cost': 200.,
                 'entry_epoch': NOW - 86400, 'entry_fee': .5,
                 'entry_cost': 1., 'mark': 103.5, 'upnl': 7.}}}},
        'book_risk': {'equity': 257., 'frozen': True, 'peak_equity': 300.,
                      'checked_utc': 'previous-risk-check', 'drawdown_pct': -14.3},
        'frozen': False, 'pending_order': {'keep': True}, 'capital_events': [{'amount': 50.}]}


class FloorRefreshTests(unittest.TestCase):
    def test_unavailable_screen_nan_is_null_without_financial_changes(self):
        src=ledger('btc');src['screen']={'spreads_bps':{'rfox':float('nan')}}
        dst,rows=refresh.mark_book('coinspot_floor',src,
            {('coinspot','btc'):quote('btc','AUD','coinspot')},NOW)
        self.assertIsNone(dst['screen']['spreads_bps']['rfox'])
        self.assertEqual(dst['desks']['Breakout']['positions']['btc']['entry'],100.)
        self.assertEqual(dst['desks']['Breakout']['cash'],50.)
        json.dumps(dst,allow_nan=False)

    def test_kraken_long_short_preserve_historical_finances_and_risk(self):
        for side, expected in [('long', 270.), ('short', 230.)]:
            src = ledger(side=side)
            saved = copy.deepcopy(src)
            dst, rows = refresh.mark_book('kraken_floor', src,
                                          {('kraken', 'XBT/USD'): quote()}, NOW)
            self.assertEqual(src, saved)
            self.assertAlmostEqual(dst['equity'], expected)
            self.assertEqual(dst['book_risk']['equity'], expected)
            self.assertTrue(dst['book_risk']['frozen'])
            self.assertFalse(dst['frozen'])
            self.assertEqual(dst['book_risk']['checked_utc'], 'previous-risk-check')
            self.assertEqual(dst['pending_order'], src['pending_order'])
            self.assertEqual(dst['capital_events'], src['capital_events'])
            for key in ['side', 'entry', 'entry_epoch', 'entry_fee', 'entry_cost', 'units', 'cost']:
                self.assertEqual(dst['desks']['Breakout']['positions']['XBT/USD'][key],
                                 src['desks']['Breakout']['positions']['XBT/USD'][key])

    def test_coinspot_native_aud_bid_and_cross_asset_short_ask(self):
        dst, rows = refresh.mark_book('coinspot_floor', ledger('btc'),
                                      {('coinspot', 'btc'): quote('btc', 'AUD', 'coinspot')}, NOW)
        self.assertAlmostEqual(dst['equity'], 268.)
        self.assertEqual(rows[0]['mark'], 109.)
        dst, rows = refresh.mark_book('cross_asset_floor', ledger('gold', 'short'),
                                      {('kraken', 'XAUT/USD'): quote('XAUT/USD')}, NOW)
        self.assertAlmostEqual(dst['equity'], 228.)
        self.assertEqual(rows[0]['mark'], 111.)

    def test_replica_uses_registered_cost_model_without_debiting_cash(self):
        src = ledger()
        with patch('venue_costs.financing', return_value=2.), patch('venue_costs.exit_cost', return_value=3.):
            dst, _ = refresh.mark_book('replica_floor', src,
                                       {('kraken', 'XBT/USD'): quote()}, NOW)
        self.assertEqual(dst['equity'], 264.)
        self.assertEqual(dst['desks']['Breakout']['cash'], 50.)
        pos = dst['desks']['Breakout']['positions']['XBT/USD']
        self.assertEqual(pos['gross_upnl'], 20.)
        self.assertEqual(pos['upnl'], 14.)
        self.assertEqual(pos['entry_cost'], 1.)

    def test_missing_bad_and_wrong_currency_quotes_never_mutate_book(self):
        cases = [{}, {('kraken', 'XBT/USD'): quote(currency='AUD')},
                 {('kraken', 'XBT/USD'): quote(retrieved_epoch=NOW - 121)},
                 {('kraken', 'XBT/USD'): quote(retrieved_epoch=NOW + 1)},
                 {('kraken', 'XBT/USD'): quote(bid=112.)},
                 {('kraken', 'XBT/USD'): quote(last=float('nan'))}]
        for quotes in cases:
            src = ledger()
            saved = copy.deepcopy(src)
            with self.assertRaises((ValueError, KeyError)):
                refresh.mark_book('kraken_floor', src, quotes, NOW)
            self.assertEqual(src, saved)
            self.assertEqual(src['desks']['Breakout']['unrealized'], 7.)

    def test_partial_quote_failure_keeps_other_position_and_total_unchanged(self):
        src = ledger()
        src['desks']['Breakout']['positions']['ETH/USD'] = copy.deepcopy(
            src['desks']['Breakout']['positions']['XBT/USD'])
        saved = copy.deepcopy(src)
        with self.assertRaises(KeyError):
            refresh.mark_book('kraken_floor', src, {('kraken', 'XBT/USD'): quote()}, NOW)
        self.assertEqual(src, saved)

    def test_future_entry_fails_without_rewriting_history(self):
        src = ledger()
        src['desks']['Breakout']['positions']['XBT/USD']['entry_epoch'] = NOW + 60
        with self.assertRaisesRegex(ValueError, 'future historical entry'):
            refresh.mark_book('kraken_floor', src, {('kraken', 'XBT/USD'): quote()}, NOW)

    def test_concurrent_write_is_not_clobbered_and_lock_not_stolen(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'ledger.json'
            path.write_text('{"equity": 50}')
            before = refresh.digest(path.read_bytes())
            path.write_text('{"equity": 60}')
            with self.assertRaisesRegex(RuntimeError, 'concurrent writer'):
                refresh.atomic_json(path, {'equity': 99}, before)
            self.assertEqual(json.loads(path.read_text()), {'equity': 60})
            lock = Path(td) / '.tick.lock'
            with refresh.tick_lock(lock):
                with self.assertRaises(FileExistsError):
                    with refresh.tick_lock(lock):
                        self.fail('stole lock')
            self.assertFalse(lock.exists())

    def test_exact_pair_response_no_fallback_to_other_market(self):
        def fetch(url):
            if 'AssetPairs' in url:
                return {'result': {'XXBTZUSD': {'wsname': 'XBT/USD'},
                                   'XXBTZAUD': {'wsname': 'XBT/AUD'}}}
            return {'result': {'XXBTZAUD': {'a': ['111'], 'b': ['109'], 'c': ['110']}}}
        quotes, errors = refresh.fetch_quotes({'XBT/USD'}, set(), fetch)
        self.assertEqual(quotes, {})
        self.assertTrue(errors)

    def test_flat_inventory_does_not_treat_weight_holdings_as_flat(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / 'reports').mkdir()
            (base / 'reports/value_book.json').write_text(json.dumps({'holdings': {'SPY': 2.}}))
            (base / 'reports/crypto_mid_clip_book.json').write_text(json.dumps({'holdings': {}}))
            (base / 'reports/crypto_sol_short_pilot.json').write_text(json.dumps({'quantity': 3.}))
            rows = refresh.flat_books(base, base)
            self.assertEqual([Path(row['path']).name for row in rows], ['crypto_mid_clip_book.json'])

    def test_rollback_preserves_all_four_exact_source_bytes_with_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            snapshots = {key: ('{ "book": "' + key + '" }\n').encode()
                         for key in refresh.FLOORS}
            paths = {key: Path('/external/source') / (key + '.json') for key in snapshots}
            backup = refresh.create_rollback_backup(snapshots, paths, base)
            receipt = json.loads((backup / 'receipt.json').read_text())
            self.assertEqual(len(receipt['files']), 4)
            self.assertFalse(receipt['ledger_writes_started'])
            for item in receipt['files']:
                self.assertEqual(Path(item['backup_path']).read_bytes(), snapshots[item['book']])
                self.assertEqual(item['source_path'], str(paths[item['book']]))
                self.assertEqual(item['sha256'], refresh.digest(snapshots[item['book']]))


if __name__ == '__main__':
    unittest.main()
