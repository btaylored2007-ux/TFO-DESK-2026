#!/usr/bin/env python3
"""book_kill.py — the book-level kill rule, in ONE place.

BASIS
-----
Funder-directed 2026-08-27 ("GO FOR IT FINISH IT"), registered in
`FIRM_RISK_AMENDMENT.md`. Every tournament sleeve has frozen at a drawdown
threshold since 2026-08-06 (`tournament.py` `mark()`) — −30% then, −18% since
the funder-directed `KILL_THRESHOLD_AMENDMENT.md` of 2026-08-28 — but the four
paper FLOORS — kraken_floor, replica_floor, coinspot_floor,
cross_asset_floor — had **no
book-level kill of any kind**: no peak, no drawdown, no freeze, nothing that
could stop a book that was losing continuously. They now run the SAME rule as
the sleeves, imported from here rather than copied, so the floors and the
tournament can never drift apart on what "killed" means.

THE RULE (frozen)
-----------------
    A book FREEZES when its equity is 18% or more below its own peak equity.
    A FROZEN BOOK OPENS NO NEW POSITION.
    A FROZEN BOOK STILL EXITS NORMALLY, ALWAYS, WITHOUT EXCEPTION.

That last line is the whole design. A kill that blocks exits does not reduce
risk — it traps the book in exactly the positions that caused the drawdown and
removes the only mechanism that can end it. Every gate in this module is
therefore applied at the ENTRY site only; nothing here can reach, close, resize
or alter a position that is already open, and no function in this module writes
a position, a unit count or a cash balance.

PEAK, HONESTLY
--------------
A drawdown needs a peak, and three of the four floors have never recorded one.
Where a genuine equity history exists on disk (`replica_equity.csv`) the peak is
seeded from it — that is recorded data, not synthesis. Where none exists the
peak starts at TODAY'S equity and says so in `peak_source`, which means the kill
can only measure drawdown from this date forward for that book. Back-filling a
peak that was never observed would manufacture a drawdown that never happened.

FAILURE IS LOUD
---------------
Every freeze prints a banner, writes `frozen`/`frozen_reason`/`frozen_at_utc`
into the book's own ledger, and appends to reports/kill_events.jsonl so the
event survives the next tick overwriting the ledger.

PAPER ONLY. Reads ledgers and the firm risk state; writes only risk metadata.
"""
import json
import math
import os
import time

BASE = os.path.dirname(os.path.abspath(__file__))
KILL_DD_PCT = -18.0                       # funder-directed 2026-08-28; see KILL_THRESHOLD_AMENDMENT.md
KILL_LOG = os.path.join(BASE, "reports", "kill_events.jsonl")
# Peaks live in a SIDECAR, not only in the book's ledger. Two of the four floors
# run as long-lived `--loop` daemons that rewrite their whole ledger from an
# in-memory dict every hour: a peak kept only in the ledger would be silently
# erased on every one of those writes, the drawdown would reset to ~0 forever,
# and the kill could never fire. That is precisely the shape of failure this
# amendment exists to remove, so the peak is stored where a ledger rewrite
# cannot reach it. The ledger keeps a mirror copy, for humans.
PEAKS = os.path.join(BASE, "reports", "book_peaks.json")
FIRM_STATE = os.path.join(BASE, "reports", "firm_risk_state.json")
FIRM_STATE_MAX_AGE_SEC = 6 * 3600         # older than this = UNAVAILABLE, not "clear"
AMENDMENT = ("FIRM_RISK_AMENDMENT.md (funder-directed 2026-08-27); threshold "
             "amended to -18% by KILL_THRESHOLD_AMENDMENT.md (funder-directed "
             "2026-08-28)")
RULE = ("freeze NEW ENTRIES at %.0f%% drawdown from this book's own peak equity; "
        "EXITS ARE NEVER BLOCKED" % KILL_DD_PCT)


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


KILL_LOG_MAX_BYTES = 2_000_000       # ~10k events; rotate, never lose
KILL_LOG_KEEP = 5                    # kill_events.jsonl.1 .. .5


def _rotate_kill_log():
    """CM-045: a frozen book appends one refusal per desk per tick, forever, with
    no bound. Rotate at 2MB keeping 5 generations. An audit log that eats the
    disk stops being an audit log."""
    try:
        if not os.path.exists(KILL_LOG):
            return
        if os.path.getsize(KILL_LOG) < KILL_LOG_MAX_BYTES:
            return
        for i in range(KILL_LOG_KEEP - 1, 0, -1):
            src, dst = "%s.%d" % (KILL_LOG, i), "%s.%d" % (KILL_LOG, i + 1)
            if os.path.exists(src):
                os.replace(src, dst)
        os.replace(KILL_LOG, KILL_LOG + ".1")
        print("   book_kill: rotated %s (>%d bytes)"
              % (os.path.basename(KILL_LOG), KILL_LOG_MAX_BYTES))
    except Exception as e:                          # noqa: BLE001 - reported
        print("!! book_kill: log rotation failed (%s: %s)" % (type(e).__name__, e))


