#!/usr/bin/env python3
"""V1 exit rail, variant C (Head Trader decision 2026-09-25, funder-authorised). PAPER ONLY.

Pure decision logic shared by tfo_ticket.py (equity/floor books) and tfo_crypto.py (HT crypto clips).
  Pre-half : STOP at -5% from the ORIGINAL fill (shorts -4%); HALF at +2% from the original fill.
  Post-half: the residual trails from its high-water mark (shorts: low-water mark) by
             max(3%, 3 x ATR14/price) (shorts: max(4%, 3 x ATR14/price)), floored at breakeven
             (the original fill). The -5%/-4% original-fill stop still applies (gap protection).
  NO +5% full-flat cap. One half-bank only; no further +2% re-halves after the bank.
  Max hold on the residual: 30 CALENDAR days from the original fill (entry time), equities and crypto alike
  (Head Trader ruling 2026-09-28; previously 30 weekdays for equities). The exit still fires only through the
  normal rail path (first valid regular-session print, exit costs). Equity exit_rule = MAX_HOLD_30D.
Residual exits reuse the existing FLAT ticket action so the LOG vocabulary (HALF/FLAT/STOP) is
unchanged; the ticket/fact carries exit_rule = TRAIL_3ATR | BE_FLOOR | MAX_HOLD_30D (equity) | MAX_HOLD (crypto).
Positions already half-banked when this shipped seed the trail from the current price (never below BE).

Shark exit variant V7 (Brett-approved PAPER forward test, 2 Oct 2026; see SHARK_EXIT_RULE.md). Applies ONLY to positions
tagged exit_variant="V7" (new tfo_buy clips from 2 Oct 2026); untagged positions keep the default rail above unchanged.
  Pre-half : STOP at -5% from the original fill (shorts -4%); HALF at +10% (rule HALF_10PCT), not +2%.
  Post-half: identical residual trail max(3%, 3 x ATR14/price) off the HWM, floored at breakeven; orig-fill stop kept.
  Max hold : 40 SESSIONS after the entry session (weekdays in the venue's local calendar; exchange holidays not removed,
             so it can fire 1-2 sessions early), applied to the WHOLE position (pre- or post-half). Rule MAX_HOLD_40S.

Crypto exit variant C3 (2 Oct 2026; engine60 crypto exit backtest variant A3; see SHARK_EXIT_RULE.md). Applies ONLY to
HT crypto clips tagged exit_variant="C3" (new tfo_crypto buys from 2 Oct 2026); untagged clips keep the default rail.
  Stop     : 2 x daily ATR14% at ENTRY below the original fill (stored on the position as c3_stop_dist; fixed for life).
  Half     : +15% from the original fill (rule HALF_15PCT).
  Residual : identical trail max(3%, 3 x ATR14/price) off the HWM, floored at breakeven; original stop kept.
  Max hold : 40 calendar days from the original fill (crypto trades daily), whole position. Rule MAX_HOLD_40D.
"""
import datetime as dt, math

