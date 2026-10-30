"""Synthetic capital accounting/peak regressions; no network or live files."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import firm_capital_flows as flows
import firm_risk


def event(book, amount, kind="capital_transfer", other=None):
    return {"id": "%s:%s:%s" % (book, amount, kind), "book": book,
            "utc": "2026-09-10T00:00:00Z", "type": kind, "usd": amount,
            "from": other if amount > 0 else None, "to": other if amount < 0 else None}


def capital(amount, events=(), present=True, ccy="USD", rate=1.0):
    return {"native_capital": amount, "ccy": ccy, "usd_per_native": rate,
            "events": list(events), "present": present, "path": "/unused.json"}


class CapitalFlowTests(unittest.TestCase):
    def test_new_book_funded_internally_has_zero_external_flow(self):
        old = {"donor": capital(1000)}
        new = {"donor": capital(250, [event("donor", -750, other="receiver")]),
               "receiver": capital(750, [event("receiver", 750, other="donor")])}
        net, details, errors = flows.reconcile(old, new)
        self.assertEqual((net, errors), (0.0, []))
        self.assertEqual(len(details), 2)

    def test_reverse_transfer_and_duplicate_tick_are_not_fresh_capital(self):
        old = {"a": capital(250), "b": capital(750)}
        new = {"a": capital(925, [event("a", 675, other="b")]),
               "b": capital(75, [event("b", -675, other="a")])}
        self.assertEqual(flows.reconcile(old, new)[::2], (0.0, []))
        self.assertEqual(flows.reconcile(new, new), (0.0, [], []))

    def test_external_injection_is_not_profit(self):
        old = {"a": capital(1000)}
        new = {"a": capital(1500, [event("a", 500, "injection")])}
        self.assertEqual(flows.reconcile(old, new)[::2], (500.0, []))

    def test_new_external_book_uses_funding_not_its_mark(self):
        old = {"a": capital(1000)}
        new = dict(old, b=capital(500))
        self.assertEqual(flows.reconcile(old, new)[::2], (500.0, []))

    def test_seed_reset_without_receipt_cannot_erase_loss(self):
        net, _, errors = flows.reconcile({"a": capital(1000)}, {"a": capital(750)})
        self.assertEqual(net, 0)
        self.assertIn("without a capital-flow receipt", errors[0])

    def test_snapshot_ignores_authorization_notes_and_restatements(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            path.write_text(json.dumps({"capital_events": [
                {"type": "injection", "utc": "2026-09-10T00:00:00Z", "amount_usd": 100},
                {"event": "AUTHORISATION CONFIRMED", "amount_usd": 100},
                {"delta_usd": 100, "reason": "restatement"}]}))
            snap, errors = flows.snapshot([{"key": "a", "path": str(path), "ccy": "USD", "start_usd": 200}])
        self.assertEqual(errors, [])
        self.assertEqual(len(snap["a"]["events"]), 1)
        self.assertEqual(snap["a"]["events"][0]["usd"], 100)

    def test_external_removal_uses_actual_receipted_withdrawal(self):
        old = {"a": capital(100), "b": capital(100)}
        new = {"a": capital(None, [event("a", -110, "withdrawal")], present=False),
               "b": capital(100)}
        self.assertEqual(flows.reconcile(old, new)[::2], (-110.0, []))

    def test_unexplained_removal_is_not_a_withdrawal(self):
        net, _, errors = flows.reconcile({"a": capital(100)},
                                        {"a": capital(None, present=False)})
        self.assertEqual(net, 0)
        self.assertIn("explicit external withdrawal", errors[0])

    def test_one_sided_transfer_fails_closed(self):
        _, _, errors = flows.reconcile({"a": capital(1000)},
            {"a": capital(250), "b": capital(750, [event("b", 750, other="a")])})
        self.assertTrue(errors)

    def test_fx_move_is_return_not_funding(self):
        self.assertEqual(flows.reconcile({"a": capital(100, ccy="AUD", rate=.65)},
                                        {"a": capital(100, ccy="AUD", rate=.72)}),
                         (0.0, [], []))

    def test_unreadable_snapshot_preserves_prior_funding(self):
        prev = {"book_keys": ["a"], "capital_snapshot": {"a": capital(100)}}
        with mock.patch.object(flows, "snapshot", return_value=({}, ["a unreadable"])):
            result = flows.capital_event(prev, [{"key": "a", "error": "bad"}])
        self.assertFalse(result["capital_accounting_valid"])
        self.assertEqual(result["peak_shift_usd"], 0)
        self.assertEqual(result["books_removed"], [])
        self.assertEqual(result["capital_snapshot"], prev["capital_snapshot"])

    def test_recovery_without_flow_keeps_peak_basis(self):
        prev = {"book_keys": ["a"], "capital_snapshot": {"a": capital(100)}}
        with mock.patch.object(flows, "snapshot", return_value=(prev["capital_snapshot"], [])):
            result = flows.capital_event(prev, [{"key": "a", "error": None}])
        self.assertTrue(result["capital_accounting_valid"])
        self.assertEqual(result["peak_shift_usd"], 0)
        self.assertFalse(result["capital_event"])


def row(stamp, equity, complete=True):
    return {"utc": stamp, "firm_equity_usd": str(equity), "books_readable": "2" if complete else "1",
            "books_total": "2", "level": "OK" if complete else "UNMEASURED"}


class ReplayTests(unittest.TestCase):
    def test_true_peak_survives_bad_old_peak_and_incomplete_outlier(self):
        rows = [row("1", 100), row("2", 101), row("3", 500, False), row("4", 98)]
        result = flows.replay_complete(rows, "1", 100, "1")
        self.assertEqual(result["peak"], 101)
        self.assertEqual(result["peak_at"], "2")
        self.assertIsNone(result["rows"][2]["firm_drawdown_pct"])
        self.assertAlmostEqual(result["rows"][-1]["firm_drawdown_pct"], -2.970297)

    def test_external_flow_preserves_dollar_drawdown(self):
        rows = [row("1", 90), row("2", 140), row("3", 100)]
        result = flows.replay_complete(rows, "1", 100, "0",
                                       [{"utc": "2", "usd": 50}, {"utc": "3", "usd": -40}])
        self.assertEqual(result["peak"], 110)
        self.assertAlmostEqual(result["rows"][-1]["firm_drawdown_pct"], -9.090909)

    def test_no_complete_observation_refuses_replay(self):
        with self.assertRaises(ValueError):
            flows.replay_complete([row("1", 1, False)], "1", 100, "0")


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = {k: str(Path(self.tmp.name) / filename) for k, filename in
                      [("STATE", "state.json"), ("REPORT", "report.json"),
                       ("EQUITY_CSV", "equity.csv"), ("LOCK", ".lock"), ("ERRORS", "errors.json")]}
        for key, value in self.paths.items():
            patch = mock.patch.object(firm_risk, key, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.book = {"key": "a", "label": "A", "kind": "book", "error": None,
                     "equity_usd": 80, "book_peak_usd": 100, "stale": False}
        self.g = {"books": [self.book], "errors": [], "firm_equity_usd": 80,
                  "firm_equity_live_usd": 80, "stale_equity_usd": 0,
                  "n_books_ok": 1, "n_books": 1, "n_positions": 0}
        self.prev = {"firm_peak_usd": 100, "firm_peak_at_utc": "old", "firm_drawdown_pct": -5,
                     "book_keys": ["a"], "book_equity_usd": {"a": 95}}

    def run_state(self, valid=True, shift=0):
        cap = {"capital_event": bool(shift), "peak_shift_usd": shift,
               "capital_accounting_valid": valid, "capital_accounting_errors": [] if valid else ["missing"],
               "capital_snapshot": {}, "books_added": [], "books_removed": []}
        with mock.patch.object(firm_risk.book_coverage, "gather", return_value=self.g), \
             mock.patch.object(firm_risk.book_coverage, "coverage_audit", return_value=([], [])), \
             mock.patch.object(firm_risk, "capital_event", return_value=cap):
            return firm_risk._run_locked(False, previous=self.prev)

    def test_genuine_halt_is_preserved(self):
        self.g["firm_equity_usd"] = 79
        result = self.run_state()
        self.assertEqual(result["level"], "HALT_NEW_ENTRIES")
        self.assertTrue(result["new_entries_halted"])
        self.assertFalse(result["force_liquidate"])

    def test_unreadable_equity_never_moves_peak_or_becomes_loss(self):
        self.g.update(n_books_ok=0, firm_equity_usd=0)
        self.book.update(error="missing", equity_usd=None)
        result = self.run_state(False)
        self.assertEqual(result["firm_peak_usd"], 100)
        self.assertEqual(result["firm_drawdown_pct"], -5)
        self.assertEqual(result["book_keys"], ["a"])
        self.assertFalse(result["drawdown_valid"])
        self.assertTrue(result["new_entries_halted"])
        self.assertEqual(result["level"], "UNMEASURED")

    def test_genuine_new_peak_advances_and_both_files_are_identical(self):
        self.g["firm_equity_usd"] = 105
        result = self.run_state()
        self.assertEqual(result["firm_peak_usd"], 105)
        self.assertEqual(result["firm_drawdown_pct"], 0)
        self.assertEqual(json.loads(Path(self.paths["STATE"]).read_text()),
                         json.loads(Path(self.paths["REPORT"]).read_text()))

    def test_concurrent_funding_write_defers_peak_and_halts_entries(self):
        self.g["firm_equity_usd"] = 200
        with mock.patch.object(flows, "source_fingerprints", side_effect=[{"a": "old"}, {"a": "new"}]):
            result = self.run_state()
        self.assertEqual(result["firm_peak_usd"], 100)
        self.assertEqual(result["level"], "UNMEASURED")
        self.assertTrue(result["new_entries_halted"])

    def test_atomic_serialization_failure_preserves_original(self):
        firm_risk.atomic_json(self.paths["STATE"], {"value": 1})
        with self.assertRaises(ValueError):
            firm_risk.atomic_json(self.paths["STATE"], {"value": float("nan")})
        self.assertEqual(json.loads(Path(self.paths["STATE"]).read_text()), {"value": 1})

    def test_process_cannot_enter_active_writer_transaction(self):
        script = ("import fcntl,sys\n"
                  "with open(sys.argv[1], 'a') as f:\n"
                  " try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
                  " except BlockingIOError: sys.exit(7)\n")
        with firm_risk.writer_lock():
            result = subprocess.run([sys.executable, "-c", script, self.paths["LOCK"]])
        self.assertEqual(result.returncode, 7)


if __name__ == "__main__":
    unittest.main()
