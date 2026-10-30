#!/usr/bin/env python3
"""Volume, candidate-shadow and attribution reporting for the Fundamental Desk.

PAPER ONLY.  This is an observation layer: it never changes a ledger, opens a
position, changes a rule, or treats volume/news as a substitute for valuation.
It closes two measurement gaps:

* every open position receives the lead-rule 10-session / 63-session volume
  reading in a standalone report; and
* completed READY batches keep a small shadow record of allocatable or
  undervalued-but-unfunded names, so later review can distinguish a missed edge
  from a correctly refused one.

The report uses settled daily bars only.  Missing or insufficient data is shown
as unavailable, never as a neutral signal.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

import yfinance as yf


BASE = Path(__file__).resolve().parent
REPORTS = BASE / "reports"
LEDGER = REPORTS / "fundamental_book.json"
OUT = REPORTS / "fundamental_observability.json"
HISTORY = REPORTS / "fundamental_volume_history.jsonl"
SHADOW = REPORTS / "fundamental_candidate_shadow.json"
READY_NAME = ".tfo_batch_ready.json"
RUN_ROOT = Path("/Users/test/prospect/var")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, fallback):
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return fallback


def series_for(symbol: str):
    """Return one settled daily history series, or an explicit reason.

    Per-symbol requests are deliberately used here.  The venues have dissimilar
    holidays and a partially empty multi-ticker frame is too easy to treat as a
    valid zero-volume record.  This task runs once daily, is bounded by the book
    size, and never trades from the result.
    """
    try:
        hist = yf.Ticker(symbol).history(period="4mo", interval="1d", auto_adjust=False)
    except Exception as exc:  # noqa: BLE001
        return None, "%s: %s" % (type(exc).__name__, str(exc)[:120])
    if hist is None or hist.empty or "Volume" not in hist:
        return None, "no daily volume history"
    vol = hist["Volume"].dropna()
    vol = vol[vol > 0]
    if len(vol) < 63:
        return None, "only %d settled positive-volume sessions; need 63" % len(vol)
    close = hist["Close"].dropna()
    return {"vol": vol, "close": close}, None


def volume_reading(symbol: str) -> dict:
    data, err = series_for(symbol)
    if err:
        return {"symbol": symbol, "available": False, "reason": err}
    vol = data["vol"]
    short = float(vol.tail(10).mean())
    long = float(vol.tail(63).mean())
    if long <= 0:
        return {"symbol": symbol, "available": False, "reason": "non-positive 63-session mean"}
    ratio = short / long
    state = "BUILDING" if ratio > 1.15 else "FADING" if ratio < 0.85 else "STEADY"
    last_close = None
    if not data["close"].empty:
        last_close = float(data["close"].iloc[-1])
    return {
        "symbol": symbol, "available": True,
        "ratio_10d_over_3m": round(ratio, 4), "state": state,
        "avg_volume_10d": round(short, 2), "avg_volume_3m": round(long, 2),
        "last_settled_close": last_close,
        "last_session": str(vol.index[-1].date()),
        "method": "10 settled sessions divided by 63 settled sessions; monitoring only",
    }


def ready_receipts() -> list[tuple[Path, dict]]:
    rows = []
    for path in sorted(RUN_ROOT.glob("*/" + READY_NAME)):
        receipt = read_json(path, None)
        if isinstance(receipt, dict) and receipt.get("status") == "READY":
            rows.append((path.parent, receipt))
    return rows


def shadow_candidates() -> list[dict]:
    """Candidate shadow universe from immutable READY receipts only."""
    out: dict[str, dict] = {}
    for run, receipt in ready_receipts():
        summary = ((receipt.get("aggregate") or {}).get("summary") or {})
        groups = [
            ("long_alloc", summary.get("long_alloc_symbols") or []),
            ("short_alloc", summary.get("short_alloc_symbols") or []),
            ("undervalued_unfunded", [x.get("symbol") for x in summary.get("undervalued_not_marked_long", []) if x.get("symbol")]),
        ]
        for group, symbols in groups:
            for symbol in symbols:
                if not symbol:
                    continue
                row = out.setdefault(symbol, {"symbol": symbol, "classes": [], "runs": []})
                if group not in row["classes"]:
                    row["classes"].append(group)
                if str(run) not in row["runs"]:
                    row["runs"].append(str(run))
    return sorted(out.values(), key=lambda x: x["symbol"])


def attribution(ledger: dict, signals: list[dict]) -> dict:
    positions = ledger.get("positions") or {}
    by_symbol = {s["symbol"]: s for s in signals}
    sides = {}
    dated = undated = 0
    for symbol, pos in positions.items():
        side = pos.get("side", "unknown")
        bucket = sides.setdefault(side, {"positions": 0, "notional_usd": 0.0, "unrealised_usd": 0.0,
                                         "building": 0, "steady": 0, "fading": 0, "unavailable": 0})
        bucket["positions"] += 1
        bucket["notional_usd"] += float(pos.get("notional_usd") or 0.0)
        bucket["unrealised_usd"] += float(pos.get("unrealised_usd") or 0.0)
        sig = by_symbol.get(symbol, {})
        key = (sig.get("state") or "unavailable").lower()
        bucket[key if key in bucket else "unavailable"] += 1
        if pos.get("catalyst_date"):
            dated += 1
        else:
            undated += 1
    for bucket in sides.values():
        bucket["notional_usd"] = round(bucket["notional_usd"], 2)
        bucket["unrealised_usd"] = round(bucket["unrealised_usd"], 2)
    return {
        "equity_usd": ledger.get("equity"), "cash_usd": ledger.get("cash"),
        "unrealised_usd": ledger.get("unrealised_usd"),
        "positions": len(positions), "dated_positions": dated, "undated_positions": undated,
        "by_side": sides,
        "reading": ("This is a forward-measurement dashboard, not evidence of alpha. "
                    "Volume never overrides the value, liquidity, or catalyst gates."),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    ledger = read_json(LEDGER, {})
    if not isinstance(ledger, dict) or not isinstance(ledger.get("positions"), dict):
        print("REFUSED: fundamental ledger unreadable", file=sys.stderr)
        return 2
    signals = [volume_reading(symbol) for symbol in sorted(ledger["positions"])]
    shadow = shadow_candidates()
    shadow_rows = []
    for row in shadow:
        sig = volume_reading(row["symbol"])
        shadow_rows.append({**row, "latest_signal": sig})
    report = {
        "generated_utc": utc_now(), "paper_only": True,
        "volume_method": "10 settled sessions / 63 settled sessions",
        "thresholds": {"building_gt": 1.15, "steady": "0.85 to 1.15 inclusive", "fading_lt": 0.85},
        "positions": signals,
        "attribution": attribution(ledger, signals),
        "candidate_shadow": shadow_rows,
        "candidate_shadow_note": ("Only immutable READY-batch candidates are tracked. A shadow result is a "
                                  "measurement of a decision, never permission to override a refusal."),
    }
    tmp = OUT.with_name(OUT.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, OUT)
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"generated_utc": report["generated_utc"], "paper_only": True,
                            "positions": signals}, sort_keys=True) + "\n")
    if not args.quiet:
        available = sum(1 for s in signals if s.get("available"))
        states = {k: sum(1 for s in signals if s.get("state") == k) for k in ("BUILDING", "STEADY", "FADING")}
        print("OBSERVABILITY — %d/%d volume readings | building %d steady %d fading %d | %d shadow candidate(s)"
              % (available, len(signals), states["BUILDING"], states["STEADY"],
                 states["FADING"], len(shadow_rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