def _log(event):
    try:
        os.makedirs(os.path.dirname(KILL_LOG), exist_ok=True)
        _rotate_kill_log()
        with open(KILL_LOG, "a") as f:
            f.write(json.dumps(event) + "\n")
    except Exception as e:                          # noqa: BLE001 - reported
        print("!! book_kill: could not append to %s (%s: %s)"
              % (KILL_LOG, type(e).__name__, e))


def _load_peaks():
    try:
        with open(PEAKS) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_peaks(d):
    try:
        os.makedirs(os.path.dirname(PEAKS), exist_ok=True)
        tmp = PEAKS + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, PEAKS)
    except Exception as e:                          # noqa: BLE001 - LOUD
        print("!! book_kill: COULD NOT PERSIST THE PEAK STORE (%s: %s) — a drawdown "
              "measured against a peak that cannot be saved is not a kill rule"
              % (type(e).__name__, e))


def seed_peak_from_csv(path, column="combined_equity"):
    """Highest equity in a REAL recorded series, or None. No synthesis: if the
    file does not exist the answer is None and the caller starts its peak today."""
    try:
        with open(path) as f:
            head = f.readline().strip().split(",")
            i = head.index(column)
            best = None
            for line in f:
                parts = line.strip().split(",")
                if len(parts) <= i:
                    continue
                try:
                    v = float(parts[i])
                except ValueError:
                    continue
                best = v if best is None else max(best, v)
        return best
    except Exception:
        return None


def net_capital_flow(ledger):
    """Cumulative signed capital_transfer/injection/withdrawal usd for a ledger whose
    capital_events carry usd signed from the book's view (in +, out -). Accounting
    repairs are NOT flows (they are losses) and are excluded. 2026-09-26 custodian.
    usd only (no amount_usd fallback): peak_net_flow_usd baselines were set on this definition;
    widening it re-counts pre-peak events as flows since peak (2026-10-30 16:40 WITA, HT)."""
    return sum(float(e.get("usd") or 0) for e in (ledger.get("capital_events") or [])
               if e.get("type") in ("capital_transfer", "injection", "withdrawal", "leaderboard_prize"))


