#!/usr/bin/env python3
"""bad_tick_guard.py -- shared >30% one-print bad-tick guard for V1 rails and book markers. PAPER ONLY.

Origin: 8766.T 2026-09-29. Tokio Marine went ex a 15-for-1 split; the ledger kept the pre-split qty/basis, the
book marker wrote the real post-split print into mark_usd, and the rail's 2.5x check then compared the price with
itself and filled a fictitious -93% STOP. Rails (tfo_ticket) and markers (fundamental_book.mark,
tfo_equity_refresh) now share these checks:

  rail   : reject a print >30% vs the last good mark (either direction) OR adverse >30% vs the original fill.
  marker : reject a print >30% vs the last good mark (either direction); with no mark yet, adverse >30% vs fill.
           A rejected mark is never written: the last good mark is carried, flagged mark_stale/mark_rejected.
  split-like ratios (px/ref near 2,3,4,5,8,10,15,20,25,30,50,100) are reported by split_ratio(); markers then
  request the corporate-actions check (tfo_corporate_actions) instead of treating the print as a price.
The speculative small-cap sleeve (fundamental_spec_book) keeps the legacy 2.5x check only.
"""
import json, os, tempfile, time

BAD_TICK_MAX_MOVE = 0.30
BAD_TICK_EXEMPT_BOOKS = frozenset({"fundamental_spec_book"})
SPEC_MAX_RATIO = 2.5
SPLIT_RATIOS = (2, 3, 4, 5, 8, 10, 15, 20, 25, 30, 50, 100)
SPLIT_TOL = 0.06
EPS = 1e-9          # exactly 30% is allowed (float: 130/100-1 = 0.30000000000000004)
BASE = os.path.dirname(os.path.abspath(__file__))
REQUESTS = os.path.join(BASE, "reports", "corporate_actions_requests.json")


def _f(x):
    try:
        v = float(x)
        return v if v == v else 0.0
    except (TypeError, ValueError):
        return 0.0


def split_ratio(px, ref, tol=SPLIT_TOL):
    """-> signed ratio: +n if px ~= ref/n (forward split), -n if px ~= ref*n (reverse split), else 0."""
    px, ref = _f(px), _f(ref)
    if px <= 0 or ref <= 0:
        return 0
    r = ref / px if px < ref else px / ref
    for n in SPLIT_RATIOS:
        if abs(r / n - 1) <= tol:
            return n if px < ref else -n
    return 0


def split_hint(px, ref, tol=SPLIT_TOL):
    n = split_ratio(px, ref, tol)
    if n > 0:
        return " (ratio ~%d:1 vs reference: possible unapplied %d-for-1 split/corporate action)" % (n, n)
    if n < 0:
        return " (ratio ~1:%d vs reference: possible unapplied 1-for-%d reverse split/corporate action)" % (-n, -n)
    return ""


def bad_tick(px, side, last_mark_usd, entry0_usd, max_move=BAD_TICK_MAX_MOVE):
    """Rail check. -> None if plausible, else a BLOCKED reason."""
    try:
        px = float(px)
    except (TypeError, ValueError):
        return "bad tick: unparseable price"
    if not px > 0:
        return "bad tick: non-positive price"
    last, e0 = _f(last_mark_usd), _f(entry0_usd)
    if last > 0 and abs(px / last - 1) > max_move + EPS:
        ref, name, mv = last, "last good mark", px / last - 1
    elif e0 > 0 and ((e0 - px) / e0 if side != "short" else (px - e0) / e0) > max_move + EPS:
        ref, name, mv = e0, "original fill", px / e0 - 1
    else:
        return None
    return "bad tick: px %.6g is %+.1f%% vs %s %.6g (>%d%% one-print move)%s" % (
        px, 100 * mv, name, ref, round(100 * max_move), split_hint(px, ref))


def mark_check(book, px_usd, side, last_mark_usd, entry0_usd):
    """Marker check. -> (reason|None, ref_usd, split_ratio)."""
    px, last, e0 = _f(px_usd), _f(last_mark_usd), _f(entry0_usd)
    if px <= 0:
        return "bad tick: non-positive price", last, 0
    if book in BAD_TICK_EXEMPT_BOOKS:
        if last > 0 and (px > SPEC_MAX_RATIO * last or px < last / SPEC_MAX_RATIO):
            return "price sanity (>2.5x last good mark %.6g)" % last, last, split_ratio(px, last)
        return None, last, 0
    if last > 0:
        if abs(px / last - 1) > BAD_TICK_MAX_MOVE + EPS:
            return ("bad tick: px %.6g is %+.1f%% vs last good mark %.6g (>30%% one-print move)%s; mark NOT written"
                    % (px, 100 * (px / last - 1), last, split_hint(px, last))), last, split_ratio(px, last)
        return None, last, 0
    if e0 > 0 and ((e0 - px) / e0 if side != "short" else (px - e0) / e0) > BAD_TICK_MAX_MOVE + EPS:
        return ("bad tick: px %.6g is %+.1f%% vs original fill %.6g (>30%%, no prior mark)%s; mark NOT written"
                % (px, 100 * (px / e0 - 1), e0, split_hint(px, e0))), e0, split_ratio(px, e0)
    return None, last, 0


def flag_rejected(p, px_native, px_usd, reason, ratio, checked_at):
    """Carry the last good mark; flag the rejected print on the position (never silent)."""
    p["mark_stale"] = True
    p["mark_error"] = reason
    p["mark_rejected"] = {"utc": checked_at, "px": px_native, "px_usd": px_usd, "reason": reason,
                          "split_like_ratio": ratio or None,
                          "action": "corporate-actions check requested" if ratio else "carry last good mark; review quote/FX"}


def request_ca_check(book, sym, reason, ratio, path=None):
    """Ask tfo_corporate_actions to re-check `sym` on its next run (rail cycle start / desk tick). Never raises."""
    path = path or REQUESTS
    try:
        try:
            with open(path) as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        d[sym] = {"book": book, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "reason": reason, "ratio": ratio}
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".ca_req", suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, path)
        return True
    except Exception:                                   # noqa: BLE001
        return False
