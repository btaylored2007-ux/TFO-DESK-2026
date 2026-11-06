"""dayclock_20261103 (master fixer, Brett 16:01 WITA 3 Nov "Figures are not right" + 16:13 "Fix the dashboard discrepancies"). PAPER, read-only.

ONE "today" for every number a seat, Marcus, the Head Trader, the engineer or the roll-call says in chat: the DASHBOARD's clock.
  today = equity now - the seat's opening mark at 06:00 WITA (state/thf/leaderboard_open.json, rolled after every THF tick by
          leaderboard.thf_roll_open) = exactly what the leaderboard "today" column, every trader card and the big "How are we doing
          today?" number show (Head Trader ht0600_20261103 made the headline the sum of the leaderboard rows).
The book's own day (thf_book.json seats[*].today_pnl_aud) starts at MIDNIGHT WITA. The risk rails still use it, and when a text
needs it, it is labelled "book day since 00:00 WITA (rails)". Before this, seats quoted the midnight number (tech 15:52: "Today
net -A$2346") while the dash showed +A$443 for the same seat: two clocks, one word.

seat_days(book=None, t=None) -> {"window_start_wita": ..., "clock": "06:00"|"book", "seats": {seat: {day_aud, book_day_aud, equity_aud,
open_aud}}, "desk": {day_aud, book_day_aud}}. Never raises: if the opening marks are missing/stale it falls back to the book's own
day and says so (clock="book")."""
import datetime as dt, json, os
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPEN = os.path.join(BASE, "state", "thf", "leaderboard_open.json")
BOOK = os.path.join(BASE, "state", "thf", "thf_book.json")
UTC = dt.timezone.utc
LABEL = "today = since 06:00 WITA (the dashboard / leaderboard clock)"
BOOK_LABEL = "book day since 00:00 WITA (risk rails)"


def _rj(p):
    try:
        with open(p) as fh: return json.load(fh)
    except Exception: return None


def _f(x):
    try: return float(x)
    except Exception: return None


_CACHE = {}
def seat_days(book=None, t=None, ttl=20):
    """Cached for ttl seconds when called with no book/time (chat prompts call it once per seat)."""
    if book is None and t is None and ttl:
        c = _CACHE.get("v")
        if c and dt.datetime.now(UTC).timestamp() - c[0] < ttl: return c[1]
        v = _seat_days(None, None); _CACHE["v"] = (dt.datetime.now(UTC).timestamp(), v); return v
    return _seat_days(book, t)


def _seat_days(book=None, t=None):
    t = t or dt.datetime.now(UTC)
    D = book if isinstance(book, dict) else (_rj(BOOK) or {})
    seats = {k: v for k, v in (D.get("seats") or {}).items() if isinstance(v, dict)}
    out = dict(window_start_wita=None, clock="book", seats={}, desk=dict(day_aud=0.0, book_day_aud=0.0), why=None)
    rows = {}
    try:
        from veteran_desk import leaderboard as LB
        O = _rj(OPEN)
        if O and O.get("window_start_utc") == LB.window_start(t).isoformat() and (O.get("seats") or {}):
            st = LB.standings(D=D, O=O, t=t)   # the dash's own call (thf_dash.py): same baseline, same capital-event rule
            rows = {r["seat"]: r for r in (st.get("rows") or [])}
            out.update(window_start_wita=st.get("window_start_wita") or O.get("window_start_wita"), clock="06:00")
        else:
            out["why"] = "opening marks missing or from an older window - book day used"
    except Exception as x:
        out["why"] = "leaderboard unavailable (%s) - book day used" % type(x).__name__
    for s, B in seats.items():
        bd = _f(B.get("today_pnl_aud")); r = rows.get(s)
        day = _f(r.get("day_pnl_aud")) if r else None
        eq = _f(r.get("equity_aud")) if r else _f(B.get("equity_aud"))
        out["seats"][s] = dict(day_aud=round(day if day is not None else (bd or 0.0), 2), book_day_aud=(round(bd, 2) if bd is not None else None),
                               equity_aud=eq, open_aud=(_f(r.get("open_equity_aud")) if r else None), clock=("06:00" if r else "book"))
        out["desk"]["day_aud"] += out["seats"][s]["day_aud"]; out["desk"]["book_day_aud"] += (bd or 0.0)
    out["desk"] = {k: round(v, 2) for k, v in out["desk"].items()}
    return out


def day_of(seat, days=None):
    """(day_aud, book_day_aud, clock) for one seat; (None, None, 'none') when the seat has no trading book."""
    d = (days or seat_days())["seats"].get(seat)
    return (d["day_aud"], d["book_day_aud"], d["clock"]) if d else (None, None, "none")


if __name__ == "__main__":
    print(json.dumps(seat_days(), indent=1, default=str))
