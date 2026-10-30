import json
import tempfile
import unittest
from pathlib import Path

import fundamental_feature_attribution as attribution


class FeatureAttributionTests(unittest.TestCase):
    def _provenance(self, features, suffix="a"):
        return {
            "features": features,
            "manifest_sha256": suffix * 64,
            "queue_receipt_sha256": suffix * 64,
            "batch_sha256": suffix * 64,
            "research_manifest_sha256": suffix * 64,
            "allocator_receipt_sha256": suffix * 64,
            "queue_receipt_path": "/tmp/immutable-queue.json",
            "stable_issuer_id": "lei:" + suffix,
        }

    def test_open_and_closed_feature_receipts_are_attributed_without_guessing(self):
        payload = {
            "positions": {
                "OPEN": {
                    "notional_usd": 10000,
                    "unrealised_usd": 125,
                    "exit_fills": [{"realised_usd": 25}],
                    "research_provenance": self._provenance(
                        ["filing_diff", "bank_v1"], "a"),
                },
                "LEGACY": {"notional_usd": 1000, "unrealised_usd": 10},
            },
            "closed": {
                "CLOSED": {
                    "unrealised_usd": 999,
                    "close_record": {"realised_usd": -40},
                    "notional_usd": 0,
                    "research_provenance": self._provenance(["bank_v1"], "b"),
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            ledger.write_text(json.dumps(payload), encoding="utf-8")
            report = attribution.build(ledger, as_of_utc="2026-09-02T00:00:00Z")
        rows = {row["feature"]: row for row in report["features"]}
        self.assertEqual(rows["bank_v1"]["entries"], 2)
        self.assertEqual(rows["bank_v1"]["realised_usd"], -15.0)
        self.assertEqual(rows["bank_v1"]["unrealised_usd"], 125.0)
        self.assertEqual(rows["bank_v1"]["total_pnl_usd"], 110.0)
        self.assertEqual(rows["bank_v1"]["entry_notional_usd"], 10000.0)
        self.assertEqual(rows["bank_v1"]["total_return_pct"], 1.1)
        self.assertEqual(rows["bank_v1"]["winning_positions"], 1)
        self.assertEqual(rows["bank_v1"]["losing_positions"], 1)
        self.assertEqual(rows["filing_diff"]["entries"], 1)
        self.assertEqual(report["unattributed_positions"], ["LEGACY"])

    def test_partial_receipt_is_invalid_not_inferred(self):
        payload = {
            "positions": {
                "BAD": {"research_provenance": {"features": ["event_queue"]}},
            },
            "closed": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            ledger.write_text(json.dumps(payload), encoding="utf-8")
            report = attribution.build(ledger)
        self.assertEqual(report["invalid_provenance_receipts"], ["BAD"])
        rows = {row["feature"]: row for row in report["features"]}
        self.assertEqual(rows["event_queue"]["entries"], 0)

    def test_bound_general_research_is_unattributed_with_a_valid_receipt(self):
        payload = {"positions": {"GENERAL": {
            "notional_usd": 2500, "unrealised_usd": 25,
            "research_provenance": self._provenance([]),
        }}, "closed": {}}
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            ledger.write_text(json.dumps(payload), encoding="utf-8")
            report = attribution.build(ledger)
        self.assertEqual(report["unattributed_positions"], ["GENERAL"])
        self.assertEqual(report["invalid_provenance_receipts"], [])
        self.assertTrue(all(row["entries"] == 0 for row in report["features"]))

    def test_bad_hash_or_missing_queue_binding_is_invalid(self):
        bad = self._provenance(["filing_diff"])
        bad["manifest_sha256"] = "not-a-hash"
        bad.pop("queue_receipt_path")
        payload = {"positions": {"BAD": {"research_provenance": bad}}, "closed": {}}
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            ledger.write_text(json.dumps(payload), encoding="utf-8")
            report = attribution.build(ledger)
        self.assertEqual(report["invalid_provenance_receipts"], ["BAD"])

    def test_nonfinite_pnl_is_quarantined_not_flat(self):
        payload = {
            "positions": {
                "NAN": {
                    "notional_usd": 1000,
                    "unrealised_usd": float("nan"),
                    "research_provenance": self._provenance(["bank_v1"]),
                }
            },
            "closed": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            ledger.write_text(json.dumps(payload), encoding="utf-8")
            report = attribution.build(ledger)
        self.assertEqual(report["invalid_performance_positions"][0]["symbol"], "NAN")
        row = next(row for row in report["features"] if row["feature"] == "bank_v1")
        self.assertEqual(row["entries"], 0)
        self.assertEqual(row["flat_positions"], 0)


if __name__ == "__main__":
    unittest.main()
