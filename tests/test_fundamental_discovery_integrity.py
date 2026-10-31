from __future__ import annotations
import importlib.util as _ilu
if _ilu.find_spec("fundamental_coverage_ledger_v2") is None:
    import pytest as _pt
    _pt.skip("fundamental_coverage_ledger_v2 is not in the code-only export. See TEST_TRIAGE.md.", allow_module_level=True)

import datetime as dt
import json
import contextlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from fundamental_catalyst_lifecycle import classify_event  # noqa: E402
from fundamental_discovery_common import source_looks_verified  # noqa: E402
from fundamental_discovery_common import file_sha256  # noqa: E402
from fundamental_coverage_ledger_v2 import build_ledger  # noqa: E402
from fundamental_transactional_aggregate import (  # noqa: E402
    ManifestError,
    REGISTERED_ALLOCATOR,
    _event_queue_match,
    _json_sha256,
    _record_matches_feature,
    _registered_capital_contract,
    _registered_allocator_module,
    aggregate_manifest,
    build_allocator_receipt,
    build_manifest,
    enrich_manifest,
    run_registered_allocator,
    utc_now,
    write_manifest,
)
from fundamental_session_queue import build_queue as build_session_queue  # noqa: E402
import fundamental_session_queue as session_queue  # noqa: E402
import fundamental_open_new as entry  # noqa: E402


def full_block(symbol: str = "TEST", verdict: str = "undervalued", trade: str = "LONG") -> str:
    return f"""SYMBOL: {symbol}
EXCHANGE: NYSE
US_LISTED: yes
SECTOR: technology
VERDICT: {verdict}
CONFIDENCE: medium
WHY CHEAP: A dated de-rate left the multiple below its own history.
CHEAPNESS: real after one-off and currency checks.
DIRECTION: improving over five filed years.
METRICS: forward P/E 8x; FCF yield 12%; sourced 2026-08-31.
CATALYST: Contract decision on 2026-12-15.
KILLS IT: Contract not awarded by 2026-12-15.
SOURCES: https://example.com/filing-2026-08-31
TRADE DIRECTION: {trade} — economic call only.
"""


TODAY = dt.date.today()
EVENT_DATE = (TODAY + dt.timedelta(days=30)).isoformat()
MEASUREMENT_DATE = (TODAY + dt.timedelta(days=45)).isoformat()
REVIEW_DATE = (TODAY + dt.timedelta(days=60)).isoformat()


def routed_block(
    route: str = "direct_local",
    *,
    verdict: str = "undervalued",
    trade: str = "LONG",
    catalyst_direction: str = "positive",
    event_date: str = EVENT_DATE,
    review_date: str = REVIEW_DATE,
    execution_symbol: str = "TEST.AX",
    stable_id: str = "LEI:TEST-ISSUER",
) -> str:
    extras = """EXEC_SYMBOL: TEST.AX
EXEC_EXCHANGE: ASX
EXEC_CCY: AUD
EXEC_ROUTE: {route}
EXECUTION_STATUS: pending_session
RESEARCH_FEATURES: none
RESEARCH_FEATURE_RECEIPTS: []
STABLE_ISSUER_ID: {stable_id}
CATALYST_DATE: {event_date}
EVENT_DATE: {event_date}
MEASUREMENT_PERIOD_END: {measurement_date}
THESIS_REVIEW_DATE: {review_date}
CATALYST_DIRECTION: {catalyst_direction} — signed award decision
OWN HISTORY: 12th percentile across 12 years and a full cycle
VOLUME TREND: 1.20, building, measured 2026-09-02
LIQUIDITY: exact route requires the fresh entry gate before any PAPER order
""".format(
        route=route,
        stable_id=stable_id,
        event_date=event_date,
        measurement_date=MEASUREMENT_DATE,
        review_date=review_date,
        catalyst_direction=catalyst_direction,
    ).replace("EXEC_SYMBOL: TEST.AX", f"EXEC_SYMBOL: {execution_symbol}")
    return full_block(verdict=verdict, trade=trade).replace(
        "EXCHANGE: NYSE\n", "EXCHANGE: NYSE\n" + extras
    )


