#!/usr/bin/env python3
"""fundamental_refresh.py — 5-minute mark for both fundamental books. PAPER ONLY.

Funder-directed 2026-08-28: refresh the dashboard every 5 minutes.

WHY THIS IS NOT JUST `mark()` ON A 5-MINUTE TIMER
-------------------------------------------------
The main book holds 56 positions across 11 currencies and the sleeve holds 2.
Marking all of them every 5 minutes is 288 runs a day and roughly 16,000 price
fetches. This operation has ALREADY been rate-limited repeatedly today, and a
rate limit here is not a slow response — it is yfinance **silently returning
empty**, which a US desk measured across 43 consecutive tickers with no exception
raised. Hammering the source is the single most reliable way to poison the very
ledger the dashboard is displaying.

So this refresh is SESSION-AWARE. It marks a book only when at least one of its
positions sits on an exchange that is actually open. Outside every relevant
session it does nothing at all and says so — because re-fetching a closed market
produces the same stale close, at full API cost, with a fresh chance of being
throttled into blanks.

WHAT A CLOSED MARKET MEANS FOR THE DASHBOARD
--------------------------------------------
Nothing bad. The last mark IS the current value when the exchange is shut. The
dashboard's freshness dot already reports mark age, so a book that has not moved
because London is closed looks exactly like what it is.

VENUE HOURS (UTC), deliberately generous at the edges
-----------------------------------------------------
    ASX            00:00-06:00      TSE/Tokyo      00:00-06:00
    KRX/HKEX/TWSE  01:00-08:00      LSE/AIM/EU     07:00-16:30
    US/TSX/TSXV    13:30-20:00      B3 Sao Paulo   13:00-20:00
Weekends: no equity venue in the book trades, so the whole run is skipped.

PAPER ONLY. Reads prices, writes ledgers. No broker, no order, no credential.
"""
import datetime
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
LOG = os.path.join(BASE, "reports", "refresh.log")

# suffix -> (open_utc_hour, close_utc_hour). "" == US.
VENUE_HOURS = {
    "": (13.5, 20.0), ".TO": (13.5, 20.0), ".V": (13.5, 20.0),
    ".AX": (0.0, 6.0), ".NZ": (22.0, 4.75),
    ".T": (0.0, 6.0), ".KS": (0.0, 6.5), ".KQ": (0.0, 6.5),
    ".HK": (1.5, 8.0), ".TW": (1.0, 5.5), ".TWO": (1.0, 5.5),
    ".SS": (1.5, 7.0), ".SZ": (1.5, 7.0), ".SI": (1.0, 9.0),
    ".L": (7.0, 16.5), ".PA": (7.0, 16.0), ".AS": (7.0, 16.0),
    ".BR": (7.0, 16.0), ".DE": (7.0, 16.0), ".MI": (7.0, 16.0),
    ".MC": (7.0, 16.0), ".LS": (7.0, 16.0), ".SW": (7.0, 16.0),
    ".VI": (7.0, 16.0), ".ST": (7.0, 16.0), ".OL": (7.0, 16.0),
    ".CO": (7.0, 16.0), ".HE": (7.0, 16.0), ".IR": (7.0, 16.0),
    ".AT": (7.0, 15.0), ".JO": (7.0, 15.0), ".TA": (7.0, 14.0),
    ".SA": (13.0, 20.0), ".MX": (14.5, 21.0), ".SN": (13.5, 20.0),
    ".NS": (3.75, 10.0), ".BO": (3.75, 10.0), ".QA": (7.0, 10.5),
    ".SR": (7.0, 12.0), ".KL": (1.0, 9.0), ".BK": (3.0, 9.5),
}


def _log(msg):
    line = "%s  %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), msg)
    print(line)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def venue_open(symbol, now_utc):
    """Is this symbol's exchange plausibly open? Generous by design — a false
    'open' costs one wasted fetch; a false 'closed' means the dashboard shows a
    stale price during live trading, which is the worse error."""
    if now_utc.weekday() >= 5:
        return False
    suffix = ""
    if "." in symbol:
        suffix = "." + symbol.rsplit(".", 1)[1]
    hrs = VENUE_HOURS.get(suffix)
    if hrs is None:
        return True                       # unknown venue: assume open, never skip silently
    o, c = hrs
    h = now_utc.hour + now_utc.minute / 60.0
    if o <= c:
        return o <= h <= c
    return h >= o or h <= c               # venue wrapping midnight UTC (e.g. NZ)


def symbols(ledger_path):
    import json
    try:
        with open(ledger_path) as f:
            return list((json.load(f).get("positions") or {}).keys())
    except Exception:                                  # noqa: BLE001
        return []


