#!/usr/bin/env python3
"""Sector-specific value research adapters for the TFO Fundamental Desk.

PAPER RESEARCH ONLY.  These adapters prevent a generic ROE/margin screen from
turning sector accounting into a false signal.  They validate that a research
record contains the evidence needed for the selected sector method and append a
hash-addressed observation to an independent forward ledger.  They never assign
capital, change a live position, or reinterpret the frozen fundamental book.

The output ``RESEARCH_READY`` means only that a specialist may underwrite the
name.  It is deliberately not a trading verdict.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BASE = Path(__file__).resolve().parent
DEFAULT_LEDGER = BASE / "reports" / "sector_adapter_forward.jsonl"


@dataclass(frozen=True)
class Adapter:
    adapter_id: str
    required_metrics: tuple[str, ...]
    forbidden_primary_metrics: tuple[str, ...]
    method: str
    requires_regime_history: bool = False


ADAPTERS: dict[str, Adapter] = {
    "insurer_v1": Adapter(
        "insurer_v1",
        ("tangible_common_equity", "price", "shares_outstanding",
         "combined_ratio_ex_prior_year_development", "catastrophe_normalised_result",
         "reserve_development_history"),
        ("provider_normalized_income",),
        "P/TBV against full-cycle history plus underwriting ex reserve development and catastrophe normalisation.",
        True,
    ),
    "bank_v1": Adapter(
        "bank_v1",
        ("tangible_common_equity", "price", "shares_outstanding", "credit_cost",
         "nonperforming_assets", "net_interest_margin", "local_sovereign_hurdle"),
        ("generic_enterprise_value", "provider_normalized_income"),
        "P/TCE and earnings after through-cycle credit costs, compared with the local sovereign hurdle.",
        True,
    ),
    "investment_entity_v1": Adapter(
        "investment_entity_v1",
        ("tested_nav", "price", "cash_distributions", "disposal_realisations",
         "lookthrough_debt", "nav_discount"),
        ("ifrs_fvtpl_revenue", "ifrs_fvtpl_roe", "provider_normalized_income"),
        "Cash distributions and realised disposal values against independently tested NAV and debt.",
        True,
    ),
    "regulated_utility_v1": Adapter(
        "regulated_utility_v1",
        ("regulatory_earnings", "cash_realisations", "allowed_return_normalised",
         "achieved_return_or_mechanism", "rate_base", "price"),
        ("ifrs_contract_asset_revenue", "provider_normalized_income", "raw_cross_border_allowed_return"),
        "Regulatory earnings/cash realisation and tax/inflation-normalised allowed returns.",
        True,
    ),
    "holdco_dualclass_v1": Adapter(
        "holdco_dualclass_v1",
        ("taxed_sotp", "net_debt", "price", "shares_outstanding", "entitlement",
         "control_rights", "own_history_discount"),
        ("untaxed_sotp",),
        "Taxed sum-of-parts with exact economic entitlement, control rights and own-history discount.",
        False,
    ),
    "resources_v1": Adapter(
        "resources_v1",
        ("reserve_life", "realised_price", "hedge_book", "aisc_or_c1",
         "midcycle_price", "midcycle_cashflow", "price"),
        ("spot_only_cashflow", "provider_normalized_income"),
        "Reserve life, realised prices, hedges and AISC/C1 under a mandatory mid-cycle commodity stress.",
        True,
    ),
}


def _iso_date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


N_A_LIKE = frozenset({
    "-", "--", "—", "n/a", "na", "n.a.", "none", "null", "nil",
    "unknown", "unavailable", "not available", "not applicable",
    "not disclosed", "not reported", "not provided", "pending",
})


def _substantive_scalar(value: Any) -> bool:
    if value is None or isinstance(value, (bool, dict, list, tuple, set)):
        return False
    if _finite(value):
        return True
    if isinstance(value, str):
        normalised = " ".join(value.strip().lower().split())
        return bool(normalised) and normalised not in N_A_LIKE
    return False


def _usable_metric_value(value: Any) -> bool:
    """Accept finite scalars or substantive text/lists, never opaque objects."""
    if isinstance(value, list):
        return bool(value) and any(_substantive_scalar(item) for item in value)
    return _substantive_scalar(value)


def _primary_metric(record: dict, name: str, as_of: dt.date | None) -> tuple[bool, str | None]:
    metric = (record.get("metrics") or {}).get(name)
    if not isinstance(metric, dict):
        return False, "missing metric %s" % name
    if not _usable_metric_value(metric.get("value")):
        return False, "metric %s has no usable value" % name
    if not str(metric.get("source_url") or "").startswith(("http://", "https://")):
        return False, "metric %s lacks a primary-source URL" % name
    filed_date = _iso_date(metric.get("filed_date"))
    if filed_date is None:
        return False, "metric %s lacks a filed_date" % name
    if as_of is not None and filed_date > as_of:
        return False, "metric %s was filed after as_of_date" % name
    return True, None


def evaluate(record: dict) -> dict:
    """Validate one specialist research record without making a trade verdict."""
    reasons: list[str] = []
    adapter_id = str(record.get("adapter_id") or "")
    adapter = ADAPTERS.get(adapter_id)
    if adapter is None:
        reasons.append("unknown adapter_id")

    stable_id = str(record.get("stable_issuer_id") or "").strip()
    symbol = str(record.get("symbol") or "").strip()
    if not stable_id:
        reasons.append("stable_issuer_id is required")
    if not symbol:
        reasons.append("symbol is required")
    as_of = _iso_date(record.get("as_of_date"))
    if as_of is None:
        reasons.append("as_of_date must be ISO")

    catalyst = record.get("catalyst") or {}
    event_date = _iso_date(catalyst.get("event_date"))
    review_date = _iso_date(catalyst.get("review_date"))
    if event_date is None or (as_of is not None and event_date <= as_of):
        reasons.append("catalyst event_date must be sourced and future as of the observation")
    if review_date is None or (as_of is not None and review_date <= as_of):
        reasons.append("catalyst review_date must be sourced and future")
    if catalyst.get("direction") not in ("bullish", "bearish"):
        reasons.append("catalyst direction must be bullish or bearish")
    if catalyst.get("status") != "alive":
        reasons.append("catalyst must be walked forward and marked alive")
    if not str(catalyst.get("source_url") or "").startswith(("http://", "https://")):
        reasons.append("catalyst lacks a primary-source URL")

    valuation = record.get("valuation") or {}
    pct = valuation.get("own_history_percentile")
    if not _finite(pct) or not 0 <= float(pct) <= 100:
        reasons.append("valuation own_history_percentile must be between 0 and 100")
    history = record.get("history") or {}
    years = history.get("years_span")
    n_obs = history.get("n_observations")
    full_cycle = history.get("full_cycle")
    if not _finite(years) or not _finite(n_obs) or float(n_obs) < 20:
        reasons.append("history must contain at least 20 observations and a measured span")
    elif float(years) <= 0:
        reasons.append("history years_span must be positive")
    elif full_cycle is not None and not isinstance(full_cycle, bool):
        reasons.append("history full_cycle must be a boolean when supplied")
    elif full_cycle is True and float(years) < 3:
        reasons.append("history full_cycle is impossible for a span shorter than 3 years")
    elif float(years) < 10 and full_cycle is not True:
        reasons.append("history shorter than 10 years must explicitly span a full cycle")

    if adapter is not None:
        for name in adapter.required_metrics:
            ok, reason = _primary_metric(record, name, as_of)
            if not ok and reason:
                reasons.append(reason)
        primary_metric = str(valuation.get("primary_metric") or "")
        if primary_metric in adapter.forbidden_primary_metrics:
            reasons.append("%s is forbidden as the primary metric for %s" % (primary_metric, adapter_id))
        if primary_metric not in adapter.required_metrics:
            reasons.append("primary_metric must be one of the adapter's sourced required metrics")
        if adapter.requires_regime_history:
            rp = valuation.get("current_regime_percentile")
            if not _finite(rp) or not 0 <= float(rp) <= 100:
                reasons.append("current_regime_percentile is required for this cycle-sensitive adapter")

    digest = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "schema": 1,
        "paper_only": True,
        "observation_sha256": digest,
        "stable_issuer_id": stable_id or None,
        "symbol": symbol or None,
        "adapter_id": adapter_id or None,
        "status": "RESEARCH_READY" if not reasons else "INSUFFICIENT_EVIDENCE",
        "capital_eligible": False,
        "reasons": reasons,
        "method": adapter.method if adapter else None,
        "warning": "Research readiness is not undervaluation, alpha validation, or permission to open a position.",
    }


def append_observation(result: dict, ledger: Path = DEFAULT_LEDGER) -> bool:
    """Append one hash-addressed result exactly once under an exclusive lock."""
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a+", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.seek(0)
        prior = {json.loads(line).get("observation_sha256") for line in f if line.strip()}
        if result["observation_sha256"] in prior:
            return False
        row = dict(result)
        row["recorded_utc"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        f.seek(0, os.SEEK_END)
        f.write(json.dumps(row, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, help="normalised specialist research JSON")
    ap.add_argument("--output")
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    args = ap.parse_args()
    try:
        record = json.loads(Path(args.input).read_text(encoding="utf-8"))
        result = evaluate(record)
        result["appended"] = append_observation(result, Path(args.ledger))
        text = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.output:
            out = Path(args.output)
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(out.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, out)
        print(text, end="")
        return 0 if result["status"] == "RESEARCH_READY" else 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("REFUSED: %s" % exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