def allocator_fixture(
    root: Path,
    manifest: dict,
    long_alloc: dict[str, float],
) -> tuple[dict, Path, Path]:
    """Create the unchanged allocator's bound output contract in a temp run."""

    manifest_path = root / "manifest.json"
    write_manifest(manifest_path, manifest)
    capital = {
        "seed_usd": 600_000,
        "contributed_capital_usd": 600_000,
        "cash": 100_000,
        "positions": {},
    }
    capital_path = root / "capital.json"
    capital_path.write_text(json.dumps(capital), encoding="utf-8")
    allocator_output = {
        "blocks_parsed": manifest["block_count"],
        "parse_failures": [],
        "capital_contract": _registered_capital_contract(capital),
        "long_alloc": long_alloc,
    }
    allocator_path = root / "allocator_output_v2.json"
    allocator_path.write_text(json.dumps(allocator_output), encoding="utf-8")
    capital_snapshot_path = root / "capital.snapshot.json"
    capital_snapshot_path.write_text(json.dumps(capital), encoding="utf-8")
    run_receipt = {
        "schema": "tfo.fundamental.allocator-run.v1",
        "status": "complete",
        "paper_only": True,
        "batch_id": manifest["batch_id"],
        "batch_sha256": manifest["batch_sha256"],
        "run_dir": str(root.resolve()),
        "capital_ledger": {
            "path": str(capital_path.resolve()),
            "sha256_before": file_sha256(capital_path),
            "sha256_after": file_sha256(capital_path),
        },
        "capital_snapshot": {
            "path": str(capital_snapshot_path.resolve()),
            "sha256": file_sha256(capital_snapshot_path),
        },
        "allocator_module": {
            "path": str(REGISTERED_ALLOCATOR.resolve()),
            "sha256": file_sha256(REGISTERED_ALLOCATOR),
        },
        "allocator_output": {
            "path": str(allocator_path.resolve()),
            "sha256": file_sha256(allocator_path),
        },
        "started_utc": utc_now(),
        "finished_utc": utc_now(),
        "stdout_sha256": "00" * 32,
    }
    run_receipt_path = root / "allocator.run.json"
    write_manifest(run_receipt_path, run_receipt)
    receipt = build_allocator_receipt(
        manifest,
        allocator_run_receipt_path=run_receipt_path,
        current_date=TODAY,
    )
    receipt_path = root / "allocator.receipt.json"
    write_manifest(receipt_path, receipt)
    return receipt, manifest_path, receipt_path


class CoverageLedgerTests(unittest.TestCase):
    def test_unrepresented_and_gate_stops_are_not_no_hits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hunt = root / "hunt_1"
            hunt.mkdir()
            (hunt / "blocks_x.md").write_text(
                """RUN SUMMARY: 3 screened | 0 undervalued
SYMBOL: GATE
EXCHANGE: TSX
US_LISTED: no
SECTOR: materials
VERDICT: insufficient_evidence
CONFIDENCE: high
WHY CHEAP: no valuation work was performed after gate refusal
CHEAPNESS: artifact
DIRECTION: not assessed after gate refusal
METRICS: ADV only
CATALYST: not researched after gate refusal
KILLS IT: already refused
SOURCES: https://example.com/quote-2026-09-02
TRADE DIRECTION: NEITHER — gate/scope refusal
\n"""
                + full_block("NOHIT", "fairly_priced", "NEITHER"),
                encoding="utf-8",
            )
            ledger = build_ledger(root)
            totals = ledger["totals"]
            self.assertEqual(totals["canonical_universe"], 3)
            self.assertEqual(totals["not_researched"], 2)  # gate block + unrepresented delta
            self.assertEqual(totals["researched_no_hit"], 1)
            self.assertEqual(totals["researched_hit"], 0)
            self.assertEqual(
                totals["canonical_universe"],
                totals["not_researched"]
                + totals["researched_no_hit"]
                + totals["researched_hit"]
                + totals["unknown"]
                + totals["incomplete"],
            )

    def test_comma_formatted_summary_count_reconciles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hunt = root / "hunt_1"
            hunt.mkdir()
            (hunt / "blocks_x.md").write_text(
                "RUN SUMMARY: 2,520 screened | 0 undervalued\n" + full_block("ONE"),
                encoding="utf-8",
            )
            ledger = build_ledger(root)
            self.assertEqual(ledger["totals"]["canonical_universe"], 2520)
            self.assertEqual(ledger["totals"]["not_researched_unrepresented"], 2519)

    def test_closing_fence_stops_sources_from_swallowing_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hunt = root / "hunt_1"
            hunt.mkdir()
            (hunt / "blocks_x.md").write_text(
                "```\n"
                + full_block("ONE")
                + "```\n\n## SUMMARY\nposition/ADV is not a source\n",
                encoding="utf-8",
            )
            ledger = build_ledger(root)
            self.assertEqual(ledger["totals"]["researched_hit"], 1)


