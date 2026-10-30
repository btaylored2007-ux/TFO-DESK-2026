#!/usr/bin/env python3
"""firm_risk.py — the firm's equity, its peak, its drawdown, and the only two
firm-level triggers that exist.

BASIS
-----
Funder-directed 2026-08-27, registered in `FIRM_RISK_AMENDMENT.md`. Before
today the firm had 27 books, per-book equity everywhere, and NO firm number at
all: no firm equity series, no firm peak, no firm drawdown, no aggregate
trigger. Every book could be individually inside its own limits while the firm
as a whole bled, and nothing anywhere would have said so.

WHAT IT DOES
------------
Each tick: total equity across EVERY book (`book_coverage`, the same coverage
`risk_engine` uses, so the two can never disagree), append it to
reports/firm_equity.csv, track the firm peak, compute the firm drawdown, and
publish reports/firm_risk_state.json — which the floors read before opening a
NEW position.

THE TWO LEVELS (frozen)
-----------------------
    -10%  WARN            loud banner, dashboard banner. Nothing is blocked.
    -20%  HALT NEW ENTRIES a firm-level flag. ACCURACY (CM-045): the gate is
          wired into the FOUR desk floors only (kraken, replica, coinspot,
          cross_asset). The remaining books — tournament sleeves, growth_book,
          value_book, the carry books, the punt, the engine — publish into the
          firm series but do NOT yet consult the flag before opening. That gap
          is recorded in FIRM_RISK_AMENDMENT.md. Previously this docstring
          claimed 'every book checks it', which was not true; it now checks it before
                          OPENING a position. EXITS ARE NEVER BLOCKED.

*** THIS MONITOR NEVER FORCE-LIQUIDATES. NOT AT ANY LEVEL. ***
Twenty-seven books are running pre-registered forward tests. Auto-closing them
on a drawdown trigger would destroy every one of those track records at once
and convert a pre-registered experiment into discretion applied at the worst
possible moment — which is precisely the behaviour the registrations exist to
prevent. Closing books is the funder's decision, always, made by a human with
the whole picture. This file halts new risk and shouts; it does not sell.

HONESTY ABOUT THE PEAK
----------------------
There was no firm equity series before 2026-08-27. The 2026-09-10 repair
replays a separately verified interval after a $75,000 internal transfer was
mistaken for new external funding. Original observations remain unchanged.
The result is an observed drawdown, not an inception or cash-flow-adjusted
investment return. Funding receipts/bases are reconciled separately from marks.

STALE BOOKS ARE FLAGGED, NOT HIDDEN
-----------------------------------
The OANDA book's live status read is empty (a sustained 401), so its equity is
a stale ledger NAV. It is included in the headline and ALSO reported separately
as `stale_equity_usd`, and the CSV carries the live-only total in its own
column — so either series can be rebuilt and neither is a guess dressed as a
measurement.

PAPER ONLY. Reads ledgers, writes a CSV, a state file and a report. It holds no
credentials, places no order, and closes nothing.

Usage: python3 firm_risk.py [--status | --repair-capital-20260910 [--apply]]
"""
import argparse
import contextlib
import csv
import fcntl
import hashlib
import json
import os
import tempfile
import time

import book_coverage
import firm_capital_flows

BASE = os.path.dirname(os.path.abspath(__file__))
EQUITY_CSV = os.path.join(BASE, "reports", "firm_equity.csv")
STATE = os.path.join(BASE, "reports", "firm_risk_state.json")
REPORT = os.path.join(BASE, "reports", "firm_risk.json")
ERRORS = os.path.join(BASE, "reports", "firm_risk_errors.json")
LOCK = os.path.join(BASE, "reports", ".firm_risk.lock")

WARN_DD_PCT = -10.0                  # loud, blocks nothing
HALT_DD_PCT = -20.0                  # halts NEW entries firm-wide; never sells
NEVER_LIQUIDATE = True               # not a switch. A constant, so grep finds it.
AMENDMENT = "FIRM_RISK_AMENDMENT.md (funder-directed 2026-08-27)"

