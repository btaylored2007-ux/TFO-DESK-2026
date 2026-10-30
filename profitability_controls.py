#!/usr/bin/env python3
"""Read-only profitability controls for the Taylor Family Office PAPER desk.

This program never imports or calls a trading engine.  It reads the existing
ledgers and book-coverage registry, constructs two independent shadow views, and
writes reports only:

* fully-loaded NAV: entry costs, liquidation reserve, financing/borrow and a
  simple equity-to-recorded-capital diagnostic;
* capital shadow: dead-capital-inclusive versus active-only capital ratios,
  evidence-status capital, book concentration and fundamental sector exposure.

The reports are measurements.  They do not resize, close, open, freeze or
otherwise mutate any book.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping


BASE = Path("/Users/test/Applications/MultiVenueDesk")
REPORTS = BASE / "reports"
FUNDAMENTAL_LEDGER = REPORTS / "fundamental_book.json"
SPEC_LEDGER = REPORTS / "fundamental_spec_book.json"
TOURNAMENT_LEDGER = REPORTS / "tournament.json"
NAV_REPORT = REPORTS / "fully_loaded_nav.json"
CAPITAL_REPORT = REPORTS / "capital_concentration_shadow.json"

# Explicitly terminated experiments stay in the all-capital denominator.  They
# are excluded only from the separately labelled active-only diagnostic.
DEAD_BOOK_KEYS = frozenset({"punt_24h"})
ZERO_CAPITAL_SHADOW_KEYS = frozenset({"floor_exit_shadow"})
RETURN_BASIS_NOTE = (
    "Simple equity / recorded capital - 1 diagnostic; not inception return, "
    "time-weighted return (TWR), money-weighted return, or a full cash-flow-adjusted "
    "performance audit. Timing of external funding and historical capital changes "
    "is not reconstructed. Legacy *_return_pct keys retain this diagnostic meaning."
)

# This is the current main-book entry cap and its provenance.  The monitor does
# not enforce or modify it; it independently reports existing breaches.
MAIN_SECTOR_CAP_PCT = 0.20
MAIN_SECTOR_CAP_SOURCE = (
    "fundamental_open_new.py SECTOR_CAP_PCT and "
    "FUNDAMENTAL_ENTRY_SAFETY_PREREGISTRATION_2026-09-02.md"
)


class MonitorError(RuntimeError):
    """A missing or inconsistent input that makes the measurement unsafe."""


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _number(value: Any, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MonitorError(f"{field} is missing or non-numeric")
    out = float(value)
    if not math.isfinite(out):
        raise MonitorError(f"{field} is not finite")
    if positive and out <= 0:
        raise MonitorError(f"{field} must be positive")
    return out


def _load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open() as handle:
            value = json.load(handle)
    except Exception as exc:  # fail-loud boundary
        raise MonitorError(f"cannot read {path}: {type(exc).__name__}: {exc}") from exc
    if not isinstance(value, dict):
        raise MonitorError(f"{path} is not a JSON object")
    return value


def _fingerprint(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    stat = path.stat()
    return {
        "path": str(path),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "mtime_utc": dt.datetime.fromtimestamp(
            stat.st_mtime, dt.timezone.utc
        ).isoformat().replace("+00:00", "Z"),
    }


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write a report atomically.  Input ledgers are never opened for writing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _all_positions(ledger: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any], bool]]:
    rows: list[tuple[str, Mapping[str, Any], bool]] = []
    for closed, key in ((False, "positions"), (True, "closed")):
        group = ledger.get(key) or {}
        if not isinstance(group, Mapping):
            raise MonitorError(f"fundamental ledger `{key}` is not an object")
        for symbol, position in group.items():
            if not isinstance(position, Mapping):
                raise MonitorError(f"position {symbol} in `{key}` is not an object")
            if closed and symbol == 'SOXL_2026-09-18T20:13:57Z' and 'entry_px_usd' not in position:
                position = _restore_soxl_cost_schema(position)
            rows.append((str(symbol), position, closed))
    return rows


def _restore_soxl_cost_schema(position):
    """Recover reporting inputs only from the exact pre-close archival record."""
    path = REPORTS / 'revisions/rollback/FUND-SOXL-REBANK-20260919-061357.json'
    expected = '3483bb9603ba9edc63699bd0423a129b68a56cee9899e4f420fd618a32c6c1b9'
    try:
        payload = path.read_bytes()
        if path.is_symlink() or hashlib.sha256(payload).hexdigest() != expected:
            raise MonitorError('SOXL historical cost evidence hash mismatch')
        archived = json.loads(payload)['positions']['SOXL']
        if (position.get('sym') != 'SOXL' or position.get('utc') != '2026-09-18T20:13:57Z'
                or position.get('units') != archived['units']
                or position.get('entry') != archived['entry_px_usd']
                or archived.get('entry_ccy') != 'USD' or archived.get('entry_fx_usd') != 1):
            raise MonitorError('SOXL historical cost evidence does not match closure')
        # Orig units are for the original entry-cost model, never closure quantity.
        return {**position, 'entry_px_usd': archived['entry_px_usd'],
                'exit_state': archived['exit_state'],
                '_cost_schema_evidence': {'path': str(path), 'sha256': expected,
                    'scope': 'Original entry-cost basis only; no P&L or ledger repair'}}
    except (OSError, KeyError, ValueError) as exc:
        raise MonitorError('SOXL historical cost evidence unavailable: ' + str(exc)) from exc


def _original_units(position: Mapping[str, Any], symbol: str) -> float:
    state = position.get("exit_state") or {}
    if isinstance(state, Mapping) and isinstance(state.get("orig_units"), (int, float)):
        return _number(state["orig_units"], f"{symbol}.exit_state.orig_units", positive=True)

    current = float(position.get("units") or 0.0)
    exited = 0.0
    for fill in position.get("exit_fills") or []:
        if isinstance(fill, Mapping):
            # Engine exit fills record sold size as `units`; Head Trader tickets
            # (tfo_ticket.py / tfo_rotation.py, 2026-09-25+) record it as `qty`.
            # Use `units` when present, else `qty`; never add both.
            sold = fill.get("units") if fill.get("units") is not None else fill.get("qty")
            exited += float(sold or 0.0)
    if current + exited > 0:
        return current + exited

    notional = float(position.get("notional_usd") or 0.0)
    entry = float(position.get("entry_px_usd") or 0.0)
    if notional > 0 and entry > 0:
        return notional / entry
    raise MonitorError(f"cannot reconstruct original units for {symbol}")


ENTRY_FEE_TOLERANCE_USD = 0.02   # recorded fees are cents-rounded; the model is not


def _recorded_entry_fee(position: Mapping[str, Any]) -> tuple[float | None, str]:
    """The entry fee this position says it paid, and the field it came from.

    `entry_fees_usd` is the V1 field (desk_costs, written by tfo_buy / tfo_crypto /
    fundamental_open_new / convexity_autotrade and the V1_FEE_BACKFILL_20260926
    backfill).  `entry_cost_usd` is the older engine name.  A compact archival
    record (e.g. the 2026-09-18 SOXL close) carries one combined `fees_usd` from
    the backfill that covers entry AND exit; it counts as the entry record.
    """
    for key in ("entry_fees_usd", "entry_cost_usd"):
        value = position.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value), key
    fees = position.get("fees_usd")
    if (isinstance(fees, (int, float)) and not isinstance(fees, bool)
            and isinstance(position.get("fee_backfill"), Mapping) and not position.get("exit_fills")):
        return float(fees), "fees_usd (combined entry+exit archival record)"
    return None, "none"


def _entry_fee_model(symbol: str, position: Mapping[str, Any], orig_units: float) -> float:
    """Entry fee under the desk's ONE cost model (desk_costs.fill_fees, V1 and V2)."""
    import desk_costs  # pure schedule: no ledger, no network

    sym = str(position.get("sym") or symbol.split("_")[0])
    when_s = position.get("entry_utc") or position.get("utc")
    if not isinstance(when_s, str):
        raise MonitorError(f"{symbol} has no entry_utc; the fee schedule is date-aware")
    try:
        when = dt.datetime.fromisoformat(when_s.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MonitorError(f"{symbol}.entry_utc is not ISO-8601: {when_s}") from exc
    side = desk_costs.side_for("BUY", position.get("side") or "long")
    native = position.get("entry_px")
    native = native if isinstance(native, (int, float)) and not isinstance(native, bool) else None
    try:
        return float(desk_costs.fill_fees(sym, side, orig_units, position["entry_px_usd"], native,
                                          position.get("entry_ccy"), when)["fees_usd"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MonitorError(f"cannot model entry fee for {symbol}: {exc}") from exc


def build_fully_loaded_nav(ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild the main fundamental book on a liquidation-cost basis.

    Financing already debited from cash is disclosed, not subtracted again.
    Released borrow is added back only where the historical fill fields show it
    was included in realised P&L after the same financing had already been
    debited through `costs_paid_usd`.
    """
    reported_equity = _number(ledger.get("equity"), "equity")
    contributed = _number(
        ledger.get("contributed_capital_usd", ledger.get("seed_usd")),
        "contributed_capital_usd/seed_usd",
        positive=True,
    )
    rows = _all_positions(ledger)
    if not rows:
        raise MonitorError("fundamental ledger has no open or closed positions")

    rule = ledger.get("trade_rule") or {}
    if not isinstance(rule, Mapping):
        raise MonitorError("trade_rule is not an object")
    entry_bps = _number(rule.get("entry_cost_bps"), "trade_rule.entry_cost_bps")
    exit_bps = _number(rule.get("exit_cost_bps"), "trade_rule.exit_cost_bps")
    slippage_bps = _number(
        rule.get("slippage_bps_per_side"), "trade_rule.slippage_bps_per_side"
    )

    original_basis = 0.0
    modelled_entry_fees = 0.0
    recorded_entry_fees = 0.0
    entry_shortfalls: list[dict[str, Any]] = []
    entry_shortfall_raw = 0.0
    recorded_sources: set[str] = set()
    open_marked_gross = 0.0
    stale_symbols: list[str] = []
    released_borrow = 0.0
    original_basis_by_symbol: dict[str, float] = {}
    for symbol, position, closed in rows:
        entry_px = _number(position.get("entry_px_usd"), f"{symbol}.entry_px_usd", positive=True)
        orig_units = _original_units(position, symbol)
        basis = orig_units * entry_px
        original_basis += basis
        original_basis_by_symbol[symbol] = basis
        # Per trade, never netted: one trade's stamp duty must not hide another's missing fee.
        model_fee = _entry_fee_model(symbol, position, orig_units)
        recorded_fee, source = _recorded_entry_fee(position)
        modelled_entry_fees += model_fee
        if recorded_fee is not None:
            recorded_entry_fees += recorded_fee
            recorded_sources.add(source)
        short = model_fee - (recorded_fee or 0.0)
        if recorded_fee is None or short > ENTRY_FEE_TOLERANCE_USD:
            entry_shortfall_raw += max(0.0, short)
            entry_shortfalls.append({"symbol": symbol, "modelled_usd": round(model_fee, 2),
                                     "recorded_usd": recorded_fee, "recorded_source": source,
                                     "shortfall_usd": round(max(0.0, short), 2)})
        released_borrow += max(0.0, float(position.get("borrow_released_usd") or 0.0)
                               - float(position.get('borrow_released_cash_correct_usd') or 0.0))
        if not closed:
            units = _number(position.get("units"), f"{symbol}.units")
            mark = _number(position.get("mark_usd"), f"{symbol}.mark_usd", positive=True)
            open_marked_gross += abs(units * mark)
            if position.get("mark_stale"):
                stale_symbols.append(symbol)

    # Entry costs are measured against desk_costs (the desk's ONE fill-cost model,
    # which every V1 entry path debits).  The trade_rule bps figure is kept only as
    # a disclosed benchmark: it is the engine's gate/exit assumption, not a fee
    # schedule, and "expected under a different model" is not "not debited".
    bps_benchmark = original_basis * (entry_bps + slippage_bps) / 10_000.0
    expected_entry_cost = modelled_entry_fees
    explicit = ledger.get("entry_costs_paid_usd")
    if not recorded_sources and isinstance(explicit, (int, float)) and not isinstance(explicit, bool):
        # Legacy aggregate-only ledgers: no per-trade record to test.
        recorded_entry_cost, recorded_cost_source = float(explicit), "ledger.entry_costs_paid_usd"
        entry_cost_true_up = max(0.0, expected_entry_cost - recorded_entry_cost)
        entry_shortfalls = []
    else:
        recorded_entry_cost = recorded_entry_fees
        recorded_cost_source = ("per-position " + " | ".join(sorted(recorded_sources))
                                if recorded_sources else "no entry-cost debit field found")
        entry_cost_true_up = entry_shortfall_raw
    # A recorded fee is only a debit if it reached cash.  Fundamental books debit
    # every fee through costs_paid_usd (equity identity), so recorded entry fees
    # can never exceed it.
    costs_paid = float(ledger.get("costs_paid_usd") or 0.0)
    fees_outside_cash = max(0.0, recorded_entry_cost - costs_paid) if recorded_sources else 0.0
    exit_cost_reserve = open_marked_gross * (exit_bps + slippage_bps) / 10_000.0

    financing_charged = float(
        ledger.get("borrow_accrued_total_usd", ledger.get("costs_paid_usd", 0.0)) or 0.0
    )
    financing_in_costs = float(ledger.get("costs_paid_usd") or 0.0)
    open_financing = sum(
        float(position.get("borrow_accrued_usd") or 0.0)
        for _, position, closed in rows
        if not closed
    )

    # Current fill schema subtracts borrow from realised and includes realised in
    # short cash_delta, after borrow was already debited daily from cash.  Keep
    # this correction explicit and removable rather than silently rewriting the
    # ledger.  A future schema can set financing_cash_debited_once=true to say
    # the duplication has been corrected at source.
    duplicate_borrow_correction = 0.0
    if not ledger.get("financing_cash_debited_once"):
        duplicate_borrow_correction = released_borrow

    bridge = {
        "entry_cost_true_up_usd": round(-entry_cost_true_up, 2) + 0.0,  # never -0.0
        "exit_cost_reserve_usd": round(-exit_cost_reserve, 2),
        "released_borrow_double_charge_correction_usd": round(
            duplicate_borrow_correction, 2
        ),
    }
    adjustment = sum(bridge.values())
    fully_loaded_nav = reported_equity + adjustment

    identity = ledger.get("equity_identity_usd")
    identity_gap = (
        reported_equity - float(identity) if isinstance(identity, (int, float)) else None
    )
    alerts: list[dict[str, Any]] = []
    if entry_cost_true_up > 0.01:
        alerts.append(
            {
                "code": "ENTRY_COST_NOT_DEBITED",
                "severity": "ATTENTION",
                "amount_usd": round(entry_cost_true_up, 2),
                "message": "Modelled entry cost exceeds recorded entry-cost debits.",
                "symbols": [r["symbol"] for r in entry_shortfalls],
            }
        )
    if fees_outside_cash > 0.01:
        alerts.append(
            {
                "code": "ENTRY_FEES_NOT_IN_CASH",
                "severity": "ATTENTION",
                "amount_usd": round(fees_outside_cash, 2),
                "message": "Recorded entry fees exceed costs_paid_usd, so some were never debited from cash.",
            }
        )
    if duplicate_borrow_correction > 0.01:
        alerts.append(
            {
                "code": "RELEASED_BORROW_DOUBLE_CHARGE",
                "severity": "ATTENTION",
                "amount_usd": round(duplicate_borrow_correction, 2),
                "message": (
                    "Released borrow appears in realised/cash after already being "
                    "debited through daily financing; shadow NAV adds it back once."
                ),
            }
        )
    if stale_symbols:
        alerts.append(
            {
                "code": "STALE_MARK_RESERVE",
                "severity": "ATTENTION",
                "symbols": sorted(stale_symbols),
                "message": "Exit reserve uses last-good marks for these positions.",
            }
        )
    if identity_gap is not None and abs(identity_gap) > 1.0:
        alerts.append(
            {
                "code": "REPORTED_IDENTITY_GAP",
                "severity": "PROBLEM",
                "amount_usd": round(identity_gap, 2),
                "message": "Reported equity differs from the ledger's own capital identity by more than $1.",
            }
        )

    status = "PROBLEM" if any(a["severity"] == "PROBLEM" for a in alerts) else (
        "ATTENTION" if alerts else "OK"
    )
    return {
        "status": status,
        "paper_only": True,
        "read_only_shadow": True,
        "return_basis_note": RETURN_BASIS_NOTE,
        "reported_equity_usd": round(reported_equity, 2),
        "contributed_capital_usd": round(contributed, 2),
        "reported_pnl_usd": round(reported_equity - contributed, 2),
        "reported_return_pct": round((reported_equity / contributed - 1.0) * 100.0, 4),
        "fully_loaded_liquidation_nav_usd": round(fully_loaded_nav, 2),
        "fully_loaded_pnl_usd": round(fully_loaded_nav - contributed, 2),
        "fully_loaded_return_pct": round((fully_loaded_nav / contributed - 1.0) * 100.0, 4),
        "adjustment_from_reported_usd": round(adjustment, 2),
        "adjustment_bridge": bridge,
        "cost_evidence": {
            "historical_schema_evidence": [p['_cost_schema_evidence'] for _, p, _ in rows
                                            if '_cost_schema_evidence' in p],
            "entry_cost_bps_including_slippage": entry_bps + slippage_bps,
            "exit_cost_bps_including_slippage": exit_bps + slippage_bps,
            "original_entry_basis_usd": round(original_basis, 2),
            "expected_entry_cost_usd": round(expected_entry_cost, 2),
            "expected_entry_cost_model": "desk_costs.fill_fees per trade (entry side, original units, entry date)",
            "recorded_entry_cost_usd": round(recorded_entry_cost, 2),
            "recorded_entry_cost_source": recorded_cost_source,
            "entry_cost_shortfalls": entry_shortfalls,
            "costs_paid_usd": round(costs_paid, 4),
            "recorded_entry_fees_outside_cash_usd": round(fees_outside_cash, 2),
            "trade_rule_bps_entry_benchmark_usd": round(bps_benchmark, 2),
            "trade_rule_bps_benchmark_note": (
                "trade_rule entry+slippage bps on original basis; disclosed benchmark only, "
                "not the desk fee schedule and not used for the entry true-up"),
            "open_marked_gross_usd": round(open_marked_gross, 2),
            "exit_cost_reserve_usd": round(exit_cost_reserve, 2),
        },
        "financing_evidence": {
            "borrow_financing_charged_usd": round(financing_charged, 4),
            "financing_in_costs_paid_usd": round(financing_in_costs, 4),
            "open_borrow_allocated_usd": round(open_financing, 4),
            "released_borrow_allocated_usd": round(released_borrow, 4),
            "modelled_not_quoted": True,
        },
        "mark_quality": {
            "open_positions": sum(1 for _, _, closed in rows if not closed),
            "closed_positions": sum(1 for _, _, closed in rows if closed),
            "stale_mark_count": len(stale_symbols),
            "stale_symbols": sorted(stale_symbols),
        },
        "identity_gap_usd": round(identity_gap, 2) if identity_gap is not None else None,
        "alerts": alerts,
        "method": (
            "reported equity - omitted entry cost - current liquidation cost reserve "
            "+ released-borrow duplicate-debit correction; financing already in cash "
            "is disclosed and never subtracted again"
        ),
    }


def _tournament_roles(tournament: Mapping[str, Any]) -> dict[str, str]:
    roles: dict[str, str] = {}
    for group in ("competitors", "controls", "discretionary"):
        for row in tournament.get(group) or []:
            if isinstance(row, Mapping) and row.get("name"):
                roles[f"tour_{row['name']}"] = str(row.get("role") or group.rstrip("s"))
    return roles


def _evidence_class(key: str, tournament_roles: Mapping[str, str]) -> tuple[str, str]:
    if key in DEAD_BOOK_KEYS:
        return "terminated", "terminated book retained in all-capital diagnostic"
    if key == "carry_paper_book":
        return "unvalidated", (
            "bounded delta-neutral PAPER experiment; corrected hourly units and "
            "current 32% basket / 16% asset admission rules have no validated "
            "full-cost edge; historical funding/cost reconciliation remains outstanding"
        )
    if key in ZERO_CAPITAL_SHADOW_KEYS:
        return "measurement_shadow", "registered mirrored measurement; zero funded capital and zero firm equity"
    if key in {"maker_calibration_probe", "carry_newnames_probe"}:
        return "measurement_probe", "calibration/probe capital; not an alpha claim"
    role = tournament_roles.get(key)
    if role == "control":
        return "control", "registered tournament control; not an alpha claim"
    if role:
        return "unvalidated", f"tournament role={role}; evidence clock immature"
    return "unvalidated", "no validated after-cost edge classification"


def _sector_shadow(
    ledger: Mapping[str, Any],
    *,
    book_key: str,
    sector_cap_pct: float | None,
    require_sector: bool,
) -> dict[str, Any]:
    seed = _number(ledger.get("seed_usd"), f"{book_key}.seed_usd", positive=True)
    positions = ledger.get("positions") or {}
    if not isinstance(positions, Mapping):
        raise MonitorError(f"{book_key}.positions is not an object")
    sectors: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"entry_notional_usd": 0.0, "marked_gross_usd": 0.0, "positions": 0}
    )
    names: list[dict[str, Any]] = []
    missing: list[str] = []
    for symbol, position in positions.items():
        if not isinstance(position, Mapping):
            raise MonitorError(f"{book_key}.{symbol} is not an object")
        sector = str(position.get("sector") or "UNCLASSIFIED").strip().lower()
        if sector == "unclassified":
            missing.append(str(symbol))
        notional = abs(_number(position.get("notional_usd"), f"{book_key}.{symbol}.notional_usd"))
        units = abs(_number(position.get("units"), f"{book_key}.{symbol}.units"))
        mark = _number(position.get("mark_usd"), f"{book_key}.{symbol}.mark_usd", positive=True)
        marked = units * mark
        sectors[sector]["entry_notional_usd"] += notional
        sectors[sector]["marked_gross_usd"] += marked
        sectors[sector]["positions"] += 1
        names.append(
            {
                "symbol": str(symbol),
                "sector": sector,
                "side": position.get("side"),
                "entry_notional_usd": round(notional, 2),
                "marked_gross_usd": round(marked, 2),
                "pct_seed": round(notional / seed * 100.0, 4),
            }
        )
    cap_usd = seed * sector_cap_pct if sector_cap_pct is not None else None
    sector_rows = []
    for sector, values in sectors.items():
        notional = float(values["entry_notional_usd"])
        breach = cap_usd is not None and notional > cap_usd + 0.01
        sector_rows.append(
            {
                "sector": sector,
                "positions": int(values["positions"]),
                "entry_notional_usd": round(notional, 2),
                "marked_gross_usd": round(float(values["marked_gross_usd"]), 2),
                "pct_seed": round(notional / seed * 100.0, 4),
                "cap_usd": round(cap_usd, 2) if cap_usd is not None else None,
                "breach_usd": round(max(0.0, notional - cap_usd), 2)
                if cap_usd is not None
                else None,
                "breach": breach,
            }
        )
    sector_rows.sort(key=lambda row: row["entry_notional_usd"], reverse=True)
    names.sort(key=lambda row: row["entry_notional_usd"], reverse=True)
    return {
        "book_key": book_key,
        "seed_usd": round(seed, 2),
        "sector_cap_pct": sector_cap_pct * 100.0 if sector_cap_pct is not None else None,
        "sector_cap_source": MAIN_SECTOR_CAP_SOURCE if sector_cap_pct is not None else None,
        "sector_rows": sector_rows,
        "sector_breaches": [row for row in sector_rows if row["breach"]],
        "sector_coverage": {
            "positions": len(positions),
            "classified": len(positions) - len(missing),
            "unclassified": len(missing),
            "unclassified_symbols": sorted(missing),
            "complete": not missing,
            "required": require_sector,
        },
        "top_names": names[:10],
    }


def build_capital_shadow(
    registry_rows: Iterable[Mapping[str, Any]],
    nav: Mapping[str, Any],
    main_ledger: Mapping[str, Any],
    spec_ledger: Mapping[str, Any],
    tournament: Mapping[str, Any],
) -> dict[str, Any]:
    roles = _tournament_roles(tournament)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in registry_rows:
        key = str(raw.get("key") or "")
        if not key or key in seen:
            raise MonitorError(f"registry contains missing or duplicate key: {key!r}")
        seen.add(key)
        if raw.get("error"):
            raise MonitorError(f"registry book {key} is unreadable: {raw['error']}")
        start = _number(raw.get("start_usd"), f"registry.{key}.start_usd")
        equity = _number(raw.get("equity_usd"), f"registry.{key}.equity_usd")
        if key in ZERO_CAPITAL_SHADOW_KEYS:
            if start != 0 or equity != 0 or raw.get("kind") != "shadow":
                raise MonitorError(f"registered zero-capital shadow {key} must have kind=shadow, start=0 and equity=0")
        elif start <= 0 and raw.get('net_funding_basis_verified') is not True:
            raise MonitorError(f"registry.{key}.start_usd must be positive")
        evidence, why = _evidence_class(key, roles)
        rows.append(
            {
                "key": key,
                "label": raw.get("label") or key,
                "start_usd": start,
                "reported_equity_usd": equity,
                "reported_pnl_usd": equity - start,
                "evidence_class": evidence,
                "evidence_reason": why,
                "dead_capital": key in DEAD_BOOK_KEYS,
                "zero_capital_shadow": key in ZERO_CAPITAL_SHADOW_KEYS,
                "net_funding_basis_verified": raw.get('net_funding_basis_verified') is True,
                "stale": bool(raw.get("stale")) and not (
                    raw.get('positions') == [] and raw.get('gross_usd', 0) == 0
                    and raw.get('cash_usd') == equity),
                "ledger_timestamp_stale": bool(raw.get('stale')),
            }
        )
    if "fundamental_book" not in seen:
        raise MonitorError("registry does not contain fundamental_book")

    total_start = sum(row["start_usd"] for row in rows)
    total_reported = sum(row["reported_equity_usd"] for row in rows)
    nav_delta = _number(nav.get("adjustment_from_reported_usd"), "nav.adjustment_from_reported_usd")
    total_fully_loaded = total_reported + nav_delta
    dead_start = sum(row["start_usd"] for row in rows if row["dead_capital"])
    dead_equity = sum(row["reported_equity_usd"] for row in rows if row["dead_capital"])
    active_start = total_start - dead_start
    active_reported = total_reported - dead_equity
    active_fully_loaded = total_fully_loaded - dead_equity
    if active_start <= 0:
        raise MonitorError("active-capital denominator is non-positive")

    for row in rows:
        equity = row["reported_equity_usd"]
        if row["key"] == "fundamental_book":
            equity += nav_delta
        row["fully_loaded_equity_usd"] = round(equity, 2)
        row["pct_firm_fully_loaded_nav"] = round(equity / total_fully_loaded * 100.0, 4)
        row["reported_pnl_usd"] = round(row["reported_pnl_usd"], 2)
        row["start_usd"] = round(row["start_usd"], 2)
        row["reported_equity_usd"] = round(row["reported_equity_usd"], 2)
    rows.sort(key=lambda row: row["fully_loaded_equity_usd"], reverse=True)

    classes: dict[str, dict[str, Any]] = {}
    for row in rows:
        group = classes.setdefault(
            row["evidence_class"],
            {"start_usd": 0.0, "fully_loaded_equity_usd": 0.0, "books": 0, "book_keys": []},
        )
        group["start_usd"] += row["start_usd"]
        group["fully_loaded_equity_usd"] += row["fully_loaded_equity_usd"]
        group["books"] += 1
        group["book_keys"].append(row["key"])
    for group in classes.values():
        group["start_usd"] = round(group["start_usd"], 2)
        group["fully_loaded_equity_usd"] = round(group["fully_loaded_equity_usd"], 2)
        group["pct_firm_fully_loaded_nav"] = round(
            group["fully_loaded_equity_usd"] / total_fully_loaded * 100.0, 4
        )
        group["book_keys"].sort()

    main_sector = _sector_shadow(
        main_ledger,
        book_key="fundamental_book",
        sector_cap_pct=MAIN_SECTOR_CAP_PCT,
        require_sector=True,
    )
    spec_sector = _sector_shadow(
        spec_ledger,
        book_key="fundamental_spec_book",
        sector_cap_pct=None,
        require_sector=False,
    )

    unvalidated_equity = float((classes.get("unvalidated") or {}).get("fully_loaded_equity_usd", 0.0))
    fundamental_family = sum(
        row["fully_loaded_equity_usd"]
        for row in rows
        if row["key"] in {"fundamental_book", "fundamental_spec_book"}
    )
    alerts: list[dict[str, Any]] = []
    if dead_start:
        alerts.append(
            {
                "code": "DEAD_CAPITAL_RETAINED",
                "severity": "ATTENTION",
                "capital_usd": round(dead_start, 2),
                "loss_usd": round(dead_equity - dead_start, 2),
                "message": "Terminated-book loss remains in the all-capital diagnostic to prevent survivor bias.",
            }
        )
    for breach in main_sector["sector_breaches"]:
        alerts.append(
            {
                "code": "MAIN_SECTOR_CAP_BREACH",
                "severity": "ATTENTION",
                "sector": breach["sector"],
                "breach_usd": breach["breach_usd"],
                "message": "Existing exposure exceeds the current entry cap; monitor does not trade or resize it.",
            }
        )
    if spec_sector["sector_coverage"]["unclassified"]:
        alerts.append(
            {
                "code": "SPEC_SECTOR_UNCLASSIFIED",
                "severity": "ATTENTION",
                "positions": spec_sector["sector_coverage"]["unclassified"],
                "message": "Speculative sleeve lacks sector fields; its sector concentration is unmeasured.",
            }
        )
    stale = [row["key"] for row in rows if row["stale"]]
    if stale:
        alerts.append(
            {
                "code": "STALE_BOOKS",
                "severity": "PROBLEM",
                "book_keys": stale,
                "message": "One or more registry books are stale; firm concentration is not current.",
            }
        )

    status = "PROBLEM" if any(a["severity"] == "PROBLEM" for a in alerts) else (
        "ATTENTION" if alerts else "OK"
    )
    return {
        "status": status,
        "paper_only": True,
        "read_only_shadow": True,
        "return_basis_note": RETURN_BASIS_NOTE,
        "nav_scope_note": (
            "Firm diagnostic adds the main fundamental book's cost-reserve bridge "
            "to other books' recorded equity; it is not a complete firm-wide "
            "liquidation-cost or cash-flow audit."
        ),
        "capital_denominators": {
            "all_capital": {
                "contributed_capital_usd": round(total_start, 2),
                "reported_equity_usd": round(total_reported, 2),
                "fully_loaded_equity_usd": round(total_fully_loaded, 2),
                "reported_return_pct": round((total_reported / total_start - 1.0) * 100.0, 4),
                "fully_loaded_return_pct": round((total_fully_loaded / total_start - 1.0) * 100.0, 4),
                "return_basis_note": RETURN_BASIS_NOTE,
                "treatment": "includes terminated books and their losses; simple unadjusted capital diagnostic",
            },
            "active_only_diagnostic": {
                "contributed_capital_usd": round(active_start, 2),
                "reported_equity_usd": round(active_reported, 2),
                "fully_loaded_equity_usd": round(active_fully_loaded, 2),
                "reported_return_pct": round((active_reported / active_start - 1.0) * 100.0, 4),
                "fully_loaded_return_pct": round((active_fully_loaded / active_start - 1.0) * 100.0, 4),
                "return_basis_note": RETURN_BASIS_NOTE,
                "treatment": "excludes explicitly terminated books; simple unadjusted capital diagnostic, never inception performance",
            },
            "dead_capital": {
                "contributed_capital_usd": round(dead_start, 2),
                "equity_usd": round(dead_equity, 2),
                "pnl_usd": round(dead_equity - dead_start, 2),
                "book_keys": sorted(DEAD_BOOK_KEYS & seen),
            },
        },
        "evidence_capital": classes,
        "unvalidated_capital": {
            "fully_loaded_equity_usd": round(unvalidated_equity, 2),
            "pct_firm_fully_loaded_nav": round(unvalidated_equity / total_fully_loaded * 100.0, 4),
            "definition": "capital in books with no validated after-cost edge; controls, terminated books, measurement probes and zero-capital shadows shown separately",
        },
        "fundamental_family_concentration": {
            "fully_loaded_equity_usd": round(fundamental_family, 2),
            "pct_firm_fully_loaded_nav": round(fundamental_family / total_fully_loaded * 100.0, 4),
            "book_keys": ["fundamental_book", "fundamental_spec_book"],
            "registered_firm_cap": None,
            "interpretation": "shadow observation, not an allocation veto",
        },
        "sector_concentration": {
            "main": main_sector,
            "speculative": spec_sector,
            "note": "main sector cap is checked on remaining entry notional; marked gross is shown separately",
        },
        "books": rows,
        "alerts": alerts,
        "policy": (
            "report only: no allocation, entry, exit, freeze, restart or registration mutation is permitted"
        ),
    }


def _load_registry() -> list[Mapping[str, Any]]:
    # book_coverage is a read-only adapter over ledgers.  Importing it does not
    # tick any book; registry() only reads current state and public FX adapters.
    sys.path.insert(0, str(BASE))
    try:
        import book_coverage  # type: ignore

        rows = book_coverage.registry()
    except Exception as exc:
        raise MonitorError(f"book coverage registry failed: {type(exc).__name__}: {exc}") from exc
    if not isinstance(rows, list) or not rows:
        raise MonitorError("book coverage registry returned no books")
    import firm_capital_flows
    risk = _load_json(REPORTS / 'firm_risk.json')
    risk_time = dt.datetime.fromisoformat(risk['utc'].replace('Z', '+00:00'))
    fresh = 0 <= (dt.datetime.now(dt.timezone.utc) - risk_time).total_seconds() < 1200
    flow = firm_capital_flows.capital_event(risk, rows)
    if fresh and risk.get('capital_accounting_valid') is True and flow['capital_accounting_valid']:
        for row in rows:
            row['net_funding_basis_verified'] = True
    # Internal retirement transfers retain the source's residual funding basis.
    from coinspot_retirement import verified_retirement
    retirement = verified_retirement(BASE)
    if retirement:
        rows.append({'key': 'coinspot_retired_funding', 'label': 'CoinSpot retired funding basis',
                     'start_usd': retirement['retained_funding_basis_usd'], 'equity_usd': 0,
                     'net_funding_basis_verified': True, 'stale': False})
    import book_archive
    present = {r.get('key') for r in rows}
    for key, arc in book_archive.verified_archives().items():
        if key not in present:
            rows.append({'key': key + '_archived_funding', 'label': '%s archived funding basis' % arc.get('label', key),
                         'start_usd': arc['retained_funding_basis_usd'], 'equity_usd': 0,
                         'net_funding_basis_verified': True, 'stale': False})
    return rows


def run(nav_path: Path = NAV_REPORT, capital_path: Path = CAPITAL_REPORT) -> tuple[dict[str, Any], dict[str, Any]]:
    main = _load_json(FUNDAMENTAL_LEDGER)
    spec = _load_json(SPEC_LEDGER)
    tournament = _load_json(TOURNAMENT_LEDGER)
    registry_rows = _load_registry()
    generated = _utc_now()

    nav = build_fully_loaded_nav(main)
    nav["generated_utc"] = generated
    nav["input"] = _fingerprint(FUNDAMENTAL_LEDGER)
    nav["output_path"] = str(nav_path)

    capital = build_capital_shadow(registry_rows, nav, main, spec, tournament)
    capital["generated_utc"] = generated
    capital["inputs"] = [
        _fingerprint(FUNDAMENTAL_LEDGER),
        _fingerprint(SPEC_LEDGER),
        _fingerprint(TOURNAMENT_LEDGER),
    ]
    capital["output_path"] = str(capital_path)

    _atomic_json(nav_path, nav)
    _atomic_json(capital_path, capital)
    return nav, capital


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nav-output", type=Path, default=NAV_REPORT)
    parser.add_argument("--capital-output", type=Path, default=CAPITAL_REPORT)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="return 2 when a completed report has PROBLEM status",
    )
    args = parser.parse_args(argv)
    try:
        nav, capital = run(args.nav_output, args.capital_output)
    except MonitorError as exc:
        print(f"PROBLEM profitability controls: {exc}", file=sys.stderr)
        return 2
    print(
        "profitability controls: "
        f"NAV {nav['status']} reported ${nav['reported_equity_usd']:,.2f} -> "
        f"fully loaded ${nav['fully_loaded_liquidation_nav_usd']:,.2f}; "
        f"capital {capital['status']} simple equity/capital diagnostic "
        f"{capital['capital_denominators']['all_capital']['fully_loaded_return_pct']:.4f}% "
        "(unadjusted for flow timing; not inception/TWR)"
    )
    print(f"  {args.nav_output}")
    print(f"  {args.capital_output}")
    if args.strict and (nav["status"] == "PROBLEM" or capital["status"] == "PROBLEM"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
