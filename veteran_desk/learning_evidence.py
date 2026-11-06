"""Durable cross-book PAPER outcome evidence; advisory, never changes trading rules.

Only canonical realized close legs enter the AUD evidence adapter. Open positions,
cancelled/unfilled rows and hypothetical results cannot become outcomes. Historical
peers are labelled separately from the requesting seat's own performance. This
module imports no provider, trading, book-writing or model code.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
from typing import Any

ROOT = Path("/opt/tfo/live/thf")
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_STATUS_BYTES = 4 * 1024 * 1024
STALE_SOURCE_S = 6 * 3600
STALE_PACKET_S = 15 * 60
SCHEMA_VERSION = 1
# Bump whenever outcome accounting changes so unchanged source files are replayed.
ADAPTER_ACCOUNTING_REVISION = 1
SLICE_SUFFIX = re.compile(r"-(?:T[12]|DXH|Q|P[0-9]+)$")
GUARD = (
    "SHARED CROSS-BOOK PAPER EVIDENCE — historical research, NOT your own THF P&L. "
    "Wins AND losses after recorded fees and known financing; trades are not "
    "independent bets. Hypotheses require forward tests. Never copy an old price "
    "or bypass stops, liquidity, sizing, drawdown, or risk gates."
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS source_state (
    source_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS close_events (
    event_key TEXT PRIMARY KEY, source_id TEXT NOT NULL, desk TEXT NOT NULL,
    seat TEXT NOT NULL, parent_id TEXT NOT NULL, content_hash TEXT NOT NULL,
    payload TEXT NOT NULL, valid INTEGER NOT NULL DEFAULT 1,
    first_seen_utc TEXT NOT NULL, last_seen_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS close_events_source ON close_events(source_id);
CREATE TABLE IF NOT EXISTS event_versions (
    revision INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT NOT NULL,
    content_hash TEXT NOT NULL, observed_utc TEXT NOT NULL,
    action TEXT NOT NULL, payload TEXT NOT NULL
);
"""


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _clock(value=None) -> dt.datetime:
    if value is None:
        return dt.datetime.now(dt.timezone.utc)
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            raise ValueError("now must have a timezone")
        return value.astimezone(dt.timezone.utc)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("now must be an aware datetime or finite epoch")
    return dt.datetime.fromtimestamp(value, dt.timezone.utc)


def _stamp(value) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(dt.timezone.utc) if parsed.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _num(value, *, nonnegative=False, positive=False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("number missing or not numeric")
    n = float(value)
    if not math.isfinite(n) or (nonnegative and n < 0) or (positive and n <= 0):
        raise ValueError("number outside finite cost/price domain")
    return n


def _text(value, limit=120) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))[:limit]


def _identity(value, field):
    if not isinstance(value, str) or not value or len(value) > 200 or re.search(r"[\x00-\x1f\x7f]", value):
        raise ValueError("%s must be a bounded stable string ID" % field)
    return value


def parent_id(value: str) -> str:
    """Recognize registered slice IDs without splitting arbitrary ticker text."""
    last = str(value)
    while True:
        current = SLICE_SUFFIX.sub("", last)
        if current == last:
            return current
        last = current


def default_sources(root=ROOT) -> list[dict]:
    """Same peer locations as thf_competition.sources; no normalized-P&L reuse.

    Fundamental/OANDA are coverage-only until their fee/currency adapters are
    audited. A peer's generic 'net' or unrealized P&L is not an outcome.
    """
    root = Path(root)
    peers = Path("/opt/tfo/data") if root.resolve() == ROOT else root
    return [
        {"id": "thf", "desk": "thf", "path": root / "state/thf/thf_book.json",
         "adapter": "seated_aud_v1"},
        {"id": "veteran", "desk": "veteran", "path": peers / "reports/veteran_desk_book.json",
         "adapter": "seated_aud_v1"},
        {"id": "fundamental", "desk": "fundamental", "path": peers / "reports/fundamental_book.json",
         "adapter": "unverified_json"},
        {"id": "oanda_aggro", "desk": "oanda_aggro",
         "path": peers / "state/oanda_aggro/trades.jsonl", "adapter": "unverified_jsonl"},
    ]


def _event_key(source, seat, trade_id):
    return _hash([source["id"], seat, trade_id])


