"""dashboard_api.py -- read-only aggregation for the interactive Private Wealth dashboard (PAPER ONLY).

Added 2026-10-03 (dashboard_interactive). Serves /api/dashboard via command_server.py.

What it does
  * freshness: one row per panel / data source with its source, as-of time, age and status
  * series:    equity curves (firm + sleeves) for the hover charts and time-range picker
  * fee:       funder-fee schedule + paid history, so the page can draw after-fee lines
  * benchmark: Brett's benchmark, 19%/yr BEFORE the funder fee (and its after-fee equivalent)
  * positions: every open position with venue / sleeve tags and its exit rails
  * fills:     head-trader fills with costs (entry + exit tickets, fees, fee detail)
  * alerts:    trend18_alerts.jsonl, kill status, mark refusals, blocked rails, stale sources
  * ibkr:      static IBKR paper mirror status (gateway lives on another machine; never contacted here)

Rules: reads files only. Never writes a ledger, never sends an order, never contacts a broker.
Every section is wrapped so one bad file degrades that section only.
"""
import csv
import datetime as dt
import json
import os
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
R = os.path.join(BASE, "reports")
S = os.path.join(BASE, "state")
UTC = dt.timezone.utc

BENCH_PRE_FEE_CAGR_PCT = 19.0          # Brett, 3 Oct 2026: benchmark is 19% a year BEFORE the funder fee
IBKR_MIRROR = {                         # Static: the IB gateway runs on the operator box, not this Mac. Never contacted.
    "status": "MIRROR_PENDING",
    "label": "Mirror pending",
    "account": "DU\u2026092 (paper)",
    "base_ccy": "AUD",
    "balance_note": "IBKR caps the paper balance at about A$1M, so orders will be mirrored at ~56% of the paper-ledger size.",
    "gateway_host": "operator box (not this Mac)",
    "first_mirror": "Trend18 entry at the Mon 5 Oct 2026 US close (06:50 AEDT Tue 6 Oct)",
    "role": "Execution check only: the paper ledger on this Mac stays the official record for the book and this dashboard.",
    "connected_from_dashboard": False,
}

_CACHE = {"ts": 0.0, "data": None}
_LOCK = threading.Lock()
TTL_S = 20.0


# ----------------------------------------------------------------------------- helpers
def _now():
    return dt.datetime.now(UTC)


def _parse(v):
    if v in (None, ""):
        return None
    try:
        if isinstance(v, (int, float)):
            return dt.datetime.fromtimestamp(float(v), UTC)
        s = str(v).strip().replace("Z", "+00:00")
        if len(s) == 10:
            s += "T00:00:00+00:00"
        t = dt.datetime.fromisoformat(s)
        return t if t.tzinfo else t.replace(tzinfo=UTC)
    except Exception:
        return None


def _iso(t):
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if t else None


def _load(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {} if default is None else default


def _mtime(path):
    try:
        return dt.datetime.fromtimestamp(os.path.getmtime(path), UTC)
    except OSError:
        return None


def _tail_jsonl(path, n=200):
    out = []
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 400_000))
            lines = f.read().decode("utf-8", "replace").splitlines()
        for ln in lines[-n:]:
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
    except OSError:
        pass
    return out


def _f(x, d=None):
    try:
        v = float(x)
        return v if v == v else d
    except (TypeError, ValueError):
        return d


def venue_of(sym, book=""):
    s = str(sym or "")
    if "/" in s or s.startswith("PF_") or s.endswith("-USD") or "crypto" in str(book):
        return "Crypto (Kraken)"
    suf = s.rsplit(".", 1)[1].upper() if "." in s else ""
    return {"AX": "ASX", "T": "Tokyo", "L": "London", "HK": "Hong Kong", "KS": "Korea", "TO": "Toronto",
            "DE": "Xetra", "PA": "Paris", "AS": "Amsterdam", "SW": "Swiss", "MI": "Milan", "SI": "Singapore",
            "NZ": "NZX", "ST": "Stockholm", "CO": "Copenhagen", "HE": "Helsinki", "OL": "Oslo", "MC": "Madrid"}.get(suf, "US" if not suf else suf)


def sleeve_of(book, sym="", p=None):
    b = str(book or "").lower()
    if "trend18" in b:
        return "Trend18"
    if "fundamental" in b and "spec" not in b:
        return "Shark (equity clips)"
    if "ht_crypto" in b or "crypto" in b:
        return "Crypto clips"
    if "convexity" in b:
        return "Convexity"
    if "gap" in b:
        return "Powder (GCL)"
    if "kraken" in b or "replica" in b or "cross" in b:
        return "Legacy floors"
    return str(book or "Other")


# ----------------------------------------------------------------------------- freshness
def _us_last_close(now):
    """Most recent US regular-session close (approx: weekdays; 20:00Z in EDT, 21:00Z in EST). Holidays not removed."""
    t = now
    for _ in range(8):
        d = t.date()
        if d.weekday() < 5:
            # EDT roughly 2nd Sun Mar -> 1st Sun Nov
            edt = (d.month > 3 and d.month < 11) or (d.month == 3 and d.day >= 8) or (d.month == 11 and d.day < 7)
            close = dt.datetime(d.year, d.month, d.day, 20 if edt else 21, 0, tzinfo=UTC)
            if close <= now:
                return close
        t = dt.datetime(d.year, d.month, d.day, tzinfo=UTC) - dt.timedelta(seconds=1)
    return now - dt.timedelta(days=3)


