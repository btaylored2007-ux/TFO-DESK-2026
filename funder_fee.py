#!/usr/bin/env python3
"""PAPER funder-fee ledger (2 Oct 2026; Brett: fee = % of equity, scales with capital).
Scheme FLAT_DAILY (Brett, 2 Oct 2026 19:11 AEST: "the fee must pay out regardless", accrued and paid DAILY so it is squared every day):
  every calendar day at 09:00 Australia/Sydney (DST-aware) the desk accrues AND pays a flat % of net-of-fee equity,
  whether or not equity is at the high-water mark. No reserve, no floor rate, no smoothing, no HWM scheme.
  RATE HISTORY (RATE_SCHEDULE; each accrual uses the rate for its own Sydney fee date; past charges are never restated):
    from the 2026-10-02 inception (charged 3 Oct): 0.047619%/day (= 0.3333%/wk / 7, ~17.4%/yr simple)
    from the 2026-10-04 charge (Sun 4 Oct 09:00 AEDT): 0.0274%/day (~10.0%/yr simple; Brett, 3 Oct 2026 19:17 AEST)
  The HWM is still tracked (ratchets UP only, daily, to net equity after the fee) for DISPLAY ONLY; it never changes the fee.
  The first accrual (Sat 3 Oct 09:00 AEST) is pro-rated from the 2 Oct inception. External capital flows shift the HWM and P&L baselines.
  Replaced HWM_FLEX_RESERVE (weekly Saturday accrual, earned-fee reserve) before any accrual had been made.
Net-of-fee equity = firm equity (reports/firm_equity.csv, same source as tfo_buy.firm_state) - cumulative fees paid since inception.
ACCOUNTING ONLY: it never moves cash between books, never trades, never touches a rail. PAPER.
  funder_fee.py tick       init if needed, daily mark, daily 09:00 accrual+payment (idempotent per Sydney day, catches up), write summary
                           (launchd com.tfo.funder-fee: hourly + 09:01 local)
  funder_fee.py summary    print the live summary JSON (what /api/funder serves)
Files: reports/funder_fee_ledger.jsonl (events: inception, mark, scheme, accrual), reports/funder_fee_summary.json."""
import csv, datetime as dt, json, os, sys, tempfile
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.abspath(__file__)); R = os.path.join(BASE, "reports")
SYD, UTC = ZoneInfo("Australia/Sydney"), dt.timezone.utc
SCHEME = "FLAT_DAILY"
RATE_DAY_V1 = (4000.0 / 1.2e6) / 7    # 0.047619%/day (0.3333%/wk / 7; Brett: $4,000/wk on $1.2M) -- charged 3 Oct 2026 only
RATE_DAY_V2 = 0.000274                # 0.0274%/day (~10%/yr) -- Brett, 3 Oct 2026 19:17 AEST, from the 4 Oct 2026 charge
# (first Sydney fee date it applies to, rate per calendar day, change record). Append only: history is kept, past charges are never restated.
RATE_SCHEDULE = [
    ("2026-10-02", RATE_DAY_V1, "Brett 2 Oct 2026 19:11 AEST: FLAT_DAILY 0.047619%/day (0.3333%/wk / 7) of net-of-fee equity, paid daily regardless of HWM"),
    ("2026-10-04", RATE_DAY_V2, "Brett 3 Oct 2026 19:17 AEST: lowered to a flat 0.0274%/day (~10%/yr) of net-of-fee equity; still charged and paid daily, scales "
                                "with equity, no HWM scheme; effective from the Sun 4 Oct 2026 09:00 Sydney charge; earlier charges not restated"),
]
RATE_DAY = RATE_SCHEDULE[-1][1]       # current rate (0.0274%/day), paid every calendar day regardless of HWM
RATE_TARGET_WK = RATE_DAY * 7         # 0.1918%/wk
RATE_TARGET = RATE_TARGET_WK          # back-compat alias


def rate_for(sydney_date):
    """Rate per calendar day for the accrual dated sydney_date (ISO 'YYYY-MM-DD', Sydney fee date)."""
    d = str(sydney_date)[:10]; r = RATE_SCHEDULE[0][1]
    for eff, rate, _ in RATE_SCHEDULE:
        if d >= eff: r = rate
    return r


