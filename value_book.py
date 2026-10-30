#!/usr/bin/env python3
"""Value Book — pre-registered global value/quality paper portfolio.

Rules frozen in VALUE_BOOK_PREREGISTRATION.md (2026-08-06). Editing them
invalidates the registration. Top-10 global composite value/quality names,
ex-traps, equal weight, quarterly rebalance, $50k paper, benchmark VT.

CURRENCY (fixed 2026-08-16, accounting only — NOT a rule change):
    The registration says "$50,000 (USD paper; FX unmodelled and noted)". FX is
    now modelled. Until today this book sized each name as equity/N divided by
    the RAW LOCAL price and valued it as units x local price, then summed ten
    currencies as though all were dollars: 005930.KS quotes in KRW (5,000 KRW ~
    US$3.60 was booked as $5,000), BARC.L quotes in PENCE not pounds (5,000 GBp
    ~ US$67 booked as $5,000), BNP.PA in EUR. Every price is now translated to
    USD (see fx.py) for BOTH sizing and valuation, and the currency and rate used
    are persisted with the book so any figure can be audited afterwards.
    Selection and the 63-trading-day rebalance schedule are untouched.

    Positions opened before the fix keep their historical unit counts — history
    is not retro-edited — but they are now VALUED correctly, so equity restates.

    If a name's currency or rate cannot be obtained, the book REFUSES to size or
    value it and says so. It never falls back to "assume USD".

PAPER ONLY. No broker, no credentials, no real orders.

Usage:
    python3 value_book.py             # tick (rebalances only when 63 TD due)
    python3 value_book.py --status
    python3 value_book.py --force     # force rebalance (first tick)
"""
import argparse
import json
import os
import time
import warnings

import fx

warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
BOOK = os.path.join(BASE, "reports", "value_book.json")
LEDGER = os.path.join(BASE, "reports", "value_book_ledger.jsonl")
EQUITY_CSV = os.path.join(BASE, "reports", "value_book_equity.csv")
SCREEN_JSON = os.path.join(BASE, "reports", "value_screen.json")

TOP_N = 10
REBALANCE_TD = 63
COST_BPS_SIDE = 10.0
START_CASH = 50000.0
SCREEN_MAX_AGE_SEC = 6 * 3600     # reuse a screen run from the last 6h
# Non-negotiable #7 (funder directive 2026-08-22): a name whose latest bar is
# older than this many trading days is REFUSED for fills — the 10d ffill must
# never price a halted name at its pre-halt close.
MAX_FILL_AGE_TDAYS = 2



def _firm_halt_blocks(b, want, have, sym):
    """CM-047 firm halt clamp — see FIRM_RISK_AMENDMENT.md. Under a firm-wide
    HALT_NEW_ENTRIES a target may not EXCEED what is already held: new names and
    add-tos refused, reductions and full exits untouched. Fails OPEN."""
    try:
        import book_kill as _kill
        ok, why = _kill.firm_entries_allowed(b.get("name", ""), verbose=False)
    except Exception:                               # noqa: BLE001 - fail OPEN
        return want
    if ok or want <= have:
        return want
    print("   HALT-REFUSED %s: %s target $%.0f clamped to held $%.0f — %s"
          % (b.get("name", "value_book"), sym, want, have, why))
    b.setdefault("halt_refusals", []).append({"sym": sym, "want": round(want, 2),
                                              "clamped_to": round(have, 2),
                                              "reason": why})
    b["halt_refusals"] = b["halt_refusals"][-50:]
    return have


