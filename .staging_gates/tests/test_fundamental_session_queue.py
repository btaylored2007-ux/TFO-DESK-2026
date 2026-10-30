import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fundamental_session_queue as mod


TODAY = dt.date.today()
FUTURE = (TODAY + dt.timedelta(days=30)).isoformat()
REVIEW = (TODAY + dt.timedelta(days=45)).isoformat()
HEX = {
    "batch_sha256": "aa" * 32,
    "research_manifest_sha256": "bb" * 32,
    "allocator_receipt_sha256": "cc" * 32,
}


def candidate():
    return {
        "stable_issuer_id": "LEI:TEST",
        "symbol": "TEST.AX",
        "economic_direction": "LONG",
        "execution_status": "pending_session",
        "execution_symbol": "TEST.AX",
        "execution_venue": "ASX",
        "quote_currency": "AUD",
        "notional_usd": 1000,
        "sector": "materials",
        "catalyst_date": FUTURE,
        "review_date": REVIEW,
        "event_date": FUTURE,
        "measurement_period_end": TODAY.isoformat(),
        "thesis_review_date": REVIEW,
        "thesis": "Signed event. KILLS IT: issuer withdraws the event.",
        "execution_route": "direct_local",
        "research_symbol": "TEST",
        "research_features": [],
        "research_feature_receipts": [],
    }


def manifest():
    return {
        "schema": mod.EXECUTION_MANIFEST_SCHEMA,
        "status": "complete",
        "paper_only": True,
        "as_of": TODAY.isoformat(),
        "batch_id": "current-test",
        **HEX,
        "candidates": [candidate()],
        "candidate_held_out": [],
        "unmatched_allocations": [],
        "conflicting_symbols_held_out": [],
        "conflicting_issuers_held_out": [],
    }