def _hypothetical(row) -> bool:
    return any(row.get(k) for k in ("shadow", "lab", "counterfactual", "is_shadow", "hypothetical")) or (
        str(row.get("source") or row.get("kind") or "").lower()
        in ("shadow", "lab", "strategy_lab", "counterfactual")
    )


def _leg(row, source, seat, now):
    trade_id = _identity(row.get("id"), "trade")
    if not trade_id or not row.get("ticker") or row.get("side") not in ("LONG", "SHORT"):
        raise ValueError("missing stable ID, symbol or side")
    fill = _num(row.get("fill"), positive=True)
    shares = _num(row.get("shares"), positive=True)
    gross, net = _num(row.get("gross_aud")), _num(row.get("net_aud"))
    entry_fee = _num(row.get("cost_aud"), nonnegative=True)
    fees = gross - net
    if fees < -0.02 or fees + 0.03 < entry_fee:
        raise ValueError("gross/net/entry fee identity does not reconcile")
    closed, filled = _stamp(row.get("closed_utc")), _stamp(row.get("filled_utc"))
    if closed is None or filled is None or closed < filled or closed > now:
        raise ValueError("missing, future or inconsistent execution timestamps")
    # Canonical book.py debits this cumulative financing separately from net_aud.
    # It is repeated, not allocated, on partial slices; parent aggregation uses max.
    borrow = _num(row.get("borrow_accrued_aud", 0), nonnegative=True)
    prepaid = _num(row.get("borrow_min_charge_aud_est", 0), nonnegative=True)
    original = row.get("shares_orig")
    original = _num(original, positive=True) if original is not None else None
    if original is not None and shares > original + max(1e-7, original * 1e-8):
        raise ValueError("slice shares exceed original shares")
    return {
        "event_key": _event_key(source, seat, trade_id), "source_id": source["id"],
        "desk": source["desk"], "seat": seat, "id": trade_id,
        "parent_id": parent_id(trade_id), "ticker": _text(row["ticker"], 80),
        "side": row["side"], "venue": _text(row.get("venue"), 40),
        "grade": _text(row.get("grade"), 12), "sleeve": _text(row.get("sleeve"), 60),
        "filled_utc": filled.isoformat(), "closed_utc": closed.isoformat(),
        "fill": fill, "shares": shares, "shares_orig": original,
        "gross_aud": gross, "reported_net_aud": net, "fees_aud": max(0.0, fees),
        "borrow_cumulative_aud": borrow, "borrow_prepaid_aud": prepaid, "exit_reason": _text(row.get("exit_reason"), 100),
        "paper_only": True, "net_basis": "recorded canonical close, fees included; financing aggregated per parent",
    }


def _extract(data, source, now):
    """Return accepted legs, coverage, seen IDs, invalid IDs and active parent IDs."""
    if not isinstance(data, dict) or data.get("paper_only") is not True or not isinstance(data.get("seats"), dict):
        raise ValueError("canonical paper seated book schema is not verified")
    legs, seen, invalid, active, invalid_parents = {}, set(), set(), set(), set()
    counts = {"closed_rows": 0, "accepted_legs": 0, "excluded_rows": 0, "quarantined_rows": 0,
              "duplicate_rows": 0, "reasons": {}}
    for seat0, book in data["seats"].items():
        if not isinstance(book, dict):
            raise ValueError("seat book is not an object")
        seat = _identity(seat0, "seat")
        positions, closed = book.get("positions", {}), book.get("closed", [])
        if not isinstance(positions, dict) or not isinstance(closed, list):
            raise ValueError("positions/closed container schema is invalid")
        for position in positions.values():
            if isinstance(position, dict) and position.get("status") in ("open", "armed", "queued"):
                if position.get("id"):
                    active.add((seat, parent_id(str(position["id"]))))
        for row in closed:
            counts["closed_rows"] += 1
            if not isinstance(row, dict):
                counts["quarantined_rows"] += 1
                counts["reasons"]["invalid_row"] = counts["reasons"].get("invalid_row", 0) + 1
                continue
            key = _event_key(source, seat, str(row.get("id"))) if row.get("id") else None
            if key:
                seen.add(key)
            if row.get("status") != "closed" or _hypothetical(row) or row.get("fill") is None:
                counts["excluded_rows"] += 1
                if key:
                    invalid.add(key)
                    if isinstance(row.get("id"), str) and len(row["id"]) <= 200:
                        invalid_parents.add((seat, parent_id(row["id"])))
                continue
            try:
                leg = _leg(row, source, seat, now)
                if key in invalid:
                    raise ValueError("conflicting duplicate ID")
                if key in legs:
                    counts["duplicate_rows"] += 1
                    if _hash(legs[key]) != _hash(leg):
                        legs.pop(key)
                        invalid.add(key)
                        raise ValueError("conflicting duplicate ID")
                else:
                    legs[key] = leg
            except ValueError as exc:
                counts["quarantined_rows"] += 1
                reason = str(exc)
                counts["reasons"][reason] = counts["reasons"].get(reason, 0) + 1
                if key:
                    invalid.add(key)
                    legs.pop(key, None)
                    if isinstance(row.get("id"), str) and len(row["id"]) <= 200:
                        invalid_parents.add((seat, parent_id(row["id"])))
    for key in invalid:
        legs.pop(key, None)
    counts["accepted_legs"] = len(legs)
    return legs, counts, seen, invalid, active, invalid_parents


