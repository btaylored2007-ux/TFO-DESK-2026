#!/usr/bin/env python3
"""fundamental_book.py — TFO FUNDAMENTAL DESK. PAPER ONLY.

WHAT THIS IS
------------
The $500,000 NEW funder-directed book from the global fundamental value screen of
2026-08-27 (`reports/research_global_value_screen_2026-08-27.md`). It is additional
paper capital: it does NOT resize, reweight or touch any of the firm's other 27
books, and this file cannot — it reads their ledgers not at all.

BASIS
-----
Funder-directed 2026-08-27. Registers as a documented exception on the
`rally_rider` precedent (live 2026-08-20), NOT via `quant_validation.py`. The
standing of the evidence, stated here so it can never be mistaken for something
it is not: this book rests on a cross-sectional fundamental screen with a dated
catalyst overlay. NO backtest, NO search-deflation, NO purged/embargoed CV, NO
kill-tests. It carries no validated edge. Its honest claim is 326 names screened
by a disciplined method with every reject retained, producing 8 long and 51 short
opinions each with a dated reason and a named disconfirming condition.

Search count, which travels with every future claim touching this dataset:
326 screened / 59 directional / 8 undervalued.

WHY THE ENTRY PRICE IS THE ONLY THING THIS FILE INVENTS, AND IT DOESN'T
----------------------------------------------------------------------
Positions open at the price the market actually shows on the day they open, taken
live. A book that back-dates its entries to the screen date would manufacture P&L
that no execution could have captured — the exact failure the firm's own
`value_screen.py` docstring warns about for backtests. So: entry price is fetched,
stamped and frozen; if a name cannot be priced it does NOT open, and it is
recorded as unopened with the reason.

MARKS ARE LIVE OR THEY ARE ABSENT
---------------------------------
A position that cannot be marked is flagged `mark_stale` and carries its LAST
GOOD mark forward, never its cost, and never zero. The book publishes
`n_unmarked` and `unmarked_usd` every tick. Valuing an unmarkable position at
cost is how a book reports thin-air P&L (see the CoinSpot floor, and defect #5).

SHORTS
------
51 of 58 positions are shorts, at $150,000 gross. The firm's own 14-year shorts
study found the same signal expressed as CASH beat expressing it as SHORT by
~15pp/yr, and the firm has not run naked shorts outside a registered rule. This
book does. That is the funder's directed departure and it is recorded here, in
the registration, and on the dashboard rather than left implicit — so the forward
record measures it honestly. A short's P&L is (entry - mark) * units.

Borrow, short interest, options liquidity and implied volatility were NOT
assessed for any of the 51 shorts. No borrow cost is modelled. That is a real
omission, it flatters the short book, and it is published as
`borrow_cost_modelled: false` rather than hidden.

THE KILL RULE IS IMPORTED, NEVER COPIED
---------------------------------------
`book_kill.py` supplies the book-level kill rule so this book can never drift
from the tournament sleeves and the four floors on what "killed" means. A frozen
book opens no new position and STILL EXITS NORMALLY.

PAPER ONLY. Reads a screen artefact and price data; writes one ledger, one CSV
and one report. Holds no credentials, places no order, connects to no broker.

Usage:  python3 fundamental_book.py [--open] [--status]
"""
import argparse

import desk_lock
import csv
import json
import math
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
try:
    import fundamental_borrow as _borrow
    _BORROW_ERR = None
except Exception as _e:                       # noqa: BLE001
    _borrow, _BORROW_ERR = None, "%s: %s" % (type(_e).__name__, _e)
    print("   BORROW MODEL UNAVAILABLE (%s) — shorts will be FLATTERED; the 5%% "
          "gate is measuring the wrong number this tick" % _BORROW_ERR)

try:
    import fundamental_exits as _exits
    _EXIT_ERR = None
except Exception as _e:                       # noqa: BLE001 - fail-closed on TRADING
    _exits, _EXIT_ERR = None, "%s: %s" % (type(_e).__name__, _e)
    print("   TRADING RULE UNAVAILABLE (%s) — the book will MARK but not TRADE; "
          "no exit can fire this tick" % _EXIT_ERR)

try:
    import book_kill as _kill
    _KILL_ERR = None
