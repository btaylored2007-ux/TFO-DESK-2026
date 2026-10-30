#!/usr/bin/env python3
"""Uniform cash yield on idle capital — the shared accrual module.

Funder-directed amendment, 2026-08-27. The frozen rule, the reasoning and the
sign-off live in CASH_YIELD_AMENDMENT.md. Read that first; this file only
implements it.

WHAT THIS DOES
    Idle cash on this desk was modelled at 0%. That is not neutral: it
    structurally penalises a book that correctly holds cash (dip_rider waiting
    for a flush, capacity_probe refusing fills its book cannot absorb) against a
    book that is always deployed. This module credits idle cash at the LIVE
    13-week T-bill rate (^IRX) so the November verdict compares strategies, not
    deployment habits.

THE RULE (frozen — see CASH_YIELD_AMENDMENT.md)
    instrument   SHORT-TERM T-BILLS ONLY (^IRX, 13-week). Not bonds: bond
                 duration risk would bolt an unintended rates bet onto every book.
    accrual      interest = cash * (rate/100) * (elapsed_seconds / (365*86400)),
                 prorated by ACTUAL elapsed time since that book's last accrual,
                 so a change of tick cadence cannot distort it.
    forward-only Never retroactive. The first accrual for a book only sets the
                 baseline instant and credits ZERO.
    fail-safe    A BAD live rate (<=0, >25, NaN, dead feed) accrues ZERO and is
                 never masked by the cache. A TRANSPORT failure may serve a
                 cache younger than 24h; older than that also accrues ZERO
                 => accrue ZERO, print loudly, persist an error marker.
                 A stale rate is NEVER carried forward past 24h and is NEVER
                 guessed.
    separable    cash_interest_accrued is each book's OWN cumulative field, so
                 the November review can always split STRATEGY return from
                 INTEREST return.

STRUCTURAL SAFETY
    accrue() takes a state dict and the cash NUMBER — never a ledger root, never
    a price map. It writes only the CY_FIELDS below, and asserts on the way out
    that no holding/position/cash/cost key was touched. It cannot trade.

PAPER ONLY. No broker, no credentials, no real orders.

Usage (diagnostic only):
    python3 cash_yield.py            # print the live rate and cache state
"""
import json
import os
import time

BASE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(BASE, "reports")
CACHE = os.path.join(REPORTS, "cash_yield_rate.json")
ERROR_MARKER = os.path.join(REPORTS, "cash_yield_error.json")

TICKER = "^IRX"                 # 13-week T-bill discount rate, quoted in PERCENT
REFRESH_SECONDS = 3600.0        # refresh at most hourly
MAX_CACHE_AGE = 86400.0         # REFUSE anything older than 24h (non-negotiable #7)
MAX_BAR_AGE_SECONDS = 7 * 86400.0   # a feed still echoing a >7d-old bar is dead
RATE_MAX = 25.0                 # absurd-rate ceiling
SECONDS_PER_YEAR = 365.0 * 86400.0

# The only keys accrue() may create or modify.
F_CUM = "cash_interest_accrued"          # cumulative, separable, auditable
F_SINCE = "cash_yield_since"             # first accrual instant (UTC)
F_RATE = "last_rate_used"                # percent, e.g. 3.69
F_RATE_ASOF = "cash_yield_rate_asof"     # UTC stamp of the rate observation
F_RATE_BAR = "cash_yield_rate_bar_date"  # the ^IRX bar the rate came from
F_LAST_EPOCH = "cash_yield_last_epoch"   # accrual clock (float epoch seconds)
F_LAST_UTC = "cash_yield_last_utc"
F_LAST_INTEREST = "cash_yield_last_interest"
F_LAST_ELAPSED = "cash_yield_last_elapsed_s"
F_ERROR = "cash_yield_error"
F_NOTE = "cash_yield_note"
CY_FIELDS = (F_CUM, F_SINCE, F_RATE, F_RATE_ASOF, F_RATE_BAR, F_LAST_EPOCH,
             F_LAST_UTC, F_LAST_INTEREST, F_LAST_ELAPSED, F_ERROR, F_NOTE)

