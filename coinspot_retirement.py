"""Read-only retirement proof. An instruction alone cannot hide funded holdings."""
import hashlib
import json
import math
import threading
from pathlib import Path

BASE = Path(__file__).resolve().parent
KRAKEN = Path("/Users/test/Applications/KrakenQuantDesk")


# 2026-10-21: the receipt's archived_ledger_path lives under ~/Documents (iCloud/
# FileProvider). open() on it blocked forever in the kernel, which hung book_coverage
# -> firm_risk/risk_engine/tfo_daily_pnl on every tick and froze firm_risk.json.
# Prefer a desk-local mirror (same bytes; still checked against final_ledger_sha256 and
# the canonical ledger), and never wait more than ARCHIVE_READ_TIMEOUT_S on the original.
LOCAL_ARCHIVE = "reports/coinspot_final_archive.json"
ARCHIVE_READ_TIMEOUT_S = 5.0


def _read_bytes_bounded(path, timeout=None):
    timeout = ARCHIVE_READ_TIMEOUT_S if timeout is None else timeout
    box = {}
    def worker():
        try:
            box["raw"] = Path(path).read_bytes()
        except BaseException as e:   # noqa: BLE001 - re-raised below as OSError
            box["err"] = e
    t = threading.Thread(target=worker, daemon=True)
    t.start(); t.join(timeout)
    if t.is_alive():
        raise OSError("archive read timed out after %.0fs: %s" % (timeout, path))
    if "err" in box:
        raise OSError(str(box["err"]))
    return box["raw"]


def verified_retirement(base=BASE, kraken=KRAKEN):
    base, kraken = Path(base), Path(kraken)
    try:
        receipt = json.loads((base / "reports/coinspot_retirement.json").read_bytes())
        if (receipt.get("state") != "RETIRED" or receipt.get("paper_only") is not True
                or receipt.get("positions_closed") is not True
                or receipt.get("historical_pnl_verified") is not False):
            return None
        ledger_path = kraken / "reports/coinspot_floor.json"
        archive = Path(receipt["archived_ledger_path"])
        warehouse = base / "reports/gap_close_levered_book.json"
        if ledger_path.is_symlink() or archive.is_symlink():
            return None
        if str(warehouse) != receipt["warehouse_path"]:
            return None
        raw = ledger_path.read_bytes()
        if (hashlib.sha256(raw).hexdigest() != receipt["final_ledger_sha256"]
                or _archive_bytes(base, archive) != raw):
            return None
        ledger = json.loads(raw)
        if not ledger.get("desks") or ledger.get("positions") or ledger.get("holdings"):
            return None
        if float(ledger.get("cash", 0)) != 0:
            return None
        for desk in ledger["desks"].values():
            if desk.get("positions") or float(desk["cash"]) != 0:
                return None
        amount = receipt["transfer_usd"]
        if isinstance(amount, bool) or not math.isfinite(amount) or amount <= 0:
            return None
        native = receipt["transfer_aud"]
        if isinstance(native, bool) or not math.isfinite(native) or native <= 0:
            return None
        basis = ledger["start_cash"]
        if isinstance(basis, bool) or not math.isfinite(basis):
            return None
        original = ledger["original_start_cash"]
        if (isinstance(original, bool) or not math.isfinite(original)
                or abs(original - native - basis) > .005):
            return None
        utc = receipt["transfer_utc"]
        if not isinstance(utc, str) or not utc:
            return None
        def matches(event, sign):
            value = event.get("usd")
            return (event.get("type") == "capital_transfer"
                    and event.get("from") == "coinspot_floor"
                    and event.get("to") == "gap_close_levered_book"
                    and event.get("utc") == utc
                    and isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(value) and abs(value - sign * amount) < .005)
        inbound = json.loads(warehouse.read_bytes())
        if (sum(matches(e, -1) for e in ledger.get("capital_events", [])) != 1
                or sum(matches(e, 1) for e in inbound.get("capital_events", [])) != 1):
            return None
        outgoing = next(e for e in ledger["capital_events"] if matches(e, -1))
        if abs(float(outgoing["aud"]) + native) > .005:
            return None
        out = dict(receipt, retained_funding_basis_usd=basis * amount / native)
        local = base / LOCAL_ARCHIVE
        if local.exists() and not local.is_symlink():
            # readers (private_wealth_monitor ledger_history) open this path: give them the
            # verified desk-local copy, never the iCloud original that can block forever.
            out["archived_ledger_path_original"] = receipt["archived_ledger_path"]
            out["archived_ledger_path"] = str(local)
        return out
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _archive_bytes(base, archive):
    local = Path(base) / LOCAL_ARCHIVE
    if local.exists() and not local.is_symlink():
        return local.read_bytes()
    return _read_bytes_bounded(archive)