except Exception as _e:                       # noqa: BLE001 - reported, fail-closed
    _kill, _KILL_ERR = None, "%s: %s" % (type(_e).__name__, _e)
    print("   BOOK KILL RULE UNAVAILABLE (%s) — NEW entries refused; exits unaffected"
          % _KILL_ERR)

SCREEN = "/Users/test/prospect/var/global_run/aggregate.json"
LEDGER = os.path.join(BASE, "reports", "fundamental_book.json")
EQUITY_CSV = os.path.join(BASE, "reports", "fundamental_equity.csv")
BOOK_LABEL = "TFO Fundamental Desk"
SEED = 500_000.0
REGISTRATION = ("reports/research_global_value_screen_2026-08-27.md "
                "(funder-directed 2026-08-27, documented exception)")

# Named in §9.4 of the handover: their own desks said do not act until a specific
# document is read. Opening them anyway would be acting on evidence the analyst
# explicitly disclaimed, so they are held and their capital stays cash.
HOLD_UNTIL_READ = {
    "0688.HK": "H1 2026 released 2026-08-26; figures could not be obtained. Desk "
               "marked it unactionable in either direction until read.",
    "1301.TW": "Reported P/B 0.699 unreconciled against 1.029 computed from the "
               "same provider's own balance sheet. Resolve against the filed 2Q26 "
               "balance sheet first.",
}

CSV_HEADER = ("utc,equity_usd,cash_usd,long_mv_usd,short_mv_usd,unrealised_usd,"
              "n_long,n_short,n_unmarked,drawdown_pct\n")


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) and f > 0 else None
    except (TypeError, ValueError):
        return None


_FX_CACHE = {}


def fx_to_usd(ccy, throttle=0.3):
    """USD per 1 unit of `ccy`. Returns None rather than a guess.

    THIS FUNCTION EXISTS BECAUSE THE FIRST BUILD OF THIS FILE DID NOT HAVE IT.
    Sizing ran `units = notional_usd / local_price`, which treats a yen price as
    dollars: Denso sized at 21 units for a claimed $40,000 position that was
    really worth about $260, and every non-USD name in the book was wrong the
    same way. That is hazard §3(c) from the run's own data-hazard catalogue —
    currency units mixed inside one calculation — walked into by the engine that
    was built to act on the catalogue. Every price that touches a dollar figure
    is converted here, at both entry and mark, or the position does not open.

    GBp is the London trap the UK desk measured: quotes are in PENCE while the
    currency code says GBP-ish. One hundred pence to the pound, applied here once
    rather than in every caller.

    DEFECT FIX 2026-08-28: that last paragraph was aspirational. The division was
    NOT applied here — `ccy.upper()` folded "GBp" into "GBP" on the first line,
    `pence = (ccy == "GBP" and False)` was dead code that could never be True,
    and the function returned the POUND rate for a PENCE-quoted code. Only
    `fetch_prices()` below survived it, by dividing the price itself; every other
    caller that believed this docstring was wrong by 100x:

      * `fundamental_spec_open_queued.live_measure()` computes
        `adv = volume * px * fx_usd` with a PENCE px and this POUND rate, so the
        LIQUIDITY SCREEN read every London name as 100x more liquid than it is.
        The spec sleeve holds POLB.L, SRB.L and FDEV.L, all admitted through it.
      * the `mark_fx_usd` / `entry_fx_usd` this book publishes per position were
        the pound rate against a pence `mark`, which is how the firm risk report
        re-marked WPP.L at $395,156 against a stored $3,949.

    It now does what it always claimed: the returned factor is USD per ONE UNIT
    OF THE QUOTED PRICE, so `px * fx_to_usd(ccy)` is dollars for every ccy. The
    pence codes are matched CASE-SENSITIVELY and cached under their own key —
    "GBp".upper() == "GBP", so folding case first would have made pence and
    pounds share a cache entry and collide.
    """
    if not ccy:
        return None
    # Case-sensitive, BEFORE any upper(): these are minor units (pence), and the
    # only thing distinguishing them from their major unit is the letter case.
    if ccy in ("GBp", "GBX", "GBp".lower()):
        key = "GBp"
        if key in _FX_CACHE:
            return _FX_CACHE[key]
        major = fx_to_usd("GBP", throttle=throttle)
        if major is None:
            return None
        _FX_CACHE[key] = major / 100.0
        return _FX_CACHE[key]
    ccy = ccy.upper()
    if ccy == "USD":
        return 1.0
    if ccy in _FX_CACHE:
        return _FX_CACHE[ccy]
    import yfinance as yf
    base = "GBP" if ccy in ("GBP", "GBX") else ccy
    rate = None
    try:
        h = yf.Ticker("%sUSD=X" % base).history(period="5d")
        if len(h):
            rate = float(h["Close"].iloc[-1])
    except Exception:                          # noqa: BLE001 - reported by caller
        rate = None
    if rate is not None and rate > 0:
        _FX_CACHE[ccy] = rate
    time.sleep(throttle)
    return _FX_CACHE.get(ccy)


