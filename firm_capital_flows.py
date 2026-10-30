"""Consolidated capital accounting. Reads metadata; never alters a source book.

Internal transfers net to zero. Funding basis changes are distinct from marks.
Unreadable/missing books are not withdrawals. Unresolved changes fail closed.
"""
import hashlib
import os
import json
import math


VERSION = 2
EXTERNAL_TYPES = {"injection", "capital_contribution", "contribution", "deposit",
                  "capital_withdrawal", "withdrawal",
                  "leaderboard_prize"}  # VD paper prize capital (funder-authorised, not P&L) 2026-10-30 custodian


def source_fingerprints(previous):
    """Detect a funding write while book valuations are being gathered.

Normal marks do not affect this digest. An in-flight transfer can temporarily
expose just its receiving leg; that tick must not advance capital or the peak.
"""
    result = {}
    for key, meta in (previous or {}).items():
        try:
            with open(meta["path"]) as f:
                data = json.load(f)
            fields = {k: data.get(k) for k in ("start", "start_cash", "seed", "seed_usd",
                      "seed_aud", "contributed_capital_usd", "capital_events")}
            result[key] = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()
        except (OSError, KeyError, TypeError, ValueError):
            result[key] = "UNREADABLE"
    return result


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def snapshot(books, previous=None):
    """Keep native funding stable when the reporting FX rate changes."""
    out, errors = {}, []
    rows = {b["key"]: b for b in books}
    for key in sorted(set(rows) | set(previous or {})):
        b = rows.get(key)
        prior = (previous or {}).get(key, {})
        path = (b or {}).get("path") or prior.get("path")
        if b and b.get("error"):
            errors.append("%s: unreadable book; funding snapshot not advanced" % key)
            continue
        try:
            with open(path) as f:
                ledger = json.load(f)
        except (OSError, TypeError, ValueError) as exc:
            # A removed book must retain its ledger/withdrawal receipt so that
            # de-registration cannot erase its remaining equity as a 'flow'.
            errors.append("%s: funding ledger unreadable (%s)" % (key, exc))
            continue
        if not isinstance(ledger, dict):
            errors.append("%s: funding ledger is not an object" % key)
            continue
        ccy = (b or {}).get("ccy", prior.get("ccy", "USD"))
        usd = _number((b or {}).get("start_usd"))
        if ccy == "USD":
            native = usd
            rate = 1.0
        else:
            native = next((_number(ledger[k]) for k in ("start_cash", "seed", "seed_aud")
                           if _number(ledger.get(k)) is not None), None)
            rate = usd / native if usd is not None and native else None
        events = []
        # Optional sidecar receipts (MultiVenueDesk/reports/capital_event_sidecars/<key>.json)
        # used when a live writer (e.g. kraken_paper_bot.save_state) strips capital_events.
        _side_events = []
        try:
            _side_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "reports", "capital_event_sidecars", key + ".json")
            if os.path.isfile(_side_path):
                with open(_side_path) as _sf:
                    _side = json.load(_sf)
                if isinstance(_side, dict):
                    _side_events = list(_side.get("capital_events") or [])
        except (OSError, TypeError, ValueError):
            _side_events = []
        for raw in list(ledger.get("capital_events") or []) + _side_events:
            kind = raw.get("type")
            if kind != "capital_transfer" and kind not in EXTERNAL_TYPES:
                continue  # restatement/authorization notes are NOT fresh flows
            amount = _number(raw.get("usd", raw.get("amount_usd")))
            if amount is None or not raw.get("utc"):
                errors.append("%s: capital event lacks a finite USD amount/date" % key)
                continue
            if kind in {"withdrawal", "capital_withdrawal"}:
                amount = -abs(amount)
            event = {"book": key, "utc": raw["utc"], "type": kind,
                     "usd": amount, "from": raw.get("from"), "to": raw.get("to")}
            if "performance_flow" in raw:
                event["performance_flow"] = raw["performance_flow"]
            event["id"] = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
            events.append(event)
        out[key] = {"path": path, "present": b is not None, "ccy": ccy,
                    "native_capital": native, "usd_per_native": rate,
                    "events": events}
        if key == "coinspot_floor" and b is None:
            from coinspot_retirement import verified_retirement
            receipt = verified_retirement()
            if receipt:
                out[key]["verified_retirement"] = {
                    "transfer_usd": receipt["transfer_usd"],
                    "transfer_utc": receipt["transfer_utc"],
                    "to": "gap_close_levered_book"}
        # Verified zero-balance archive (book_archive.py, HT Option A 2026-09-25).
        import book_archive
        if b is None:
            arc = book_archive.verified_archive(key)
            if arc:
                out[key]["verified_archive"] = {
                    "retained_funding_basis_usd": arc["retained_funding_basis_usd"],
                    "archived_utc": arc["archived_utc"],
                    "funding_fingerprint": arc["funding_fingerprint"]}
        elif book_archive.receipt_exists(key):
            out[key]["archive_receipt_void"] = True
    return out, errors


