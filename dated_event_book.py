#!/usr/bin/env python3
"""dated_event_book.py — the slow, long-only, dated-event situation book. PAPER ONLY.

PRE-REGISTERED 2026-09-06. Funder-directed ("do b"). Charter: DATED_EVENT_BOOK_PREREGISTRATION.md

WHERE THIS CAME FROM
--------------------
Not a hunt. Thirty analysts mined the desk's own 33 books and 195 closed trades for
what has actually worked, and one strategy fell out of the tape:

  Every positive dollar on the firm came from HELD listed-equity LONGS and from
  names carried THROUGH a real information event. Every short leg on every desk
  lost. Every exit lab (exit_axis N=1,190; spec_rally t=-2.01) says holding beats
  early exit. Every cost lab says hold 30+ days or do not trade. Small equal
  tickets contained the tail — the worst trade on the desk cost 0.17% of equity
  because the ticket was $3,947.

This book is that strategy and nothing else. It is funded from the convexity
sleeve's cash because the sleeve's sealed registration permits only options,
warrants and cash-settled contracts — long equity was never on its list, and TAP
and 7004.T sit there only as funder exceptions. Rather than write a third
exception, the funder chose a book whose charter says what it holds.

THE RULES ARE THE BOOK. Every gate below is in the charter; the charter is sealed.
"""
import argparse
import json
import os
import sys
import time
import datetime as _dt

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
LEDGER = os.path.join(BASE, "reports", "dated_event_book.json")
SLEEVE = os.path.join(BASE, "reports", "convexity_sleeve.json")

TICKET_USD        = 7500.0     # flat. No conviction multiplier — the desk measured it cannot rank its own ideas
EVENT_MIN_TD      = 5          # trading days ahead at entry (see charter for why 5, not 20)
EVENT_MAX_TD      = 90
HOLD_MIN_TD       = 30         # declared hold — the cost lab's zero-crossing is 14d; 30d is the margin
EVENT_EXIT_TD     = 5          # close at event day + 5 sessions, or HOLD_MIN_TD, whichever is LATER
MAX_ADV_FRACTION  = 0.01       # position <= 1% of 20-day ADV
SECTOR_CAP        = 0.20
CCY_BLOC_CAP      = 0.20
REREAD_DRAWDOWN   = -0.25      # no price stop on longs; at -25% a mandatory thesis re-read
KILL_EVENTS       = 20         # strategy verdict at 20 completed events or 120 trading days
KILL_TD           = 120
BOOK_KILL_DD      = -0.18      # freezes new entries, never force-liquidates


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _today():
    return _dt.date.today()


def _trading_days_between(a, b):
    """Weekday count from date a (exclusive) to b (inclusive). Holidays ignored — conservative."""
    if b <= a:
        return 0
    n, d = 0, a
    while d < b:
        d += _dt.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def load():
    if not os.path.exists(LEDGER):
        return None
    with open(LEDGER) as f:
        return json.load(f)


def save(d):
    d["updated_utc"] = _utc()
    tmp = LEDGER + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, LEDGER)


def create(seed_from_sleeve_usd):
    """Create the book by TRANSFERRING cash from the convexity sleeve.

    Recorded as a capital event in BOTH ledgers. CM-052: a capital move that is
    not written down reads as phantom performance on one side and a phantom loss
    on the other.
    """
    if os.path.exists(LEDGER):
        raise SystemExit("REFUSED: book already exists. Registrations are append-only.")
    s = json.load(open(SLEEVE))
    if seed_from_sleeve_usd > float(s["cash"]) + 1e-6:
        raise SystemExit("REFUSED: sleeve holds $%.2f cash; cannot transfer $%.2f."
                         % (s["cash"], seed_from_sleeve_usd))
    ev = {"utc": _utc(), "type": "capital_transfer", "usd": -seed_from_sleeve_usd,
          "to": "dated_event_book", "note": "funder-directed 2026-09-06: fund the dated-event long book"}
    s["cash"] = round(float(s["cash"]) - seed_from_sleeve_usd, 2)
    s["seed_usd"] = round(float(s["seed_usd"]) - seed_from_sleeve_usd, 2)   # seed moves with the cash
    s.setdefault("capital_events", []).append(ev)
    s["updated_utc"] = _utc()
    tmp = SLEEVE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=2)
    os.replace(tmp, SLEEVE)
    d = {
        "book": "dated_event_book",
        "label": "Dated-Event Long Book",
        "paper_only": True,
        "seed_usd": seed_from_sleeve_usd, "cash": seed_from_sleeve_usd,
        "realised_usd": 0.0, "costs_paid_usd": 0.0, "unrealised_usd": 0.0,
        "created_utc": _utc(),
        "capital_events": [{"utc": _utc(), "type": "capital_transfer", "usd": seed_from_sleeve_usd,
                            "from": "convexity_sleeve", "note": ev["note"]}],
        "positions": {}, "pending": {}, "closed": [], "events_completed": 0,
        "charter": "DATED_EVENT_BOOK_PREREGISTRATION.md",
    }
    save(d)
    return d