class CatalystLifecycleTests(unittest.TestCase):
    def test_date_only_prose_is_not_a_verifiable_source(self) -> None:
        self.assertFalse(source_looks_verified("Issuer filing reviewed 2026-08-31"))
        self.assertTrue(source_looks_verified("https://example.com/filing dated 2026-08-31"))

    def test_past_killed_and_unverified_are_alerts_not_decisions(self) -> None:
        event = classify_event(
            symbol="OLD",
            source_path="sample.md",
            line=10,
            catalyst="The scheme was withdrawn and the 2026-08-01 window has closed.",
            event_calendar="",
            kills_it="Decision date 2026-08-01.",
            news="Company later confirmed it was cancelled.",
            why_cheap="",
            sources="not sourced",
            as_of=dt.date(2026, 9, 2),
        )
        self.assertIn("past", event["flags"])
        self.assertIn("possible_killed", event["flags"])
        self.assertIn("unverified_source", event["flags"])
        self.assertTrue(event["needs_review"])
        self.assertIn("does not decide", event["non_decision"])

    def test_sourced_future_event_is_not_review_alert(self) -> None:
        event = classify_event(
            symbol="FUT",
            source_path="sample.md",
            line=1,
            catalyst="Signed tender outcome due 2026-12-15.",
            event_calendar="2026-12-15 award decision.",
            kills_it="No award on 2026-12-15.",
            news="",
            why_cheap="",
            sources="https://example.com/notice-2026-08-31",
            as_of=dt.date(2026, 9, 2),
        )
        self.assertEqual(event["flags"], ["future"])
        self.assertFalse(event["needs_review"])

    def test_future_kill_date_cannot_mask_past_catalyst(self) -> None:
        event = classify_event(
            symbol="MASK",
            source_path="sample.md",
            line=3,
            catalyst="Tender decision occurred 2026-08-01.",
            event_calendar="",
            kills_it="Thesis invalid if remediation is absent by 2026-12-15.",
            news="",
            why_cheap="",
            sources="https://example.com/tender",
            as_of=dt.date(2026, 9, 2),
        )
        self.assertIn("past", event["flags"])
        self.assertNotIn("future", event["flags"])
        self.assertEqual(event["future_dates"], [])
        self.assertEqual(event["kill_dates"], ["2026-12-15"])
        self.assertTrue(event["needs_review"])