def reconcile(previous, current):
    """Return net EXTERNAL flow, diagnostics and an auditable event breakdown.

    A newly registered book may establish its documented initial funding basis.
    Existing-book changes require receipts; silently editing seed is not a flow.
    Cross-book transfers require matching receipts. A removed book requires an
    explicit external withdrawal.
"""
    errors, details, net = [], [], 0.0
    seen = {e["id"] for b in previous.values() for e in b.get("events", [])}
    events = [e for b in current.values() for e in b.get("events", [])
              if e["id"] not in seen]
    for e in events:
        if e["type"] != "capital_transfer":
            continue
        other = e["from"] if e["usd"] > 0 else e["to"]
        mates = [m for m in events if m["type"] == "capital_transfer"
                 and m["book"] == other and m["utc"] == e["utc"]
                 and abs(m["usd"] + e["usd"]) < 0.005
                 and (m["to"] if m["usd"] < 0 else m["from"]) == e["book"]]
        if len(mates) != 1:
            errors.append("%s: internal transfer has no unique paired receipt" % e["book"])
    for key in sorted(set(previous) | set(current)):
        old, new = previous.get(key), current.get(key)
        if not new:
            errors.append("%s: disappeared without readable funding metadata" % key)
            continue
        old_present = bool(old and old.get("present", True))
        new_present = new.get("present", True)
        moves = [e for e in events if e["book"] == key]
        explicit = sum(e["usd"] for e in moves)
        if not new_present:
            if not old_present:
                if moves:
                    errors.append("%s: capital changed outside firm coverage" % key)
                continue
            retirement = new.get("verified_retirement")
            archive = new.get("verified_archive")
            if archive and not moves:
                amount = 0.0
                basis = ("verified zero-balance archive; retained funding basis %.2f"
                         % archive.get("retained_funding_basis_usd", 0.0))
            elif archive and all(e["type"] == "capital_transfer" for e in moves):
                # Paired dust sweep before archive (pairing verified above): the
                # outbound leg offsets the receiving book's inbound leg.
                amount = explicit
                basis = ("verified zero-balance archive after paired internal transfer; retained funding basis %.2f"
                         % archive.get("retained_funding_basis_usd", 0.0))
            elif (key == "coinspot_floor" and retirement and len(moves) == 1
                    and moves[0]["type"] == "capital_transfer"
                    and moves[0]["to"] == retirement.get("to")
                    and moves[0]["utc"] == retirement.get("transfer_utc")
                    and abs(explicit + retirement.get("transfer_usd", 0)) < .005):
                amount, basis = explicit, "verified retirement: paired internal transfer"
            elif not moves or any(e["type"] == "capital_transfer" for e in moves):
                errors.append("%s: removed book lacks an explicit external withdrawal" % key)
                continue
            elif explicit >= 0:
                errors.append("%s: removal is not a negative external flow" % key)
                continue
            else:
                amount, basis = explicit, "explicit withdrawal on book removal"
        else:
            if new.get("archive_receipt_void"):
                # An archived book is registered again: its archive no longer
                # verifies. Re-counting its seed would be a phantom flow.
                errors.append("%s: archived book reappeared; archive receipt no longer verifies" % key)
                continue
            before = old.get("native_capital") if old_present else 0.0
            after = new.get("native_capital")
            rate = new.get("usd_per_native")
            if old_present and old.get("ccy") != new.get("ccy"):
                errors.append("%s: funding currency changed; cannot classify flow" % key)
                continue
            if before is None or after is None or rate is None:
                if not old_present:
                    if moves:
                        amount, basis = explicit, "explicit funding of new book"
                    else:
                        errors.append("%s: new book has no recorded funding basis" % key)
                        continue
                elif moves:
                    amount, basis = explicit, "explicit capital events"
                else:
                    continue
            else:
                amount = (after - before) * rate
                basis = "recorded native funding-basis delta"
                if old_present and abs(amount) >= 0.005 and not moves:
                    errors.append("%s: funding basis changed without a capital-flow receipt" % key)
                    continue
                if moves and abs(amount - explicit) > 0.02:
                    errors.append("%s: funding basis delta %.2f != event flow %.2f" %
                                  (key, amount, explicit))
                    continue
            if abs(amount) < 0.005 and not moves:
                continue
        net += amount
        details.append({"book": key, "usd": round(amount, 2), "basis": basis,
                        "events": moves})
    return round(net, 2), details, errors