def _row(pid, panel, source, asof, max_age_s, now, cadence, note="", expected_after=None, dormant=False):
    t = _parse(asof)
    age = (now - t).total_seconds() if t else None
    if t is None:
        status = "UNKNOWN"
    elif age < -60:
        status = "CLOCK_ERROR"
    elif dormant:
        status = "DORMANT"
    elif expected_after is not None:
        status = "FRESH" if t >= expected_after - dt.timedelta(hours=1) else ("LAG" if age < max_age_s else "STALE")
    else:
        status = "FRESH" if age <= max_age_s else ("LAG" if age <= 3 * max_age_s else "STALE")
    return {"id": pid, "panel": panel, "source": source, "asof_utc": _iso(t), "age_s": None if age is None else round(age),
            "max_age_s": max_age_s, "cadence": cadence, "status": status, "note": note}


def freshness(now, ctx):
    rows = []
    add = rows.append
    fs = ctx.get("funder") or {}
    add(_row("funder", "Funder fee", "funder_fee.py -> /api/funder (state: reports/funder_fee*.json)",
             fs.get("equity_asof_utc"), 75 * 60, now, "fee 09:00 Sydney daily; equity mark hourly",
             "Fee rate %.4f%%/day" % (_f(fs.get("rate_pct_day"), 0.0) or 0.0)))
    fr = _load(os.path.join(R, "firm_risk_state.json"))
    add(_row("firm", "Firm equity / risk", "reports/firm_risk_state.json + firm_equity.csv (desk_tick.sh)", fr.get("utc"), 75 * 60, now,
             "hourly desk tick", "Level %s · DD %s%%" % (fr.get("level"), fr.get("firm_drawdown_pct"))))
    pos = ctx.get("pos") or {}
    gen = pos.get("generated")
    gen_t = None
    if gen:
        age_s = _f(pos.get("payload_age_s"), 0) or 0
        gen_t = now - dt.timedelta(seconds=age_s)
    mk = pos.get("marks") or {}
    add(_row("positions", "Positions & marks", "/api/positions (positions_payload: book ledgers + yfinance/Kraken marks)",
             _iso(gen_t), 120, now, "8 s while a viewer is open",
             "%s rows · %s live · %s closed-market · %s stale" % (mk.get("rows"), mk.get("live"), mk.get("closed"), mk.get("stale"))))
    t18 = ctx.get("trend18") or {}
    add(_row("trend18", "Trend18 sleeve", "trend18.py -> state/trend18*.json", t18.get("updated"), 26 * 3600, now,
             "stage/mark after the US close; monthly signal", "funded $%s" % format(_f(t18.get("funded_usd"), 0) or 0, ",.0f")))
    fw = ctx.get("deep18") or {}
    up = sorted([t.get("updated_utc") for t in fw.get("tests") or [] if t.get("updated_utc")])
    add(_row("deep18_fwd", "DEEP18 forward tests", "deep18_fwd.py -> state/deep18_fwd/", up[-1] if up else None, 26 * 3600, now,
             "launchd com.tfo.deep18-fwd 07:40-12:40 & 16:45/17:45 Sydney"))
    sh = ctx.get("shadow") or {}
    add(_row("shadow", "Shadow 2x Nasdaq", "shadow_nasdaq2x.py -> state/shadow_nasdaq2x.json", sh.get("last_mark_utc") or sh.get("updated_utc"),
             80 * 3600, now, "daily after the US close (06:30 Sydney)", "last mark %s" % sh.get("last_mark_date"),
             expected_after=_us_last_close(now)))
    er = _load(os.path.join(R, "ht_equity_rails_latest.json"))
    add(_row("shark", "Shark equity clips (rails)", "tfo_ticket.py rails -> reports/ht_equity_rails_latest.json", er.get("utc"), 15 * 60, now,
             "launchd com.tfo.ht-equity-rails every 300 s", "%d legs on rails" % len(er.get("rows") or [])))
    fb_t = _parse(_load(os.path.join(R, "fundamental_book.json")).get("updated"))
    add(_row("shark_book", "Shark book ledger", "reports/fundamental_book.json", _iso(fb_t), 75 * 60, now, "marked each rails cycle"))
    hb = _load(os.path.join(R, "ht_crypto_rails_heartbeat.json"))
    add(_row("crypto", "Crypto clips (rails)", "tfo_crypto.py rails -> reports/ht_crypto_rails_heartbeat.json", hb.get("at_utc"), 5 * 60, now,
             "launchd com.tfo.ht-crypto-rails every 60 s", "%s open clips" % hb.get("n_open_clips")))
    rw = _load(os.path.join(R, "rail_watchdog.json"))
    add(_row("rails", "Rail watchdog", "rail_watchdog.py -> reports/rail_watchdog.json", rw.get("updated_utc") or _iso(_mtime(os.path.join(R, "rail_watchdog.json"))),
             15 * 60, now, "each rails cycle"))
    cyc = _load(os.path.join(R, "private_wealth_cycle_latest.json"))
    add(_row("pw_cycle", "Private Wealth cycle receipt", "reports/private_wealth_cycle_latest.json (desk_tick)", cyc.get("utc"), 80 * 60, now, "hourly (70 min cadence)"))
    w = _load(os.path.join(R, "private_wealth_watch.json"))
    add(_row("watchdog", "Watchdog & live tape", "private_wealth_monitor.py tick -> reports/private_wealth_watch.json", w.get("utc"), 180, now, "every 20 s"))
    dp = _load(os.path.join(R, "daily_pnl_history.json"), [])
    add(_row("daily_pnl", "Daily P&L / target", "reports/daily_pnl_history.json", (dp[0] if isinstance(dp, list) and dp else {}).get("asof"), 80 * 60, now, "hourly"))
    hl = _load(os.path.join(R, "private_wealth_hunt_latest.json"))
    add(dict(_row("hunt", "Head Trader hunt (RETIRED)", "reports/private_wealth_hunt_latest.json", hl.get("asof"), 24 * 3600, now, "retired — no longer runs",
             "RETIRED"), status="RETIRED", stale=False))   # 2026-10-30 dashfix: panel retired
    ke = _tail_jsonl(os.path.join(R, "kill_events.jsonl"), 5)
    add(_row("kill", "Kill rule / refusals", "book_kill.py -> reports/kill_events.jsonl + book_peaks.json",
             _load(os.path.join(R, "book_peaks.json")).get("Kraken Floor", {}).get("checked_utc") or (ke[-1].get("utc") if ke else None),
             80 * 60, now, "each book tick"))
    for pid, panel, fn, book in (("eq_fund", "Shark equity curve", "fundamental_equity.csv", "fundamental_book"),
                                  ("eq_spec", "Spec book curve", "fundamental_spec_equity.csv", "fundamental_spec_book"),
                                  ("eq_growth", "Growth book curve", "growth_book_equity.csv", "growth_book"),
                                  ("eq_value", "Value book curve", "value_book_equity.csv", "value_book")):
        last = _last_csv_time(os.path.join(R, fn))
        book_eq = _f((fr.get("book_equity_usd") or {}).get(book))
        dormant = book != "fundamental_book" and (book_eq is None or book_eq < 5000)
        add(_row(pid, panel, "reports/" + fn, _iso(last), 75 * 60, now, "hourly when the book is active",
                 "book inactive / not in the firm registry" if dormant else "", dormant=dormant))
    add({"id": "ibkr", "panel": "IBKR paper mirror", "source": "static (gateway on the operator box; not contacted)", "asof_utc": None,
         "age_s": None, "max_age_s": None, "cadence": "n/a", "status": "PENDING", "note": "Mirror pending: first mirror at the Mon 5 Oct US close"})
    return rows


