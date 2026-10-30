"""2026-10-28: trend_core.py mark must take the trend_core ledger lock (shared with trend_core_global.py)."""
import os, sys, unittest, unittest.mock as um
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import trend_core as tc, desk_lock

class T(unittest.TestCase):
    def test_mark_takes_lock(self):
        seen = []
        class L:
            def __enter__(s): seen.append("in")
            def __exit__(s, *a): seen.append("out")
        with um.patch.object(desk_lock, "ledger_lock", side_effect=lambda n, **k: (seen.append(n), L())[1]), \
             um.patch.object(tc, "mark", side_effect=lambda: seen.append("mark")), um.patch.object(tc, "status_text"), \
             um.patch.object(sys, "argv", ["trend_core.py", "mark"]):
            tc.main()
        self.assertEqual(seen, ["trend_core", "in", "mark", "out"])
    def test_global_uses_same_lock_name(self):
        src = open(os.path.join(os.path.dirname(tc.__file__), "trend_core_global.py")).read()
        self.assertIn('ledger_lock("trend_core"', src)
if __name__ == "__main__": unittest.main()
