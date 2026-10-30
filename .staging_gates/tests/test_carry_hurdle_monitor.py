#!/usr/bin/env python3
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import carry_hurdle_monitor as monitor


class CarryHurdleMonitorTests(unittest.TestCase):
    def test_assigned_number_reads_executor_constants(self):
        text = "ENTRY_THRESHOLD_PCT = 2.0\nEXIT_TRAILING_DAYS = 7\n"
        self.assertEqual(monitor.assigned_number(text, "ENTRY_THRESHOLD_PCT"), 2.0)
        self.assertEqual(monitor.assigned_number(text, "EXIT_TRAILING_DAYS"), 7.0)
        with self.assertRaises(ValueError):
            monitor.assigned_number(text, "MISSING")

    def test_trailing_mean_has_inclusive_window_boundary(self):
        rows = [
            {"t": 0, "preferred_mean": 9},
            {"t": 9 * 86400, "preferred_mean": 1},
            {"t": 10 * 86400, "preferred_mean": 3},
        ]
        value, count = monitor.trailing_mean(rows, 10 * 86400, 1)
        self.assertEqual((value, count), (2.0, 2))

    def test_history_rows_skips_invalid_and_sorts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "history.jsonl"
            path.write_text(
                '{"t": 2, "preferred_mean": 2}\ninvalid\n'
                '{"t": 1, "preferred_mean": 1}\n'
                '{"t": 3}\n'
            )
            self.assertEqual([row["t"] for row in monitor.history_rows(path)], [1, 2])

    def test_run_separates_registered_exit_from_economic_hurdle(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            book = base / "book.json"
            snap = base / "snapshot.json"
            history = base / "history.jsonl"
            executor = base / "executor.py"
            reg_v2 = base / "v2.md"
            reg_new = base / "new.md"
            output = base / "report.json"

            book.write_text(json.dumps({
                "positions": {
                    "BTC": {
                        "notional_aud": 10000,
                        "entry_cost_aud": 16,
                        "funding_accrued_aud": 5,
                        "opened_epoch": 0,
                        "entry_carry_pct": 4.2,
                    }
                }
            }))
            snap.write_text(json.dumps({
                "preferred_mean_annualised_pct": 1.5,
                "harvest_threshold_pct": 4.0,
                "cost_bps_maker": 32.0,
            }))
            history.write_text(
                json.dumps({"t": 9 * 86400, "preferred_mean": 1.0}) + "\n" +
                json.dumps({"t": 10 * 86400, "preferred_mean": 1.5}) + "\n"
            )
            executor.write_text(
                "COST_BPS_PER_SIDE = 25.0\n"
                "ENTRY_THRESHOLD_PCT = 2.0\n"
                "EXIT_TRAILING_DAYS = 7\n"
            )
            reg_v2.write_text("sealed v2")
            reg_new.write_text("sealed new names")

            patches = [
                mock.patch.object(monitor, "BOOK", book),
                mock.patch.object(monitor, "SNAP", snap),
                mock.patch.object(monitor, "HISTORY", history),
                mock.patch.object(monitor, "EXECUTOR", executor),
                mock.patch.object(monitor, "REG_V2", reg_v2),
                mock.patch.object(monitor, "REG_NEW", reg_new),
                mock.patch.object(monitor, "OUT", output),
            ]
            for patch in patches:
                patch.start()
                self.addCleanup(patch.stop)
            report = monitor.run()

            self.assertFalse(report["decay"]["registered_exit_fires_now"])
            self.assertFalse(report["control_flags"]["rule_holds_but_economics_negative"])
            self.assertFalse(report["current_book"]["economic_verdict_available"])
            self.assertIsNone(report["decay"]["current_decay_from_entry_pp"])
            self.assertIn("UNRECONCILED", report["status"])
            self.assertTrue(report["control_flags"]["monitor_entry_gate_metadata_stale"])
            self.assertTrue(report["control_flags"]["monitor_cost_metadata_stale"])
            self.assertEqual(report["book_action"].split(" ", 1)[0], "NONE")
            self.assertEqual(json.loads(output.read_text())["control"], report["control"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