def fetch_prices(symbols, throttle=0.35, require_executable=True):
    """Timestamped native quotes + FX. Entries require a current regular session.

    Valuation callers explicitly allow closed/delayed/pre-market observations;
    those observations retain their actual source clock and cannot enable exits.
    """
    from tfo_equity_refresh import collect_quotes
    from paper_execution_price import eligible
    try:   # paid-first (HT 2026-10-30): Massive US / FMP ASX-LSE-JP-HK, Yahoo fallback per symbol
        from paid_feeds import fetch_quote as _paid_fetch
    except Exception:
        _paid_fetch = None
    native_quotes, failed = collect_quotes(symbols, fetcher=_paid_fetch)
    prices = {}
    for symbol, q in native_quotes.items():
        if q.get("px_usd") is None:
            continue
        if require_executable and not eligible(q):
            failed[symbol] = "No fresh regular-session execution quote: %s at %s; FX %s" % (q.get("status"), q.get("quote_utc"), q.get("fx_status"))
            continue
        prices[symbol] = q
    return prices, failed


def quote_metadata(q):
    """Keep price time distinct from fetch time, and fail closed on execution."""
    from paper_execution_price import eligible
    return {"mark_utc": q.get("quote_utc"), "mark_quote_utc": q.get("quote_utc"),
            "mark_checked_utc": q.get("fetched_at"),
            "mark_stale": not eligible(q),
            "mark_paper_executable": q.get('paper_executable') is True,
            "mark_price_class": q.get('paper_price_class'),
            "mark_quote_current": q.get("quote_current", False),
            "mark_session": q.get("session", "unknown"),
            "mark_price_session": q.get("price_session", q.get("session", "unknown")),
            "mark_executable": q.get("executable") is True,
            "mark_fx_quote_utc": q.get("fx_quote_utc"),
            "mark_fx_status": q.get("fx_status", "source_time_unknown"),
            "mark_fx_source": q.get("fx_source"),
            "mark_note": q.get("status", "source timestamp unverified"),
            "mark_source": q.get("source", "Yahoo chart")}


def load_ledger():
    try:
        with open(LEDGER) as f:
            return json.load(f)
    except Exception:
        return None


def save_ledger(d):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    tmp = LEDGER + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2, default=str)
    os.replace(tmp, LEDGER)