def queue(symbol, event_date, event_url, confirms_it, kills_it, sector, ccy, adv_shares,
          thesis, fill_session_date):
    """Queue an entry to FILL AT THE NEXT SESSION'S OPEN. Never previousClose.

    52 of 65 fundamental-book entries filled at a stale prior close and booked
    ~$1.2k of unearnable P&L. This book records the research timestamp now and
    fills at the first live quote after the named session opens.
    """
    d = load()
    if d is None:
        raise SystemExit("REFUSED: create the book first.")
    if symbol in d["positions"] or symbol in d["pending"]:
        raise SystemExit("REFUSED: %s already queued or held." % symbol)
    if not event_url or "http" not in str(event_url).lower():
        raise SystemExit("REFUSED: event_url carries no URL. A date that traces to nothing "
                         "is a fabricated field (CM-069).")
    ed = _dt.date.fromisoformat(event_date)
    fd = _dt.date.fromisoformat(fill_session_date)
    td = _trading_days_between(fd, ed)
    if td < EVENT_MIN_TD or td > EVENT_MAX_TD:
        raise SystemExit("REFUSED: event is %d trading days from the fill session; charter "
                         "requires %d-%d. The desk's habit is to arrive AFTER the event — "
                         "this gate exists to stop that." % (td, EVENT_MIN_TD, EVENT_MAX_TD))
    if ed <= _today():
        raise SystemExit("REFUSED: event date %s has already passed." % event_date)
    for lab, txt in (("CONFIRMS-IT", confirms_it), ("KILLS-IT", kills_it)):
        if not txt or len(str(txt).strip()) < 20:
            raise SystemExit("REFUSED: %s limb missing or too thin. Both limbs are written "
                             "BEFORE entry, or there is no entry." % lab)
    if not adv_shares or adv_shares <= 0:
        raise SystemExit("REFUSED: no ADV — the 1%%-of-ADV gate cannot be evaluated.")
    # sector / currency caps against the FULL book (pending + open)
    book = list(d["positions"].values()) + list(d["pending"].values())
    n_after = len(book) + 1
    for cap, key, val in ((SECTOR_CAP, "sector", sector), (CCY_BLOC_CAP, "ccy", ccy)):
        same = sum(1 for p in book if p.get(key) == val) + 1
        if n_after >= 5 and same / n_after > cap + 1e-9:
            raise SystemExit("REFUSED: %s '%s' would be %d/%d = %.0f%% of the book; cap %.0f%%."
                             % (key, val, same, n_after, 100 * same / n_after, 100 * cap))
    if TICKET_USD > d["cash"] + 1e-6:
        raise SystemExit("REFUSED: $%.2f cash cannot fund a $%.2f ticket." % (d["cash"], TICKET_USD))
    d["pending"][symbol] = {
        "symbol": symbol, "ticket_usd": TICKET_USD, "event_date": event_date,
        "event_url": event_url, "confirms_it": confirms_it, "kills_it": kills_it,
        "sector": sector, "ccy": ccy, "adv_shares_20d": adv_shares,
        "thesis": thesis[:800], "research_utc": _utc(),
        "fill_session": fill_session_date, "fill_rule": "first live quote after the session open",
    }
    save(d)
    return d["pending"][symbol]


LIVE_TIMEOUT_S = 45


