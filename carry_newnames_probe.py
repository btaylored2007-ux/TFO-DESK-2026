#!/usr/bin/env python3
"""Carry New-Names Probe — $10k PAPER, delta-neutral basis carry on XMR + HYPE.

Sealed pre-registration: CARRY_NEWNAMES_PROBE_PREREGISTRATION.md
Spec hash (sealed 2026-08-10): 6196101b61833248

WHAT THIS IS
------------
The BTC/ETH basis-carry executor (carry_paper_desk.py) is the one mechanism on
this desk that clears its cost wall. The 2026-08-10 eight-hunt sweep produced a
single survivor beyond it: delta-neutral basis carry on two *new names* the
executor does not hold — XMR (+12.1%/yr, t 5.7) and HYPE (+8.6%/yr, t 5.6), the
only two that cleared a Bonferroni screen. The broad set was proven to HURT, so
widening the universe is barred.

This is the highest-lever and most-caveated finding, so it deploys as the
smallest, hardest-capped vehicle: $10k paper, per-name notional capped to
0.5% of trailing 24h $volume AND $5,000, frozen at the desk-wide book kill
(-18% since KILL_THRESHOLD_AMENDMENT.md, 2026-08-28). Its job is to
measure the UNMEASURED basis-cascade tail live, cheaply.

MECHANISM
---------
Per name: long spot / short perp, same asset. Harvest FUNDING only. No
directional P&L (the two legs cancel; a delta-neutral book's net price exposure
is ~0, so equity moves with funding harvested, not with the coin).

RULE (frozen)
-------------
* Universe: XMR, HYPE only.
* Enter when annualized funding > +2% AND the liquidity screen passes.
* Exit when funding < 0.
* Hard caps: per-name notional <= 0.5% of 24h $volume AND <= $5,000.
  Skip a name whose 24h $volume is below floor. An illiquid mark is never
  counted as harvestable at size.
* Seed $10,000; uninvested cash sits flat. Freeze at the desk-wide book kill
  drawdown (-18% since 2026-08-28; imported, never copied).

DATA HONESTY
------------
Funding + liquidity come from the SAME source carry_monitor.py uses (Kraken
Futures funding snapshots collected under
KrakenQuantDesk/reports/microstructure/funding_*.jsonl). If a name's leg is not
present in that free public feed, or its last print is stale, the probe SKIPS it
and says so. It never invents a mark. Funding accrual on an open position is
real; spot/perp are marked at the live quote; any illiquid mark is flagged and
never headlined.

PAPER ONLY. No API key, no account, no order, no credential. This file reads
public data and writes one JSON state file. It touches nothing else on the desk.

Usage:
    python3 carry_newnames_probe.py            # one tick (accrue/enter/exit)
    python3 carry_newnames_probe.py --status   # print current state, no tick
    python3 carry_newnames_probe.py --json      # tick, print full state JSON
"""
import argparse
import json
import os
import time

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE, "reports", "carry_newnames_probe.json")

SPEC_HASH = "6196101b61833248"

# ---- the frozen rule (editing these invalidates the registration) -----------
UNIVERSE = ["XMR", "HYPE"]        # perps; nothing else may be added
SEED_USD = 10_000.0
ENTRY_ANN_PCT = 2.0               # enter when annualized funding > +2%
EXIT_ANN_PCT = 0.0               # exit when funding < 0
MAX_NAME_VOL_FRAC = 0.005         # <= 0.5% of trailing 24h $volume
MAX_NAME_NOTIONAL_USD = 5_000.0   # AND <= $5,000 (half the book)
# The kill threshold is IMPORTED, never copied. This file held its own literal
# `30.0` and imports nothing from book_kill, so when the funder moved the desk-
# wide kill to -18% (KILL_THRESHOLD_AMENDMENT.md, 2026-08-28) this one book
# would silently have kept killing at -30% — the exact drift book_kill.py was
# built to make impossible. Stated as a POSITIVE magnitude here because the
# comparison below is `dd >= KILL_DRAWDOWN_PCT` on a positive drawdown.
try:
    import book_kill as _bk
    KILL_DRAWDOWN_PCT = abs(_bk.KILL_DD_PCT)
except Exception as _e:                   # noqa: BLE001 - loud, never silent
    KILL_DRAWDOWN_PCT = 18.0
    print("!! carry_newnames_probe: could not import the shared kill rule (%s: %s)"
          " — falling back to a LOCAL %.0f%% threshold, which can drift"
          % (type(_e).__name__, _e, KILL_DRAWDOWN_PCT))

# Liquidity screen. Same 24h $volume floor the carry monitor uses to separate
# liquid perps from cascade-prone illiquid ones. A round-trip spread ceiling is
# part of the registered screen, but the funding feed carries no bid/ask, so
# spread is recorded as unavailable rather than fabricated (see screen rows).
MIN_VOL24H_USD = 1_000_000.0
SPREAD_CEILING_BPS = 90.0         # desk round-trip cost ceiling (carry_monitor)

