import copy
import hashlib
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import private_wealth_monitor as pw


class PrivateWealthMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.reports = self.base / 'reports'
        self.reports.mkdir()
        self.now = datetime(2026, 9, 24, 7, 0, tzinfo=timezone.utc)

    def quote(self, seconds=0, kind='LIVE'):
        return {'price': 100, 'utc': pw.stamp(self.now - timedelta(seconds=seconds)),
                'class': kind, 'venue': 'Kraken'}

    def receipt(self, ident, seconds=0):
        return {'cycle_id': ident, 'utc': pw.stamp(self.now + timedelta(seconds=seconds)),
                'paper_only': True, 'paper_tickets': [], 'risk': {}, 'tape': {},
                'rails': [], 'reconciliation_defect': {'status': 'FAIL'}}

    def test_freshness_boundaries_and_clock_error(self):
        for seconds, status in ((1199, 'FRESH'), (1200, 'LAG'), (21600, 'STALE'), (-10, 'CLOCK ERROR')):
            self.assertEqual(pw.freshness(pw.stamp(self.now-timedelta(seconds=seconds)), self.now)['status'], status)
        self.assertEqual(pw.freshness(None, self.now)['status'], 'UNKNOWN')

    def test_local_only_retires_source_without_granting_readiness(self):
        (self.reports / 'private_wealth_operating_mode.json').write_text('{"mode":"LOCAL_ONLY"}')
        (self.reports / 'private_wealth_cycle_latest.json').write_text(json.dumps({
            'new_risk_ready': True, 'controlling_snapshot': {'firm_equity_usd': 999},
            'reconciliation_defect': {'status': 'FAIL', 'reason': 'external mismatch'}}))
        with patch.object(pw, 'reconcile', side_effect=AssertionError('Must not read retired source')):
            result = pw.build(self.base, self.now)
        self.assertIsNone(result['source_books'])
        self.assertEqual(result['historical_positions'], [])
        self.assertEqual(result['cycle'], {})
        self.assertFalse(result['new_risk_ready'])
        self.assertNotIn('SOURCE_ACCOUNTING_GAP', [x['code'] for x in result['alerts']])
        self.assertIn('LOCAL_ACCOUNTING', [x['code'] for x in result['alerts']])

    def test_cadence_only_fresh_live_regular_clean_rails(self):
        rail = {'symbol': 'ETH/USD', 'fill': 100, 'stop': 99.6, 'current_position_verified': True}
        self.assertEqual(pw.cadence([rail], {'ETH/USD': self.quote()}, self.now)['minutes'], 5)
        for quote in (self.quote(60), self.quote(0, 'DELAYED'), self.quote(-20)):
            self.assertEqual(pw.cadence([rail], {'ETH/USD': quote}, self.now)['minutes'], 15)
        for field in ('closed', 'dirty'):
            self.assertEqual(pw.cadence([{**rail, field: True}], {'ETH/USD': self.quote()}, self.now)['minutes'], 15)
        quote = {**self.quote(), 'venue': 'NYSE', 'session': 'AFTER_HOURS'}
        self.assertEqual(pw.cadence([rail], {'ETH/USD': quote}, self.now)['minutes'], 15)
        quote['session'] = 'REGULAR'
        self.assertEqual(pw.cadence([rail], {'ETH/USD': quote}, self.now)['minutes'], 5)
        self.assertEqual(pw.cadence([{**rail, 'fill': 1}], {'ETH/USD': self.quote()}, self.now)['minutes'], 15)
        self.assertEqual(pw.cadence([{**rail, 'current_position_verified': False}], {'ETH/USD': self.quote()}, self.now)['minutes'], 15)

    def test_already_banked_half_does_not_accelerate(self):
        rail = {'symbol': 'ETH/USD', 'fill': 100, 'stop': 90, 'half': 100, 'half_done': True, 'current_position_verified': True}
        self.assertEqual(pw.cadence([rail], {'ETH/USD': self.quote()}, self.now)['minutes'], 15)

    def test_claim_exclusion_recovery_and_idempotent_completion(self):
        first = pw.begin(self.base, self.now)
        self.assertTrue(first['run'])
        self.assertEqual(pw.begin(self.base, self.now)['reason'], 'BUSY')
        later = self.now + timedelta(minutes=11)
        self.assertEqual(pw.begin(self.base, later)['reason'], 'RECOVERY_REQUIRED')
        receipt = self.receipt(first['cycle_id'], 660)
        with self.assertRaisesRegex(ValueError, 'recovery_evidence'):
            pw.complete(receipt, self.base, later)
        receipt['recovery_evidence'] = 'Fixture: no financial writes, checked ticket IDs'
        self.assertEqual(pw.complete(receipt, self.base, later)['status'], 'RECORDED')
        self.assertEqual(pw.complete(receipt, self.base, later)['status'], 'ALREADY_RECORDED')
        receipt['risk'] = {'changed': True}
        with self.assertRaisesRegex(ValueError, 'different contents'):
            pw.complete(receipt, self.base, later)
        self.assertEqual(pw.begin(self.base, later)['reason'], 'NOT_DUE')

    def test_crash_after_archive_is_recoverable_without_new_cycle(self):
        claim = pw.begin(self.base, self.now)
        receipt = self.receipt(claim['cycle_id'])
        receipt['started_utc'] = claim['started_utc']
        pw.atomic(self.reports / 'private_wealth_cycles' / (claim['cycle_id']+'.json'), receipt)
        self.assertEqual(pw.complete(receipt, self.base, self.now)['status'], 'ALREADY_RECORDED')
        self.assertIsNone(pw.load(self.reports/'private_wealth_gate.json')['active'])
        self.assertEqual(pw.load(self.reports/'private_wealth_cycle_latest.json'), receipt)

    def test_malformed_receipt_does_not_release_claim(self):
        claim = pw.begin(self.base, self.now)
        receipt = self.receipt(claim['cycle_id'])
        del receipt['risk']
        with self.assertRaises(ValueError):
            pw.complete(receipt, self.base, self.now)
        self.assertEqual(pw.begin(self.base, self.now)['reason'], 'BUSY')

    def test_prior_cycle_start_is_rejected_without_releasing_claim(self):
        claim = pw.begin(self.base, self.now)
        receipt = self.receipt(claim['cycle_id'])
        receipt['started_utc'] = pw.stamp(self.now - timedelta(minutes=30))
        with self.assertRaisesRegex(ValueError, 'active claimed start'):
            pw.complete(receipt, self.base, self.now)
        self.assertEqual(pw.begin(self.base, self.now)['reason'], 'BUSY')
        receipt['started_utc'] = claim['started_utc']
        pw.complete(receipt, self.base, self.now)
        self.assertEqual(pw.build(self.base, self.now)['cadence']['anchor_utc'], claim['started_utc'])

    def test_timing_correction_requires_matching_immutable_archive(self):
        ident = '81fdafb8-e60b-4af8-b56a-6c70c8ef5c7e'
        receipt = self.receipt(ident)
        receipt['started_utc'] = pw.stamp(self.now - timedelta(minutes=30))
        archive = self.reports / 'private_wealth_cycles' / (ident+'.json')
        pw.atomic(archive, receipt)
        correction = {ident: {'original_started_utc': receipt['started_utc'],
                             'claimed_started_utc': pw.stamp(self.now), 'evidence': 'Test claim',
                             'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}}
        pw.atomic(self.reports/'private_wealth_timing_corrections.json', correction)
        self.assertEqual(pw.cycle_start(receipt, self.reports), self.now)
        archive.write_text(archive.read_text()+' ')
        self.assertEqual(pw.cycle_start(receipt, self.reports), pw.parsed(receipt['started_utc']))

    # 2026-09-28: exit-coverage health scores V1 books only; separate desks become neutral notes.
    def sleeves_cycle(self, status, remaining, non_v1=None):
        data = {'status': status, 'remaining_exit_routes': remaining}
        if non_v1 is not None:
            data['non_v1_exit_routes'] = non_v1
        (self.reports / 'private_wealth_operating_mode.json').write_text('{"mode":"LOCAL_ONLY"}')
        (self.reports / 'private_wealth_cycle_latest.json').write_text(json.dumps({
            'desk_scope': 'LOCAL_ONLY', 'boards': {'Sleeves': {'verified_utc': pw.stamp(self.now), 'data': data}},
            'reconciliation_defect': {'status': 'PASS'}}))
        return pw.build(self.base, self.now)

    FLOOR = {'book': 'cross_asset_floor', 'status': 'INCOMPLETE', 'position_count': 3,
             'legs': [{'verified': False, 'verification_error': 'Check missing completion, expired or future-dated'}] * 3}
    V1GAP = {'book': 'fundamental_book', 'status': 'INCOMPLETE', 'position_count': 2,
             'legs': [{'verified': False, 'verification_error': 'No HT rail decision for held leg'}, {'verified': True}]}

    def test_separate_desk_gap_is_a_neutral_note_not_attention(self):
        report = self.sleeves_cycle('VERIFIED', [], [self.FLOOR])
        self.assertNotIn('EXIT_COVERAGE_UNAVAILABLE', [x['code'] for x in report['alerts']])
        self.assertEqual([n['book'] for n in report['health_notes']], ['cross_asset_floor'])
        self.assertIn('not V1 capital', report['health_notes'][0]['message'])

    def test_pre_scope_receipt_with_only_separate_desk_gap_does_not_alert(self):
        report = self.sleeves_cycle('COVERAGE_UNAVAILABLE', [self.FLOOR])
        self.assertNotIn('EXIT_COVERAGE_UNAVAILABLE', [x['code'] for x in report['alerts']])
        self.assertEqual(len(report['health_notes']), 1)

    def test_real_v1_exit_gap_still_raises(self):
        for remaining, non_v1 in (([self.V1GAP], [self.FLOOR]), ([self.V1GAP, self.FLOOR], None)):
            report = self.sleeves_cycle('COVERAGE_UNAVAILABLE', remaining, non_v1)
            alert = [x for x in report['alerts'] if x['code'] == 'EXIT_COVERAGE_UNAVAILABLE']
            self.assertEqual(len(alert), 1)
            self.assertIn('fundamental_book', alert[0]['message']); self.assertNotIn('cross_asset_floor', alert[0]['message'])
            self.assertEqual(report['health'], 'ATTENTION')

    def test_missing_data_never_healthy(self):
        report = pw.build(self.base, self.now)
        self.assertEqual(report['health'], 'ATTENTION')
        self.assertFalse(report['new_risk_ready'])
        self.assertEqual(report['boards']['Positions']['freshness']['status'], 'UNKNOWN')

    def test_standing_authorization_does_not_clear_missing_evidence(self):
        pw.atomic(self.reports/'private_wealth_authority.json', {'authorized': True, 'paper_only': True})
        report = pw.build(self.base, self.now)
        self.assertTrue(report['authority']['authorized'])
        self.assertFalse(report['new_risk_ready'])
        self.assertEqual(report['health'], 'ATTENTION')

    def test_cycle_id_risk_evidence_requires_exact_observation(self):
        ident = '81fdafb8-e60b-4af8-b56a-6c70c8ef5c7e'
        risk = {'utc': pw.stamp(self.now), 'scope': 'LOCAL_22_BOOK_REGISTRY_ONLY',
                'firm_equity_usd': 123, 'coverage_audit_clean': False}
        pw.atomic(self.reports/'private_wealth_cycle_latest.json', {'cycle_id': ident, 'risk': risk})
        evidence = self.reports/'private_wealth_verification'/(ident+'-evidence.json')
        pw.atomic(evidence, {'local_observation': risk})
        self.assertEqual(pw.build(self.base, self.now)['local_risk']['firm_equity_usd'], 123)
        pw.atomic(evidence, {'local_observation': {**risk, 'firm_equity_usd': 999}})
        self.assertIsNone(pw.build(self.base, self.now)['local_risk']['firm_equity_usd'])

    def test_independent_board_stamps(self):
        receipt = {'utc': pw.stamp(self.now), 'boards': {'Hunt': {'verified_utc': pw.stamp(self.now-timedelta(hours=7))}}}
        pw.atomic(self.reports/'private_wealth_cycle_latest.json', receipt)
        report = pw.build(self.base, self.now)
        self.assertEqual(report['boards']['Hunt']['freshness']['status'], 'STALE')
        self.assertEqual(report['cycle_freshness']['status'], 'FRESH')

    def test_newer_risk_requires_matching_local_evidence(self):
        evidence = self.reports / 'evidence.json'
        observation = {'scope': 'LOCAL_22_BOOK_REGISTRY_ONLY', 'observation_utc': pw.stamp(self.now),
                       'firm_equity_usd': 123, 'evidence': str(evidence)}
        pw.atomic(self.reports/'private_wealth_cycle_latest.json', {'risk': observation})
        self.assertIsNone(pw.build(self.base, self.now)['local_risk']['firm_equity_usd'])
        pw.atomic(evidence, {'verified_utc': pw.stamp(self.now)})
        self.assertEqual(pw.build(self.base, self.now)['local_risk']['firm_equity_usd'], 123)
        pw.atomic(evidence, {'verified_utc': pw.stamp(self.now-timedelta(minutes=1))})
        self.assertIsNone(pw.build(self.base, self.now)['local_risk']['firm_equity_usd'])

    def test_native_exit_receipts_are_counted_without_relabelling_actions(self):
        import exit_check_evidence
        ticket = exit_check_evidence.ticket('replica_floor', 'BTC/USD',
                                           'registered trailing exit', 'long',
                                           1, 105, 100, 105, 5)
        result = pw.performance([{'paper_tickets': [ticket]}])
        self.assertEqual(result['strategies'][0]['booked_pnl_usd'], 5)
        self.assertEqual(result['tickets'][0]['action'], 'registered trailing exit')
        self.assertIsNone(result['strategies'][0]['net_estimate_usd'])
        ticket['paper_only'] = False
        self.assertEqual(pw.performance([{'paper_tickets': [ticket]}])['strategies'], [])

    def test_performance_deduplicates_and_preserves_unknown_costs(self):
        ticket = {'ticket_id': 'x', 'verified': True, 'utc': pw.stamp(self.now),
                  'action': 'HALF', 'book': 'test', 'booked_pnl_usd': 12}
        receipt = {'paper_tickets': [ticket]}
        result = pw.performance([receipt, receipt])['strategies'][0]
        self.assertEqual(result['exit_tranches'], 1)
        self.assertEqual(result['booked_pnl_usd'], 12)
        self.assertIsNone(result['net_estimate_usd'])
        ticket.update(gross_pnl_usd=15, fees_usd=2, slippage_estimate_usd=1, cost_source='fixture')
        result = pw.performance([receipt])['strategies'][0]
        self.assertEqual(result['net_estimate_usd'], 12)
        self.assertEqual(result['win_rate_pct'], 100)

    def test_performance_conflicting_id_is_excluded(self):
        a = {'ticket_id': 'x', 'action': 'SELL', 'verified': True, 'utc': pw.stamp(self.now)}
        b = {**a, 'booked_pnl_usd': 20}
        report = pw.performance([{'paper_tickets': [a, b, a]}])
        self.assertFalse(report['strategies'])
        self.assertTrue(report['issues'])

    def test_cost_drawdown_and_negative_fee(self):
        rows = [{'ticket_id': str(i), 'action': 'SELL', 'verified': True, 'utc': pw.stamp(self.now+timedelta(seconds=i)),
                 'book': 'fixture', 'gross_pnl_usd': gross, 'fees_usd': 1,
                 'slippage_estimate_usd': 1, 'cost_source': 'fixture'} for i, gross in enumerate((12, -3, 4))]
        result = pw.performance([{'paper_tickets': rows}])['strategies'][0]
        self.assertEqual(result['net_estimate_usd'], 7)
        self.assertEqual(result['max_realised_drawdown_usd'], 5)
        rows[0]['fees_usd'] = -1
        self.assertIsNone(pw.performance([{'paper_tickets': rows}])['strategies'][0]['net_estimate_usd'])

    def test_watch_alert_dedup_and_preserved_feed_timestamp(self):
        with patch.object(pw, 'utcnow', return_value=self.now), patch.object(pw, 'kraken_trade', return_value={'ETH/USD': self.quote()}), patch.object(pw, 'request_json', return_value={'ok': True}):
            pw.tick(self.base, notify=False)
            pw.tick(self.base, notify=False)
        events = (self.reports/'private_wealth_alerts.jsonl').read_text().splitlines()
        self.assertEqual(len(events), 1)
        with patch.object(pw, 'utcnow', return_value=self.now+timedelta(minutes=10)), patch.object(pw, 'kraken_trade', side_effect=TimeoutError('fixture timeout')), patch.object(pw, 'request_json', return_value={'ok': False}):
            pw.tick(self.base, notify=False)
        watch = pw.load(self.reports/'private_wealth_watch.json')
        self.assertEqual(watch['tape']['ETH/USD']['utc'], pw.stamp(self.now))
        self.assertIn('FEED_UNAVAILABLE', watch['alert_codes'])
        self.assertIn('DASHBOARD_DOWN', watch['alert_codes'])
        later = self.now+timedelta(minutes=11)
        restored = {**self.quote(), 'utc': pw.stamp(later)}
        with patch.object(pw, 'utcnow', return_value=later), patch.object(pw, 'kraken_trade', return_value={'ETH/USD': restored}), patch.object(pw, 'request_json', return_value={'ok': True}):
            pw.tick(self.base, notify=False)
        last = json.loads((self.reports/'private_wealth_alerts.jsonl').read_text().splitlines()[-1])
        self.assertIn('FEED_UNAVAILABLE', last['recovered'])
        self.assertIn('DASHBOARD_DOWN', last['recovered'])


if __name__ == '__main__':
    unittest.main()