ATR_K = 3.0
TRAIL_MIN_LONG = 0.03
TRAIL_MIN_SHORT = 0.04
HALF_AT = 0.02
STOP_LONG, STOP_SHORT = -0.05, -0.04
MAX_HOLD_TRADING_DAYS = 30   # legacy name kept for importers; since 2026-09-28 this is 30 CALENDAR days
MAX_HOLD_CALENDAR_DAYS = 30  # HT ruling 2026-09-28: 30 calendar days from the original fill (entry time)
MAX_HOLD_CRYPTO_DAYS = 30
MAX_HOLD_RULE_EQUITY = "MAX_HOLD_30D"
MAX_HOLD_RULE_CRYPTO = "MAX_HOLD"   # crypto label unchanged
ENTRY_TIME_FIELDS = ("entry_utc", "opened_utc")  # convexity_sleeve stores its fill time as opened_utc
RULE_TEXT = "trail 3xATR after +2% half, BE floor"
# --- shark exit variant V7 (2026-10-02 paper forward test) ---
V7 = "V7"
V7_HALF_AT = 0.10
V7_MAX_HOLD_SESSIONS = 40
V7_MAX_HOLD_RULE = "MAX_HOLD_40S"
V7_RULE_TEXT = "V7: trail 3xATR after +10% half, BE floor, 40-session max hold"
C3 = "C3"
C3_STOP_ATR_K = 2.0
C3_HALF_AT = 0.15
C3_MAX_HOLD_DAYS = 40
C3_MAX_HOLD_RULE = "MAX_HOLD_40D"
C3_RULE_TEXT = "C3: stop 2xATR(entry), trail 3xATR after +15% half, BE floor, 40-day max hold"
# --- 2026-10-17 (Brett, weekly cut review): main-book entry-engine crypto clips (theme entry_engine_crypto) leave the
# C3 +15% half: bank HALF at +2%, residual FLAT by +5%; stop = C3 2xATR(entry) but never tighter than 1.5%.
# Trail/BE floor/40-day max hold unchanged. Stops stay stops (taker); the +2%/+5% exits are planned exits.
EE = "EE"
EE_HALF_AT = 0.02
EE_FLAT_AT = 0.05
EE_MIN_STOP = 0.015
EE_RULE_TEXT = "EE: stop max(2xATR(entry), 1.5%), half at +2%, flat by +5%, trail 3xATR / BE floor between, 40-day max hold"
VENUE_TZ = {"US": "America/New_York", "AX": "Australia/Sydney", "T": "Asia/Tokyo", "L": "Europe/London", "HK": "Asia/Hong_Kong"}


def ee_stop_dist(stop_dist):
    """Entry-engine stop distance: the C3 2xATR stop (missing/invalid -> 5%), floored at 1.5%."""
    sd = stop_dist if (isinstance(stop_dist, (int, float)) and math.isfinite(stop_dist) and stop_dist > 0) else -STOP_LONG
    return max(float(sd), EE_MIN_STOP)


def rule_text(variant=None, profile=None):
    if profile == EE:
        return EE_RULE_TEXT
    return V7_RULE_TEXT if variant == V7 else (C3_RULE_TEXT if variant == C3 else RULE_TEXT)


def c3_stop_dist(atr_pct):
    """C3 stop distance (fraction of entry) = 2 x ATR14%; None when the ATR is unusable."""
    try:
        a = float(atr_pct)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(a) or a <= 0:
        return None
    return C3_STOP_ATR_K * a


def sessions_after(entry_utc, now, venue="US"):
    """Weekday sessions strictly after the entry session up to and including today, in the venue's local calendar
    (exchange holidays not removed). None if the entry time is unknown."""
    if not entry_utc:
        return None
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(VENUE_TZ.get(venue, "UTC"))
        return weekdays_after(_parse(entry_utc).astimezone(tz).date(), _parse(now).astimezone(tz).date())
    except (TypeError, ValueError, KeyError):
        return None


def trail_pct(side, atr_pct):
    floor = TRAIL_MIN_SHORT if side == "short" else TRAIL_MIN_LONG
    try:
        a = float(atr_pct)
    except (TypeError, ValueError):
        return floor
    if not math.isfinite(a) or a <= 0:
        return floor
    return max(floor, ATR_K * a)


def _parse(ts):
    if isinstance(ts, dt.datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=dt.timezone.utc)
    return dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def weekdays_after(d0, d1):
    """Weekdays strictly after date d0 up to and including d1 (exchange holidays not removed)."""
    if d1 <= d0:
        return 0
    days = (d1 - d0).days
    full, rem = divmod(days, 7)
    n = full * 5
    wd = d0.weekday()
    for i in range(1, rem + 1):
        if (wd + i) % 7 < 5:
            n += 1
    return n


def entry_time(p):
    """Original fill time of a position: entry_utc, else opened_utc (convexity_sleeve). None if neither."""
    for f in ENTRY_TIME_FIELDS:
        v = (p or {}).get(f)
        if v:
            return v
    return None


