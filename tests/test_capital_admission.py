import copy
import hashlib
from pathlib import Path
import unittest

import capital_admission as ca
import autonomous_deploy as ad


def passing_receipt():
    return {
        "evaluation": {"t_stat": 3.2, "n_effective": 60, "per_trade_bps": 12,
                       "deflated_sharpe": .98, "n_trials_searched": 5000,
                       "survives_deflation": True, "beats_benchmark": True},
        "beta_to_SPY": .1,
        "capital_admission": {"schema_version": 1, "paper_only": True,
            "checks": {name: {"passed": True, "evidence": str(Path(__file__).resolve()),
                              "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
                       for name in ca.REQUIRED_CHECKS}},
    }


class AdmissionTests(unittest.TestCase):
    def test_missing_or_changed_artifact_does_not_release_capital(self):
        for field, value in (("evidence", "does-not-exist"), ("sha256", "0" * 64),
                             ("evidence", "/etc/hosts")):
            receipt = passing_receipt()
            receipt["capital_admission"]["checks"]["liquidity"][field] = value
            self.assertFalse(ad.qualifies(receipt)[0])

    def test_complete_evidence_passes_existing_numeric_gate(self):
        self.assertEqual(ad.qualifies(passing_receipt()), (True, []))

    def test_old_or_nested_winner_cannot_release_capital(self):
        self.assertFalse(ad.qualifies({"plot": passing_receipt()})[0])
        self.assertFalse(ad.qualifies(passing_receipt()["evaluation"])[0])

    def test_each_missing_failed_or_unsupported_check_refuses(self):
        for name in ca.REQUIRED_CHECKS:
            for value in (None, {"passed": False}, {"passed": True}):
                r = passing_receipt()
                r["capital_admission"]["checks"][name] = value
                self.assertFalse(ad.qualifies(r)[0], name)

    def test_nan_inf_bool_and_negative_t_do_not_pass(self):
        for field in ("t_stat", "n_effective", "per_trade_bps", "deflated_sharpe", "n_trials_searched"):
            for value in (float("nan"), float("inf"), True, None):
                r = passing_receipt(); r["evaluation"][field] = value
                self.assertFalse(ad.qualifies(r)[0], (field, value))
        r = passing_receipt(); r["evaluation"]["t_stat"] = -5
        self.assertFalse(ad.qualifies(r)[0])

    def test_benchmark_and_distinctness_are_required(self):
        r = passing_receipt(); r["evaluation"]["beats_benchmark"] = False
        self.assertFalse(ad.qualifies(r)[0])
        r = passing_receipt(); r["capital_admission"]["checks"]["mechanism_distinct"]["passed"] = False
        self.assertFalse(ad.qualifies(r)[0])

    def test_favourable_nested_beta_cannot_mask_root(self):
        r = passing_receipt(); r["beta_to_SPY"] = .8
        r["plot"] = {"beta": 0}
        self.assertFalse(ad.qualifies(r)[0])

    def test_receipt_is_not_mutated(self):
        r = passing_receipt(); before = copy.deepcopy(r)
        ad.qualifies(r)
        self.assertEqual(r, before)


if __name__ == "__main__":
    unittest.main()