def after_fee_cagr_pct(pre_fee_pct, rate=None):
    """Pre-fee CAGR -> after-fee CAGR for a flat fee charged every calendar day on equity: (1+g)(1-r)^365 - 1."""
    r = RATE_DAY if rate is None else rate
    return 100 * ((1 + pre_fee_pct / 100.0) * (1 - r) ** 365 - 1)
ACCRUAL_HOUR = 9                      # 09:00 Sydney, every calendar day
MAX_EQUITY_AGE_H = 6                  # an accrual uses the firm-equity row at/before the boundary if it is <= 6h old ...
FALLBACK_AFTER_H = 6                  # ... else waits up to 6h after the boundary for a row, then uses the first row after it (flagged)
DRAG_SIMPLE_PCT = 100 * RATE_DAY * 365                      # ~10.0%/yr (0.0274%/day x 365)
DRAG_COMPOUND_PCT = 100 * (1 - (1 - RATE_DAY) ** 365)        # ~9.52% of capital/yr when charged daily on net equity
BREAK_EVEN_PRE_FEE_PCT = 100 * ((1 - RATE_DAY) ** -365 - 1)  # ~10.52% pre-fee CAGR just to stand still after the fee
DRAG_COMPOUND_PCT_V1 = 100 * (1 - (1 - RATE_DAY_V1) ** 365)  # ~15.96% (old rate, for reference)
BREAK_EVEN_PRE_FEE_PCT_V1 = 100 * ((1 - RATE_DAY_V1) ** -365 - 1)  # ~18.99% (old rate, for reference)
RULE_TEXT = ("Flat daily: every calendar day at 09:00 Sydney the desk accrues and pays 0.0274% (~10%/yr) of net-of-fee equity, "
             "whether or not equity is at the high-water mark. No reserve, no floor, no HWM scheme. HWM tracked for display only. "
             "Rate history: 0.047619%/day for the 3 Oct 2026 charge; 0.0274%/day from the Sun 4 Oct 2026 charge (Brett, 3 Oct 19:17 AEST). "
             "PAPER accounting only.")
R2_AFTER_FEE = {k: round(after_fee_cagr_pct(g), 2) for k, g in (("holdout_2021_26", 20.7), ("full_1988_2026", 22.4), ("reviewers_low", 16.0), ("reviewers_high", 17.5))}
MODEL_NOTE = ("Annual fee drag ~%.1f%%/yr simple (0.0274%%/day x 365); ~%.2f%% of capital/yr when charged daily on net equity. "
              "Pre-fee break-even ~%.2f%% CAGR ((1-0.000274)^-365 - 1). R2 trend book after fee: 2021-26 holdout 20.7%% -> %.1f%%; "
              "full 22.4%% -> %.1f%%; reviewers' realistic 16-17.5%% -> %.1f-%.1f%%. (Old 0.047619%%/day: drag ~%.1f%%, break-even ~%.1f%%.)"
              % (DRAG_SIMPLE_PCT, DRAG_COMPOUND_PCT, BREAK_EVEN_PRE_FEE_PCT, R2_AFTER_FEE["holdout_2021_26"], R2_AFTER_FEE["full_1988_2026"],
                 R2_AFTER_FEE["reviewers_low"], R2_AFTER_FEE["reviewers_high"], DRAG_COMPOUND_PCT_V1, BREAK_EVEN_PRE_FEE_PCT_V1))


def _p(name, reports=None): return os.path.join(reports or R, name)
def _iso(t): return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
def _parse(s): return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
def _syd(t, fmt="%a %d %b %H:%M %Z"): return t.astimezone(SYD).strftime(fmt)


def equity_rows(reports=None):
    out = []
    with open(_p("firm_equity.csv", reports)) as f:
        for r in csv.DictReader(f):
            try:
                out.append({"utc": _parse(r["utc"]), "equity": float(r["firm_equity_usd"]), "peak": float(r.get("firm_peak_usd") or 0),
                            "event": str(r.get("capital_event") or "").strip().upper() == "YES"})
            except (TypeError, ValueError, KeyError):
                continue
    return out