def _last_csv_time(path):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4000))
            lines = [x for x in f.read().decode("utf-8", "replace").splitlines() if x.strip()]
        return _parse(lines[-1].split(",")[0]) if lines else None
    except OSError:
        return None


# ----------------------------------------------------------------------------- series
def _read_series(path, tcol, vcol, flagcol=None, step_flag=False):
    pts = []
    try:
        with open(path) as f:
            for rec in csv.DictReader(f):
                t = _parse(rec.get(tcol))
                v = _f(rec.get(vcol))
                if t is None or v is None or v <= 0:
                    continue
                pts.append([t, v, (rec.get(flagcol) or "").upper() == "YES" if flagcol else False])
    except OSError:
        return [], 0
    pts.sort(key=lambda p: p[0])
    # display-only spike filter: a short run (<= 12 prints spanning <= 30 min) that sits >3% away from BOTH the print
    # before and the print after the run, while those two agree within 1.5% (internal-transfer double counts that the
    # desk later corrected). Longer-lived moves are never hidden.
    keep, hidden, i, n = [], 0, 0, len(pts)
    while i < n:
        removed = False
        if keep:
            a = keep[-1][1]
            for run in range(1, 13):
                j = i + run
                if j >= n or (pts[j - 1][0] - pts[i][0]).total_seconds() > 1800:
                    break
                b = pts[j][1]
                if abs(b / a - 1) < 0.015 and all(abs(pts[k][1] / a - 1) > 0.03 and abs(pts[k][1] / b - 1) > 0.03 for k in range(i, j)):
                    hidden += run
                    i = j
                    removed = True
                    break
        if not removed:
            keep.append(pts[i])
            i += 1
    # capital-event flag kept only where equity actually stepped (>1%) vs the previous kept print; the CSV flags every
    # internal transfer, which would bury the chart in markers
    # Books without a flow column (sleeve CSVs) get a step flag when equity moves >5% in one print: at these books'
    # sizes that is a transfer in/out of powder, not market P&L. The page excludes flagged steps from time-weighted returns.
    for k in range(len(keep)):
        step = k > 0 and abs(keep[k][1] / keep[k - 1][1] - 1)
        if flagcol:
            flag = bool(keep[k][2]) and k > 0 and step > 0.01
        else:
            flag = step_flag and k > 0 and step > 0.05
        keep[k] = [keep[k][0], keep[k][1], flag]
    return keep, hidden


