#!/usr/bin/env python3
"""Forward PAPER performance attribution for research-origin features.

Reads only the fundamental PAPER ledger.  Every feature receives transparent
co-attribution when several features contributed to one entry; feature rows are
therefore not additive.  Entries without the immutable provenance receipt stay
explicitly unattributed rather than being inferred from thesis prose.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any

from fundamental_discovery_common import atomic_json
from fundamental_open_new import RESEARCH_FEATURES


BASE = Path(__file__).resolve().parent
LEDGER = BASE / "reports" / "fundamental_book.json"
OUTPUT = BASE / "reports" / "fundamental_feature_attribution.json"


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _number(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise ValueError("performance value is not numeric")
    if not math.isfinite(out):
        raise ValueError("performance value is not finite")
    return out


def _hex64(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-fA-F]{64}", value))


def _features(position: dict[str, Any]) -> tuple[list[str], bool]:
    receipt = position.get("research_provenance")
    if not isinstance(receipt, dict):
        return [], False
    values = receipt.get("features")
    hash_fields = (
        "manifest_sha256", "queue_receipt_sha256", "batch_sha256",
        "research_manifest_sha256", "allocator_receipt_sha256",
    )
    issuer = receipt.get("stable_issuer_id")
    queue_path = receipt.get("queue_receipt_path")
    if (
        not isinstance(values, list)
        or not issuer
        or not isinstance(queue_path, str)
        or not os.path.isabs(queue_path)
        or not all(_hex64(receipt.get(field)) for field in hash_fields)
    ):
        return [], False
    if not all(isinstance(value, str) for value in values):
        return [], False
    clean = sorted(set(value for value in values if value in RESEARCH_FEATURES))
    # A fully bound general-research entry explicitly claims no feature. Keep
    # it unattributed without misreporting its valid receipt as corrupt.
    return clean, len(clean) == len(values)


def _realised(position: dict[str, Any]) -> float:
    fills = position.get("exit_fills")
    total = 0.0
    if isinstance(fills, list):
        total = sum(_number(row.get("realised_usd")) for row in fills
                    if isinstance(row, dict))
    close_record = position.get("close_record")
    if isinstance(close_record, dict):
        total += _number(close_record.get("realised_usd"))
    return total


def build(ledger_path: str | Path = LEDGER, *, as_of_utc: str | None = None) -> dict[str, Any]:
    ledger = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
    rows = {
        feature: {
            "feature": feature,
            "entries": 0,
            "open_positions": 0,
            "closed_positions": 0,
            "entry_notional_usd": 0.0,
            "open_notional_usd": 0.0,
            "realised_usd": 0.0,
            "unrealised_usd": 0.0,
            "total_pnl_usd": 0.0,
            "total_return_pct": None,
            "winning_positions": 0,
            "losing_positions": 0,
            "flat_positions": 0,
            "symbols": [],
        }
        for feature in sorted(RESEARCH_FEATURES)
    }
    unattributed: list[str] = []
    invalid_receipts: list[str] = []
    invalid_performance: list[dict[str, str]] = []

    for state, positions in (("open", ledger.get("positions", {})),
                             ("closed", ledger.get("closed", {}))):
        if not isinstance(positions, dict):
            raise ValueError(f"ledger {state} positions are not an object")
        for symbol, position in positions.items():
            if not isinstance(position, dict):
                invalid_receipts.append(symbol)
                continue
            features, valid = _features(position)
            if isinstance(position.get("research_provenance"), dict) and not valid:
                invalid_receipts.append(symbol)
                continue
            if not features:
                unattributed.append(symbol)
                continue
            try:
                realised = _realised(position)
                unrealised = _number(position.get("unrealised_usd")) if state == "open" else 0.0
                raw_orig_units = (position.get("exit_state") or {}).get("orig_units")
                orig_units = _number(raw_orig_units) if raw_orig_units is not None else 0.0
                entry_notional = (
                    orig_units * _number(position.get("entry_px_usd"))
                    if orig_units > 0 else _number(position.get("notional_usd"))
                )
            except (AttributeError, TypeError, ValueError) as exc:
                invalid_performance.append({"symbol": symbol, "reason": str(exc)})
                continue
            total_pnl = realised + unrealised
            for feature in features:
                row = rows[feature]
                row["entries"] += 1
                row[f"{state}_positions"] += 1
                row["entry_notional_usd"] += entry_notional
                if state == "open":
                    row["open_notional_usd"] += _number(position.get("notional_usd"))
                row["realised_usd"] += realised
                row["unrealised_usd"] += unrealised
                if total_pnl > 0.005:
                    row["winning_positions"] += 1
                elif total_pnl < -0.005:
                    row["losing_positions"] += 1
                else:
                    row["flat_positions"] += 1
                row["symbols"].append(symbol)

    for row in rows.values():
        for field in ("entry_notional_usd", "open_notional_usd",
                      "realised_usd", "unrealised_usd"):
            row[field] = round(row[field], 2)
        row["total_pnl_usd"] = round(row["realised_usd"] + row["unrealised_usd"], 2)
        if row["entry_notional_usd"] > 0:
            row["total_return_pct"] = round(
                100.0 * row["total_pnl_usd"] / row["entry_notional_usd"], 4)
        row["symbols"] = sorted(set(row["symbols"]))

    return {
        "paper_only": True,
        "as_of_utc": as_of_utc or _utc(),
        "attribution": "full co-attribution; feature rows are not additive",
        "features": [rows[key] for key in sorted(rows)],
        "unattributed_positions": sorted(set(unattributed)),
        "invalid_provenance_receipts": sorted(set(invalid_receipts)),
        "invalid_performance_positions": sorted(
            invalid_performance, key=lambda row: (row["symbol"], row["reason"])),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", default=str(LEDGER))
    parser.add_argument("--output", default=str(OUTPUT))
    args = parser.parse_args()
    try:
        report = build(args.ledger)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("REFUSED:", exc)
        return 2
    atomic_json(args.output, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
