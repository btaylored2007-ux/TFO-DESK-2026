#!/usr/bin/env python3
"""Hash-sealed, strict aggregation adapter for TFO research batches.

This is discovery infrastructure only. It never imports an engine module, reads
capital, sizes positions, or writes a live ledger.  A manifest is COMPLETE only
when every represented block satisfies the current research contract. Otherwise
the batch is QUARANTINED and cannot produce an aggregate.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
import math
import os
import shutil
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

from fundamental_discovery_common import (
    TRADE_DIRECTIONS,
    VERDICTS,
    atomic_json,
    file_sha256,
    full_block_claims,
    leading_word,
    parse_blocks,
    source_looks_verified,
    summary_screened_claims,
)


REQUIRED_FIELDS = (
    "SYMBOL",
    "EXCHANGE",
    "US_LISTED",
    "SECTOR",
    "VERDICT",
    "CONFIDENCE",
    "WHY CHEAP",
    "CHEAPNESS",
    "DIRECTION",
    "METRICS",
    "CATALYST",
    "KILLS IT",
    "SOURCES",
    "TRADE DIRECTION",
)
CONFIDENCE = frozenset({"low", "medium", "high"})
V2_ROUTE_FIELDS = ("EXEC_SYMBOL", "EXEC_EXCHANGE", "EXEC_CCY", "EXEC_ROUTE")
V2_REQUIRED_FIELDS = V2_ROUTE_FIELDS + (
    "STABLE_ISSUER_ID",
    "EXECUTION_STATUS",
    "MEASUREMENT_PERIOD_END",
    "CATALYST_DIRECTION",
    "OWN HISTORY",
    "VOLUME TREND",
    "LIQUIDITY",
)
RESEARCH_FEATURES = frozenset(
    {
        "event_queue",
        "filing_diff",
        "insurer_v1",
        "bank_v1",
        "investment_entity_v1",
        "regulated_utility_v1",
        "holdco_dualclass_v1",
        "resources_v1",
    }
)
QUEUE_CANDIDATE_FIELDS = frozenset(
    {
        "stable_issuer_id",
        "symbol",
        "economic_direction",
        "execution_status",
        "execution_symbol",
        "execution_venue",
        "quote_currency",
        "notional_usd",
        "sector",
        "catalyst_date",
        "thesis",
    }
)
REGISTERED_ALLOCATOR = Path("/Users/test/prospect/var/global_run/aggregate.py")
DEFAULT_CAPITAL_LEDGER = Path(
    "/Users/test/Applications/MultiVenueDesk/reports/fundamental_book.json"
)


class ManifestError(RuntimeError):
    pass


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def validate_block(block: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for field in REQUIRED_FIELDS:
        if not str(block.get(field, "")).strip():
            issues.append(f"missing {field}")
    if block.get("_duplicates"):
        issues.append("duplicate fields: " + ", ".join(block["_duplicates"]))

    verdict = leading_word(block.get("VERDICT")).lower()
    if verdict not in VERDICTS:
        issues.append(f"VERDICT must start with an exact allowed token, got {verdict!r}")
    confidence = leading_word(block.get("CONFIDENCE")).lower()
    if confidence not in CONFIDENCE:
        issues.append(f"CONFIDENCE must start low/medium/high, got {confidence!r}")
    trade = leading_word(block.get("TRADE DIRECTION")).upper()
    if trade not in TRADE_DIRECTIONS:
        issues.append(f"TRADE DIRECTION must start LONG/SHORT/NEITHER, got {trade!r}")
    if not source_looks_verified(block.get("SOURCES")):
        issues.append("SOURCES is absent or not verifiable")
    if "KILLS IT" not in block or len(str(block.get("KILLS IT", "")).strip()) < 8:
        issues.append("KILLS IT is not substantive")
    if _field(block, "NOTIONAL_USD", "NOTIONAL USD"):
        issues.append("research block must not supply NOTIONAL_USD; allocator receipt required")
    if any(str(block.get(field, "")).strip() for field in V2_ROUTE_FIELDS):
        for field in V2_REQUIRED_FIELDS:
            if not str(block.get(field, "")).strip():
                issues.append(f"incomplete v2 block: missing {field}")
        if not _field(block, "EVENT_DATE", "CATALYST_DATE", "CATALYST DATE"):
            issues.append("incomplete v2 block: missing EVENT_DATE")
        if not _field(block, "THESIS_REVIEW_DATE", "REVIEW_DATE", "REVIEW DATE"):
            issues.append("incomplete v2 block: missing THESIS_REVIEW_DATE")
        route = str(block.get("EXEC_ROUTE", "")).strip().lower()
        if route and not _valid_route(route) and route != "unverified":
            issues.append(f"EXEC_ROUTE is invalid: {route!r}")
        catalyst_direction = leading_word(block.get("CATALYST_DIRECTION")).lower()
        if catalyst_direction not in {"positive", "negative", "two_sided"}:
            issues.append(
                "CATALYST_DIRECTION must start positive/negative/two_sided, "
                f"got {catalyst_direction!r}"
            )
        execution_status = _field(block, "EXECUTION_STATUS", "EXECUTION STATUS").lower()
        if execution_status not in {"pending_session", "ready"}:
            issues.append("EXECUTION_STATUS must be pending_session or ready")
    unknown_features = sorted(_research_features(block) - RESEARCH_FEATURES)
    if unknown_features:
        issues.append("unknown RESEARCH_FEATURES: " + ", ".join(unknown_features))
    return issues


def _field(block: dict[str, Any], *names: str) -> str:
    for name in names:
        value = str(block.get(name, "")).strip()
        if value:
            return value
    return ""


def _iso_date(raw: str) -> str | None:
    try:
        return dt.date.fromisoformat(raw[:10]).isoformat()
    except (TypeError, ValueError):
        return None


def _research_features(block: dict[str, Any]) -> set[str]:
    raw = _field(block, "RESEARCH_FEATURES", "RESEARCH FEATURES")
    return {
        token.strip().lower()
        for token in raw.replace(";", ",").replace("|", ",").split(",")
        if token.strip().lower() not in {"", "none", "legacy_general"}
    }


def _event_date(block: dict[str, Any]) -> str | None:
    # EVENT_DATE is canonical: it is when the market learns the answer and is
    # therefore mapped to the queue's legacy catalyst_date field.
    return _iso_date(_field(block, "EVENT_DATE", "CATALYST_DATE", "CATALYST DATE"))


def _thesis_review_date(block: dict[str, Any]) -> str | None:
    return _iso_date(
        _field(block, "THESIS_REVIEW_DATE", "REVIEW_DATE", "REVIEW DATE")
    )


def _valid_route(raw: str) -> bool:
    route = raw.strip().lower()
    return route == "direct_local" or (route.startswith("adr:") and bool(route[4:].strip()))


def _substantive_kills_it(raw: str) -> bool:
    text = raw.strip().lower()
    return len(text) >= 20 and text not in {
        "n/a",
        "na",
        "none",
        "not applicable",
        "not researched",
        "not established",
    }


def _receipt_records(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("feature receipt file is missing or too large")
    text = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return [payload]
        if isinstance(payload, list) and all(isinstance(row, dict) for row in payload):
            return payload
    except json.JSONDecodeError:
        pass
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if line.strip():
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("feature receipt JSONL row is not an object")
            rows.append(row)
    return rows


def _evidence_date(record: dict[str, Any]) -> dt.date | None:
    for field in ("recorded_utc", "known_on", "latest_known_on", "as_of_date"):
        value = str(record.get(field) or "")
        parsed = _iso_date(value)
        if parsed:
            return dt.date.fromisoformat(parsed)
    return None


def _record_matches_feature(
    record: dict[str, Any], feature: str, stable_id: str, as_of: dt.date
) -> bool:
    evidence_date = _evidence_date(record)
    if evidence_date is None or evidence_date > as_of:
        return False
    if feature in RESEARCH_FEATURES - {"event_queue", "filing_diff"}:
        return (
            record.get("adapter_id") == feature
            and record.get("status") == "RESEARCH_READY"
            and str(record.get("stable_issuer_id") or "") == stable_id
        )
    if feature == "filing_diff":
        if not stable_id.upper().startswith("CIK:"):
            return False
        suffix = stable_id.split(":", 1)[1]
        if not suffix.isdigit():
            return False
        cik = suffix.lstrip("0")
        record_cik = str(record.get("cik") or "").lstrip("0")
        material_leads = [
            change
            for change in record.get("material_changes") or []
            if isinstance(change, dict) and change.get("status") == "MATERIAL_LEAD"
        ]
        return bool(
            cik
            and cik == record_cik
            and record.get("status") == "COMPLETE"
            and record.get("research_queue") is True
            and material_leads
        )
    return (
        feature == "event_queue"
        and record.get("status") == "EVIDENCE_READY"
        and str(record.get("stable_issuer_id") or "") == stable_id
    )


def _verify_adapter_observation(
    item: dict[str, Any], record: dict[str, Any], feature: str, stable_id: str, as_of: dt.date
) -> dict[str, Any]:
    meta = item.get("observation")
    if not isinstance(meta, dict):
        raise ValueError("adapter receipt lacks raw observation binding")
    path = Path(str(meta.get("path") or "")).resolve()
    expected_file_sha = str(meta.get("sha256") or "")
    if not expected_file_sha or file_sha256(path) != expected_file_sha:
        raise ValueError("raw adapter observation file hash mismatch")
    observation = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(observation, dict):
        raise ValueError("raw adapter observation must be an object")
    if observation.get("adapter_id") != feature or str(
        observation.get("stable_issuer_id") or ""
    ) != stable_id:
        raise ValueError("raw adapter observation issuer/adapter mismatch")
    observation_as_of = _iso_date(str(observation.get("as_of_date") or ""))
    if observation_as_of is None or dt.date.fromisoformat(observation_as_of) > as_of:
        raise ValueError("raw adapter observation is not point-in-time")
    observation_sha = _json_sha256(observation)
    if observation_sha != str(record.get("observation_sha256") or ""):
        raise ValueError("adapter result does not hash the bound raw observation")
    metrics = observation.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise ValueError("raw adapter observation lacks metrics")
    for name, metric in metrics.items():
        if not isinstance(metric, dict):
            raise ValueError(f"raw adapter metric {name} is not an object")
        filed = _iso_date(str(metric.get("filed_date") or ""))
        if filed is None or dt.date.fromisoformat(filed) > as_of:
            raise ValueError(f"raw adapter metric {name} is not point-in-time")
        if not str(metric.get("source_url") or "").startswith(("http://", "https://")):
            raise ValueError(f"raw adapter metric {name} lacks primary source")
    return {
        "path": str(path),
        "sha256": expected_file_sha,
        "observation_sha256": observation_sha,
        "as_of_date": observation_as_of,
    }


def _validate_filing_point_in_time(record: dict[str, Any], as_of: dt.date) -> None:
    latest_known = _iso_date(str(record.get("latest_known_on") or ""))
    if latest_known is None or dt.date.fromisoformat(latest_known) > as_of:
        raise ValueError("filing diff latest_known_on is not point-in-time")
    receipts = record.get("source_receipts")
    if not isinstance(receipts, dict) or set(receipts) != {"prior", "latest"}:
        raise ValueError("filing diff lacks prior/latest source hash receipts")
    for label in ("prior", "latest"):
        source = receipts.get(label)
        if not isinstance(source, dict):
            raise ValueError(f"filing diff {label} source receipt is invalid")
        digest = str(source.get("snapshot_sha256") or "")
        if len(digest) != 64 or any(char not in "0123456789abcdefABCDEF" for char in digest):
            raise ValueError(f"filing diff {label} snapshot hash is invalid")
        if not str(source.get("primary_source_url") or "").startswith(("http://", "https://")):
            raise ValueError(f"filing diff {label} lacks primary source URL")
        known = _iso_date(str(source.get("known_on") or ""))
        if known is None or dt.date.fromisoformat(known) > as_of:
            raise ValueError(f"filing diff {label} source is not point-in-time")
        if str(source.get("cik") or "").lstrip("0") != str(record.get("cik") or "").lstrip("0"):
            raise ValueError(f"filing diff {label} issuer binding mismatch")
    if _iso_date(str(receipts["latest"].get("known_on") or "")) != latest_known:
        raise ValueError("filing diff latest source date does not match diff")
    for change in record.get("material_changes") or []:
        if not isinstance(change, dict):
            raise ValueError("filing diff change is not an object")
        for label in ("prior", "latest"):
            metric = change.get(label)
            if not isinstance(metric, dict):
                continue
            for field in ("filed", "end"):
                value = _iso_date(str(metric.get(field) or ""))
                if value is None or dt.date.fromisoformat(value) > as_of:
                    raise ValueError(f"filing diff {label}.{field} is not point-in-time")


def _event_queue_match(
    path: Path,
    expected_record_sha: str,
    research_symbol: str,
    stable_id: str,
    block_event_date: str | None,
    as_of: dt.date,
) -> tuple[dict[str, Any], dt.date]:
    """Verify the dedicated event-evidence receipt and both bound source files."""

    receipt = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(receipt, dict):
        raise ValueError("event_queue receipt is not an object")
    if receipt.get("schema") != "tfo.discovery.event-evidence.v1":
        raise ValueError("event_queue receipt has unsupported schema")
    if receipt.get("status") != "EVIDENCE_READY" or receipt.get("paper_only") is not True:
        raise ValueError("event_queue receipt is not EVIDENCE_READY/PAPER-only")
    if receipt.get("non_signal") is not True:
        raise ValueError("event_queue receipt must remain non-signal evidence")
    if _json_sha256(receipt) != expected_record_sha:
        raise ValueError("event_queue receipt digest mismatch")
    if str(receipt.get("symbol") or "").upper() != research_symbol.upper():
        raise ValueError("event_queue research symbol mismatch")
    if str(receipt.get("stable_issuer_id") or "") != stable_id:
        raise ValueError("event_queue stable issuer mismatch")
    receipt_event_date = _iso_date(str(receipt.get("event_date") or ""))
    evidence_date = _iso_date(str(receipt.get("as_of_date") or ""))
    if (
        receipt_event_date is None
        or receipt_event_date != block_event_date
        or dt.date.fromisoformat(receipt_event_date) <= as_of
    ):
        raise ValueError("event_queue event_date is absent, mismatched, or not future")
    if evidence_date is None or dt.date.fromisoformat(evidence_date) > as_of:
        raise ValueError("event_queue receipt is not point-in-time")

    lifecycle_meta = receipt.get("lifecycle")
    source_meta = receipt.get("source")
    if not isinstance(lifecycle_meta, dict) or not isinstance(source_meta, dict):
        raise ValueError("event_queue lifecycle/source bindings are absent")
    lifecycle_path = Path(str(lifecycle_meta.get("path") or "")).resolve()
    source_path = Path(str(source_meta.get("path") or "")).resolve()
    if file_sha256(lifecycle_path) != lifecycle_meta.get("sha256"):
        raise ValueError("event_queue lifecycle hash mismatch")
    if file_sha256(source_path) != source_meta.get("sha256"):
        raise ValueError("event_queue source hash mismatch")
    lifecycle = json.loads(lifecycle_path.read_text(encoding="utf-8"))
    if lifecycle.get("schema") != "tfo.discovery.catalyst-lifecycle.v1":
        raise ValueError("event_queue lifecycle schema mismatch")
    if _iso_date(str(lifecycle.get("as_of") or "")) != evidence_date:
        raise ValueError("event_queue lifecycle as_of mismatch")
    event_matches = [
        event
        for event in lifecycle.get("events") or []
        if isinstance(event, dict)
        and _json_sha256(event) == receipt.get("event_sha256")
        and str(event.get("symbol") or "").upper() == research_symbol.upper()
        and receipt_event_date in (event.get("future_dates") or [])
    ]
    if len(event_matches) != 1:
        raise ValueError("event_queue lifecycle event digest is absent or not unique")
    event = event_matches[0]
    if (
        Path(str(event.get("source_path") or "")).resolve() != source_path
        or event.get("line") != source_meta.get("line")
    ):
        raise ValueError("event_queue lifecycle/source location mismatch")
    flags = set(event.get("flags") or [])
    if event.get("needs_review") or "future" not in flags or flags & {
        "unverified_source",
        "possible_killed",
        "stale_claim",
        "past",
        "due_today",
    }:
        raise ValueError("event_queue record is stale, unverified, killed, or due")
    return receipt, dt.date.fromisoformat(evidence_date)


def _verified_feature_receipts(
    block: dict[str, Any], as_of: dt.date
) -> tuple[list[dict[str, Any]], list[str]]:
    features = sorted(_research_features(block))
    raw = _field(block, "RESEARCH_FEATURE_RECEIPTS", "RESEARCH FEATURE RECEIPTS")
    if not features and not raw:
        return [], []
    reasons: list[str] = []
    try:
        items = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return [], ["RESEARCH_FEATURE_RECEIPTS is not valid JSON"]
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        return [], ["RESEARCH_FEATURE_RECEIPTS must be a JSON list of objects"]
    item_features = [str(item.get("feature") or "").strip().lower() for item in items]
    if item_features != features:
        reasons.append("feature receipt IDs do not exactly match RESEARCH_FEATURES")
    stable_id = _field(block, "STABLE_ISSUER_ID", "STABLE ISSUER ID")
    verified: list[dict[str, Any]] = []
    for item, feature in zip(items, item_features):
        path = Path(str(item.get("path") or "")).resolve()
        expected_file_sha = str(item.get("sha256") or "")
        expected_record_sha = str(item.get("record_sha256") or "")
        try:
            if feature not in RESEARCH_FEATURES:
                raise ValueError("feature is not registered")
            if not expected_file_sha or file_sha256(path) != expected_file_sha:
                raise ValueError("feature receipt file hash mismatch")
            if feature == "event_queue":
                record, evidence_date = _event_queue_match(
                    path,
                    expected_record_sha,
                    str(block.get("_symbol") or ""),
                    stable_id,
                    _event_date(block),
                    as_of,
                )
            else:
                records = _receipt_records(path)
                matches = [
                    record
                    for record in records
                    if _record_matches_feature(record, feature, stable_id, as_of)
                    and _json_sha256(record) == expected_record_sha
                ]
                if len(matches) != 1:
                    raise ValueError("no unique point-in-time evidence record match")
                record = matches[0]
                evidence_date = _evidence_date(record)
            raw_observation = None
            if feature in RESEARCH_FEATURES - {"event_queue", "filing_diff"}:
                raw_observation = _verify_adapter_observation(
                    item, record, feature, stable_id, as_of
                )
            elif feature == "filing_diff":
                _validate_filing_point_in_time(record, as_of)
            verified.append(
                {
                    "feature": feature,
                    "path": str(path),
                    "sha256": expected_file_sha,
                    "record_sha256": expected_record_sha,
                    "evidence_status": record.get("status") or "VERIFIED_EVENT_ALERT",
                    "evidence_date": evidence_date.isoformat(),
                    "raw_observation": raw_observation,
                }
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            reasons.append(f"{feature or '(missing feature)'} receipt refused: {exc}")
    return verified, reasons


def candidate_from_block(
    block: dict[str, Any],
    *,
    allocator_notional_usd: float | int | str | None,
    allocator_sector: str | None,
    as_of: dt.date,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Build one queue candidate using only an external allocator notional.

    Verdict, direction, catalyst and route are recomputed from the verified
    source block.  Notional is a required function argument and is never read
    from research text or a serialized manifest candidate.
    """

    verdict = leading_word(block.get("VERDICT")).lower()
    trade = leading_word(block.get("TRADE DIRECTION")).upper()
    catalyst_direction = leading_word(block.get("CATALYST_DIRECTION")).lower()
    reasons: list[str] = []
    if verdict != "undervalued":
        reasons.append("exact verdict is not undervalued")
    if trade != "LONG":
        reasons.append("economic direction is not LONG")
    if catalyst_direction != "positive":
        reasons.append("catalyst direction is not positive")
    execution_symbol = _field(block, "EXEC_SYMBOL")
    execution_venue = _field(block, "EXEC_EXCHANGE")
    quote_currency = _field(block, "EXEC_CCY")
    execution_route = _field(block, "EXEC_ROUTE").lower()
    stable_issuer_id = _field(block, "STABLE_ISSUER_ID", "STABLE ISSUER ID")
    execution_status = _field(block, "EXECUTION_STATUS", "EXECUTION STATUS").lower()
    catalyst_date = _event_date(block)
    review_date = _thesis_review_date(block)
    measurement_period_end = _iso_date(_field(block, "MEASUREMENT_PERIOD_END"))
    feature_receipts, feature_issues = _verified_feature_receipts(block, as_of)
    reasons.extend(feature_issues)
    try:
        notional_usd = float(allocator_notional_usd)
    except (TypeError, ValueError):
        notional_usd = math.nan

    for label, value in (
        ("stable_issuer_id", stable_issuer_id),
        ("execution_symbol", execution_symbol),
        ("execution_venue", execution_venue),
        ("quote_currency", quote_currency),
    ):
        if not value:
            reasons.append(f"missing {label}")
    if execution_route == "unverified" or not _valid_route(execution_route):
        reasons.append("execution_route is absent, invalid, or unverified")
    if execution_status not in {"pending_session", "ready"}:
        reasons.append("execution_status must be pending_session or ready")
    if catalyst_date is None or dt.date.fromisoformat(catalyst_date) <= as_of:
        reasons.append("catalyst_date must be exact and future")
    if review_date is None or dt.date.fromisoformat(review_date) <= as_of:
        reasons.append("review_date must be exact and future")
    if measurement_period_end is None:
        reasons.append("measurement_period_end must be an exact date")
    if not _substantive_kills_it(_field(block, "KILLS IT")):
        reasons.append("KILLS IT is not substantive")
    if not math.isfinite(notional_usd) or notional_usd <= 0:
        reasons.append("positive notional_usd is missing from deterministic allocator receipt")
    if allocator_sector is None:
        reasons.append("sector is not classified by the registered allocator")
    if reasons:
        return None, reasons

    candidate = {
        "stable_issuer_id": stable_issuer_id,
        # fundamental_session_queue requires symbol to be the exact selected
        # execution symbol.  The issuer identity stays in stable_issuer_id.
        "symbol": execution_symbol,
        "economic_direction": "LONG",
        "execution_status": execution_status,
        "execution_symbol": execution_symbol,
        "execution_venue": execution_venue,
        "quote_currency": quote_currency,
        "notional_usd": notional_usd,
        "sector": allocator_sector,
        "research_sector": _field(block, "SECTOR"),
        "catalyst_date": catalyst_date,
        "review_date": review_date,
        "event_date": catalyst_date,
        "measurement_period_end": measurement_period_end,
        "thesis_review_date": review_date,
        "research_features": sorted(_research_features(block)),
        "research_feature_receipts": feature_receipts,
        "thesis": " ".join(
            part
            for part in (
                _field(block, "WHY CHEAP"),
                _field(block, "CATALYST"),
                f"KILLS IT: {_field(block, 'KILLS IT')}",
            )
            if part
        ),
        # Additional receipt fields are harmless to the queue and preserve the
        # route and originating research symbol exactly.
        "execution_route": execution_route,
        "research_symbol": block.get("_symbol"),
    }
    missing = sorted(key for key in QUEUE_CANDIDATE_FIELDS if candidate.get(key) in (None, ""))
    if missing:
        return None, ["queue candidate missing: " + ", ".join(missing)]
    return candidate, []


