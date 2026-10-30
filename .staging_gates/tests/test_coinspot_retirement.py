"""A book disappears only after evidenced closure and a paired cash transfer."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import book_coverage as coverage
import coinspot_retirement as retirement
import firm_capital_flows as flows
from test_firm_capital_flows import capital, event


class RetirementProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.base, self.kraken = root / "desk", root / "kraken"
        for directory in (self.base / "reports", self.kraken / "reports", self.base / "archive"):
            directory.mkdir(parents=True)
        self.ledger_path = self.kraken / "reports/coinspot_floor.json"
        self.archive = self.base / "archive/coinspot-final.json"
        self.warehouse_path = self.base / "reports/gap_close_levered_book.json"
        self.utc = "2026-09-24T08:00:00Z"
        move = dict(type="capital_transfer", utc=self.utc, usd=-5500., aud=-8000.,
                    **{"from": "coinspot_floor", "to": "gap_close_levered_book"})
        self.ledger = {"start_cash": -3000., "original_start_cash": 5000.,
                       "desks": {"A": {"cash": 0., "positions": {}}},
                       "capital_events": [move]}
        self.warehouse = {"cash": 15500., "capital_events": [dict(move, usd=5500.)]}
        raw = json.dumps(self.ledger).encode()
        self.ledger_path.write_bytes(raw)
        self.archive.write_bytes(raw)
        self.warehouse_path.write_text(json.dumps(self.warehouse))
        self.receipt = {"state": "RETIRED", "paper_only": True, "positions_closed": True,
                        "historical_pnl_verified": False, "transfer_usd": 5500.,
                        "transfer_aud": 8000., "transfer_utc": self.utc,
                        "warehouse_path": str(self.warehouse_path),
                        "archived_ledger_path": str(self.archive),
                        "final_ledger_sha256": hashlib.sha256(raw).hexdigest()}
        self.receipt_path = self.base / "reports/coinspot_retirement.json"
        self.receipt_path.write_text(json.dumps(self.receipt))

    def verify(self):
        return retirement.verified_retirement(self.base, self.kraken)

    def test_local_mirror_used_and_still_hash_bound(self):
        self.archive.unlink()  # original archive unreachable
        mirror = self.base / retirement.LOCAL_ARCHIVE
        mirror.write_bytes(self.ledger_path.read_bytes())
        got = self.verify()
        self.assertEqual(got["archived_ledger_path"], str(mirror))
        self.assertEqual(got["archived_ledger_path_original"], str(self.archive))
        mirror.write_bytes(b"{}")
        self.assertIsNone(self.verify())

    def test_hung_archive_read_fails_closed_quickly(self):
        import threading, time
        gate = threading.Event(); self.addCleanup(gate.set)
        real = Path.read_bytes
        archive = self.archive
        def fake(pth):
            if pth == archive:
                gate.wait(30)
            return real(pth)
        with patch.object(retirement, "ARCHIVE_READ_TIMEOUT_S", 0.2), \
             patch.object(Path, "read_bytes", fake):
            t0 = time.time()
            self.assertIsNone(retirement.verified_retirement(self.base, self.kraken))
            self.assertLess(time.time() - t0, 5)

    def test_complete_proof_retains_removed_funding_residual(self):
        self.assertAlmostEqual(self.verify()["retained_funding_basis_usd"], -2062.5)

    def test_uncommitted_missing_or_claimed_verified_history_fails(self):
        for key, value in (("state", "RETIRING"), ("positions_closed", False),
                           ("historical_pnl_verified", True), ("transfer_usd", 0)):
            with self.subTest(key=key):
                self.receipt_path.write_text(json.dumps(dict(self.receipt, **{key: value})))
                self.assertIsNone(self.verify())
        self.receipt_path.unlink()
        self.assertIsNone(self.verify())

    def test_modified_ledger_cannot_be_hidden(self):
        self.ledger["desks"]["A"]["positions"] = {"btc": {"units": 1}}
        self.ledger_path.write_text(json.dumps(self.ledger))
        self.assertIsNone(self.verify())

    def test_even_matching_hash_cannot_hide_unliquidated_positions(self):
        self.ledger["desks"]["A"]["positions"] = {"btc": {"units": 1}}
        raw = json.dumps(self.ledger).encode()
        self.ledger_path.write_bytes(raw)
        self.archive.write_bytes(raw)
        self.receipt["final_ledger_sha256"] = hashlib.sha256(raw).hexdigest()
        self.receipt_path.write_text(json.dumps(self.receipt))
        self.assertIsNone(self.verify())

    def test_missing_or_duplicate_inbound_transfer_fails(self):
        for events in ([], self.warehouse["capital_events"] * 2):
            self.warehouse_path.write_text(json.dumps(dict(self.warehouse, capital_events=events)))
            self.assertIsNone(self.verify())

    def test_canonical_symlink_cannot_hide_another_ledger(self):
        self.ledger_path.unlink()
        self.ledger_path.symlink_to(self.archive)
        self.assertIsNone(self.verify())

    def test_coverage_exemption_is_exact_path_and_proof_bound(self):
        with patch.object(coverage, "BASE", str(self.base)), \
             patch.object(coverage, "KRAKEN_DESK", str(self.kraken)):
            books = [{"path": str(self.warehouse_path)}]
            self.assertEqual(coverage.coverage_audit(books, False)[0], [])
            copy = self.kraken / "reports/copied.json"
            copy.write_bytes(self.ledger_path.read_bytes())
            self.assertEqual([x["path"] for x in coverage.coverage_audit(books, False)[0]], [str(copy)])


class RetirementCapitalTests(unittest.TestCase):
    def test_retirement_is_internal_not_profit_or_external_withdrawal(self):
        outgoing = event("coinspot_floor", -5500., other="gap_close_levered_book")
        incoming = event("gap_close_levered_book", 5500., other="coinspot_floor")
        old = {"coinspot_floor": capital(5000., ccy="AUD", rate=.6875),
               "gap_close_levered_book": capital(10000.)}
        new = {"coinspot_floor": capital(-3000., [outgoing], present=False, ccy="AUD"),
               "gap_close_levered_book": capital(15500., [incoming])}
        new["coinspot_floor"]["verified_retirement"] = {
            "transfer_usd": 5500., "transfer_utc": outgoing["utc"],
            "to": "gap_close_levered_book"}
        self.assertEqual(flows.reconcile(old, new)[::2], (0., []))
        self.assertEqual(flows.reconcile(new, new), (0., [], []))
        new["gap_close_levered_book"]["events"] = []
        self.assertTrue(flows.reconcile(old, new)[2])

    def test_unverified_internal_removal_stays_blocked(self):
        old = {"coinspot_floor": capital(5000.)}
        new = {"coinspot_floor": capital(None, [event("coinspot_floor", -5000., other="x")], present=False)}
        self.assertTrue(flows.reconcile(old, new)[2])


if __name__ == "__main__":
    unittest.main()
