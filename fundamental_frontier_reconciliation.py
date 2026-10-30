"""Read-only, deterministic routing records over the historical hunt ledger.

Completion below means a reconciled screening *batch*, not an assertion that
every company was researched or that any company is eligible for a trade.
Canonical research partitions always come from the coverage builder unchanged.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from fundamental_discovery_common import file_sha256


STATES = {"ready", "in_progress", "complete", "blocked_data", "retry_after", "stale_revisit"}

# These source associations repair rows written before the ledger recorded paths.
# They are exact legacy labels; new labels need an explicit source, not fuzzy matching.
LEGACY_SOURCES = {
    "US large cap (SPX tier)": [f"global_run/blocks_us_{part}.md" for part in
                                ("consumer", "healthcare", "industrials_tech", "energy", "materials_util_reit", "financials")],
    "Global resource producers": ["global_run/blocks_resources.md"],
    "Crypto-adjacent equities": ["global_run/blocks_crypto_adjacent.md"],
    "Japan large cap (TSE)": ["global_run/blocks_japan.md"],
    "UK large cap (LSE)": ["global_run/blocks_uk.md"],
    "Europe DACH/Benelux large cap": ["global_run/blocks_europe_dach.md"],
    "Europe France/Italy/Iberia/Nordics large cap": ["global_run/blocks_europe_south_nordics.md"],
    "Hong Kong / China (HKEX)": ["global_run/blocks_hongkong.md"],
    "Australia (ASX)": ["global_run/blocks_australia.md"],
    "Canada (TSX)": ["global_run/blocks_canada.md"],
    "Korea + Taiwan large cap": ["global_run/blocks_korea_taiwan.md"],
    "India (NSE)": ["global_run/blocks_india.md"],
    "Brazil (B3)": ["global_run/blocks_brazil.md"],
    "Southeast Asia (SGX/IDX/SET/KLSE/PSE/HOSE)": ["hunt2/blocks_sea.md"],
    "Middle East + Africa (Tadawul/QSE/DFM/JSE/EGX)": ["hunt2/blocks_mea.md"],
    "LatAm ex-Brazil + CEE": ["hunt2/blocks_latam_cee.md"],
    "US small/micro cap": ["hunt3/blocks_us_smallcap.md"],
    "Japan small/mid cap": ["hunt3/blocks_japan_smallcap.md"],
    "Holdcos + closed-end funds": ["hunt3/blocks_holdco_cef.md"],
    "Global net-nets (NCAV sweep)": ["hunt3/blocks_netnet.md"],
    "Europe small/mid cap": ["hunt3/blocks_europe_smallcap.md"],
    "UK small cap + AIM": ["hunt3/blocks_uk_smallcap.md"],
    "Euronext (recovery after gate fix)": ["hunt3/blocks_euronext_recovery.md"],
}

# Exact directory-to-scope associations from the Sep 3 reconciliation. An
# unfinished revisit shadows previous completion of that same/containing scope.
ATTEMPT_ALIASES = {
    "us_largecap_revisit": "US large cap (SPX tier)",
    "canada_senior": "Canada (TSX)",
    "shipping": "Global shipping and tankers as a sector",
    "latam": "LatAm ex-Brazil + CEE",
    "sea_deep": "Southeast Asia (SGX/IDX/SET/KLSE/PSE/HOSE)",
    "africa_jse_egx": "Middle East + Africa (Tadawul/QSE/DFM/JSE/EGX)",
    "turkey_bist": "Turkey (BIST)",
    "vietnam_philippines": "Vietnam / Philippines",
    "us_midcap": "US MID CAP (NYSE/Nasdaq)",
    "china_smid": "China A-shares (Shanghai .SS / Shenzhen .SZ) small/mid cap",
    "japan_smid_dated": "Japan small/mid cap",
}


def frontier_id(name: str) -> str:
    return "frontier-" + hashlib.sha256(name.strip().casefold().encode()).hexdigest()[:16]


def data_scope(name: str) -> str:
    normalized = name.casefold()
    if "turkey" in normalized or "türkiye" in normalized or "turkiye" in normalized:
        return "turkey"
    if "vietnam" in normalized or "philippin" in normalized:
        return "vietnam_philippines"
    return normalized


def timestamp(value: Any) -> dt.datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO date or timezone-aware datetime")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return dt.datetime.combine(dt.date.fromisoformat(value), dt.time(), dt.timezone.utc)
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("datetime must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def iso(value: dt.datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def load_object(path: Path, *, optional: bool = False) -> tuple[dict, list[str]]:
    if optional and not path.exists():
        return {}, []
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise ValueError("top-level value must be an object")
        return result, []
    except (OSError, ValueError, TypeError) as exc:
        return {}, [f"{path}: {type(exc).__name__}: {exc}"]


def receipt(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": file_sha256(path)}


def source_paths(row: dict, root: Path, records: dict[str, dict]) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    sources = row.get("files")
    if sources is not None and (not isinstance(sources, list) or not all(isinstance(p, str) for p in sources)):
        return [], ["files must be a list of exact source paths"]
    if not sources and isinstance(row.get("manifest"), str):
        manifest_path = Path(row["manifest"])
        if not manifest_path.is_absolute():
            manifest_path = root / manifest_path
        manifest, manifest_errors = load_object(manifest_path)
        issues.extend(manifest_errors)
        manifest_files = manifest.get("files", [])
        if isinstance(manifest_files, list):
            sources = [p["path"] for p in manifest_files if isinstance(p, dict) and isinstance(p.get("path"), str)]
        else:
            issues.append("manifest files is not a list")
    if not sources:
        sources = LEGACY_SOURCES.get(row.get("frontier", ""), [])
    if not sources:
        # Older rows embed exact filenames in a source or note. Bare subsequent
        # filenames inherit the explicit directory from the first filename.
        text = str(row.get("source") or row.get("note") or "")
        sources = re.findall(r"(?:/?[A-Za-z0-9_.-]+/)*blocks_[A-Za-z0-9_.-]+\.md", text)
    result: list[str] = []
    previous_parent: Path | None = None
    for item in sources:
        path = Path(item)
        if path.parent == Path("."):
            if previous_parent:
                path = previous_parent / path
            else:
                matches = [Path(p) for p in records if Path(p).name == path.name]
                if len(matches) != 1:
                    issues.append(f"ambiguous or missing bare source filename: {item}")
                    continue
                path = matches[0]
        elif not path.is_absolute():
            path = root / path
        path = path.resolve()
        previous_parent = path.parent
        if str(path) not in records:
            issues.append(f"source not represented in canonical coverage: {path}")
        elif str(path) not in result:
            result.append(str(path))
    if not result:
        issues.append("no exact represented source files resolved")
    return result, issues


def screening_evidence(row: dict, root: Path, records: dict[str, dict]) -> tuple[dict, list[str]]:
    paths, issues = source_paths(row, root, records)
    evidence = {"source_files": [], "partition": {}, "represented_symbol_blocks": 0,
                "canonical_universe_nonunique": 0, "completion_scope": "legacy_screening_batch"}
    for path in paths:
        record = records[path]
        evidence["source_files"].append({"path": path, "sha256": record["sha256"]})
        evidence["represented_symbol_blocks"] += record["represented_symbol_blocks"]
        evidence["canonical_universe_nonunique"] += record["canonical_universe"]
        for state, count in record["partition"].items():
            evidence["partition"][state] = evidence["partition"].get(state, 0) + count
        if record["parse_issues"]:
            issues.append(f"parse issues in {path}")
        if record["partition"].get("incomplete"):
            issues.append(f"incomplete research blocks in {path}")
    expected = row.get("blocks_written", row.get("screened"))
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
        issues.append("screened/blocks_written must be a nonnegative integer")
    elif "blocks_written" in row:
        if expected != evidence["represented_symbol_blocks"]:
            issues.append(f"blocks_written mismatch: legacy={expected}, physical={evidence['represented_symbol_blocks']}")
    elif expected != evidence["canonical_universe_nonunique"]:
        issues.append(f"screened denominator mismatch: legacy={expected}, canonical={evidence['canonical_universe_nonunique']}")
    if not evidence["represented_symbol_blocks"]:
        issues.append("zero represented research blocks; file existence is not completed coverage")
    if row.get("manifest"):
        if not isinstance(row["manifest"], str):
            issues.append("manifest path must be a string")
            return evidence, issues
        manifest_path = Path(row["manifest"])
        if not manifest_path.is_absolute():
            manifest_path = root / manifest_path
        manifest, errors = load_object(manifest_path)
        issues.extend(errors)
        if not errors:
            evidence["manifest"] = receipt(manifest_path)
            if manifest.get("status") != "complete" or manifest.get("quarantine_reasons"):
                issues.append("manifest is incomplete or quarantined")
            manifest_files = manifest.get("files")
            if not isinstance(manifest_files, list):
                issues.append("manifest files is not a list")
            else:
                actual = {p: records[p]["sha256"] for p in paths}
                declared = {str(Path(p["path"]).resolve()): p.get("sha256")
                            for p in manifest_files if isinstance(p, dict) and isinstance(p.get("path"), str)}
                if len(declared) != len(manifest_files):
                    issues.append("manifest contains malformed or duplicate file records")
                if actual != declared:
                    issues.append("manifest source paths or hashes do not match canonical files")
                if manifest.get("block_count") != evidence["represented_symbol_blocks"]:
                    issues.append("manifest block count does not match physical blocks")
            evidence["completion_scope"] = "hash_verified_screening_batch"
    evidence["research_depth_caveat"] = (
        "Completion is a screening batch only. Unknown and not_researched counts remain unresolved; "
        "routing permission requires new research and cannot authorise a trade."
    )
    return evidence, issues


def build_frontiers(root: Path, file_records: list[dict], legacy_path: Path,
                    *, now: dt.datetime | None = None, lifecycle_path: Path | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    records = {r["path"]: r for r in file_records}
    legacy, errors = load_object(legacy_path)
    lifecycle_path = lifecycle_path or root / "hunt_coverage/frontier_lifecycle.json"
    lifecycle, lifecycle_errors = load_object(lifecycle_path, optional=True)
    errors.extend(lifecycle_errors)
    frontiers: list[dict] = []

    def new_row(name: str, original: dict, origin: str) -> dict:
        row = {"frontier_id": frontier_id(name), "frontier": name, "origin": origin,
               "state": "in_progress", "reconciled": False, "eligible": False,
               "last_completed_utc": None, "last_completed_date": None,
               "retry_after_utc": None, "evidence": [receipt(legacy_path)] if legacy_path.is_file() else [], "excludes": [],
               "legacy_record": original, "reconciliation": {"status": "unreconciled", "reasons": []}}
        frontiers.append(row)
        return row

    for collection in ("covered", "never_covered", "blocked_data"):
        items = legacy.get(collection, [])
        if not isinstance(items, list):
            errors.append(f"legacy {collection} must be a list")
            continue
        for index, item in enumerate(items):
            if isinstance(item, str) and collection == "never_covered":
                item = {"frontier": item}
            if not isinstance(item, dict) or not isinstance(item.get("frontier"), str) or not item["frontier"].strip():
                errors.append(f"malformed legacy {collection}[{index}]")
                continue
            row = new_row(item["frontier"], item, collection)
            row["legacy_index"] = index
            issues: list[str] = []
            if collection == "blocked_data":
                row.update(state="blocked_data", reconciled=True)
                row["reconciliation"] = {"status": "reconciled", "reasons": [str(item.get("reason", "explicit legacy blocked_data"))]}
                continue
            if collection == "never_covered":
                row.update(state="ready", reconciled=True)
            else:
                evidence, issues = screening_evidence(item, root, records)
                row["screening_evidence"] = evidence
                row["completion_scope"] = evidence["completion_scope"]
                row["evidence"] = evidence["source_files"] + ([evidence["manifest"]] if "manifest" in evidence else [])
                try:
                    completed = timestamp(item.get("date"))
                    if completed > now:
                        raise ValueError("completion date is in the future")
                except (ValueError, TypeError) as exc:
                    issues.append(f"invalid completion date: {exc}")
                else:
                    if not issues:
                        row.update(last_completed_date=completed.date().isoformat(),
                                   last_completed_utc=iso(completed), state="complete", reconciled=True)
            row["reconciliation"] = {"status": "reconciled" if row["reconciled"] else "unreconciled", "reasons": issues}
            if item["frontier"].startswith("SPEC SLEEVE:"):
                row["excludes"].append("speculative sleeve evidence does not close a fundamental value frontier")

    # Append-only history can contain repeated labels. Keep the latest record
    # as current state and retain older rows as evidence, with data blocks taking
    # precedence over any duplicate ready/completed row.
    grouped: dict[str, list[dict]] = {}
    for row in frontiers:
        grouped.setdefault(row["frontier_id"], []).append(row)
    frontiers = []
    for history in grouped.values():
        blocked = [r for r in history if r["state"] == "blocked_data"]
        chosen = blocked[-1] if blocked else history[-1]
        if len(history) > 1:
            chosen["previous_legacy_records"] = [r["legacy_record"] for r in history if r is not chosen]
        frontiers.append(chosen)

    # Keep historic partial attempts visible, and let later, verified completion
    # supersede only a matching scope. No timeout is inferred for abandoned work.
    for key, audit in legacy.items():
        if not key.startswith("ledger_reconciliation_") or not isinstance(audit, dict):
            continue
        audit_date = key.removeprefix("ledger_reconciliation_")
        try:
            timestamp(audit_date)
        except ValueError:
            errors.append(f"invalid reconciliation date: {key}")
            continue
        attempts = audit.get("truncated_zero_block_frontiers", [])
        if not isinstance(attempts, list):
            errors.append(f"{key}.truncated_zero_block_frontiers must be a list")
            attempts = []
        attempted = audit.get("attempted_no_block_file", {})
        if not isinstance(attempted, dict):
            errors.append(f"{key}.attempted_no_block_file must be an object")
            attempted = {}
        pending_dirs = attempted.get("dirs", [])
        if not isinstance(pending_dirs, list):
            errors.append(f"{key}.attempted_no_block_file.dirs must be a list")
            pending_dirs = []
        attempts = list(attempts) + [{"frontier": ATTEMPT_ALIASES.get(d, f"Unfinished scope: {d}"), "artifact_dir": d}
                                     for d in pending_dirs if isinstance(d, str)]
        for attempt in attempts:
            if not isinstance(attempt, dict) or not isinstance(attempt.get("frontier"), str):
                errors.append(f"malformed attempt in {key}")
                continue
            name = attempt["frontier"]
            matches = [r for r in frontiers if r["frontier"] == name or
                       (name in {"US MID CAP (NYSE/Nasdaq)", "Japan small/mid cap"} and r["frontier"].startswith(name))]
            if not matches:
                matches = [new_row(name, attempt, key)]
            for row in matches:
                row.setdefault("attempt_history", []).append({"date": audit_date, "record": attempt})
                if row["state"] == "blocked_data":
                    continue
                if row["reconciled"] and row["last_completed_date"] and row["last_completed_date"] >= audit_date:
                    continue
                row["state"] = "in_progress"
                row["excludes"].append(f"unfinished/truncated attempt recorded by {key}; explicit completion or release required")

    # Runtime state is separate from the derived report so rebuilding coverage
    # cannot erase a claim, retry restriction, or explicit data block.
    overrides = lifecycle.get("frontiers", [])
    if not isinstance(overrides, list):
        errors.append("lifecycle frontiers must be a list")
        overrides = []
    for override in overrides:
        if not isinstance(override, dict) or not isinstance(override.get("frontier"), str):
            errors.append("malformed lifecycle frontier")
            continue
        matches = [r for r in frontiers if r["frontier"] == override["frontier"]]
        if len(matches) != 1:
            errors.append(f"lifecycle frontier must match exactly once: {override['frontier']}")
            continue
        row = matches[0]
        state = override.get("state")
        if state not in STATES:
            errors.append(f"invalid lifecycle state for {row['frontier']}")
            row["reconciled"] = False
            continue
        row["lifecycle_record"] = override
        # A lifecycle record cannot erase a legacy data block or unfinished work
        # simply by asserting ready/complete. Publish reconciled coverage first.
        if row["state"] == "blocked_data":
            continue
        if state in {"ready", "complete", "stale_revisit"}:
            if row["state"] == "in_progress" or not row["reconciled"]:
                row["excludes"].append("lifecycle readiness cannot replace missing reconciled completion")
                continue
        row["state"] = state
        if state == "retry_after":
            try:
                retry = timestamp(override.get("retry_after_utc"))
            except (ValueError, TypeError) as exc:
                row["reconciled"] = False
                row["reconciliation"]["reasons"].append(f"invalid retry date: {exc}")
            else:
                row["retry_after_utc"] = iso(retry)

    # Apply the explicit blocked scopes again after all source layers; duplicate
    # ready/covered labels cannot shadow the data failure.
    blocked_names = {data_scope(r["frontier"]) for r in frontiers if r["state"] == "blocked_data"}
    for row in frontiers:
        if data_scope(row["frontier"]) in blocked_names:
            row["state"] = "blocked_data"
        if row["state"] == "retry_after" and row["retry_after_utc"] and timestamp(row["retry_after_utc"]) <= now:
            row["state"] = "stale_revisit" if row["last_completed_date"] else "ready"
        row["reconciliation"]["status"] = "reconciled" if row["reconciled"] else "unreconciled"
        row["eligible"] = bool(row["reconciled"] and not row["excludes"] and row["state"] in {"ready", "complete", "stale_revisit"} and not errors)
    never = [r for r in frontiers if r["eligible"] and r["origin"] == "never_covered"]
    previous = [r for r in frontiers if r["eligible"] and r["last_completed_date"]]
    if never:
        # No imaginary pool sizes: sort only an explicitly recorded numeric size.
        candidates = sorted(never, key=lambda r: (-r["legacy_record"].get("pool_size", 0) if isinstance(r["legacy_record"].get("pool_size", 0), int) else 0, r["legacy_index"]))
    else:
        candidates = sorted(previous, key=lambda r: (r["last_completed_date"], r.get("legacy_index", 0)))
    for row in previous:
        if not never and row["state"] == "complete":
            row["state"] = "stale_revisit"
    selected = candidates[0] if candidates else None
    return {"frontiers": frontiers, "frontier_reconciliation": {
        "status": "blocked" if errors else "reconciled", "errors": errors,
        "legacy_receipt": receipt(legacy_path) if legacy_path.is_file() else {"path": str(legacy_path)},
        "lifecycle_receipt": receipt(lifecycle_path) if lifecycle_path.is_file() else None,
        "known_data_gaps": legacy.get("known_data_gaps", []),
        "policy": "usable never-covered first; otherwise oldest reconciled screening batch, excluding unfinished work and blocks; no invented revisit cadence",
        "eligibility_scope": "research routing only; completion never replaces source, valuation, catalyst, session, liquidity or paper-entry gates",
        "eligible_count": len([r for r in frontiers if r["eligible"]]),
        "selected_frontier_id": selected["frontier_id"] if selected else None,
        "selected_frontier": selected["frontier"] if selected else None,
        "selected_reason": ("largest documented usable untouched pool" if never else "least-recent reconciled screening batch without an unfinished/data-blocked shadow") if selected else "no reconciled eligible frontier",
    }}
