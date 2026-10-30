import copy
import sys
import time
import unittest
from contextlib import ExitStack
from unittest.mock import patch

sys.path.insert(0, '/Users/test/Applications/KrakenQuantDesk')
import kraken_floor as k
import replica_floor as r
import cross_asset_floor as c
import convexity_sleeve as v
import cash_yield_books
import exit_check_evidence as evidence


class NativeEvidenceTests(unittest.TestCase):
    def floor(self, module, signal=None, missing=False):
        p = {'units': 2, 'entry': 10, 'cost': 20, 'side': 'long',
             'entry_epoch': time.time()-100, 'instrument_class': 'crypto'}
        ledger = {'start_cash': 100, 'desks': {'Test': {
            'cash': 80, 'realized_pnl': 0, 'positions': {'XBT/USD': p}, 'closed_trades': []}}}
        bars = [] if missing else [(time.time()-100, 10, 11, 9, 10)]*60
        with ExitStack() as stack:
            for name, value in [('load', ledger), ('save', None), ('kill_check', None),
                                ('kill_entries_allowed', (False, 'test freeze')),
                                ('fetch_ohlc', bars), ('exit_signal', signal)]:
                stack.enter_context(patch.object(module, name, return_value=value))
            stack.enter_context(patch.object(module, 'DESKS', {'Test': {}}))
            stack.enter_context(patch.object(module, '_liq', None))
            stack.enter_context(patch.object(module, '_kill', None))
            stack.enter_context(patch.object(module.time, 'sleep'))
            stack.enter_context(patch.object(cash_yield_books, 'accrue_book'))
            if module is r:
                stack.enter_context(patch.object(r, 'venue_filter', return_value=([], {})))
                stack.enter_context(patch.object(r, 'log_equity'))
            module._tick_locked(exits_only=True)
        return ledger

    def test_held_only_floor_checks_hold_under_entry_freeze(self):
        for module in (k, r):
            d = self.floor(module)
            self.assertTrue(evidence.assess(d)['coverage_verified'])
            self.assertEqual(d['exit_checks']['legs']['Test/XBT/USD']['status'], 'HOLD')

    def test_missing_bars_are_not_hold(self):
        for module in (k, r):
            d = self.floor(module, missing=True)
            self.assertFalse(evidence.assess(d)['coverage_verified'])

    def test_floor_exit_persists_unique_ticket_and_cash_identity(self):
        for module in (k, r):
            d = self.floor(module, signal='stop')
            desk = d['desks']['Test']
            self.assertEqual(desk['positions'], {})
            ticket = d['exit_checks']['legs']['Test/XBT/USD']['ticket']
            self.assertEqual(ticket['ticket_id'], desk['closed_trades'][0]['ticket_id'])
            self.assertAlmostEqual(desk['cash'], 80 + ticket['proceeds_usd'])

    def test_convexity_wait_preserves_quote_and_never_exits(self):
        d = {'cash': 0, 'positions': {'X': {'units': 2, 'premium_usd': 20,
                                         'mark_usd': 10, 'thesis_dead': True}}}
        def quote(sym, position, meta):
            meta['quote'] = {'quote_utc': evidence.utc(), 'session': 'closed', 'executable': False}
            return None, 'Closed'
        with patch.object(v, 'load', return_value=d), patch.object(v, 'save'), \
                patch.object(v, '_live_mark', side_effect=quote):
            self.assertEqual(v.mark_and_manage(False), [])
        self.assertTrue(evidence.assess(d)['coverage_verified'])
        self.assertEqual(d['exit_checks']['legs']['X']['status'], 'WAIT_MARKET')

    def test_cross_asset_hold_retains_instrument_and_bar(self):
        p = {'units': 2, 'entry': 10, 'cost': 20, 'side': 'long', 'entry_epoch': time.time()}
        d = {'start_cash': 100, 'desks': {'Test': {'cash': 80, 'realized_pnl': 0,
             'positions': {'gold': p}, 'closed_trades': []}}}
        def ohlc(inst, source, meta):
            meta.update(bar_epoch=time.time()-100, interval_minutes=1440, instrument=inst, source=source)
            return [11]*30, [9]*30, [10]*30
        with ExitStack() as stack:
            for name, value in [('load', d), ('save', None), ('kill_check', None),
                                ('kill_entries_allowed', (False, 'frozen')), ('daily_vol', .01),
                                ('quote', (10, 10.1)), ('exit_signal', None)]:
                stack.enter_context(patch.object(c, name, return_value=value))
            stack.enter_context(patch.object(c, 'ohlc', side_effect=ohlc))
            stack.enter_context(patch.object(c, 'DESKS', {'Test': {'stop': 8}}))
            stack.enter_context(patch.object(c, 'ACTIVE_DESKS', ['Test']))
            stack.enter_context(patch.object(c, 'UNIVERSE', {'gold': ('XAUTUSD', 'spot', 'metal', '')}))
            stack.enter_context(patch.object(c, '_kill', None))
            stack.enter_context(patch.object(c.time, 'sleep'))
            stack.enter_context(patch.object(cash_yield_books, 'accrue_book'))
            c._tick_locked(100, exits_only=True)
        self.assertTrue(evidence.assess(d)['coverage_verified'])
        self.assertEqual(d['exit_checks']['legs']['Test/gold']['instrument'], 'XAUTUSD')