def held(entry_utc, now, crypto=False):
    """Holding age in calendar days (float) from the original fill time; equities and crypto alike.
    HT ruling 2026-09-28: equity max hold is 30 calendar days from fill (no longer weekdays after the entry day)."""
    if not entry_utc:
        return None
    try:
        e, n = _parse(entry_utc), _parse(now)
    except (TypeError, ValueError):
        return None
    return (n - e).total_seconds() / 86400.0


def max_hold(crypto):
    return MAX_HOLD_CRYPTO_DAYS if crypto else MAX_HOLD_CALENDAR_DAYS


def max_hold_due_utc(entry_utc, crypto=False):
    """UTC datetime at which the residual's max hold is reached (None if entry time unknown)."""
    if not entry_utc:
        return None
    try:
        return _parse(entry_utc) + dt.timedelta(days=max_hold(crypto))
    except (TypeError, ValueError):
        return None


def decide(e0, px, side, half_banked, wm=None, atr_pct=None, held_age=None, crypto=False, variant=None, held_sessions=None, stop_dist=None, profile=None):
    """-> dict(act, rule, r0, level, wm, trail_pct). act in {None, 'STOP', 'HALF', 'FLAT'}.
    wm is the stored high-water mark (long) / low-water mark (short); None seeds from px.
    variant="V7" (shark exit test, 2026-10-02): +10% half and a 40-session whole-position max hold (held_sessions);
    any other variant value (None) is the unchanged default rail."""
    if profile == EE:  # 2026-10-17: entry-engine crypto clips (+2% half / +5% flat / stop >= 1.5%)
        out = _decide_variant(e0, px, side, half_banked, wm, atr_pct, held_age is not None and held_age >= C3_MAX_HOLD_DAYS,
                              -ee_stop_dist(stop_dist), EE_HALF_AT, "HALF_2PCT", C3_MAX_HOLD_RULE)
        if half_banked and out["act"] is None and out["r0"] >= EE_FLAT_AT - 1e-9:
            out.update(act="FLAT", rule="FLAT_5PCT")
        return out
    if variant == V7:
        return _decide_v7(e0, px, side, half_banked, wm, atr_pct, held_sessions)
    if variant == C3:  # stop_dist missing/invalid -> never wider than the default 5% stop
        sd = stop_dist if (isinstance(stop_dist, (int, float)) and math.isfinite(stop_dist) and stop_dist > 0) else -STOP_LONG
        return _decide_variant(e0, px, side, half_banked, wm, atr_pct, held_age is not None and held_age >= C3_MAX_HOLD_DAYS,
                               -sd, C3_HALF_AT, "HALF_15PCT", C3_MAX_HOLD_RULE)
    short = side == "short"
    sg = -1.0 if short else 1.0
    e0, px = float(e0), float(px)
    r0 = sg * (px / e0 - 1.0)
    stop = STOP_SHORT if short else STOP_LONG
    out = {"act": None, "rule": None, "r0": r0, "level": None, "wm": None, "trail_pct": None}
    if not half_banked:
        out["level"] = e0 * (1.0 + sg * stop)
        if r0 <= stop:
            out.update(act="STOP", rule="STOP_ORIG")
        elif r0 >= HALF_AT:
            out.update(act="HALF", rule="HALF_2PCT")
        return out
    w = px if wm is None else (min(float(wm), px) if short else max(float(wm), px))
    d = trail_pct(side, atr_pct)
    if short:
        trail = w * (1.0 + d); level = min(e0, trail); hit = px >= level
    else:
        trail = w * (1.0 - d); level = max(e0, trail); hit = px <= level
    out.update(wm=w, trail_pct=d, level=level)
    if r0 <= stop:
        out.update(act="STOP", rule="STOP_ORIG")
    elif hit:
        out.update(act="FLAT", rule="BE_FLOOR" if level == e0 else "TRAIL_3ATR")
    elif held_age is not None and held_age >= max_hold(crypto):
        out.update(act="FLAT", rule=MAX_HOLD_RULE_CRYPTO if crypto else MAX_HOLD_RULE_EQUITY)
    return out