def _thin(pts, now, recent_days=10, older_step_s=3600):
    out, last_t = [], None
    cut = now - dt.timedelta(days=recent_days)
    for p in pts:
        if p[0] >= cut or p[2] or last_t is None or (p[0] - last_t).total_seconds() >= older_step_s:
            out.append(p)
            last_t = p[0]
    if pts and out[-1] is not pts[-1]:
        out.append(pts[-1])
    return out


def series(now, ctx):
    out = {}
    firm, hid = _read_series(os.path.join(R, "firm_equity.csv"), "utc", "firm_equity_usd", "capital_event")
    out["firm"] = {"label": "Firm equity", "source": "reports/firm_equity.csv", "hidden_spikes": hid, "after_fee": "actual",
                   "points": [[_iso(t), round(v, 2), 1 if ev else 0] for t, v, ev in _thin(firm, now)]}
    fb, hid = _read_series(os.path.join(R, "fundamental_equity.csv"), "utc", "equity_usd")
    out["shark"] = {"label": "Shark book (fundamental_book)", "source": "reports/fundamental_equity.csv", "hidden_spikes": hid,
                    "after_fee": "proforma", "returns_ok": False,
                    "note": "This file is the book's cash (~$20-25k) plus the market value of its positions. Names are funded from "
                            "outside the book, so the line jumps whenever a position is added or removed: it tracks exposure, not profit. "
                            "Returns, drawdown and the benchmark are therefore not shown for it; see the shark card for P&L.",
                    "points": [[_iso(t), round(v, 2), 0] for t, v, ev in _thin(fb, now)]}
    sh, hid = _read_series(os.path.join(S, "shadow_nasdaq2x_history.csv"), "date", "shadow_value_usd")
    out["shadow"] = {"label": "Shadow 2x Nasdaq (not firm equity)", "source": "state/shadow_nasdaq2x_history.csv", "hidden_spikes": hid,
                     "after_fee": "proforma", "points": [[_iso(t + dt.timedelta(hours=20)), round(v, 2), 0] for t, v, _ in sh]}
    for t in (ctx.get("deep18") or {}).get("tests") or []:
        curve = t.get("equity_curve") or []
        pts = []
        for p in curve:
            ts = _parse(p.get("date") or p.get("utc"))
            v = _f(p.get("value_usd") if p.get("value_usd") is not None else p.get("shadow_value_usd"))
            af = _f(p.get("after_fee_usd"))
            cum = _f(p.get("cum_pnl_after_fee_usd"))
            if ts is None:
                continue
            if v is None and af is None and cum is not None:
                v = (_f(t.get("notional_usd"), 0) or 0) + cum
            if v is None and af is not None:
                v = af
            if v:
                pts.append([_iso(ts + dt.timedelta(hours=20)), round(v, 2), 0, round(af, 2) if af else None])
        if pts:
            out["fwd_" + t.get("id", "?")] = {"label": "DEEP18 " + str(t.get("name") or t.get("id")) + " (shadow)",
                                              "source": "state/deep18_fwd/ (equity_curve)", "hidden_spikes": 0,
                                              "after_fee": "series" if any(p[3] for p in pts) else "proforma", "points": pts}
    t18 = ctx.get("trend18") or {}
    hist = [h for h in (t18.get("rebalances") or []) if isinstance(h, dict)]
    pts = [[h.get("utc"), _f(h.get("equity_usd")), 0] for h in hist if h.get("utc") and _f(h.get("equity_usd"))]
    if pts:
        out["trend18"] = {"label": "Trend18 sleeve", "source": "state/trend18.json", "hidden_spikes": 0, "after_fee": "proforma", "points": pts}
    return out