def _batch_digest(file_records: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for record in file_records:
        digest.update(record["name"].encode("utf-8"))
        digest.update(record["sha256"].encode("ascii"))
    return digest.hexdigest()


def build_manifest(
    run_dir: str | Path,
    *,
    frontier: str,
    batch_id: str | None = None,
) -> dict[str, Any]:
    root = Path(run_dir).resolve()
    files = sorted(path for path in root.glob("blocks_*.md") if path.is_file())
    file_records: list[dict[str, Any]] = []
    quarantine: list[dict[str, Any]] = []
    total_blocks = 0
    for path in files:
        blocks, parse_issues = parse_blocks(path)
        total_blocks += len(blocks)
        block_issues: list[dict[str, Any]] = []
        for block in blocks:
            issues = validate_block(block)
            if issues:
                block_issues.append(
                    {
                        "symbol": block.get("_symbol") or "(missing)",
                        "line": block.get("_start_line"),
                        "issues": issues,
                    }
                )
        full_claims = full_block_claims(path)
        claim_mismatches = [claim for claim in full_claims if claim != len(blocks)]
        record = {
            "name": path.name,
            "path": str(path),
            "sha256": file_sha256(path),
            "bytes": path.stat().st_size,
            "blocks": len(blocks),
            "summary_screened_claims": summary_screened_claims(path),
            "full_block_claims": full_claims,
            "parse_issues": parse_issues,
            "block_issues": block_issues,
            "full_block_claim_mismatches": claim_mismatches,
        }
        file_records.append(record)
        if not blocks:
            quarantine.append({"file": path.name, "reason": "no SYMBOL blocks"})
        if parse_issues:
            quarantine.append({"file": path.name, "reason": "parse issues", "detail": parse_issues})
        if block_issues:
            quarantine.append(
                {"file": path.name, "reason": "incomplete/invalid blocks", "detail": block_issues}
            )
        if claim_mismatches:
            quarantine.append(
                {
                    "file": path.name,
                    "reason": "claimed FULL block count does not match physical blocks",
                    "claimed": claim_mismatches,
                    "actual": len(blocks),
                }
            )

    if not files:
        quarantine.append({"file": None, "reason": "no blocks_*.md files"})
    digest = _batch_digest(file_records)
    resolved_batch_id = batch_id or f"{root.name}-{digest[:12]}"
    return {
        "schema": "tfo.discovery.batch-manifest.v1",
        "created_utc": utc_now(),
        "batch_id": resolved_batch_id,
        "frontier": frontier,
        "run_dir": str(root),
        "status": "quarantined" if quarantine else "complete",
        # Research sealing never emits execution candidates.  A separate
        # allocator-enrichment command reparses these exact source hashes and
        # supplies notionals from a bound deterministic receipt.
        "candidates": [],
        "candidate_held_out": [],
        "candidate_stage": "awaiting_allocator_enrichment",
        "batch_sha256": digest,
        "files": file_records,
        "file_count": len(files),
        "block_count": total_blocks,
        "quarantine_reasons": quarantine,
        "compatibility": (
            "legacy §6 blocks remain research evidence; once any v2 route field is declared "
            "the strict v2 contract applies. This sealed research manifest never contains "
            "execution candidates or notionals; enrich recomputes both eligibility and exact "
            "routes from verified blocks plus a separate deterministic allocator receipt"
        ),
        "non_execution": "manifest only; no sizing, ledger mutation, or trade",
    }


def write_manifest(path: str | Path, manifest: dict[str, Any]) -> None:
    atomic_json(path, manifest)


def _verify_manifest_files(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    root = Path(manifest["run_dir"]).resolve()
    failures: list[dict[str, Any]] = []
    for record in manifest.get("files") or []:
        path = (root / record["name"]).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            failures.append({"file": record["name"], "reason": "path escapes run_dir"})
            continue
        if not path.is_file():
            failures.append({"file": record["name"], "reason": "missing after seal"})
            continue
        actual_hash = file_sha256(path)
        if actual_hash != record["sha256"]:
            failures.append(
                {
                    "file": record["name"],
                    "reason": "hash changed after seal",
                    "expected": record["sha256"],
                    "actual": actual_hash,
                }
            )
        if path.stat().st_size != record["bytes"]:
            failures.append({"file": record["name"], "reason": "size changed after seal"})
    return failures


def _verify_complete_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema") != "tfo.discovery.batch-manifest.v1":
        raise ManifestError("unsupported research manifest schema")
    if manifest.get("status") != "complete":
        raise ManifestError("batch is quarantined; operation refused")
    if manifest.get("candidates") not in (None, []):
        raise ManifestError(
            "serialized research-manifest candidates are forbidden; "
            "eligibility must be recomputed during allocator enrichment"
        )
    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise ManifestError("complete manifest has no file records")
    names = [str(record.get("name") or "") for record in records]
    if any(not name for name in names) or len(set(names)) != len(names):
        raise ManifestError("manifest file names are absent or duplicated")
    if int(manifest.get("file_count", -1)) != len(records):
        raise ManifestError("manifest file_count does not reconcile")
    if int(manifest.get("block_count", -1)) != sum(int(record.get("blocks", -1)) for record in records):
        raise ManifestError("manifest block_count does not reconcile")
    if manifest.get("batch_sha256") != _batch_digest(records):
        raise ManifestError("manifest batch digest does not reconcile to file records")
    root = Path(str(manifest.get("run_dir") or "")).resolve()
    actual_names = sorted(path.name for path in root.glob("blocks_*.md") if path.is_file())
    if sorted(names) != actual_names:
        raise ManifestError("manifest file set differs from run directory")
    changed = _verify_manifest_files(manifest)
    if changed:
        raise ManifestError("manifest verification failed: " + json.dumps(changed, sort_keys=True))


def _read_verified_blocks(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    _verify_complete_manifest(manifest)
    all_blocks: list[dict[str, Any]] = []
    root = Path(manifest["run_dir"]).resolve()
    for record in manifest["files"]:
        blocks, parse_issues = parse_blocks(root / record["name"])
        if parse_issues:
            raise ManifestError(f"parse changed after seal in {record['name']}")
        if len(blocks) != int(record["blocks"]):
            raise ManifestError(f"block count changed after seal in {record['name']}")
        for block in blocks:
            issues = validate_block(block)
            if issues:
                raise ManifestError(
                    f"strict validation changed after seal for {block.get('_symbol')}: {issues}"
                )
        all_blocks.extend(blocks)
    return all_blocks


def _candidate_signature(block: dict[str, Any]) -> tuple[str, ...]:
    return (
        leading_word(block.get("VERDICT")).lower(),
        leading_word(block.get("TRADE DIRECTION")).upper(),
        leading_word(block.get("CATALYST_DIRECTION")).lower(),
        _field(block, "STABLE_ISSUER_ID", "STABLE ISSUER ID"),
        _field(block, "EXEC_SYMBOL").upper(),
        _field(block, "EXEC_EXCHANGE").upper(),
        _field(block, "EXEC_CCY").upper(),
        _field(block, "EXEC_ROUTE").lower(),
        _field(block, "EVENT_DATE", "CATALYST_DATE", "CATALYST DATE"),
        _field(block, "THESIS_REVIEW_DATE", "REVIEW_DATE", "REVIEW DATE"),
        ",".join(sorted(_research_features(block))),
    )


def _conflicts(blocks: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    symbol_signatures: dict[str, set[tuple[str, ...]]] = {}
    issuer_signatures: dict[str, set[tuple[str, ...]]] = {}
    for block in blocks:
        symbol = str(block.get("_symbol") or "").upper()
        stable_id = _field(block, "STABLE_ISSUER_ID", "STABLE ISSUER ID")
        signature = _candidate_signature(block)
        symbol_signatures.setdefault(symbol, set()).add(signature)
        if stable_id:
            issuer_signatures.setdefault(stable_id, set()).add(signature)
    return (
        {symbol for symbol, values in symbol_signatures.items() if len(values) > 1},
        {issuer for issuer, values in issuer_signatures.items() if len(values) > 1},
    )


def aggregate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    all_blocks = _read_verified_blocks(manifest)
    conflicted_symbols, conflicted_issuers = _conflicts(all_blocks)

    seen: dict[str, dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    corroborations: list[dict[str, Any]] = []
    for block in all_blocks:
        symbol = block["_symbol"].upper()
        verdict = leading_word(block.get("VERDICT")).lower()
        trade = leading_word(block.get("TRADE DIRECTION")).upper()
        current = {
            "symbol": symbol,
            "verdict": verdict,
            "trade": trade,
            "candidate_signature": _candidate_signature(block),
            "stable_issuer_id": _field(block, "STABLE_ISSUER_ID", "STABLE ISSUER ID"),
            "file": block["_file"],
        }
        if symbol not in seen:
            seen[symbol] = current
            continue
        previous = seen[symbol]
        duplicates.append({"symbol": symbol, "first": previous, "second": current})
        if previous["candidate_signature"] == current["candidate_signature"]:
            corroborations.append({"symbol": symbol, "first": previous, "second": current})
        else:
            conflicts.append({"symbol": symbol, "first": previous, "second": current})

    conflicted = {item["symbol"] for item in conflicts} | conflicted_symbols
    tally = Counter(item["verdict"] for item in seen.values())
    trade_tally = Counter(item["trade"] for item in seen.values())
    economic_longs = sorted(
        symbol
        for symbol, item in seen.items()
        if item["verdict"] == "undervalued"
        and item["trade"] == "LONG"
        and symbol not in conflicted
        and item["stable_issuer_id"] not in conflicted_issuers
    )
    undervalued_pending = sorted(
        symbol
        for symbol, item in seen.items()
        if item["verdict"] == "undervalued" and item["trade"] != "LONG"
    )
    return {
        "schema": "tfo.discovery.strict-aggregate.v1",
        "generated_utc": utc_now(),
        "batch_id": manifest["batch_id"],
        "batch_sha256": manifest["batch_sha256"],
        "frontier": manifest["frontier"],
        "manifest_status": manifest["status"],
        "status": "complete",
        # Aggregate is research-only. Execution candidates can be emitted only
        # by enrich_manifest after a separate allocator receipt is verified.
        "candidates": [],
        "candidate_held_out": [],
        "candidate_stage": "awaiting_allocator_enrichment",
        "blocks_parsed": len(all_blocks),
        "unique_symbols": len(seen),
        "tally": dict(tally),
        "trade_tally": dict(trade_tally),
        "duplicates": duplicates,
        "conflicts_held_out": conflicts,
        "conflicting_issuers_held_out": sorted(conflicted_issuers),
        "corroborations": corroborations,
        "economic_long_candidates_not_sized": economic_longs,
        "undervalued_but_not_long": undervalued_pending,
        "non_execution": (
            "strict discovery aggregate only; no capital read, sizing, ledger mutation, or trade"
        ),
    }


def _json_sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _reconciles(expected: Any, actual: Any, *, tolerance: float = 0.005) -> bool:
    """Compare deterministic accounting structures without key-order float noise."""

    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return math.isfinite(float(expected)) and math.isfinite(float(actual)) and math.isclose(
            float(expected), float(actual), rel_tol=0.0, abs_tol=tolerance
        )
    if isinstance(expected, dict) and isinstance(actual, dict):
        return set(expected) == set(actual) and all(
            _reconciles(expected[key], actual[key], tolerance=tolerance) for key in expected
        )
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(
            _reconciles(left, right, tolerance=tolerance)
            for left, right in zip(expected, actual)
        )
    return expected == actual


def _registered_allocator_module(binding: dict[str, Any] | None = None) -> Any:
    if not REGISTERED_ALLOCATOR.is_file():
        raise ManifestError("registered allocator module is unavailable")
    if binding is not None and (
        Path(str(binding.get("path") or "")).resolve() != REGISTERED_ALLOCATOR.resolve()
        or file_sha256(REGISTERED_ALLOCATOR) != binding.get("sha256")
    ):
        raise ManifestError("registered allocator classifier binding mismatch")
    spec = importlib.util.spec_from_file_location("tfo_registered_allocator", REGISTERED_ALLOCATOR)
    if spec is None or spec.loader is None:
        raise ManifestError("registered allocator module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if binding is not None and file_sha256(REGISTERED_ALLOCATOR) != binding.get("sha256"):
        raise ManifestError("registered allocator changed while loading classifier")
    return module


def _registered_capital_contract(capital_snapshot: dict[str, Any]) -> dict[str, Any]:
    module = _registered_allocator_module()
    try:
        return module.capital_contract_from_ledger(capital_snapshot)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ManifestError(f"registered allocator rejected capital snapshot: {exc}") from exc


def run_registered_allocator(
    manifest: dict[str, Any],
    *,
    run_dir: str | Path,
    capital_ledger_path: str | Path,
    capital_snapshot_output: str | Path,
    allocator_output_path: str | Path,
    quote_fetcher: Any | None = None,
) -> dict[str, Any]:
    """Run the unchanged registered allocator against one explicit sealed run.

    The imported module's RUN and LEDGER globals are scoped to this invocation;
    its source file is never edited.  The live ledger must remain byte-identical
    throughout the read-only sizing run.
    """

    _read_verified_blocks(manifest)
    explicit_run = Path(run_dir).resolve()
    if explicit_run != Path(manifest["run_dir"]).resolve():
        raise ManifestError("explicit allocator run_dir differs from sealed manifest")
    ready_path = explicit_run / ".tfo_batch_ready.json"
    ready_hash_before = _verify_ready_receipt_if_present(explicit_run, ready_path)
    ledger = Path(capital_ledger_path).resolve()
    try:
        ledger_hash_before = file_sha256(ledger)
        capital_snapshot = json.loads(ledger.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManifestError(f"capital ledger is unavailable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"capital ledger is not valid JSON: {exc}") from exc
    if not isinstance(capital_snapshot, dict):
        raise ManifestError("capital ledger is not an object")
    snapshot_path = Path(capital_snapshot_output).resolve()
    atomic_json(snapshot_path, capital_snapshot)
    destination = Path(allocator_output_path).resolve()
    protected_paths = {(explicit_run / "aggregate.json").resolve()}
    protected_paths.update(
        (explicit_run / record["name"]).resolve() for record in manifest["files"]
    )
    if destination in protected_paths:
        raise ManifestError("allocator output must be additive and outside sealed sources/aggregate")
    started_utc = utc_now()
    stdout = io.StringIO()
    try:
        with tempfile.TemporaryDirectory(prefix="tfo-allocator-stage-") as temporary:
            staging = Path(temporary).resolve()
            for record in manifest["files"]:
                source = (explicit_run / record["name"]).resolve()
                staged = staging / record["name"]
                shutil.copy2(source, staged)
                if file_sha256(staged) != record["sha256"]:
                    raise ManifestError(f"staged source hash mismatch: {record['name']}")
            spec = importlib.util.spec_from_file_location(
                "tfo_registered_allocator_run", REGISTERED_ALLOCATOR
            )
            if spec is None or spec.loader is None:
                raise ManifestError("registered allocator module cannot be loaded")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.RUN = str(staging)
            module.LEDGER = str(ledger)
            # ``load_capital_contract`` captured LEDGER as a default argument.
            module.load_capital_contract.__defaults__ = (str(ledger),)
            if quote_fetcher is not None:
                module._live_quote_fetcher = quote_fetcher
            with contextlib.redirect_stdout(stdout):
                return_code = module.main()
            staged_output = staging / "aggregate.json"
            if not staged_output.is_file():
                raise ManifestError("registered allocator did not produce staged aggregate.json")
            payload = json.loads(staged_output.read_text(encoding="utf-8"))
    except ManifestError:
        raise
    except Exception as exc:  # noqa: BLE001 - convert allocator failure to quarantine
        raise ManifestError(
            f"registered allocator failed: {type(exc).__name__}: {exc}"
        ) from exc
    finished_utc = utc_now()
    if return_code not in (None, 0):
        raise ManifestError(
            f"registered allocator refused the run (exit {return_code}): "
            f"{stdout.getvalue()[-500:]}"
        )
    try:
        ledger_hash_after = file_sha256(ledger)
    except OSError as exc:
        raise ManifestError(f"capital ledger disappeared during allocator run: {exc}") from exc
    if ledger_hash_after != ledger_hash_before:
        raise ManifestError("capital ledger changed during allocator run")
    if not isinstance(payload, dict):
        raise ManifestError("registered allocator output is not an object")
    changed_sources = _verify_manifest_files(manifest)
    if changed_sources:
        raise ManifestError("sealed source changed during allocator staging")
    ready_hash_after = _verify_ready_receipt_if_present(explicit_run, ready_path)
    if ready_hash_after != ready_hash_before:
        raise ManifestError("sealed READY receipt changed during allocator staging")
    atomic_json(destination, payload)
    return {
        "schema": "tfo.fundamental.allocator-run.v1",
        "status": "complete",
        "paper_only": True,
        "batch_id": manifest["batch_id"],
        "batch_sha256": manifest["batch_sha256"],
        "run_dir": str(explicit_run),
        "capital_ledger": {
            "path": str(ledger),
            "sha256_before": ledger_hash_before,
            "sha256_after": ledger_hash_after,
        },
        "capital_snapshot": {
            "path": str(snapshot_path),
            "sha256": file_sha256(snapshot_path),
        },
        "allocator_module": {
            "path": str(REGISTERED_ALLOCATOR.resolve()),
            "sha256": file_sha256(REGISTERED_ALLOCATOR),
        },
        "allocator_output": {
            "path": str(destination),
            "sha256": file_sha256(destination),
        },
        "started_utc": started_utc,
        "finished_utc": finished_utc,
        "stdout_sha256": hashlib.sha256(stdout.getvalue().encode("utf-8")).hexdigest(),
        "non_execution": "deterministic sizing output only; no ledger mutation or trade",
    }


def _verify_ready_receipt_if_present(run_dir: Path, ready_path: Path) -> str | None:
    """Verify, but never create or update, an existing sealed READY receipt."""

    if not ready_path.exists():
        return None
    try:
        ready = json.loads(ready_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"sealed READY receipt is unreadable: {exc}") from exc
    if ready.get("status") != "READY" or ready.get("schema") != 1:
        raise ManifestError("sealed READY receipt is not READY/schema 1")
    if Path(str(ready.get("run_dir") or "")).resolve() != run_dir:
        raise ManifestError("sealed READY receipt run_dir mismatch")
    aggregate = ready.get("aggregate")
    if not isinstance(aggregate, dict):
        raise ManifestError("sealed READY receipt lacks aggregate binding")
    aggregate_path = run_dir / str(aggregate.get("name") or "")
    if aggregate_path.resolve() != (run_dir / "aggregate.json").resolve() or file_sha256(
        aggregate_path
    ) != aggregate.get("sha256"):
        raise ManifestError("sealed READY aggregate hash mismatch")
    blocks = ready.get("blocks")
    if not isinstance(blocks, list):
        raise ManifestError("sealed READY receipt lacks block bindings")
    for row in blocks:
        if not isinstance(row, dict):
            raise ManifestError("sealed READY block binding is invalid")
        path = (run_dir / str(row.get("name") or "")).resolve()
        try:
            path.relative_to(run_dir)
        except ValueError as exc:
            raise ManifestError("sealed READY block path escapes run_dir") from exc
        if file_sha256(path) != row.get("sha256"):
            raise ManifestError("sealed READY block hash mismatch")
    return file_sha256(ready_path)


def _verified_allocator_run(
    manifest: dict[str, Any], run_receipt: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    if run_receipt.get("schema") != "tfo.fundamental.allocator-run.v1":
        raise ManifestError("unsupported allocator-run receipt schema")
    if run_receipt.get("status") != "complete" or run_receipt.get("paper_only") is not True:
        raise ManifestError("allocator-run receipt is not complete/PAPER-only")
    if run_receipt.get("batch_id") != manifest.get("batch_id") or run_receipt.get(
        "batch_sha256"
    ) != manifest.get("batch_sha256"):
        raise ManifestError("allocator-run receipt batch binding mismatch")
    if Path(str(run_receipt.get("run_dir") or "")).resolve() != Path(
        manifest["run_dir"]
    ).resolve():
        raise ManifestError("allocator-run receipt run_dir mismatch")
    run_dir = Path(manifest["run_dir"]).resolve()
    _verify_ready_receipt_if_present(run_dir, run_dir / ".tfo_batch_ready.json")
    try:
        started = dt.datetime.fromisoformat(
            str(run_receipt.get("started_utc") or "").replace("Z", "+00:00")
        )
        finished = dt.datetime.fromisoformat(
            str(run_receipt.get("finished_utc") or "").replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ManifestError("allocator-run receipt timestamps are invalid") from exc
    if finished < started:
        raise ManifestError("allocator-run receipt timestamps are reversed")
    ledger_meta = run_receipt.get("capital_ledger")
    if not isinstance(ledger_meta, dict) or not ledger_meta.get("sha256_before") or (
        ledger_meta.get("sha256_before") != ledger_meta.get("sha256_after")
    ):
        raise ManifestError("capital ledger changed during allocator run")
    bindings: dict[str, tuple[Path, str]] = {}
    for label in ("allocator_module", "allocator_output", "capital_snapshot"):
        meta = run_receipt.get(label)
        if not isinstance(meta, dict):
            raise ManifestError(f"allocator-run receipt lacks {label} binding")
        path = Path(str(meta.get("path") or "")).resolve()
        expected = str(meta.get("sha256") or "")
        if label == "allocator_module" and path != REGISTERED_ALLOCATOR.resolve():
            raise ManifestError("allocator-run receipt module is not registered")
        if label == "allocator_output":
            protected = {(run_dir / "aggregate.json").resolve()}
            protected.update(
                (run_dir / row["name"]).resolve()
                for row in manifest["files"]
            )
            if path in protected:
                raise ManifestError("allocator-run output overwrote a sealed run artifact")
        try:
            actual = file_sha256(path)
        except OSError as exc:
            raise ManifestError(f"allocator-run {label} is unavailable: {exc}") from exc
        if not expected or actual != expected:
            raise ManifestError(f"allocator-run {label} hash mismatch")
        bindings[label] = (path, expected)
    try:
        allocator_output = json.loads(bindings["allocator_output"][0].read_text(encoding="utf-8"))
        capital_snapshot = json.loads(bindings["capital_snapshot"][0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"allocator-run bound JSON is unreadable: {exc}") from exc
    if not isinstance(allocator_output, dict) or not isinstance(capital_snapshot, dict):
        raise ManifestError("allocator-run output/snapshot must be objects")
    if int(allocator_output.get("blocks_parsed", -1)) != int(manifest["block_count"]):
        raise ManifestError("allocator-run block count mismatch")
    if allocator_output.get("parse_failures"):
        raise ManifestError("allocator-run output contains parse failures")
    if not isinstance(allocator_output.get("long_alloc"), dict):
        raise ManifestError("allocator-run output lacks long_alloc")
    latest_source_mtime = max(
        (Path(manifest["run_dir"]) / record["name"]).stat().st_mtime
        for record in manifest["files"]
    )
    if bindings["allocator_output"][0].stat().st_mtime + 1e-6 < latest_source_mtime:
        raise ManifestError("allocator-run output predates a sealed research source")
    computed_contract = _registered_capital_contract(capital_snapshot)
    if not _reconciles(allocator_output.get("capital_contract"), computed_contract):
        raise ManifestError("allocator-run capital contract does not reconcile to snapshot")
    return allocator_output, capital_snapshot


def _verify_bound_allocator_artifacts(
    manifest: dict[str, Any], receipt: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    run_meta = receipt.get("allocator_run_receipt")
    if not isinstance(run_meta, dict):
        raise ManifestError("allocator receipt lacks allocator-run receipt binding")
    run_path = Path(str(run_meta.get("path") or "")).resolve()
    try:
        if file_sha256(run_path) != run_meta.get("sha256"):
            raise ManifestError("allocator-run receipt file hash mismatch")
        run_receipt = json.loads(run_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"allocator-run receipt is unreadable: {exc}") from exc
    allocator_output, capital_snapshot = _verified_allocator_run(manifest, run_receipt)
    for label in ("allocator_module", "allocator_output", "capital_snapshot"):
        if receipt.get(label) != run_receipt.get(label):
            raise ManifestError(f"allocator receipt {label} differs from sealed run receipt")
    return allocator_output, capital_snapshot


def build_allocator_receipt(
    manifest: dict[str, Any],
    *,
    allocator_run_receipt_path: str | Path,
    current_date: dt.date | None = None,
) -> dict[str, Any]:
    """Bind a sealed allocator run without rereading or rewriting live capital."""

    blocks = _read_verified_blocks(manifest)
    run_path = Path(allocator_run_receipt_path).resolve()
    try:
        run_receipt = json.loads(run_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"allocator-run receipt is unreadable: {exc}") from exc
    allocator_output, _ = _verified_allocator_run(manifest, run_receipt)
    current_date = current_date or dt.date.today()
    run_finished = dt.datetime.fromisoformat(
        str(run_receipt["finished_utc"]).replace("Z", "+00:00")
    )
    run_date = run_finished.date()
    now_utc = dt.datetime.now(dt.timezone.utc)
    if current_date not in {run_date, run_date + dt.timedelta(days=1)} or not (
        dt.timedelta(minutes=-5) <= now_utc - run_finished <= dt.timedelta(hours=6)
    ):
        raise ManifestError("allocator-run receipt is not current")
    provisional = {
        "schema": "tfo.fundamental.allocator-notionals.v1",
        "status": "complete",
        "source": "deterministic_allocator",
        "generated_utc": utc_now(),
        "as_of_date": current_date.isoformat(),
        "batch_id": manifest["batch_id"],
        "batch_sha256": manifest["batch_sha256"],
        "allocator_run_receipt": {"path": str(run_path), "sha256": file_sha256(run_path)},
        "allocator_module": run_receipt["allocator_module"],
        "allocator_output": run_receipt["allocator_output"],
        "capital_snapshot": run_receipt["capital_snapshot"],
        "allocations": [],
        "paper_only": True,
        "non_execution": "receipt binding only; no sizing, ledger mutation, or trade",
    }
    _verify_bound_allocator_artifacts(manifest, provisional)
    blocks_by_execution: dict[str, list[dict[str, Any]]] = {}
    for block in blocks:
        symbol = _field(block, "EXEC_SYMBOL").upper()
        if symbol:
            blocks_by_execution.setdefault(symbol, []).append(block)
    seen_issuers: set[str] = set()
    allocations = []
    for raw_symbol, raw_notional in sorted(allocator_output["long_alloc"].items()):
        symbol = str(raw_symbol).upper()
        matches = blocks_by_execution.get(symbol) or []
        if len(matches) != 1:
            raise ManifestError(f"allocator symbol {symbol} lacks one exact v2 source block")
        stable_id = _field(matches[0], "STABLE_ISSUER_ID", "STABLE ISSUER ID")
        if not stable_id or stable_id in seen_issuers:
            raise ManifestError(f"allocator symbol {symbol} has absent/duplicate stable issuer")
        try:
            notional = float(raw_notional)
        except (TypeError, ValueError) as exc:
            raise ManifestError(f"allocator notional is invalid for {symbol}") from exc
        if not math.isfinite(notional) or notional <= 0:
            raise ManifestError(f"allocator notional is not positive for {symbol}")
        seen_issuers.add(stable_id)
        allocations.append(
            {
                "stable_issuer_id": stable_id,
                "execution_symbol": symbol,
                "notional_usd": notional,
            }
        )
    provisional["allocations"] = allocations
    return provisional


def _allocator_map(
    manifest: dict[str, Any], receipt: dict[str, Any], current_date: dt.date
) -> tuple[dict[tuple[str, str], dict[str, Any]], str, dt.date]:
    if receipt.get("schema") != "tfo.fundamental.allocator-notionals.v1":
        raise ManifestError("unsupported allocator receipt schema")
    if receipt.get("status") != "complete":
        raise ManifestError("allocator receipt is not complete")
    if receipt.get("source") != "deterministic_allocator":
        raise ManifestError("allocator receipt source must be deterministic_allocator")
    if receipt.get("batch_id") != manifest.get("batch_id"):
        raise ManifestError("allocator batch_id does not match research manifest")
    if receipt.get("batch_sha256") != manifest.get("batch_sha256"):
        raise ManifestError("allocator batch hash does not match research manifest")
    try:
        receipt_date = dt.date.fromisoformat(str(receipt.get("as_of_date") or ""))
        generated_date = dt.datetime.fromisoformat(
            str(receipt.get("generated_utc") or "").replace("Z", "+00:00")
        ).astimezone().date()
    except ValueError as exc:
        raise ManifestError("allocator receipt lacks exact current dates") from exc
    if receipt_date != current_date or generated_date != current_date:
        raise ManifestError("allocator receipt is not current; historical as_of is forbidden")

    allocator_output, _ = _verify_bound_allocator_artifacts(manifest, receipt)

    rows = receipt.get("allocations")
    if not isinstance(rows, list):
        raise ManifestError("allocator allocations must be a list")
    allocations: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ManifestError("allocator row must be an object")
        stable_id = str(row.get("stable_issuer_id") or "").strip()
        symbol = str(row.get("execution_symbol") or "").strip().upper()
        if not stable_id or not symbol:
            raise ManifestError("allocator row lacks stable_issuer_id or execution_symbol")
        key = (stable_id, symbol)
        if key in allocations:
            raise ManifestError(f"duplicate allocator key: {stable_id}/{symbol}")
        allocations[key] = row
    sized = allocator_output.get("long_alloc")
    if not isinstance(sized, dict):
        raise ManifestError("allocator output lacks long_alloc")
    receipt_by_symbol = {
        str(row["execution_symbol"]).upper(): row.get("notional_usd") for row in allocations.values()
    }
    if set(receipt_by_symbol) != {str(symbol).upper() for symbol in sized}:
        raise ManifestError("allocator receipt symbols do not reconcile to allocator long_alloc")
    for symbol, value in sized.items():
        try:
            if not math.isclose(
                float(receipt_by_symbol[str(symbol).upper()]), float(value), abs_tol=0.005
            ):
                raise ManifestError(f"allocator notional mismatch for {symbol}")
        except (TypeError, ValueError) as exc:
            raise ManifestError(f"allocator notional is invalid for {symbol}") from exc
    return allocations, _json_sha256(receipt), receipt_date


def enrich_manifest(
    manifest: dict[str, Any],
    allocator_receipt: dict[str, Any],
    *,
    research_manifest_path: str | Path,
    allocator_receipt_path: str | Path,
    current_date: dt.date | None = None,
) -> dict[str, Any]:
    """Emit a session-queue-compatible manifest after deterministic sizing.

    Eligibility is recomputed from hash-verified research blocks.  Serialized
    candidates in the input manifest are forbidden, and research text is never
    allowed to supply notional.
    """

    research_manifest_path = Path(research_manifest_path).resolve()
    allocator_receipt_path = Path(allocator_receipt_path).resolve()
    try:
        manifest_on_disk = json.loads(research_manifest_path.read_text(encoding="utf-8"))
        receipt_on_disk = json.loads(allocator_receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"enrichment source receipt is unreadable: {exc}") from exc
    if _json_sha256(manifest_on_disk) != _json_sha256(manifest):
        raise ManifestError("research manifest argument differs from bound file")
    if _json_sha256(receipt_on_disk) != _json_sha256(allocator_receipt):
        raise ManifestError("allocator receipt argument differs from bound file")
    blocks = _read_verified_blocks(manifest)
    current_date = current_date or dt.date.today()
    allocations, allocation_sha, as_of = _allocator_map(
        manifest, allocator_receipt, current_date
    )
    allocator_module = _registered_allocator_module(allocator_receipt["allocator_module"])
    conflicted, conflicted_issuers = _conflicts(blocks)
    candidates: list[dict[str, Any]] = []
    candidate_held_out: list[dict[str, Any]] = []
    matched_allocation_keys: set[tuple[str, str]] = set()
    emitted_keys: set[tuple[str, str]] = set()

    for block in blocks:
        research_symbol = str(block.get("_symbol") or "").upper()
        stable_id = _field(block, "STABLE_ISSUER_ID", "STABLE ISSUER ID")
        execution_symbol = _field(block, "EXEC_SYMBOL").upper()
        key = (stable_id, execution_symbol)
        allocation = allocations.get(key) if stable_id and execution_symbol else None
        if allocation is not None:
            matched_allocation_keys.add(key)
        # The registered allocator sizes with the first 60 characters of its
        # normalized raw SECTOR label. Reuse its exact classifier and registry
        # so execution's concentration bucket is the one used during sizing.
        # Detailed research labels remain untouched in the sealed source.
        sector_raw = (_field(block, "SECTOR") or "unknown").strip()[:60]
        try:
            allocator_sector = allocator_module.trusted_sector(
                allocator_module.sector_key(sector_raw))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ManifestError(f"registered allocator sector classifier failed: {exc}") from exc
        candidate, reasons = candidate_from_block(
            block,
            allocator_notional_usd=(allocation or {}).get("notional_usd"),
            allocator_sector=allocator_sector,
            as_of=as_of,
        )
        if research_symbol in conflicted:
            candidate = None
            reasons = [*reasons, "conflicting duplicate research symbol"]
        if stable_id in conflicted_issuers:
            candidate = None
            reasons = [*reasons, "conflicting duplicate stable issuer"]
        if candidate is not None:
            if key in emitted_keys:
                continue
            emitted_keys.add(key)
            candidates.append(candidate)
            continue

        # Record potential economic signals and every attempted allocator row.
        # Routine rejects remain in the research aggregate without creating a
        # noisy execution holdout list.
        verdict = leading_word(block.get("VERDICT")).lower()
        trade = leading_word(block.get("TRADE DIRECTION")).upper()
        if allocation is not None or verdict == "undervalued" or trade == "LONG":
            candidate_held_out.append(
                {
                    "research_symbol": block.get("_symbol") or "(missing)",
                    "stable_issuer_id": stable_id or None,
                    "execution_symbol": execution_symbol or None,
                    "file": block.get("_file"),
                    "line": block.get("_start_line"),
                    "reasons": sorted(set(reasons)),
                }
            )

    unmatched_allocations = [
        {
            "stable_issuer_id": key[0],
            "execution_symbol": key[1],
            "notional_usd": row.get("notional_usd"),
            "reason": "no exact stable-issuer/execution-symbol block match",
        }
        for key, row in allocations.items()
        if key not in matched_allocation_keys
    ]
    candidates.sort(key=lambda row: (row["symbol"], row["stable_issuer_id"]))
    return {
        "schema": "tfo.discovery.execution-manifest.v1",
        "status": "complete",
        "paper_only": True,
        "generated_utc": utc_now(),
        "as_of": as_of.isoformat(),
        "batch_id": manifest["batch_id"],
        "batch_sha256": manifest["batch_sha256"],
        "research_manifest_sha256": _json_sha256(
            {
                key: manifest[key]
                for key in (
                    "schema",
                    "batch_id",
                    "batch_sha256",
                    "run_dir",
                    "files",
                    "file_count",
                    "block_count",
                )
            }
        ),
        "allocator_receipt_sha256": allocation_sha,
        "research_manifest_file": {
            "path": str(research_manifest_path),
            "sha256": file_sha256(research_manifest_path),
        },
        "allocator_receipt_file": {
            "path": str(allocator_receipt_path),
            "sha256": file_sha256(allocator_receipt_path),
        },
        "candidates": candidates,
        "candidate_held_out": candidate_held_out,
        "unmatched_allocations": unmatched_allocations,
        "conflicting_symbols_held_out": sorted(conflicted),
        "conflicting_issuers_held_out": sorted(conflicted_issuers),
        "rule": (
            "Execution-manifest enrichment only. Every row still requires the existing "
            "session queue, fresh live gate, dry run and portfolio controls before PAPER entry."
        ),
        "non_execution": "no sizing, ledger mutation, venue verification, or trade",
    }


def load_manifest(path: str | Path) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ManifestError(f"manifest unreadable: {type(exc).__name__}: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    seal = sub.add_parser("seal")
    seal.add_argument("--run-dir", required=True)
    seal.add_argument("--frontier", required=True)
    seal.add_argument("--batch-id")
    seal.add_argument("--manifest", required=True)

    aggregate = sub.add_parser("aggregate")
    aggregate.add_argument("--manifest", required=True)
    aggregate.add_argument("--output", required=True)

    run_allocator = sub.add_parser("run-allocator")
    run_allocator.add_argument("--manifest", required=True)
    run_allocator.add_argument("--run-dir", required=True)
    run_allocator.add_argument("--capital-ledger", default=str(DEFAULT_CAPITAL_LEDGER))
    run_allocator.add_argument("--capital-snapshot-output", required=True)
    run_allocator.add_argument("--allocator-output", required=True)
    run_allocator.add_argument("--run-receipt", required=True)

    bind = sub.add_parser("bind-allocator")
    bind.add_argument("--manifest", required=True)
    bind.add_argument("--allocator-run-receipt", required=True)
    bind.add_argument("--receipt", required=True)

    enrich = sub.add_parser("enrich")
    enrich.add_argument("--manifest", required=True)
    enrich.add_argument("--allocator-receipt", required=True)
    enrich.add_argument("--output", required=True)

    args = parser.parse_args()
    if args.command == "seal":
        manifest = build_manifest(args.run_dir, frontier=args.frontier, batch_id=args.batch_id)
        write_manifest(args.manifest, manifest)
        print(json.dumps({"status": manifest["status"], "blocks": manifest["block_count"]}))
        return 0 if manifest["status"] == "complete" else 2
    try:
        manifest = load_manifest(args.manifest)
        if args.command == "aggregate":
            payload = aggregate_manifest(manifest)
        elif args.command == "run-allocator":
            payload = run_registered_allocator(
                manifest,
                run_dir=args.run_dir,
                capital_ledger_path=args.capital_ledger,
                capital_snapshot_output=args.capital_snapshot_output,
                allocator_output_path=args.allocator_output,
            )
        elif args.command == "bind-allocator":
            payload = build_allocator_receipt(
                manifest,
                allocator_run_receipt_path=args.allocator_run_receipt,
            )
        else:
            allocator_receipt = load_manifest(args.allocator_receipt)
            payload = enrich_manifest(
                manifest,
                allocator_receipt,
                research_manifest_path=args.manifest,
                allocator_receipt_path=args.allocator_receipt,
            )
    except ManifestError as exc:
        print(f"QUARANTINED: {exc}")
        return 2
    if args.command == "bind-allocator":
        destination = args.receipt
    elif args.command == "run-allocator":
        destination = args.run_receipt
    else:
        destination = args.output
    atomic_json(destination, payload)
    if args.command == "aggregate":
        summary = {"unique_symbols": payload["unique_symbols"], "tally": payload["tally"]}
    elif args.command == "run-allocator":
        summary = {
            "status": payload["status"],
            "batch_id": payload["batch_id"],
            "allocator_output": payload["allocator_output"]["path"],
        }
    else:
        summary = {
            "status": payload["status"],
            "batch_id": payload["batch_id"],
            "allocations": len(payload.get("allocations") or []),
            "candidates": len(payload.get("candidates") or []),
        }
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