# Keys accrue() must NEVER touch. Asserted, not merely intended.
FORBIDDEN = ("holdings", "positions", "entry_px", "entry_px_usd", "cash",
             "equity", "costs", "costs_paid", "units", "peak", "peak_equity",
             "frozen", "name")


def _utc(epoch=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _loud(msg, detail=None):
    """Print loudly AND persist an error marker. Never silent."""
    line = "CASH-YIELD ERROR: " + str(msg)[:400]
    print(line)
    rec = {"msg": line, "at_utc": _utc(), "detail": detail}
    try:
        os.makedirs(REPORTS, exist_ok=True)
        tmp = ERROR_MARKER + ".tmp"
        with open(tmp, "w") as f:
            json.dump(rec, f, indent=2)
        os.replace(tmp, ERROR_MARKER)
    except Exception as e:                                  # pragma: no cover
        print("CASH-YIELD ERROR: could not persist error marker: %s" % str(e)[:200])
    return rec


def _clear_marker():
    try:
        if os.path.exists(ERROR_MARKER):
            os.remove(ERROR_MARKER)
    except Exception:
        pass


def _valid_rate(pct):
    """A rate we are willing to accrue on. Anything else accrues ZERO."""
    try:
        pct = float(pct)
    except Exception:
        return False
    if pct != pct:                       # NaN
        return False
    if pct <= 0.0 or pct > RATE_MAX:
        return False
    return True


def _read_cache():
    try:
        with open(CACHE) as f:
            c = json.load(f)
        pct = float(c["rate_pct"])
        asof = float(c["asof_epoch"])
        if not _valid_rate(pct):
            return None
        return {"rate_pct": pct, "asof_epoch": asof,
                "bar_date": c.get("bar_date"), "source": c.get("source", TICKER)}
    except Exception:
        return None


def _write_cache(pct, asof, bar_date):
    rec = {"rate_pct": float(pct), "asof_epoch": float(asof),
           "asof_utc": _utc(asof), "bar_date": bar_date, "source": TICKER,
           "instrument": "13-week US T-bill (short-term bills only; NOT bonds)",
           "amendment": "CASH_YIELD_AMENDMENT.md"}
    os.makedirs(REPORTS, exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f, indent=2)
    os.replace(tmp, CACHE)
    return rec


class RateQualityError(RuntimeError):
    """The source ANSWERED but the answer is not usable (bad value, dead feed).

    Distinct from a transport failure on purpose. A transport hiccup may be
    served from a fresh cache; a BAD VALUE means the source itself is broken and
    must never be masked by a cache -- that is the failure class that left the
    Kraken engine blind for four days (hostile review 2026-08-27 proved the old
    code credited interest on a -1.0% and a 99% live rate whenever a cache
    existed). A broken source now accrues ZERO and says so loudly.
    """


def _fetch_rate():
    """(pct, bar_epoch, bar_date) from ^IRX, or raise. Percent units."""
    import yfinance as yf
    h = yf.Ticker(TICKER).history(period="10d", interval="1d")
    if h is None or len(h) == 0:
        raise RuntimeError("%s returned no bars" % TICKER)
    close = h["Close"].dropna()
    if len(close) == 0:
        raise RateQualityError("%s returned no non-NaN closes" % TICKER)
    pct = float(close.iloc[-1])
    idx = close.index[-1]
    bar_epoch = float(idx.timestamp())
    bar_date = idx.strftime("%Y-%m-%d")
    if not _valid_rate(pct):
        raise RateQualityError("%s rate %r fails the sanity gate (0 < r <= %.1f)"
                           % (TICKER, pct, RATE_MAX))
    age = time.time() - bar_epoch
    if age > MAX_BAR_AGE_SECONDS:
        raise RateQualityError("%s newest bar %s is %.1f days old — feed is dead"
                           % (TICKER, bar_date, age / 86400.0))
    return pct, bar_epoch, bar_date


def current_rate():
    """(rate_pct, asof_epoch) for the live 13-week T-bill, or (None, None).

    asof_epoch is when WE observed the rate (the cache stamp). The cache is
    refreshed at most hourly and is REFUSED once it is older than 24h — a stale
    input is never carried forward and never guessed.
    """
    now = time.time()
    cached = _read_cache()
    if cached is not None and (now - cached["asof_epoch"]) <= REFRESH_SECONDS:
        return cached["rate_pct"], cached["asof_epoch"]

    try:
        pct, _bar_epoch, bar_date = _fetch_rate()
        _write_cache(pct, now, bar_date)
        _clear_marker()
        return pct, now
    except RateQualityError as e:
        # The source answered with something unusable. NEVER fall back to cache
        # here: a cache would hide a broken feed behind a plausible number.
        _loud("%s returned an UNUSABLE rate (%s) — the source is broken. "
              "Refusing to serve a cached rate over a bad live one. Accruing ZERO."
              % (TICKER, str(e)[:200]),
              {"failure_class": "data_quality",
               "cache_present": cached is not None})
        return None, None
    except Exception as e:
        fetch_err = str(e)[:300]

    if cached is not None:
        age = now - cached["asof_epoch"]
        if age <= MAX_CACHE_AGE:
            print("CASH-YIELD WARN: %s fetch failed (%s); serving cached %.4f%% "
                  "observed %s (%.1fh old, limit 24h)"
                  % (TICKER, fetch_err, cached["rate_pct"],
                     _utc(cached["asof_epoch"]), age / 3600.0))
            return cached["rate_pct"], cached["asof_epoch"]
        _loud("%s fetch failed (%s) and the cached rate is %.1fh old (> 24h) — "
              "REFUSING it. Accruing ZERO." % (TICKER, fetch_err, age / 3600.0),
              {"cached_rate_pct": cached["rate_pct"],
               "cached_asof_utc": _utc(cached["asof_epoch"])})
        return None, None

    _loud("%s fetch failed (%s) and there is no usable cache. Accruing ZERO."
          % (TICKER, fetch_err))
    return None, None


def accrue(state_dict, cash_value):
    """Accrue T-bill interest on `cash_value` into `state_dict`; return interest.

    Mutates ONLY the CY_FIELDS bookkeeping keys. It is handed the cash NUMBER
    and a state dict — never a ledger root — so it cannot reach holdings or
    positions. The CALLER adds the returned interest to its own cash.

    Returns 0.0 (loudly, with an error field and an on-disk marker) on any
    failure. Never raises, never guesses a rate, never back-credits.
    """
    now = time.time()
    if not isinstance(state_dict, dict):
        _loud("accrue() called with %s, not a state dict — accruing ZERO"
              % type(state_dict).__name__)
        return 0.0

    before = {k: state_dict.get(k, KeyError) for k in FORBIDDEN}
    interest = 0.0
    try:
        prev = state_dict.get(F_LAST_EPOCH)
        first = not isinstance(prev, (int, float)) or isinstance(prev, bool)

        # The accrual clock always advances, even on a failed or zero tick.
        # Not advancing it would let a later tick back-credit a period this
        # amendment has already declared forgone.
        state_dict[F_LAST_EPOCH] = now
        state_dict[F_LAST_UTC] = _utc(now)
        state_dict.setdefault(F_CUM, 0.0)

        if first:
            state_dict[F_SINCE] = _utc(now)
            state_dict[F_LAST_INTEREST] = 0.0
            state_dict[F_LAST_ELAPSED] = 0.0
            state_dict[F_NOTE] = ("baseline set; forward-only from this instant, "
                                  "no retroactive credit (CASH_YIELD_AMENDMENT.md)")
            state_dict[F_ERROR] = None
            return 0.0

        elapsed = now - float(prev)
        state_dict[F_LAST_ELAPSED] = elapsed
        if elapsed <= 0.0:
            state_dict[F_LAST_INTEREST] = 0.0
            state_dict[F_NOTE] = "elapsed %.3fs <= 0 (clock skew) — accrued ZERO" % elapsed
            return 0.0

        pct, asof = current_rate()
        if pct is None or not _valid_rate(pct):
            state_dict[F_LAST_INTEREST] = 0.0
            state_dict[F_NOTE] = "no usable %s rate — accrued ZERO" % TICKER
            state_dict[F_ERROR] = {
                "msg": "no usable %s rate; accrued ZERO for %.1fs of elapsed time"
                       % (TICKER, elapsed),
                "at_utc": _utc(now)}
            return 0.0

        cache = _read_cache()
        state_dict[F_RATE] = float(pct)
        state_dict[F_RATE_ASOF] = _utc(asof)
        if cache:
            state_dict[F_RATE_BAR] = cache.get("bar_date")

        try:
            cash = float(cash_value)
        except Exception:
            cash = float("nan")
        if cash != cash:
            state_dict[F_LAST_INTEREST] = 0.0
            state_dict[F_ERROR] = {"msg": "cash_value %r is not a number — accrued ZERO"
                                          % (cash_value,), "at_utc": _utc(now)}
            _loud("cash_value %r is not a number — accrued ZERO" % (cash_value,))
            return 0.0

        if cash <= 0.0:
            # A negative or zero balance is not idle capital. We credit nothing
            # and we do NOT invent a borrow charge — that would be a different
            # (unauthorised) amendment. The clock still advanced, so this period
            # is forgone, not deferred.
            state_dict[F_LAST_INTEREST] = 0.0
            state_dict[F_NOTE] = ("cash %.2f <= 0 — no idle balance, accrued ZERO "
                                  "(no borrow charge modelled)" % cash)
            state_dict[F_ERROR] = None
            return 0.0

        interest = cash * (float(pct) / 100.0) * (elapsed / SECONDS_PER_YEAR)
        if interest != interest or interest < 0.0:
            state_dict[F_LAST_INTEREST] = 0.0
            _loud("computed interest %r is not a usable credit — accrued ZERO"
                  % (interest,))
            state_dict[F_ERROR] = {"msg": "bad interest %r" % (interest,),
                                   "at_utc": _utc(now)}
            return 0.0

        state_dict[F_CUM] = round(float(state_dict.get(F_CUM, 0.0)) + interest, 10)
        state_dict[F_LAST_INTEREST] = interest
        state_dict[F_NOTE] = ("%.6f = %.2f * %.4f%% * %.1fs/365d"
                              % (interest, cash, pct, elapsed))
        state_dict[F_ERROR] = None
        return interest
    except Exception as e:                                  # never break a tick
        rec = _loud("accrue() failed: %s" % str(e)[:300])
        try:
            state_dict[F_ERROR] = rec
            state_dict[F_LAST_INTEREST] = 0.0
        except Exception:
            pass
        return 0.0
    finally:
        # Structural proof, every single call: nothing outside CY_FIELDS moved.
        try:
            for k in FORBIDDEN:
                now_v = state_dict.get(k, KeyError)
                if now_v is not before[k]:
                    _loud("accrue() touched forbidden key %r — this is a bug; "
                          "restoring it" % k)
                    if before[k] is KeyError:
                        state_dict.pop(k, None)
                    else:
                        state_dict[k] = before[k]
        except Exception:
            pass


def _diagnostic():
    pct, asof = current_rate()
    print("CASH YIELD — %s (13-week T-bill, short-term bills only)  [PAPER ONLY]" % TICKER)
    if pct is None:
        print("  rate        : UNAVAILABLE — every book accrues ZERO this tick")
    else:
        print("  rate        : %.4f%% per annum   observed %s" % (pct, _utc(asof)))
        print("  per $100k/d : $%.4f" % (100000.0 * pct / 100.0 * 86400.0 / SECONDS_PER_YEAR))
    c = _read_cache()
    if c:
        print("  cache       : %s  bar %s  age %.2fh (refresh 1h, refuse 24h)"
              % (CACHE, c.get("bar_date"), (time.time() - c["asof_epoch"]) / 3600.0))
    print("  amendment   : CASH_YIELD_AMENDMENT.md")
    if os.path.exists(ERROR_MARKER):
        print("  *** ERROR MARKER PRESENT: %s" % ERROR_MARKER)


if __name__ == "__main__":
    _diagnostic()