# A funding print older than this is not a live quote; we will not enter or
# accrue against it. The collector prints active names every few minutes.
MAX_FUNDING_AGE_SEC = 24 * 3600

INTERVALS_PER_YEAR = 3 * 365      # Kraken funding is per 8h -> 3/day
SEC_PER_INTERVAL = 8 * 3600.0

# Reuse the EXACT data source carry_monitor.py already uses, so this probe's
# funding/liquidity view is consistent with the rest of the desk. Import is
# read-only: carry_monitor does no work at import time. If it cannot be loaded
# we degrade to "no data" and skip honestly rather than invent marks.
try:
    import carry_monitor as _cm
    _load_funding = _cm.load_funding
    _latest_by_base = _cm.latest_by_base
    _relative_rate = _cm.relative_rate
    _annualise = _cm.annualise
    _BASE_ALIAS = getattr(_cm, "BASE_ALIAS", {"XBT": "BTC"})
    _DATA_OK = True
except Exception as _e:          # pragma: no cover - defensive
    _DATA_OK = False
    _DATA_ERR = str(_e)


def _now():
    return time.time()


def _fresh_funding():
    """Latest funding print per base from the shared feed, keyed by base.

    Returns {} if the feed is unavailable — the probe then skips every name and
    records the reason. Never fabricates a print.
    """
    if not _DATA_OK:
        return {}
    try:
        rows = _load_funding()
    except Exception:
        return {}
    return _latest_by_base(rows) if rows else {}


def _screen_name(name, latest):
    """Evaluate one universe name against the frozen screen.

    Returns a screen dict describing whether the name is tradeable right now and,
    if not, exactly why. Honest about missing/stale/illiquid data.
    """
    row = latest.get(name)
    s = {
        "name": name,
        "tradeable": False,
        "reasons": [],
        "ann_funding_pct": None,
        "vol24h_usd": None,
        "mark": None,
        "funding_age_sec": None,
        "spread_bps": None,          # not in the funding feed
        "spread_note": "no bid/ask in funding feed — spread unmeasured",
        "cap_notional_usd": None,
        "illiquid": None,
    }
    if row is None:
        s["reasons"].append("no funding data in shared feed (leg unavailable)")
        return s

    mark = row.get("markPrice")
    rel = _relative_rate(row.get("fundingRate"), mark)
    ann = _annualise(rel)
    vol_coins = float(row.get("vol24h") or 0)
    vol_usd = vol_coins * float(mark or 0)
    age = _now() - float(row.get("t") or 0)

    s["ann_funding_pct"] = round(ann, 3) if ann is not None else None
    s["vol24h_usd"] = round(vol_usd, 0)
    s["mark"] = mark
    s["funding_age_sec"] = round(age)
    s["illiquid"] = vol_usd < MIN_VOL24H_USD
    if vol_usd > 0:
        s["cap_notional_usd"] = round(
            min(MAX_NAME_VOL_FRAC * vol_usd, MAX_NAME_NOTIONAL_USD), 2)

    if ann is None:
        s["reasons"].append("funding rate unreadable")
    if age > MAX_FUNDING_AGE_SEC:
        s["reasons"].append("stale quote (age %.0fh > %.0fh) — not a live mark"
                            % (age / 3600.0, MAX_FUNDING_AGE_SEC / 3600.0))
    if vol_usd < MIN_VOL24H_USD:
        s["reasons"].append("24h $volume %s below floor %s — cascade risk, illiquid"
                            % ("{:,.0f}".format(vol_usd),
                               "{:,.0f}".format(MIN_VOL24H_USD)))
    if ann is not None and ann <= ENTRY_ANN_PCT:
        s["reasons"].append("funding %.2f%% <= entry threshold +%.1f%%"
                            % (ann, ENTRY_ANN_PCT))

    s["tradeable"] = (not s["reasons"]) and ann is not None and ann > ENTRY_ANN_PCT
    return s


def _blank_state():
    return {
        "probe": "carry_newnames_probe",
        "paper": True,
        "spec_hash": SPEC_HASH,
        "seed": SEED_USD,
        "cash": SEED_USD,
        "positions": {},          # name -> {entry, notional, funding_accrued, ...}
        "equity": SEED_USD,
        "peak": SEED_USD,
        "drawdown_pct": 0.0,
        "frozen": False,
        "screen": [],
        "universe": list(UNIVERSE),
        "caps": {
            "name_vol_frac": MAX_NAME_VOL_FRAC,
            "name_notional_usd": MAX_NAME_NOTIONAL_USD,
            "min_vol24h_usd": MIN_VOL24H_USD,
            "entry_ann_pct": ENTRY_ANN_PCT,
            "kill_drawdown_pct": KILL_DRAWDOWN_PCT,
        },
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "updated": None,
    }


