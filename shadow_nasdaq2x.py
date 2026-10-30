#!/usr/bin/env python3
"""SHADOW LEDGER: avenues candidate #1 "2x Nasdaq combo" (research/engine60/avenues_FINAL_20261002.md). PAPER SHADOW ONLY.

Brett 2026-10-02 ("do what you think is best for the firm"): run candidate #1 as a SHADOW next to the desk for 3-6 months.
  * NOT firm capital: it is not a registered book (book_coverage.registry), is not in firm equity / firm_risk / desk caps,
    and is NOT charged by funder_fee.py. It never places, previews or queues an order and never writes any desk ledger.
  * Its only files: state/shadow_nasdaq2x.json (latest state) and state/shadow_nasdaq2x_history.csv (one row per US session).
    The path contains "shadow" (book_coverage.AUDIT_SKIP) and the JSON has no ledger-shaped top-level keys.

Design (marks at US closes, Yahoo daily bars via yfinance; total-return marks from adjusted closes):
  start    : notional $1,797,160 at the US close of 2026-10-02 (desk equity that day).
  QLD 30%  : ProShares Ultra QQQ (2x daily QQQ) at 30% x brake of shadow value, held only while QQQ's adjusted close is
             above its 200-session SMA (signal at close d, trade at close d). Off = cash at T-bill. Reset to 30% x brake
             at the first session of each month (research rebalances every sleeve monthly), on a filter flip or a brake change.
             Switch cost 5bp of traded notional (research: 0.0005 per switch).
  value 20%: value+profitability sleeve weights from value_prof_sleeve.py (research definition, all target names; desk
             overlays are not applied because the shadow mirrors the backtest), rebalanced at the close of the FIRST US
             session of each month (signal = prior month-end close, as the research cadence). 10bp of traded notional.
  TOM SPY  : turn-of-month overlay on idle cash (avenues_ladder2.TOM): if SPY's 5-session return to session il-2 is
             negative (il = last session of the month, T+1 settlement), idle cash above the $50k reserve buys SPY at the
             OPEN of session il-1 and sells at the CLOSE of the 3rd session of the next month; 3bp per side.
  brake    : avenues_risk.breaker2 on shadow value: DD vs reference peak <= -10/-15/-20% -> exposure x0.67/0.5/0.25
             (applied to QLD, value and TOM at the next rebalance point); after 42 sessions in a cut state the reference
             resets to current value (re-risk).
  cash     : remainder, accrues ^IRX (13-week T-bill) per calendar day.
  pro-forma: an INFORMATIONAL "after-fee equivalent" series (funder rate per calendar day: 0.047619% to 3 Oct 2026, 0.0274%
             from 4 Oct 2026 per Brett 3 Oct 19:17 AEST) so the shadow can be compared with the
             fee-paying desk. No fee is charged to anyone; funder_fee.py never reads this file.
Commands:
  shadow_nasdaq2x.py run      mark every final US session since the last mark (idempotent; launchd com.tfo.shadow-nasdaq2x)
  shadow_nasdaq2x.py status   print the dashboard summary
"""
import argparse, csv, datetime as dt, json, math, os, sys
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE, "state")
STATE_PATH = os.path.join(STATE_DIR, "shadow_nasdaq2x.json")
HISTORY_PATH = os.path.join(STATE_DIR, "shadow_nasdaq2x_history.csv")
LOCK_PATH = os.path.join(STATE_DIR, ".shadow_nasdaq2x.lock")
VALUE_TARGET = os.path.join(BASE, "reports", "value_prof_sleeve", "target_latest.json")

LABEL = "Shadow: 2x Nasdaq combo (paper, not in firm equity)"
STATUS = "SHADOW_PAPER_NOT_IN_FIRM_EQUITY"
START_DATE = "2026-10-02"
NOTIONAL_USD = 1_797_160.0
W_QLD, W_VALUE = 0.30, 0.20
SMA_N = 200
RESERVE_USD = 50_000.0
COST_QLD, COST_VALUE, COST_TOM_SIDE = 0.0005, 0.0010, 0.0003
BRAKE_TIERS = ((-0.10, 0.67), (-0.15, 0.5), (-0.20, 0.25))
BRAKE_COOL = 42
# informational pro-forma only; mirrors funder_fee.RATE_SCHEDULE (history kept): 0.047619%/day, then 0.0274%/day from 4 Oct 2026
FEE_SCHEDULE = ((dt.date(2026, 10, 2), 0.00047619), (dt.date(2026, 10, 4), 0.000274))
FEE_DAY = FEE_SCHEDULE[-1][1]