def refresh(book, ledger, cmd, now_utc):
    syms = symbols(os.path.join(BASE, ledger))
    if not syms:
        _log("%-14s no open positions — nothing to mark" % book)
        return
    live = [s for s in syms if venue_open(s, now_utc)]
    if not live:
        _log("%-14s all %d position(s) on CLOSED venues — SKIPPED. The last mark "
             "IS the current value; re-fetching a shut market costs API budget "
             "and risks a rate-limit blank." % (book, len(syms)))
        return
    _log("%-14s %d of %d position(s) on OPEN venues (%s) — marking"
         % (book, len(live), len(syms), ", ".join(sorted(set(
             "." + s.rsplit(".", 1)[1] if "." in s else "US" for s in live))[:6])))
    try:
        r = subprocess.run([PY] + cmd, cwd=BASE, capture_output=True,
                           text=True, timeout=600)
        out = (r.stdout or "").strip().splitlines()
        _log("%-14s -> %s" % (book, out[-1] if out else "(no output)"))
        err = (r.stderr or "")
        if "RateLimit" in err or "Too Many Requests" in err:
            _log("%-14s !! RATE LIMITED — this mark may be incomplete. Marks are "
                 "not verdicts, so a partial mark is recoverable, but do NOT "
                 "read a flat equity line from this tick as 'no movement'." % book)
    except subprocess.TimeoutExpired:
        _log("%-14s !! TIMED OUT after 600s — mark abandoned, ledger left "
             "untouched rather than half-written" % book)


ALERTS = os.path.join(BASE, "reports", "catalyst_alerts.jsonl")


def check_catalyst_fills():
    """Detect catalyst-expiry closes and make them UNMISSABLE.

    The first expiry is ~40 days out, so nobody will be watching the moment it
    fires. Three independent surfaces, because a notification nobody sees is the
    same as no notification:
      1. an append-only JSONL record — the permanent one, survives everything
      2. a macOS notification — immediate, if anyone is at the Mac
      3. the DASHBOARD, via /api/alerts — the one actually read from a phone
    """
    import json as _json
    found = []
    for label, ledger in (("MAIN BOOK", "reports/fundamental_book.json"),
                          ("SLEEVE", "reports/fundamental_spec_book.json")):
        try:
            with open(os.path.join(BASE, ledger)) as f:
                d = _json.load(f)
        except Exception:                              # noqa: BLE001
            continue
        for fill in (d.get("trade_fills_this_tick") or []):
            if fill.get("action") != "catalyst_expired":
                continue
            rec = {
                "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "book": label, "sym": fill.get("sym"),
                "catalyst_date": fill.get("catalyst_date"),
                "realised_usd": fill.get("realised_usd"),
                "cash_delta_usd": fill.get("cash_delta_usd"),
                "net_gain_pct": fill.get("net_gain_pct"),
                "reason": fill.get("reason"),
                "rule": ("CATALYST EXPIRY — funder-directed 'sell the news', "
                         "registered 2026-08-28 before it ran. The reason for "
                         "holding RESOLVED; the position closed IN FULL. This is "
                         "a reason-expired exit, so it is NOT blocked by the net "
                         "gate and MAY have closed at a loss. That is correct."),
            }
            found.append(rec)
            try:
                with open(ALERTS, "a") as f:
                    f.write(_json.dumps(rec) + "\n")
            except OSError:
                pass
            _log("*** CATALYST EXPIRY FIRED: %s %s (catalyst %s) realised $%s ***"
                 % (label, rec["sym"], rec["catalyst_date"], rec["realised_usd"]))
            try:
                subprocess.run(["osascript", "-e",
                    'display notification "%s closed - catalyst %s passed. '
                    'Realised $%s." with title "TFO: CATALYST EXPIRY"'
                    % (rec["sym"], rec["catalyst_date"], rec["realised_usd"])],
                    timeout=10)
            except Exception:                          # noqa: BLE001
                pass
    return found


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    if now.weekday() >= 5:
        _log("weekend (%s) — no equity venue in either book trades. Skipped."
             % now.strftime("%a"))
        return
    refresh("MAIN BOOK", "reports/fundamental_book.json",
            ["fundamental_book.py"], now)
    refresh("SPEC SLEEVE", "reports/fundamental_spec_book.json",
            ["fundamental_spec_book.py", "--mark"], now)
    check_catalyst_fills()
    # Keep catalyst coverage current. A position with no date is IMMUNE to the
    # expiry rule, so the size of that blind spot must be measured every cycle,
    # not discovered months later. It writes dates only where a block sources
    # one and NEVER invents one.
    try:
        subprocess.run([PY, "fundamental_catalyst_backfill.py"],
                       cwd=BASE, capture_output=True, text=True, timeout=300)
    except Exception:                                      # noqa: BLE001
        pass
    # Post-exit tracker: re-marks every closed/scaled position so we can answer
    # "did we exit too early?" with data in February instead of instinct now.
    # Runs AFTER the marks so it reads fresh prices, and never blocks the refresh.
    try:
        r = subprocess.run([PY, "fundamental_post_exit.py", "--quiet"],
                           cwd=BASE, capture_output=True, text=True, timeout=600)
        if r.returncode == 0:
            _log("post-exit     tracker updated")
        else:
            _log("post-exit     !! failed rc=%s %s"
                 % (r.returncode, (r.stderr or "")[-120:]))
    except Exception as exc:                               # noqa: BLE001
        _log("post-exit     !! %s: %s" % (type(exc).__name__, str(exc)[:90]))


if __name__ == "__main__":
    main()
