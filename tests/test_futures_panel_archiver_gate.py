import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import futures_panel_archiver as mod


class FuturesGateTests(unittest.TestCase):
    @staticmethod
    def _archive(path, values=None):
        conn = sqlite3.connect(path)
        conn.execute("""CREATE TABLE panel(
            fsym TEXT, spot TEXT, ts INTEGER, oi REAL, funding REAL,
            funding_abs REAL, basis REAL, lsr REAL, liq REAL, aggr REAL,
            cvd REAL, buy_vol REAL, sell_vol REAL)""")
        if values is not None:
            conn.execute("INSERT INTO panel VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         ("PI_XBTUSD", "XBT/USD", 1700000000, *values))
        conn.commit()
        conn.close()

    def test_incomplete_metric_row_does_not_advance_clock(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("""CREATE TABLE panel(
            fsym TEXT, spot TEXT, ts INTEGER, oi REAL, funding REAL,
            funding_abs REAL, basis REAL, lsr REAL, liq REAL, aggr REAL,
            cvd REAL, buy_vol REAL, sell_vol REAL)""")
        values = [1.0] * 10
        conn.execute("INSERT INTO panel VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("PI_XBTUSD", "XBT/USD", 1700000000, *values))
        broken = list(values)
        # required field ``funding`` is the second value after oi
        broken[1] = None
        conn.execute("INSERT INTO panel VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("PI_XBTUSD", "XBT/USD", 1700001800, *broken))
        rows = mod._field_complete_rows(conn)
        self.assertEqual(len(rows), 1)
        ready = mod._readiness(rows)
        self.assertEqual(ready["liquid_median_days"], 0.0)
        self.assertFalse(ready["liquid_seven_complete"])

    def test_one_liquid_market_never_masquerades_as_seven(self):
        rows = [("PI_XBTUSD", "XBT/USD", 1700000000 + 1800 * i) for i in range(48)]
        ready = mod._readiness(rows)
        self.assertEqual(ready["liquid_median_days"], 0.0)
        self.assertFalse(ready["liquid_seven_complete"])
        self.assertEqual(len(ready["liquid_seven_missing"]), 6)

    def test_seven_contracts_for_one_market_are_still_one_market(self):
        rows = [
            ("PI_XBTUSD_%d" % contract, "XBT/USD", 1700000000 + 1800 * step)
            for contract in range(7)
            for step in range(48)
        ]
        ready = mod._readiness(sorted(rows))
        self.assertFalse(ready["liquid_seven_complete"])
        self.assertEqual(ready["liquid_median_days"], 0.0)

    def test_all_seven_complete_markets_advance_median(self):
        rows = [
            ("FUT_%d" % index, spot, 1700000000 + 1800 * step)
            for index, spot in enumerate(mod.LIQUID_SEVEN)
            for step in range(48)
        ]
        ready = mod._readiness(sorted(rows))
        self.assertTrue(ready["liquid_seven_complete"])
        self.assertEqual(ready["liquid_seven_missing"], [])
        self.assertAlmostEqual(ready["liquid_median_days"], 1.0)

    def test_unreadable_archive_atomically_overwrites_stale_ready(self):
        with tempfile.TemporaryDirectory() as td:
            report = Path(td) / "readiness.json"
            report.write_text(json.dumps({"four_hour_ts": {"ready": True}}))
            with mock.patch.object(mod, "ARC", str(Path(td) / "missing.sqlite")), \
                    mock.patch.object(mod, "GATE_REPORT", str(report)):
                self.assertEqual(mod.gate(), 1)
            persisted = json.loads(report.read_text())
            self.assertEqual(persisted["readiness_status"], "ERROR")
            self.assertEqual(persisted["error"]["code"], "ARCHIVE_UNREADABLE")
            self.assertFalse(persisted["four_hour_ts"]["ready"])
            self.assertFalse(persisted["twelve_hour_xs"]["ready"])

    def test_zero_complete_archive_atomically_overwrites_stale_ready(self):
        with tempfile.TemporaryDirectory() as td:
            archive = Path(td) / "archive.sqlite"
            report = Path(td) / "readiness.json"
            report.write_text(json.dumps({"four_hour_ts": {"ready": True}}))
            incomplete = [1.0] * 10
            incomplete[1] = None
            self._archive(archive, incomplete)
            with mock.patch.object(mod, "ARC", str(archive)), \
                    mock.patch.object(mod, "GATE_REPORT", str(report)):
                self.assertEqual(mod.gate(), 1)
            persisted = json.loads(report.read_text())
            self.assertEqual(persisted["readiness_status"], "ERROR")
            self.assertEqual(persisted["error"]["code"], "ZERO_FIELD_COMPLETE_ROWS")
            self.assertEqual(persisted["total_rows"], 1)
            self.assertEqual(persisted["field_complete_rows"], 0)
            self.assertFalse(persisted["liquid_seven_complete"])
            self.assertFalse(persisted["four_hour_ts"]["ready"])


if __name__ == "__main__":
    unittest.main()