# ----------------------------------------------------------------------------- positions / rails / fills
def positions(ctx):
    pos = ctx.get("pos") or {}
    rails = {(r.get("book"), r.get("sym")): r for r in (_load(os.path.join(R, "ht_equity_rails_latest.json")).get("rows") or [])}
    fb = _load(os.path.join(R, "fundamental_book.json"))
    fpos = fb.get("positions") or {}
    rows = []
    for r in pos.get("rows") or []:
        sym = r.get("symbol")
        floor = str(r.get("floor") or "")
        book_key = "fundamental_book" if "Fundamental" in floor and "Spec" not in floor else (r.get("book") or floor)
        rl = rails.get((book_key, sym)) or {}
        fp = fpos.get(sym) if book_key == "fundamental_book" else None
        fp = fp or {}
        et = fp.get("entry_ticket") or {}
        entry0 = _f(rl.get("entry0_usd")) or _f(fp.get("rail_orig_entry_usd")) or _f(r.get("entry"))
        variant = fp.get("exit_variant") or rl.get("exit_variant") or ("V0" if book_key == "fundamental_book" else None)
        half_mult = 1.10 if variant == "V7" else 1.02
        rail = None
        if entry0:
            rail = {"variant": variant or "-", "stop_usd": round(entry0 * 0.95, 6),
                    "half_usd": round(entry0 * half_mult, 6), "half_banked": bool(rl.get("half_banked")),
                    "level_usd": _f(rl.get("rail_level_usd")), "trail_pct": _f(rl.get("trail_pct")),
                    "trail_wm_usd": _f(rl.get("trail_wm_usd")), "atr14_pct": _f(rl.get("atr14_pct")),
                    "max_hold_due_utc": rl.get("max_hold_due_utc"), "held_days": _f(rl.get("held_days")),
                    "status": rl.get("status"), "why": rl.get("why"), "checked_utc": rl.get("utc") or None,
                    "rule": ("V7: stop -5% from fill; half at +10%; residual trails max(3%, 3xATR14) off the high, floored at breakeven; 40-session max hold"
                             if variant == "V7" else
                             "V0: stop -5% from fill until the +2% half; residual trails max(3%, 3xATR14) off the high, floored at breakeven; 30-day max hold after the half")}
        value = _f(r.get("value"), 0) or 0
        rows.append({
            "book": book_key, "floor": floor, "sleeve": sleeve_of(book_key, sym), "venue": venue_of(sym, book_key),
            "symbol": sym, "side": r.get("side"), "units": _f(r.get("units")), "entry_usd": _f(r.get("entry")), "mark_usd": _f(r.get("mark_usd") or r.get("mark")),
            "value_usd": round(value, 2), "upnl_usd": _f(r.get("upnl")), "upnl_pct": _f(r.get("upnl_pct")), "quote_ccy": r.get("quote_ccy"),
            "mark_utc": r.get("mark_utc"), "mark_age_s": _f(r.get("mark_age_sec")), "mark_src": r.get("src"),
            "entry_utc": fp.get("entry_utc") or rl.get("entry_time_utc"), "theme": fp.get("ht_theme") or fp.get("sector"),
            "label": fp.get("ht_label"), "why": fp.get("ht_why"), "entry_fees_usd": _f(fp.get("entry_fees_usd") or et.get("fees_usd")),
            "ticket_id": et.get("ticket_id"), "rail": rail,
        })
    t18 = ctx.get("trend18") or {}
    for h in t18.get("holdings") or []:
        if _f(h.get("qty")):
            rows.append({"book": "trend18", "floor": "Trend18 sleeve", "sleeve": "Trend18", "venue": venue_of(h.get("inst"), "trend18"),
                         "symbol": h.get("inst"), "side": "long", "units": _f(h.get("qty")), "entry_usd": _f(h.get("avg_cost")),
                         "mark_usd": _f(h.get("px")), "value_usd": _f(h.get("value_usd")), "upnl_usd": _f(h.get("upl_usd")),
                         "upnl_pct": None, "mark_utc": h.get("mark_utc"), "rail": {"variant": "trend", "rule": "Monthly trend rebalance; -30% NAV kill -> 10% vol"}})
    tot = sum(x.get("value_usd") or 0 for x in rows)
    for x in rows:
        x["weight_pct"] = round(100 * (x.get("value_usd") or 0) / tot, 2) if tot else None
    return rows


def _fill(tk, book, sym_key="sym"):
    principal = _f(tk.get("principal_usd"))
    fees = _f(tk.get("fees_usd"))
    proceeds = _f(tk.get("proceeds_usd"))
    sym = tk.get(sym_key) or tk.get("pair") or tk.get("sym")
    gross = principal if str(tk.get("action")).upper() == "BUY" else (proceeds or principal)
    return {"utc": tk.get("utc"), "book": book, "sleeve": sleeve_of(book, sym), "venue": venue_of(sym, book), "symbol": sym,
            "action": tk.get("action"), "side": tk.get("side") or "long", "qty": _f(tk.get("qty")), "px_usd": _f(tk.get("px_usd")),
            "principal_usd": principal, "proceeds_usd": proceeds, "net_proceeds_usd": _f(tk.get("net_proceeds_usd")),
            "pnl_usd": _f(tk.get("pnl_usd")), "ret_orig_pct": _f(tk.get("ret_orig_pct")), "fees_usd": fees,
            "cost_bp": round(1e4 * fees / gross, 1) if fees is not None and gross else None,
            "fee_detail": tk.get("fee_detail"), "fee_schedule": tk.get("fee_schedule"), "exit_rule": tk.get("exit_rule"),
            "rail_level_usd": _f(tk.get("rail_level_usd")), "tape": tk.get("tape") or tk.get("price_class"),
            "exit_variant": tk.get("exit_variant"), "ticket_id": tk.get("ticket_id"), "why": tk.get("why")}


def fills(limit=400):
    out = []
    fb = _load(os.path.join(R, "fundamental_book.json"))
    for grp in ("positions", "closed"):
        for sym, p in (fb.get(grp) or {}).items():
            if not isinstance(p, dict):
                continue
            if isinstance(p.get("entry_ticket"), dict):
                out.append(_fill(p["entry_ticket"], "fundamental_book"))
            for x in p.get("exit_fills") or []:
                if isinstance(x, dict):
                    out.append(_fill(x, "fundamental_book"))
    cr = _load(os.path.join(R, "tfo_ht_crypto.json"))
    for tk in cr.get("head_trader_tickets") or []:
        if isinstance(tk, dict):
            out.append(_fill(tk, "tfo_ht_crypto", "pair"))
    seen, uniq = set(), []
    for x in out:
        k = (x.get("ticket_id"), x.get("action"), x.get("utc"))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(x)
    uniq.sort(key=lambda x: str(x.get("utc") or ""), reverse=True)
    return uniq[:limit]


