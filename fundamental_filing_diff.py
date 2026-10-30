#!/usr/bin/env python3
"""Point-in-time SEC filing snapshots and material-diff leads. PAPER ONLY.

This is a successor research data layer, not a strategy and not an entry path.
Every fact is filtered by the date it was filed, preserving what was actually
public at the requested cutoff.  A diff surfaces a name for specialist review;
it never calls a company undervalued and never infers a catalyst from prose.

Non-US research may use the same normalized snapshot schema with exchange or
issuer primary sources.  The built-in collector is SEC-only because EDGAR is the
desk's current point-in-time primary feed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

import fundamental_feed as feed


BASE = Path(__file__).resolve().parent
DEFAULT_DIR = BASE / "reports" / "filing_diff"
ALLOWED_FORMS = {"10-K", "10-Q", "20-F", "40-F", "6-K", "8-K"}

# Tag precedence is explicit; a later fallback fills a gap but never overwrites
# the lead tag.  ``kind`` prevents comparing an instant with a duration fact.
CONCEPTS = {
    "revenue": {"kind": "duration", "unit": "USD", "tags": (
        "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues")},
    "net_income": {"kind": "duration", "unit": "USD", "tags": (
        "NetIncomeLoss", "ProfitLoss")},
    "operating_cashflow": {"kind": "duration", "unit": "USD", "tags": (
        "NetCashProvidedByUsedInOperatingActivities",)},
    "equity": {"kind": "instant", "unit": "USD", "tags": (
        "StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest")},
    "cash": {"kind": "instant", "unit": "USD", "tags": (
        "CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents")},
    "debt": {"kind": "instant", "unit": "USD", "tags": (
        "LongTermDebtAndFinanceLeaseObligationsCurrent", "LongTermDebtCurrent",
        "ShortTermBorrowings", "ShortTermDebtCurrent")},
    "shares": {"kind": "instant", "unit": "shares", "tags": (
        "CommonStockSharesOutstanding",)},
}

MATERIAL_THRESHOLDS = {
    "revenue": 0.10, "net_income": 0.15, "operating_cashflow": 0.20,
    "equity": 0.10, "cash": 0.20, "debt": 0.10, "shares": 0.05,
}

EVENT_DIRECTIONS = {
    ("guidance_status", "weakened"): "bearish",
    ("guidance_status", "strengthened"): "bullish",
    ("buyback_status", "started"): "bullish",
    ("buyback_status", "suspended"): "bearish",
    ("contract_status", "won"): "bullish",
    ("contract_status", "lost"): "bearish",
    ("debt_refinancing_status", "completed"): "bullish",
    ("debt_refinancing_status", "maturity_warning"): "bearish",
    ("going_concern", "added"): "bearish",
    ("going_concern", "removed"): "bullish",
    ("capital_raise_status", "announced"): "bearish",
    ("capital_raise_status", "withdrawn"): "bullish",
}


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _facts_for_tag(companyfacts: dict, tag: str, unit: str) -> list[dict]:
    fact = (((companyfacts.get("facts") or {}).get("us-gaap") or {}).get(tag) or {})
    return list((fact.get("units") or {}).get(unit) or [])


def select_fact(companyfacts: dict, concept: str, known_on: str) -> dict | None:
    """Return the latest period that was public by ``known_on`` without look-ahead."""
    spec = CONCEPTS[concept]
    cutoff = _date(known_on)
    if cutoff is None:
        raise ValueError("known_on must be ISO")
    candidates = []
    for tag_rank, tag in enumerate(spec["tags"]):
        for row in _facts_for_tag(companyfacts, tag, spec["unit"]):
            filed = _date(row.get("filed"))
            end = _date(row.get("end"))
            if filed is None or end is None or filed > cutoff:
                continue
            if row.get("form") not in ALLOWED_FORMS or not _finite(row.get("val")):
                continue
            candidates.append((tag_rank, tag, row))
    if not candidates:
        return None
    # Period/publication recency is authoritative across all canonical tags.
    # Tag precedence breaks a tie only; it may never make a stale lead tag beat
    # a newer valid fallback tag.
    latest_end = max(str(row["end"]) for _, _, row in candidates)
    same_period = [item for item in candidates if str(item[2]["end"]) == latest_end]
    latest_filed = max(str(item[2].get("filed")) for item in same_period)
    same_filing = [item for item in same_period if str(item[2].get("filed")) == latest_filed]
    tag_rank, tag, row = min(
        same_filing, key=lambda item: (item[0], str(item[2].get("accn") or ""))
    )
    return {
        "concept": concept, "tag": tag, "kind": spec["kind"], "unit": spec["unit"],
        "value": float(row["val"]), "start": row.get("start"), "end": row.get("end"),
        "filed": row.get("filed"), "form": row.get("form"), "fy": row.get("fy"),
        "fp": row.get("fp"), "frame": row.get("frame"), "accession": row.get("accn"),
    }


def build_snapshot(companyfacts: dict, cik: str, known_on: str, events: dict | None = None) -> dict:
    cutoff = _date(known_on)
    if cutoff is None:
        raise ValueError("known_on must be ISO")
    metrics = {name: select_fact(companyfacts, name, known_on) for name in CONCEPTS}
    metrics = {k: v for k, v in metrics.items() if v is not None}
    cik10 = str(cik).lstrip("CIK").zfill(10)
    snapshot = {
        "schema": 1, "paper_only": True, "source": "SEC EDGAR companyfacts",
        "primary_source_url": "https://data.sec.gov/api/xbrl/companyfacts/CIK%s.json" % cik10,
        "cik": cik10, "entity": companyfacts.get("entityName"), "known_on": cutoff.isoformat(),
        "metrics": metrics, "events": events or {},
        "warning": "Point-in-time numeric snapshot and event lead only; not a valuation or trade verdict.",
    }
    snapshot["snapshot_sha256"] = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return snapshot


def _period_days(fact: dict) -> int | None:
    start, end = _date(fact.get("start")), _date(fact.get("end"))
    return (end - start).days if start and end else None


def comparable(prior: dict, latest: dict) -> bool:
    if prior.get("kind") != latest.get("kind") or prior.get("unit") != latest.get("unit"):
        return False
    if prior.get("kind") == "instant":
        return True
    p_days, l_days = _period_days(prior), _period_days(latest)
    return p_days is not None and l_days is not None and abs(p_days - l_days) <= 10


def _validate_events(snapshot: dict) -> list[str]:
    errors = []
    known_on = _date(snapshot.get("known_on"))
    for name, event in (snapshot.get("events") or {}).items():
        if not isinstance(event, dict):
            errors.append("event %s is not an object" % name)
            continue
        filed = _date(event.get("filed_date"))
        if filed is None or known_on is None or filed > known_on:
            errors.append("event %s is not point-in-time at known_on" % name)
        if not str(event.get("source_url") or "").startswith(("http://", "https://")):
            errors.append("event %s lacks a primary-source URL" % name)
    return errors


def _validate_metrics(snapshot: dict, label: str) -> list[str]:
    """Prove every supplied metric was public at that snapshot's cutoff."""
    errors = []
    known_on = _date(snapshot.get("known_on"))
    metrics = snapshot.get("metrics")
    if known_on is None:
        return ["%s snapshot has invalid known_on" % label]
    if not isinstance(metrics, dict):
        return ["%s metrics is not an object" % label]
    for name, metric in metrics.items():
        spec = CONCEPTS.get(name)
        if spec is None:
            errors.append("%s metric %s is not a registered normalized concept" % (label, name))
            continue
        if not isinstance(metric, dict):
            errors.append("%s metric %s is not an object" % (label, name))
            continue
        if metric.get("concept") != name:
            errors.append("%s metric %s concept binding is invalid" % (label, name))
        if metric.get("kind") != spec["kind"] or metric.get("unit") != spec["unit"]:
            errors.append("%s metric %s kind/unit schema is invalid" % (label, name))
        if metric.get("tag") not in spec["tags"]:
            errors.append("%s metric %s tag is not canonical" % (label, name))
        filed = _date(metric.get("filed"))
        period_end = _date(metric.get("end"))
        if filed is None or filed > known_on:
            errors.append("%s metric %s is not point-in-time at known_on" % (label, name))
        if period_end is None or period_end > known_on:
            errors.append("%s metric %s has an invalid/future period end" % (label, name))
        if not _finite(metric.get("value")):
            errors.append("%s metric %s has no finite value" % (label, name))
    return errors