def fee_factor(d0, d1):
    """prod(1 - rate) over calendar days d0 < x <= d1, each at the FEE_SCHEDULE rate in force that day."""
    f, x = 1.0, d0 + dt.timedelta(days=1)
    while x <= d1:
        f *= 1 - max((e, r) for e, r in FEE_SCHEDULE if e <= x)[1] if x >= FEE_SCHEDULE[0][0] else 1 - FEE_SCHEDULE[0][1]
        x += dt.timedelta(days=1)
    return f
FINAL_AFTER_ET = (16, 20)     # a session's daily bar is treated as final from 16:20 ET
ET, SYD, UTC = ZoneInfo("America/New_York"), ZoneInfo("Australia/Sydney"), dt.timezone.utc
CORE = ("QQQ", "QLD", "SPY", "^IRX")
LEDGER_KEYS = {"cash", "positions", "holdings", "desks", "equity", "nav", "marked_equity", "start_cash"}   # book_coverage.LEDGER_HINTS
HISTORY_FIELDS = ["date", "shadow_value_usd", "cash_usd", "qld_usd", "value_usd", "tom_spy_usd", "qld_on", "qqq_close", "qqq_sma200",
                  "brake_mult", "brake_cut_days", "drawdown_from_peak_pct", "brake_dd_pct", "return_since_start_pct",
                  "proforma_after_fee_usd", "costs_usd", "events"]


# ----------------------------------------------------------------------------------------------------------- calendar
def _easter(y):
    a = y % 19; b, c = divmod(y, 100); d, e = divmod(b, 4); f = (b + 8) // 25; g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30; i, k = divmod(c, 4); l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451; mo, da = divmod(h + l - 7 * m + 114, 31)
    return dt.date(y, mo, da + 1)


def _nth_weekday(y, m, wd, n):
    d = dt.date(y, m, 1); d += dt.timedelta(days=(wd - d.weekday()) % 7); return d + dt.timedelta(weeks=n - 1)


def _last_weekday(y, m, wd):
    d = dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1); return d - dt.timedelta(days=(d.weekday() - wd) % 7)


def _observed(d):
    return d - dt.timedelta(days=1) if d.weekday() == 5 else (d + dt.timedelta(days=1) if d.weekday() == 6 else d)


def nyse_holidays(y):
    """NYSE full-day holidays (rules since 2022). New Year's on a Saturday is not observed on the prior Friday."""
    h = {_nth_weekday(y, 1, 0, 3), _nth_weekday(y, 2, 0, 3), _easter(y) - dt.timedelta(days=2), _last_weekday(y, 5, 0),
         _observed(dt.date(y, 6, 19)), _observed(dt.date(y, 7, 4)), _nth_weekday(y, 9, 0, 1), _nth_weekday(y, 11, 3, 4),
         _observed(dt.date(y, 12, 25))}
    ny = dt.date(y, 1, 1)
    if ny.weekday() != 5: h.add(_observed(ny))
    return h


def is_session(d):
    return d.weekday() < 5 and d not in nyse_holidays(d.year)


def sessions(a, b):
    """US sessions a..b inclusive."""
    out, d = [], a
    while d <= b:
        if is_session(d): out.append(d)
        d += dt.timedelta(days=1)
    return out


def shift_session(d, n):
    step = 1 if n > 0 else -1; k = 0
    while k != n:
        d += dt.timedelta(days=step)
        if is_session(d): k += step
    return d


def last_session_of_month(d):
    e = dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1) - dt.timedelta(days=1)
    while not is_session(e): e -= dt.timedelta(days=1)
    return e


def first_session_of_month(d):
    e = dt.date(d.year, d.month, 1)
    while not is_session(e): e += dt.timedelta(days=1)
    return e


def tom_dates(d):
    """For session d's month: (signal session il-2, entry session il-1 [at the open], exit session il+3 [at the close])."""
    il = last_session_of_month(d)
    return shift_session(il, -2), shift_session(il, -1), shift_session(il, 3)