def flows_between(rows, t0, t1):
    """External capital flow estimate between t0 (excl) and t1 (incl): peak shift at rows flagged capital_event=YES
    (firm_capital_flows shifts the firm peak by the net flow)."""
    tot, prev = 0.0, None
    for r in rows:
        if prev is not None and r["event"] and t0 < r["utc"] <= t1:
            tot += r["peak"] - prev["peak"]
        prev = r
    return tot


def row_at(rows, t):
    best = None
    for r in rows:
        if r["utc"] <= t: best = r
        else: break
    return best


def load(reports=None):
    p = _p("funder_fee_ledger.jsonl", reports)
    if not os.path.exists(p): return []
    out = []
    with open(p) as f: lines = f.readlines()
    for line in lines:
        line = line.strip()
        if line:
            try: out.append(json.loads(line))
            except ValueError: pass
    return out


def _append(rec, reports=None):
    with open(_p("funder_fee_ledger.jsonl", reports), "a") as f: f.write(json.dumps(rec) + "\n")


def boundary_on(d):
    """09:00 Sydney on Sydney calendar date d, as UTC (AEST 23:00Z prev day, AEDT 22:00Z prev day)."""
    return dt.datetime.combine(d, dt.time(ACCRUAL_HOUR), tzinfo=SYD).astimezone(UTC)


def last_boundary(now):
    """Most recent 09:00 Sydney boundary at or before now."""
    d = now.astimezone(SYD).date(); b = boundary_on(d)
    return b if b <= now else boundary_on(d - dt.timedelta(days=1))


def next_boundary_after(t):
    """First 09:00 Sydney boundary strictly after t."""
    d = t.astimezone(SYD).date(); b = boundary_on(d)
    return b if b > t else boundary_on(d + dt.timedelta(days=1))


def last_weekday_boundary(now, weekday):
    b = last_boundary(now)
    while b.astimezone(SYD).weekday() != weekday: b = boundary_on(b.astimezone(SYD).date() - dt.timedelta(days=1))
    return b


def last_month_boundary(now):
    """09:00 Sydney on the 1st of the month of the most recent boundary."""
    return boundary_on(last_boundary(now).astimezone(SYD).date().replace(day=1))


def state(L):
    inc = next((e for e in L if e["event"] == "inception"), None)
    if inc is None: return None
    acc = [e for e in L if e["event"] == "accrual"]
    last = acc[-1] if acc else None
    return {"inception": inc, "accruals": acc, "last": last,
            "cum_fee": last["cum_fee_after_usd"] if last else 0.0,
            "cum_payout": last.get("cum_payout_after_usd", last["cum_fee_after_usd"]) if last else 0.0,
            "hwm": last["hwm_after_usd"] if last else inc["hwm_usd"],
            "period_start_utc": last["boundary_utc"] if last else inc["utc"],
            "period_start_row_utc": last["equity_row_utc"] if last else inc["utc"],
            "gross_at_period_start": last["equity_gross_usd"] if last else inc["equity_gross_usd"]}


def fee_rule(net, stub=1.0, sydney_date=None):
    """Flat: the fee does not depend on the HWM. The rate is the RATE_SCHEDULE rate for the accrual's Sydney fee date."""
    rate = RATE_DAY if sydney_date is None else rate_for(sydney_date)
    return rate, rate * net * stub


def _stub(st, nb):
    return 1.0 if st["last"] else max(0.0, min(1.0, (nb - _parse(st["inception"]["utc"])).total_seconds() / 86400))  # only the inception stub is pro-rated (23h/25h DST days count as full)