def open_book(verbose=True):
    """Open every position at a LIVE price. Idempotent: refuses to re-open."""
    if load_ledger():
        print("!! fundamental_book: ledger already exists — refusing to re-open.")
        print("   Re-opening would overwrite frozen entry prices and destroy the "
              "forward record. Delete the ledger deliberately if that is intended.")
        return None
    with open(SCREEN) as f:
        scr = json.load(f)

    want = [(s, v, "long") for s, v in scr["long_alloc"].items()] + \
           [(s, v, "short") for s, v in scr["short_alloc"].items()]
    syms = [s for s, _, _ in want]
    print("TFO FUNDAMENTAL DESK — opening %d positions at live prices" % len(syms))
    prices, failed = fetch_prices(syms)

    positions, unopened, deployed = {}, {}, 0.0
    for sym, notional, side in want:
        if sym in HOLD_UNTIL_READ:
            unopened[sym] = {"side": side, "notional": notional,
                             "reason": "HELD: " + HOLD_UNTIL_READ[sym]}
            continue
        p = prices.get(sym)
        if not p:
            unopened[sym] = {"side": side, "notional": notional,
                             "reason": "NOT OPENED — no live price (%s)"
                                       % failed.get(sym, "unknown")}
            continue
        units = notional / p["px_usd"]        # px_usd, never the native quote
        positions[sym] = {
            "side": side, "notional_usd": round(notional, 2),
            "entry_px": p["px"], "entry_ccy": p["ccy"],
            "entry_fx_usd": p["fx_usd"], "entry_px_usd": p["px_usd"],
            "units": units, "entry_utc": _utc(),
            "mark": p["px"], "mark_usd": p["px_usd"], "mark_fx_usd": p["fx_usd"],
            "mark_utc": _utc(), "mark_stale": False, "unrealised_usd": 0.0,
        }
        deployed += notional

    cash = SEED - deployed
    led = {
        "book": BOOK_LABEL, "registration": REGISTRATION,
        "paper_only": True, "seed_usd": SEED,
        "opened_utc": _utc(),
        "evidence_standing": ("cross-sectional fundamental screen with a dated "
                              "catalyst overlay; NO backtest, NO deflation, NO CV, "
                              "NO kill-tests; carries no validated edge"),
        # These two are written ONCE at creation. Both went stale and both were
        # then reported as fact: `borrow_cost_modelled: False` produced a
        # firm-wide coverage warning that the short book was "flattered" while
        # borrow was accruing daily inside the gate, and `search_count` still
        # claimed 326 after 22 desks had screened 4,306. A field that is only
        # ever written at creation must say so, or a later reader treats a
        # snapshot as a live measurement.
        "search_count": {"screened": 326, "directional": 59, "undervalued": 8,
                         "written_at": "book creation — refresh me"},
        "borrow_cost_modelled": True,
        "shorts_are_a_directed_departure": (
            "51 of 58 positions are shorts at $150k gross. The firm's 14-year shorts "
            "study found CASH beat SHORT expression by ~15pp/yr and the firm has not "
            "run naked shorts outside a registered rule. Funder-directed exception."),
        "cash": round(cash, 2), "positions": positions,
        "unopened": unopened,
        "price_failures": failed,
        "realised_usd": 0.0, "costs_paid_usd": 0.0,
    }
    save_ledger(led)
    print("   opened %d positions, deployed $%s, cash $%s, held/unopened %d"
          % (len(positions), "{:,.0f}".format(deployed), "{:,.0f}".format(cash),
             len(unopened)))
    return led


BOOK_KEY_FOR_GUARD = "fundamental_book"


def mark(verbose=True):
    # LOCKED across the ENTIRE read-modify-write. Without this, an entry opened
    # during the ~2-minute price fetch is reverted by the write-back with no
    # error — which silently destroyed a confirmed CCP.AX position 2026-08-28.
    with desk_lock.ledger_lock("fundamental"):
        return _mark_locked(verbose)


