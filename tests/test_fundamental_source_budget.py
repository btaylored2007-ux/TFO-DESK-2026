import json
import multiprocessing
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import fundamental_source_budget as mod


def reserve_worker(path, barrier, output, subject):
    barrier.wait()
    row = mod.reserve(Path(path), "batch", "event_discovery", 4, subject)
    output.put(row["status"])


def initialise_worker(path, barrier, output):
    barrier.wait()
    try:
        mod.initialise(Path(path), "raced", 20)
        output.put("INITIALISED")
    except ValueError:
        output.put("DUPLICATE")


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "budget.jsonl"
        mod.initialise(self.path, "batch", 20)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stage_quota_exhaustion_is_unresearched(self):
        self.assertEqual(mod.reserve(self.path, "batch", "event_discovery", 5, "first")["status"], "RESERVED")
        refused = mod.reserve(self.path, "batch", "event_discovery", 1, "second")
        self.assertEqual(refused["status"], "UNRESEARCHED")
        self.assertEqual(mod.status(self.path, "batch")["nothing_found_count"], 0)

    def test_primary_direct_does_not_consume_search_quota(self):
        mod.reserve(self.path, "batch", "primary_direct", 500, "issuer filings")
        status = mod.status(self.path, "batch")
        self.assertEqual(status["usage"]["primary_direct"], 500)
        self.assertEqual(status["usage"]["event_discovery"], 0)

    def test_both_provider_controls_are_required(self):
        mod.control_check(self.path, "batch", "start", True, "control filing resolved")
        self.assertEqual(mod.status(self.path, "batch")["coverage_status"], "COVERAGE_FAILURE")
        mod.control_check(self.path, "batch", "midpoint", True, "control filing still resolves")
        self.assertEqual(mod.status(self.path, "batch")["coverage_status"], "ACTIVE")

    def test_concurrent_reservations_cannot_oversubscribe_quota(self):
        context = multiprocessing.get_context("fork")
        barrier = context.Barrier(2)
        output = context.Queue()
        workers = [
            context.Process(target=reserve_worker,
                            args=(str(self.path), barrier, output, "worker-%d" % index))
            for index in range(2)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
            self.assertEqual(worker.exitcode, 0)
        statuses = sorted(output.get(timeout=2) for _ in workers)
        self.assertEqual(statuses, ["RESERVED", "UNRESEARCHED"])
        self.assertEqual(mod.status(self.path, "batch")["usage"]["event_discovery"], 4)

    def test_concurrent_initialise_is_exactly_once(self):
        race_path = Path(self.tmp.name) / "initialise-race.jsonl"
        context = multiprocessing.get_context("fork")
        barrier = context.Barrier(2)
        output = context.Queue()
        workers = [
            context.Process(target=initialise_worker,
                            args=(str(race_path), barrier, output))
            for _ in range(2)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
            self.assertEqual(worker.exitcode, 0)
        statuses = sorted(output.get(timeout=2) for _ in workers)
        self.assertEqual(statuses, ["DUPLICATE", "INITIALISED"])
        rows = mod.read_rows(race_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["batch_id"], "raced")

    def test_status_cli_is_nonzero_on_coverage_failure(self):
        result = subprocess.run(
            [sys.executable, str(Path(mod.__file__)), "--ledger", str(self.path),
             "status", "--batch-id", "batch"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["coverage_status"], "COVERAGE_FAILURE")


if __name__ == "__main__":
    unittest.main()