# 2026-10-17 (Brett 10:29 AEDT, weekly cut review): the OANDA practice-account AGGRO sleeve ledger is COUNTED by this monitor.
# It is a practice account (A$, paper) outside V1 desk equity - like the TFO OANDA sleeve - so it is NOT added to firm equity
# (its NAV is the whole practice account, incl. TFO OANDA trades and Brett's two index positions; adding it would invent
# capital and break the capital-flow reconciliation). It is claimed here, its risk rails are reported every tick, and a
# breach / kill / halt / stale or unreadable ledger is a loud warning in firm_risk.json.
AGGRO_STATS = os.path.join(BASE, "state", "oanda_aggro", "stats.json")
AGGRO_STALE_H = 6.0


def oanda_aggro_monitor(path=None, now=None):
    """-> dict describing the AGGRO ledger for firm_risk.json. Never raises."""
    path = path or AGGRO_STATS
    out = {"ledger": path, "in_firm_equity": False, "currency": "AUD", "warnings": [],
           "basis": "OANDA PRACTICE account (paper, A$), outside V1 desk equity; risk monitored, not summed into firm equity"}
    try:
        with open(path) as f:
            d = json.load(f)
        if not isinstance(d, dict):
            raise ValueError("ledger is not an object")
    except FileNotFoundError:
        out["error"] = "AGGRO ledger missing"; out["warnings"].append(out["error"]); return out
    except Exception as e:                          # noqa: BLE001 - reported
        out["error"] = "AGGRO ledger unreadable (%s)" % type(e).__name__; out["warnings"].append(out["error"]); return out
    now = time.time() if now is None else now
    try:
        age_h = (now - os.path.getmtime(path)) / 3600.0
    except OSError:
        age_h = None
    num = lambda k: d.get(k) if isinstance(d.get(k), (int, float)) and not isinstance(d.get(k), bool) else None
    opn = [r for r in (d.get("open") or []) if isinstance(r, dict) and r.get("owner") == "AGGRO"]
    out.update(asof_utc=d.get("asof_utc"), age_h=round(age_h, 2) if age_h is not None else None,
               stale=age_h is None or age_h > AGGRO_STALE_H,
               account_nav_aud=num("nav"), start_nav_aud=num("start_nav"), total_ret_pct=num("total_ret_pct"),
               week=d.get("week"), week_ret_pct=num("week_ret_pct"), week_target_pct=num("week_target_pct"),
               week_halted=bool(d.get("week_halted")), killed=bool(d.get("killed")),
               account_open_risk_pct=num("open_risk_pct"), account_open_risk_cap_pct=num("open_risk_cap_pct"),
               aggro_open_risk_pct=round(100 * sum(float(r.get("risk_frac") or 0) for r in opn), 2),
               margin_pct=num("margin_pct"), aggro_upl_aud=num("aggro_upl_aud"),
               aggro_positions=[{k: r.get(k) for k in ("id", "ins", "dir", "design", "risk_pct")} for r in opn])
    w = out["warnings"]
    try:   # 2026-10-25 (Brett, TFO Risk audited): OANDA reconcile failing -> book UNVERIFIED, its stale marks are not counted
        import oanda_reconcile_guard as _RG
        import datetime as _dt
        rg = _RG.board(os.path.join(BASE, "state", "oanda"), None if now is None else _dt.datetime.fromtimestamp(now, _dt.timezone.utc))
    except Exception as e:                          # noqa: BLE001 - reported
        rg = {"verified": False, "reconcile_status": "UNKNOWN (%s)" % type(e).__name__}
    out["verified"] = bool(rg.get("verified")); out["reconcile"] = rg
    if not out["verified"]:
        for k in ("account_nav_aud", "total_ret_pct", "week_ret_pct", "account_open_risk_pct", "aggro_open_risk_pct", "margin_pct", "aggro_upl_aud"):
            out[k] = None
        out["marks_counted"] = False
        w.append("OANDA book UNVERIFIED (reconcile %s since %s): marks not counted%s" % (rg.get("reconcile_status"), rg.get("reconcile_first_fail_utc"),
                 "; NEW ENTRIES BLOCKED" if rg.get("entries_blocked") else ""))
    else:
        out["marks_counted"] = True
    if out["stale"]:
        w.append("AGGRO ledger stale (%s h old, limit %.0f h)" % (out["age_h"], AGGRO_STALE_H))
    if out["killed"]:
        w.append("AGGRO HARD KILL recorded")
    if out["week_halted"]:
        w.append("AGGRO weekly halt active (no new AGGRO entries)")
    r, cap = out["account_open_risk_pct"], out["account_open_risk_cap_pct"]
    if r is not None and cap is not None and r > cap + 1e-9:
        w.append("account open risk %.2f%% > %.2f%% cap" % (r, cap))
    if out["margin_pct"] is not None and out["margin_pct"] > 60:
        w.append("margin %.1f%% > 60%%" % out["margin_pct"])
    return out


