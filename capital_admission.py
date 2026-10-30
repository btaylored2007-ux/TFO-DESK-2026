"""Evidence gate for NEW paper capital, authorised 2026-09-10.

Existing bounded experiments retain their capital. A green P&L number is not
authority to top up a book. Fresh deployment requires all independent evidence
checks, an unambiguous root evaluation and the existing risk/capital caps.
"""
import math
import hashlib
from pathlib import Path

REQUIRED_CHECKS = (
    "preregistered", "untouched_holdout", "purged_embargo_cv", "liquidity",
    "decay", "execution_delay", "independent_bets", "costs_measured",
    "point_in_time_data", "mechanism_distinct",
)


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def evaluate_admission(receipt):
    reasons = []
    if not isinstance(receipt, dict):
        return False, ["receipt must be an object"]
    evidence = receipt.get("capital_admission")
    if (not isinstance(evidence, dict) or type(evidence.get("schema_version")) is not int
            or evidence.get("schema_version") != 1):
        return False, ["missing capital_admission schema v1; no increase to unvalidated books"]
    if evidence.get("paper_only") is not True:
        reasons.append("PAPER-only evidence required")
    checks = evidence.get("checks") or {}
    if not isinstance(checks, dict):
        checks = {}
    for name in REQUIRED_CHECKS:
        record = checks.get(name)
        if not isinstance(record, dict) or record.get("passed") is not True:
            reasons.append("evidence check failed or missing: " + name)
        elif not isinstance(record.get("evidence"), str) or not record["evidence"].strip():
            reasons.append("evidence reference missing: " + name)
    ev = receipt.get("evaluation")
    if not isinstance(ev, dict):
        return False, reasons + ["root evaluation missing or ambiguous"]
    for name in ("t_stat", "n_effective", "per_trade_bps", "deflated_sharpe", "n_trials_searched"):
        if not finite_number(ev.get(name)):
            reasons.append("non-finite evaluation field: " + name)
    if finite_number(ev.get("t_stat")) and ev["t_stat"] < 2:
        reasons.append("positive t_stat must be >= 2")
    if finite_number(ev.get("n_effective")) and ev["n_effective"] < 20:
        reasons.append("n_effective must be >= 20")
    if finite_number(ev.get("per_trade_bps")) and ev["per_trade_bps"] <= 0:
        reasons.append("net return must clear costs")
    if finite_number(ev.get("deflated_sharpe")) and not 0.95 < ev["deflated_sharpe"] <= 1:
        reasons.append("deflated Sharpe must exceed .95")
    if finite_number(ev.get("n_trials_searched")) and ev["n_trials_searched"] < 1:
        reasons.append("search count must be positive")
    if ev.get("survives_deflation") is not True:
        reasons.append("deflation did not pass")
    if ev.get("beats_benchmark") is not True:
        reasons.append("benchmark-adjusted evidence did not pass")
    beta = receipt.get("beta_to_SPY")
    if not finite_number(beta) or abs(beta) > .30:
        reasons.append("finite |beta_to_SPY| <= .30 required")
    return not reasons, reasons


def verify_artifacts(receipt, base):
    """Bind attestations to existing desk evidence; not a substitute for replication.

    No network, no code execution. Every required check names a local evidence
    artifact and its SHA256; unknown or modified artifacts cannot release capital.
    """
    reasons = []
    root = Path(base).resolve()
    checks = receipt.get("capital_admission", {}).get("checks", {})
    checked = {}
    for name in REQUIRED_CHECKS:
        record = checks.get(name) or {}
        try:
            path = Path(record["evidence"])
            path = (path if path.is_absolute() else root / path).resolve()
            if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
                raise ValueError("not a bounded local desk artifact")
            if path not in checked:
                checked[path] = hashlib.sha256(path.read_bytes()).hexdigest()
            if record.get("sha256") != checked[path]:
                raise ValueError("missing or changed SHA256")
        except (KeyError, TypeError, ValueError, OSError) as exc:
            reasons.append("unverified evidence %s: %s" % (name, exc))
    return not reasons, reasons