def _snapshot_receipt(snapshot: dict, label: str) -> tuple[dict, list[str]]:
    errors = []
    expected = snapshot.get("snapshot_sha256")
    body = {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    actual = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if snapshot.get("schema") != 1 or snapshot.get("paper_only") is not True:
        errors.append("%s snapshot schema/PAPER binding is invalid" % label)
    source_url = str(snapshot.get("primary_source_url") or "")
    if not source_url.startswith(("http://", "https://")):
        errors.append("%s snapshot lacks a primary source URL" % label)
    if not isinstance(expected, str) or len(expected) != 64 or expected != actual:
        errors.append("%s snapshot content hash mismatch" % label)
    return {
        "snapshot_sha256": expected,
        "primary_source_url": source_url,
        "known_on": snapshot.get("known_on"),
        "cik": snapshot.get("cik"),
    }, errors


def compare_snapshots(prior: dict, latest: dict) -> dict:
    if prior.get("cik") != latest.get("cik"):
        raise ValueError("snapshots identify different issuers")
    pdate, ldate = _date(prior.get("known_on")), _date(latest.get("known_on"))
    if pdate is None or ldate is None or ldate <= pdate:
        raise ValueError("latest known_on must be after prior known_on")
    prior_receipt, prior_integrity = _snapshot_receipt(prior, "prior")
    latest_receipt, latest_integrity = _snapshot_receipt(latest, "latest")
    validation_errors = (
        prior_integrity
        + latest_integrity
        + _validate_events(prior)
        + _validate_events(latest)
        + _validate_metrics(prior, "prior")
        + _validate_metrics(latest, "latest")
    )
    if validation_errors:
        return {
            "schema": 1,
            "paper_only": True,
            "cik": latest.get("cik"),
            "prior_known_on": prior.get("known_on"),
            "latest_known_on": latest.get("known_on"),
            "status": "COVERAGE_FAILURE",
            "validation_errors": validation_errors,
            "source_receipts": {"prior": prior_receipt, "latest": latest_receipt},
            "material_changes": [],
            "research_queue": False,
            "capital_eligible": False,
            "warning": "Point-in-time validation failed; no filing-diff lead may be emitted.",
        }
    changes = []
    all_metrics = sorted(set((prior.get("metrics") or {})) | set((latest.get("metrics") or {})))
    for name in all_metrics:
        p = (prior.get("metrics") or {}).get(name)
        l = (latest.get("metrics") or {}).get(name)
        if not p or not l or p.get("accession") == l.get("accession"):
            continue
        if not comparable(p, l):
            changes.append({"kind": "numeric", "concept": name, "status": "NOT_COMPARABLE",
                            "prior_period": [p.get("start"), p.get("end")],
                            "latest_period": [l.get("start"), l.get("end")],
                            "reason": "period lengths or fact kinds differ; no directional inference"})
            continue
        pv, lv = float(p["value"]), float(l["value"])
        denominator = max(abs(pv), 1.0)
        rel = (lv - pv) / denominator
        if abs(rel) >= MATERIAL_THRESHOLDS[name]:
            changes.append({"kind": "numeric", "concept": name, "status": "MATERIAL_LEAD",
                            "relative_change": round(rel, 6), "prior": p, "latest": l,
                            "direction": "unclassified",
                            "reason": "numeric change needs sector-specific interpretation"})

    pevents, levents = prior.get("events") or {}, latest.get("events") or {}
    for name in sorted(set(pevents) | set(levents)):
        p, l = pevents.get(name) or {}, levents.get(name) or {}
        if p.get("status") == l.get("status"):
            continue
        direction = EVENT_DIRECTIONS.get((name, str(l.get("status") or "")), "unclassified")
        changes.append({"kind": "event", "event": name, "status": "MATERIAL_LEAD",
                        "prior_status": p.get("status"), "latest_status": l.get("status"),
                        "direction": direction, "source_url": l.get("source_url"),
                        "filed_date": l.get("filed_date"),
                        "reason": "exact normalised status change; read the primary disclosure before any verdict"})

    return {
        "schema": 1, "paper_only": True, "cik": latest.get("cik"),
        "prior_known_on": prior.get("known_on"), "latest_known_on": latest.get("known_on"),
        "status": "COVERAGE_FAILURE" if validation_errors else "COMPLETE",
        "validation_errors": validation_errors, "material_changes": changes,
        "source_receipts": {"prior": prior_receipt, "latest": latest_receipt},
        "research_queue": bool(changes) and not validation_errors,
        "capital_eligible": False,
        "warning": "A filing diff is a research lead. Value, direction, catalyst life and live execution gates still apply.",
    }


def atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def capture(cik: str, known_on: str, output: Path) -> dict:
    result = feed.edgar_company_facts(cik)
    if not result.ok:
        raise ValueError(result.reason)
    snapshot = build_snapshot(result.data, cik, known_on)
    atomic_write(output, snapshot)
    return snapshot


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("capture-sec")
    p.add_argument("--cik", required=True)
    p.add_argument("--known-on", required=True)
    p.add_argument("--output", required=True)
    p = sub.add_parser("diff")
    p.add_argument("--prior", required=True)
    p.add_argument("--latest", required=True)
    p.add_argument("--output", required=True)
    args = ap.parse_args()
    try:
        if args.command == "capture-sec":
            out = capture(args.cik, args.known_on, Path(args.output))
        else:
            prior = json.loads(Path(args.prior).read_text(encoding="utf-8"))
            latest = json.loads(Path(args.latest).read_text(encoding="utf-8"))
            out = compare_snapshots(prior, latest)
            atomic_write(Path(args.output), out)
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0 if out.get("status") != "COVERAGE_FAILURE" else 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("REFUSED: %s" % exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
