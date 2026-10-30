#!/usr/bin/env python3
"""Carry paper desk — bounded, unvalidated delta-neutral PAPER experiment.

WHAT THIS IS
------------
The carry monitor watches funding and says HARVESTABLE when delta-neutral basis
carry (long spot X / short perp X) rises above the 32%/yr gross basket threshold. This module is
the next step: when that happens, it OPENS A TRACKED PAPER POSITION automatically,
accrues funding on it every tick, watches the margin-stress condition, and closes
it when the carry regime dies. The desk is therefore positioned the moment the
regime turns, not after someone reads about it.

PAPER ONLY. This writes rows to a local ledger. It places no orders anywhere,
holds no credentials, and its only inputs are the desk's own collected funding
data. The A$ figures are simulated.

MECHANICS MODELLED
------------------
  entry/exit cost   25 bps per side, modelled maker; execution is not guaranteed
  funding accrual   latest observed annualised rate x notional x elapsed years;
                    historical pre-CM071 underaccrual requires reconciliation
  basis P&L         ~0 by construction (coin-for-coin hedge) — NOT modelled
  margin stress     perp mark rising vs entry squeezes the short leg's margin:
                      +20%  -> MARGIN STRESS flag (top-up would be required)
                      +30%  -> LIQUIDATION RISK event logged (sizing rule broken)

SIZING (from the research file, not invented here)
--------------------------------------------------
A$50,000 book supports ~A$33,000 notional after the margin haircut, split equally
across qualifying assets (BTC/ETH only — the liquid names whose basis held through
the 2025-10-11 cascade). Never more than 2 assets, never leverage above 3x on the
futures leg — modelled at 1x here.

Usage:
    python3 carry_paper_desk.py            # tick: open/accrue/close as rules dictate
    python3 carry_paper_desk.py --status
"""
import argparse
import json
import math
import os
import time

import desk_lock

BASE = os.path.dirname(os.path.abspath(__file__))
MONITOR_STATE = os.path.join(BASE, "reports", "carry_monitor.json")
HISTORY_FILE = os.path.join(BASE, "reports", "carry_history.jsonl")
BOOK_FILE = os.path.join(BASE, "reports", "carry_paper_book.json")
LEDGER_FILE = os.path.join(BASE, "reports", "carry_paper_ledger.jsonl")

NOTIONAL_AUD = 33000.0
ELIGIBLE = ("BTC", "ETH")
# --- Carry v2 amendment (funder-approved 2026-08-10, spec 6196101b61833248,
#     CARRY_V2_PREREGISTRATION_DRAFT.md). Two changes only; exit rule, sizing,
#     delta-neutrality and margin-stress kills UNCHANGED. Cost margin ~5.1x->5.9x. ---
# CM-071 corrected the basket's units but missed this per-asset admission gate.
# 2% in the former 8x-understated units is 16% in hourly-correct annual units.
# 2026-09-10 correction narrows new admission; existing holdings are unaffected.
ENTRY_THRESHOLD_PCT = 16.0
EXIT_TRAILING_DAYS = 7         # close when trailing-mean carry goes negative
# CM-042 (2026-08-27, blind-spot audit): 16.0 was NOT achievable. Kraken's base
# tier measured live from AssetPairs is 40 bps taker / 25 bps MAKER on every pair
# this desk trades (reports/gold_basis_cache/cost_inputs.json). The 16 bps figure
# implied a $10M/30-day volume tier this desk will never reach, and it flattered
# every Carry v2 estimate built on it (the "+1.15%/yr maker lever" was computed at
# a cost that does not exist). Corrected to the MEASURED maker rate. Entry gate,
# exit rule, sizing, eligibility and margin kills are UNCHANGED — cost only, and
# the correction moves AGAINST the book, never for it.
COST_BPS_PER_SIDE = 25.0       # v2 corrected: MEASURED Kraken maker (50 bps
                               # round-trip). Post passive, never chase.
