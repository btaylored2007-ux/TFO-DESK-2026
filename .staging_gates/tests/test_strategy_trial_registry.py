#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import strategy_trial_registry as registry  # noqa: E402


class TestStrategyTrialRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = os.path.join(self.tmp.name, "ledger.jsonl")
        self.closed = os.path.join(self.tmp.name, "closed.json")
        with open(self.ledger, "w") as target:
            target.write(json.dumps({
                "utc": "2026-09-02T00:00:00Z", "mechanism_id": "bootstrap",
                "status": "reconciled_floor", "count_before": 3644,
                "variants": 0, "count_after": 3644, "rationale": "floor",
            }) + "\n")
        with open(self.closed, "w") as target:
            json.dump({"closed": {"dead-family": {"reason": "answered"}}}, target)

    def tearDown(self):
        self.tmp.cleanup()

    def test_floor_and_exact_single_reservation(self):
        self.assertEqual(registry.current_floor(self.ledger), 3644)
        with open(self.ledger, "rb") as source:
            before = source.read()
        receipt = registry.reserve("new-family", 7, "predeclared grid",
                                   self.ledger, self.closed)
        self.assertEqual(receipt["count_before"], 3644)
        self.assertEqual(receipt["count_after"], 3651)
        self.assertEqual(registry.current_floor(self.ledger), 3651)
        with open(self.ledger, "rb") as source:
            after = source.read()
        self.assertTrue(after.startswith(before))

    def test_duplicate_and_closed_mechanisms_fail(self):
        registry.reserve("new-family", 1, "one", self.ledger, self.closed)
        with self.assertRaises(ValueError):
            registry.reserve("new-family", 1, "again", self.ledger, self.closed)
        with self.assertRaises(ValueError):
            registry.reserve("dead-family", 1, "again", self.ledger, self.closed)

    def test_invalid_variant_count_fails_without_append(self):
        with open(self.ledger, "rb") as source:
            before = source.read()
        with self.assertRaises(ValueError):
            registry.reserve("bad", 0, "bad", self.ledger, self.closed)
        with open(self.ledger, "rb") as source:
            self.assertEqual(source.read(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
