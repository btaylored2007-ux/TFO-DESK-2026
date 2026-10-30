import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
import uuid

import private_wealth_cycle_receipt as receipt


class ReceiptEvidenceTests(unittest.TestCase):
    def test_scanner_requires_current_cycle_and_committed_current_hash(self):
        now = dt.datetime.now(dt.timezone.utc).timestamp()
        raw = b'{"holdings":{"BTC":1}}'
        scan = {'asof': dt.datetime.fromtimestamp(now - 10, dt.timezone.utc).isoformat(),
                'execution_enabled': True, 'sleeves': [{'book': 'crypto', 'action': 'HOLD'}]}
        tx = {'status': 'COMMITTED', 'entries': {'crypto.json': {'after_hash': receipt.digest(raw)}}}
        self.assertTrue(receipt.scanner_assessment('crypto', raw, scan, tx, now - 20, now)['coverage_verified'])
        self.assertFalse(receipt.scanner_assessment('crypto', raw, scan, tx, now - 5, now)['coverage_verified'])
        self.assertFalse(receipt.scanner_assessment('crypto', b'changed', scan, tx, now - 20, now)['coverage_verified'])
        scan['execution_enabled'] = False
        self.assertFalse(receipt.scanner_assessment('crypto', raw, scan, tx, now - 20, now)['coverage_verified'])

    def test_maker_wait_binds_current_ledger_and_settled_bar(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'maker.json'; path.write_text('{}')
            now = dt.datetime.now(dt.timezone.utc).timestamp()
            yesterday=dt.datetime.fromtimestamp(now-86400,dt.timezone.utc).strftime('%Y-%m-%d')
            evidence = {'ledger_stable': True, 'ledger_path': str(path), 'ledger_sha256': receipt.digest(path.read_bytes()),
                        'observed_utc': dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(),
                        'latest_settled': yesterday, 'last_evaluated_settled': yesterday,
                        'pending_order': None, 'registered_stop_signal': None, 'held_since_settled_bars': 0}
            self.assertTrue(receipt.maker_assessment(path, evidence, now)['coverage_verified'])
            evidence['registered_stop_signal'] = 'stop'
            self.assertFalse(receipt.maker_assessment(path, evidence, now)['coverage_verified'])
            evidence['registered_stop_signal'] = None
            path.write_text('{"changed":true}')
            self.assertFalse(receipt.maker_assessment(path, evidence, now)['coverage_verified'])

    def test_scanner_tickets_preserve_unknown_costs_and_deduplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'crypto.json').write_text(json.dumps({'capital_events': [{'ticket_id': 'one', 'utc': '2026-09-24T07:00:00Z', 'action': 'STOP'}]}))
            scan = {'banks_stops_executed': [{'book': 'crypto', 'ticket_id': 'one', 'sold': {'BTC': {'pnl_usd': 4}}}]}
            tickets = receipt.scanner_tickets(scan, root, set())
            self.assertEqual(tickets[0]['booked_pnl_usd'], 4)
            self.assertIsNone(tickets[0]['fees_usd'])
            self.assertIsNone(tickets[0]['slippage_estimate_usd'])
            self.assertEqual(receipt.scanner_tickets(scan, root, {'one'}), [])
            (root / 'crypto.json').write_text('{"capital_events":[]}')
            with self.assertRaises(ValueError): receipt.scanner_tickets(scan, root, set())

    def test_future_evidence_is_not_fresh(self):
        self.assertFalse(receipt.fresh('2026-09-25T00:00:00Z', 1))
        self.assertFalse(receipt.fresh(None, 1))

    def test_admission_reports_actual_uncertainty_without_false_halt(self):
        now=dt.datetime.now(dt.timezone.utc).timestamp()
        daily={'status':'PROXY','performance_verified':False,'asof':dt.datetime.fromtimestamp(now,dt.timezone.utc).isoformat(),'pnl_pct':-.2}
        reasons=receipt.admission_blockers({'new_entries_halted':False,'firm_drawdown_pct':-.2},daily,True,True,now)
        self.assertTrue(any('opening baseline' in r for r in reasons))
        self.assertFalse(any('halted=true' in r for r in reasons))

    def test_current_day_gate_is_separate_from_historical_performance(self):
        now=dt.datetime.now(dt.timezone.utc).timestamp()
        daily={'status':'VERIFIED','current_daily_risk_verified':True,
               'performance_verified':False,'historical_performance_verified':False,
               'asof':dt.datetime.fromtimestamp(now,dt.timezone.utc).isoformat(),'pnl_pct':.1}
        risk={'new_entries_halted':False,'firm_drawdown_pct':-.2}
        self.assertEqual(receipt.admission_blockers(risk,daily,True,True,now),[])
        del daily['current_daily_risk_verified']
        self.assertTrue(any('Current-day risk unverified' in r for r in receipt.admission_blockers(risk,daily,True,True,now)))
        daily['current_daily_risk_verified']=True
        daily['status']='PROXY'
        self.assertTrue(any('opening baseline' in r for r in receipt.admission_blockers(risk,daily,True,True,now)))

    def test_approved_proxy_keeps_loss_and_expiry_gates(self):
        now = dt.datetime.now(dt.timezone.utc).timestamp()
        iso = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()
        daily = {'status': 'PROXY', 'current_daily_risk_verified': False,
                 'daily_risk_basis_allowed': True, 'daily_risk_basis': 'FUNDER_APPROVED_FIXED_PROXY',
                 'daily_risk_authorization_expires_utc': iso(now+100), 'asof': iso(now), 'pnl_pct': -.5}
        risk = {'new_entries_halted': False, 'firm_drawdown_pct': -.5}
        self.assertEqual(receipt.admission_blockers(risk, daily, True, True, now), [])
        daily['pnl_pct'] = -3
        self.assertTrue(any('-3%' in r for r in receipt.admission_blockers(risk, daily, True, True, now)))
        daily['pnl_pct'] = -.5
        self.assertTrue(receipt.admission_blockers(risk, daily, True, True, now+101))

    def test_retained_exit_survives_later_hold_without_replay(self):
        ident=str(uuid.uuid4());now=dt.datetime.now(dt.timezone.utc).timestamp()
        ticket={'ticket_id':ident,'paper_only':True,'verified':True,'book':'native',
                'utc':dt.datetime.fromtimestamp(now-2000,dt.timezone.utc).isoformat(),'booked_pnl_usd':None}
        ledger={'exit_checks':{'legs':{'x':{'status':'HOLD'}}},'closed':[{'execution_receipt':ticket}]}
        found,issues=receipt.retained_native_tickets(ledger,'native',set(),now)
        self.assertEqual(found,[ticket]);self.assertEqual(issues,[])
        self.assertEqual(receipt.retained_native_tickets(ledger,'native',{ident},now)[0],[])
        ticket['book']='other'
        self.assertTrue(receipt.retained_native_tickets(ledger,'native',set(),now)[1])

    def test_builder_refuses_unclaimed_and_archived_cycles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); reports = root / 'reports'; reports.mkdir()
            gate = reports / 'private_wealth_gate.json'
            gate.write_text('{"active":null}')
            with self.assertRaisesRegex(ValueError, 'already be claimed'):
                receipt.build('test-cycle', base=root)
            gate.write_text(json.dumps({'active': {'cycle_id': 'test-cycle'}}))
            archive = reports / 'private_wealth_cycles'; archive.mkdir()
            frozen = b'{"original":true}'
            (archive / 'test-cycle.json').write_bytes(frozen)
            with self.assertRaisesRegex(ValueError, 'immutable'):
                receipt.build('test-cycle', base=root)
            self.assertEqual((archive / 'test-cycle.json').read_bytes(), frozen)