def _read_source(path: Path):
    before = path.stat()
    if before.st_size > MAX_SOURCE_BYTES:
        raise ValueError("source exceeds bounded read size")
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
        raise ValueError("source changed while being read; retry next cycle")
    return raw, after


def _connect(path):
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, SCHEMA_VERSION):
        conn.close()
        raise ValueError("unsupported learning database schema")
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA user_version=%d" % SCHEMA_VERSION)
    return conn


def _atomic(path: Path, data):
    encoded = (_json(data) + "\n").encode()
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _stats(trades):
    total = len(trades)
    nets = [row["net_after_costs_aud"] for row in trades]
    return {
        "completed_trades": total, "wins": sum(n > 0 for n in nets),
        "losses": sum(n < 0 for n in nets), "breakeven": sum(n == 0 for n in nets),
        "net_after_costs_aud": round(sum(nets), 2),
        "gross_aud": round(sum(row["gross_aud"] for row in trades), 2),
        "fees_aud": round(sum(row["fees_aud"] for row in trades), 2),
        "financing_aud": round(sum(row["financing_aud"] for row in trades), 2),
        "mean_net_aud": round(sum(nets) / total, 2) if total else None,
        "evidence": "descriptive historical PAPER outcomes; no validated alpha or independent-bet claim",
    }


def _aggregate(events, source_details):
    by_parent = {}
    for row in events:
        by_parent.setdefault((row["source_id"], row["seat"], row["parent_id"]), []).append(row)
    complete, partial, quarantined = [], [], []
    for (source_id, seat, pid), legs in sorted(by_parent.items()):
        info = source_details.get(source_id, {})
        active = {tuple(x) for x in info.get("active_parents", [])}
        shares = sum(row["shares"] for row in legs)
        originals = [row["shares_orig"] for row in legs if row["shares_orig"] is not None]
        original = max(originals) if originals else None
        invalidated = {tuple(x) for x in info.get("invalidated_parents", []) + info.get("current_invalid_parents", [])}
        inconsistent = len({(r["ticker"], r["side"], r["fill"], r["filled_utc"]) for r in legs}) != 1
        if inconsistent or (seat, pid) in invalidated:
            quarantined.append({"source_id": source_id, "seat": seat, "parent_id": pid,
                                "reason": "conflicting identity or invalidated close slice"})
            continue
        if original is not None and shares > original + max(1e-5, original * 1e-6):
            quarantined.append({"source_id": source_id, "seat": seat, "parent_id": pid,
                                "reason": "closed slice quantities exceed original position"})
            continue
        whole = (
            math.isclose(shares, original, rel_tol=1e-6, abs_tol=1e-5) if original is not None
            else any(row["id"] == pid for row in legs)
        )
        row0 = max(legs, key=lambda x: x["closed_utc"])
        borrow = max(row["borrow_cumulative_aud"] for row in legs)
        prepaid = max(row["borrow_prepaid_aud"] for row in legs)
        financing = borrow + prepaid
        record = {
            "source_id": source_id, "desk": row0["desk"], "seat": seat, "parent_id": pid,
            "ticker": row0["ticker"], "side": row0["side"], "grade": row0["grade"],
            "venue": row0["venue"], "sleeve": row0["sleeve"],
            "closed_utc": row0["closed_utc"], "legs": len(legs),
            "gross_aud": round(sum(row["gross_aud"] for row in legs), 2),
            "reported_net_aud": round(sum(row["reported_net_aud"] for row in legs), 2),
            "fees_aud": round(sum(row["fees_aud"] for row in legs), 2),
            "financing_aud": round(financing, 2),
            "daily_financing_aud": round(borrow, 2), "prepaid_financing_aud": round(prepaid, 2),
            "net_after_costs_aud": round(sum(row["reported_net_aud"] for row in legs) - financing, 2),
            "source_path": info.get("path"), "source_sha256": info.get("sha256"),
            "net_basis": "sum canonical net_aud minus max cumulative daily borrow and max prepaid minimum borrow; no duplicated partial financing",
        }
        if (seat, pid) in active or not whole:
            record["reason"] = "residual open/armed or final close not established"
            partial.append(record)
        else:
            complete.append(record)
    return complete, partial, quarantined


