import datetime as dt
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fundamental_hunt_preflight as preflight


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 9, 8, 6, tzinfo=dt.timezone.utc)

    def row(self, name, state='stale_revisit', **changes):
        return dict(frontier_id=name, frontier=name, state=state,
                    origin='never_covered' if state == 'ready' else 'covered',
                    reconciled=True, eligible=True, evidence=[{'source': 'test'}],
                    last_completed_date='2026-08-27', **changes)

    def select(self, rows):
        return preflight.select_frontier(
            {'schema': 'tfo.discovery.coverage.v2', 'frontiers': rows}, self.now)

    def test_excludes_blocked_in_progress_and_future_retry(self):
        rows = [self.row('blocked', 'blocked_data'), self.row('running', 'in_progress'),
                self.row('retry', retry_after_utc='2026-09-09T00:00:00Z'),
                self.row('valid')]
        selected, excluded = self.select(rows)
        self.assertEqual(selected['frontier_id'], 'valid')
        self.assertEqual(len(excluded), 3)

    def test_new_frontier_precedes_oldest_revisit(self):
        selected, _ = self.select([self.row('old'), self.row('new', 'ready')])
        self.assertEqual(selected['frontier_id'], 'new')

    def test_ready_override_does_not_make_covered_frontier_untouched(self):
        recent = self.row('recent', 'ready')
        recent.update(origin='covered', last_completed_date='2026-09-04')
        selected, _ = self.select([recent, self.row('old')])
        self.assertEqual(selected['frontier_id'], 'old')

    def test_unreconciled_and_duplicate_fail_closed(self):
        row = self.row('a')
        row['reconciled'] = False
        with self.assertRaisesRegex(ValueError, 'no reconciled'):
            self.select([row])
        with self.assertRaisesRegex(ValueError, 'duplicated'):
            self.select([self.row('a'), self.row('a')])

    def test_empty_frontier_contract_is_blocked(self):
        with self.assertRaisesRegex(ValueError, 'no reconciled'):
            self.select([])

    def test_prompt_pin_rejects_modified_or_different_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'prompt.md'
            path.write_text('reviewed method')
            pin = dict(schema='tfo.discovery.prompt-pin.v1', path=str(path),
                       sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                       reviewed_utc='2026-09-08T06:00:00Z')
            self.assertEqual(preflight.validate_prompt(pin, path), pin['sha256'])
            path.write_text('unreviewed change')
            with self.assertRaisesRegex(ValueError, 'changed'):
                preflight.validate_prompt(pin, path)
            with self.assertRaisesRegex(ValueError, 'different file'):
                preflight.validate_prompt(pin, Path(tmp) / 'other.md')


if __name__ == '__main__':
    unittest.main()