def load_state():
    try:
        with open(STATE_FILE) as f:
            st = json.load(f)
        # keep the frozen constants authoritative on the record
        st.setdefault("positions", {})
        st["spec_hash"] = SPEC_HASH
        # Funding basis: after a capital_transfer, `start`/`seed_usd` are the
        # live allocated capital. Do NOT reset seed to SEED_USD — that breaks
        # firm capital accounting (UNMEASURED) every tick.
        if isinstance(st.get("start"), (int, float)):
            st["seed"] = float(st["start"])
        elif isinstance(st.get("seed_usd"), (int, float)):
            st["seed"] = float(st["seed_usd"])
        elif not isinstance(st.get("seed"), (int, float)):
            st["seed"] = SEED_USD
        st["universe"] = list(UNIVERSE)
        return st
    except (OSError, ValueError):
        return _blank_state()


def save_state(st):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=2)
    os.replace(tmp, STATE_FILE)


def _mark_equity(st, latest):
    """Equity = uninvested cash + reserved notionals + funding harvested.

    Delta-neutral, so the net directional value of each open leg is ~0 (long
    spot gains offset short perp losses and vice-versa). We therefore mark each
    position's net leg value at its reserved notional and let funding_accrued be
    the only live P&L. Any basis drift beyond that is the UNMEASURED tail this
    probe exists to observe; it is not fabricated here.
    """
    invested = 0.0
    funding = 0.0
    for name, pos in st["positions"].items():
        invested += float(pos.get("notional") or 0.0)
        funding += float(pos.get("funding_accrued") or 0.0)
        row = latest.get(name)
        if row and row.get("markPrice"):
            pos["spot_mark"] = row.get("markPrice")
            pos["perp_mark"] = row.get("markPrice")
            pos["mark_src"] = "live"
        else:
            pos["mark_src"] = "stale/no-quote"
    equity = st["cash"] + invested + funding
    st["equity"] = round(equity, 2)
    st["peak"] = round(max(st.get("peak", SEED_USD), equity), 2)
    dd = (st["peak"] - equity) / st["peak"] * 100.0 if st["peak"] > 0 else 0.0
    st["drawdown_pct"] = round(dd, 3)
    if dd >= KILL_DRAWDOWN_PCT:
        st["frozen"] = True
    return equity


def tick():
    st = load_state()

    # CM-068 (2026-09-03, funder-directed): accrue T-bill yield on idle cash.
    # This book held $10,000.00 earning exactly zero. Forward-only from the first
    # tick, never retroactive; a missing or >24h-stale rate accrues ZERO loudly
    # rather than inventing money, and the cumulative figure is kept in its own
    # field so rate income is never mistaken for trading skill.
    try:
        import cash_yield_books as _cyb
        _cyb.accrue_book(st, "Carry New-Names Probe")
    except Exception as _cy_e:                              # noqa: BLE001
        print("   CASH-YIELD skipped: %s" % _cy_e)
    latest = _fresh_funding()
    now = _now()
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S")

    if not _DATA_OK:
        st["screen"] = [{"name": n, "tradeable": False,
                         "reasons": ["funding data source unavailable: %s"
                                     % (globals().get("_DATA_ERR", "unknown"))]}
                        for n in UNIVERSE]
        _mark_equity(st, {})
        st["updated"] = now_iso
        st["data_source_ok"] = False
        save_state(st)
        return st

    st["data_source_ok"] = True

    # ---- 1. accrue funding on, and manage exits for, open positions ----------
    for name in list(st["positions"].keys()):
        pos = st["positions"][name]
        row = latest.get(name)
        # accrue real funding since last tick, using the live per-interval rate
        last = float(pos.get("last_accrued_ts") or pos.get("opened_ts") or now)
        dt = max(0.0, now - last)
        if row and row.get("markPrice"):
            rel = _relative_rate(row.get("fundingRate"), row.get("markPrice"))
            ann = _annualise(rel)
            if rel is not None:
                delta = float(pos["notional"]) * rel * (dt / SEC_PER_INTERVAL)
                pos["funding_accrued"] = round(
                    float(pos.get("funding_accrued") or 0.0) + delta, 6)
            pos["last_accrued_ts"] = now
            pos["last_ann_funding_pct"] = round(ann, 3) if ann is not None else None
            # EXIT when funding < 0 (frozen rule)
            if ann is not None and ann < EXIT_ANN_PCT:
                st["cash"] = round(st["cash"] + float(pos["notional"])
                                   + float(pos.get("funding_accrued") or 0.0), 6)
                st.setdefault("closed", []).append({
                    "name": name, "closed_at": now_iso,
                    "reason": "funding %.2f%% < 0" % ann,
                    "funding_harvested": pos.get("funding_accrued"),
                    "notional": pos.get("notional"),
                })
                del st["positions"][name]
        else:
            # lost the live quote: cannot mark honestly -> flag, do not accrue
            pos["last_accrued_ts"] = now
            pos["mark_src"] = "stale/no-quote"

    # ---- 2. screen the universe ---------------------------------------------
    screen = [_screen_name(n, latest) for n in UNIVERSE]
    st["screen"] = screen

    # ---- 3. entries (only if not frozen) ------------------------------------
    if not st.get("frozen"):
        for s in screen:
            name = s["name"]
            if name in st["positions"]:
                continue
            if not s["tradeable"]:
                continue
            cap = s.get("cap_notional_usd") or 0.0
            # cap also by available cash and the half-book per-name ceiling
            notional = min(cap, MAX_NAME_NOTIONAL_USD, max(0.0, st["cash"]))
            if notional <= 0:
                s["reasons"].append("no cash headroom to size")
                s["tradeable"] = False
                continue
            st["cash"] = round(st["cash"] - notional, 6)
            st["positions"][name] = {
                "side": "long spot / short perp",
                "entry": s["mark"],
                "notional": round(notional, 2),
                "funding_accrued": 0.0,
                "opened_at": now_iso,
                "opened_ts": now,
                "last_accrued_ts": now,
                "entry_ann_funding_pct": s["ann_funding_pct"],
                "vol24h_usd_at_entry": s["vol24h_usd"],
                "cap_notional_usd": s["cap_notional_usd"],
                "illiquid": s["illiquid"],
                "mark_src": "live",
            }

    # ---- 4. mark equity, drawdown, freeze -----------------------------------
    _mark_equity(st, latest)
    st["updated"] = now_iso
    save_state(st)
    return st