# ----------------------------------------------------------------------------- alerts / kill
def kill_status(ctx):
    fr = _load(os.path.join(R, "firm_risk_state.json"))
    peaks = _load(os.path.join(R, "book_peaks.json"))
    books = []
    for name, b in peaks.items():
        if isinstance(b, dict):
            books.append({"book": name, "frozen": bool(b.get("frozen")), "drawdown_pct": _f(b.get("drawdown_pct")),
                          "frozen_reason": b.get("frozen_reason"), "checked_utc": b.get("checked_utc")})
    t18 = (ctx.get("trend18") or {}).get("kill") or {}
    fwd = [{"id": t.get("id"), "name": t.get("name"), "active": bool((t.get("kill") or {}).get("active")), "reason": (t.get("kill") or {}).get("reason")}
           for t in (ctx.get("deep18") or {}).get("tests") or []]
    return {"firm": {"level": fr.get("level"), "new_entries_halted": bool(fr.get("new_entries_halted")),
                     "force_liquidate": bool(fr.get("force_liquidate")), "drawdown_pct": _f(fr.get("firm_drawdown_pct")),
                     "warn_dd_pct": _f(fr.get("warn_dd_pct")), "halt_dd_pct": _f(fr.get("halt_dd_pct")), "asof_utc": fr.get("utc")},
            "books": books, "book_kill_dd_pct": -18.0, "trend18": {"active": bool(t18.get("active")), "detail": t18},
            "deep18_fwd": fwd}


def alerts(now, ctx, fresh_rows, kill):
    out = []

    def add(sev, src, text, utc=None):
        out.append({"severity": sev, "source": src, "text": text, "utc": utc})
    t18 = ctx.get("trend18") or {}
    marks_now = t18.get("mark_utc") or {}
    if not marks_now:
        try:
            marks_now = json.load(open(os.path.join(BASE, "state", "trend18.json"))).get("mark_utc") or {}
        except Exception:
            marks_now = {}
    for a in (t18.get("mark_alerts") or [])[-20:]:
        # 2026-10-28 (Brett t1674u): a MARK_MISSING alert is resolved once every named symbol has a real mark
        # newer than the alert; showing it afterwards said 'previous marks kept' about prices that are live.
        if a.get("kind") == "MARK_MISSING":
            try:
                import ast
                syms = ast.literal_eval(str(a.get("text", "")).split("no price for ", 1)[1].split(" this mark", 1)[0])
                if syms and all(str(marks_now.get(x) or "") > str(a.get("utc") or "~") for x in syms):
                    continue
            except Exception:
                pass
        add("warn", "trend18_alerts.jsonl", "%s: %s" % (a.get("kind"), a.get("text")), a.get("utc"))
    for inst, m in (t18.get("stale_marks") or {}).items():
        add("bad", "Trend18 mark refused", "%s mark refused by the sanity check: %s (sleeve equity may be overstated)" % (inst, (m or {}).get("why")), (m or {}).get("utc"))
    if (t18.get("kill") or {}).get("active"):
        add("bad", "Trend18 kill", "Trend18 kill rule is ACTIVE: sleeve cut to 10% vol until a new 6-month high")
    if t18.get("review_flag"):
        add("warn", "Trend18 review", str((t18.get("review_flag") or {}).get("reason")), (t18.get("review_flag") or {}).get("utc"))
    for t in kill.get("deep18_fwd") or []:
        if t["active"]:
            add("warn", "DEEP18 forward test", "%s killed: %s" % (t["name"], t["reason"]))
    f = kill.get("firm") or {}
    if f.get("level") and f.get("level") != "OK":
        add("bad", "Firm risk", "Firm risk level %s (drawdown %s%%)" % (f.get("level"), f.get("drawdown_pct")), f.get("asof_utc"))
    if f.get("new_entries_halted"):
        add("bad", "Firm risk", "New entries halted by the firm drawdown rule", f.get("asof_utc"))
    for b in kill.get("books") or []:
        if b["frozen"]:
            add("bad", "Book kill", "%s FROZEN: %s" % (b["book"], b.get("frozen_reason")), b.get("checked_utc"))
    rw = _load(os.path.join(R, "rail_watchdog.json"))
    for a in rw.get("alerts") or []:
        add("bad", "Rail watchdog", "%s %s exit blocked %sx: %s" % (a.get("symbol"), a.get("rule"), a.get("count"), a.get("reason")), a.get("first_blocked_utc"))
    for s in rw.get("stale_runners") or []:
        add("warn", "Rail watchdog", "Rails runner %s is late" % s.get("runner"))
    day_ago = now - dt.timedelta(hours=24)
    reasons = {}
    for e in _tail_jsonl(os.path.join(R, "kill_events.jsonl"), 600):
        t = _parse(e.get("utc"))
        if not t or t < day_ago:
            continue
        if e.get("event") == "ENTRY_REFUSED" and str(e.get("reason")).strip().lower() in ("ok", ""):
            continue
        k = (e.get("event"), e.get("book"), str(e.get("reason") or e.get("note") or "")[:160])
        reasons.setdefault(k, [0, e.get("utc")])
        reasons[k][0] += 1
        reasons[k][1] = e.get("utc")
    for (ev, book, why), (n, last) in sorted(reasons.items(), key=lambda kv: str(kv[1][1]), reverse=True)[:6]:
        add("info" if ev == "ENTRY_REFUSED" else "warn", "kill_events.jsonl", "%s %s x%d (24h): %s" % (book, ev.replace("_", " ").lower(), n, why), last)
    fb = _load(os.path.join(R, "fundamental_book.json"))
    for sym, why in (fb.get("price_failures") or {}).items():
        add("warn", "Mark refusal", "%s price refused/failed: %s" % (sym, str(why)[:160]))
    mk = (ctx.get("pos") or {}).get("marks") or {}
    if mk.get("warning"):
        add("warn", "Marks", str(mk.get("warning")))
    fs = ctx.get("funder") or {}
    if (fs.get("today") or {}).get("next_due_now"):
        add("warn", "Funder fee", "Today's fee is due but waiting for a fresh equity mark")
    for r in fresh_rows:
        if r["status"] in ("STALE", "UNKNOWN", "CLOCK_ERROR"):
            add("warn", "Freshness", "%s data is %s (source %s)" % (r["panel"], r["status"].lower().replace("_", " "), r["source"]), r.get("asof_utc"))
    order = {"bad": 0, "warn": 1, "info": 2}
    out.sort(key=lambda a: (order.get(a["severity"], 3), "" if a.get("utc") is None else "~" + str(a.get("utc"))), reverse=False)
    return out