HOURS_PER_YEAR = 24 * 365
# --- Non-negotiable #7 stale gates (funder directive 2026-08-22: "all books
#     need live prices, no stale prices to trade against"). Entry refused on a
#     stale snapshot or stale funding row; accrual SUSPENDED (never accrue a
#     dead rate); exits always allowed. 24h mirrors the sibling probe's
#     registered gate (carry_newnames_probe MAX_FUNDING_AGE_SEC). ---
MAX_SNAPSHOT_AGE_SEC = 2 * 3600   # one missed monitor tick, with margin
MAX_FUNDING_AGE_SEC = 24 * 3600   # per-asset row age gate (registered precedent)


def _log(event):
    event["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    os.makedirs(os.path.dirname(LEDGER_FILE), exist_ok=True)
    with open(LEDGER_FILE, "a") as f:
        f.write(json.dumps(event) + "\n")


def load_book():
    try:
        with open(BOOK_FILE) as f:
            book = json.load(f)
        if not isinstance(book, dict) or not isinstance(book.get("positions"), dict):
            raise ValueError("carry book structure is invalid")
        return book
    except FileNotFoundError:
        return {"positions": {}, "realized_aud": 0.0, "funding_accrued_aud": 0.0,
                "costs_paid_aud": 0.0, "events": []}


def save_book(b):
    b["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = BOOK_FILE + ".tmp.%d" % os.getpid()
    with open(tmp, "w") as f:
        json.dump(b, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, BOOK_FILE)


def monitor_snapshot():
    try:
        with open(MONITOR_STATE) as f:
            return json.load(f)
    except Exception:
        return None


def trailing_mean_carry(days=EXIT_TRAILING_DAYS):
    """Mean of the preferred-basket carry over the trailing window, from history."""
    cutoff = time.time() - days * 86400
    vals = []
    try:
        with open(HISTORY_FILE) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("t", 0) >= cutoff and r.get("preferred_mean") is not None:
                    vals.append(float(r["preferred_mean"]))
    except OSError:
        pass
    return (sum(vals) / len(vals), len(vals)) if vals else (None, 0)


def asset_row(snap, base):
    rows = snap.get("assets", [])
    for a in rows if isinstance(rows, list) else []:
        if isinstance(a, dict) and a.get("base") == base:
            return a
    return None


def funding_accrual(notional_aud, annualised_pct, hours):
    """Observed-rate PAPER estimate; never extrapolate backwards or NaN."""
    values = (notional_aud, annualised_pct, hours)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in values):
        raise ValueError("non-finite carry accrual input")
    if notional_aud < 0 or hours < 0:
        raise ValueError("negative carry notional or elapsed time")
    return notional_aud * annualised_pct / 100.0 * hours / HOURS_PER_YEAR


def remaining_notional(positions):
    used = 0.0
    for position in positions.values():
        value = position.get("notional_aud")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError("invalid existing carry notional")
        used += value
    return max(0.0, NOTIONAL_AUD - used)


def valid_entry_row(row):
    if not isinstance(row, dict) or row.get("liquid") is not True:
        return False
    values = [row.get(key) for key in ("annualised_pct", "age_sec", "mark")]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in values):
        return False
    rate, age, mark = values
    return rate >= ENTRY_THRESHOLD_PCT and 0 <= age <= MAX_FUNDING_AGE_SEC and mark > 0


def tick(verbose=True):
    with desk_lock.ledger_lock("carry", timeout=55):
        return _tick_locked(verbose)


