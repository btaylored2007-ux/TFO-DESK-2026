from __future__ import annotations
import importlib.util as _ilu
if _ilu.find_spec("fundamental_coverage_ledger_v2") is None:
    import pytest as _pt
    _pt.skip("fundamental_coverage_ledger_v2 is not in the code-only export. See TEST_TRIAGE.md.", allow_module_level=True)

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fundamental_coverage_ledger_v2 import build_ledger
from fundamental_discovery_common import file_sha256


NOW = dt.datetime(2026, 9, 8, 6, tzinfo=dt.timezone.utc)
BLOCK = """RUN SUMMARY: 1 screened | 0 undervalued
SYMBOL: TEST
EXCHANGE: NYSE
US_LISTED: yes
SECTOR: technology
VERDICT: fairly_priced
CONFIDENCE: medium
WHY CHEAP: The discount has disappeared after audited earnings improved.
CHEAPNESS: No remaining historical discount after the capital return.
DIRECTION: Improving across the last five annual filings.
METRICS: FCF yield 5 percent measured 2026-08-27.
CATALYST: Annual results scheduled for 2026-11-03.
KILLS IT: Revenue growth reverses at the next quarterly release.
SOURCES: https://example.com/filing-2026-08-27
TRADE DIRECTION: NEITHER
"""


class FrontierReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "hunt_coverage").mkdir()
        (self.root / "hunt").mkdir()
        self.source = self.root / "hunt/blocks_test.md"
        self.source.write_text(BLOCK)
        self.legacy = {"covered": [{"frontier": "Example", "date": "2026-08-27", "screened": 1,
                                    "files": [str(self.source)]}], "never_covered": [], "blocked_data": []}

    def build(self, lifecycle=None):
        (self.root / "hunt_coverage/coverage.json").write_text(json.dumps(self.legacy))
        if lifecycle is not None:
            (self.root / "hunt_coverage/frontier_lifecycle.json").write_text(json.dumps(lifecycle))
        return build_ledger(self.root, now=NOW)

    def test_default_source_reconciles_and_routes_oldest_without_changing_partitions(self):
        ledger = self.build()
        row = ledger["frontiers"][0]
        self.assertTrue(row["reconciled"])
        self.assertTrue(row["eligible"])
        self.assertEqual(row["state"], "stale_revisit")
        self.assertEqual(row["last_completed_date"], "2026-08-27")
        self.assertEqual(row["completion_scope"], "legacy_screening_batch")
        self.assertEqual(row["screening_evidence"]["partition"], ledger["files"][0]["partition"])
        self.assertEqual(row["evidence"][0]["sha256"], file_sha256(self.source))

    def test_blocked_data_overrides_never_covered_alias_and_lifecycle_ready(self):
        self.legacy["never_covered"] = ["Turkey small cap", "Vietnam / Philippines"]
        self.legacy["blocked_data"] = [{"frontier": "Turkey (BIST)", "reason": "real-terms method missing"},
                                       {"frontier": "Vietnam / Philippines", "reason": "no source"}]
        ledger = self.build({"frontiers": [{"frontier": "Turkey (BIST)", "state": "ready"}]})
        rows = [r for r in ledger["frontiers"] if r["frontier"] != "Example"]
        self.assertTrue(all(r["state"] == "blocked_data" and not r["eligible"] for r in rows))

    def test_retry_restriction_survives_rebuild_and_expires_at_exact_instant(self):
        lifecycle = {"frontiers": [{"frontier": "Example", "state": "retry_after", "retry_after_utc": "2026-09-08T06:00:01Z"}]}
        row = self.build(lifecycle)["frontiers"][0]
        self.assertEqual(row["state"], "retry_after")
        self.assertFalse(row["eligible"])
        self.assertFalse(build_ledger(self.root, now=NOW)["frontiers"][0]["eligible"])
        lifecycle["frontiers"][0]["retry_after_utc"] = "2026-09-08T06:00:00Z"
        self.assertTrue(self.build(lifecycle)["frontiers"][0]["eligible"])

    def test_invalid_retry_cannot_become_ready(self):
        for invalid in (None, "tomorrow", "2026-09-08T07:00:00"):
            with self.subTest(invalid=invalid):
                row = self.build({"frontiers": [{"frontier": "Example", "state": "retry_after", "retry_after_utc": invalid}]})["frontiers"][0]
                self.assertFalse(row["reconciled"])
                self.assertFalse(row["eligible"])

    def test_in_progress_shadow_is_not_expired_by_age_or_ready_assertion(self):
        self.legacy["ledger_reconciliation_2026-09-03"] = {"truncated_zero_block_frontiers": [{"frontier": "Example", "blocks_parsed": 0}]}
        row = self.build({"frontiers": [{"frontier": "Example", "state": "ready"}]})["frontiers"][0]
        self.assertEqual(row["state"], "in_progress")
        self.assertFalse(row["eligible"])
        self.assertTrue(row["excludes"])

    def test_new_matching_completed_batch_supersedes_old_attempt(self):
        self.legacy["ledger_reconciliation_2026-09-03"] = {"truncated_zero_block_frontiers": [{"frontier": "Example", "blocks_parsed": 0}]}
        self.legacy["covered"][0]["date"] = "2026-09-04"
        self.assertTrue(self.build()["frontiers"][0]["eligible"])

    def test_denominator_mismatch_and_header_stub_never_complete(self):
        self.legacy["covered"][0]["screened"] = 2
        self.assertFalse(self.build()["frontiers"][0]["reconciled"])
        self.source.write_text("# A research job started but never wrote a block\n")
        self.legacy["covered"][0]["screened"] = 0
        row = self.build()["frontiers"][0]
        self.assertFalse(row["eligible"])
        self.assertIsNone(row["last_completed_date"])

    def test_manifest_hash_mutation_is_a_routing_block(self):
        manifest = self.root / "hunt/manifest.json"
        manifest.write_text(json.dumps({"status": "complete", "quarantine_reasons": [], "block_count": 1,
                                        "files": [{"path": str(self.source), "sha256": file_sha256(self.source)}]}))
        self.legacy["covered"][0]["manifest"] = str(manifest)
        del self.legacy["covered"][0]["files"]  # UK-style legacy row gives only the manifest.
        self.assertTrue(self.build()["frontiers"][0]["eligible"])
        self.source.write_text(BLOCK + "\nChanged after sealing.\n")
        self.assertFalse(self.build()["frontiers"][0]["eligible"])

    def test_malformed_input_blocks_selection_without_dropping_canonical_counts(self):
        self.legacy["never_covered"] = [17]
        ledger = self.build()
        self.assertEqual(ledger["totals"]["represented_symbol_blocks"], 1)
        self.assertEqual(ledger["frontier_reconciliation"]["status"], "blocked")
        self.assertIsNone(ledger["frontier_reconciliation"]["selected_frontier"])

    def test_never_covered_has_ledger_evidence_and_precedes_previous_history(self):
        self.legacy["never_covered"] = [{"frontier": "Untouched", "pool_size": 30}]
        ledger = self.build()
        self.assertEqual(ledger["frontier_reconciliation"]["selected_frontier"], "Untouched")
        row = next(r for r in ledger["frontiers"] if r["frontier"] == "Untouched")
        self.assertEqual(row["state"], "ready")
        self.assertTrue(row["evidence"])

    def test_repeated_coverage_uses_latest_batch_and_preserves_history(self):
        latest = dict(self.legacy["covered"][0], date="2026-09-04")
        self.legacy["covered"].append(latest)
        ledger = self.build()
        self.assertEqual(len(ledger["frontiers"]), 1)
        self.assertEqual(ledger["frontiers"][0]["last_completed_date"], "2026-09-04")
        self.assertEqual(len(ledger["frontiers"][0]["previous_legacy_records"]), 1)


if __name__ == "__main__":
    unittest.main()