def final_session(now_utc=None):
    """Latest US session whose daily bar is final."""
    now = (now_utc or dt.datetime.now(UTC)).astimezone(ET); d = now.date()
    if is_session(d) and (now.hour, now.minute) >= FINAL_AFTER_ET: return d
    d -= dt.timedelta(days=1)
    while not is_session(d): d -= dt.timedelta(days=1)
    return d


# ----------------------------------------------------------------------------------------------------------- data
def fetch_daily(symbols, period):
    """{sym: {date: (open, close, adjclose)}} from Yahoo via yfinance (the desk's existing price source)."""
    import yfinance as yf
    syms = sorted(set(symbols))
    df = yf.download(syms, period=period, interval="1d", auto_adjust=False, actions=False, progress=False, group_by="column", threads=True)
    out = {}
    if df is None or df.empty: return out
    for s in syms:
        try:
            o, c, a = df[("Open", s)], df[("Close", s)], df[("Adj Close", s)]
        except KeyError:
            continue
        rows = {}
        for ts in df.index:
            ov, cv, av = o.get(ts), c.get(ts), a.get(ts)
            if cv is None or av is None or not (cv == cv) or not (av == av) or cv <= 0: continue
            rows[ts.date()] = (float(ov) if ov == ov and ov else float(cv), float(cv), float(av))
        if rows: out[s] = rows
    return out


def sma_signal(px_qqq, d, n=SMA_N):
    """(adj close, SMA_n of adj closes, above?) at session d, or (None, None, None) without n sessions of history."""
    ds = sorted(x for x in px_qqq if x <= d)
    if len(ds) < n or ds[-1] != d: return None, None, None
    closes = [px_qqq[x][2] for x in ds[-n:]]
    sma = sum(closes) / n; c = px_qqq[d][2]
    return c, sma, bool(c > sma)


def spy_r5_negative(px_spy, d):
    ds = sorted(x for x in px_spy if x <= d)
    if len(ds) < 6 or ds[-1] != d: return None
    return px_spy[ds[-1]][2] / px_spy[ds[-6]][2] - 1 < 0


def load_value_weights(signal_needed, builder=None):
    """Value weights {ticker: w} for the month-end signal `signal_needed` (ISO date). Uses value_prof_sleeve's
    target_latest.json; if its signal is older, builds a fresh target in-process (nothing is written to reports/)."""
    t = None
    try: t = _read_json(VALUE_TARGET)
    except Exception: t = None
    stale = not t or str(t.get("signal_date", "")) < signal_needed
    if stale:
        try:
            if builder is None:
                sys.path.insert(0, BASE); import value_prof_sleeve as vps; builder = vps.build_target
            fresh = builder()
            if fresh and fresh.get("targets"): t, stale = fresh, str(fresh.get("signal_date", "")) < signal_needed
        except Exception as e:
            print("value target rebuild unavailable: %s" % str(e)[:160])
    if not t or not t.get("targets"): return {}, None, True
    w = {r["ticker"]: float(r["weight"]) for r in t["targets"] if float(r.get("weight") or 0) > 0}
    tot = sum(w.values())
    return ({k: v / tot for k, v in w.items()} if tot > 0 else {}), t.get("signal_date"), stale


# ----------------------------------------------------------------------------------------------------------- state
def _guard(obj):
    bad = LEDGER_KEYS & set(obj)
    if bad: raise ValueError("refusing ledger-shaped keys %s in the shadow state" % sorted(bad))


def _read_json(path):
    with open(path) as f: return json.load(f)


def load_state(path=None):
    try: return _read_json(path or STATE_PATH)
    except Exception: return None


def save_state(st, path=None, history_rows=(), history_path=None):
    _guard(st)
    path = path or STATE_PATH; hp = history_path or HISTORY_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(st, f, indent=1, default=str)
    os.replace(tmp, path)
    if history_rows:
        new = not os.path.exists(hp)
        with open(hp, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=HISTORY_FIELDS, extrasaction="ignore")
            if new: w.writeheader()
            for r in history_rows: w.writerow(r)


