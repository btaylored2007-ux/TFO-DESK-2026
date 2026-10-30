#!/usr/bin/env python3
"""Observe future frozen-floor short outcomes versus cash. PAPER ONLY."""
import hashlib
import json
import math
import os
import time


BASE = os.path.dirname(os.path.abspath(__file__))
SOURCES = {
    "kraken_floor": "/Users/test/Applications/KrakenQuantDesk/kraken_bot/kraken_floor.json",
    "replica_floor": os.path.join(BASE, "replica_floor.json"),
}
STATE = os.path.join(BASE, "reports", "short_expression_shadow.json")


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _legacy_fingerprint(source, desk, ordinal, trade):
    payload = {"source": source, "desk": desk, "ordinal": ordinal,
               "trade": trade}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _fingerprint(source, desk, duplicate_ordinal, trade):
    """Content identity, stable when unrelated ledger rows move or are inserted."""
    payload = {"source": source, "desk": desk,
               "duplicate_ordinal": duplicate_ordinal, "trade": trade}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def read_trades(sources=SOURCES):
    out = []
    for source, path in sources.items():
        with open(path) as handle:
            payload = json.load(handle)
        desks = payload.get("desks")
        if not isinstance(desks, dict):
            raise ValueError("%s has no desks object" % source)
        for desk, ledger in desks.items():
            closed = ledger.get("closed_trades")
            if not isinstance(closed, list):
                raise ValueError("%s/%s has no closed_trades list" % (source, desk))
            duplicate_counts = {}
            for ordinal, trade in enumerate(closed):
                if not isinstance(trade, dict) or trade.get("side") not in ("long", "short"):
                    raise ValueError("%s/%s trade %d invalid" % (source, desk, ordinal))
                try:
                    pnl = float(trade["pnl"])
                except (KeyError, TypeError, ValueError):
                    raise ValueError("%s/%s trade %d has invalid pnl" %
                                     (source, desk, ordinal))
                if not math.isfinite(pnl):
                    raise ValueError("%s/%s trade %d has non-finite pnl" %
                                     (source, desk, ordinal))
                canonical_trade = json.dumps(trade, sort_keys=True, separators=(",", ":"))
                duplicate_ordinal = duplicate_counts.get(canonical_trade, 0)
                duplicate_counts[canonical_trade] = duplicate_ordinal + 1
                out.append({
                    "id": _fingerprint(source, desk, duplicate_ordinal, trade),
                    "legacy_id": _legacy_fingerprint(source, desk, ordinal, trade),
                    "identity_schema": 2,
                    "source": source, "desk": desk, "ordinal": ordinal,
                    "t": trade.get("t"), "symbol": trade.get("sym"),
                    "side": trade["side"], "actual_pnl_usd": pnl,
                    "cash_pnl_usd": 0.0,
                    "gross_pnl_usd": trade.get("gross_pnl"),
                    "costs_usd": trade.get("costs"),
                    "reason": trade.get("reason"),
                })
    return out


def _stats(records):
    for ordinal, record in enumerate(records):
        try:
            pnl = float(record["actual_pnl_usd"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("shadow record %d has invalid actual_pnl_usd" % ordinal)
        if not math.isfinite(pnl):
            raise ValueError("shadow record %d has non-finite actual_pnl_usd" % ordinal)
    shorts = [r for r in records if r["side"] == "short"]
    longs = [r for r in records if r["side"] == "long"]
    return {
        "n_all": len(records), "n_shorts": len(shorts), "n_longs": len(longs),
        "short_actual_pnl_usd": round(sum(r["actual_pnl_usd"] for r in shorts), 2),
        "short_cash_pnl_usd": 0.0,
        "cash_minus_short_actual_usd": round(-sum(
            r["actual_pnl_usd"] for r in shorts), 2),
        "long_actual_pnl_usd": round(sum(r["actual_pnl_usd"] for r in longs), 2),
    }


def run(sources=SOURCES, state_path=STATE):
    current = read_trades(sources)
    if os.path.exists(state_path):
        with open(state_path) as handle:
            state = json.load(handle)
        if not isinstance(state.get("pre_start_ids"), list) or not isinstance(
                state.get("forward"), list):
            raise ValueError("shadow state schema invalid")
    else:
        state = {
            "paper_only": True, "created_utc": _utc(),
            "identity_schema": 2,
            "evidence_bar": {"days": 182, "forward_closed_shorts": 100},
            "pre_start_ids": [r["id"] for r in current],
            "pre_start_context": _stats(current), "forward": [],
        }
    # One-time compatibility bridge for v1 ordinal IDs.  On the first unchanged
    # read after this deployment, mirror pre-start IDs and rewrite forward IDs
    # to stable v2 content identities before detecting additions.
    pre_start = set(state["pre_start_ids"])
    forward_by_id = {record["id"]: record for record in state["forward"]}
    migrated = 0
    for record in current:
        if record["legacy_id"] in pre_start and record["id"] not in pre_start:
            state["pre_start_ids"].append(record["id"])
            pre_start.add(record["id"])
            migrated += 1
        old_forward = forward_by_id.get(record["legacy_id"])
        if old_forward is not None and old_forward.get("id") != record["id"]:
            old_forward["legacy_id"] = old_forward["id"]
            old_forward["id"] = record["id"]
            old_forward["identity_schema"] = 2
            migrated += 1
    state["identity_schema"] = 2
    state["identity_migrations"] = int(state.get("identity_migrations") or 0) + migrated
    known = set(state["pre_start_ids"]) | {r["id"] for r in state["forward"]}
    additions = [r for r in current if r["id"] not in known]
    state["forward"].extend(additions)
    state["updated_utc"] = _utc()
    state["forward_stats"] = _stats(state["forward"])
    state["verdict"] = ("IMMATURE — observation only" if
                        state["forward_stats"]["n_shorts"] < 100 else
                        "EVIDENCE COUNT MET — statistical validation still required")
    tmp = state_path + ".tmp"
    with open(tmp, "w") as target:
        json.dump(state, target, indent=2)
    os.replace(tmp, state_path)
    return state, additions


def main():
    try:
        state, additions = run()
    except Exception as exc:                              # noqa: BLE001
        print("REFUSED: %s: %s" % (type(exc).__name__, exc))
        return 2
    print(json.dumps({"paper_only": True, "new_records": len(additions),
                      "forward": state["forward_stats"],
                      "verdict": state["verdict"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