def _decide_v7(e0, px, side, half_banked, wm, atr_pct, held_sessions):
    return _decide_variant(e0, px, side, half_banked, wm, atr_pct, held_sessions is not None and held_sessions >= V7_MAX_HOLD_SESSIONS,
                           STOP_SHORT if side == "short" else STOP_LONG, V7_HALF_AT, "HALF_10PCT", V7_MAX_HOLD_RULE)


def _decide_variant(e0, px, side, half_banked, wm, atr_pct, timed_out, stop, half_at, half_rule, hold_rule):
    """Shared V7/C3 logic: stop (negative return vs fill), half at +half_at, trail/BE after the half, whole-position max hold."""
    short = side == "short"
    sg = -1.0 if short else 1.0
    e0, px = float(e0), float(px)
    r0 = sg * (px / e0 - 1.0)
    out = {"act": None, "rule": None, "r0": r0, "level": None, "wm": None, "trail_pct": None}
    if not half_banked:
        out["level"] = e0 * (1.0 + sg * stop)
        if r0 <= stop:
            out.update(act="STOP", rule="STOP_ORIG")
        elif r0 >= half_at - 1e-9:  # float tolerance so exactly +15%/+10% counts
            out.update(act="HALF", rule=half_rule)
        elif timed_out:
            out.update(act="FLAT", rule=hold_rule)
        return out
    w = px if wm is None else (min(float(wm), px) if short else max(float(wm), px))
    d = trail_pct(side, atr_pct)
    if short:
        trail = w * (1.0 + d); level = min(e0, trail); hit = px >= level
    else:
        trail = w * (1.0 - d); level = max(e0, trail); hit = px <= level
    out.update(wm=w, trail_pct=d, level=level)
    if r0 <= stop:
        out.update(act="STOP", rule="STOP_ORIG")
    elif hit:
        out.update(act="FLAT", rule="BE_FLOOR" if level == e0 else "TRAIL_3ATR")
    elif timed_out:
        out.update(act="FLAT", rule=hold_rule)
    return out


def atr_pct_from_bars(bars):
    """bars: list of (high, low, close) completed daily bars, oldest first. Wilder ATR14 / last close."""
    bars = [(float(h), float(l), float(c)) for h, l, c in bars if all(math.isfinite(float(x)) and float(x) > 0 for x in (h, l, c))]
    if len(bars) < 15:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, l, _ = bars[i]; pc = bars[i - 1][2]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs[:14]) / 14.0
    for tr in trs[14:]:
        atr = (atr * 13.0 + tr) / 14.0
    return atr / bars[-1][2]


def equity_atr_pct(sym):
    """ATR14/price from completed yfinance daily bars (today's partial bar dropped)."""
    import yfinance as yf
    h = yf.Ticker(sym).history(period="4mo", interval="1d", auto_adjust=False)
    if h is None or h.empty:
        return None
    idx = h.index
    today = dt.datetime.now(idx.tz).date() if getattr(idx, "tz", None) is not None else dt.date.today()
    if idx[-1].date() >= today:
        h = h.iloc[:-1]
    return atr_pct_from_bars(list(zip(h["High"], h["Low"], h["Close"])))


def kraken_atr_pct(get, kpair):
    """ATR14/price from Kraken daily OHLC; the last (in-progress) row is dropped."""
    rows = [r for r in list(get("OHLC", pair=kpair, interval=1440).values())[0] if isinstance(r, list)][:-1]
    return atr_pct_from_bars([(r[2], r[3], r[4]) for r in rows[-60:]])


def refresh_atr(state, fetch, today):
    """Daily ATR refresh into state (atr14_pct, atr_date). Keeps the last good value on failure."""
    if state.get("atr_date") == today and state.get("atr14_pct"):
        return state
    try:
        a = fetch()
        if a and math.isfinite(a) and a > 0:
            state["atr14_pct"] = round(a, 6); state["atr_date"] = today; state.pop("atr_error", None)
        else:
            state["atr_error"] = "no ATR (insufficient bars)"
    except Exception as e:  # network / data: fall back to stored ATR or the 3%/4% floor
        state["atr_error"] = str(e)[:160]
    return state