def _examples(trades):
    # Show both tails and recent evidence; never just profitable examples.
    slim = lambda r: {k: r[k] for k in (
        "desk", "seat", "parent_id", "ticker", "side", "grade", "venue", "closed_utc",
        "legs", "net_after_costs_aud", "fees_aud", "financing_aud")}
    return {
        "wins": [slim(r) for r in sorted(trades, key=lambda r: -r["net_after_costs_aud"])
                 if r["net_after_costs_aud"] > 0][:2],
        "losses": [slim(r) for r in sorted(trades, key=lambda r: r["net_after_costs_aud"])
                   if r["net_after_costs_aud"] < 0][:2],
        "recent": [slim(r) for r in sorted(trades, key=lambda r: r["closed_utc"], reverse=True)[:2]],
    }


def sync(root=ROOT, output_dir=None, now=None, sources=None) -> dict:
    """Import changed canonical snapshots and publish evidence; never writes books.

    sources is an optional list of explicit {id,desk,path,adapter} descriptors.
    Unknown adapters are coverage-only. New/corrected legs are versioned in one
    SQLite transaction. Disappearing rows remain historical evidence and produce
    a warning; explicit invalidation is quarantined rather than silently erased.
    """
    root, moment = Path(root), _clock(now)
    output = Path(output_dir) if output_dir is not None else root / "state/thf/autonomy"
    output.mkdir(parents=True, exist_ok=True)
    specs = default_sources(root) if sources is None else list(sources)
    ids = [_identity(str(spec["id"]), "source") for spec in specs]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate source IDs")
    paths = [str(Path(spec["path"]).resolve()) for spec in specs]
    if len(paths) != len(set(paths)):
        raise ValueError("duplicate canonical source paths")
    inodes = []
    for spec in specs:
        if spec.get("adapter") == "seated_aud_v1":
            try:
                stat = Path(spec["path"]).stat()
                inodes.append((stat.st_dev, stat.st_ino))
            except OSError:
                pass
    if len(inodes) != len(set(inodes)):
        raise ValueError("duplicate canonical source files")
    details, counters = {}, {"new_legs": 0, "corrected_legs": 0, "invalidated_legs": 0,
                             "unchanged_sources": 0, "changed_sources": 0}
    utc = moment.isoformat()
    conn = _connect(output / "learning.sqlite")
    try:
        conn.execute("BEGIN IMMEDIATE")
        for raw_spec in specs:
            spec = dict(raw_spec)
            spec["id"], spec["desk"] = str(spec["id"]), str(spec.get("desk") or spec["id"])
            path = Path(spec["path"])
            source_id = spec["id"]
            previous = conn.execute("SELECT * FROM source_state WHERE source_id=?", (source_id,)).fetchone()
            detail = {"id": source_id, "desk": spec["desk"], "path": str(path),
                      "adapter": spec.get("adapter"), "status": "unavailable"}
            try:
                stat = path.stat()
                fingerprint = _hash([str(path.resolve()), stat.st_mtime_ns, stat.st_size,
                                     spec.get("adapter"), spec["desk"], ADAPTER_ACCOUNTING_REVISION])
                if previous is not None and previous["fingerprint"] == fingerprint:
                    detail = json.loads(previous["detail"])
                    counters["unchanged_sources"] += 1
                else:
                    raw, stat = _read_source(path)
                    counters["changed_sources"] += 1
                    detail.update(mtime_utc=dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat(),
                                  sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), observed_utc=utc)
                    adapter = spec.get("adapter")
                    if adapter != "seated_aud_v1":
                        # Do not borrow thf_competition's broad fallback P&L fields.
                        if adapter == "unverified_jsonl":
                            rows = [line for line in raw.splitlines() if line.strip()]
                            coverage = len(rows)
                        else:
                            data = json.loads(raw)
                            coverage = sum(len(data.get(k) or []) for k in ("closed", "recovery_closes", "thesis_exits")) if isinstance(data, dict) else 0
                        detail.update(status="quarantined", quarantined_rows=coverage,
                                      reason="unverified realized net, currency and fee schema",
                                      active_parents=[])
                    else:
                        data = json.loads(raw)
                        legs, coverage, seen, invalid, active, invalid_parents = _extract(data, spec, moment)
                        existing = {row["event_key"]: row for row in conn.execute(
                            "SELECT * FROM close_events WHERE source_id=?", (source_id,))}
                        missing = set(existing) - seen
                        detail.update(coverage, status="ready", active_parents=sorted(active),
                                      current_invalid_parents=sorted(invalid_parents),
                                      adapter_accounting_revision=ADAPTER_ACCOUNTING_REVISION,
                                      missing_historical_rows=len(missing),
                                      asof_utc=data.get("updated") or data.get("asof_utc"))
                        for key, payload in legs.items():
                            content = _hash(payload)
                            old = existing.get(key)
                            if old is not None and old["content_hash"] == content and old["valid"]:
                                continue
                            counters["new_legs" if old is None else "corrected_legs"] += 1
                            action = "insert" if old is None else "correct"
                            conn.execute("INSERT INTO event_versions(event_key,content_hash,observed_utc,action,payload) VALUES(?,?,?,?,?)",
                                         (key, content, utc, action, _json(payload)))
                            conn.execute("""INSERT INTO close_events VALUES(?,?,?,?,?,?,?,?,?,?)
                                ON CONFLICT(event_key) DO UPDATE SET content_hash=excluded.content_hash,
                                payload=excluded.payload,valid=1,last_seen_utc=excluded.last_seen_utc""",
                                (key, source_id, spec["desk"], payload["seat"], payload["parent_id"],
                                 content, _json(payload), 1, utc, utc))
                        for key in invalid & set(existing):
                            old = existing[key]
                            if old["valid"]:
                                counters["invalidated_legs"] += 1
                                conn.execute("UPDATE close_events SET valid=0,last_seen_utc=? WHERE event_key=?", (utc, key))
                                conn.execute("INSERT INTO event_versions(event_key,content_hash,observed_utc,action,payload) VALUES(?,?,?,?,?)",
                                             (key, old["content_hash"], utc, "invalidate", old["payload"]))
                        if coverage["quarantined_rows"] or missing:
                            detail["status"] = "attention"
                    conn.execute("""INSERT INTO source_state VALUES(?,?,?)
                        ON CONFLICT(source_id) DO UPDATE SET fingerprint=excluded.fingerprint,detail=excluded.detail""",
                        (source_id, fingerprint, _json(detail)))
                asof = _stamp(detail.get("asof_utc")) or _stamp(detail.get("mtime_utc"))
                age = (moment - asof).total_seconds() if asof else None
                detail["age_s"] = round(age, 1) if age is not None else None
                detail["stale"] = age is None or age < -60 or age > STALE_SOURCE_S
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                old_detail = json.loads(previous["detail"]) if previous is not None else {}
                detail = dict(old_detail, **detail)
                detail.update(status="unavailable", reason=type(exc).__name__ + ": " + str(exc)[:180],
                              stale=True, active_parents=old_detail.get("active_parents", []))
            details[source_id] = detail
        events = []
        for record in conn.execute("SELECT payload FROM close_events WHERE valid=1"):
            payload = json.loads(record["payload"])
            if payload["source_id"] in details:
                events.append(payload)
        invalidated = 0
        for record in conn.execute("SELECT source_id,seat,parent_id FROM close_events WHERE valid=0"):
            if record["source_id"] in details:
                invalidated += 1
                details[record["source_id"]].setdefault("invalidated_parents", []).append(
                    [record["seat"], record["parent_id"]])
                if details[record["source_id"]]["status"] == "ready":
                    details[record["source_id"]]["status"] = "attention"
        versions = conn.execute("SELECT count(*) FROM event_versions").fetchone()[0]
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    complete, partial, quarantined = _aggregate(events, details)
    desks, seats = {}, {}
    for source_id, detail in details.items():
        trades = [r for r in complete if r["source_id"] == source_id]
        own_partial = [r for r in partial if r["source_id"] == source_id]
        desks[source_id] = dict(_stats(trades), partial_parents=len(own_partial),
                               partial_reported_net_aud=round(sum(r["reported_net_aud"] for r in own_partial), 2),
                               examples=_examples(trades), source_status=detail["status"])
        if detail.get("adapter") != "seated_aud_v1":
            # No eligible adapter means unknown performance, not a zero-P&L book.
            for name in ("wins", "losses", "breakeven", "net_after_costs_aud", "gross_aud",
                         "fees_aud", "financing_aud", "mean_net_aud", "partial_reported_net_aud"):
                desks[source_id][name] = None
            desks[source_id]["evidence"] = "coverage only; after-cost realized performance is unknown"
    for row in complete + partial:
        key = row["source_id"] + "/" + row["seat"]
        if key not in seats:
            trades = [r for r in complete if r["source_id"] == row["source_id"] and r["seat"] == row["seat"]]
            seats[key] = dict(_stats(trades), examples=_examples(trades))
    hypotheses = []
    for key, stats in sorted(seats.items()):
        if stats["completed_trades"] >= 3:
            hypotheses.append({
                "id": "review-" + _hash(key)[:16], "source_seat": key,
                "status": "requires_preregistered_forward_test",
                "question": "Check whether setup/venue timing and after-cost execution explain BOTH wins and losses.",
                "sample_trades": stats["completed_trades"],
                "may_change_risk_limits": False, "may_auto_promote": False,
            })
    problem = any(d["status"] != "ready" or d.get("stale") for d in details.values()) or bool(quarantined) or bool(invalidated)
    result = {
        "schema_version": SCHEMA_VERSION, "paper_only": True, "advisory_only": True,
        "generated_utc": utc, "status": "attention" if problem else "ready",
        "guard": GUARD, "sources": list(details.values()), "desks": desks, "seats": seats,
        "totals": dict(_stats(complete), accepted_close_legs=len(events), partial_parents=len(partial),
                       quarantined_parent_groups=len(quarantined), invalidated_legs=invalidated,
                       evidence_versions=versions),
        "changes": counters, "hypotheses": hypotheses[:30],
        "quarantine_examples": quarantined[:10],
        "limitations": ["No alpha or future-profit inference from small samples.",
                       "Partial legs are not independent trades; incomplete parents excluded from completed-trade stats.",
                       "Unknown fee/currency schemas are coverage-only, never assigned zero costs.",
                       "Historical source outcomes are not live entry quotes or authority to change risk."],
    }
    _atomic(output / "learning_status.json", result)
    return result