CSV_HEADER = ("utc,firm_equity_usd,firm_equity_live_usd,stale_equity_usd,"
              "firm_peak_usd,firm_drawdown_pct,level,books_readable,books_total,"
              "positions,capital_event\n")


def capital_event(prev, books):
    """Capital is a recorded flow, never simply a changed readable-book set."""
    return firm_capital_flows.capital_event(prev, books)


@contextlib.contextmanager
def writer_lock():
    """Serialize the entire read/reconcile/publish transaction across ticks."""
    os.makedirs(os.path.dirname(LOCK), exist_ok=True)
    with open(LOCK, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def atomic_json(path, payload):
    """Readers see one complete generation, including on a process crash."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".firm-risk-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {}


def persist_errors(errs):
    """An unreadable book is unmeasured firm equity. Never silent, never zero."""
    try:
        os.makedirs(os.path.dirname(ERRORS), exist_ok=True)
        if errs:
            with open(ERRORS, "w") as f:
                json.dump({"utc": _utc(), "errors": errs, "source": "firm_risk"},
                          f, indent=2)
        elif os.path.exists(ERRORS):
            os.remove(ERRORS)
    except Exception as e:                          # noqa: BLE001
        print("!! firm_risk: could not persist error marker (%s: %s)"
              % (type(e).__name__, e))


def append_csv(row):
    """Append one observation. CM-045: if the header on disk has a different
    field count from CSV_HEADER (it did — the file was created with 10 columns
    and `capital_event` was added later as an 11th), REWRITE the header rather
    than appending rows a DictReader will mis-assign. The firm drawdown is only
    as good as this file."""
    try:
        os.makedirs(os.path.dirname(EQUITY_CSV), exist_ok=True)
        need_header = not os.path.exists(EQUITY_CSV) or os.path.getsize(EQUITY_CSV) == 0
        if not need_header:
            with open(EQUITY_CSV) as f:
                first = f.readline()
            if first.count(",") != CSV_HEADER.count(","):
                with open(EQUITY_CSV) as f:
                    rest = f.readlines()[1:]
                tmp = EQUITY_CSV + ".tmp"
                with open(tmp, "w") as f:
                    f.write(CSV_HEADER)
                    f.writelines(rest)
                os.replace(tmp, EQUITY_CSV)
                print("   firm_equity.csv header REWRITTEN %d -> %d fields "
                      "(CM-045); %d existing rows preserved"
                      % (first.count(",") + 1, CSV_HEADER.count(",") + 1, len(rest)))
        with open(EQUITY_CSV, "a") as f:
            if need_header:
                f.write(CSV_HEADER)
            f.write(row)
            f.flush()
            os.fsync(f.fileno())
        return True
    except Exception as e:
        print("   ERROR firm equity series not appended: %s" % e)
        return False


def run(verbose=True):
    with writer_lock():
        return _run_locked(verbose=verbose)


def _run_locked(verbose=True, previous=None):
    prev = load_state() if previous is None else previous
    before_capital = firm_capital_flows.source_fingerprints(prev.get("capital_snapshot"))
    g = book_coverage.gather(verbose=verbose)
    persist_errors(g["errors"])

    # CM-044: the registry is hand-written for everything outside
    # reports/tournament/, which is how three live books stayed invisible. Run
    # the self-audit EVERY tick — an audit that is never called is not a guard,
    # it is a comment. Never allowed to break the tick.
    try:
        unclaimed, _ = book_coverage.coverage_audit(g["books"], verbose=verbose)
    except Exception as e:                          # noqa: BLE001 - reported
        unclaimed = []
        print("   COVERAGE AUDIT FAILED (%s: %s) — coverage is UNVERIFIED this "
              "tick" % (type(e).__name__, e))

    aggro = oanda_aggro_monitor()
    unclaimed = [u for u in unclaimed
                 if os.path.realpath(u.get("path") or "") != os.path.realpath(AGGRO_STATS)]   # claimed by the AGGRO monitor
    if verbose and aggro["warnings"]:
        print("   OANDA AGGRO (practice, outside firm equity): " + "; ".join(aggro["warnings"]))

    eq = g["firm_equity_usd"]
    cap = capital_event(prev, g["books"])
    if before_capital != firm_capital_flows.source_fingerprints(prev.get("capital_snapshot")):
        cap.update(capital_accounting_valid=False, peak_shift_usd=0.0, external_flow_usd=None,
                   capital_snapshot=prev.get("capital_snapshot") or {})
        cap["capital_accounting_errors"].append("capital metadata changed during valuation; retry next tick")
    peak = prev.get("firm_peak_usd")
    peak_source = prev.get("peak_source")
    # CM-045: `unmeasured` must be known BEFORE the peak is initialised. A cold
    # start during a partial read would otherwise seal an incomplete equity in
    # as the firm peak permanently — the peak is never revised downward, so that
    # error would understate every future drawdown for the life of the series.
    n_ok, n_tot = g["n_books_ok"], g["n_books"]
    unmeasured = n_ok < n_tot or not cap["capital_accounting_valid"]
    if (not isinstance(peak, (int, float)) or peak <= 0) and unmeasured:
        peak, peak_source = None, None
        if verbose:
            print("!! firm peak NOT initialised: only %d of %d books readable. "
                  "A partial read must never seal in a firm peak." % (n_ok, n_tot))
    elif not isinstance(peak, (int, float)) or peak <= 0:
        peak = eq
        peak_source = ("first observation %s — no firm equity series existed before "
                       "this date, so firm drawdown is measured from here forward" % _utc())
        if verbose:
            print("   firm peak initialised at $%s (%s)"
                  % ("{:,.0f}".format(eq), peak_source))
    peak_at = prev.get("firm_peak_at_utc") or _utc()

    # Only verified EXTERNAL capital changes the dollar high-water basis.
    # Internal movements and coverage failures cannot change a firm peak.
    peak_shift = float(cap.get("peak_shift_usd") or 0.0)
    if not unmeasured and peak_shift and isinstance(peak, (int, float)):
        peak = peak + peak_shift
        peak_source = ("shifted %+.2f on %s for net EXTERNAL funding (added %s, "
                       "removed %s) — bookkeeping, not performance; the prior "
                       "drawdown is preserved across the change"
                       % (peak_shift, _utc(), cap.get("books_added") or "none",
                          cap.get("books_removed") or "none"))
        _fl = [d for d in (cap.get("capital_flow_details") or []) if isinstance(d, dict)]   # 2026-10-30 dashfix: name the flows
        if _fl: peak_source += "; flows: " + ", ".join("%s %s %+.2f" % (d.get("book"), d.get("type"), float(d.get("usd") or 0)) for d in _fl[:8])
        if cap.get("owner_performance_flows"):
            peak_source = ("owner unit redemption on %s; performance-equivalent peak shifted %+.8f; "
                           "existing percentage drawdown preserved, real capital reduced"
                           % (_utc(), peak_shift))
        if verbose:
            print("   firm peak SHIFTED %+.2f -> $%s (%s)"
                  % (peak_shift, "{:,.2f}".format(peak), "book-set change"))

    # -- CM-045 (b): a partial read must never establish or move the peak. -----
    if peak and eq > peak and not unmeasured:
        peak, peak_at = eq, _utc()
    elif peak and eq > peak and unmeasured:
        if verbose:
            print("   firm peak NOT advanced: %d/%d books readable — a partial "
                  "read cannot set a peak" % (n_ok, n_tot))
    dd = (eq / peak - 1.0) * 100.0 if peak else 0.0
    # Partial equity cannot describe an economic loss. Preserve the last known
    # complete drawdown for display and mark it invalid; entry gate fails closed.
    if unmeasured:
        dd = float(prev.get("firm_drawdown_pct") or 0.0)

    # -- CM-045 (c): never publish OK on an incomplete read. -------------------
    # Before this, total coverage failure published equity $0, drawdown 0.00%,
    # level OK — the most dangerous line this file could print.
    if unmeasured:
        level = "UNMEASURED"
        halted = True
        if verbose:
            print("!! FIRM LEVEL UNMEASURED — only %d of %d books readable. "
                  "Firm equity/drawdown are INCOMPLETE and must not be read as "
                  "OK. Unreadable: %s"
                  % (n_ok, n_tot,
                     ", ".join(b["key"] for b in g["books"] if b["error"]) or
                     "; ".join(cap["capital_accounting_errors"])))
    elif dd <= HALT_DD_PCT:
        level, halted = "HALT_NEW_ENTRIES", True
    elif dd <= WARN_DD_PCT:
        level, halted = "WARN", False
    else:
        level, halted = "OK", False

    headline = ("firm equity $%s vs peak $%s = %.2f%% (warn %.0f%% / halt-new-entries %.0f%%)"
                % ("{:,.0f}".format(eq),
                   "{:,.0f}".format(peak) if peak else "UNSET (partial read)", dd,
                   WARN_DD_PCT, HALT_DD_PCT))
    if unmeasured:
        headline = ("firm equity/drawdown UNMEASURED; last complete drawdown %.2f%%; "
                    "new entries halted until coverage and capital reconcile" % dd)

    state = {
        "utc": _utc(), "epoch": time.time(),
        "firm_equity_usd": round(eq, 2),
        "firm_equity_live_usd": round(g["firm_equity_live_usd"], 2),
        "stale_equity_usd": round(g["stale_equity_usd"], 2),
        "firm_peak_usd": round(peak, 2) if peak else None,
        "firm_peak_at_utc": peak_at if peak else None,
        "peak_source": peak_source,
        "firm_drawdown_pct": round(dd, 2),
        "drawdown_valid": not unmeasured,
        "drawdown_basis_note": prev.get("drawdown_basis_note",
            "observed firm drawdown; not inception return or P&L"),
        "owner_withdrawal_performance_flows": cap.get("owner_performance_flows", []),
        "capital_repair": prev.get("capital_repair"),
        "warn_dd_pct": WARN_DD_PCT, "halt_dd_pct": HALT_DD_PCT,
        "level": level, "new_entries_halted": halted,
        "force_liquidate": False,
        "never_force_liquidate": NEVER_LIQUIDATE,
        "liquidation_policy": ("This monitor NEVER force-liquidates at any level. "
                               "Closing a book is the funder's decision. The halt "
                               "blocks NEW entries only; exits are never blocked."),
        "headline": headline, "amendment": AMENDMENT,
        "books_total": g["n_books"], "books_readable": g["n_books_ok"],
        "unclaimed_ledgers": [u["path"] for u in unclaimed],
        "coverage_audit_clean": not unclaimed,
        "oanda_aggro": aggro,
        "positions": g["n_positions"],
        "unreadable_books": g["errors"],
        "book_keys": (prev.get("book_keys", []) if unmeasured else
                      sorted(b["key"] for b in g["books"])),
        # CM-045: keyed by book KEY (book_drawdowns is keyed by label). This is
        # the ONLY record of what a removed book was worth, and without it the
        # peak cannot be shifted when a book leaves the set.
        "book_equity_usd": (prev.get("book_equity_usd", {}) if unmeasured else
                           {b["key"]: round(b["equity_usd"], 2)
                            for b in g["books"]
                            if not b["error"] and b["equity_usd"] is not None}),
        **cap,
        "book_drawdowns": sorted(
            [{"book": b["label"], "kind": b["kind"],
              "equity_usd": round(b["equity_usd"], 2),
              "peak_usd": round(b["book_peak_usd"], 2) if b["book_peak_usd"] else None,
              "drawdown_pct": (round((b["equity_usd"] / b["book_peak_usd"] - 1) * 100, 2)
                               if b["book_peak_usd"] else None),
              "stale": b["stale"]}
             for b in g["books"] if not b["error"] and b["equity_usd"] is not None],
            key=lambda r: (r["drawdown_pct"] is None, r["drawdown_pct"] or 0)),
    }

    # 2026-10-29 TFO HT: daily P&L vs firm equity at the first complete read of the WITA day (external flows shift the base); -3% daily freeze flag
    _wd = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600)); _ds = prev.get("day_start_equity_usd")
    if prev.get("day_start_date_wita") != _wd or not _ds:
        _ds = None if unmeasured else round(eq, 2)
    elif not unmeasured and peak_shift: _ds = round(_ds + peak_shift, 2)
    _dp = round((eq / _ds - 1.0) * 100.0, 2) if _ds and not unmeasured else None
    state.update(day_start_date_wita=_wd if _ds else None, day_start_equity_usd=_ds, daily_pnl_pct=_dp, daily_freeze_pct=-3.0,
                 daily_freeze=bool(unmeasured or (_dp is not None and _dp <= -3.0)),
                 daily_freeze_note="true when firm daily P&L <= -3.0% vs WITA-day start (or equity unmeasured - fail closed); informational for TFO Risk")
    row = "%s,%.2f,%.2f,%.2f,%s,%.4f,%s,%d,%d,%d,%s\n" % (
        state["utc"], eq, g["firm_equity_live_usd"], g["stale_equity_usd"],
        ("%.2f" % peak) if peak else "", dd, level, g["n_books_ok"],
        g["n_books"], g["n_positions"],
        "YES" if cap["capital_event"] else "no")
    state["series_appended"] = append_csv(row)

    try:
        atomic_json(STATE, state)
        atomic_json(REPORT, state)
    except Exception as e:                          # noqa: BLE001
        print("!! firm_risk: COULD NOT PUBLISH THE FIRM RISK STATE (%s: %s) — the "
              "firm halt cannot be seen by the books this tick"
              % (type(e).__name__, e))
        raise RuntimeError("firm risk publication failed") from e
    return state


def repair_capital_20260910(apply=False):
    """Known-incident repair; dry-run by default, no trading/ledger mutation.

    The historical proof is deliberately exact and narrow. Do not generalize this
    into guessing deposits from a price jump or resetting an inconvenient peak.
    """
    with writer_lock():
        old = load_state()
        if (old.get("capital_repair") or {}).get("id") == "internal-transfer-20260910":
            return {"already_applied": True, "state": old}
        if old.get("new_entries_halted"):
            raise RuntimeError("repair refuses to clear a pre-existing firm halt")
        sources = {}
        for key in ("convexity_sleeve", "dated_event_book"):
            path = os.path.join(BASE, "reports", key + ".json")
            with open(path, "rb") as f:
                sources[key] = (path, f.read())
        convexity = json.loads(sources["convexity_sleeve"][1])
        dated = json.loads(sources["dated_event_book"][1])
        net_dated = sum(float(e["usd"]) for e in dated.get("capital_events", [])
                        if e.get("type") == "capital_transfer")
        if abs(float(convexity["seed_usd"]) + net_dated - 150000.0) > 0.005:
            raise RuntimeError("paired books no longer reconcile to verified $150,000 capital")
        expected = [("2026-09-05T19:38:11Z", 75000.0),
                    ("2026-09-06T08:33:39Z", -67503.96)]
        evidence = []
        for stamp, amount in expected:
            leg = [e for e in dated.get("capital_events", [])
                   if e.get("utc") == stamp and e.get("type") == "capital_transfer"
                   and e.get("usd") == amount
                   and e.get("from" if amount > 0 else "to") == "convexity_sleeve"]
            opposite = [e for e in convexity.get("capital_events", [])
                        if e.get("utc") == stamp and e.get("type") == "capital_transfer"
                        and e.get("usd") == -amount
                        and e.get("to" if amount > 0 else "from") == "dated_event_book"]
            if len(leg) != 1 or len(opposite) != 1:
                raise RuntimeError("verified paired transfer receipts changed/absent")
            evidence.append({"utc": stamp, "dated_event_usd": amount,
                             "convexity_usd": -amount, "net_external_usd": 0.0})
        with open(EQUITY_CSV, "rb") as f:
            raw_csv = f.read()
        rows = list(csv.DictReader(raw_csv.decode().splitlines()))
        by_utc = {r["utc"]: r for r in rows}
        before = by_utc["2026-09-05T18:50:55Z"]
        incident = by_utc["2026-09-05T19:59:56Z"]
        if (float(before["firm_peak_usd"]) != 1692532.35 or
                float(incident["firm_peak_usd"]) - float(before["firm_peak_usd"]) != 75000.0 or
                abs(float(incident["firm_equity_usd"]) - float(before["firm_equity_usd"]) - 200.99) > 0.01):
            raise RuntimeError("historical incident observations no longer match verified evidence")
        # The verified interval has no external flows. The only newly registered
        # funded book is the paired internal transfer. The Value Book disappears
        # only during UNMEASURED records; its recovery is not a contribution.
        for row in rows:
            if row["utc"] < incident["utc"]:
                continue
            if row["capital_event"] == "YES" and row["utc"] not in {
                    incident["utc"], "2026-09-09T05:35:14Z", "2026-09-09T07:49:45Z"}:
                raise RuntimeError("unreviewed capital event in repair interval: " + row["utc"])
        replay = firm_capital_flows.replay_complete(
            rows, before["utc"], 1692532.35, "2026-09-04T10:21:51Z")
        result = {"id": "internal-transfer-20260910", "run_utc": _utc(),
                  "applied": False, "old_peak_usd": old["firm_peak_usd"],
                  "replayed_peak_usd": replay["peak"], "replayed_peak_at_utc": replay["peak_at"],
                  "old_reported_drawdown_pct": old["firm_drawdown_pct"],
                  "latest_replayed_observation": replay["rows"][-1],
                  "basis_since_utc": replay["basis_since_utc"],
                  "basis_note": replay["basis_note"], "paired_transfers": evidence,
                  "original_csv_sha256": hashlib.sha256(raw_csv).hexdigest(),
                  "original_csv_rows": len(rows), "external_flows_in_verified_interval": [],
                  "limitations": ["No inception return is reconstructed.",
                      "Underlying historical marks are carried as recorded, not independently revalued.",
                      "Incomplete observations are retained and excluded from the high-water replay."]}
        if not apply:
            return result
        # Full safe valuation only. book_coverage cannot trade; source books,
        # cash, fills and frozen registrations remain outside this operation.
        g = book_coverage.gather(verbose=False)
        if g["n_books_ok"] != g["n_books"] or sorted(b["key"] for b in g["books"]) != old["book_keys"]:
            raise RuntimeError("repair requires complete unchanged book coverage")
        initial_snapshot, errors = firm_capital_flows.snapshot(g["books"])
        if errors:
            raise RuntimeError("cannot establish reconciled funding snapshot: " + "; ".join(errors))
        # New post-audit flow receipts must be reviewed before this narrow replay
        # can assert a zero-external-flow interval.
        for meta in initial_snapshot.values():
            for event in meta["events"]:
                if event["utc"] > before["utc"] and event["type"] != "capital_transfer":
                    raise RuntimeError("unreviewed external flow: " + event["id"])
        root = os.path.join(BASE, "reports", "repairs")
        os.makedirs(root, exist_ok=True)
        backup = tempfile.mkdtemp(prefix="firm-capital-20260910-", dir=root)
        backed_up = {}
        for path in (STATE, REPORT, EQUITY_CSV):
            with open(path, "rb") as f:
                payload = f.read()
            target = os.path.join(backup, os.path.basename(path))
            with open(target, "xb") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            backed_up[target] = hashlib.sha256(payload).hexdigest()
        for key, (path, payload) in sources.items():
            target = os.path.join(backup, key + ".json")
            with open(target, "xb") as f:
                f.write(payload)
            backed_up[target] = hashlib.sha256(payload).hexdigest()
        result["backup_directory"] = backup
        result["backup_sha256"] = backed_up
        replay_path = os.path.join(backup, "verified_interval_replay.json")
        atomic_json(replay_path, replay)
        corrected = dict(old, firm_peak_usd=replay["peak"], firm_peak_at_utc=replay["peak_at"],
                         capital_snapshot=initial_snapshot,
                         capital_repair={"id": result["id"], "receipt": os.path.join(backup, "receipt.json")},
                         drawdown_basis_note=replay["basis_note"],
                         peak_source="2026-09-10 verified complete-observation replay; original series preserved in " + backup)
        state = _run_locked(verbose=False, previous=corrected)
        result.update(applied=True, current_equity_usd=state["firm_equity_usd"],
                      current_peak_usd=state["firm_peak_usd"], current_drawdown_pct=state["firm_drawdown_pct"],
                      drawdown_valid=state["drawdown_valid"], level=state["level"],
                      original_history_preserved=True)
        atomic_json(os.path.join(backup, "receipt.json"), result)
        return result


def report(s):
    print("=" * 74)
    print("TAYLOR FAMILY OFFICE — FIRM-WIDE DRAWDOWN MONITOR   [%s]" % s["utc"])
    print("=" * 74)
    print("  firm equity        : $%s   (live $%s · STALE $%s)"
          % ("{:,.0f}".format(s["firm_equity_usd"]),
             "{:,.0f}".format(s["firm_equity_live_usd"]),
             "{:,.0f}".format(s["stale_equity_usd"])))
    print("  firm peak          : %s  (%s)"
          % ("$" + "{:,.0f}".format(s["firm_peak_usd"]) if s["firm_peak_usd"]
             else "UNSET — no complete read has been observed yet",
             s["firm_peak_at_utc"] or "n/a"))
    print("  firm drawdown      : %.2f%%" % s["firm_drawdown_pct"])
    print("  books              : %d readable of %d · %d positions"
          % (s["books_readable"], s["books_total"], s["positions"]))
    for e in s.get("unreadable_books") or []:
        print("    !! UNREADABLE     : %s" % e)
    if s.get("capital_event"):
        print("  !! CAPITAL EVENT   : net external funding $%s; internal transfers net to zero"
              % "{:,.2f}".format(s.get("external_flow_usd") or 0))
    if s["level"] == "HALT_NEW_ENTRIES":
        print("!" * 74)
        print("!! FIRM HALT — NEW ENTRIES REFUSED FIRM-WIDE (drawdown %.2f%% <= %.0f%%)"
              % (s["firm_drawdown_pct"], s["halt_dd_pct"]))
        print("!! NOTHING IS LIQUIDATED. EXITS RUN NORMALLY. Closing is the funder's call.")
        print("!" * 74)
    elif s["level"] == "UNMEASURED":
        print("  level              : UNMEASURED — incomplete coverage; not an all-clear")
    elif s["level"] == "WARN":
        print("*" * 74)
        print("** FIRM DRAWDOWN WARNING — %.2f%% (warn at %.0f%%). Nothing is blocked."
              % (s["firm_drawdown_pct"], s["warn_dd_pct"]))
        print("*" * 74)
    else:
        print("  level              : OK  (warn %.0f%% · halt-new-entries %.0f%% · "
              "NEVER force-liquidate)" % (s["warn_dd_pct"], s["halt_dd_pct"]))
    worst = [b for b in s.get("book_drawdowns") or [] if b["drawdown_pct"] is not None][:5]
    if worst:
        # Read the threshold rather than printing a literal: this line said
        # "-30%" for the first hour after KILL_THRESHOLD_AMENDMENT.md moved it to
        # -18%, which is the specific way a risk display becomes a liability.
        try:
            import book_kill as _bk
            _kill_pct = _bk.KILL_DD_PCT
        except Exception:                             # noqa: BLE001
            _kill_pct = None
        print("\n  DEEPEST BOOK DRAWDOWNS (book-level kill freezes NEW entries at %s)"
              % ("%.0f%%" % _kill_pct if _kill_pct is not None
                 else "a threshold this process could not read"))
        for b in worst:
            print("    %-28s %7.2f%%   equity $%s"
                  % (b["book"][:28], b["drawdown_pct"], "{:,.0f}".format(b["equity_usd"])))
    ag = s.get("oanda_aggro")
    if ag:
        if ag.get("error"):
            print("\n  OANDA AGGRO        : !! %s (%s)" % (ag["error"], ag.get("ledger")))
        else:
            print("\n  OANDA AGGRO        : week %s%% (target %s%%), account open risk %s%% / cap %s%%, AGGRO risk %s%%, %d open; "
                  "practice A$, outside firm equity" % (ag.get("week_ret_pct"), ag.get("week_target_pct"), ag.get("account_open_risk_pct"),
                                                         ag.get("account_open_risk_cap_pct"), ag.get("aggro_open_risk_pct"), len(ag.get("aggro_positions") or [])))
        for w in ag.get("warnings") or []:
            print("    !! AGGRO          : %s" % w)
    print("\n  series -> %s" % EQUITY_CSV)
    print("  state  -> %s" % STATE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true",
                    help="print the last published state without recomputing")
    ap.add_argument("--repair-capital-20260910", action="store_true",
                    help="preview the evidence-bound internal-transfer high-water repair")
    ap.add_argument("--apply", action="store_true", help="apply that repair after immutable backups")
    a = ap.parse_args()
    if a.apply and not a.repair_capital_20260910:
        ap.error("--apply requires --repair-capital-20260910")
    if a.repair_capital_20260910:
        print(json.dumps(repair_capital_20260910(apply=a.apply), indent=2))
        return
    if a.status:
        s = load_state()
        if not s:
            print("firm_risk: no state published yet — run without --status")
            return
        report(s)
        return
    report(run())


if __name__ == "__main__":
    main()