def _live(sym):
    """Live quote via fetch_prices in a SUBPROCESS with a hard timeout.

    yfinance hangs on this host with no timeout (documented: "tick blackout —
    DNS outage hangs yfinance, holds tick lock for hours"). It did exactly that
    during this book's fill test, 20 minutes after the same call had worked. An
    in-process call would hang the hourly desk_tick and block every other book
    at the one moment this book needs to fill. A subprocess dies on its own; the
    tick prints "no live quote — will retry" and moves on. No stale carry, no
    guess — a missing quote is a missing quote.
    """
    import subprocess
    code = ("import sys,json;sys.path.insert(0,%r);import fundamental_book as fb;"
            "px,_=fb.fetch_prices([%r]);print(json.dumps(px.get(%r)))" % (BASE, sym, sym))
    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           timeout=LIVE_TIMEOUT_S)
        v = json.loads((r.stdout or "null").strip().splitlines()[-1])
        if isinstance(v, dict) and (v.get("px_usd") or 0) > 0:
            return v
    except subprocess.TimeoutExpired:
        print("  QUOTE TIMEOUT %-10s no answer in %ds — treating as no live quote" % (sym, LIVE_TIMEOUT_S))
    except Exception:                                       # noqa: BLE001
        pass
    return None


def _session_open_utc(sym, date_):
    """Approximate session open in UTC by venue suffix. Conservative."""
    suf = sym.rsplit(".", 1)[-1] if "." in sym else "US"
    hour = {"L": 7, "T": 0, "HK": 1, "AX": 0, "TO": 13, "US": 13, "PA": 7, "DE": 7,
            "ST": 7, "HE": 6, "CO": 7, "OL": 7, "SW": 7, "MC": 7}.get(suf, 13)
    return _dt.datetime(date_.year, date_.month, date_.day, hour, 0, tzinfo=_dt.timezone.utc)


def tick(verbose=True):
    d = load()
    if d is None:
        return None
    now = _dt.datetime.now(_dt.timezone.utc)
    # 1. FILL pending orders whose session has opened
    import v1_venues  # 2026-09-28 funder directive: V1 entry venues US/.T/.AX from 29 Sep Sydney
    for sym in list(d["pending"]):
        p = d["pending"][sym]
        if v1_venues.entry_block_reason(sym):
            if verbose:
                print("  PENDING %-10s %s — not filled" % (sym, v1_venues.entry_block_reason(sym)))
            continue
        fd = _dt.date.fromisoformat(p["fill_session"])
        if now < _session_open_utc(sym, fd):
            continue
        q = _live(sym)
        if not q:
            if verbose:
                print("  PENDING %-10s session open but no live quote — will retry" % sym)
            continue
        px_usd = float(q["px_usd"]); units = round(p["ticket_usd"] / px_usd, 6)
        if units > MAX_ADV_FRACTION * p["adv_shares_20d"]:
            print("  REFUSED %-10s %s units = %.2f%% of ADV > %.0f%% cap" %
                  (sym, units, 100 * units / p["adv_shares_20d"], 100 * MAX_ADV_FRACTION))
            del d["pending"][sym]
            continue
        cost = round(units * px_usd, 2); fee = round(cost * 0.0010, 2)      # 10 bps/side
        if cost + fee > float(d["cash"]) + 1e-8:
            print("  REFUSED %-10s insufficient cash including entry fee" % sym)
            continue
        d["cash"] = round(d["cash"] - cost - fee, 2)
        d["costs_paid_usd"] = round(d["costs_paid_usd"] + fee, 2)
        ed = _dt.date.fromisoformat(p["event_date"])
        hold_exit = fd
        n = 0
        while n < HOLD_MIN_TD:
            hold_exit += _dt.timedelta(days=1)
            if hold_exit.weekday() < 5:
                n += 1
        ev_exit = ed
        n = 0
        while n < EVENT_EXIT_TD:
            ev_exit += _dt.timedelta(days=1)
            if ev_exit.weekday() < 5:
                n += 1
        exit_date = max(hold_exit, ev_exit)
        d["positions"][sym] = {**p, "units": units, "entry_px": float(q["px"]), "entry_ccy": q.get("ccy"),
                               "entry_fx_usd": float(q.get("fx_usd") or 1.0), "entry_px_usd": px_usd,
                               "entry_utc": _utc(), "cost_usd": cost, "entry_fee_usd": fee,
                               "mark_usd": px_usd, "unrealised_usd": 0.0,
                               "planned_exit_date": exit_date.isoformat(),
                               "reread_triggered": False}
        del d["pending"][sym]
        if verbose:
            print("  FILLED  %-10s %s units @ %s %s ($%.4f) = $%s   exit planned %s"
                  % (sym, units, q["px"], q.get("ccy"), px_usd, format(cost, ",.2f"), exit_date))
    # 2. MARK and manage open positions
    unreal = 0.0
    for sym in list(d["positions"]):
        pos = d["positions"][sym]
        q = _live(sym)
        if q:
            pos["mark_usd"] = float(q["px_usd"]); pos["mark_utc"] = _utc(); pos["mark_stale"] = False
        else:
            pos["mark_stale"] = True
        u = (pos["mark_usd"] - pos["entry_px_usd"]) * pos["units"]
        pos["unrealised_usd"] = round(u, 2); unreal += u
        dd = pos["mark_usd"] / pos["entry_px_usd"] - 1.0
        if dd <= REREAD_DRAWDOWN and not pos.get("reread_triggered"):
            pos["reread_triggered"] = True; pos["reread_due_utc"] = _utc()
            print("  RE-READ %-10s at %+.1f%% — thesis re-read due within 5 sessions (no price stop)" % (sym, 100 * dd))
        ex = _dt.date.fromisoformat(pos["planned_exit_date"])
        if _today() > ex and not pos.get("mark_stale"):
            pnl = round(u, 2); fee = round(pos["mark_usd"] * pos["units"] * 0.0010, 2)
            d["cash"] = round(d["cash"] + pos["mark_usd"] * pos["units"] - fee, 2)
            d["realised_usd"] = round(d["realised_usd"] + pnl, 2)
            d["costs_paid_usd"] = round(d["costs_paid_usd"] + fee, 2)
            d["events_completed"] += 1
            d["closed"].append({**pos, "exit_utc": _utc(), "exit_px_usd": pos["mark_usd"],
                                "pnl_usd": pnl, "reason": "planned-exit (hold %dd / event+%d)" % (HOLD_MIN_TD, EVENT_EXIT_TD)})
            del d["positions"][sym]; unreal -= u
            if verbose:
                print("  EXIT    %-10s planned date reached  P&L $%+.2f" % (sym, pnl))
    d["unrealised_usd"] = round(unreal, 2)
    d["equity"] = round(d["cash"] + sum(p["mark_usd"] * p["units"] for p in d["positions"].values()), 2)
    # cash yield on idle cash, same as every other book (CM-068)
    try:
        import cash_yield_books as _cyb
        _cyb.accrue_book(d, "Dated-Event Long Book", verbose=verbose)
    except Exception as _e:                                 # noqa: BLE001
        print("  CASH-YIELD skipped: %s" % _e)
    d["equity"] = round(d["cash"] + sum(p["mark_usd"] * p["units"] for p in d["positions"].values()), 2)
    save(d)
    return d


