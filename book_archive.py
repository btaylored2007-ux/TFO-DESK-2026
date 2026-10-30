"""Verified zero-balance book archive (HT decision 2026-09-25, Option A). PAPER ONLY.

Modelled on coinspot_retirement: an instruction alone cannot hide a funded book.
A receipt at reports/book_archives/<key>.json de-registers a book ONLY while its
ledger re-verifies on every read:
  * ledger readable, not a symlink, still at the receipt path;
  * zero balance: cash/equity/nav 0, no holdings/positions/open or pending orders
    (carry AUD shape: cap + realized + funding - costs == 0);
  * funding fingerprint unchanged (seed/start fields + capital events), so the
    retained funding basis carried by the receipt is still the book's basis.
The retained basis keeps firm funding totals, P&L-since-seed and
capital_accounting_valid unchanged when the book leaves the registry. If any
check fails the archive is void, the book re-registers, and firm_capital_flows
flags the reappearance as a capital-accounting error (loud, never silent).
"""
import glob
import hashlib
import json
import math
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent
ARCHIVE_DIR = BASE / "reports" / "book_archives"
FUNDING_FIELDS = ("start", "start_cash", "seed", "seed_usd", "seed_aud",
                  "contributed_capital_usd", "notional_cap_aud", "original_start_cash")
FLOW_TYPES = {"capital_transfer", "deposit", "withdrawal", "capital_injection",
              "capital_withdrawal", "external_deposit", "external_withdrawal"}
BALANCE_FIELDS = ("cash", "equity", "nav", "marked_equity")
BOOK_FIELDS = ("holdings", "positions", "open_orders", "pending_orders",
               "queued_orders", "open_tickets", "legs", "pending_order", "position",
               "quantity")


def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def funding_fingerprint(ledger):
    fields = {k: ledger.get(k) for k in FUNDING_FIELDS if k in ledger}
    events = [e for e in (ledger.get("capital_events") or [])
              if isinstance(e, dict) and e.get("type") in FLOW_TYPES]
    blob = json.dumps({"fields": fields, "events": events}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def zero_balance_problems(ledger):
    """Return a list of reasons the ledger is NOT a zero-balance book."""
    bad = []
    if not isinstance(ledger, dict):
        return ["ledger is not an object"]
    for k in BALANCE_FIELDS:
        if k in ledger and ledger[k] is not None:
            if not _finite(ledger[k]) or abs(float(ledger[k])) >= 0.005:
                bad.append("%s=%r" % (k, ledger[k]))
    for k in BOOK_FIELDS:
        if ledger.get(k):
            bad.append("%s not empty" % k)
    if ledger.get("desks"):
        bad.append("multi-desk ledger not supported")
    if "notional_cap_aud" in ledger:  # carry AUD shape publishes no equity field
        try:
            eq = (float(ledger.get("notional_cap_aud")) + float(ledger.get("realized_aud") or 0)
                  + float(ledger.get("funding_accrued_aud") or 0)
                  - float(ledger.get("costs_paid_aud") or 0))
            if not math.isfinite(eq) or abs(eq) >= 0.005:
                bad.append("carry derived equity %.4f AUD" % eq)
        except (TypeError, ValueError):
            bad.append("carry equity not derivable")
    elif not any(k in ledger for k in ("cash",)):
        bad.append("no cash field")
    return bad


def _receipt_path(key):
    return ARCHIVE_DIR / ("%s.json" % key)


def receipt_exists(key):
    return _receipt_path(key).is_file()


def verified_archive(key):
    """Receipt dict (with retained_funding_basis_usd) if the archive re-verifies, else None."""
    try:
        p = _receipt_path(key)
        if p.is_symlink():
            return None
        receipt = json.loads(p.read_bytes())
        if (receipt.get("state") != "ARCHIVED" or receipt.get("paper_only") is not True
                or receipt.get("key") != key):
            return None
        ledger_path = Path(receipt["ledger_path"])
        if ledger_path.is_symlink() or not ledger_path.is_file():
            return None
        ledger = json.loads(ledger_path.read_bytes())
        if zero_balance_problems(ledger):
            return None
        if funding_fingerprint(ledger) != receipt.get("funding_fingerprint"):
            return None
        basis = receipt.get("retained_funding_basis_usd")
        if not _finite(basis):
            return None
        return receipt
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def verified_archives():
    """{key: receipt} for every receipt that currently re-verifies."""
    out = {}
    for p in sorted(glob.glob(str(ARCHIVE_DIR / "*.json"))):
        key = os.path.splitext(os.path.basename(p))[0]
        r = verified_archive(key)
        if r:
            out[key] = r
    return out


def retained_basis_usd(exclude=()):
    return round(sum(float(r["retained_funding_basis_usd"])
                     for k, r in verified_archives().items() if k not in set(exclude)), 2)


def write_receipt(key, label, ledger_path, retained_basis, archived_copy, note=""):
    """Custodian-only. Refuses unless the ledger is a zero-balance book right now."""
    import datetime
    ledger_path = Path(ledger_path)
    if ledger_path.is_symlink():
        raise ValueError("ledger is a symlink")
    ledger = json.loads(ledger_path.read_bytes())
    bad = zero_balance_problems(ledger)
    if bad:
        raise ValueError("%s not zero-balance: %s" % (key, "; ".join(bad)))
    if not _finite(retained_basis):
        raise ValueError("retained basis not finite")
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    receipt = {
        "state": "ARCHIVED", "paper_only": True, "key": key, "label": label,
        "ledger_path": str(ledger_path), "archived_copy_path": str(archived_copy),
        "funding_fingerprint": funding_fingerprint(ledger),
        "retained_funding_basis_usd": round(float(retained_basis), 2),
        "archived_utc": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "authority": "TFO - HEAD TRADER decision 2026-09-25 18:23 AEST (Option A)",
        "method": "verified_zero_balance_archive_v1",
        "note": note,
    }
    tmp = _receipt_path(key).with_suffix(".tmp")
    tmp.write_text(json.dumps(receipt, indent=2))
    os.replace(tmp, _receipt_path(key))
    return receipt
