import unittest

import fundamental_filing_diff as mod


def companyfacts():
    def fact(tag, unit, rows):
        return {tag: {"units": {unit: rows}}}
    facts = {}
    facts.update(fact("Revenues", "USD", [
        {"start": "2025-01-01", "end": "2025-03-31", "val": 100, "filed": "2025-05-01", "form": "10-Q", "accn": "a"},
        {"start": "2026-01-01", "end": "2026-03-31", "val": 120, "filed": "2026-05-01", "form": "10-Q", "accn": "b"},
        {"start": "2026-01-01", "end": "2026-03-31", "val": 999, "filed": "2026-10-01", "form": "10-Q", "accn": "restated"},
    ]))
    facts.update(fact("CommonStockSharesOutstanding", "shares", [
        {"end": "2025-03-31", "val": 100, "filed": "2025-05-01", "form": "10-Q", "accn": "a"},
        {"end": "2026-03-31", "val": 110, "filed": "2026-05-01", "form": "10-Q", "accn": "b"},
    ]))
    return {"entityName": "Test Inc", "facts": {"us-gaap": facts}}


class FilingDiffTests(unittest.TestCase):
    def test_snapshot_excludes_later_restatement(self):
        snap = mod.build_snapshot(companyfacts(), "1", "2026-06-01")
        self.assertEqual(snap["metrics"]["revenue"]["value"], 120.0)
        self.assertEqual(snap["metrics"]["revenue"]["accession"], "b")

    def test_newer_fallback_tag_beats_stale_lead_tag(self):
        facts = companyfacts()
        facts["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"] = {
            "units": {"USD": [{
                "start": "2024-01-01", "end": "2024-03-31", "val": 80,
                "filed": "2024-05-01", "form": "10-Q", "accn": "old-lead",
            }]}
        }
        snap = mod.build_snapshot(facts, "1", "2026-06-01")
        self.assertEqual(snap["metrics"]["revenue"]["tag"], "Revenues")
        self.assertEqual(snap["metrics"]["revenue"]["accession"], "b")

    def test_short_term_borrowings_is_a_canonical_current_debt_tag(self):
        facts = companyfacts()
        facts["facts"]["us-gaap"]["ShortTermBorrowings"] = {
            "units": {"USD": [{
                "end": "2026-03-31", "val": 25,
                "filed": "2026-05-01", "form": "10-Q", "accn": "debt-1",
            }]}
        }
        snap = mod.build_snapshot(facts, "1", "2026-06-01")
        self.assertEqual(snap["metrics"]["debt"]["tag"], "ShortTermBorrowings")

    def test_material_comparable_change_is_research_lead_only(self):
        prior = mod.build_snapshot(companyfacts(), "1", "2025-06-01")
        latest = mod.build_snapshot(companyfacts(), "1", "2026-06-01")
        diff = mod.compare_snapshots(prior, latest)
        self.assertTrue(diff["research_queue"])
        self.assertFalse(diff["capital_eligible"])
        self.assertTrue(any(x.get("concept") == "shares" for x in diff["material_changes"]))

    def test_event_after_known_on_is_coverage_failure(self):
        prior = mod.build_snapshot(companyfacts(), "1", "2025-06-01")
        latest = mod.build_snapshot(companyfacts(), "1", "2026-06-01", events={
            "buyback_status": {"status": "started", "filed_date": "2026-07-01",
                               "source_url": "https://sec.example/filing"}
        })
        self.assertEqual(mod.compare_snapshots(prior, latest)["status"], "COVERAGE_FAILURE")

    def test_mismatched_duration_is_not_directional(self):
        p = {"kind": "duration", "unit": "USD", "start": "2025-01-01", "end": "2025-03-31"}
        l = {"kind": "duration", "unit": "USD", "start": "2026-01-01", "end": "2026-06-30"}
        self.assertFalse(mod.comparable(p, l))

    def test_prior_future_filed_metric_fails_closed_without_leads(self):
        prior = mod.build_snapshot(companyfacts(), "1", "2025-06-01")
        latest = mod.build_snapshot(companyfacts(), "1", "2026-06-01")
        prior["metrics"]["revenue"]["filed"] = "2025-06-02"
        diff = mod.compare_snapshots(prior, latest)
        self.assertEqual(diff["status"], "COVERAGE_FAILURE")
        self.assertFalse(diff["research_queue"])
        self.assertEqual(diff["material_changes"], [])
        self.assertTrue(any("prior metric revenue" in reason for reason in diff["validation_errors"]))

    def test_latest_future_filed_metric_fails_closed_without_leads(self):
        prior = mod.build_snapshot(companyfacts(), "1", "2025-06-01")
        latest = mod.build_snapshot(companyfacts(), "1", "2026-06-01")
        latest["metrics"]["shares"]["filed"] = "2026-06-02"
        diff = mod.compare_snapshots(prior, latest)
        self.assertEqual(diff["status"], "COVERAGE_FAILURE")
        self.assertFalse(diff["research_queue"])
        self.assertEqual(diff["material_changes"], [])

    def test_wrong_normalized_kind_or_unit_and_hash_tamper_fail_closed(self):
        prior = mod.build_snapshot(companyfacts(), "1", "2025-06-01")
        latest = mod.build_snapshot(companyfacts(), "1", "2026-06-01")
        latest["metrics"]["revenue"].update({"kind": "instant", "unit": "shares"})
        diff = mod.compare_snapshots(prior, latest)
        self.assertEqual(diff["status"], "COVERAGE_FAILURE")
        self.assertFalse(diff["research_queue"])
        self.assertEqual(diff["material_changes"], [])
        self.assertTrue(any("kind/unit schema" in reason for reason in diff["validation_errors"]))
        self.assertTrue(any("content hash mismatch" in reason for reason in diff["validation_errors"]))

    def test_complete_diff_binds_primary_source_snapshot_hashes(self):
        prior = mod.build_snapshot(companyfacts(), "1", "2025-06-01")
        latest = mod.build_snapshot(companyfacts(), "1", "2026-06-01")
        diff = mod.compare_snapshots(prior, latest)
        self.assertEqual(diff["source_receipts"]["prior"]["snapshot_sha256"],
                         prior["snapshot_sha256"])
        self.assertEqual(diff["source_receipts"]["latest"]["snapshot_sha256"],
                         latest["snapshot_sha256"])


if __name__ == "__main__":
    unittest.main()