def evaluate(ledger, equity, label, seed_peak=None, seed_note=None, verbose=True,
             net_flow_usd=None):
    """Update and return the book's `book_risk` subtree. NEVER touches positions.

    `ledger` is the book's own dict — only its `book_risk` key is written, so a
    bug here cannot corrupt a holding, a unit count or a cash balance.
    """
    if not isinstance(equity, (int, float)) or equity != equity:
        # A book that cannot value itself cannot be risk-managed. Say so; do not
        # invent a number and do not silently skip the check.
        br = ledger.setdefault("book_risk", {})
        br.update({"amendment": AMENDMENT, "rule": RULE, "checked_utc": _utc(),
                   "error": "book published no usable equity — DRAWDOWN UNMEASURED"})
        print("!! KILL RULE UNMEASURED for %s: no usable equity this tick" % label)
        _log({"utc": _utc(), "book": label, "event": "equity_unavailable"})
        return br

    # The SIDECAR is the authority; the ledger's copy is a mirror that a daemon
    # rewrite may legitimately have wiped. Whichever peak is HIGHER wins, so a
    # lost file can only ever forget history, never shrink a drawdown that was
    # already recorded.
    store = _load_peaks()
    saved = store.get(label) or {}
    br = ledger.get("book_risk") or {}
    flow_mode = isinstance(net_flow_usd, (int, float))
    cands = [(x.get("peak_equity"), x.get("peak_net_flow_usd")) for x in (saved, br)
             if isinstance(x.get("peak_equity"), (int, float))]
    if flow_mode:
        # FLOW-ADJUSTED (HT 2026-09-26): drawdown = (equity - flows since peak)/peak - 1, so
        # paired transfers out (half-bank/STOP proceeds) and funding in are not P&L.
        cands = [c for c in cands if isinstance(c[1], (int, float))]
        cands.sort(key=lambda c: c[0] - c[1])
        peak_flow = cands[-1][1] if cands else float(net_flow_usd)
    peaks = [c[0] for c in cands[-1:]] if flow_mode else [c[0] for c in cands]
    if saved.get("frozen"):
        br["frozen"] = True
        br.setdefault("frozen_reason", saved.get("frozen_reason", "kill rule"))
        br.setdefault("frozen_at_utc", saved.get("frozen_at_utc"))
    for k in ("peak_source", "peak_since_utc", "peak_at_utc"):
        if saved.get(k) and not br.get(k):
            br[k] = saved[k]
    peak = max(peaks) if peaks else None
    if not isinstance(peak, (int, float)):
        if isinstance(seed_peak, (int, float)) and seed_peak > 0:
            peak, src = float(seed_peak), (seed_note or "seeded from recorded equity history")
        else:
            peak, src = float(equity), ("first observation %s — no equity history existed "
                                        "before this date, so drawdown is measured from "
                                        "here forward" % _utc())
        br["peak_source"] = src
        br["peak_since_utc"] = _utc()
        print("   book_risk: %s peak initialised at $%.2f (%s)" % (label, peak, src))

    adj = float(equity) - (float(net_flow_usd) - peak_flow) if flow_mode else float(equity)
    if adj > peak:
        peak = float(equity)
        br["peak_at_utc"] = _utc()
        if flow_mode:
            peak_flow, adj = float(net_flow_usd), float(equity)
    dd = (adj / peak - 1.0) * 100.0 if peak else 0.0
    if flow_mode:
        br.update({"peak_net_flow_usd": round(peak_flow, 2), "net_flow_usd": round(float(net_flow_usd), 2),
                   "drawdown_basis": "flow-adjusted: (equity - net capital transfers since peak) / peak"})

    br.update({"amendment": AMENDMENT, "rule": RULE, "kill_drawdown_pct": KILL_DD_PCT,
               "equity": round(float(equity), 2), "peak_equity": round(float(peak), 2),
               "drawdown_pct": round(dd, 2), "checked_utc": _utc()})
    br.pop("error", None)
    br.setdefault("frozen", False)

    if dd <= KILL_DD_PCT and not br.get("frozen"):
        br["frozen"] = True
        br["frozen_reason"] = "kill rule: drawdown %.2f%% <= %.1f%%" % (dd, KILL_DD_PCT)
        br["frozen_at_utc"] = _utc()
        banner = ("!! BOOK FROZEN — %s: equity $%.2f vs peak $%.2f = %.2f%% "
                  "(kill %.1f%%). NEW ENTRIES REFUSED. EXITS CONTINUE NORMALLY."
                  % (label, equity, peak, dd, KILL_DD_PCT))
        print("!" * 74)
        print(banner)
        print("!" * 74)
        _log({"utc": _utc(), "book": label, "event": "FROZEN", "equity": equity,
              "peak_equity": peak, "drawdown_pct": round(dd, 2),
              "kill_drawdown_pct": KILL_DD_PCT, "amendment": AMENDMENT})
    elif br.get("frozen") and verbose:
        # A frozen book stays frozen: un-freezing is a funder decision, never an
        # automatic consequence of the equity recovering.
        print("   %s is FROZEN (%s) — entries refused, exits still running"
              % (label, br.get("frozen_reason")))
    ledger["book_risk"] = br
    store[label] = {k: br.get(k) for k in
                    ("peak_equity", "peak_net_flow_usd", "drawdown_basis", "peak_at_utc", "peak_since_utc", "peak_source",
                     "equity", "drawdown_pct", "frozen", "frozen_reason",
                     "frozen_at_utc", "checked_utc")}
    _save_peaks(store)
    return br


def firm_gate(verbose=True):
    """(halted, level, detail) from the firm-wide monitor.

    Missing, malformed or stale state refuses NEW entries. This prospective
    tightening was authorised 2026-09-10; it does not freeze a book permanently,
    change a drawdown threshold, or affect an exit.
    """
    try:
        with open(FIRM_STATE) as f:
            s = json.load(f)
        if not isinstance(s, dict) or type(s.get("new_entries_halted")) is not bool:
            raise ValueError("missing boolean entry-halt decision")
        if s.get("level") not in {"OK", "WARN", "HALT_NEW_ENTRIES"}:
            raise ValueError("unknown or unmeasured firm risk level")
        if s.get("drawdown_valid") is False or s.get("capital_accounting_valid") is False:
            raise ValueError("invalid accounting or drawdown")
        if s.get("level") == "HALT_NEW_ENTRIES" and not s["new_entries_halted"]:
            raise ValueError("contradictory firm halt decision")
        if (s.get("books_total") is not None
                and s.get("books_readable") != s["books_total"]):
            raise ValueError("incomplete firm book coverage")
        epoch = s.get("epoch")
        if isinstance(epoch, bool) or not isinstance(epoch, (int, float)) or not math.isfinite(epoch):
            raise ValueError("invalid state timestamp")
    except Exception as e:                          # noqa: BLE001
        if verbose:
            print("   FIRM RISK STATE UNAVAILABLE (%s: %s) — NEW entries refused; "
                  "exits unaffected" % (type(e).__name__, e))
        return True, "UNAVAILABLE", "%s unreadable or invalid" % FIRM_STATE
    age = time.time() - epoch
    if age < -60 or age > FIRM_STATE_MAX_AGE_SEC:
        if verbose:
            print("   FIRM RISK STATE STALE/FUTURE (%.1f h old) — NEW entries refused; "
                  "exits unaffected" % (age / 3600.0))
        return True, "STALE", "state %.1f h old" % (age / 3600.0)
    return bool(s.get("new_entries_halted")), s.get("level", "OK"), s.get("headline", "")


