import datetime as dt
import json
import os
import tempfile
import unittest

from desk_risk_health import check_risk_reports


class RiskReportHealthTests(unittest.TestCase):
    def check(self, age=0, readable=35, total=35, level="OK", stamp=None):
        now = 1800000000
        stamp = stamp or dt.datetime.fromtimestamp(now - age, dt.timezone.utc).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            payloads = {
                "firm_risk.json": {"utc": stamp, "level": level,
                                   "books_readable": readable, "books_total": total},
                "risk_report.json": {"generated": stamp, "coverage": {
                    "n_books_readable": readable, "n_books": total}},
            }
            for name, payload in payloads.items():
                with open(os.path.join(tmp, name), "w") as f:
                    json.dump(payload, f)
            return check_risk_reports(tmp, now=now)

    def test_current_complete_reports_are_healthy(self):
        self.assertEqual(self.check()[1], [])

    def test_old_embedded_timestamp_cannot_be_hidden_by_fresh_file(self):
        reports, errors = self.check(age=5 * 3600)
        self.assertEqual(len(errors), 2)
        self.assertTrue(reports["firm_risk.json"]["stale"])

    def test_future_timestamp_fails(self):
        self.assertEqual(len(self.check(age=-3600)[1]), 2)

    def test_partial_coverage_is_not_an_all_clear(self):
        self.assertEqual(len(self.check(readable=34)[1]), 2)

    def test_unmeasured_status_is_loud_even_with_complete_counts(self):
        self.assertEqual(len(self.check(level="UNMEASURED")[1]), 1)

    def test_missing_and_bad_reports_are_loud(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(len(check_risk_reports(tmp)[1]), 2)
        self.assertEqual(len(self.check(stamp="invalid")[1]), 2)


if __name__ == "__main__":
    unittest.main()