# ----------------------------------------------------------------------------- sleeves summary
def sleeves(ctx, pos_rows, kill):
    fb = _load(os.path.join(R, "fundamental_book.json"))
    cr = _load(os.path.join(R, "tfo_ht_crypto.json"))
    t18 = ctx.get("trend18") or {}
    sh = ctx.get("shadow") or {}
    sab = _load(os.path.join(R, "shark_exit_ab_summary.json"))
    cab = _load(os.path.join(R, "crypto_exit_ab_summary.json"))
    score = _tail_jsonl(os.path.join(R, "shark_scorecard.jsonl"), 40)
    cnt = {}
    for r in pos_rows:
        cnt[r["sleeve"]] = cnt.get(r["sleeve"], 0) + 1
    return [
        {"id": "trend18", "name": "Trend18 (DEEP18 R2, levered)", "in_firm_equity": True, "equity_usd": _f(t18.get("equity_usd")),
         "pnl_usd": _f(t18.get("pnl_usd")), "costs_usd": _f(t18.get("costs_paid_usd")), "status": "UNFUNDED (staged)" if not _f(t18.get("funded_usd")) else "FUNDED",
         "positions": cnt.get("Trend18", 0), "kill": "ACTIVE" if (t18.get("kill") or {}).get("active") else "armed (-30% NAV)", "updated_utc": t18.get("updated"),
         "next": t18.get("next")},
        {"id": "shark", "name": "Shark equity clips (fundamental_book)", "in_firm_equity": True, "equity_usd": _f(fb.get("equity")),
         "pnl_usd": (_f(fb.get("realised_usd"), 0) or 0) + (_f(fb.get("unrealised_usd"), 0) or 0), "costs_usd": _f(fb.get("costs_paid_usd")),
         "status": "%d long / %d short" % (fb.get("n_long") or 0, fb.get("n_short") or 0), "positions": cnt.get("Shark (equity clips)", 0),
         "kill": "frozen" if (fb.get("book_risk") or {}).get("frozen") else "armed (-18% book DD)", "updated_utc": fb.get("updated"),
         "exit_test": {"rule_doc": sab.get("rule_doc"), "trades": len(sab.get("trades") or []), "updated_utc": sab.get("updated_utc")},
         "grades": [{"utc": s.get("ts"), "symbol": s.get("symbol"), "grade": s.get("grade"), "action": s.get("action"), "session": s.get("session"),
                     "reasons": "; ".join(s.get("reasons") or [])[:240]} for s in reversed(score[-12:])]},
        {"id": "crypto", "name": "Crypto clips (tfo_ht_crypto) — RETIRED/ARCHIVE", "in_firm_equity": False, "equity_usd": _f(cr.get("equity")),
         "pnl_usd": _f(cr.get("realised_usd")), "costs_usd": _f(cr.get("fees_paid_usd")), "status": "RETIRED/ARCHIVE (%d open clips)" % len(cr.get("positions") or {}),
         "positions": cnt.get("Crypto clips", 0), "kill": "retired", "updated_utc": _iso(_parse(cr.get("updated"))), "rails": cr.get("rails"),
         "exit_test": {"rule_doc": cab.get("rule_doc"), "trades": len(cab.get("trades") or []), "updated_utc": cab.get("updated_utc")}},
        {"id": "shadow", "name": "Shadow 2x Nasdaq combo", "in_firm_equity": False, "equity_usd": _f(sh.get("shadow_value_usd")),
         "pnl_usd": _f(sh.get("pnl_since_start_usd")), "costs_usd": _f(sh.get("costs_paid_usd")), "status": sh.get("phase"),
         "positions": None, "kill": sh.get("brake_state"), "updated_utc": sh.get("updated_utc")},
        {"id": "deep18_fwd", "name": "DEEP18 forward tests (FT1-FT5)", "in_firm_equity": False,
         "equity_usd": None, "pnl_usd": sum(_f(t.get("pnl_net_costs_and_fee_usd"), 0) or 0 for t in (ctx.get("deep18") or {}).get("tests") or []),
         "costs_usd": sum(_f(t.get("costs_usd"), 0) or 0 for t in (ctx.get("deep18") or {}).get("tests") or []), "status": "PAPER SHADOW",
         "positions": None, "kill": "%d killed" % sum(1 for t in kill.get("deep18_fwd") or [] if t["active"]), "updated_utc": None},
    ]