def pending_state(note):
    return {"label": LABEL, "status": STATUS, "phase": "PENDING_FIRST_MARK", "paper_shadow_only": True, "in_firm_equity": False,
            "in_funder_fee": False, "orders": "NONE", "start_date": START_DATE, "notional_usd": NOTIONAL_USD,
            "note": note, "updated_utc": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "design": __doc__.split("Design")[1].split("Commands:")[0].strip()}


def _value_total(st): return sum(st["value_sleeve"]["names_usd"].values())


def shadow_value(st):
    return st["shadow_cash_usd"] + st["qld"]["usd"] + _value_total(st) + st["tom"]["usd"]


def _brake_update(st, v):
    b = st["brake"]
    b["ref_usd"] = max(b["ref_usd"], v)
    b["cut_days"] = b["cut_days"] + 1 if b["mult"] < 1 else 0
    if b["cut_days"] >= BRAKE_COOL: b["ref_usd"] = v; b["cut_days"] = 0; b["resets"] = b.get("resets", 0) + 1
    dd = v / b["ref_usd"] - 1; m = 1.0
    for th, mm in BRAKE_TIERS:
        if dd <= th: m = mm
    changed = m != b["mult"]; b["mult"] = m; b["dd_pct"] = round(100 * dd, 4)
    return changed


def _trade(st, kind, target, cost_rate, events):
    """Move cash <-> sleeve to reach target; cost charged to cash. Returns cost."""
    if kind == "qld":
        cur = st["qld"]["usd"]; delta = target - cur; st["qld"]["usd"] = target
    else:
        raise ValueError(kind)
    cost = abs(delta) * cost_rate; st["shadow_cash_usd"] -= delta + cost
    if abs(delta) > 0.005: events.append("QLD %+.0f" % delta)
    return cost


def _rebalance_value(st, target_total, weights, cost_rate, events):
    names = st["value_sleeve"]["names_usd"]; cur_total = sum(names.values())
    new = {k: target_total * w for k, w in weights.items()}
    traded = sum(abs(new.get(k, 0.0) - names.get(k, 0.0)) for k in set(new) | set(names))
    cost = traded * cost_rate
    st["shadow_cash_usd"] -= (sum(new.values()) - cur_total) + cost
    st["value_sleeve"]["names_usd"] = new
    events.append("VALUE rebalance %.0f -> %.0f (%d names, traded %.0f)" % (cur_total, sum(new.values()), len(new), traded))
    return cost


def _decide_and_rebalance(st, d, px, events, force=False, value_loader=load_value_weights):
    """At the close of session d: QQQ filter, brake multiplier, monthly value rebalance, TOM scheduling."""
    v = shadow_value(st); m = st["brake"]["mult"]; cost = 0.0
    c, sma, above = sma_signal(px.get("QQQ", {}), d)
    if above is None:
        above = bool(st["qld"].get("on")); events.append("QQQ SMA200 unavailable: QLD state held")
    st["qqq"] = {"date": d.isoformat(), "adj_close": c, "sma200": sma, "above_sma200": above}
    flip = above != bool(st["qld"].get("on")); mult_changed = m != st["qld"].get("mult_at_trade")
    vs = st["value_sleeve"]
    month_rb = d == first_session_of_month(d) and vs.get("rebalanced_month") != d.strftime("%Y-%m")   # research: all sleeves to target monthly
    if force or flip or mult_changed or (month_rb and above):
        if "QLD" not in px or d not in px["QLD"]:
            events.append("QLD price missing: trade deferred")
        else:
            cost += _trade(st, "qld", (W_QLD * m * v) if above else 0.0, COST_QLD, events)
            st["qld"]["on"] = above; st["qld"]["mult_at_trade"] = m
            if flip: events.append("QLD %s (QQQ %s SMA200)" % ("ON" if above else "OFF", "above" if above else "below"))
    if force or month_rb or m != vs.get("mult_at_trade"):
        sig_needed = shift_session(first_session_of_month(d), -1).isoformat() if d == first_session_of_month(d) else vs.get("signal_date") or ""
        if force or month_rb or not vs.get("weights"):
            w, sig, stale = value_loader(sig_needed)
            if w:
                avail = {k: x for k, x in w.items() if k in px and d in px[k]}
                tot = sum(avail.values())
                if tot > 0:
                    vs["weights"] = {k: x / tot for k, x in avail.items()}; vs["signal_date"] = sig; vs["weights_stale"] = bool(stale)
                    vs["names_without_price"] = sorted(set(w) - set(avail))
        if vs.get("weights"):
            cost += _rebalance_value(st, W_VALUE * m * v, vs["weights"], COST_VALUE, events)
            vs["mult_at_trade"] = m
            if month_rb or force: vs["rebalanced_month"] = d.strftime("%Y-%m")
        else:
            events.append("value weights unavailable: sleeve left in cash")
    t = st["tom"]
    sig_d, entry_d, exit_d = tom_dates(d)
    if d == sig_d and not t.get("active") and not t.get("pending_entry"):
        neg = spy_r5_negative(px.get("SPY", {}), d)
        if neg:
            idle = v * (1 - (W_QLD if above else 0.0) - W_VALUE) - RESERVE_USD
            amt = max(0.0, min(m * idle, st["shadow_cash_usd"]))
            if amt > 0:
                t.update(pending_entry=entry_d.isoformat(), exit_date=exit_d.isoformat(), amount_usd=round(amt, 2))
                events.append("TOM scheduled: SPY %.0f at open %s, exit close %s" % (amt, entry_d, exit_d))
        else:
            events.append("TOM skipped (SPY 5-session return not negative)" if neg is False else "TOM skipped (SPY history missing)")
    return cost


