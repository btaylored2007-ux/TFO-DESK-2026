#!/usr/bin/env python3
"""Venue-session verifier queue for global fundamental candidates. PAPER ONLY.

Research may finish while the selected line is closed.  This module keeps those
names pending and re-runs the same fresh entry gate used by
``fundamental_open_new.py`` only when asked.  It does not open positions.  A
``READY_FOR_DRY_RUN`` result is an execution receipt, not permission to bypass
the deterministic entry routine or any portfolio cap.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Callable

import fundamental_book
import fundamental_open_new
import fundamental_transactional_aggregate


BASE = Path(__file__).resolve().parent
DEFAULT_QUEUE = BASE / "reports" / "fundamental_session_queue.json"
EXECUTION_MANIFEST_SCHEMA = "tfo.discovery.execution-manifest.v1"
QUEUE_SCHEMA = "tfo.discovery.session-queue.v1"
HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
REQUIRED = {
    "stable_issuer_id", "symbol", "economic_direction", "execution_status",
    "execution_symbol", "execution_venue", "quote_currency", "notional_usd",
    "sector", "catalyst_date", "review_date", "event_date",
    "measurement_period_end", "thesis_review_date", "thesis",
    "execution_route", "research_symbol", "research_features",
    "research_feature_receipts",
}
RESEARCH_FEATURES = frozenset({
    "event_queue", "filing_diff", "insurer_v1", "bank_v1",
    "investment_entity_v1", "regulated_utility_v1",
    "holdco_dualclass_v1", "resources_v1",
})


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _json_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(HEX64.fullmatch(value))


def _exact_date(value: object) -> dt.date | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _receipt_records(path: Path) -> list[dict]:
    if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("feature receipt file is missing or too large")
    text = path.read_text(encoding="utf-8")
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return [value]
        if isinstance(value, list) and all(isinstance(row, dict) for row in value):
            return value
    except json.JSONDecodeError:
        pass
    rows = []
    for line in text.splitlines():
        if line.strip():
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("feature receipt JSONL row is not an object")
            rows.append(row)
    return rows


def _record_evidence_date(record: dict) -> dt.date | None:
    for field in ("recorded_utc", "known_on", "latest_known_on", "as_of_date"):
        raw = str(record.get(field) or "")[:10]
        try:
            return dt.date.fromisoformat(raw)
        except ValueError:
            continue
    return None


def _verify_raw_adapter_observation(receipt: dict, record: dict, feature: str,
                                    stable_id: str, as_of: dt.date) -> None:
    raw = receipt.get("raw_observation")
    if not isinstance(raw, dict):
        raise ValueError("adapter receipt lacks raw_observation binding")
    required = {"path", "sha256", "observation_sha256", "as_of_date"}
    if not required.issubset(raw):
        raise ValueError("adapter raw_observation fields are incomplete")
    path = Path(str(raw.get("path") or ""))
    if not path.is_absolute() or not _valid_sha256(raw.get("sha256")):
        raise ValueError("adapter raw_observation path/digest is invalid")
    if _file_sha256(path) != raw["sha256"]:
        raise ValueError("adapter raw_observation file hash mismatch")
    observation = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(observation, dict):
        raise ValueError("adapter raw_observation is not an object")
    observation_sha = _json_sha256(observation)
    if not _valid_sha256(raw.get("observation_sha256")) or observation_sha != raw["observation_sha256"]:
        raise ValueError("adapter raw_observation content digest mismatch")
    if record.get("observation_sha256") != observation_sha:
        raise ValueError("adapter result is not bound to raw_observation")
    observation_date = _exact_date(raw.get("as_of_date"))
    if observation_date is None or observation_date > as_of:
        raise ValueError("adapter raw_observation is not point-in-time")
    if observation.get("as_of_date") != raw["as_of_date"]:
        raise ValueError("adapter raw_observation date binding mismatch")
    if observation.get("adapter_id") != feature or str(
            observation.get("stable_issuer_id") or "") != stable_id:
        raise ValueError("adapter raw_observation identity mismatch")


def _verify_feature_receipts(candidate: dict, as_of: dt.date) -> None:
    features = candidate.get("research_features")
    receipts = candidate.get("research_feature_receipts")
    if not isinstance(features, list) or not all(isinstance(item, str) for item in features):
        raise ValueError("research_features must be an explicit list of feature IDs")
    if features != sorted(set(features)):
        raise ValueError("research_features must be unique and exactly sorted")
    unknown = sorted(set(features) - RESEARCH_FEATURES)
    if unknown:
        raise ValueError("unknown research_features: %s" % ", ".join(unknown))
    if not isinstance(receipts, list) or not all(isinstance(item, dict) for item in receipts):
        raise ValueError("research_feature_receipts must be an explicit list of objects")
    if [item.get("feature") for item in receipts] != features:
        raise ValueError("feature receipt IDs do not exactly match research_features")
    if not features:
        if receipts:
            raise ValueError("feature receipts supplied without research_features")
        return

    stable_id = str(candidate.get("stable_issuer_id") or "")
    research_symbol = str(candidate.get("research_symbol") or "")
    adapter_features = RESEARCH_FEATURES - {"event_queue", "filing_diff"}
    required = {
        "feature", "path", "sha256", "record_sha256", "evidence_status",
        "evidence_date", "raw_observation",
    }
    for receipt in receipts:
        feature = receipt["feature"]
        missing = sorted(required - set(receipt))
        if missing:
            raise ValueError("%s receipt fields missing: %s" % (feature, ", ".join(missing)))
        path = Path(str(receipt.get("path") or ""))
        if not path.is_absolute():
            raise ValueError("%s receipt path is not absolute" % feature)
        for field in ("sha256", "record_sha256"):
            if not _valid_sha256(receipt.get(field)):
                raise ValueError("%s receipt %s is not 64-hex" % (feature, field))
        if _file_sha256(path) != receipt["sha256"]:
            raise ValueError("%s receipt file hash mismatch" % feature)
        evidence_date = _exact_date(receipt.get("evidence_date"))
        if evidence_date is None or evidence_date > as_of:
            raise ValueError("%s receipt evidence_date is not point-in-time" % feature)

        if feature == "event_queue":
            try:
                _, report_date = fundamental_transactional_aggregate._event_queue_match(
                    path,
                    receipt["record_sha256"],
                    research_symbol,
                    stable_id,
                    str(candidate.get("event_date") or ""),
                    as_of,
                )
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError("event_queue receipt is invalid: %s" % exc) from exc
            if report_date != evidence_date:
                raise ValueError("event_queue evidence_date does not match receipt")
            if receipt["evidence_status"] != "EVIDENCE_READY" or receipt["raw_observation"] is not None:
                raise ValueError("event_queue verified fields are inconsistent")
        else:
            matches = [
                row for row in _receipt_records(path)
                if _json_sha256(row) == receipt["record_sha256"]
            ]
            if len(matches) != 1:
                raise ValueError("%s record digest is absent or not unique" % feature)
            record = matches[0]
            record_date = _record_evidence_date(record)
            if record_date != evidence_date:
                raise ValueError("%s evidence_date does not match record" % feature)
            if feature == "filing_diff":
                if receipt["evidence_status"] != "COMPLETE" or record.get("status") != "COMPLETE":
                    raise ValueError("filing_diff evidence is not COMPLETE")
                material_leads = [
                    change
                    for change in record.get("material_changes") or []
                    if isinstance(change, dict) and change.get("status") == "MATERIAL_LEAD"
                ]
                if record.get("research_queue") is not True or not material_leads:
                    raise ValueError("filing_diff has no signal-producing MATERIAL_LEAD")
                try:
                    fundamental_transactional_aggregate._validate_filing_point_in_time(
                        record, as_of
                    )
                except ValueError as exc:
                    raise ValueError("filing_diff point-in-time receipt is invalid: %s" % exc) from exc
                if receipt["raw_observation"] is not None:
                    raise ValueError("filing_diff must not claim a raw adapter observation")
                if not stable_id.upper().startswith("CIK:") or str(
                        record.get("cik") or "").lstrip("0") != stable_id.split(":", 1)[1].lstrip("0"):
                    raise ValueError("filing_diff issuer identity mismatch")
            elif feature in adapter_features:
                if receipt["evidence_status"] != "RESEARCH_READY" or record.get("status") != "RESEARCH_READY":
                    raise ValueError("adapter evidence is not RESEARCH_READY")
                if record.get("adapter_id") != feature or str(
                        record.get("stable_issuer_id") or "") != stable_id:
                    raise ValueError("adapter evidence identity mismatch")
                _verify_raw_adapter_observation(receipt, record, feature, stable_id, as_of)


def _catalyst_date(value: object) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _gate_outputs_complete(price: object, receipt: object) -> bool:
    if not isinstance(price, dict) or not isinstance(receipt, dict):
        return False
    try:
        px = float(price.get("px"))
    except (TypeError, ValueError):
        return False
    if not math.isfinite(px) or px <= 0 or receipt.get("market_state") != "REGULAR":
        return False
    measured = receipt.get("measured")
    return isinstance(measured, dict) and measured.get("spread_pct") is not None


def _verified_execution_manifest(manifest: dict, as_of: dt.date) -> None:
    if manifest.get("schema") != EXECUTION_MANIFEST_SCHEMA:
        raise ValueError("manifest is not a strict execution manifest")
    if manifest.get("status") != "complete":
        raise ValueError("execution manifest is not complete")
    if manifest.get("paper_only") is not True:
        raise ValueError("execution manifest is not PAPER-only")
    if manifest.get("as_of") != as_of.isoformat():
        raise ValueError("execution manifest is not current")
    for field in ("batch_sha256", "research_manifest_sha256", "allocator_receipt_sha256"):
        if not _valid_sha256(manifest.get(field)):
            raise ValueError("execution manifest lacks valid %s" % field)
    bindings = {}
    for field in ("research_manifest_file", "allocator_receipt_file"):
        meta = manifest.get(field)
        if not isinstance(meta, dict):
            raise ValueError("execution manifest lacks %s binding" % field)
        path = Path(str(meta.get("path") or "")).resolve()
        try:
            actual = fundamental_transactional_aggregate.file_sha256(path)
        except OSError as exc:
            raise ValueError("execution manifest source unavailable: %s" % exc) from exc
        if actual != meta.get("sha256"):
            raise ValueError("execution manifest source hash mismatch")
        bindings[field] = path
    try:
        research = json.loads(bindings["research_manifest_file"].read_text(encoding="utf-8"))
        allocator = json.loads(bindings["allocator_receipt_file"].read_text(encoding="utf-8"))
        recomputed = fundamental_transactional_aggregate.enrich_manifest(
            research,
            allocator,
            research_manifest_path=bindings["research_manifest_file"],
            allocator_receipt_path=bindings["allocator_receipt_file"],
            current_date=as_of,
        )
    except (OSError, json.JSONDecodeError, fundamental_transactional_aggregate.ManifestError) as exc:
        raise ValueError("execution manifest cannot be recomputed: %s" % exc) from exc
    for field in (
        "batch_id",
        "batch_sha256",
        "research_manifest_sha256",
        "allocator_receipt_sha256",
        "candidates",
        "candidate_held_out",
        "unmatched_allocations",
        "conflicting_symbols_held_out",
        "conflicting_issuers_held_out",
    ):
        if manifest.get(field) != recomputed.get(field):
            raise ValueError("execution manifest differs from recomputed %s" % field)


QUEUE_ONLY_FIELDS = {
    "source_candidate_sha256", "state", "queued_utc", "last_check",
}


def _validate_candidate(raw: object, as_of: dt.date) -> tuple[str, str]:
    if not isinstance(raw, dict):
        raise ValueError("execution manifest candidate is not an object")
    reserved = sorted(set(raw) & QUEUE_ONLY_FIELDS)
    if reserved:
        raise ValueError("candidate injects queue-only fields: %s" % ", ".join(reserved))
    missing = sorted(key for key in REQUIRED if key not in raw or raw.get(key) in (None, ""))
    if missing:
        raise ValueError("candidate missing required fields: %s" % ", ".join(missing))
    if raw["economic_direction"] != "LONG":
        raise ValueError("economic_direction must be LONG")
    if raw["execution_status"] not in ("pending_session", "ready"):
        raise ValueError("execution_status must be pending_session or ready")
    if str(raw["execution_symbol"]) != str(raw["symbol"]):
        raise ValueError("symbol must be the exact selected execution_symbol")
    route = str(raw.get("execution_route") or "").strip().lower()
    if route != "direct_local" and not (route.startswith("adr:") and route[4:].strip()):
        raise ValueError("execution_route is absent, invalid, or unverified")
    try:
        notional = float(raw["notional_usd"])
    except (TypeError, ValueError) as exc:
        raise ValueError("notional_usd is invalid") from exc
    if not math.isfinite(notional) or notional <= 0:
        raise ValueError("notional_usd must be positive and finite")
    dates = {
        field: _exact_date(raw.get(field))
        for field in ("catalyst_date", "review_date", "event_date",
                      "measurement_period_end", "thesis_review_date")
    }
    invalid = sorted(field for field, value in dates.items() if value is None)
    if invalid:
        raise ValueError("candidate dates must be exact ISO: %s" % ", ".join(invalid))
    if dates["catalyst_date"] <= as_of or dates["event_date"] <= as_of:
        raise ValueError("event/catalyst date is not future")
    if dates["review_date"] <= as_of or dates["thesis_review_date"] <= as_of:
        raise ValueError("review date is not future")
    if raw["catalyst_date"] != raw["event_date"]:
        raise ValueError("catalyst_date does not match canonical event_date")
    if raw["review_date"] != raw["thesis_review_date"]:
        raise ValueError("review_date does not match canonical thesis_review_date")
    _verify_feature_receipts(raw, as_of)
    return str(raw["stable_issuer_id"]), str(raw["execution_symbol"]).upper()


def _queue_binding_payload(queue: dict) -> dict:
    return {
        "schema": queue.get("schema"),
        "paper_only": queue.get("paper_only"),
        "as_of": queue.get("as_of"),
        "batch_id": queue.get("batch_id"),
        "batch_sha256": queue.get("batch_sha256"),
        "research_manifest_sha256": queue.get("research_manifest_sha256"),
        "allocator_receipt_sha256": queue.get("allocator_receipt_sha256"),
        "source_execution_manifest_sha256": queue.get("source_execution_manifest_sha256"),
        "candidate_source_sha256s": [
            row.get("source_candidate_sha256")
            for row in queue.get("candidates") or []
            if isinstance(row, dict)
        ],
    }


def _validate_queue_receipt(queue: dict, as_of: dt.date) -> None:
    if not isinstance(queue, dict) or queue.get("schema") != QUEUE_SCHEMA:
        raise ValueError("queue is not the strict session-queue schema")
    if queue.get("status") != "complete" or queue.get("paper_only") is not True:
        raise ValueError("queue is not a complete PAPER-only receipt")
    if queue.get("as_of") != as_of.isoformat():
        raise ValueError("queue is not current")
    for field in ("batch_sha256", "research_manifest_sha256",
                  "allocator_receipt_sha256", "source_execution_manifest_sha256",
                  "queue_receipt_sha256"):
        if not _valid_sha256(queue.get(field)):
            raise ValueError("queue lacks valid %s" % field)
    manifest = queue.get("execution_manifest")
    if not isinstance(manifest, dict) or _json_sha256(manifest) != queue["source_execution_manifest_sha256"]:
        raise ValueError("queue execution manifest binding is missing or modified")
    for field in ("batch_id", "batch_sha256", "research_manifest_sha256",
                  "allocator_receipt_sha256"):
        if queue.get(field) != manifest.get(field):
            raise ValueError("queue does not preserve execution manifest %s" % field)
    candidates = queue.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("queue candidates is not a list")
    identities = set()
    for row in candidates:
        if not isinstance(row, dict) or not _valid_sha256(row.get("source_candidate_sha256")):
            raise ValueError("queued candidate lacks source provenance")
        source = {key: value for key, value in row.items() if key not in QUEUE_ONLY_FIELDS}
        if _json_sha256(source) != row["source_candidate_sha256"]:
            raise ValueError("queued candidate changed after manifest admission")
        identity = _validate_candidate(source, as_of)
        if identity in identities:
            raise ValueError("duplicate queued stable-issuer/execution-symbol identity")
        identities.add(identity)
    if queue["queue_receipt_sha256"] != _json_sha256(_queue_binding_payload(queue)):
        raise ValueError("queue receipt does not bind the exact manifest/candidate set")


def validate_queue_for_open(queue: dict, as_of: dt.date | None = None) -> bool:
    """Public final-gate validator used by the opener before exact-row matching."""
    today = dt.date.today()
    if as_of is not None and as_of != today:
        raise ValueError("historical queue validation is forbidden")
    _validate_queue_receipt(queue, today)
    _verified_execution_manifest(queue.get("execution_manifest") or {}, today)
    return True


def build_queue(manifest: dict, as_of: dt.date | None = None) -> dict:
    today = dt.date.today()
    if as_of is not None and as_of != today:
        raise ValueError("historical queue as_of is forbidden")
    as_of = today
    if not isinstance(manifest, dict):
        raise ValueError("execution manifest is not an object")
    _verified_execution_manifest(manifest, as_of)
    if not manifest.get("batch_id"):
        raise ValueError("execution manifest lacks batch_id")
    if not isinstance(manifest.get("candidates"), list):
        raise ValueError("execution manifest candidates is not a list")
    manifest_hash = _json_sha256(manifest)
    candidates = []
    identities = set()
    for raw in manifest["candidates"]:
        identity = _validate_candidate(raw, as_of)
        if identity in identities:
            raise ValueError("duplicate execution-manifest stable-issuer/execution-symbol identity")
        identities.add(identity)
        candidates.append({
            **raw,
            "source_candidate_sha256": _json_sha256(raw),
            "state": "PENDING_SESSION", "queued_utc": utc_now(),
            "last_check": None,
        })
    queue = {
        "schema": QUEUE_SCHEMA,
        "status": "complete",
        "paper_only": True,
        "as_of": as_of.isoformat(),
        "batch_id": manifest["batch_id"],
        "batch_sha256": manifest["batch_sha256"],
        "research_manifest_sha256": manifest["research_manifest_sha256"],
        "allocator_receipt_sha256": manifest["allocator_receipt_sha256"],
        "source_execution_manifest_sha256": manifest_hash,
        "generated_utc": utc_now(),
        "execution_manifest": manifest,
        "candidates": candidates,
        "rule": "Verifier only. READY_FOR_DRY_RUN must still pass the existing dry-run and portfolio gates before any PAPER open.",
    }
    queue["queue_receipt_sha256"] = _json_sha256(_queue_binding_payload(queue))
    return queue


def verify_queue(queue: dict, gate: Callable | None = None,
                 as_of: dt.date | None = None) -> dict:
    gate = gate or fundamental_open_new.live_entry_gate
    today = dt.date.today()
    if as_of is not None and as_of != today:
        raise ValueError("historical queue verification as_of is forbidden")
    as_of = today
    validate_queue_for_open(queue, as_of)
    out = dict(queue)
    rows = []
    for candidate in queue.get("candidates") or []:
        row = dict(candidate)
        side = "long" if row["economic_direction"] == "LONG" else "short"
        catalyst_date = _catalyst_date(row.get("catalyst_date"))
        if catalyst_date is None or catalyst_date <= as_of:
            price, receipt, refusal = None, None, "catalyst_date is missing, invalid, or not future"
        else:
            try:
                price, receipt, refusal = gate(
                    row["execution_symbol"], side, float(row["notional_usd"]), fundamental_book)
            except Exception as exc:  # noqa: BLE001
                price, receipt, refusal = None, None, "%s: %s" % (type(exc).__name__, str(exc)[:160])
            if not refusal and not _gate_outputs_complete(price, receipt):
                price, receipt, refusal = None, None, "fresh gate returned incomplete outputs"
        if refusal:
            row["state"] = "PENDING_SESSION" if "REGULAR session" in refusal else "REFUSED_FRESH_GATE"
            row["last_check"] = {"utc": utc_now(), "reason": refusal}
        else:
            row["state"] = "READY_FOR_DRY_RUN"
            row["last_check"] = {"utc": utc_now(), "price": price, "liquidity_receipt": receipt}
        rows.append(row)
    out["candidates"] = rows
    out["verified_utc"] = utc_now()
    out["counts"] = {state: sum(1 for r in rows if r["state"] == state)
                     for state in ("READY_FOR_DRY_RUN", "PENDING_SESSION", "REFUSED_FRESH_GATE")}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build")
    p.add_argument("--manifest", required=True)
    p.add_argument("--output", default=str(DEFAULT_QUEUE))
    p = sub.add_parser("verify")
    p.add_argument("--queue", default=str(DEFAULT_QUEUE))
    p.add_argument("--output", default=str(DEFAULT_QUEUE))
    args = ap.parse_args()
    try:
        if args.command == "build":
            value = build_queue(json.loads(Path(args.manifest).read_text(encoding="utf-8")))
        else:
            value = verify_queue(json.loads(Path(args.queue).read_text(encoding="utf-8")))
        atomic_write(Path(args.output), value)
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("REFUSED: %s" % exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