def status():
    d = load()
    if d is None:
        print("dated_event_book: not created"); return
    print("=" * 70)
    print("DATED-EVENT LONG BOOK   [%s]   PAPER ONLY" % _utc())
    print("=" * 70)
    print("  equity    $%s   (seed $%s from convexity sleeve)" % (format(d.get("equity", d["cash"]), ",.2f"), format(d["seed_usd"], ",.0f")))
    print("  cash      $%s   realised $%s   costs $%s" % (format(d["cash"], ",.2f"), format(d["realised_usd"], ",.2f"), format(d["costs_paid_usd"], ",.2f")))
    print("  events completed %d / %d for verdict" % (d["events_completed"], KILL_EVENTS))
    for s, p in d["pending"].items():
        print("  PENDING  %-9s $%s  event %s  fills %s open" % (s, format(p["ticket_usd"], ",.0f"), p["event_date"], p["fill_session"]))
    for s, p in d["positions"].items():
        print("  OPEN     %-9s %s @ %s %s  mark $%.4f  %+.2f%%  event %s  exit %s" %
              (s, p["units"], p["entry_px"], p.get("entry_ccy"), p["mark_usd"],
               100 * (p["mark_usd"] / p["entry_px_usd"] - 1), p["event_date"], p["planned_exit_date"]))


if __name__ == "__main__":
    _book = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", "dated_event_book.json")
    if not os.path.exists(_book):
        raise SystemExit(0)  # RETIRED 2026-09-19 — do not resurrect
    ap = argparse.ArgumentParser()
    ap.add_argument("--tick", action="store_true")
    a = ap.parse_args()
    tick() if a.tick else status()