def _mark_locked(verbose=True):
    """Mark to live prices, compute equity, run the kill rule. Opens nothing."""
    led = load_ledger()
    if not led:
        print("fundamental_book: no ledger — run with --open first")
        return None

    # CM-072 (2026-09-04): apply SOURCED cash distributions before marking.
    # The book had no concept of cash paid to holders, so an ex-dividend price
    # drop was booked as a loss on longs and a gain on shorts — and the dividend
    # a short OWES was never charged. Already firing: WOW.AX, TSN, ANTO.L, IMO.TO
    # (phantom gains on shorts) and ODL.OL (phantom loss on a long).
    # It also made an entire trade class unreachable: liquidations and wind-ups
    # pay by RETURNING CAPITAL, which this book could only read as a price
    # collapse. An adversarial verifier killed a real dissolving-REIT candidate
    # on exactly that — correctly. The gate was right; the defect was ours.
    # Applies ONLY events carrying a source URL (CM-069). No source, no event.
    try:
        import fundamental_distributions as _fd
        _fd.apply_pending(led, verbose=verbose)
    except Exception as _e:                                 # noqa: BLE001
        print("   DISTRIBUTIONS skipped: %s" % _e)

    # CM-068 (2026-09-03, funder-directed): accrue T-bill yield on idle cash.
    # This book held $65,478.96 earning exactly zero. Forward-only from the first
    # tick, never retroactive; a missing or >24h-stale rate accrues ZERO loudly
    # rather than inventing money, and the cumulative figure is kept in its own
    # field so rate income is never mistaken for trading skill.
    try:
        import cash_yield_books as _cyb
        _cyb.accrue_book(led, "TFO Fundamental Desk")
    except Exception as _cy_e:                              # noqa: BLE001
        print("   CASH-YIELD skipped: %s" % _cy_e)
    syms = list(led["positions"])
    prices, failed = fetch_prices(syms, require_executable=False)

    n_unmarked = 0
    unmarked_usd = 0.0
    # 2026-09-29 bad-tick guard (8766.T): this marker wrote a post-split print into mark_usd, which let the rail's
    # price check compare the price with itself. A print >30% from the last good mark (or, with no mark yet, adverse
    # >30% vs the fill) is NOT written: the last good mark is carried and flagged (mark_stale/mark_rejected) and the
    # position counts as unmarked. A split-like ratio first runs the corporate-actions check in memory (no locks: we
    # already hold "fundamental"); if a split is found and applied, the print is re-checked on the new basis.
    ca_actions = []
    rejected = set()
    for sym, p in led["positions"].items():
        q = prices.get(sym)
        if q:
            try:
                import bad_tick_guard as _btg
                _e0 = p.get("rail_orig_entry_usd") or p.get("entry_px_usd")
                why, _ref, ratio = _btg.mark_check(BOOK_KEY_FOR_GUARD, q["px_usd"], p.get("side", "long"), p.get("mark_usd"), _e0)
                if why and ratio:
                    try:
                        import tfo_corporate_actions as _tca, datetime as _dt
                        _acts = _tca.process_book("fundamental_book", led, _dt.datetime.now(_dt.timezone.utc), _tca.yahoo_fetch,
                                                  lambda c, d: (1.0, "unused"), only={sym}, splits_only=True, closed=False)
                        ca_actions.extend(a for a in _acts if a["type"] == "split")
                        _e0 = p.get("rail_orig_entry_usd") or p.get("entry_px_usd")
                        why, _ref, ratio = _btg.mark_check(BOOK_KEY_FOR_GUARD, q["px_usd"], p.get("side", "long"), p.get("mark_usd"), _e0)
                    except Exception as _ca_e:                  # noqa: BLE001
                        why += "; corporate-actions check failed: %s" % _ca_e
                if why:
                    _btg.flag_rejected(p, q.get("px"), q["px_usd"], why, ratio, _utc())
                    if ratio:
                        _btg.request_ca_check("fundamental_book", sym, why, ratio)
                    print("   MARK REJECTED %-10s %s" % (sym, why))
                    rejected.add(sym)
                    n_unmarked += 1
                    unmarked_usd += p["notional_usd"]
                    continue
            except Exception as _g_e:                           # noqa: BLE001
                print("   bad-tick guard unavailable for %s: %s" % (sym, _g_e))
            p["mark"], p["mark_usd"] = q["px"], q["px_usd"]
            p["mark_fx_usd"] = q["fx_usd"]
            p.update(quote_metadata(q))
            p.pop("mark_error", None)
            p.pop("mark_rejected", None)
        else:
            # Carry the LAST GOOD mark. Never cost, never zero.
            p["mark_stale"] = True
            p["mark_error"] = failed.get(sym, "unknown")
            n_unmarked += 1
            unmarked_usd += p["notional_usd"]

    # ---- BORROW, charged before the rule reads net gain -------------------
    # Order matters: the gate tests net_gain(), and net_gain() subtracts accrued
    # borrow on shorts. Accruing after the rule would let a position clear a gate
    # it had not actually cleared.
    if _borrow is not None:
        charged = _borrow.accrue(led, time.time(), _utc())
        if charged:
            led["cash"] = round(led["cash"] - charged, 2)
            led["costs_paid_usd"] = round(led.get("costs_paid_usd", 0.0) + charged, 4)
    else:
        led["borrow_model"] = {"error": _BORROW_ERR,
                               "warning": "shorts flattered; gate is under-measuring"}

    # ---- THE TRADING RULE (funder-directed 2026-08-27) -------------------
    # "Exit fill of MIN 5% over fill and exit cost, and then scaled if it is
    # rallying." Runs AFTER marking so every decision sees a live price, and
    # BEFORE equity is struck so a realised tranche lands in cash this tick.
    # A frozen book still runs this: freezing blocks NEW ENTRIES, never exits.
    decisions, fills = [], []
    import exit_check_evidence as evidence
    evidence.start(led, 'fundamental_book._mark_locked')
    import head_trader_gate as _htg
    if _exits is not None and not _htg.active():
        for sym, p in list(led["positions"].items()):
            from paper_execution_price import chronological, eligible, receipt
            q = prices.get(sym)
            if sym in rejected:
                evidence.record(led, sym, p, 'WAIT_DATA', 'Mark rejected by the bad-tick guard; last good mark carried', quote=quote_metadata(q) if q else None)
                continue
            if q and eligible(q) and not chronological(q, p):
                evidence.record(led, sym, p, 'WAIT_DATA', 'Delayed bar predates entry, repeats an exit bar, or entry clock is unknown', quote=quote_metadata(q))
                continue
            d = _exits.evaluate(p, sym)
            decisions.append(d)
            # CM-049(c): apply() is called on EVERY decision, including holds.
            # evaluate() is deliberately pure (so the rule can be dry-run against
            # the live book), which means the position's PEAK net gain is only
            # persisted by apply(). Calling apply only on trades froze the peak
            # at the last rung: a runner could rally to +40%, fall back to +16%
            # and never trail, because the stored peak was still +15%. On a hold
            # apply() records the peak and returns (0.0, 0.0) — no cash moves.
            units_before = p['units']
            realised, cash_delta = _exits.apply(p, d)
            q = prices.get(sym)
            evidence.record(led, sym, p,
                            ('EXIT' if d['action'] != 'hold' else 'HOLD')
                            if eligible(q) else
                            ('WAIT_MARKET' if q else 'WAIT_DATA'),
                            d.get('reason', ''), decision=d, applied=True,
                            quote=quote_metadata(q) if q else None)
            # CM-051: "stop_out" MUST be in this tuple. apply() had already
            # closed the position while this guard skipped the cash credit, so
            # $17,079 of released short collateral evaporated and book equity
            # fell by the whole notional. Any NEW action that releases units has
            # to be added here — the guard is the credit, not a formality.
            # "catalyst_expired" added 2026-08-28 (Amendment 3). It releases the
            # FULL position, so omitting it here would repeat the CM-051 loss
            # exactly: apply() closes the position, this guard skips the cash
            # credit, and the released collateral evaporates.
            if d["action"] in ("scale_out", "close_runner", "stop_out",
                               "catalyst_expired"):
                ticket = evidence.ticket('fundamental_book', sym, d['action'], p['side'],
                                         units_before - p['units'], p['mark_usd'],
                                         (units_before - p['units']) * p['entry_px_usd'],
                                         cash_delta, realised,
                                         **receipt(q),
                                         cost_source='Registered fundamental exit cost and borrow model')
                p['last_paper_exit_source_utc'] = q.get('quote_utc')
                led['exit_checks']['legs'][sym]['ticket'] = ticket
                for f in p.get("exit_fills", []):
                    if f.get("utc_field") == "set_by_caller":
                        f['ticket_id'] = ticket['ticket_id']
                        f['execution_receipt'] = ticket
                        f["utc_field"] = _utc()
                        f["utc"] = f.pop("utc_field")
                led["cash"] = round(led["cash"] + cash_delta, 2)
                led["realised_usd"] = round(led.get("realised_usd", 0.0) + realised, 2)
                fills.append({"symbol": sym, **{k: d[k] for k in
                              ("action", "release_fraction", "net_gain_pct", "reason")},
                              "realised_usd": round(realised, 2)})
                print("   TRADE %s %s: %s" % (d["action"].upper(), sym, d["reason"]))
        # positions fully released are closed out of the book, with the record kept
        for sym in [s2 for s2, p in led["positions"].items() if p["units"] <= 1e-9]:
            led.setdefault("closed", {})[sym] = led["positions"].pop(sym)
    led["trade_rule"] = (_exits.cost_model() if _exits is not None
                         else {"error": _EXIT_ERR, "trading": "DISABLED"})
    led["trade_decisions"] = decisions
    led["trade_fills_this_tick"] = fills
    evidence.finish(led)

    # Value only the units STILL HELD after this tick's exits. A pre-exit
    # aggregate counts sold stock both here and in the credited cash, creating
    # a one-tick equity/peak spike. Partial shorts likewise must release their
    # realised P&L from unrealised. FX moves remain part of this unhedged book.
    long_mv = short_mv = unreal = 0.0
    for p in led["positions"].values():
        mv = p["units"] * p.get("mark_usd", p["mark"])
        if p["side"] == "long":
            pnl = mv - p["notional_usd"]
            long_mv += mv
        else:
            pnl = p["notional_usd"] - mv
            short_mv += mv
        p["unrealised_usd"] = round(pnl, 2)
        unreal += pnl

    # Equity = cash + long market value + short collateral + short P&L.
    # The short leg is held at its opening notional (the proceeds sit as
    # collateral) and moves only by its P&L, so a short that goes against the
    # book reduces equity exactly as it should.
    short_notional = sum(v["notional_usd"] for v in led["positions"].values()
                         if v["side"] == "short")
    short_pnl = short_notional - short_mv
    equity = led["cash"] + long_mv + short_notional + short_pnl

    # ---- CM-054: CAPITAL IDENTITY GUARD, every tick, no exceptions ----------
    # On 2026-08-29 this book was deploying $600,000 against a recorded $500,000
    # seed. The $100,000 had NO entry in `deployments`, NO entry in `events`, and
    # no seed update — so it was published as +20.11% PERFORMANCE and flowed
    # straight into firm equity. Capital entering a book is a CAPITAL EVENT. It
    # is never a gain, and it must never arrive silently again.
    # Realised P&L includes allocated borrow already charged to cash. Add back
    # only forward receipts using the corrected cash treatment, not legacy fills.
    borrow_in_realised = sum(float(p.get('borrow_released_cash_correct_usd') or 0)
                             for group in ('positions', 'closed')
                             for p in led.get(group, {}).values())
    _idn = (led["seed_usd"] + led.get("realised_usd", 0.0) + borrow_in_realised
            - led.get("costs_paid_usd", 0.0) + unreal
            + led.get("cash_interest_accrued", 0.0))
    _gap = equity - _idn
    led["equity_identity_usd"] = round(_idn, 2)
    led["equity_identity_gap_usd"] = round(_gap, 2)
    if abs(_gap) > max(500.0, 0.005 * (led["seed_usd"] or 1)):
        led["capital_identity_breach"] = {
            "utc": _utc(), "gap_usd": round(_gap, 2),
            "identity_usd": round(_idn, 2), "equity_usd": round(equity, 2),
            "note": ("Equity does not reconcile to seed + realised - costs + "
                     "unrealised + cash interest. Either capital entered without "
                     "being recorded "
                     "as a capital event, or positions were opened without "
                     "debiting cash. The P&L on this book is UNTRUSTWORTHY until "
                     "this is resolved."),
        }
        print("!" * 74)
        print("!! CAPITAL IDENTITY BREACH: $%s unexplained (equity $%s vs identity $%s)"
              % ("{:,.2f}".format(_gap), "{:,.2f}".format(equity),
                 "{:,.2f}".format(_idn)))
        print("!! Capital entering a book is a CAPITAL EVENT, never a gain.")
        print("!! Treat this book's P&L as UNTRUSTWORTHY until reconciled.")
        print("!" * 74)
    else:
        led.pop("capital_identity_breach", None)

    led.update({
        "updated": _utc(), "epoch": time.time(),
        "long_mv_usd": round(long_mv, 2), "short_mv_usd": round(short_mv, 2),
        "short_notional_usd": round(short_notional, 2),
        "unrealised_usd": round(unreal, 2),
        "equity": round(equity, 2),
        "n_long": sum(1 for v in led["positions"].values() if v["side"] == "long"),
        "n_short": sum(1 for v in led["positions"].values() if v["side"] == "short"),
        "n_unmarked": n_unmarked, "unmarked_usd": round(unmarked_usd, 2),
        "price_failures": failed,
    })
    if n_unmarked:
        print("!! %d position(s) UNMARKED this tick ($%s notional) — carrying last "
              "good mark, NOT cost" % (n_unmarked, "{:,.0f}".format(unmarked_usd)))

    if _kill is not None:
        _kill.evaluate(led, equity, BOOK_LABEL, verbose=verbose,
                       net_flow_usd=_kill.net_capital_flow(led))
    else:
        led.setdefault("book_risk", {})["error"] = \
            "book_kill unavailable (%s) — DRAWDOWN UNMEASURED" % _KILL_ERR

    peak = (led.get("book_risk") or {}).get("peak_equity") or equity
    dd = (led.get("book_risk") or {}).get("drawdown_pct")
    if not isinstance(dd, (int, float)):
        dd = (equity / peak - 1.0) * 100.0 if peak else 0.0
    try:
        new = not os.path.exists(EQUITY_CSV)
        with open(EQUITY_CSV, "a") as f:
            if new:
                f.write(CSV_HEADER)
            f.write("%s,%.2f,%.2f,%.2f,%.2f,%.2f,%d,%d,%d,%.4f\n" % (
                led["updated"], equity, led["cash"], long_mv, short_mv, unreal,
                led["n_long"], led["n_short"], n_unmarked, dd))
    except Exception as e:                     # noqa: BLE001
        print("!! could not append the equity series (%s) — the book's drawdown is "
              "only as good as this file" % e)
    save_ledger(led)
    if ca_actions:                  # splits applied by the marker's split-like bad-tick path: log after the save
        try:
            import tfo_corporate_actions as _tca
            _tca.record_actions(ca_actions)
        except Exception as _e:                                 # noqa: BLE001
            print("   corporate-actions log failed: %s" % _e)
    return led


