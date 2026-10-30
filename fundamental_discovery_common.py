#!/usr/bin/env python3
"""Shared, read-only parsing helpers for TFO discovery-integrity tools.

This module deliberately knows nothing about the live book or execution.  It
reads research artefacts and returns evidence with line numbers.  Callers decide
whether a legacy block is informative, incomplete, or eligible for a strict v2
manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


FIELD = re.compile(
    r"^\s*\**\s*([A-Z][A-Z0-9 _/+\-]{1,40}?)\s*\**\s*:\s*(.*)$",
    re.IGNORECASE,
)
VERDICTS = frozenset(
    {
        "undervalued",
        "value_trap",
        "fairly_priced",
        "insufficient_evidence",
        "not_applicable",
    }
)
TRADE_DIRECTIONS = frozenset({"LONG", "SHORT", "NEITHER"})


def canonical_key(raw: str) -> str:
    return re.sub(r"\s+", " ", raw.strip().upper())


def leading_word(raw: str | None) -> str:
    words = re.findall(r"[A-Za-z_]+", (raw or "").replace("*", ""))
    return words[0] if words else ""


def normalise_symbol(raw: str | None) -> str:
    value = re.sub(r"[`*<>]", "", raw or "").strip()
    return value.split()[0] if value else ""


def parse_blocks(path: str | Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse every SYMBOL block, retaining incomplete blocks and evidence lines."""

    source = Path(path)
    text = source.read_text(encoding="utf-8", errors="replace")
    blocks: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    last_key: str | None = None

    def finish(end_line: int) -> None:
        nonlocal current, last_key
        if current is None:
            return
        current["_end_line"] = end_line
        current["_symbol"] = normalise_symbol(current.get("SYMBOL"))
        if not current["_symbol"]:
            issues.append(
                {
                    "file": str(source),
                    "line": current.get("_start_line"),
                    "kind": "missing_symbol",
                }
            )
        blocks.append(current)
        current = None
        last_key = None

    lines = text.splitlines()
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if current is not None and (stripped == "```" or stripped.startswith("#")):
            # Most legacy blocks are fenced.  Closing the block here prevents a
            # final SOURCES field from swallowing the run summary that follows.
            finish(number - 1)
            continue
        match = FIELD.match(line)
        key = canonical_key(match.group(1)) if match else None
        value = match.group(2).strip() if match else None
        if key == "SYMBOL":
            finish(number - 1)
            current = {
                "_file": str(source),
                "_start_line": number,
                "_duplicates": [],
                "SYMBOL": value,
            }
            last_key = "SYMBOL"
            continue
        if current is None:
            continue
        if match:
            if key in current:
                current["_duplicates"].append(key)
                current[key] = f"{current[key]} || {value}"
            else:
                current[key] = value
            last_key = key
        elif line.strip() and line.strip() not in {"```", "---"} and last_key:
            # Continuations matter for multi-line catalysts, KILLS IT and SOURCES.
            current[last_key] = f"{current[last_key]} {line.strip()}".strip()
    finish(len(lines))
    return blocks, issues


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: str | Path, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def atomic_text(path: str | Path, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def summary_screened_claims(path: str | Path) -> list[int]:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return [
        int(value.replace(",", ""))
        for value in re.findall(
            r"RUN\s+SUMMARY\s*:\s*\**\s*([0-9][0-9,]*)\s+screened",
            text,
            flags=re.IGNORECASE,
        )
    ]


def full_block_claims(path: str | Path) -> list[int]:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    patterns = (
        r"FULL\s+BLOCKS\s+(?:WRITTEN|RETURNED)\s*[:=]?\s*([0-9][0-9,]*)",
        r"([0-9][0-9,]*)\s+FULL\s+(?:§6\s+)?BLOCKS",
    )
    values: list[int] = []
    for pattern in patterns:
        values.extend(
            int(value.replace(",", ""))
            for value in re.findall(pattern, text, flags=re.IGNORECASE)
        )
    return values


def source_looks_verified(raw: str | None) -> bool:
    text = (raw or "").strip().lower()
    if not text or text in {"n/a", "na", "none", "not applicable"} or any(
        marker in text
        for marker in (
            "not sourced",
            "none assessed",
            "not researched",
            "source unavailable",
        )
    ):
        return False
    # A date says when a claim was allegedly observed; it does not identify a
    # source that another reviewer can reopen.  Require a retrievable URL or an
    # explicit local evidence path, never date-only prose.
    return bool(re.search(r"https?://|/Users/", raw or ""))


def block_research_state(block: dict[str, Any]) -> tuple[str, list[str]]:
    """Return a conservative research state and reasons.

    A low-information reject is never promoted to `researched_no_hit`.  If the
    evidence is ambiguous it stays `unknown`, preserving the distinction the
    legacy ledger lost.
    """

    reasons: list[str] = []
    verdict = leading_word(block.get("VERDICT")).lower()
    if not block.get("_symbol") or verdict not in VERDICTS:
        return "incomplete", ["missing symbol or exact verdict"]

    combined = " ".join(
        str(block.get(field, ""))
        for field in (
            "WHY CHEAP",
            "CATALYST",
            "TECH/OPPORTUNITY",
            "CONTRACTS/TENDERS",
            "NEWS FLOW 90D",
            "EVENT CALENDAR",
            "LIQUIDITY",
            "TRADE DIRECTION",
        )
    ).lower()
    explicit_not_researched = any(
        marker in combined
        for marker in (
            "not researched after",
            "not assessed after",
            "research stopped at",
            "hard-gate failure",
            "hard gate failed",
            "gate/scope refusal",
            "no valuation work was performed",
        )
    )
    if explicit_not_researched:
        return "not_researched", ["block explicitly says research stopped at gate/scope"]

    substantive_fields = 0
    for field in (
        "WHY CHEAP",
        "CHEAPNESS",
        "DIRECTION",
        "METRICS",
        "CATALYST",
        "KILLS IT",
        "SOURCES",
    ):
        value = str(block.get(field, "")).strip()
        if len(value) >= 18 and "not researched" not in value.lower():
            substantive_fields += 1
    if substantive_fields < 5 or not source_looks_verified(block.get("SOURCES")):
        reasons.append(f"only {substantive_fields}/7 substantive core fields or sources unverified")
        return "unknown", reasons
    if verdict == "undervalued":
        return "researched_hit", []
    return "researched_no_hit", []


def iso_dates(raw: str | None) -> list[str]:
    return sorted(set(re.findall(r"\b20\d{2}-[01]\d-[0-3]\d\b", raw or "")))