def capital_event(prev, books):
    previous = prev.get("capital_snapshot")
    current, errors = snapshot(books, previous)
    old_keys = set(prev.get("book_keys") or [])
    # Include unreadable keys. A failed read never becomes a withdrawal.
    new_keys = {b["key"] for b in books}
    added, removed = sorted(new_keys - old_keys), sorted(old_keys - new_keys)
    if previous is None:
        net, details = 0.0, []
        if old_keys and old_keys != new_keys:
            errors.append("legacy funding snapshot absent during book-set change")
    elif errors:
        net, details = 0.0, []
    else:
        net, details, errors = reconcile(previous, current)
    peak_shift = net
    owner_flows = [e for d in details for e in d.get("events", [])
                   if "performance_flow" in e]
    if not errors and owner_flows:
        external = [e for d in details for e in d.get("events", [])
                    if e["type"] != "capital_transfer"]
        try:
            if len(owner_flows) != 1 or len(external) != 1:
                raise ValueError("owner redemption requires one isolated external flow")
            e = owner_flows[0]
            receipt = e["performance_flow"]
            equity, peak = _number(prev.get("firm_equity_usd")), _number(prev.get("firm_peak_usd"))
            if (e["type"] not in {"withdrawal", "capital_withdrawal"}
                    or not equity or not peak or peak < equity
                    or not -equity < e["usd"] < 0 or abs(net-e["usd"]) > .005
                    or receipt.get("method") != "owner_unit_redemption_v1"
                    or receipt.get("pre_flow_risk_utc") != prev.get("utc")
                    or receipt.get("firm_equity_before_usd") != equity
                    or receipt.get("firm_peak_before_usd") != peak
                    or prev.get("capital_accounting_valid") is not True
                    or prev.get("drawdown_valid") is not True):
                raise ValueError("owner redemption receipt does not match verified pre-flow risk state")
            # Redeem units at unchanged NAV; preserve existing percentage loss.
            # Market changes after the receipted pre-flow valuation still affect drawdown.
            peak_shift = peak*e["usd"]/equity
        except (AttributeError, TypeError, ValueError) as exc:
            errors.append(str(exc))
    valid = not errors
    return {"capital_event": bool(details), "books_added": added, "books_removed": removed,
            "added_equity_usd": 0.0, "removed_equity_usd": 0.0,
            "external_flow_usd": net if valid else None,
            "peak_shift_usd": peak_shift if valid else 0.0,
            "owner_performance_flows": owner_flows if valid else [],
            "capital_accounting_version": VERSION,
            "capital_accounting_valid": valid, "capital_accounting_errors": errors,
            "capital_flow_details": details,
            "capital_snapshot": current if valid else (previous or {}),
            "capital_basis_note": "net external funding; paired internal transfers net to zero; unreadable books are not withdrawals"}


def replay_complete(rows, since_utc, initial_peak, initial_peak_at, external_flows=()):
    """Replay a VERIFIED interval; incomplete observations cannot move a peak.

No claim about performance before since_utc. External flows must be supplied
from verified receipts, never inferred from the old (potentially bad) peak.
"""
    peak, peak_at, out = float(initial_peak), initial_peak_at, []
    pending = sorted(external_flows, key=lambda e: e["utc"])
    index = 0
    for row in rows:
        if row["utc"] < since_utc:
            continue
        complete = (int(row["books_readable"]) == int(row["books_total"])
                    and row.get("level") != "UNMEASURED")
        if not complete:
            out.append({"utc": row["utc"], "complete": False, "firm_equity_usd": None,
                        "firm_peak_usd": round(peak, 2), "firm_drawdown_pct": None})
            continue
        while index < len(pending) and pending[index]["utc"] <= row["utc"]:
            peak += float(pending[index]["usd"])
            index += 1
        eq = float(row["firm_equity_usd"])
        if not math.isfinite(eq) or eq < 0 or not math.isfinite(peak) or peak <= 0:
            raise ValueError("invalid equity/peak in replay")
        if eq > peak:
            peak, peak_at = eq, row["utc"]
        out.append({"utc": row["utc"], "complete": True, "firm_equity_usd": eq,
                    "firm_peak_usd": round(peak, 2),
                    "firm_drawdown_pct": round(100 * (eq / peak - 1), 6)})
    if not out or not any(r["complete"] for r in out):
        raise ValueError("no complete observations in replay interval")
    return {"peak": round(peak, 2), "peak_at": peak_at, "rows": out,
            "basis_since_utc": since_utc,
            "basis_note": "verified-interval drawdown, NOT inception return or P&L"}