def report(led):
    print("=" * 74)
    print("TFO FUNDAMENTAL DESK   [%s]" % led.get("updated", led.get("opened_utc")))
    print("=" * 74)
    print("  equity            : $%s   (seed $%s)"
          % ("{:,.2f}".format(led.get("equity", 0)), "{:,.0f}".format(led["seed_usd"])))
    print("  cash              : $%s" % "{:,.2f}".format(led["cash"]))
    print("  long  %2d positions: $%s market value"
          % (led.get("n_long", 0), "{:,.0f}".format(led.get("long_mv_usd", 0))))
    print("  short %2d positions: $%s notional"
          % (led.get("n_short", 0), "{:,.0f}".format(led.get("short_notional_usd", 0))))
    print("  unrealised        : $%s" % "{:,.2f}".format(led.get("unrealised_usd", 0)))
    br = led.get("book_risk") or {}
    if br.get("drawdown_pct") is not None:
        print("  drawdown          : %.2f%%  (kill %.1f%%)%s"
              % (br["drawdown_pct"],
                 br.get("kill_drawdown_pct",
                        getattr(_kill, "KILL_DD_PCT", float("nan"))),
                 "  ** FROZEN **" if br.get("frozen") else ""))
    if led.get("n_unmarked"):
        print("  !! UNMARKED       : %d position(s), $%s notional"
              % (led["n_unmarked"], "{:,.0f}".format(led["unmarked_usd"])))
    if led.get("unopened"):
        print("\n  HELD / NOT OPENED (%d):" % len(led["unopened"]))
        for s, v in led["unopened"].items():
            print("    %-10s $%-8s %s" % (s, "{:,.0f}".format(v["notional"]),
                                          v["reason"][:78]))
    # CM-050(a): this read a `borrow_cost_modelled` key nothing ever wrote, so
    # it printed False even with borrow live and charging on 46 shorts. Report
    # what actually happened, with the money.
    if _borrow is not None:
        _bt = led.get("borrow_accrued_total_usd") or 0.0
        _nd = len(led.get("borrow_doubtful") or {})
        print("\n  borrow            : MODELLED — $%s accrued to date across %d "
              "shorts (tiers %s)"
              % ("{:,.2f}".format(_bt),
                 sum(1 for x in led.get("positions", {}).values()
                     if x.get("side") == "short"),
                 ", ".join("%s %.2f%%" % (k, v * 100)
                           for k, v in sorted(
                               (led.get("borrow_model", {}).get(
                                   "tier_rates_annual") or {}).items()))))
        if _nd:
            print("  BORROW DOUBTFUL   : %d name(s) — availability is an EXISTENCE "
                  "question, not a cost one; never presented as executable" % _nd)
    else:
        print("\n  borrow            : NOT MODELLED (%s) — shorts are FLATTERED "
              "and the 5%% gate is under-measuring on that side" % _BORROW_ERR)
    print("  PAPER ONLY")
    print("  ledger -> %s" % LEDGER)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--open", action="store_true", help="open the book (once)")
    ap.add_argument("--status", action="store_true", help="print last state, no fetch")
    a = ap.parse_args()
    if a.status:
        led = load_ledger()
        if not led:
            print("fundamental_book: no ledger yet")
            return
        report(led)
        return
    if a.open:
        led = open_book()
        if led is None:
            return
    led = mark()
    if led:
        report(led)


if __name__ == "__main__":
    main()
