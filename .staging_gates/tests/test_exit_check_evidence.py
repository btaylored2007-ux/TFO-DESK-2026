import copy
import datetime as dt
import unittest
from unittest.mock import patch
import exit_check_evidence as e


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.p = {'units': 2, 'entry': 10, 'side': 'long'}
        self.d = {'positions': {'X': self.p}}
        e.start(self.d, 'test')

    def test_hold_requires_matching_current_position(self):
        e.record(self.d, 'X', self.p, 'HOLD', 'No trigger')
        e.finish(self.d)
        self.assertTrue(e.assess(self.d)['coverage_verified'])
        self.p['units'] = 3
        self.assertFalse(e.assess(self.d)['coverage_verified'])

    def test_wait_market_requires_actual_source_evidence(self):
        e.record(self.d, 'X', self.p, 'WAIT_MARKET', 'Closed')
        e.finish(self.d)
        self.assertFalse(e.assess(self.d)['coverage_verified'])
        self.d['exit_checks']['legs']['X']['quote'] = {
            'mark_quote_utc': e.utc(), 'mark_session': 'closed'}
        self.assertTrue(e.assess(self.d)['coverage_verified'])

    def test_missing_and_old_checks_fail(self):
        self.assertFalse(e.assess(self.d)['coverage_verified'])
        e.record(self.d, 'X', self.p, 'HOLD', 'No trigger')
        e.finish(self.d)
        self.assertFalse(e.assess(self.d, now=0)['coverage_verified'])
        self.assertFalse(e.assess(self.d, now=9999999999)['coverage_verified'])

    def test_wait_data_and_unapplied_exit_fail(self):
        for status in ('WAIT_DATA', 'EXIT'):
            e.record(self.d, 'X', self.p, status, 'No disposition')
            e.finish(self.d)
            self.assertFalse(e.assess(self.d)['coverage_verified'])

    def test_closed_leg_not_required_but_new_leg_is(self):
        self.d['positions'] = {}
        self.assertTrue(e.assess(self.d)['coverage_verified'])
        self.d['desks'] = {'A': {'positions': {'X': self.p}}}
        self.assertFalse(e.assess(self.d)['coverage_verified'])

    def test_daily_bars_reject_bad_timestamps_and_prices(self):
        self.assertTrue(e.current_bar([(100, 1, 2, 1, 2)], 1440, now=200))
        for row in ((201, 1, 2, 1, 2), (-90000, 1, 2, 1, 2),
                    (100, 1, 2, 1, float('nan')), (100, 1, 2, 1, -1)):
            self.assertFalse(e.current_bar([row], 1440, now=200))


class UndeployedTests(unittest.TestCase):
    def test_empty_undeployed_is_noop_but_orphan_holding_fails(self):
        import profit_gate_sleeve as p
        for ledger, expected in (({'positions': {}}, 0),
                                 ({'positions': {'X': {'sleeve': p.SLEEVE}}}, 2)):
            import head_trader_gate  # 17 Oct 2026: isolate from the LIVE head-trader flag file
            with patch.object(p, '_load_json', side_effect=[ledger, {}]), \
                    patch.object(head_trader_gate, 'active', return_value=False), \
                    patch.object(p, 'fetch_prices') as fetch:
                self.assertEqual(p._tick_locked(False), expected)
                fetch.assert_not_called()