# ----------------------------------------------------------------------------- build
def _safe(fn, *a, default=None):
    try:
        return fn(*a)
    except Exception as exc:  # degrade the section, never the endpoint
        return default if default is not None else {"error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}


def _ctx(pos_payload):
    ctx = {"pos": pos_payload or {}}
    for key, mod, fn in (("funder", "funder_fee", "summary"), ("trend18", "trend18", "summary"),
                         ("shadow", "shadow_nasdaq2x", "summary")):
        try:
            ctx[key] = getattr(__import__(mod), fn)()
        except Exception as exc:
            ctx[key] = {"status": "UNAVAILABLE", "error": str(exc)[:160]}
    try:
        ctx["deep18"] = __import__("deep18_fwd").summary_all(400)
    except Exception as exc:
        ctx["deep18"] = {"status": "UNAVAILABLE", "error": str(exc)[:160]}
    return ctx


def _syd_to_utc(s):
    """'2026-10-03 09:00 AEST' (Sydney wall clock) -> UTC ISO."""
    try:
        from zoneinfo import ZoneInfo
        t = dt.datetime.strptime(str(s)[:16], "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("Australia/Sydney"))
        return _iso(t)
    except Exception:
        return None


def fee_block(ctx):
    f = ctx.get("funder") or {}
    rate = (_f(f.get("rate_pct_day"), 0.0274) or 0.0274) / 100.0
    after = (1 + BENCH_PRE_FEE_CAGR_PCT / 100.0) * (1 - rate) ** 365 - 1
    return {"fee": {"rate_pct_day": f.get("rate_pct_day"), "rate_history": f.get("rate_history"), "inception_utc": f.get("inception_utc"),
                    "cum_fee_paid_usd": f.get("cum_fee_paid_usd"),
                    "history": [{"boundary_utc": _iso(_parse(h.get("boundary_utc"))) if h.get("boundary_utc") else _syd_to_utc(h.get("boundary_sydney")),
                                 "boundary_sydney": h.get("boundary_sydney"), "fee_usd": h.get("fee_usd"), "rate": h.get("rate"),
                                 "stub_fraction": h.get("stub_fraction")} for h in (f.get("history") or [])],
                    "annual_drag_simple_pct": (f.get("annual_fee_drag") or {}).get("simple_pct"),
                    "break_even_pre_fee_cagr_pct": f.get("break_even_pre_fee_cagr_pct")},
            "benchmark": {"pre_fee_cagr_pct": BENCH_PRE_FEE_CAGR_PCT, "after_fee_cagr_pct": round(100 * after, 2),
                          "rule": "Benchmark: 19%% a year before the funder fee (Brett). At %.4f%%/day the fee takes ~%.1f%%/yr, so 19%% pre-fee is ~%.1f%%/yr after the fee."
                                  % (rate * 100, 100 * (1 - (1 - rate) ** 365), 100 * after),
                          "line": "Starts at the equity at the start of the selected range and compounds at 19%/yr; at each capital step it receives the same dollars that moved in or out."}}


def build(pos_payload=None):
    now = _now()
    ctx = _ctx(pos_payload)
    fresh = _safe(freshness, now, ctx, default=[])
    kill = _safe(kill_status, ctx, default={})
    pos_rows = _safe(positions, ctx, default=[])
    out = {"generated_utc": _iso(now), "paper_only": True, "read_only": True, "freshness": fresh,
           "series": _safe(series, now, ctx, default={}), "positions": pos_rows, "fills": _safe(fills, default=[]),
           "kill": kill, "alerts": _safe(alerts, now, ctx, fresh, kill, default=[]),
           "sleeves": _safe(sleeves, ctx, pos_rows, kill, default=[]), "ibkr": dict(IBKR_MIRROR)}
    out.update(_safe(fee_block, ctx, default={}))
    return out


def cached(pos_payload_fn=None):
    """20 s single-flight cache; while one build runs, others get the last good copy."""
    ts, data = _CACHE["ts"], _CACHE["data"]
    if data is not None and time.time() - ts < TTL_S:
        return data
    if not _LOCK.acquire(blocking=data is None):
        return data
    try:
        if _CACHE["data"] is not None and time.time() - _CACHE["ts"] < TTL_S:
            return _CACHE["data"]
        pos = None
        if pos_payload_fn:
            try:
                pos = pos_payload_fn()
            except Exception:
                pos = None
        data = build(pos)
        _CACHE.update(ts=time.time(), data=data)
        return data
    finally:
        _LOCK.release()


if __name__ == "__main__":
    import sys
    d = build(_load(sys.argv[1]) if len(sys.argv) > 1 else None)
    print(json.dumps({k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in d.items()}, indent=1))
