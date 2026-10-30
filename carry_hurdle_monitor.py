#!/usr/bin/env python3
"""Read-only carry hurdle, decay, and opportunity-cost control.

Reads the sealed carry registration, active executor constants, current carry
snapshot/history, and paper book.  It never imports or runs the executor and
never writes outside ``reports``.  The output distinguishes the frozen exit
rule from economic attractiveness: a position may correctly remain open under
the registered trailing-carry exit while failing to beat the 4% cash benchmark.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import re

BASE = pathlib.Path(__file__).resolve().parent
BOOK = BASE / "reports/carry_paper_book.json"
SNAP = BASE / "reports/carry_monitor.json"
HISTORY = BASE / "reports/carry_history.jsonl"
EXECUTOR = BASE / "carry_paper_desk.py"
MONITOR = BASE / "carry_monitor.py"
REG_V2 = BASE / "CARRY_V2_PREREGISTRATION_DRAFT.md"
REG_NEW = BASE / "CARRY_NEWNAMES_PROBE_PREREGISTRATION.md"
OUT = BASE / "reports/carry_hurdle_monitor.json"
CORRECTED_UNITS_FROM = dt.datetime(2026, 9, 4, tzinfo=dt.timezone.utc).timestamp()


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assigned_number(text: str, name: str) -> float:
    match = re.search(r"^%s\s*=\s*([0-9.]+)" % re.escape(name), text, re.MULTILINE)
    if not match:
        raise ValueError("cannot find %s" % name)
    return float(match.group(1))


def history_rows(path: pathlib.Path = HISTORY) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        if row.get("t") is not None and row.get("preferred_mean") is not None:
            rows.append(row)
    return sorted(rows, key=lambda row: row["t"])


def trailing_mean(rows: list[dict], now: float, days: float) -> tuple[float | None, int]:
    values = [
        float(row["preferred_mean"])
        for row in rows
        if float(row["t"]) >= now - days * 86400
    ]
    return (sum(values) / len(values), len(values)) if values else (None, 0)


def run() -> dict:
    book = json.loads(BOOK.read_text())
    snapshot = json.loads(SNAP.read_text())
    rows = history_rows()
    if not rows:
        raise RuntimeError("carry history is empty")
    as_of = float(rows[-1]["t"])
    executor_text = EXECUTOR.read_text()
    cost_side_bps = assigned_number(executor_text, "COST_BPS_PER_SIDE")
    executor_asset_gate_pct = assigned_number(executor_text, "ENTRY_THRESHOLD_PCT")
    exit_days = assigned_number(executor_text, "EXIT_TRAILING_DAYS")
    positions = book.get("positions", {})
    notional = sum(float(position.get("notional_aud", 0)) for position in positions.values())
    entry_cost_recorded = sum(float(position.get("entry_cost_aud", 0)) for position in positions.values())
    funding = sum(float(position.get("funding_accrued_aud", 0)) for position in positions.values())
    opened = min((float(position["opened_epoch"]) for position in positions.values()), default=as_of)
    elapsed_days = max(0.0, (as_of - opened) / 86400.0)

    registered_cash_pct = 4.0
    monitor_harvest_gate_pct = float(snapshot["harvest_threshold_pct"])
    expected_basket_gate = assigned_number(MONITOR.read_text(), "HARVEST_THRESHOLD_PCT")
    historical_unreconciled = any(
        float(position.get("opened_epoch", 0)) < CORRECTED_UNITS_FROM
        for position in positions.values()
    )
    experiment_days = 182.0
    current_rate = float(snapshot.get("preferred_mean_annualised_pct") or 0.0)
    entry_weighted_rate = (
        sum(
            float(position.get("entry_carry_pct", 0)) * float(position.get("notional_aud", 0))
            for position in positions.values()
        ) / notional
        if notional else 0.0
    )

    exit_cost_current = notional * cost_side_bps / 10000.0
    honest_full_lifecycle_cost = notional * 2 * cost_side_bps / 10000.0
    current_rate_gross_income = notional * current_rate / 100.0
    cash_income = notional * registered_cash_pct / 100.0
    actual_accrual_ann_pct = (
        funding / notional * 365.0 / elapsed_days * 100.0
        if notional and elapsed_days > 0 else None
    )
    cash_opportunity_to_date = notional * registered_cash_pct / 100.0 * elapsed_days / 365.0
    accounting_net_if_exit = funding - entry_cost_recorded - exit_cost_current
    economic_surplus_if_exit = accounting_net_if_exit - cash_opportunity_to_date
    cost_only_shortfall = max(0.0, entry_cost_recorded + exit_cost_current - funding)
    daily_current_gross = notional * current_rate / 100.0 / 365.0 if notional else 0.0
    days_to_cost_break_even = cost_only_shortfall / daily_current_gross if daily_current_gross > 0 else None
    remaining_days = max(0.0, experiment_days - elapsed_days)
    full_horizon_cash = notional * registered_cash_pct / 100.0 * experiment_days / 365.0
    remaining_needed = honest_full_lifecycle_cost + full_horizon_cash - funding
    required_remaining_ann_pct = (
        remaining_needed / (notional * remaining_days / 365.0) * 100.0
        if notional and remaining_days > 0 else None
    )

    trailing_1d, observations_1d = trailing_mean(rows, as_of, 1)
    trailing_3d, observations_3d = trailing_mean(rows, as_of, 3)
    trailing_7d, observations_7d = trailing_mean(rows, as_of, exit_days)
    monitor_maker_roundtrip_bps = float(snapshot.get("cost_bps_maker", 0.0))
    executor_roundtrip_bps = 2 * cost_side_bps
    rate_spread_to_cash = current_rate - registered_cash_pct

    report = {
        "control": "carry_hurdle_decay_opportunity_cost_monitor",
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "as_of_history_utc": dt.datetime.fromtimestamp(as_of, dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "paper_only": True,
        "read_only": True,
        "sources": {
            "book": str(BOOK),
            "snapshot": str(SNAP),
            "history": str(HISTORY),
            "executor": str(EXECUTOR),
            "sealed_v2_registration": str(REG_V2),
            "sealed_newnames_registration": str(REG_NEW),
            "hashes_sha256": {
                "executor": sha256(EXECUTOR),
                "v2_registration": sha256(REG_V2),
                "newnames_registration": sha256(REG_NEW),
            },
        },
        "registered_rules_observed": {
            "cash_benchmark_pct": registered_cash_pct,
            "original_v2_asset_gate_in_understated_units_pct": 2.0,
            "prospective_asset_gate_corrected_units_pct": executor_asset_gate_pct,
            "expected_monitor_basket_gate_pct": expected_basket_gate,
            "monitor_snapshot_harvest_gate_pct": monitor_harvest_gate_pct,
            "executor_per_asset_gate_pct": executor_asset_gate_pct,
            "exit_trailing_days": exit_days,
            "exit_trigger_pct": 0.0,
            "evidence_horizon_days": experiment_days,
            "current_executor_cost_bps_per_side": cost_side_bps,
        },
        "current_book": {
            "historical_funding_reconciliation": "REQUIRED" if historical_unreconciled else "NOT_FLAGGED",
            "economic_verdict_available": not historical_unreconciled,
            "note": "Recorded ledger diagnostics only; missing historical settlements are not estimated or retroactively multiplied. Latest rate is a scenario, not earned yield.",
            "positions": sorted(positions),
            "notional_aud": notional,
            "elapsed_days": round(elapsed_days, 4),
            "funding_accrued_aud": funding,
            "actual_recorded_accrual_annualised_pct": (
                round(actual_accrual_ann_pct, 4) if actual_accrual_ann_pct is not None else None
            ),
            "recorded_entry_cost_aud": entry_cost_recorded,
            "recorded_entry_cost_bps": (
                round(entry_cost_recorded / notional * 10000, 4) if notional else None
            ),
            "expected_exit_cost_at_current_executor_aud": round(exit_cost_current, 4),
            "accounting_net_if_exit_now_aud": round(accounting_net_if_exit, 4),
        },
        "decay": {
            "entry_comparison_available": not historical_unreconciled,
            "entry_weighted_carry_pct": round(entry_weighted_rate, 4),
            "current_preferred_carry_pct": current_rate,
            "current_decay_from_entry_pp": None if historical_unreconciled else round(current_rate - entry_weighted_rate, 4),
            "current_decay_from_entry_pct": (
                round((current_rate / entry_weighted_rate - 1) * 100, 2)
                if entry_weighted_rate and not historical_unreconciled else None
            ),
            "trailing_1d_mean_pct": round(trailing_1d, 4) if trailing_1d is not None else None,
            "trailing_1d_observations": observations_1d,
            "trailing_3d_mean_pct": round(trailing_3d, 4) if trailing_3d is not None else None,
            "trailing_3d_observations": observations_3d,
            "trailing_7d_mean_pct": round(trailing_7d, 4) if trailing_7d is not None else None,
            "trailing_7d_observations": observations_7d,
            "registered_exit_fires_now": bool(
                trailing_7d is not None and observations_7d >= 6 and trailing_7d < 0
            ),
        },
        "hurdle_and_opportunity_cost": {
            "cash_benchmark_income_aud_per_year": round(cash_income, 4),
            "gross_income_at_current_rate_aud_per_year": round(current_rate_gross_income, 4),
            "current_rate_minus_cash_pp": round(rate_spread_to_cash, 4),
            "cash_opportunity_cost_to_date_aud": round(cash_opportunity_to_date, 4),
            "economic_surplus_if_exit_now_aud": round(economic_surplus_if_exit, 4),
            "cost_only_shortfall_aud": round(cost_only_shortfall, 4),
            "days_to_cover_recorded_costs_at_current_rate": (
                round(days_to_cost_break_even, 2) if days_to_cost_break_even is not None else None
            ),
            "honest_full_lifecycle_cost_at_current_executor_aud": round(honest_full_lifecycle_cost, 4),
            "remaining_days_in_182d_evidence_horizon": round(remaining_days, 4),
            "required_average_carry_over_remaining_horizon_pct": (
                round(required_remaining_ann_pct, 4) if required_remaining_ann_pct is not None else None
            ),
            "current_rate_clears_required_remaining_hurdle": bool(
                required_remaining_ann_pct is not None and current_rate >= required_remaining_ann_pct
            ),
        },
        "control_flags": {
            "current_carry_below_cash": current_rate < registered_cash_pct,
            "current_basket_below_new_entry_gate": current_rate < expected_basket_gate,
            "rule_holds_but_economics_negative": bool(
                not historical_unreconciled and trailing_7d is not None and trailing_7d >= 0 and economic_surplus_if_exit < 0
            ),
            "monitor_entry_gate_metadata_stale": not math.isclose(
                monitor_harvest_gate_pct, expected_basket_gate
            ),
            "monitor_cost_metadata_stale": not math.isclose(
                monitor_maker_roundtrip_bps, executor_roundtrip_bps
            ),
            "monitor_advertised_maker_roundtrip_bps": monitor_maker_roundtrip_bps,
            "executor_current_maker_roundtrip_bps": executor_roundtrip_bps,
            "book_entry_cost_differs_from_current_executor": bool(
                notional and not math.isclose(entry_cost_recorded / notional * 10000, cost_side_bps)
            ),
        },
        "status": (
            "HISTORICAL FUNDING UNRECONCILED — economic verdict unavailable; current-rate scenarios are not earned yield"
            if historical_unreconciled else
            "CARRY EXIT RULE HOLDS, BUT NET RECORDED ECONOMICS TRAIL CASH — no rule or position changed"
            if trailing_7d is not None and trailing_7d >= 0 and economic_surplus_if_exit < 0 else
            "CARRY ECONOMIC HURDLE MET" if economic_surplus_if_exit >= 0 else
            "CARRY MONITOR INDETERMINATE"
        ),
        "book_action": "NONE — this control is observational and cannot override the sealed exit rule",
    }
    temporary = OUT.with_name(OUT.name + ".%d.tmp" % os.getpid())
    with temporary.open("w") as stream:
        stream.write(json.dumps(report, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, OUT)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    del args
    report = run()
    print(report["status"])


if __name__ == "__main__":
    main()