def _tdays_since(ts):
    """Trading days (Mon-Fri, UTC dates) strictly after the bar's date."""
    import datetime as dt
    d = ts.date() if hasattr(ts, "date") else ts
    today = dt.datetime.now(dt.timezone.utc).date()
    n = 0
    while d < today:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def _log(ev):
    ev["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "a") as f:
        f.write(json.dumps(ev) + "\n")


def load_book():
    try:
        with open(BOOK) as f:
            return json.load(f)
    except Exception:
        return {"cash": START_CASH, "holdings": {}, "last_rebalance_date": None,
                "rebalances": 0, "costs_paid": 0.0, "peak_equity": START_CASH,
                "registered": "2026-08-06"}


def save_book(b):
    b["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = BOOK + ".tmp"
    with open(tmp, "w") as f:
        json.dump(b, f, indent=2)
    os.replace(tmp, BOOK)


def current_picks():
    """Top-10 ex-trap from the screen, re-running it if the cached one is stale."""
    fresh = False
    try:
        age = time.time() - os.path.getmtime(SCREEN_JSON)
        fresh = age < SCREEN_MAX_AGE_SEC
    except OSError:
        pass
    if not fresh:
        import global_value_screen as gvs
        payload = gvs.screen(verbose=False)
    else:
        with open(SCREEN_JSON) as f:
            payload = json.load(f)
    picks = [r for r in payload["results"] if not r["value_trap_flag"]][:TOP_N]
    return [r["ticker"] for r in picks], picks


def fetch_closes(tickers):
    """Fresh 10d fetch; returns (closes, asof, stale) where `stale` maps each
    name whose LATEST OWN bar is older than MAX_FILL_AGE_TDAYS trading days to
    a refusal reason (fix 2026-08-22, funder directive Non-negotiable #7: the
    ffill previously priced a halted name at its pre-halt close silently).
    Stale names are excluded from `closes` so the existing refusal machinery
    handles them; the refusal is also printed loudly here."""
    import yfinance as yf
    raw = yf.download(tickers, period="10d", interval="1d", auto_adjust=True,
                      progress=False, threads=True)["Close"]
    if hasattr(raw, "iloc"):
        last = raw.ffill().iloc[-1]
        closes, stale = {}, {}
        for t in tickers:
            if t not in last.index or last[t] != last[t]:
                continue
            try:
                col = raw[t].dropna()
                age = _tdays_since(col.index[-1]) if len(col) else 10 ** 6
            except Exception:
                age = 10 ** 6
            if age > MAX_FILL_AGE_TDAYS:
                stale[t] = ("last bar %d trading days old (limit %d) — refusing"
                            " to fill at a stale close"
                            % (min(age, 999999), MAX_FILL_AGE_TDAYS))
                print("value_book: REFUSED %s — %s" % (t, stale[t]))
                continue
            closes[t] = float(last[t])
        return closes, raw.index[-1].strftime("%Y-%m-%d"), stale
    return {}, time.strftime("%Y-%m-%d"), {}


def _provider_ccy(t):
    """Ask the data provider for a currency ONLY where the suffix table can't.

    The suffix table is authoritative where it has an entry — it knows .L prints
    in pence, which providers routinely report as 'GBP'.
    """
    sfx = fx.suffix_of(t)
    if sfx and sfx not in fx.SUFFIX_CCY and t not in fx.OVERRIDES:
        return fx.yf_currency(t)
    return None


def price_book(tickers, closes=None, rates=None):
    """Local closes translated to USD, with the audit trail for every name.

    Returns a dict with usd_px (only names we can honestly value), local_px, the
    quote currency and the USD-per-quoted-unit rate actually used per name, and
    `refused` — {ticker: reason} for every name we will NOT size or value.
    Nothing missing is ever silently treated as USD or as zero.
    """
    tickers = list(dict.fromkeys(tickers))
    stale_px = {}
    if closes is None:
        closes, asof, stale_px = fetch_closes(tickers)
    else:
        closes, asof = dict(closes), time.strftime("%Y-%m-%d")

    ccy_map, unknown = fx.currencies_needed(tickers, info_lookup=_provider_ccy)
    fx_asof = None
    if rates is None:
        rates, fx_asof = fx.fetch_usd_rates(set(ccy_map.values()))

    usd_px, used, refused = {}, {}, {}
    for t in tickers:
        if t in unknown:
            refused[t] = "quote currency unknown — refusing to assume USD"
            continue
        if t in stale_px:
            refused[t] = stale_px[t]
            continue
        if t not in closes:
            refused[t] = "no price"
            continue
        try:
            rate = fx.usd_per_quote_unit(ccy_map[t], rates)
        except fx.FXUnavailable as e:
            refused[t] = str(e)
            continue
        usd_px[t] = float(closes[t]) * rate
        used[t] = rate
    return {"usd_px": usd_px, "local_px": closes, "ccy": ccy_map, "rate": used,
            "refused": refused, "asof": asof, "fx_asof": fx_asof, "rates": rates}


def equity_of(b, usd_px):
    """Book equity in USD. Raises FXUnavailable if ANY holding cannot be valued.

    A partial sum is a wrong number wearing a right number's clothes, so this
    refuses rather than dropping the names it cannot translate.
    """
    missing = sorted(t for t in b["holdings"] if t not in usd_px)
    if missing:
        raise fx.FXUnavailable("cannot value in USD: " + ", ".join(missing))
    return b["cash"] + sum(u * usd_px[t] for t, u in b["holdings"].items())


def tick(force=False, verbose=True):
    b = load_book()
    # ---- cash yield amendment (CASH_YIELD_AMENDMENT.md, funder-directed
    # 2026-08-27, "GO FOR IT"). Idle cash accrues the live 13-week T-bill rate
    # (^IRX), prorated by ACTUAL elapsed time, FORWARD ONLY from this book's
    # first accrual — never retroactive. Credited BEFORE the frozen rebalance
    # logic below so equity_of() values it correctly; not one byte of the
    # rebalance rule changed. cash_interest_accrued is kept as a SEPARATE field
    # so the November review can always split STRATEGY return from INTEREST
    # return. Fail-safe: no rate / bad rate / stale >24h accrues ZERO, loudly.
    # Never breaks the tick.
    try:
        import cash_yield
        _cy = cash_yield.accrue(b, b.get("cash", 0.0))
        if _cy:
            b["cash"] = b.get("cash", 0.0) + _cy
    except Exception as e:
        msg = "CASH-YIELD ERROR: value_book: " + str(e)[:200]
        print(msg)
        b["cash_yield_error"] = {"msg": msg, "at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    # trading-day gap approximated by calendar days * 5/7
    due = force or b["last_rebalance_date"] is None
    if not due and b["last_rebalance_date"]:
        days = (time.time() - time.mktime(
            time.strptime(b["last_rebalance_date"], "%Y-%m-%d"))) / 86400.0
        due = days * (5.0 / 7.0) >= REBALANCE_TD

    action = None
    if due:
        ticks, picks = current_picks()
        pb = price_book(list(set(ticks) | set(b["holdings"])))
        usd, asof = pb["usd_px"], pb["asof"]
        usable = [t for t in ticks if t in usd]
        held_unpriceable = sorted(t for t in b["holdings"] if t not in usd)
        if held_unpriceable:
            # We cannot know what we are worth, so we cannot know what to deploy.
            action = ("rebalance REFUSED — cannot value in USD: %s (%s)"
                      % (", ".join(held_unpriceable),
                         "; ".join("%s: %s" % (t, pb["refused"].get(t, "?"))
                                   for t in held_unpriceable)))
            _log({"event": "REBALANCE_REFUSED", "unvaluable": held_unpriceable,
                  "refused": pb["refused"]})
        elif len(usable) >= 6:
            eq = equity_of(b, usd)
            per = eq / len(usable)
            traded = 0.0
            new_hold = {}
            for t in usable:
                want = per                                   # USD
                have = b["holdings"].get(t, 0.0) * usd[t]     # USD
                want = _firm_halt_blocks(b, want, have, t)    # CM-047
                if want <= 0:
                    continue
                traded += abs(want - have)
                new_hold[t] = want / usd[t]                   # units, USD-sized
            for t, u in b["holdings"].items():
                if t not in new_hold:
                    traded += u * usd[t]
            cost = traded * COST_BPS_SIDE / 10000.0
            b["holdings"] = new_hold
            # bookkeeping only (not a rule change): entry prices for live P&L.
            # entry_px stays in the LOCAL quote currency (other desk modules mark
            # against local closes); entry_px_usd is the translated twin.
            b["entry_px"] = {t: pb["local_px"][t] for t in new_hold}
            b["entry_px_usd"] = {t: usd[t] for t in new_hold}
            b["cash"] = eq - sum(u * usd[t] for t, u in new_hold.items()) - cost
            b["costs_paid"] = round(b.get("costs_paid", 0.0) + cost, 2)
            b["last_rebalance_date"] = asof
            b["rebalances"] = b.get("rebalances", 0) + 1
            b["last_picks"] = [{"ticker": r["ticker"], "name": r["name"],
                                "country": r.get("country"),
                                "composite": r["composite"]}
                               for r in picks if r["ticker"] in usable]
            _log({"event": "REBALANCE", "held": usable,
                  "equity": round(eq, 2), "cost": round(cost, 2),
                  "ccy": {t: pb["ccy"].get(t) for t in new_hold},
                  "fx_usd_per_unit": {t: pb["rate"].get(t) for t in new_hold},
                  "fx_asof": pb["fx_asof"], "refused": pb["refused"]})
            action = "REBALANCED into %d names (cost $%.2f)" % (len(usable), cost)
        else:
            action = ("rebalance skipped — only %d/%d picks were priceable in USD"
                      % (len(usable), TOP_N))
        if pb["refused"]:
            action += "  [refused: %s]" % ", ".join(
                "%s (%s)" % (t, r) for t, r in sorted(pb["refused"].items()))
    else:
        pb = (price_book(list(b["holdings"])) if b["holdings"]
              else {"usd_px": {}, "local_px": {}, "ccy": {}, "rate": {},
                    "refused": {}, "asof": None, "fx_asof": None, "rates": {}})
        usd = pb["usd_px"]

    # ---- exit axis. While disabled it does bookkeeping only (not a rule
    # change): entry_sigma frozen from settled LOCAL daily closes (matching
    # entry_px), peak_close per symbol from settled DAILY closes (never the
    # intraday mark), peak_tracking_since (UTC).
    # DECISIONS are a NO-OP unless reports/exit_axis_config.json
    # enables value_book — that is a rule change needing a funder-signed
    # amendment). Basis is LOCAL settled closes (matches entry_px); a name
    # whose FX is refused is never exited. Loud on failure, never breaks the
    # tick, never touches b["equity"] (which can be None).
    try:
        import exit_axis
        xa_act = exit_axis.value_exit_check(b, pb, COST_BPS_SIDE, _log)
        if xa_act:
            action = (action + "; " + xa_act) if action else xa_act
    except Exception as e:
        msg = "EXIT-AXIS ERROR: value_book: %s" % str(e)[:200]
        print(msg)
        b["exit_axis_error"] = {"msg": msg, "at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    # ---- valuation: USD or nothing. Never a partial sum dressed as a total. ----
    if b["holdings"]:
        b["equity_ccy"] = "USD"
        b["holding_ccy"] = {t: pb["ccy"].get(t) for t in b["holdings"]}
        b["fx_usd_per_unit"] = {t: pb["rate"].get(t) for t in b["holdings"]}
        b["fx_asof"] = pb["fx_asof"]
        b["mark_px_local"] = {t: pb["local_px"].get(t) for t in b["holdings"]}
        b["mark_px_usd"] = {t: usd.get(t) for t in b["holdings"]}
        try:
            eq = equity_of(b, usd)
        except fx.FXUnavailable as e:
            b["equity"] = None
            b["equity_status"] = "UNAVAILABLE — %s" % e
            b["equity_unavailable_detail"] = dict(pb["refused"])
            _log({"event": "VALUATION_UNAVAILABLE", "reason": str(e),
                  "refused": pb["refused"]})
            if verbose:
                print("!! VALUE BOOK EQUITY UNAVAILABLE — %s" % e)
        else:
            prior_eq, prior_peak = b.get("equity"), b.get("peak_equity")
            b["equity"] = round(eq, 2)
            b["equity_status"] = "ok"
            b.pop("equity_unavailable_detail", None)
            b["last_valued_equity"] = round(eq, 2)
            b["last_valued_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            if not b.get("fx_restated_at"):
                # One-time change of measurement basis: the old peak was measured
                # in the mixed-currency unit and is not comparable to a USD
                # equity. Carrying it forward would print a drawdown the book
                # never suffered — and could trip the registration's 40% review
                # trigger on an accounting artefact. Rebase the peak here, keep
                # the old numbers on the record, and never retro-edit history.
                b["fx_restated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                b["fx_restatement_note"] = (
                    "FX translation added 2026-08-16 (fx.py). Prior equity/peak "
                    "were sums of mixed currencies taken as USD; restated to true "
                    "USD. Units held are unchanged — history is not retro-edited. "
                    "Drawdown is measured from the restated peak onward.")
                b["pre_fx_equity"] = prior_eq
                b["pre_fx_peak_equity"] = prior_peak
                b["peak_equity"] = eq
                _log({"event": "FX_RESTATEMENT", "pre_fx_equity": prior_eq,
                      "pre_fx_peak_equity": prior_peak, "usd_equity": round(eq, 2),
                      "ccy": b["holding_ccy"], "fx_usd_per_unit": b["fx_usd_per_unit"],
                      "fx_asof": b["fx_asof"]})
                if verbose:
                    print("** VALUE BOOK RESTATED to true USD: $%.2f (was $%s "
                          "on the mixed-currency basis)"
                          % (eq, prior_eq if prior_eq is not None else "—"))
            b["peak_equity"] = max(b.get("peak_equity", eq), eq)
            b["drawdown_pct"] = round((eq / b["peak_equity"] - 1.0) * 100, 2)
            b["review_flag"] = bool(eq / b["peak_equity"] - 1.0 <= -0.40)
            new = not os.path.exists(EQUITY_CSV)
            with open(EQUITY_CSV, "a") as f:
                if new:
                    f.write("date,equity,drawdown_pct\n")
                f.write("%s,%.2f,%.2f\n" % (time.strftime("%Y-%m-%d"), eq,
                                            b["drawdown_pct"]))
    save_book(b)
    if verbose:
        status(b, action)
    return b, action


def status(b=None, action=None):
    b = b or load_book()
    print("VALUE BOOK — pre-registered 2026-08-06, global value/quality top-10  [PAPER ONLY]")
    eq = b.get("equity")
    if eq is None:
        print("  equity      : *** UNAVAILABLE *** %s"
              % b.get("equity_status", "no USD valuation this tick"))
        if b.get("last_valued_equity") is not None:
            print("                last good USD mark $%.2f at %s (stale — NOT the "
                  "book's value now)" % (b["last_valued_equity"],
                                         b.get("last_valued_at")))
    else:
        print("  equity      : $%.2f %s  (start $%.0f)   drawdown %s%%%s"
              % (eq, b.get("equity_ccy") or "USD", START_CASH,
                 b.get("drawdown_pct", 0.0),
                 "   *** REVIEW: DD > 40% ***" if b.get("review_flag") else ""))
    if not b.get("fx_restated_at"):
        print("                (PRE-FX BASIS — this figure sums local prices in "
              "several currencies as though they were dollars; it restates to "
              "true USD on the next tick)")
    if b.get("fx_restated_at"):
        print("  fx basis    : USD-translated since %s (was mixed-currency $%s) "
              "— rates as of %s"
              % (b["fx_restated_at"], b.get("pre_fx_equity"), b.get("fx_asof")))
    ccy = b.get("holding_ccy") or {}
    rate = b.get("fx_usd_per_unit") or {}
    for p in b.get("last_picks", []):
        t = p["ticker"]
        c = ccy.get(t)
        if not ccy:
            fxs = ""                       # book not yet valued on the FX basis
        elif c and rate.get(t):
            fxs = "%s @ %.6g USD/unit" % (c, rate[t])
        else:
            fxs = (c or "ccy ?") + " — NO RATE"
        print("    %-9s %-28s %-11s comp %.3f  %s"
              % (t, p["name"][:28], (p.get("country") or "?")[:11],
                 p["composite"], fxs))
    print("  rebalances  : %d   costs paid $%.2f   last %s"
          % (b.get("rebalances", 0), b.get("costs_paid", 0.0),
             b.get("last_rebalance_date")))
    if action:
        print("  this tick   : " + action)
    print("  registration: VALUE_BOOK_PREREGISTRATION.md — benchmark VT — eval 2028-08-06")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if args.status:
        status()
    else:
        tick(force=args.force)


if __name__ == "__main__":
    main()