_FIRM_CACHE = {"epoch": 0.0, "val": None}
FIRM_CACHE_TTL_SEC = 60


def firm_entries_allowed(label="", verbose=True):
    """(allowed, reason) consulting ONLY the firm-wide halt.

    For books that have no `book_risk` ledger structure of their own — the
    tournament sleeves, growth_book, value_book, the carry books. Before
    CM-047 these 27 books published into the firm equity series but never
    consulted the flag, so a firm-wide HALT_NEW_ENTRIES stopped the four desk
    floors and nothing else — roughly a third of firm capital.

    Result is cached for 60s: set_holdings() is called once per sleeve per tick
    and re-reading (and re-printing) the firm state 14 times a tick would be
    both wasteful and unreadable.

    Fails closed for NEW entries on unavailable risk state under the
    2026-09-10 tightening amendment. Exits never consult this function.
    """
    now = time.time()
    if _FIRM_CACHE["val"] is not None and now - _FIRM_CACHE["epoch"] < FIRM_CACHE_TTL_SEC:
        halted, level, detail = _FIRM_CACHE["val"]
    else:
        halted, level, detail = firm_gate(verbose=verbose)
        _FIRM_CACHE.update({"epoch": now, "val": (halted, level, detail)})
    if halted:
        return False, ("FIRM-WIDE HALT (%s): %s — NEW entries and ADD-TOs refused; "
                       "reductions and exits are unaffected and no position is "
                       "closed by this. %s" % (level, detail, AMENDMENT))
    return True, "ok"


def entries_allowed(ledger, label, verbose=True):
    """(allowed, reason). Called at the ENTRY site only.

    Two independent gates: this book's own kill, and the firm-wide halt. Either
    one refuses NEW entries. Neither can block an exit, because neither is ever
    consulted on the exit path.
    """
    # Both the ledger mirror AND the sidecar are consulted: a daemon that
    # rewrote its ledger from an in-memory dict must not be able to un-freeze
    # itself by forgetting.
    br = dict(_load_peaks().get(label) or {})
    br.update({k: v for k, v in (ledger.get("book_risk") or {}).items() if v is not None})
    if br.get("frozen") or (_load_peaks().get(label) or {}).get("frozen"):
        return False, ("BOOK FROZEN: %s (%s)" % (br.get("frozen_reason", "kill rule"), AMENDMENT))
    if br.get("error"):
        return False, ("BOOK RISK UNMEASURED: %s — fail-closed on NEW entries, "
                       "exits unaffected" % br["error"])
    halted, level, detail = firm_gate(verbose=verbose)
    if halted:
        return False, ("FIRM-WIDE HALT (%s): %s — new entries refused firm-wide; "
                       "no position is closed by this. %s" % (level, detail, AMENDMENT))
    return True, "ok"


def record_refusal(ledger, label, sym, desk, reason):
    """Machine-visible refusal trail in the book's own ledger + the kill log."""
    sub = ledger.setdefault("book_risk", {}).setdefault("entry_refusals", [])
    rec = {"utc": _utc(), "book": label, "desk": desk, "symbol": sym, "reason": reason}
    sub.insert(0, rec)
    del sub[200:]
    ledger["book_risk"]["last_refusal_utc"] = rec["utc"]
    ledger["book_risk"]["refusal_count"] = int(
        ledger["book_risk"].get("refusal_count") or 0) + 1
    print("   ENTRY REFUSED (kill rule) %s [%s/%s]: %s" % (sym, label, desk, reason))
    _log(rec | {"event": "ENTRY_REFUSED"})


if __name__ == "__main__":
    print(__doc__)
    print("kill drawdown: %.1f%%   firm state: %s" % (KILL_DD_PCT, FIRM_STATE))
    print("firm gate now:", firm_gate())