class V1ExitScopeTests(unittest.TestCase):
    """2026-09-28: exit-coverage health scores V1 books; separate desks are reported, never scored."""
    def run_build(self, checks, ticket_issue_book=None):
        import sys, types
        from unittest.mock import patch
        import book_coverage, exit_check_evidence, private_wealth_monitor as monitor, head_trader_gate
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); reports = root / 'reports'; reports.mkdir()
            now = dt.datetime.now(dt.timezone.utc); stamp = now.strftime('%Y-%m-%dT%H:%M:%SZ')
            (reports / 'private_wealth_gate.json').write_text(json.dumps({'active': {'cycle_id': 'c1', 'started_utc': stamp}}))
            (reports / 'firm_risk.json').write_text(json.dumps({'utc': stamp, 'capital_accounting_valid': True, 'coverage_audit_clean': True,
                                                                'book_keys': sorted(checks)}))
            for f in ('tfo_bank_rail_scan_latest.json', '.tfo_bank_rail_transaction.json', 'daily_profit_target_status.json'):
                (reports / f).write_text('{}')
            books = []
            for key in checks:
                path = reports / (key + '.json'); path.write_text(json.dumps({'positions': {'X': {}}, 'book': key}))
                books.append({'key': key, 'path': str(path), 'positions': [{'symbol': 'X'}]})
            gathered = {'books': books, 'n_books': len(books), 'n_positions': len(books), 'errors': [], 'positions': []}
            def assess(ledger, now=None, max_age=None):
                return dict(checks[ledger['book']])
            issues = [{'book': ticket_issue_book, 'reason': 'x'}] if ticket_issue_book else []
            with patch.object(book_coverage, 'gather', lambda *_: gathered), patch.object(book_coverage, 'coverage_audit', lambda *_: ([], None)), \
                 patch.object(exit_check_evidence, 'assess', assess), patch.object(head_trader_gate, 'active', lambda: False), \
                 patch.object(monitor, 'kraken_trade', lambda: {}), \
                 patch.object(receipt, 'retained_native_tickets', lambda ledger, book, known, now: ([], [i for i in issues if i['book'] == book])):
                return receipt.build('c1', base=root)

    OK = {'status': 'VERIFIED', 'coverage_verified': True}
    BAD = {'status': 'INCOMPLETE', 'coverage_verified': False, 'legs': [{'verified': False, 'verification_error': 'Check expired'}], 'position_count': 1}

    def test_incomplete_separate_desk_does_not_fail_v1_coverage(self):
        out = self.run_build({'fundamental_book': self.OK, 'tfo_ht_crypto': self.OK, 'cross_asset_floor': self.BAD})
        self.assertTrue(out['local_exit_coverage_verified'])
        self.assertEqual(out['boards']['Sleeves']['data']['status'], 'VERIFIED')
        self.assertEqual(out['remaining_exit_routes'], [])
        self.assertEqual([r['book'] for r in out['non_v1_exit_routes']], ['cross_asset_floor'])     # still reported
        self.assertEqual({a['book']: a['exit_scope'] for a in out['native_exit_checks']}['cross_asset_floor'], 'NON_V1_SEPARATE_DESK')

    def test_real_v1_gap_still_fails_coverage(self):
        out = self.run_build({'fundamental_book': self.BAD, 'cross_asset_floor': self.OK})
        self.assertFalse(out['local_exit_coverage_verified'])
        self.assertEqual(out['boards']['Sleeves']['data']['status'], 'COVERAGE_UNAVAILABLE')
        self.assertEqual([r['book'] for r in out['remaining_exit_routes']], ['fundamental_book'])

    def test_v1_ticket_issue_still_fails_but_separate_desk_issue_does_not(self):
        self.assertFalse(self.run_build({'fundamental_book': self.OK}, ticket_issue_book='fundamental_book')['local_exit_coverage_verified'])
        self.assertTrue(self.run_build({'fundamental_book': self.OK, 'cross_asset_floor': self.OK},
                                       ticket_issue_book='cross_asset_floor')['local_exit_coverage_verified'])

    def test_check_written_after_build_start_is_not_future_dated(self):
        # 2026-09-28: the 60s crypto runner can refresh its checks after the build starts; judge at read time.
        import time
        from unittest.mock import patch
        import book_coverage
        seen = {}
        def assess_spy(ledger, now=None, max_age=None):
            seen['ok'] = now >= ledger['checked_epoch']
            return {'status': 'VERIFIED' if seen['ok'] else 'INCOMPLETE', 'coverage_verified': seen['ok']}
        def gather_late(*a):
            g = gather_late.g
            time.sleep(0.02)
            for b in g['books']:
                Path(b['path']).write_text(json.dumps({'positions': {'X': {}}, 'book': b['key'], 'checked_epoch': time.time()}))
            return g
        import exit_check_evidence
        with patch.object(exit_check_evidence, 'assess', assess_spy), patch.object(book_coverage, 'gather', gather_late):
            out = self._build_with_gather(gather_late)
        self.assertTrue(seen.get('ok')); self.assertTrue(out['local_exit_coverage_verified'])

    def _build_with_gather(self, gather_fn):
        from unittest.mock import patch
        import book_coverage, private_wealth_monitor as monitor, head_trader_gate
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); reports = root / 'reports'; reports.mkdir()
            stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            (reports / 'private_wealth_gate.json').write_text(json.dumps({'active': {'cycle_id': 'c1', 'started_utc': stamp}}))
            (reports / 'firm_risk.json').write_text(json.dumps({'utc': stamp, 'capital_accounting_valid': True, 'coverage_audit_clean': True, 'book_keys': ['tfo_ht_crypto']}))
            for f in ('tfo_bank_rail_scan_latest.json', '.tfo_bank_rail_transaction.json', 'daily_profit_target_status.json'):
                (reports / f).write_text('{}')
            path = reports / 'tfo_ht_crypto.json'; path.write_text('{"positions":{"X":{}},"book":"tfo_ht_crypto","checked_epoch":0}')
            gather_fn.g = {'books': [{'key': 'tfo_ht_crypto', 'path': str(path), 'positions': [{'symbol': 'X'}]}], 'n_books': 1, 'n_positions': 1, 'errors': [], 'positions': []}
            with patch.object(book_coverage, 'coverage_audit', lambda *_: ([], None)), patch.object(head_trader_gate, 'active', lambda: False), \
                 patch.object(monitor, 'kraken_trade', lambda: {}):
                return receipt.build('c1', base=root)

    def test_unlisted_books_are_v1_and_fail_closed(self):
        self.assertEqual(receipt.exit_scope('kraken_floor'), 'V1')
        self.assertEqual(receipt.exit_scope('some_new_book'), 'V1')
        self.assertEqual(receipt.exit_scope('cross_asset_floor'), 'NON_V1_SEPARATE_DESK')

    def test_ht_rails_fresh_fill_waiting_for_post_fill_print_is_a_data_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp); now = dt.datetime.now(dt.timezone.utc)
            def rows(why):
                (reports / 'ht_equity_rails_latest.json').write_text(json.dumps({'utc': now.isoformat(), 'execute': True, 'paper_only': True,
                    'books': ['fundamental_book'], 'rows': [{'book': 'fundamental_book', 'sym': 'AZN.L', 'status': 'BLOCKED', 'rail': 'STOP',
                    'why': why, 'quote_utc': now.isoformat(), 'session': 'regular'}]}))
                return receipt.ht_rails_assessment('fundamental_book', {'positions': {'AZN.L': {}}}, reports, now.timestamp())
            self.assertTrue(rows('delayed bar predates entry or repeats exit bar')['coverage_verified'])
            self.assertFalse(rows('price sanity (>2.5x last good)')['coverage_verified'])   # a real fault still fails


if __name__ == '__main__':
    unittest.main()