def _mark_session(st, d, prev_d, px, rf_annual, events):
    """Mark all sleeves from the close of prev_d to the close of d. Returns cost (TOM legs)."""
    cost = 0.0
    days = (d - prev_d).days
    st["shadow_cash_usd"] *= (1 + rf_annual / 365.0) ** days
    def ratio(sym):
        p = px.get(sym, {})
        if d in p and prev_d in p: return p[d][2] / p[prev_d][2]
        return None
    r = ratio("QLD")
    if st["qld"]["usd"]:
        if r is None: events.append("QLD mark missing: held flat")
        else: st["qld"]["usd"] *= r
    names = st["value_sleeve"]["names_usd"]; missing = []
    for k in list(names):
        rk = ratio(k)
        if rk is None: missing.append(k)
        else: names[k] *= rk
    if missing: events.append("value marks missing (held flat): %s" % ",".join(sorted(missing)[:8]))
    t = st["tom"]
    if t.get("active"):
        rs = ratio("SPY")
        if rs is None: events.append("SPY mark missing: TOM held flat")
        else: t["usd"] *= rs
    elif t.get("pending_entry") == d.isoformat():
        spy = px.get("SPY", {}).get(d)
        if spy:
            o, c, a = spy
            amt = min(float(t["amount_usd"]), max(0.0, st["shadow_cash_usd"]))
            st["shadow_cash_usd"] -= amt
            t["usd"] = amt * (c / o) * (1 - COST_TOM_SIDE); cost += amt * COST_TOM_SIDE
            t["active"] = True; t["entered"] = d.isoformat(); t.pop("pending_entry", None)
            events.append("TOM entered SPY %.0f at open" % amt)
        else:
            t.pop("pending_entry", None); events.append("TOM entry skipped (no SPY bar)")
    if t.get("active") and t.get("exit_date") and d.isoformat() >= t["exit_date"]:
        proceeds = t["usd"] * (1 - COST_TOM_SIDE); cost += t["usd"] * COST_TOM_SIDE
        st["shadow_cash_usd"] += proceeds; events.append("TOM exit SPY %.0f at close" % proceeds)
        t.update(usd=0.0, active=False); t.pop("exit_date", None); t.pop("entered", None); t.pop("amount_usd", None)
    return cost


def _new_state():
    return {"label": LABEL, "status": STATUS, "phase": "LIVE_SHADOW", "paper_shadow_only": True, "in_firm_equity": False,
            "in_funder_fee": False, "orders": "NONE", "start_date": START_DATE, "notional_usd": NOTIONAL_USD,
            "shadow_cash_usd": NOTIONAL_USD, "qld": {"usd": 0.0, "on": None, "mult_at_trade": None},
            "value_sleeve": {"names_usd": {}, "weights": {}, "signal_date": None, "rebalanced_month": None, "mult_at_trade": None},
            "tom": {"usd": 0.0, "active": False},
            "brake": {"ref_usd": NOTIONAL_USD, "mult": 1.0, "cut_days": 0, "dd_pct": 0.0, "resets": 0},
            "peak_usd": NOTIONAL_USD, "max_drawdown_pct": 0.0, "proforma_after_fee_usd": NOTIONAL_USD, "costs_paid_usd": 0.0,
            "last_mark_date": None, "last_mark_utc": None, "sessions_marked": 0,
            "design": __doc__.split("Design")[1].split("Commands:")[0].strip()}