class TransactionTests(unittest.TestCase):
    def test_incomplete_batch_is_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "blocks_x.md").write_text(
                "SYMBOL: HALF\nVERDICT: undervalued\n", encoding="utf-8"
            )
            manifest = build_manifest(root, frontier="test")
            self.assertEqual(manifest["status"], "quarantined")
            with self.assertRaises(ManifestError):
                aggregate_manifest(manifest)

    def test_complete_batch_aggregates_without_sizing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "blocks_x.md").write_text(
                "RUN SUMMARY: 1 screened | 1 undervalued\n" + full_block(),
                encoding="utf-8",
            )
            manifest = build_manifest(root, frontier="test")
            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(manifest["candidates"], [])
            self.assertEqual(manifest["candidate_stage"], "awaiting_allocator_enrichment")
            aggregate = aggregate_manifest(manifest)
            self.assertEqual(aggregate["status"], "complete")
            self.assertEqual(aggregate["candidates"], [])
            self.assertEqual(aggregate["economic_long_candidates_not_sized"], ["TEST"])
            self.assertNotIn("long_alloc", aggregate)

    def test_v2_candidate_is_exact_and_session_queue_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "blocks_x.md").write_text(routed_block(), encoding="utf-8")
            manifest = build_manifest(root, frontier="test")
            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(manifest["candidates"], [])
            receipt, manifest_path, receipt_path = allocator_fixture(
                root, manifest, {"TEST.AX": 2500}
            )
            execution = enrich_manifest(
                manifest,
                receipt,
                research_manifest_path=manifest_path,
                allocator_receipt_path=receipt_path,
                current_date=TODAY,
            )
            candidate = execution["candidates"][0]
            required = {
                "stable_issuer_id", "symbol", "economic_direction", "execution_status",
                "execution_symbol", "execution_venue", "quote_currency", "notional_usd",
                "sector", "catalyst_date", "thesis",
            }
            self.assertTrue(required.issubset(candidate))
            self.assertEqual(candidate["symbol"], candidate["execution_symbol"])
            self.assertEqual(candidate["execution_route"], "direct_local")
            self.assertEqual(candidate["notional_usd"], 2500.0)
            queue = build_session_queue(execution)
            queued = queue["candidates"][0]
            self.assertEqual(queued["symbol"], queued["execution_symbol"])
            self.assertEqual(queued["state"], "PENDING_SESSION")

    def test_partial_v2_block_quarantines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            partial = full_block().replace(
                "EXCHANGE: NYSE\n", "EXCHANGE: NYSE\nEXEC_SYMBOL: TEST.AX\n"
            )
            (root / "blocks_x.md").write_text(partial, encoding="utf-8")
            manifest = build_manifest(root, frontier="test")
            self.assertEqual(manifest["status"], "quarantined")
            self.assertEqual(manifest["candidates"], [])

    def test_unverified_route_is_research_complete_but_not_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "blocks_x.md").write_text(
                routed_block(route="unverified"), encoding="utf-8"
            )
            manifest = build_manifest(root, frontier="test")
            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(manifest["candidates"], [])
            receipt, manifest_path, receipt_path = allocator_fixture(
                root, manifest, {"TEST.AX": 2500}
            )
            execution = enrich_manifest(
                manifest,
                receipt,
                research_manifest_path=manifest_path,
                allocator_receipt_path=receipt_path,
                current_date=TODAY,
            )
            self.assertEqual(execution["candidates"], [])
            self.assertIn("unverified", " ".join(execution["candidate_held_out"][0]["reasons"]))

    def test_adr_route_requires_an_exact_nonempty_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "blocks_x.md").write_text(
                routed_block(route="adr:"), encoding="utf-8"
            )
            manifest = build_manifest(root, frontier="test")
            self.assertEqual(manifest["status"], "quarantined")
            issues = manifest["quarantine_reasons"][0]["detail"][0]["issues"]
            self.assertTrue(any("EXEC_ROUTE is invalid" in issue for issue in issues))

    def test_exact_verdict_token_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "blocks_x.md").write_text(
                full_block(verdict="not undervalued", trade="NEITHER"), encoding="utf-8"
            )
            manifest = build_manifest(root, frontier="test")
            self.assertEqual(manifest["status"], "quarantined")

    def test_hash_change_after_seal_refuses_aggregation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "blocks_x.md"
            path.write_text(full_block(), encoding="utf-8")
            manifest = build_manifest(root, frontier="test")
            self.assertEqual(manifest["status"], "complete")
            path.write_text(full_block() + "\n<!-- mutation -->\n", encoding="utf-8")
            with self.assertRaises(ManifestError):
                aggregate_manifest(manifest)

    def test_claimed_full_block_mismatch_quarantines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "blocks_x.md").write_text(
                "FULL BLOCKS WRITTEN: 2\n" + full_block(), encoding="utf-8"
            )
            manifest = build_manifest(root, frontier="test")
            self.assertEqual(manifest["status"], "quarantined")

    def _enrich(self, root: Path, block: str, allocation: float | None = 2500) -> dict:
        (root / "blocks_x.md").write_text(block, encoding="utf-8")
        manifest = build_manifest(root, frontier="test")
        self.assertEqual(manifest["status"], "complete", manifest["quarantine_reasons"])
        receipt, manifest_path, receipt_path = allocator_fixture(
            root,
            manifest,
            {} if allocation is None else {"TEST.AX": allocation},
        )
        return enrich_manifest(
            manifest,
            receipt,
            research_manifest_path=manifest_path,
            allocator_receipt_path=receipt_path,
            current_date=TODAY,
        )

    def _ready_entry(self, root: Path, block: str | None = None) -> dict:
        execution = self._enrich(root, block or routed_block())
        self.assertEqual(len(execution["candidates"]), 1, execution["candidate_held_out"])
        gate = (
            {"px": 2.0, "ccy": "AUD", "fx_usd": 0.625, "px_usd": 1.25},
            {"market_state": "REGULAR", "measured": {
                "spread_pct": 0.1, "adv_usd": 8_000_000,
                "position_pct_of_adv": 0.0003125,
            }},
            None,
        )
        queue = session_queue.verify_queue(
            build_session_queue(execution), gate=lambda *args: gate)
        self.assertEqual(queue["counts"]["READY_FOR_DRY_RUN"], 1)
        queue_path = root / "queue.json"
        write_manifest(queue_path, queue)
        return {"queue": queue, "path": queue_path, "gate": gate}

    def _open_ready_entry(self, root: Path, ready: dict, *, dry_run: bool = True) -> int:
        row = ready["queue"]["candidates"][0]
        with mock.patch.object(entry, "LEDGER", str(root / "capital.json")), \
                mock.patch.object(entry, "ENTRY_LOG", str(root / "entries.jsonl")), \
                mock.patch.object(entry, "ENTRY_HOLDS", str(root / "holds.json")), \
                mock.patch.object(entry, "_entry_risk_allowed", return_value=(True, "isolated test")), \
                mock.patch.object(entry.desk_lock, "ledger_lock", return_value=contextlib.nullcontext()):
            return entry.open_new(
                row["symbol"], "long", row["notional_usd"], row["thesis"],
                row["catalyst_date"], sector=row["sector"], dry_run=dry_run,
                sizing_policy="registered",
                research_features=row["research_features"],
                stable_issuer_id=row["stable_issuer_id"],
                manifest_sha256=ready["queue"]["source_execution_manifest_sha256"],
                queue_receipt_path=str(ready["path"]),
                queue_receipt_sha256=file_sha256(ready["path"]),
            )

    def test_bound_general_entry_reaches_market_gate_and_preserves_empty_attribution(self):
        for issuer in (
            "LEI:TEST-ISSUER",
            "2138005I669UO6PJXM24 (GLEIF LEI, legal name GLOBALDATA PLC)",
            "2138001AVBSD1HSC6Z10 (GLEIF LEI, legal name JOHNSON MATTHEY PLC)",
            "213800MZGBGCJIPOBB41 (GLEIF LEI, legal name YOUGOV PLC)",
        ):
            with self.subTest(issuer=issuer), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                ready = self._ready_entry(root, routed_block(stable_id=issuer))
                before = (root / "capital.json").read_bytes()
                with mock.patch.object(entry, "live_entry_gate", return_value=ready["gate"]) as gate:
                    self.assertEqual(self._open_ready_entry(root, ready), 0)
                    gate.assert_called_once()
                self.assertEqual((root / "capital.json").read_bytes(), before)
                self.assertFalse((root / "entries.jsonl").exists())
                with mock.patch.object(entry, "live_entry_gate", return_value=ready["gate"]):
                    self.assertEqual(self._open_ready_entry(root, ready, dry_run=False), 0)
                ledger = json.loads((root / "capital.json").read_text())
                provenance = ledger["positions"]["TEST.AX"]["research_provenance"]
                self.assertEqual(provenance["features"], [])
                self.assertEqual(provenance["stable_issuer_id"], issuer)
                self.assertEqual(provenance["queue_receipt_sha256"], file_sha256(ready["path"]))
                self.assertEqual(ledger["new_entries"][0]["research_provenance"], provenance)

    def test_bound_general_entry_still_refuses_closed_market_and_insufficient_cash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready = self._ready_entry(root)
            before = (root / "capital.json").read_bytes()
            with mock.patch.object(entry, "live_entry_gate", return_value=(
                    None, None, "venue is not in a REGULAR session (marketState=CLOSED)")) as gate:
                self.assertEqual(self._open_ready_entry(root, ready), 2)
                gate.assert_called_once()
            self.assertEqual((root / "capital.json").read_bytes(), before)
            capital = json.loads(before)
            capital["cash"] = 100
            (root / "capital.json").write_text(json.dumps(capital))
            with mock.patch.object(entry, "live_entry_gate") as gate:
                self.assertEqual(self._open_ready_entry(root, ready), 2)
                gate.assert_not_called()
            self.assertFalse((root / "entries.jsonl").exists())

    def test_bad_annotated_identity_refuses_even_with_valid_receipt_chain(self):
        for issuer in (
            "garbage (GLEIF LEI, legal name GLOBALDATA PLC)",
            "2138005I669UO6PJXM25 (GLEIF LEI, legal name GLOBALDATA PLC)",
            "2138005I669UO6PJXM24 (unverified guess)",
        ):
            with self.subTest(issuer=issuer), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                ready = self._ready_entry(root, routed_block(stable_id=issuer))
                with mock.patch.object(entry, "live_entry_gate") as gate:
                    self.assertEqual(self._open_ready_entry(root, ready), 2)
                    gate.assert_not_called()
                self.assertFalse((root / "entries.jsonl").exists())

    def test_self_consistently_rehashed_candidate_cannot_replace_verified_manifest_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready = self._ready_entry(root)
            queue = ready["queue"]
            row = queue["candidates"][0]
            row["notional_usd"] = 20_000
            row["thesis"] = "Replaced thesis. KILLS IT: an invented condition."
            row["source_candidate_sha256"] = entry._json_sha256({
                key: value for key, value in row.items() if key not in entry.QUEUE_ONLY_FIELDS})
            queue["queue_receipt_sha256"] = entry._json_sha256(entry._queue_binding_payload(queue))
            write_manifest(ready["path"], queue)
            with mock.patch.object(entry, "live_entry_gate") as gate:
                self.assertEqual(self._open_ready_entry(root, ready), 2)
                gate.assert_not_called()
            self.assertFalse((root / "entries.jsonl").exists())

    def test_bound_general_entry_rechecks_sealed_research_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready = self._ready_entry(root)
            (root / "blocks_x.md").write_text(routed_block() + "\nChanged after sealing.\n")
            with mock.patch.object(entry, "live_entry_gate") as gate:
                self.assertEqual(self._open_ready_entry(root, ready), 2)
                gate.assert_not_called()
            self.assertFalse((root / "entries.jsonl").exists())

    def test_feature_receipt_is_required_and_rechecked_by_final_opener(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            feature_path = root / "filing.json"
            record = {
                "status": "COMPLETE", "cik": "1", "latest_known_on": TODAY.isoformat(),
                "research_queue": True, "material_changes": [{"status": "MATERIAL_LEAD"}],
                "source_receipts": {
                    phase: {"snapshot_sha256": digest * 64,
                            "primary_source_url": "https://sec.example/" + phase,
                            "known_on": TODAY.isoformat(), "cik": "0001"}
                    for phase, digest in (("prior", "1"), ("latest", "2"))
                },
            }
            write_manifest(feature_path, record)
            receipt = {"feature": "filing_diff", "path": str(feature_path),
                       "sha256": file_sha256(feature_path), "record_sha256": _json_sha256(record)}
            block = routed_block(stable_id="CIK:0001").replace(
                "RESEARCH_FEATURES: none", "RESEARCH_FEATURES: filing_diff").replace(
                "RESEARCH_FEATURE_RECEIPTS: []",
                "RESEARCH_FEATURE_RECEIPTS: " + json.dumps([receipt]))
            ready = self._ready_entry(root, block)
            with mock.patch.object(entry, "live_entry_gate", return_value=ready["gate"]):
                self.assertEqual(self._open_ready_entry(root, ready), 0)
            original_queue = json.loads(json.dumps(ready["queue"]))
            row = ready["queue"]["candidates"][0]
            row["research_features"] = []
            row["research_feature_receipts"] = []
            row["source_candidate_sha256"] = entry._json_sha256({
                key: value for key, value in row.items() if key not in entry.QUEUE_ONLY_FIELDS})
            ready["queue"]["queue_receipt_sha256"] = entry._json_sha256(
                entry._queue_binding_payload(ready["queue"]))
            write_manifest(ready["path"], ready["queue"])
            with mock.patch.object(entry, "live_entry_gate") as gate:
                self.assertEqual(self._open_ready_entry(root, ready), 2)
                gate.assert_not_called()
            ready["queue"] = original_queue
            write_manifest(ready["path"], original_queue)
            write_manifest(feature_path, {**record, "research_queue": False})
            with mock.patch.object(entry, "live_entry_gate") as gate:
                self.assertEqual(self._open_ready_entry(root, ready), 2)
                gate.assert_not_called()
            self.assertFalse((root / "entries.jsonl").exists())

    def test_research_admission_hold_is_batch_bound_and_malformed_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready = self._ready_entry(root)
            holds = {"schema": "tfo.fundamental.entry-holds.v1", "paper_only": True,
                     "holds": [{
                         "research_manifest_sha256": ready["queue"]["research_manifest_sha256"],
                         "symbol": "TEST.AX", "reason": "Dated catalyst is unsourced.",
                         "evidence_urls": ["https://example.com/issuer-disclosure"],
                     }]}
            write_manifest(root / "holds.json", holds)
            with mock.patch.object(entry, "live_entry_gate") as gate:
                self.assertEqual(self._open_ready_entry(root, ready), 2)
                gate.assert_not_called()
            holds["holds"][0]["research_manifest_sha256"] = "ff" * 32
            write_manifest(root / "holds.json", holds)
            with mock.patch.object(entry, "live_entry_gate", return_value=ready["gate"]) as gate:
                self.assertEqual(self._open_ready_entry(root, ready), 0)
                gate.assert_called_once()
            (root / "holds.json").write_text("{")
            with mock.patch.object(entry, "live_entry_gate") as gate:
                self.assertEqual(self._open_ready_entry(root, ready), 2)
                gate.assert_not_called()

    def test_undervalued_neither_gets_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            execution = self._enrich(Path(tmp), routed_block(trade="NEITHER"), None)
            self.assertEqual(execution["candidates"], [])

    def test_execution_sector_reuses_bound_allocator_classifier_for_actual_research_labels(self):
        for symbol, raw, expected in (
            ("DATA.L", "Industrials - Consulting Services", "industrials"),
            ("JMAT.L", "Basic Materials - Specialty Chemicals", "materials"),
            ("YOU.L", "Industrials - Consulting Services", "industrials"),
        ):
            with self.subTest(symbol=symbol), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                block = routed_block().replace("SECTOR: technology", "SECTOR: " + raw)
                execution = self._enrich(root, block)
                row = execution["candidates"][0]
                self.assertEqual(row["sector"], expected)
                self.assertEqual(row["research_sector"], raw)
                self.assertEqual((root / "blocks_x.md").read_text(), block)
                queued = build_session_queue(execution)["candidates"][0]
                self.assertEqual(queued["sector"], expected)
                self.assertIn(queued["sector"], entry.REGISTERED_SECTORS)

    def test_unknown_sector_stays_unfunded_even_with_a_positive_allocator_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            block = routed_block().replace("SECTOR: technology", "SECTOR: Unclassified activity")
            execution = self._enrich(Path(tmp), block)
            self.assertEqual(execution["candidates"], [])
            self.assertIn("sector is not classified by the registered allocator",
                          execution["candidate_held_out"][0]["reasons"])

    def test_sector_classifier_refuses_an_unbound_allocator_module(self):
        for binding in (
            {"path": str(REGISTERED_ALLOCATOR), "sha256": "ff" * 32},
            {"path": "/tmp/unregistered_allocator.py", "sha256": file_sha256(REGISTERED_ALLOCATOR)},
        ):
            with self.subTest(binding=binding), self.assertRaisesRegex(ManifestError, "binding mismatch"):
                _registered_allocator_module(binding)

    def test_fairly_priced_long_gets_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            execution = self._enrich(
                Path(tmp), routed_block(verdict="fairly_priced"), 2500
            )
            self.assertEqual(execution["candidates"], [])
            self.assertIn(
                "exact verdict is not undervalued",
                execution["candidate_held_out"][0]["reasons"],
            )

    def test_negative_and_two_sided_catalysts_get_zero(self) -> None:
        for direction in ("negative", "two_sided"):
            with self.subTest(direction=direction), tempfile.TemporaryDirectory() as tmp:
                execution = self._enrich(
                    Path(tmp), routed_block(catalyst_direction=direction), 2500
                )
                self.assertEqual(execution["candidates"], [])

    def test_missing_allocator_notional_gets_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            execution = self._enrich(Path(tmp), routed_block(), None)
            self.assertEqual(execution["candidates"], [])
            self.assertIn(
                "positive notional_usd is missing from deterministic allocator receipt",
                execution["candidate_held_out"][0]["reasons"],
            )

    def test_past_event_or_review_gets_zero(self) -> None:
        stale = (TODAY - dt.timedelta(days=1)).isoformat()
        for block in (
            routed_block(event_date=stale),
            routed_block(review_date=stale),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                execution = self._enrich(Path(tmp), block, 2500)
                self.assertEqual(execution["candidates"], [])

    def test_future_measurement_horizon_is_preserved(self) -> None:
        # MEASUREMENT_PERIOD_END is the future KILLS-IT check horizon, not an
        # evidence publication date. Point-in-time gates apply to its receipts.
        with tempfile.TemporaryDirectory() as tmp:
            execution = self._enrich(Path(tmp), routed_block(), 2500)
            self.assertEqual(
                execution["candidates"][0]["measurement_period_end"],
                MEASUREMENT_DATE,
            )

    def test_no_change_filing_diff_is_not_signal_provenance(self) -> None:
        record = {
            "status": "COMPLETE",
            "cik": "1",
            "latest_known_on": TODAY.isoformat(),
            "research_queue": False,
            "material_changes": [],
        }
        self.assertFalse(
            _record_matches_feature(record, "filing_diff", "CIK:0001", TODAY)
        )
        record["research_queue"] = True
        record["material_changes"] = [{"status": "MATERIAL_LEAD"}]
        self.assertTrue(
            _record_matches_feature(record, "filing_diff", "CIK:0001", TODAY)
        )

    def test_hand_authored_event_ready_without_bindings_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "event.json"
            payload = {
                "schema": "tfo.discovery.event-evidence.v1",
                "status": "EVIDENCE_READY",
                "paper_only": True,
                "non_signal": True,
                "symbol": "TEST",
                "stable_issuer_id": "LEI:TEST-ISSUER",
                "event_date": EVENT_DATE,
                "as_of_date": TODAY.isoformat(),
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "bindings are absent"):
                _event_queue_match(
                    path,
                    _json_sha256(payload),
                    "TEST",
                    "LEI:TEST-ISSUER",
                    EVENT_DATE,
                    TODAY,
                )

    def test_source_mutation_and_candidate_injection_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "blocks_x.md"
            source.write_text(routed_block(), encoding="utf-8")
            manifest = build_manifest(root, frontier="test")
            receipt, manifest_path, receipt_path = allocator_fixture(
                root, manifest, {"TEST.AX": 2500}
            )
            injected = dict(manifest)
            injected["candidates"] = [{"symbol": "INJECTED"}]
            with self.assertRaises(ManifestError):
                aggregate_manifest(injected)
            source.write_text(routed_block() + "\n<!-- mutation -->\n", encoding="utf-8")
            with self.assertRaises(ManifestError):
                enrich_manifest(
                    manifest,
                    receipt,
                    research_manifest_path=manifest_path,
                    allocator_receipt_path=receipt_path,
                    current_date=TODAY,
                )

    def test_conflicting_duplicate_stable_issuer_is_refused_at_allocator_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            one = routed_block(execution_symbol="TEST.AX")
            two = routed_block(
                verdict="fairly_priced",
                execution_symbol="TEST.L",
                stable_id="LEI:TEST-ISSUER",
            ).replace("SYMBOL: TEST", "SYMBOL: TEST2", 1)
            (root / "blocks_x.md").write_text(one + "\n" + two, encoding="utf-8")
            manifest = build_manifest(root, frontier="test")
            self.assertEqual(manifest["status"], "complete")
            with self.assertRaisesRegex(ManifestError, "duplicate stable issuer"):
                allocator_fixture(
                    root, manifest, {"TEST.AX": 1250, "TEST.L": 1250}
                )

    def test_registered_allocator_runs_against_explicit_sealed_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "blocks_x.md").write_text(
                routed_block(trade="NEITHER"), encoding="utf-8"
            )
            manifest = build_manifest(root, frontier="test")
            capital = {
                "seed_usd": 600_000,
                "contributed_capital_usd": 600_000,
                "cash": 100_000,
                "positions": {},
            }
            capital_path = root / "capital.json"
            capital_path.write_text(json.dumps(capital), encoding="utf-8")
            before = capital_path.read_bytes()
            sealed_aggregate = root / "aggregate.json"
            sealed_aggregate.write_bytes(b'{"sealed": true}\n')
            ready = {
                "schema": 1,
                "status": "READY",
                "run_dir": str(root.resolve()),
                "aggregate": {
                    "name": "aggregate.json",
                    "sha256": file_sha256(sealed_aggregate),
                },
                "blocks": [{
                    "name": "blocks_x.md",
                    "sha256": file_sha256(root / "blocks_x.md"),
                }],
            }
            (root / ".tfo_batch_ready.json").write_text(json.dumps(ready), encoding="utf-8")
            sealed_before = sealed_aggregate.read_bytes()
            allocator_output = root / "allocator_output_v2.json"
            run_receipt = run_registered_allocator(
                manifest,
                run_dir=root,
                capital_ledger_path=capital_path,
                capital_snapshot_output=root / "capital.snapshot.json",
                allocator_output_path=allocator_output,
            )
            self.assertEqual(run_receipt["status"], "complete")
            self.assertEqual(capital_path.read_bytes(), before)
            self.assertEqual(sealed_aggregate.read_bytes(), sealed_before)
            self.assertEqual(file_sha256(sealed_aggregate), ready["aggregate"]["sha256"])
            output = json.loads(allocator_output.read_text(encoding="utf-8"))
            self.assertEqual(output["blocks_parsed"], 1)
            self.assertEqual(output["long_alloc"], {})
            self.assertEqual(output["capital_contract"]["cash_usd"], 100_000)

            run_receipt_path = root / "allocator.run.json"
            write_manifest(run_receipt_path, run_receipt)
            # Later marks may change the live ledger; binding uses only the
            # exact snapshot sealed during the allocator run.
            capital_path.write_text(json.dumps({**capital, "cash": 99_000}), encoding="utf-8")
            bound = build_allocator_receipt(
                manifest,
                allocator_run_receipt_path=run_receipt_path,
                current_date=TODAY,
            )
            self.assertEqual(bound["capital_snapshot"], run_receipt["capital_snapshot"])

            sealed_aggregate.write_bytes(b'{"sealed": false}\n')
            with self.assertRaisesRegex(ManifestError, "READY aggregate hash mismatch"):
                build_allocator_receipt(
                    manifest,
                    allocator_run_receipt_path=run_receipt_path,
                    current_date=TODAY,
                )
            sealed_aggregate.write_bytes(sealed_before)

            allocator_output.write_text(json.dumps({**output, "long_alloc": {"INJECT": 1}}),
                                        encoding="utf-8")
            with self.assertRaisesRegex(ManifestError, "output hash mismatch"):
                build_allocator_receipt(
                    manifest,
                    allocator_run_receipt_path=run_receipt_path,
                    current_date=TODAY,
                )

    def test_registered_allocator_refuses_run_dir_outside_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            root = Path(tmp)
            (root / "blocks_x.md").write_text(routed_block(trade="NEITHER"), encoding="utf-8")
            manifest = build_manifest(root, frontier="test")
            capital_path = root / "capital.json"
            capital_path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ManifestError, "run_dir differs"):
                run_registered_allocator(
                    manifest,
                    run_dir=other,
                    capital_ledger_path=capital_path,
                    capital_snapshot_output=root / "capital.snapshot.json",
                    allocator_output_path=root / "allocator_output_v2.json",
                )

    def test_source_mutation_during_staged_allocator_run_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "blocks_x.md"
            source.write_text(routed_block(), encoding="utf-8")
            manifest = build_manifest(root, frontier="test")
            capital = {
                "seed_usd": 600_000,
                "contributed_capital_usd": 600_000,
                "cash": 100_000,
                "positions": {},
            }
            capital_path = root / "capital.json"
            capital_path.write_text(json.dumps(capital), encoding="utf-8")

            def mutating_quotes(symbols):
                source.write_text(routed_block() + "\n<!-- concurrent mutation -->\n",
                                  encoding="utf-8")
                return ({symbol: {"ccy": "AUD"} for symbol in symbols}, {})

            with self.assertRaisesRegex(ManifestError, "source changed"):
                run_registered_allocator(
                    manifest,
                    run_dir=root,
                    capital_ledger_path=capital_path,
                    capital_snapshot_output=root / "capital.snapshot.json",
                    allocator_output_path=root / "allocator_output_v2.json",
                    quote_fetcher=mutating_quotes,
                )


if __name__ == "__main__":
    unittest.main()