def tick(now=None, reports=None):
    now = now or dt.datetime.now(UTC); rows = equity_rows(reports); L = load(reports)
    if not rows: raise RuntimeError("firm_equity.csv has no rows")
    cur = rows[-1]
    if not any(e["event"] == "inception" for e in L):
        rec = {"event": "inception", "utc": _iso(cur["utc"]), "logged_utc": _iso(now), "equity_gross_usd": round(cur["equity"], 2), "hwm_usd": round(cur["equity"], 2),
               "scheme": SCHEME, "rate_day": RATE_DAY, "rule": RULE_TEXT, "paper_only": True}
        _append(rec, reports); L.append(rec)
    st = state(L)
    # daily mark (first tick of each Sydney day)
    sd = now.astimezone(SYD).date().isoformat()
    if not any(e["event"] == "mark" and e.get("sydney_date") == sd for e in L):
        rec = {"event": "mark", "utc": _iso(cur["utc"]), "logged_utc": _iso(now), "sydney_date": sd, "equity_gross_usd": round(cur["equity"], 2),
               "cum_fee_usd": round(st["cum_fee"], 2), "equity_net_usd": round(cur["equity"] - st["cum_fee"], 2), "hwm_usd": round(st["hwm"], 2)}
        _append(rec, reports); L.append(rec)
    # daily 09:00 accrual + payment: catch up every missed boundary after the period start, one per Sydney day
    while True:
        st = state(L); ps = _parse(st["period_start_utc"]); nb = next_boundary_after(ps)
        if nb > now: break
        fee_date = nb.astimezone(SYD).date().isoformat()
        if any(a.get("sydney_date") == fee_date for a in st["accruals"]): break  # idempotency guard (never two accruals for one Sydney day)
        r = row_at(rows, nb); flag = None
        if r is None or (nb - r["utc"]).total_seconds() > MAX_EQUITY_AGE_H * 3600:
            after = next((x for x in rows if x["utc"] > nb), None)
            if after is None or (now - nb).total_seconds() < FALLBACK_AFTER_H * 3600: break  # wait for a fresh equity row
            r = after; flag = "equity row %s used (no row within %dh before the boundary)" % (_iso(r["utc"]), MAX_EQUITY_AGE_H)
        flows = flows_between(rows, _parse(st["period_start_row_utc"]), r["utc"])
        hwm = st["hwm"] + flows
        net = r["equity"] - st["cum_fee"]
        stub = _stub(st, nb)
        rate, fee = fee_rule(net, stub, fee_date)
        at = net >= hwm - 0.005
        pnl_g = r["equity"] - st["gross_at_period_start"] - flows
        rec = {"event": "accrual", "utc": _iso(now), "boundary_utc": _iso(nb), "boundary_sydney": nb.astimezone(SYD).strftime("%Y-%m-%d %H:%M %Z"), "sydney_date": fee_date,
               "equity_row_utc": _iso(r["utc"]), "equity_gross_usd": round(r["equity"], 2), "flows_usd": round(flows, 2),
               "cum_fee_before_usd": round(st["cum_fee"], 2), "equity_net_before_usd": round(net, 2), "hwm_before_usd": round(hwm, 2),
               "at_or_above_hwm": bool(at), "rate": rate, "stub_fraction": round(stub, 4), "fee_usd": round(fee, 2), "target_fee_usd": round(fee, 2),
               "payout_usd": round(fee, 2), "cum_fee_after_usd": round(st["cum_fee"] + fee, 2), "cum_payout_after_usd": round(st["cum_payout"] + fee, 2),
               "equity_net_after_usd": round(net - fee, 2), "hwm_after_usd": round(max(hwm, net - fee), 2),
               "pnl_gross_day_usd": round(pnl_g, 2), "pnl_net_day_usd": round(pnl_g - fee, 2), "scheme": SCHEME, "paper_only": True}
        if flag: rec["flag"] = flag
        _append(rec, reports); L.append(rec)
    s = summary(now, reports, rows=rows, L=L)
    _save(s, _p("funder_fee_summary.json", reports))
    return s


def _save(obj, path):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".funder_fee", suffix=".tmp")
    with os.fdopen(fd, "w") as f: json.dump(obj, f, indent=1)
    os.replace(tmp, path)