def _tick_locked(verbose=True):
    snap = monitor_snapshot()
    if not isinstance(snap, dict) or not snap or snap.get("error"):
        if verbose:
            print("carry desk: no monitor snapshot — no accrual/entries; exit checks continue")
        snap = {}

    b = load_book()
    now = time.time()
    msgs = []
    historical_unreconciled = (b.get("accounting_status", {}).get("historical_funding_reconciliation") == "REQUIRED"
        or any(p.get("opened_at", "") < "2026-09-04" for p in b["positions"].values()))

    # ---- stale-snapshot gate (funder directive 2026-08-22) ----
    try:
        snap_age = now - time.mktime(time.strptime(snap.get("checked_at", ""),
                                                   "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        snap_age = float("inf")
    snap_stale = not -60 <= snap_age <= MAX_SNAPSHOT_AGE_SEC
    if snap_stale:
        msgs.append("!! STALE SNAPSHOT: carry_monitor checked_at %.1fh old"
                    " (limit %.1fh) — entries refused, accrual suspended,"
                    " exits still allowed"
                    % (snap_age / 3600.0, MAX_SNAPSHOT_AGE_SEC / 3600.0))

    # ---- accrue + manage open positions ----
    for base in list(b["positions"].keys()):
        p = b["positions"][base]
        a = asset_row(snap, base) or {}
        row_age = a.get("age_sec")
        try:
            hours = (now - p["last_accrual_epoch"]) / 3600.0
            accrual = funding_accrual(p["notional_aud"], a.get("annualised_pct"), hours)
            mark = a.get("mark")
            valid = (not snap_stale and type(row_age) in (int, float)
                and math.isfinite(row_age) and 0 <= row_age <= MAX_FUNDING_AGE_SEC
                and type(mark) in (int, float) and math.isfinite(mark) and mark > 0
                and hours <= MAX_FUNDING_AGE_SEC / 3600.0)
        except (KeyError, TypeError, ValueError, OverflowError):
            valid = False
        if not valid:
            # Never accrue a dead rate. The unobserved window is forfeited
            # (epoch advanced without accrual) — honest under-count, never a
            # frozen-rate over-count. Exit checks below still run.
            p["last_accrual_epoch"] = now
            reason = "missing, stale, invalid funding/mark or unobserved accrual interval"
            _log({"event": "ACCRUAL_SUSPENDED", "base": base, "reason": reason})
            msgs.append("!! %s ACCRUAL SUSPENDED — %s (stale feed; rate not"
                        " accrued this tick)" % (base, reason))
            continue
        p["funding_accrued_aud"] = round(p["funding_accrued_aud"] + accrual, 4)
        b["funding_accrued_aud"] = round(b["funding_accrued_aud"] + accrual, 4)
        p["last_accrual_epoch"] = now
        p["current_carry_pct"] = a["annualised_pct"]

        # margin stress on the short perp leg
        move = (a["mark"] / p["entry_mark"] - 1.0) if p.get("entry_mark") else 0.0
        p["mark_move_pct"] = round(move * 100, 2)
        if move >= 0.30 and not p.get("liq_risk_logged"):
            p["liq_risk_logged"] = True
            _log({"event": "LIQUIDATION_RISK", "base": base, "move_pct": round(move * 100, 1)})
            msgs.append("!! %s perp +%.0f%% vs entry — LIQUIDATION RISK on short leg" % (base, move * 100))
        elif move >= 0.20:
            msgs.append("!  %s perp +%.0f%% vs entry — margin stress, top-up would be due" % (base, move * 100))

    # ---- exit rule: trailing carry regime died ----
    if b["positions"]:
        tm, n = trailing_mean_carry()
        if tm is not None and n >= 6 and tm < 0.0:
            for base in list(b["positions"].keys()):
                p = b["positions"].pop(base)
                exit_cost = p["notional_aud"] * COST_BPS_PER_SIDE / 10000.0
                pnl = p["funding_accrued_aud"] - p["entry_cost_aud"] - exit_cost
                b["realized_aud"] = round(b["realized_aud"] + pnl, 2)
                b["costs_paid_aud"] = round(b["costs_paid_aud"] + exit_cost, 2)
                _log({"event": "CLOSE", "base": base, "reason": "trailing %dd carry %.2f%% < 0" % (EXIT_TRAILING_DAYS, tm),
                      "funding_accrued_aud": p["funding_accrued_aud"],
                      "net_pnl_aud": round(pnl, 2), "held_days": round((now - p["opened_epoch"]) / 86400, 1)})
                msgs.append("CLOSED %s: net A$%.2f (funding A$%.2f - costs) — carry regime died" % (base, pnl, p["funding_accrued_aud"]))

    # ---- entry rule: monitor says HARVESTABLE ----
    if snap.get("preferred_verdict") == "HARVESTABLE" and snap_stale:
        _log({"event": "ENTRY_REFUSED_STALE", "snap_age_sec": round(snap_age)
              if snap_age != float("inf") else None})
        msgs.append("!! ENTRY REFUSED: HARVESTABLE but snapshot %.1fh old —"
                    " no stale prices to trade against" % (snap_age / 3600.0))
    if snap.get("preferred_verdict") == "HARVESTABLE" and not snap_stale:
        qualifying = [base for base in ELIGIBLE
                      if valid_entry_row(asset_row(snap, base))
                      and base not in b["positions"]]
        # CM-047: firm-wide halt gate. The carry desk published into the firm
        # equity series but never consulted the halt flag. Entries only — the
        # exit rule above has already run and is never gated.
        if qualifying:
            try:
                import book_kill as _kill
                _ok, _why = _kill.firm_entries_allowed("Carry Paper Desk",
                                                       verbose=False)
            except Exception as _e:                 # noqa: BLE001 - fail closed
                _ok, _why = False, "gate unavailable (%s)" % type(_e).__name__
            if not _ok:
                _log({"event": "ENTRY_REFUSED_FIRM_HALT",
                      "bases": qualifying, "reason": _why})
                msgs.append("!! ENTRY REFUSED (firm-wide halt): %s — %s"
                            % (", ".join(qualifying), _why))
                qualifying = []
        if qualifying:
            remaining = remaining_notional(b["positions"])
            if remaining <= 0:
                msgs.append("ENTRY REFUSED: A$33,000 total notional budget already used")
                qualifying = []
            per = remaining / len(qualifying) if qualifying else 0.0
            for base in qualifying:
                a = asset_row(snap, base)
                entry_cost = per * COST_BPS_PER_SIDE / 10000.0
                b["positions"][base] = {
                    "opened_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "opened_epoch": now, "last_accrual_epoch": now,
                    "notional_aud": per, "entry_mark": a["mark"],
                    "entry_carry_pct": a["annualised_pct"],
                    "entry_cost_aud": round(entry_cost, 2),
                    "funding_accrued_aud": 0.0,
                }
                b["costs_paid_aud"] = round(b["costs_paid_aud"] + entry_cost, 2)
                _log({"event": "OPEN", "base": base, "notional_aud": per,
                      "carry_pct": a["annualised_pct"], "mark": a["mark"],
                      "entry_cost_aud": round(entry_cost, 2)})
                msgs.append("OPENED %s: A$%.0f notional at %.2f%% carry (paper long-spot/short-perp)"
                            % (base, per, a["annualised_pct"]))

    b["accounting_status"] = {
        "current_accrual_method": "latest_observed_annual_rate_times_elapsed_hours_over_8760",
        "historical_funding_reconciliation": "REQUIRED" if historical_unreconciled else "no_pre_CM071_position_recorded",
        "historical_funding_adjustment_applied": False,
        "notional_cap_aud": NOTIONAL_AUD,
        "used_notional_aud": sum(p["notional_aud"] for p in b["positions"].values()),
        "validated_edge": False,
    }
    save_book(b)
    if verbose:
        status(b, snap, msgs)
    return b, msgs


def status(b=None, snap=None, msgs=()):
    b = b or load_book()
    snap = snap or monitor_snapshot() or {}
    print("CARRY PAPER DESK — armed executor for delta-neutral basis carry  [PAPER ONLY]")
    v = snap.get("preferred_verdict")
    print("  monitor verdict : %s   (BTC+ETH %.3f%% gross annualised, basket %.1f%% / asset %.1f%%)"
          % (v, snap.get("preferred_mean_annualised_pct") or 0.0,
             snap.get("harvest_threshold_pct", 32.0), ENTRY_THRESHOLD_PCT))
    if not b["positions"]:
        print("  book            : FLAT — armed, waiting for HARVESTABLE. Correct action.")
    for base, p in b["positions"].items():
        print("  OPEN %-4s A$%.0f  carry now %.2f%% (entry %.2f%%)  funding A$%.2f  mark %+.1f%%"
              % (base, p["notional_aud"], p.get("current_carry_pct", 0),
                 p["entry_carry_pct"], p["funding_accrued_aud"], p.get("mark_move_pct", 0)))
    print("  lifetime        : realized A$%.2f | funding accrued A$%.2f | costs A$%.2f"
          % (b["realized_aud"], b["funding_accrued_aud"], b["costs_paid_aud"]))
    for m in msgs:
        print("  " + m)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    if args.status:
        status()
    else:
        tick()


if __name__ == "__main__":
    main()
