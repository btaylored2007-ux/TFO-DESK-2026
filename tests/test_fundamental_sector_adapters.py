import json
import tempfile
import unittest
from pathlib import Path

import fundamental_sector_adapters as mod


def record(adapter_id="resources_v1"):
    spec = mod.ADAPTERS[adapter_id]
    metrics = {
        name: {"value": 1.0, "source_url": "https://issuer.example/filing", "filed_date": "2026-08-31"}
        for name in spec.required_metrics
    }
    return {
        "adapter_id": adapter_id,
        "stable_issuer_id": "LEI:TEST123",
        "symbol": "TEST.AX",
        "as_of_date": "2026-09-02",
        "metrics": metrics,
        "valuation": {"primary_metric": spec.required_metrics[0], "own_history_percentile": 12.0,
                      "current_regime_percentile": 15.0},
        "history": {"years_span": 10.0, "n_observations": 40, "full_cycle": True},
        "catalyst": {"event_date": "2026-12-01", "review_date": "2026-12-02",
                     "direction": "bullish", "status": "alive",
                     "source_url": "https://issuer.example/event"},
    }


class AdapterTests(unittest.TestCase):
    def test_complete_specialist_record_is_research_only(self):
        result = mod.evaluate(record())
        self.assertEqual(result["status"], "RESEARCH_READY")
        self.assertFalse(result["capital_eligible"])

    def test_fvtpl_roe_cannot_drive_investment_entity(self):
        row = record("investment_entity_v1")
        row["valuation"]["primary_metric"] = "ifrs_fvtpl_roe"
        result = mod.evaluate(row)
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        self.assertTrue(any("forbidden" in x for x in result["reasons"]))

    def test_stale_or_killed_catalyst_fails(self):
        row = record()
        row["catalyst"]["status"] = "killed"
        row["catalyst"]["event_date"] = "2026-08-01"
        self.assertEqual(mod.evaluate(row)["status"], "INSUFFICIENT_EVIDENCE")

    def test_metric_filed_after_as_of_is_rejected(self):
        row = record()
        metric = mod.ADAPTERS[row["adapter_id"]].required_metrics[0]
        row["metrics"][metric]["filed_date"] = "2026-09-03"
        result = mod.evaluate(row)
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        self.assertTrue(any("filed after as_of_date" in reason for reason in result["reasons"]))

    def test_object_metric_values_are_rejected_even_when_nonempty(self):
        for object_value in ({}, {"reported": 1.0}):
            with self.subTest(value=object_value):
                row = record()
                metric = mod.ADAPTERS[row["adapter_id"]].required_metrics[0]
                row["metrics"][metric]["value"] = object_value
                result = mod.evaluate(row)
                self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
                self.assertTrue(any("no usable value" in reason for reason in result["reasons"]))

    def test_na_like_scalar_and_list_metric_values_are_rejected(self):
        for unusable in ("N/A", "not applicable", ["N/A"], ["none", "unknown"]):
            with self.subTest(value=unusable):
                row = record()
                metric = mod.ADAPTERS[row["adapter_id"]].required_metrics[0]
                row["metrics"][metric]["value"] = unusable
                result = mod.evaluate(row)
                self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
                self.assertTrue(any("no usable value" in reason for reason in result["reasons"]))

    def test_impossible_history_span_and_cycle_claims_are_rejected(self):
        cases = ((0, True, "positive"), (-1, True, "positive"),
                 (2, True, "impossible"), (5, "yes", "boolean"))
        for years, full_cycle, expected in cases:
            with self.subTest(years=years, full_cycle=full_cycle):
                row = record()
                row["history"].update(years_span=years, full_cycle=full_cycle)
                result = mod.evaluate(row)
                self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
                self.assertTrue(any(expected in reason for reason in result["reasons"]))

    def test_forward_ledger_is_idempotent(self):
        result = mod.evaluate(record())
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "ledger.jsonl"
            self.assertTrue(mod.append_observation(result, ledger))
            self.assertFalse(mod.append_observation(result, ledger))
            rows = [json.loads(x) for x in ledger.read_text().splitlines()]
            self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