def _window(rows, st, cur, b0, fee_proj):
    """P&L from the 09:00 boundary b0 to now: baseline = the accrual at/just before b0 (or inception if b0 precedes it).
    after-fee = gross - fees paid at boundaries after the baseline - the current open day's projected fee."""
    inc = st["inception"]; inc_t = _parse(inc["utc"])
    base = None
    if b0 > inc_t:
        c = [a for a in st["accruals"] if _parse(a["boundary_utc"]) <= b0]
        base = c[-1] if c else None
    t_b, t_row, g0 = ((_parse(base["boundary_utc"]), _parse(base["equity_row_utc"]), base["equity_gross_usd"]) if base else (inc_t, inc_t, inc["equity_gross_usd"]))
    fees = sum(a["fee_usd"] for a in st["accruals"] if _parse(a["boundary_utc"]) > t_b)
    g = cur["equity"] - g0 - flows_between(rows, t_row, cur["utc"])
    return {"since_sydney": _syd(t_b), "pnl_gross_usd": round(g, 2), "fees_paid_usd": round(fees, 2), "projected_fee_open_day_usd": round(fee_proj, 2),
            "pnl_net_usd": round(g - fees - fee_proj, 2)}


def summary(now=None, reports=None, rows=None, L=None):
    now = now or dt.datetime.now(UTC)
    try:
        rows = rows if rows is not None else equity_rows(reports); L = L if L is not None else load(reports)
    except Exception as e:
        return {"status": "UNAVAILABLE", "error": str(e)[:200], "paper_only": True}
    st = state(L)
    if st is None or not rows:
        return {"status": "NOT_STARTED", "rule": RULE_TEXT, "paper_only": True}
    cur = rows[-1]; inc = st["inception"]; acc = st["accruals"]; last = st["last"]
    flows_open = flows_between(rows, _parse(st["period_start_row_utc"]), cur["utc"]); flows_all = flows_between(rows, _parse(inc["utc"]), cur["utc"])
    hwm = st["hwm"] + flows_open
    net = cur["equity"] - st["cum_fee"]
    at = net >= hwm - 0.005
    nb = next_boundary_after(_parse(st["period_start_utc"]))   # next accrual (may already be due if waiting for an equity row)
    stub = _stub(st, nb)
    rate, fee_proj = fee_rule(net, stub, nb.astimezone(SYD).date().isoformat())
    today = now.astimezone(SYD).date().isoformat()
    paid_today = next((a for a in reversed(acc) if a.get("sydney_date") == today), None)
    day = _window(rows, st, cur, last_boundary(now), fee_proj)
    week = _window(rows, st, cur, last_weekday_boundary(now, 5), fee_proj); week["basis"] = "since Saturday 09:00 Sydney"
    mb = last_month_boundary(now)
    month = _window(rows, st, cur, mb, fee_proj); month["month"] = mb.astimezone(SYD).strftime("%Y-%m"); month["basis"] = "since 09:00 on the 1st (Sydney)"
    paid = sum(a["fee_usd"] for a in acc); days = sum(a["stub_fraction"] for a in acc)
    return {"status": "OK", "paper_only": True, "scheme": SCHEME, "cadence": "daily 09:00 Australia/Sydney", "rule": RULE_TEXT, "model_note": MODEL_NOTE,
            "asof_utc": _iso(now), "equity_asof_utc": _iso(cur["utc"]), "inception_utc": inc["utc"], "inception_equity_usd": inc["equity_gross_usd"],
            "rate_pct_day": round(100 * rate, 6), "rate_pct_wk": round(100 * rate * 7, 4), "rate_pct_yr_simple": round(100 * rate * 365, 2),
            "rate_history": [{"from_sydney_fee_date": e, "rate_pct_day": round(100 * r_, 6), "note": n_} for e, r_, n_ in RATE_SCHEDULE],
            "rate_change": {"changed_sydney": "2026-10-03 19:17 AEST", "by": "Brett (funder)", "old_rate_pct_day": round(100 * RATE_DAY_V1, 6),
                            "new_rate_pct_day": round(100 * RATE_DAY_V2, 6), "effective_sydney_fee_date": "2026-10-04",
                            "effective_boundary_sydney": _syd(boundary_on(dt.date(2026, 10, 4))), "past_charges_restated": False},
            "break_even_pre_fee_cagr_pct": round(BREAK_EVEN_PRE_FEE_PCT, 2),
            "r2_trend_after_fee_cagr_pct": R2_AFTER_FEE,
            "annual_fee_drag": {"simple_pct": round(DRAG_SIMPLE_PCT, 1), "compound_pct": round(DRAG_COMPOUND_PCT, 1), "break_even_pre_fee_pct": round(BREAK_EVEN_PRE_FEE_PCT, 2),
                                "note": "~%.1f%%/yr (0.0274%%/day x 365); ~%.2f%% of capital per year when charged daily on net equity; pre-fee break-even ~%.2f%% CAGR"
                                        % (DRAG_SIMPLE_PCT, DRAG_COMPOUND_PCT, BREAK_EVEN_PRE_FEE_PCT)},
            "equity_gross_usd": round(cur["equity"], 2), "cum_fee_paid_usd": round(st["cum_fee"], 2), "cum_payout_usd": round(st["cum_payout"], 2),
            "equity_net_usd": round(net, 2), "hwm_usd": round(hwm, 2), "net_vs_hwm_usd": round(net - hwm, 2),
            "net_vs_hwm_pct": round(100 * (net / hwm - 1), 3) if hwm else None, "at_or_above_hwm": bool(at), "hwm_note": "display only; the fee is flat",
            "flows_since_inception_usd": round(flows_all, 2),
            "today": {"sydney_date": today, "fee_paid_usd": paid_today["fee_usd"] if paid_today else None,
                      "paid_at_sydney": paid_today["boundary_sydney"] if paid_today else None,
                      "stub_fraction": paid_today["stub_fraction"] if paid_today else None,
                      "next_accrual_utc": _iso(nb), "next_accrual_sydney": _syd(nb), "next_due_now": nb <= now, "rate_pct": round(100 * rate, 6),
                      "next_fee_projected_usd": round(fee_proj, 2), "next_payout_projected_usd": round(fee_proj, 2), "next_stub_fraction": round(stub, 4)},
            "last_day": None if not last else {k: last.get(k) for k in ("boundary_utc", "boundary_sydney", "sydney_date", "fee_usd", "payout_usd", "rate", "stub_fraction",
                                                                          "at_or_above_hwm", "pnl_gross_day_usd", "pnl_net_day_usd", "equity_net_after_usd", "hwm_after_usd")},
            "day": day, "week": week, "month": month,
            "since_inception": {"pnl_gross_usd": round(cur["equity"] - inc["equity_gross_usd"] - flows_all, 2), "fees_paid_usd": round(paid, 2),
                                "pnl_net_usd": round(cur["equity"] - inc["equity_gross_usd"] - flows_all - paid, 2)},
            "pace": {"accruals": len(acc), "days_accrued": round(days, 4), "paid_usd": round(paid, 2), "target_usd": round(paid, 2), "paid_vs_target_pct": 100.0 if acc else None,
                     "avg_daily_paid_usd": round(paid / days, 2) if days else None, "fee_per_day_now_usd": round(rate * net, 2), "fee_per_week_now_usd": round(rate * 7 * net, 2)},
            "history": [{k: a.get(k) for k in ("boundary_sydney", "sydney_date", "fee_usd", "rate", "stub_fraction", "at_or_above_hwm", "equity_net_after_usd", "hwm_after_usd",
                                               "pnl_gross_day_usd", "pnl_net_day_usd", "flag")} for a in acc[-14:]]}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "summary"
    if cmd == "tick":
        s = tick(); t = s["today"]
        print("FUNDER-FEE ok %s net %.2f hwm %.2f paid total %.2f today %s next %s ~%.2f" % (s["asof_utc"], s["equity_net_usd"], s["hwm_usd"], s["cum_fee_paid_usd"],
              "%.2f" % t["fee_paid_usd"] if t["fee_paid_usd"] is not None else "-", t["next_accrual_sydney"], t["next_fee_projected_usd"]))
    elif cmd == "summary":
        print(json.dumps(summary(), indent=1))
    else:
        print(__doc__); sys.exit(2)