def _prompt_stats(stats):
    """Keep every outcome count and cost total without repeating long narratives."""
    if not isinstance(stats, dict):
        return {"completed_trades": 0}
    return {k: stats[k] for k in (
        "completed_trades", "wins", "losses", "breakeven", "net_after_costs_aud",
        "fees_aud", "financing_aud") if k in stats}


def _prompt_example(row, source_id):
    """Public trace IDs resolve to stored parent identity; no raw IDs in prompts."""
    if not isinstance(row, dict):
        return None
    try:
        net = _num(row.get("net_after_costs_aud"))
    except ValueError:
        return None
    identity = [source_id, row.get("seat"), row.get("parent_id")]
    return {
        "trace_id": "outcome-" + _hash(identity)[:12], "source_id": source_id,
        "desk": _text(row.get("desk"), 60), "seat": _text(row.get("seat"), 100),
        "ticker": _text(row.get("ticker"), 80), "side": _text(row.get("side"), 10),
        "closed_utc": row.get("closed_utc"), "net_after_costs_aud": net,
        "fees_aud": row.get("fees_aud"), "financing_aud": row.get("financing_aud"),
    }


def _shared_prompt(status):
    """Select balanced verified evidence across all seats, never unvetted peers."""
    source_map = {source.get("id"): source for source in status.get("sources", [])
                  if isinstance(source, dict)}
    desks, candidates = {}, []
    for source_id, stats in status.get("desks", {}).items():
        source = source_map.get(source_id, {})
        if source.get("adapter") != "seated_aud_v1" or not isinstance(stats, dict):
            continue
        desks[source_id] = dict(_prompt_stats(stats), source_status=source.get("status"),
                                stale=bool(source.get("stale")))
        examples = stats.get("examples") or {}
        for kind in ("wins", "losses"):
            for row in examples.get(kind, []):
                item = _prompt_example(row, source_id)
                if item is not None:
                    candidates.append(item)
    wins = [row for row in candidates if row["net_after_costs_aud"] > 0]
    losses = [row for row in candidates if row["net_after_costs_aud"] < 0]
    return {
        "scope": "ALL seats; verified historical paper closes, not your own record",
        "desks": desks,
        "verified_win": max(wins, key=lambda row: row["net_after_costs_aud"]) if wins else None,
        "verified_loss": min(losses, key=lambda row: row["net_after_costs_aud"]) if losses else None,
    }


