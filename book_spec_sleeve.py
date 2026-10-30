#!/usr/bin/env python3
"""Per-book speculative sleeves (~10% NAV) — PAPER ONLY.

Brett 28 Oct 13:40 approve-all #4 + 14:58 WITA all-desks: carve ~10% of each book's
NAV from its own cash into a separate sleeve (own stops, War Room sub-row). Does NOT
auto-arm or place trades — hunt sleeves still need Brett funder tick. Skip books with
cash committed to a pending deploy (ESSENTIALS staged, Trend18 30 Oct funding).

ESSENTIALS + TREND-CORE already have SPEC; this module covers hunt48 books.
"""
from __future__ import annotations
import datetime as dt, json, os, sys, tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "state", "spec_sleeves")
STATIC = os.path.join(BASE, "static")
UTC = dt.timezone.utc
WITA = dt.timezone(dt.timedelta(hours=8))

# Hunt books: notional A$1m, 10% = A$100k. Separate stops mirror ESSENTIALS SPEC.
HUNT_BOOKS = ("asx", "jp", "hk", "sg", "kr", "tw", "lse")
SPEC_FRAC = 0.10
NAME_STOP = 0.20
SLEEVE_HALT = 0.30
NAME_CAP_FRAC = 0.02  # of book NAV

def wita(t=None):
    return (t or dt.datetime.now(UTC)).astimezone(WITA).strftime("%Y-%m-%d %H:%M:%S WITA")

def atomic(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=2, default=str); f.write("\n")
    os.replace(tmp, path)

def ledger_path(mkt):
    return os.path.join(STATE, f"hunt48_{mkt}.json")

def load_hunt_capital(mkt):
    p = os.path.join(BASE, "state", f"hunt48_{mkt}", "ledger.json")
    L = json.load(open(p))
    cap = float(L.get("capital_aud") or 1_000_000.0)
    used = sum(float(t.get("notional_aud") or 0) for t in (L.get("trades") or [])
               if t.get("status") in ("FILLED", "ARMED", "awaiting_funder"))
    return cap, used, L

def ensure(mkt, force=False):
    """Create or refresh the sleeve ledger. Funding = carve from free capital (accounting only)."""
    assert mkt in HUNT_BOOKS
    path = ledger_path(mkt)
    cap, used, _ = load_hunt_capital(mkt)
    target = round(cap * SPEC_FRAC, 2)
    free = cap - used
    if free < target:
        # still create but mark underfunded
        funded = round(max(0.0, free), 2)
        note = "underfunded: free capital A$%.0f < target A$%.0f" % (free, target)
    else:
        funded = target
        note = "funded from hunt book free capital (accounting carve; main RISK_AUD/MAX_OPEN unchanged)"
    if os.path.exists(path) and not force:
        S = json.load(open(path))
        S["updated_wita"] = wita()
        S["parent_free_aud"] = round(free, 2)
        atomic(path, S)
        return S
    S = dict(
        book=f"HUNT48_{mkt.upper()}_SPEC",
        parent=f"hunt48_{mkt}",
        market=mkt,
        paper_only=True,
        auto_arm=False,
        funder_tick_required=True,
        ccy="AUD",
        nav0_aud=cap,
        spec_frac=SPEC_FRAC,
        seed_aud=funded,
        cash_aud=funded,
        target_aud=target,
        positions={},
        fills=[],
        realised_aud=0.0,
        costs_paid_aud=0.0,
        name_stop_pct=NAME_STOP,
        sleeve_halt_pct=SLEEVE_HALT,
        name_cap_aud=round(cap * NAME_CAP_FRAC, 2),
        spec_halt=False,
        note=note,
        created_wita=wita(),
        updated_wita=wita(),
        status="ARMED_EMPTY",  # sleeve exists; no positions until Brett ticks setups into it
    )
    atomic(path, S)
    return S

def write_status(mkt=None):
    mkts = [mkt] if mkt else list(HUNT_BOOKS)
    out = {}
    for m in mkts:
        S = ensure(m)
        nav = S["cash_aud"] + sum(float(p.get("value_aud") or 0) for p in (S.get("positions") or {}).values())
        st = dict(
            book=S["book"], parent=S["parent"], paper_only=True,
            cash_aud=S["cash_aud"], seed_aud=S["seed_aud"], nav_aud=round(nav, 2),
            pnl_aud=round(nav - S["seed_aud"] + float(S.get("realised_aud") or 0), 2),
            n_pos=len(S.get("positions") or {}),
            spec_halt=S.get("spec_halt"), name_stop_pct=S["name_stop_pct"],
            sleeve_halt_pct=S["sleeve_halt_pct"], funder_tick_required=True,
            auto_arm=False, status=S.get("status"), updated_wita=S["updated_wita"],
        )
        atomic(os.path.join(STATIC, f"hunt48_{m}_spec_status.json"), st)
        out[m] = st
    atomic(os.path.join(STATE, "registry.json"), dict(
        paper_only=True, frac=SPEC_FRAC, books=list(HUNT_BOOKS),
        skipped=["essentials (already has SPEC + staged deploy)",
                 "trend_core (already has SPEC via global)",
                 "trend18 (cash 0 / 30 Oct funding other worker)",
                 "fundamental (cash rules owned elsewhere)",
                 "v2 (do not move V2 cash)",
                 "oanda (whole account AGGRO)"],
        updated_wita=wita(), statuses=out,
    ))
    return out

def main(argv=None):
    argv = list(argv or sys.argv[1:])
    cmd = argv[0] if argv else "status"
    if cmd == "init":
        force = "--force" in argv
        for m in HUNT_BOOKS:
            S = ensure(m, force=force)
            print(m, "seed A$%.0f cash A$%.0f status=%s" % (S["seed_aud"], S["cash_aud"], S["status"]))
        write_status()
    elif cmd == "status":
        st = write_status()
        for m, s in st.items():
            print(m, json.dumps(s))
    else:
        print("usage: book_spec_sleeve.py init|status [--force]"); return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
