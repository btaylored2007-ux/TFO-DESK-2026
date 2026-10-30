import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from fundamental_event_receipt import build_receipt


class EventReceiptTests(unittest.TestCase):
    def _files(self, root: Path, *, flags=None, needs_review=False):
        source = root / "blocks_test.md"
        source.write_text("SYMBOL: TEST\nSOURCES: https://issuer.example/filing\n")
        lifecycle = root / "lifecycle.json"
        payload = {
            "schema": "tfo.discovery.catalyst-lifecycle.v1",
            "as_of": "2026-09-02",
            "generated_utc": "2026-09-02T01:00:00Z",
            "events": [{
                "symbol": "TEST", "future_dates": ["2026-12-01"],
                "flags": flags or ["future"], "needs_review": needs_review,
                "source_path": str(source), "line": 1,
            }],
        }
        lifecycle.write_text(json.dumps(payload))
        return lifecycle

    def test_clean_future_event_gets_non_signal_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt = build_receipt(
                self._files(Path(tmp)), symbol="TEST",
                stable_issuer_id="LEI:TEST", event_date="2026-12-01",
                as_of=dt.date(2026, 9, 2),
            )
        self.assertEqual(receipt["status"], "EVIDENCE_READY")
        self.assertTrue(receipt["non_signal"])
        self.assertEqual(receipt["stable_issuer_id"], "LEI:TEST")
        self.assertEqual(len(receipt["event_sha256"]), 64)

    def test_review_or_killed_event_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            lifecycle = self._files(
                Path(tmp), flags=["future", "possible_killed"], needs_review=True)
            with self.assertRaisesRegex(ValueError, "not clean"):
                build_receipt(
                    lifecycle, symbol="TEST", stable_issuer_id="LEI:TEST",
                    event_date="2026-12-01", as_of=dt.date(2026, 9, 2),
                )

    def test_past_event_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            lifecycle = self._files(Path(tmp))
            with self.assertRaisesRegex(ValueError, "not future"):
                build_receipt(
                    lifecycle, symbol="TEST", stable_issuer_id="LEI:TEST",
                    event_date="2026-08-01", as_of=dt.date(2026, 9, 2),
                )


if __name__ == "__main__":
    unittest.main()
