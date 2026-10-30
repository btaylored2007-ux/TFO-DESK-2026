"""Only exact, hash-receipted nonlive repair copies escape the ledger audit."""
import hashlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import book_coverage as coverage


class RepairSnapshotCoverage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(os.path.realpath(self.tmp.name), "firm")
        self.other = os.path.join(os.path.realpath(self.tmp.name), "engine")
        self.folder = os.path.join(self.base, "reports", "repairs", "verified-capital-fix")
        os.makedirs(self.folder)
        os.makedirs(self.other)
        self.path = os.path.join(self.folder, "book.json")
        with open(self.path, "w") as f:
            json.dump({"cash": 1234, "positions": {}}, f)
        with open(self.path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        self.receipt = {"applied": True, "backup_directory": self.folder,
                        "backup_sha256": {self.path: digest}}
        self.write_receipt()
        self.patch = patch.multiple(coverage, BASE=self.base, KRAKEN_DESK=self.other)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def write_receipt(self):
        with open(os.path.join(self.folder, "receipt.json"), "w") as f:
            json.dump(self.receipt, f)

    def unclaimed(self):
        rows, _ = coverage.coverage_audit([], verbose=False)
        return {row["path"] for row in rows}

    def test_hash_verified_snapshot_is_archival(self):
        self.assertTrue(coverage._verified_repair_snapshot(self.path))
        self.assertNotIn(self.path, self.unclaimed())

    def test_changed_snapshot_remains_unclaimed(self):
        with open(self.path, "w") as f:
            json.dump({"cash": 9999, "positions": {}}, f)
        self.assertFalse(coverage._verified_repair_snapshot(self.path))
        self.assertIn(self.path, self.unclaimed())

    def test_unlisted_new_ledger_in_same_directory_is_not_ignored(self):
        added = os.path.join(self.folder, "unregistered.json")
        with open(added, "w") as f:
            json.dump({"cash": 1, "positions": {}}, f)
        self.assertIn(added, self.unclaimed())
        self.assertNotIn(self.path, self.unclaimed())

    def test_unapplied_or_wrong_directory_receipt_does_not_exempt(self):
        self.receipt["applied"] = False
        self.write_receipt()
        self.assertIn(self.path, self.unclaimed())
        self.receipt["applied"] = True
        self.receipt["backup_directory"] = self.base
        self.write_receipt()
        self.assertIn(self.path, self.unclaimed())

    def test_missing_receipt_does_not_exempt(self):
        os.remove(os.path.join(self.folder, "receipt.json"))
        self.assertIn(self.path, self.unclaimed())


if __name__ == "__main__":
    unittest.main()