def _fmt_usd(x):
    return "${:,.2f}".format(x or 0.0)


def print_status(st):
    print("=" * 74)
    print("CARRY NEW-NAMES PROBE — long spot / short perp · funding-only · PAPER")
    print("spec %s   updated %s" % (st.get("spec_hash"), st.get("updated") or "(never)"))
    print("=" * 74)
    frozen = st.get("frozen")
    print("seed      : %s" % _fmt_usd(st.get("seed", SEED_USD)))
    print("cash flat : %s" % _fmt_usd(st.get("cash", 0.0)))
    print("equity    : %s   peak %s   drawdown %.2f%%%s"
          % (_fmt_usd(st.get("equity", 0.0)), _fmt_usd(st.get("peak", 0.0)),
             st.get("drawdown_pct", 0.0),
             "   *** FROZEN (-30%% kill) ***" if frozen else ""))
    print()
    pos = st.get("positions", {})
    if pos:
        print("OPEN LEGS (funding is the only live P&L; net directional ~0)")
        for name, p in pos.items():
            flag = "  ILLIQUID-FLAGGED" if p.get("illiquid") else ""
            print("  %-6s notional %s  funding_accrued %s  cap %s  [%s]%s"
                  % (name, _fmt_usd(p.get("notional", 0.0)),
                     _fmt_usd(p.get("funding_accrued", 0.0)),
                     _fmt_usd(p.get("cap_notional_usd") or 0.0),
                     p.get("mark_src", "?"), flag))
    else:
        print("OPEN LEGS: none — armed, no entry.")
    print()
    print("SCREEN")
    for s in st.get("screen", []):
        verdict = "TRADEABLE" if s.get("tradeable") else "skipped"
        ann = s.get("ann_funding_pct")
        vol = s.get("vol24h_usd")
        print("  %-6s %-9s funding=%s%%  vol24h=%s"
              % (s["name"], verdict,
                 ("%.2f" % ann) if ann is not None else "n/a",
                 ("{:,.0f}".format(vol)) if vol is not None else "n/a"))
        for r in s.get("reasons", []):
            print("           - %s" % r)
    print()
    if not pos:
        print("Armed, no entry is the CORRECT honest outcome when no name clears the")
        print("+2%% funding gate with a fresh, liquid quote. Not a failure. Day 0.")


def main():
    ap = argparse.ArgumentParser(description="Carry New-Names Probe (paper)")
    ap.add_argument("--status", action="store_true",
                    help="print current state without ticking")
    ap.add_argument("--json", action="store_true",
                    help="tick, then print the full state as JSON")
    args = ap.parse_args()

    if args.status:
        st = load_state()
        if st.get("updated") is None:
            print("No probe state yet — run `python3 carry_newnames_probe.py` to tick.")
            print("(seed would be %s paper)" % _fmt_usd(SEED_USD))
            return
        print_status(st)
        return

    st = tick()
    if args.json:
        print(json.dumps(st, indent=2))
    else:
        print_status(st)


if __name__ == "__main__":
    main()
