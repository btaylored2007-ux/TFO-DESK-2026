#!/usr/bin/env python3
"""HT decision 2026-09-26 #7: dormant sleeves that write fundamental_book.json must take the 'fundamental' lock
(the one tfo_buy / tfo_ticket / fundamental_book / tfo_equity_refresh use). Offline; nothing is written."""
import contextlib, importlib, os, sys, tempfile, unittest
from unittest import mock
DESK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DESK)

class SleeveLocks(unittest.TestCase):
    def _names(self, mod, fn, inner):
        m = importlib.import_module(mod); seen = []
        @contextlib.contextmanager
        def rec(name="fundamental", timeout=900, poll=0.5):
            seen.append(name); yield
        with mock.patch.object(m.desk_lock, "ledger_lock", rec), mock.patch.object(m, inner, lambda *a, **k: 0):
            getattr(m, fn)()
        return seen

    def test_ledger_path_and_lock_name(self):
        for mod in ("profit_gate_sleeve", "seed_recovery_sleeve"):
            m = importlib.import_module(mod)
            self.assertTrue(m.LEDGER.endswith(os.path.join("reports", "fundamental_book.json")))
            self.assertEqual(self._names(mod, "tick", "_tick_locked"), ["fundamental"])
            self.assertEqual(self._names(mod, "deploy", "_deploy_locked"), ["fundamental"])

    def test_real_lock_is_reentrant_free_no_nested_fundamental(self):
        # a real flock on a temp LOCKDIR: tick under the dormant Head-Trader flag must acquire and release cleanly
        dl = importlib.import_module("desk_lock"); m = importlib.import_module("profit_gate_sleeve")
        with tempfile.TemporaryDirectory() as d, mock.patch.object(dl, "LOCKDIR", d):
            htg = importlib.import_module("head_trader_gate")
            with mock.patch.object(htg, "active", lambda *a, **k: True):
                self.assertIsNone(m.tick())
            with dl.ledger_lock("fundamental", timeout=1):             # released afterwards
                pass

if __name__ == "__main__":
    unittest.main()