def context_for(seat, root=ROOT, max_chars=4000) -> str:
    """Cheap bounded prompt packet from published evidence, with cross-book labels."""
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 0:
        raise ValueError("max_chars must be a nonnegative integer")
    if not max_chars:
        return ""
    heading = "\n" + GUARD + "\n"
    path = Path(root) / "state/thf/autonomy/learning_status.json"
    try:
        if path.stat().st_size > MAX_STATUS_BYTES:
            raise ValueError("learning status exceeds bounded read size")
        status = json.loads(path.read_text())
        if not isinstance(status, dict) or status.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported learning evidence schema")
        stamp = _stamp(status.get("generated_utc"))
        age = (dt.datetime.now(dt.timezone.utc) - stamp).total_seconds() if stamp else None
        packet = {
            "seat": _text(seat, 100), "generated_utc": status.get("generated_utc"),
            "stale": age is None or age < -60 or age > STALE_PACKET_S,
            "status": status.get("status"),
            "quality": {k: status.get("totals", {}).get(k) for k in
                        ("invalidated_legs", "quarantined_parent_groups", "partial_parents")},
            "own_thf_history": _prompt_stats(status.get("seats", {}).get("thf/" + str(seat), {"completed_trades": 0})),
            "peer_veteran_history": _prompt_stats(status.get("seats", {}).get("veteran/" + str(seat), {"completed_trades": 0})),
            "shared_cross_book": _shared_prompt(status),
            "coverage": [{"desk": s.get("desk"), "status": s.get("status"), "stale": s.get("stale"),
                          "quarantined_rows": s.get("quarantined_rows", 0),
                          "missing_historical_rows": s.get("missing_historical_rows", 0)}
                         for s in status.get("sources", [])],
            "hypotheses": [h for h in status.get("hypotheses", []) if h.get("source_seat", "").endswith("/" + str(seat))][:2],
        }
        body = _json(packet)
        # Drop optional narrative before balanced evidence at the 4,000-char budget.
        if len(heading) + len(body) > max_chars:
            packet["hypotheses"] = []
            body = _json(packet)
        if len(heading) + len(body) > max_chars:
            for key in ("verified_win", "verified_loss"):
                example = packet["shared_cross_book"].get(key)
                if example:
                    packet["shared_cross_book"][key] = {k: v for k, v in example.items()
                        if k not in ("side", "fees_aud", "financing_aud")}
            body = _json(packet)
        # Smaller callers can omit examples, but never receive truncated JSON/stats.
        if len(heading) + len(body) > max_chars:
            packet["shared_cross_book"]["verified_win"] = None
            packet["shared_cross_book"]["verified_loss"] = None
            packet["shared_cross_book"]["examples_omitted_for_budget"] = True
            body = _json(packet)
        if len(heading) + len(body) <= max_chars:
            return heading + body + ("\n" if len(heading) + len(body) < max_chars else "")
        return (heading + "Evidence packet exceeds this prompt budget; consult learning_status.json. "
                "Do not infer profitability from an omitted sample.")[:max_chars]
    except (OSError, ValueError, TypeError, AttributeError):
        return (heading + "Evidence unavailable; no new learned claim. Existing risk rules still apply.")[:max_chars]
