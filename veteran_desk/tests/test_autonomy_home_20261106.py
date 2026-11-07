"""Autonomy Home summaries are evidence-backed live data, with safe missing/stale states."""
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "thf_home.py"
NOW = dt.datetime(2026, 11, 6, 8, 0, tzinfo=dt.timezone.utc)


class AutonomyHomeTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("autonomy_home_under_test", SOURCE)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.module.L = str(self.root / "live")
        self.path = Path(self.module.L) / "state/thf/autonomy/status.json"
        self.record = {
            "schema_version": 1,
            "updated_utc": NOW.isoformat(),
            "status": "green",
            "summary": {
                "learning": "Finished trades are being reviewed.",
                "teamwork": "Two AI services contributed to the last review.",
                "recovery": "No current recovery task is waiting.",
                "trading": "Practice trading is being checked."
            },
            "metrics": {
                "completed_trades": 10, "winning_trades": 4, "losing_trades": 5,
                "known_books": 2, "provider_count": 2, "parked_requests": 0, "open_circuits": 0
            },
            "details": {"technical_note": "Only visible on request"}
        }

    def write(self, record=None):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(record if record is not None else self.record), encoding="utf-8")

    def test_missing_status_is_waiting_and_does_not_create_files_or_invent_zero_counts(self):
        result = self.module.autonomy_status(NOW)
        self.assertEqual(result["status"], "amber")
        self.assertFalse(result["fresh"])
        self.assertFalse(result["available"])
        self.assertIsNone(result["metrics"]["completed_trades"])
        self.assertFalse(self.path.parent.exists())

    def test_fresh_report_preserves_observed_trade_counts_and_plain_summaries(self):
        self.write()
        result = self.module.autonomy_status(NOW)
        self.assertTrue(result["fresh"])
        self.assertEqual(result["status"], "green")
        self.assertEqual(result["metrics"]["completed_trades"], 10)
        self.assertEqual(result["metrics"]["winning_trades"], 4)
        self.assertEqual(result["metrics"]["losing_trades"], 5)
        self.assertEqual(result["summary"], self.record["summary"])
        self.assertEqual(result["details"], self.record["details"])

    def test_180_second_freshness_boundary_does_not_keep_an_old_green_status(self):
        self.record["updated_utc"] = (NOW - dt.timedelta(seconds=180)).isoformat()
        self.write()
        self.assertTrue(self.module.autonomy_status(NOW)["fresh"])
        self.record["updated_utc"] = (NOW - dt.timedelta(seconds=181)).isoformat()
        self.write()
        result = self.module.autonomy_status(NOW)
        self.assertFalse(result["fresh"])
        self.assertEqual(result["status"], "amber")
        self.assertEqual(result["metrics"]["completed_trades"], 10)
        self.assertNotIn("contributed", result["summary"]["teamwork"])
        self.assertIn("Waiting for a fresh", result["checked"])

    def test_red_current_report_remains_red(self):
        self.record["status"] = "red"
        self.write()
        result = self.module.autonomy_status(NOW)
        self.assertTrue(result["fresh"])
        self.assertEqual(result["status"], "red")

    def test_missing_or_bad_counts_never_become_zero_or_healthy(self):
        for value in (None, -1, True, 2.5, "5"):
            with self.subTest(value=value):
                self.record["metrics"]["provider_count"] = value
                self.write()
                result = self.module.autonomy_status(NOW)
                self.assertEqual(result["status"], "amber")
                self.assertFalse(result["fresh"])
                self.assertIsNone(result["metrics"]["provider_count"])

    def test_wins_and_losses_cannot_exceed_completed_trades(self):
        self.record["metrics"]["winning_trades"] = 9
        self.write()
        result = self.module.autonomy_status(NOW)
        self.assertFalse(result["fresh"])
        self.assertIsNone(result["metrics"]["completed_trades"])

    def test_invalid_json_schema_or_naive_clock_is_waiting(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{broken")
        self.assertFalse(self.module.autonomy_status(NOW)["fresh"])
        for version in (True, "1", 2):
            self.record["schema_version"] = version
            self.write()
            self.assertFalse(self.module.autonomy_status(NOW)["fresh"])
        self.record["schema_version"] = 1
        self.record["updated_utc"] = NOW.replace(tzinfo=None).isoformat()
        self.write()
        self.assertFalse(self.module.autonomy_status(NOW)["fresh"])

    def test_future_clock_and_unreadable_or_oversized_reports_cannot_claim_ready(self):
        self.record["updated_utc"] = (NOW + dt.timedelta(seconds=31)).isoformat()
        self.write()
        result = self.module.autonomy_status(NOW)
        self.assertFalse(result["fresh"])
        self.assertIn("clock", result["checked"])
        with patch("builtins.open", side_effect=PermissionError("test unreadable")):
            self.assertFalse(self.module.autonomy_status(NOW)["fresh"])
        self.path.write_text(" " * 131073)
        self.assertFalse(self.module.autonomy_status(NOW)["fresh"])

    def test_reader_uses_live_data_root_and_keeps_accepted_code_assets_separate(self):
        mirror = self.root / "accepted_mirror"
        misleading = mirror / "state/thf/autonomy/status.json"
        misleading.parent.mkdir(parents=True)
        misleading.write_text(json.dumps(self.record))
        self.module.CODE_ROOT = str(mirror)
        self.assertFalse(self.module.autonomy_status(NOW)["fresh"])
        self.write()
        self.assertTrue(self.module.autonomy_status(NOW)["fresh"])
        self.assertEqual(Path(self.module.TOOLS), SOURCE.parents[1] / "tools")

    def test_home_includes_the_same_verified_autonomy_report(self):
        self.write()
        replacements = dict(
            health_rows=lambda *a, **k: [], overall=lambda rows: {"color": "green", "n_amber": 0, "n_red": 0, "reason": "test"},
            brains_words=lambda *a, **k: {}, doing_words=lambda *a, **k: "test",
            markets_words=lambda *a, **k: "test", numbers_light=lambda *a, **k: {},
            armed_waiting_open=lambda *a, **k: set(), kill_fix=lambda *a, **k: []
        )
        with patch.multiple(self.module, **replacements):
            home = self.module.home({}, hb={}, ais=[], tick={}, now=NOW, news={})
        self.assertEqual(home["autonomy"]["metrics"]["completed_trades"], 10)
        self.assertTrue(home["autonomy"]["fresh"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
