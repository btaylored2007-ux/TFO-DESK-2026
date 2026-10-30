#!/usr/bin/env python3
"""exposure_dial.py — the firm-level EXPOSURE DIAL, cap form only.

Registered 2026-08-15, spec hash 21e05244c53d4e77 (EXPOSURE_DIAL_REGISTRATION.md).
PAPER desk. Reads ledgers, writes ONLY its own two report files. It never trades,
never resizes a book, never levers up: scale = min(1, target/vol) by construction.

Uses book_coverage's canonical inventory and the same signed, equity-normalized
analytics as risk_engine. The legacy overlay inventory omitted newer books.

Heartbeat contract: desk_tick runs this every tick; if the current reading is
fresher than FRESH_SEC it exits immediately (cheap hourly no-op, real compute
once a day). `--force` recomputes unconditionally.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
import book_coverage
import risk_analytics
import risk_engine

REPORTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")

OUT = os.path.join(REPORTS, "exposure_dial.json")
HIST = os.path.join(REPORTS, "exposure_dial_history.jsonl")
SPEC_HASH = "21e05244c53d4e77"
TARGET = 0.10                 # 10%/yr firm vol target (frozen)
TRAIL = 63                    # trailing estimation window, trading days (frozen)
FRESH_SEC = 20 * 3600         # heartbeat guard
GATE_FLOOR = 0.80             # autonomous_deploy refuses below this (tighten-only)
MAPPED_FLOOR_PCT = 90.0       # below this mapped-capital share -> DEGRADED (fail closed)


def scale_for(vol_ann):
    """The registered cap form. Pure function; unit-tested."""
    if vol_ann is None or not np.isfinite(vol_ann) or vol_ann < 0:
        return 0.0                 # unknown risk must not manufacture FULL
    if vol_ann == 0:
        return 1.0                 # a measured flat/hedged book has zero price risk
    return float(min(1.0, TARGET / vol_ann))


def compute(coverage=None, raw=None, *, now=None, unclaimed=None, check_files=True):
    now = now or datetime.now(timezone.utc)
    coverage = coverage if coverage is not None else book_coverage.gather(verbose=False)
    if unclaimed is None:
        unclaimed, _ = book_coverage.coverage_audit(coverage["books"], verbose=False)
    if raw is None:
        raw, _ = risk_engine.fetch_price_panel(coverage["positions"])
    a = risk_analytics.analyze(coverage, raw, window=TRAIL, min_return_days=TRAIL,
                               now=now, unclaimed=unclaimed, check_files=check_files)
    stats = a["statistics"]
    vol_pct = stats.get("ann_vol_pct")
    measured_scale = scale_for(vol_pct / 100 if vol_pct is not None else None)
    degraded = not a["complete"] or a["mapped_capital_pct"] < MAPPED_FLOOR_PCT
    scale = 0.0 if degraded else measured_scale
    px = a["panel"]
    audusd = float(px["AUDUSD=X"].dropna().iloc[-1]) if "AUDUSD=X" in px and px["AUDUSD=X"].notna().any() else None
    return {
        "generated": now.isoformat(),
        "schema_version": 2,
        "spec_hash": SPEC_HASH,
        "form": "min(1, target/vol) — cap form, de-lever only",
        "trailing_days": TRAIL,
        "n_return_days": stats.get("sample_days", 0),
        "n_books": a["inventory"]["n_books"],
        "book_keys": sorted(b["key"] for b in coverage["books"]),
        "total_capital_usd": round(a["equity_usd"], 2) if a["equity_usd"] is not None else None,
        "denominator": "canonical current firm_equity_usd, including cash",
        "mapped_capital_pct": round(a["mapped_capital_pct"], 2),
        "position_history_coverage_pct": round(a["position_history_coverage_pct"], 2),
        "gross_exposure_usd": round(a["gross_usd"], 2),
        "unpriced_gross_usd": round(a["unpriced_gross_usd"], 2),
        "mapped_floor_pct": MAPPED_FLOOR_PCT,
        "audusd": audusd,
        "firm_vol_ann_pct": round(vol_pct, 2) if vol_pct is not None else None,
        "volatility_is_partial": degraded,
        "measured_subset_scale": round(measured_scale, 4),
        "target_vol_pct": TARGET * 100,
        "scale": round(scale, 4),
        "degraded": degraded,
        "missing_instruments": a["missing_instruments"],
        "unvaluable_positions": a["unvaluable_positions"],
        "degraded_reasons": a["errors"],
        "inventory": a["inventory"],
        "price_freshness": a["price_freshness"],
        "valuation": a["valuation"],
        "sample_start": a["sample_start"], "sample_end": a["sample_end"],
        "method": a["method"],
        "state": ("DEGRADED" if degraded else
                  "FULL" if scale >= 0.999 else "THROTTLE"),
        "gate_floor": GATE_FLOOR,
        "gate_open_for_new_probes": bool(scale >= GATE_FLOOR and not degraded),
    }


def write_report(d):
    """Publish this analytics snapshot atomically, then append its audit row."""
    os.makedirs(REPORTS, exist_ok=True)
    tmp = "%s.tmp.%d" % (OUT, os.getpid())
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2, allow_nan=False)
    os.replace(tmp, OUT)
    with open(HIST, "a") as f:
        f.write(json.dumps({"ts": d["generated"], "vol_pct": d["firm_vol_ann_pct"],
                            "scale": d["scale"], "state": d["state"],
                            "schema_version": d.get("schema_version"),
                            "denominator_usd": d.get("total_capital_usd"),
                            "n_books": d.get("n_books"),
                            "unpriced_gross_usd": d.get("unpriced_gross_usd"),
                            "gate_open": d["gate_open_for_new_probes"]}, allow_nan=False) + "\n")


def main():
    force = "--force" in sys.argv
    if not force and os.path.exists(OUT):
        age = time.time() - os.path.getmtime(OUT)
        if 0 <= age < FRESH_SEC:
            try:
                d = json.load(open(OUT))
                if d.get("schema_version") == 2:
                    print("exposure dial fresh (%.1fh old): scale %.2f · %s · firm vol %s%%"
                          % (age / 3600, d["scale"], d["state"], d["firm_vol_ann_pct"]))
                    return 0
            except Exception:
                pass  # unreadable -> recompute

    try:
        d = compute()
    except Exception as exc:
        # A recomputation failure must replace a previously-open fresh reading.
        d = {"generated": datetime.now(timezone.utc).isoformat(), "schema_version": 2,
             "spec_hash": SPEC_HASH, "scale": 0.0, "state": "DEGRADED",
             "degraded": True, "gate_open_for_new_probes": False,
             "gate_floor": GATE_FLOOR, "target_vol_pct": TARGET * 100,
             "firm_vol_ann_pct": None, "trailing_days": TRAIL, "n_books": 0,
             "total_capital_usd": None, "mapped_capital_pct": 0,
             "degraded_reasons": ["canonical analytics failed: %s" % type(exc).__name__]}
    write_report(d)
    print("EXPOSURE DIAL  scale %.2f · %s" % (d["scale"], d["state"]))
    print(f"  firm vol {d['firm_vol_ann_pct']}% ({d['trailing_days']}d trailing) vs "
          f"target {d['target_vol_pct']:.0f}% · {d['n_books']} books · "
          f"{d['total_capital_usd']} USD capital ({d['mapped_capital_pct']:.1f}% mapped)")
    print("  gate for new auto-probes: %s (floor %.2f)"
          % ("OPEN" if d["gate_open_for_new_probes"] else "CLOSED", GATE_FLOOR))
    print("  -> %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
