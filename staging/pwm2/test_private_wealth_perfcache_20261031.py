# tfo_pwm2_20261031: receipt cache must be invisible (identical performance) and re-read only changed files.
import json, os, tempfile, time, unittest
from pathlib import Path
from unittest.mock import patch
import private_wealth_monitor as pw
T = lambda i, a='SELL': {'ticket_id': 't%d' % i, 'action': a, 'verified': True, 'utc': '2026-10-30T0%d:00:00+00:00' % i, 'paper_only': True}
class PerfCache(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp()); pw._SLIM_CACHE.clear(); pw._PERF_MEMO.clear()
        for i in range(3): (self.d / ('c%d.json' % i)).write_text(json.dumps({'paper_tickets': [T(i)], 'desk_scope': 'LOCAL_ONLY', 'boards': {'big': 'x' * 1000}}))
    def paths(self): return sorted(self.d.glob('*.json'))
    def test_identical_to_full_load(self):
        cyc = {'paper_tickets': [T(5)]}
        self.assertEqual(pw._perf_cached([pw._receipt_slim(p) for p in self.paths()], cyc), pw.performance([pw.load(p) for p in self.paths()] + [cyc]))
    def test_unchanged_files_not_reread(self):
        [pw._receipt_slim(p) for p in self.paths()]
        with patch.object(pw, 'load', side_effect=AssertionError('reread')): [pw._receipt_slim(p) for p in self.paths()]
    def test_changed_file_reread_and_perf_recomputed(self):
        cyc = {}; a = pw._perf_cached([pw._receipt_slim(p) for p in self.paths()], cyc)
        p = self.paths()[0]; p.write_text(json.dumps({'paper_tickets': [T(1), T(7)]})); os.utime(p, ns=(time.time_ns(), time.time_ns() + 10**9))
        b = pw._perf_cached([pw._receipt_slim(q) for q in self.paths()], cyc)
        self.assertEqual(b, pw.performance([pw.load(q) for q in self.paths()] + [cyc])); self.assertNotEqual(a, b)
    def test_missing_keys_stay_missing_and_copies_independent(self):
        p = self.d / 'z.json'; p.write_text('{"other": 1}'); self.assertEqual(pw._receipt_slim(p), {})
        r = pw._receipt_slim(self.paths()[0]); r['paper_tickets'].clear(); self.assertTrue(pw._receipt_slim(self.paths()[0])['paper_tickets'])
if __name__ == '__main__': unittest.main()