class QueueTests(unittest.TestCase):
    def _build(self, value=None):
        with mock.patch.object(mod, "_verified_execution_manifest"):
            return mod.build_queue(value or manifest(), as_of=TODAY)

    def _verify(self, queue, gate):
        with mock.patch.object(mod, "_verified_execution_manifest"):
            return mod.verify_queue(queue, gate=gate, as_of=TODAY)

    def test_manifest_schema_status_paper_date_and_digests_are_stop_ships(self):
        cases = {
            "schema": "wrong",
            "status": "in_progress",
            "paper_only": False,
            "as_of": (TODAY - dt.timedelta(days=1)).isoformat(),
            "batch_sha256": "not-a-hash",
            "research_manifest_sha256": "1" * 63,
            "allocator_receipt_sha256": None,
        }
        for field, bad in cases.items():
            with self.subTest(field=field):
                value = manifest()
                value[field] = bad
                with self.assertRaises(ValueError):
                    mod._verified_execution_manifest(value, TODAY)

    def test_long_only_and_exact_candidate_fields(self):
        value = manifest()
        value["candidates"][0]["economic_direction"] = "SHORT"
        with self.assertRaisesRegex(ValueError, "must be LONG"):
            self._build(value)
        value = manifest()
        del value["candidates"][0]["research_features"]
        with self.assertRaisesRegex(ValueError, "missing required"):
            self._build(value)

    def test_closed_venue_stays_pending(self):
        queue = self._build()
        result = self._verify(
            queue,
            lambda *args: (None, None, "venue is not in a REGULAR session (marketState=CLOSED)"),
        )
        self.assertEqual(result["candidates"][0]["state"], "PENDING_SESSION")

    def test_fresh_complete_gate_pass_only_advances_to_dry_run(self):
        queue = self._build()
        result = self._verify(
            queue,
            lambda *args: ({"px": 10}, {"market_state": "REGULAR",
                                          "measured": {"spread_pct": 0.001}}, None),
        )
        self.assertEqual(result["candidates"][0]["state"], "READY_FOR_DRY_RUN")
        self.assertNotIn("OPENED", result["counts"])

    def test_missing_or_partial_gate_outputs_are_refused(self):
        for gate in (
            lambda *args: (None, None, None),
            lambda *args: ({"px": 10}, {"market_state": "REGULAR", "measured": {}}, None),
        ):
            with self.subTest(gate=gate):
                result = self._verify(self._build(), gate)
                self.assertEqual(result["candidates"][0]["state"], "REFUSED_FRESH_GATE")

    def test_nonfuture_candidate_is_rejected_before_gate(self):
        value = manifest()
        value["candidates"][0]["catalyst_date"] = TODAY.isoformat()
        value["candidates"][0]["event_date"] = TODAY.isoformat()
        with self.assertRaisesRegex(ValueError, "not future"):
            self._build(value)

    def test_queue_receipt_binds_manifest_digests_and_candidate_set(self):
        value = manifest()
        queue = self._build(value)
        self.assertEqual(queue["schema"], mod.QUEUE_SCHEMA)
        self.assertEqual(queue["source_execution_manifest_sha256"], mod._json_sha256(value))
        for field, expected in HEX.items():
            self.assertEqual(queue[field], expected)
        self.assertEqual(
            queue["queue_receipt_sha256"], mod._json_sha256(mod._queue_binding_payload(queue))
        )

    def test_injected_or_mutated_queued_candidate_is_refused_before_gate(self):
        gate = mock.Mock(return_value=({"px": 10}, {"market_state": "REGULAR",
                                                     "measured": {"spread_pct": 0.1}}, None))
        injected = self._build()
        injected["candidates"].append(copy.deepcopy(injected["candidates"][0]))
        with self.assertRaises(ValueError):
            self._verify(injected, gate)
        gate.assert_not_called()

        mutated = self._build()
        mutated["candidates"][0]["sector"] = "energy"
        with self.assertRaises(ValueError):
            self._verify(mutated, gate)
        gate.assert_not_called()

    def test_verified_filing_feature_receipt_is_exact_and_rechecked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "filing.json"
            record = {
                "status": "COMPLETE",
                "cik": "1",
                "latest_known_on": TODAY.isoformat(),
                "research_queue": True,
                "material_changes": [{"status": "MATERIAL_LEAD", "kind": "numeric"}],
                "source_receipts": {
                    "prior": {
                        "snapshot_sha256": "11" * 32,
                        "primary_source_url": "https://sec.example/prior",
                        "known_on": TODAY.isoformat(),
                        "cik": "0001",
                    },
                    "latest": {
                        "snapshot_sha256": "22" * 32,
                        "primary_source_url": "https://sec.example/latest",
                        "known_on": TODAY.isoformat(),
                        "cik": "0001",
                    },
                },
            }
            path.write_text(json.dumps(record), encoding="utf-8")
            value = manifest()
            row = value["candidates"][0]
            row["stable_issuer_id"] = "CIK:0001"
            row["research_features"] = ["filing_diff"]
            row["research_feature_receipts"] = [{
                "feature": "filing_diff",
                "path": str(path.resolve()),
                "sha256": mod._file_sha256(path),
                "record_sha256": mod._json_sha256(record),
                "evidence_status": "COMPLETE",
                "evidence_date": TODAY.isoformat(),
                "raw_observation": None,
            }]
            queue = self._build(value)
            self.assertEqual(queue["candidates"][0]["research_features"], ["filing_diff"])
            path.write_text(json.dumps({**record, "status": "COVERAGE_FAILURE"}), encoding="utf-8")
            gate = mock.Mock()
            with self.assertRaisesRegex(ValueError, "file hash mismatch"):
                self._verify(queue, gate)
            gate.assert_not_called()

    def test_no_change_filing_control_cannot_claim_signal_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "filing.json"
            record = {
                "status": "COMPLETE",
                "cik": "1",
                "latest_known_on": TODAY.isoformat(),
                "research_queue": False,
                "material_changes": [],
            }
            path.write_text(json.dumps(record), encoding="utf-8")
            row = candidate()
            row["stable_issuer_id"] = "CIK:0001"
            row["research_features"] = ["filing_diff"]
            row["research_feature_receipts"] = [{
                "feature": "filing_diff",
                "path": str(path.resolve()),
                "sha256": mod._file_sha256(path),
                "record_sha256": mod._json_sha256(record),
                "evidence_status": "COMPLETE",
                "evidence_date": TODAY.isoformat(),
                "raw_observation": None,
            }]
            with self.assertRaisesRegex(ValueError, "MATERIAL_LEAD"):
                mod._verify_feature_receipts(row, TODAY)

    def test_feature_ids_and_complete_receipt_fields_are_exact(self):
        value = manifest()
        row = value["candidates"][0]
        row["research_features"] = ["event_queue", "filing_diff"]
        row["research_feature_receipts"] = []
        with self.assertRaisesRegex(ValueError, "exactly match"):
            self._build(value)
        value = manifest()
        row = value["candidates"][0]
        row["research_features"] = ["RESEARCH_READY"]
        row["research_feature_receipts"] = []
        with self.assertRaises(ValueError):
            self._build(value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