def _history_row(st, d, costs, events):
    v = shadow_value(st)
    return {"date": d.isoformat(), "shadow_value_usd": round(v, 2), "cash_usd": round(st["shadow_cash_usd"], 2), "qld_usd": round(st["qld"]["usd"], 2),
            "value_usd": round(_value_total(st), 2), "tom_spy_usd": round(st["tom"]["usd"], 2), "qld_on": st["qld"]["on"],
            "qqq_close": st.get("qqq", {}).get("adj_close"), "qqq_sma200": st.get("qqq", {}).get("sma200"),
            "brake_mult": st["brake"]["mult"], "brake_cut_days": st["brake"]["cut_days"],
            "drawdown_from_peak_pct": round(100 * (v / st["peak_usd"] - 1), 4), "brake_dd_pct": st["brake"]["dd_pct"],
            "return_since_start_pct": round(100 * (v / NOTIONAL_USD - 1), 4), "proforma_after_fee_usd": round(st["proforma_after_fee_usd"], 2),
            "costs_usd": round(costs, 2), "events": "; ".join(events)}


def advance(st, px, upto, now_utc=None, value_loader=load_value_weights):
    """Pure-ish core: process every session in (last_mark_date, upto]. Returns (state, history_rows)."""
    start = dt.date.fromisoformat(START_DATE); rows = []
    irx = px.get("^IRX", {})
    def rf_at(d):
        ks = [k for k in irx if k <= d]
        return (irx[max(ks)][1] / 100.0) if ks else 0.04
    if st is None or st.get("phase") != "LIVE_SHADOW":
        if upto < start or start not in px.get("QQQ", {}):
            return pending_state("Waiting for the final US close of %s (marks after 16:20 ET; first launchd run after that)." % START_DATE), []
        st = _new_state(); events = ["START notional %.0f at the %s close" % (NOTIONAL_USD, START_DATE)]
        cost = _decide_and_rebalance(st, start, px, events, force=True, value_loader=value_loader)
        st["costs_paid_usd"] += cost
        v = shadow_value(st); st["peak_usd"] = max(st["peak_usd"], v); st["proforma_after_fee_usd"] = v
        st["last_mark_date"] = start.isoformat(); st["sessions_marked"] = 1
        st["value_at_last_mark"] = v
        rows.append(_history_row(st, start, cost, events))
    last = dt.date.fromisoformat(st["last_mark_date"])
    for d in sessions(last + dt.timedelta(days=1), upto):
        if d not in px.get("QQQ", {}):
            if any(k > d for k in px.get("QQQ", {})):
                continue     # unscheduled closure (calendar said session, Yahoo has later bars but none for d)
            break            # bar not available yet: stop, retry next run (never skip a session)
        events = []; prev = dt.date.fromisoformat(st["last_mark_date"]); v0 = shadow_value(st)
        cost = _mark_session(st, d, prev, px, rf_at(prev), events)
        v = shadow_value(st)
        if _brake_update(st, v): events.append("BRAKE x%.2f (DD %.2f%% vs ref)" % (st["brake"]["mult"], st["brake"]["dd_pct"]))
        cost += _decide_and_rebalance(st, d, px, events, value_loader=value_loader)
        v = shadow_value(st); st["costs_paid_usd"] += cost
        st["peak_usd"] = max(st["peak_usd"], v)
        st["max_drawdown_pct"] = min(st["max_drawdown_pct"], round(100 * (v / st["peak_usd"] - 1), 4))
        st["proforma_after_fee_usd"] *= (v / v0 if v0 > 0 else 1.0) * fee_factor(prev, d)
        st["last_mark_date"] = d.isoformat(); st["sessions_marked"] += 1; st["value_at_last_mark"] = v
        rows.append(_history_row(st, d, cost, events))
    if rows:
        st["last_mark_utc"] = (now_utc or dt.datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    st["updated_utc"] = (now_utc or dt.datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return st, rows


def run(now_utc=None, fetch=fetch_daily, state_path=None, history_path=None, value_loader=load_value_weights):
    os.makedirs(os.path.dirname(state_path or STATE_PATH), exist_ok=True)
    import fcntl
    with open(LOCK_PATH if state_path is None else state_path + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        st = load_state(state_path)
        upto = final_session(now_utc)
        last = st.get("last_mark_date") if st and st.get("phase") == "LIVE_SHADOW" else None
        if last and last >= upto.isoformat():
            return st, []
        px = fetch(CORE, "2y")
        names = set((st or {}).get("value_sleeve", {}).get("names_usd", {}) or {})
        try: names |= {r["ticker"] for r in _read_json(VALUE_TARGET).get("targets", [])}
        except Exception: pass
        if names: px.update(fetch(sorted(names), "6mo"))
        st2, rows = advance(st, px, upto, now_utc, value_loader)
        save_state(st2, state_path, rows, history_path)
        return st2, rows


# ----------------------------------------------------------------------------------------------------------- dashboard
def summary(path=None):
    """Read-only dashboard payload (command_server /api/shadow and the Overview panel). Never fetches, never writes."""
    st = load_state(path)
    if not st:
        return {"status": "NOT_STARTED", "label": LABEL, "start_date": START_DATE, "notional_usd": NOTIONAL_USD, "in_firm_equity": False,
                "note": "No shadow state yet (state/shadow_nasdaq2x.json)."}
    out = {"status": "OK", "phase": st.get("phase"), "label": LABEL, "start_date": st.get("start_date"), "notional_usd": st.get("notional_usd"),
           "in_firm_equity": False, "in_funder_fee": False, "orders": "NONE", "updated_utc": st.get("updated_utc"), "note": st.get("note")}
    if st.get("phase") != "LIVE_SHADOW":
        return out
    v = shadow_value(st); b = st["brake"]; q = st.get("qqq") or {}
    out.update({"shadow_value_usd": round(v, 2), "return_since_start_pct": round(100 * (v / st["notional_usd"] - 1), 3),
                "pnl_since_start_usd": round(v - st["notional_usd"], 2), "max_drawdown_pct": st.get("max_drawdown_pct"),
                "drawdown_from_peak_pct": round(100 * (v / st["peak_usd"] - 1), 3), "peak_usd": round(st["peak_usd"], 2),
                "qld_on": bool(st["qld"].get("on")), "qqq_adj_close": q.get("adj_close"), "qqq_sma200": q.get("sma200"), "qqq_signal_date": q.get("date"),
                "brake_mult": b["mult"], "brake_state": "NORMAL (x1.00)" if b["mult"] >= 1 else "CUT x%.2f (%d/%d sessions to re-risk)" % (b["mult"], b["cut_days"], BRAKE_COOL),
                "brake_dd_pct": b.get("dd_pct"), "last_mark_date": st.get("last_mark_date"), "last_mark_utc": st.get("last_mark_utc"),
                "sessions_marked": st.get("sessions_marked"),
                "sleeves_usd": {"qld": round(st["qld"]["usd"], 2), "value": round(_value_total(st), 2), "tom_spy": round(st["tom"]["usd"], 2),
                                "cash_tbill": round(st["shadow_cash_usd"], 2)},
                "value_names": len(st["value_sleeve"]["names_usd"]), "value_signal_date": st["value_sleeve"].get("signal_date"),
                "tom_active": bool(st["tom"].get("active")), "tom_pending_entry": st["tom"].get("pending_entry"),
                "proforma_after_fee_usd": round(st.get("proforma_after_fee_usd", v), 2), "costs_paid_usd": round(st.get("costs_paid_usd", 0.0), 2)})
    return out


def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="c")
    sp.add_parser("run"); sp.add_parser("status")
    a = ap.parse_args()
    if a.c == "status":
        print(json.dumps(summary(), indent=1)); return
    st, rows = run()
    print(json.dumps({"processed_sessions": [r["date"] for r in rows], **summary()}, indent=1, default=str))


if __name__ == "__main__":
    main()
