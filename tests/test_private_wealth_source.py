import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import patch

import book_coverage
from private_wealth_source import reconcile

BASE = Path(__file__).resolve().parents[1]


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'source.json'
        self.source = json.loads((BASE / 'reports/private_wealth_source_books.json').read_text())

    def check(self, source):
        self.path.write_text(json.dumps(source))
        return reconcile(self.path)

    def test_actual_source_gap_and_no_cash_double_count(self):
        result = self.check(self.source)
        self.assertEqual(result['book_sum_usd'], 1820849.60)
        self.assertEqual(result['gap_usd'], 7237.77)
        self.assertEqual(result['status'], 'SOURCE_GAP')
        self.assertFalse(result['execution_verified'])

    def test_matching_total_does_not_certify_execution(self):
        self.source['headline_equity_usd'] = '1820849.60'
        result = self.check(self.source)
        self.assertEqual(result['status'], 'TOTALS_MATCH')
        self.assertFalse(result['execution_verified'])

    def test_explanation_checks_arithmetic_without_certifying_execution(self):
        explanation = Path(self.tmp.name) / 'private_wealth_source_explanation.json'
        explanation.write_bytes((BASE / 'reports/private_wealth_source_explanation.json').read_bytes())
        result = self.check(self.source)
        self.assertEqual(result['status'], 'SOURCE_GAP')
        self.assertTrue(result['explanation']['arithmetic_verified'])
        self.assertFalse(result['explanation']['ledger_replayed'])
        self.assertFalse(result['execution_verified'])
        self.source['headline_powder_usd'] = '1'
        self.assertEqual(self.check(self.source)['status'], 'SOURCE_UNAVAILABLE')

    def test_changed_explanation_fails_closed(self):
        explanation = Path(self.tmp.name) / 'private_wealth_source_explanation.json'
        explanation.write_text('{}')
        self.assertEqual(self.check(self.source)['status'], 'SOURCE_UNAVAILABLE')

    def test_observed_day_rows_match_without_dropping_1051_actions(self):
        data = json.loads((BASE / 'reports/private_wealth_source_day_check.json').read_text())
        ids = [r['export_id'] for r in data['rows']] + data['null_pnl_ids']
        self.assertEqual(len(ids), data['reported_action_count'])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(sum(Decimal(r['pnl']) for r in data['rows']), Decimal('-1961.12'))
        self.assertIn('act-001', ids)
        self.assertIn('act-002', ids)
        self.assertFalse(data['execution_verified'])

    def test_invalid_sources_fail_closed(self):
        for field, value in [('equity_usd', 'NaN'), ('equity_usd', '-1'),
                             ('open_positions', True), ('open_positions', 3)]:
            source = copy.deepcopy(self.source)
            source['books'][0][field] = value
            self.assertEqual(self.check(source)['status'], 'SOURCE_UNAVAILABLE')
        self.source['books'][1]['book'] = self.source['books'][0]['book']
        self.assertEqual(self.check(self.source)['status'], 'SOURCE_UNAVAILABLE')

    def test_audit_exclusion_requires_exact_path_content_and_no_symlink(self):
        root = Path(self.tmp.name).resolve()
        evidence = root / 'report.json'
        evidence.write_text('{"paper_only":true,"read_only":true}')
        digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
        with patch.object(book_coverage, 'BASE', str(root)), patch.object(
                book_coverage, 'AUDIT_EVIDENCE_SHA256', {'report.json': digest}):
            self.assertTrue(book_coverage._verified_audit_evidence(evidence))
            other = root / 'other.json'
            other.write_bytes(evidence.read_bytes())
            self.assertFalse(book_coverage._verified_audit_evidence(other))
            evidence.write_text('{"cash":100}')
            self.assertFalse(book_coverage._verified_audit_evidence(evidence))
            evidence.unlink()
            evidence.symlink_to(other)
            self.assertFalse(book_coverage._verified_audit_evidence(evidence))


if __name__ == '__main__':
    unittest.main()
