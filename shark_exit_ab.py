#!/usr/bin/env python3
"""Shark exit A/B shadow tracker (Brett-approved PAPER forward test, 2 Oct 2026; see SHARK_EXIT_RULE.md).

Every NEW shark clip is tagged exit_variant="V7" by tfo_buy.py and is exited on the V7 rail by tfo_ticket.py.
This module runs inside each tfo_ticket.py --execute cycle (after the rail decisions) and, per V7 trade, simulates what the
CURRENT default rail (V0: -5% stop, +2% half, trail max(3%,3xATR14) BE floor, 30-calendar-day residual max hold; exactly
tfo_trail_rail.decide with no variant) would have done on the SAME executable prints. Shadow results are gross (no fees);
the actual V7 result is reported gross and net of fees.

Writes ONLY its own files (never a book, never a ticket):
  reports/shark_exit_ab_state.json   per-trade shadow state
  reports/shark_exit_ab.jsonl        events: open, shadow_half, shadow_exit, actual_exit, trade_complete
  reports/shark_exit_ab_summary.json latest per-trade snapshot (V7 actual vs V0 shadow)
  reports/shark_scorecard.jsonl      one 'exit_ab_complete' line per finished trade (both legs closed)
Never raises into the rail runner (tfo_ticket wraps the call). PAPER ONLY."""
import datetime as dt, json, os, tempfile
from zoneinfo import ZoneInfo
import tfo_trail_rail as trl

BASE = os.path.dirname(os.path.abspath(__file__)); R = os.path.join(BASE, "reports")
SYD, UTC = ZoneInfo("Australia/Sydney"), dt.timezone.utc
BOOK = "fundamental_book"
GUARD_SEC = 300  # mirrors the rail's 5-minute round-trip guard between tickets


def _p(reports, name): return os.path.join(reports, name)


def _save_json(path, obj):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix="." + os.path.basename(path), suffix=".tmp")
    with os.fdopen(fd, "w") as f: json.dump(obj, f, indent=1, default=str)
    os.replace(tmp, path)


def _append(path, rec):
    with open(path, "a") as f: f.write(json.dumps(rec, default=str) + "\n")


def _iso(t): return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _venue(sym): return "US" if "." not in sym else sym.rsplit(".", 1)[1]


def _default_quote(sym, side, e0, last, now):
    """Executable regular-session print for a symbol the rail no longer quotes (actual leg closed). Same sanity as the rail."""
    import fundamental_book as fb
    from paper_execution_price import eligible
    from bad_tick_guard import bad_tick
    from tfo_ticket import time_block
    qs, _ = fb.fetch_prices([sym], require_executable=False); q = qs.get(sym)
    if not q: return None
    px = float(q["px_usd"])
    if q.get("session") != "regular" or not eligible(q): return None
    if _venue(sym) == "US" and not (q.get("quote_current") and (q.get("age_sec") or 999) < 60): return None
    if px <= 0 or (last and (px > 2.5 * last or px < last / 2.5)) or bad_tick(px, side, last, e0): return None
    if time_block(sym, now): return None
    return px


def _actual_result(p, notional0):
    fills = p.get("exit_fills") or []
    pnl = sum(float(f.get("pnl_usd") or 0) for f in fills)
    fees = float(p.get("entry_fees_usd") or 0) + sum(float(f.get("fees_usd") or 0) for f in fills)
    last = fills[-1] if fills else {}
    return {"gross_ret": pnl / notional0 if notional0 else None, "net_ret": (pnl - fees) / notional0 if notional0 else None,
            "pnl_usd": round(pnl, 2), "fees_usd": round(fees, 2), "exit_rules": [f.get("exit_rule") or f.get("action") for f in fills],
            "exit_utc": last.get("utc"), "exit_px_usd": last.get("px_usd")}


def update(rows, now=None, reports=R, book=None, quote_fn=None, atr_fn=None):
    now = now or dt.datetime.now(UTC)
    sp = _p(reports, "shark_exit_ab_state.json"); lp = _p(reports, "shark_exit_ab.jsonl")
    st = json.load(open(sp)) if os.path.exists(sp) else {"created_utc": _iso(now), "trades": {}}
    d = book if book is not None else json.load(open(_p(reports, BOOK + ".json")))
    pos = d.get("positions") or {}
    closed = d.get("closed") or {}
    rowmap = {r.get("sym"): r for r in rows if r.get("book") == BOOK}
    quote_fn = quote_fn or _default_quote
    atr_fn = atr_fn or trl.equity_atr_pct
    T = st["trades"]
    # 1) register new V7-tagged clips
    for sym, p in pos.items():
        if p.get("exit_variant") != trl.V7: continue
        key = "%s|%s" % (sym, p.get("entry_utc"))
        if key in T: continue
        e0 = float(p.get("rail_orig_entry_usd") or p["entry_px_usd"])
        T[key] = {"sym": sym, "entry_utc": p.get("entry_utc"), "side": p.get("side", "long"), "e0": e0,
                  "notional0": float((p.get("entry_ticket") or {}).get("principal_usd") or p.get("notional_usd") or 0),
                  "shadow": {"variant": "V0", "half_banked": False, "wm": None, "atr14_pct": None, "atr_date": None, "frac": 1.0,
                             "realized": 0.0, "closed": False, "last_px": e0, "last_act_utc": p.get("entry_utc"), "events": []},
                  "actual": {"variant": "V7", "closed": False}, "complete": False}
        _append(lp, {"utc": _iso(now), "event": "open", "key": key, "sym": sym, "e0_usd": e0, "notional_usd": T[key]["notional0"], "paper_only": True})
    # 2) step each open shadow on this cycle's executable print
    for key, t in T.items():
        sh = t["shadow"]
        if t.get("complete") or sh["closed"]: continue
        sym, side, e0 = t["sym"], t["side"], t["e0"]
        r = rowmap.get(sym)
        live = r is not None and str(pos.get(sym, {}).get("entry_utc")) == str(t["entry_utc"])
        if live:
            px = r.get("px_usd") if (r.get("_trail_ok") and not str(r.get("why") or "").startswith(("London", "ASX opening", "Tokyo close"))) else None
        else:
            try: px = quote_fn(sym, side, e0, sh.get("last_px"), now)
            except Exception: px = None
        if px is None: continue
        px = float(px)
        lt = sh.get("last_act_utc")
        if lt and (now - trl._parse(lt)).total_seconds() < GUARD_SEC: sh["last_px"] = px; continue
        if sh["half_banked"]:
            today = now.astimezone(SYD).date().isoformat()
            if sh.get("atr_date") != today:
                try:
                    a = atr_fn(sym)
                    if a: sh["atr14_pct"], sh["atr_date"] = round(float(a), 6), today
                except Exception: pass
        dec = trl.decide(e0, px, side, sh["half_banked"], sh.get("wm"), sh.get("atr14_pct"), trl.held(t["entry_utc"], now, False))
        sg = -1.0 if side == "short" else 1.0; r0 = sg * (px / e0 - 1.0)
        if dec["act"] == "HALF":
            sh["realized"] += 0.5 * r0; sh["frac"] = 0.5; sh["half_banked"] = True; sh["wm"] = px; sh["last_act_utc"] = _iso(now)
            ev = {"utc": _iso(now), "event": "shadow_half", "key": key, "sym": sym, "px_usd": px, "ret_orig_pct": round(100 * r0, 3), "rule": dec["rule"]}
            sh["events"].append(ev); _append(lp, ev)
        elif dec["act"] in ("STOP", "FLAT"):
            sh["realized"] += sh["frac"] * r0; sh["frac"] = 0.0; sh["closed"] = True; sh["last_act_utc"] = _iso(now)
            sh["exit_rule"], sh["exit_utc"], sh["exit_px_usd"] = dec["rule"], _iso(now), px
            ev = {"utc": _iso(now), "event": "shadow_exit", "key": key, "sym": sym, "px_usd": px, "ret_orig_pct": round(100 * r0, 3),
                  "rule": dec["rule"], "shadow_total_ret_pct": round(100 * sh["realized"], 3)}
            sh["events"].append(ev); _append(lp, ev)
        elif sh["half_banked"] and dec.get("wm") is not None:
            sh["wm"] = dec["wm"]
        sh["last_px"] = px
        sh["mtm_ret"] = sh["realized"] + sh["frac"] * r0
    # 3) detect the actual V7 leg closing (position gone from the book, found in closed with the same entry time)
    for key, t in T.items():
        a = t["actual"]
        if a["closed"]: continue
        p = pos.get(t["sym"])
        if p is not None and str(p.get("entry_utc")) == str(t["entry_utc"]):
            r = rowmap.get(t["sym"])
            if r and r.get("px_usd") is not None:
                sg = -1.0 if t["side"] == "short" else 1.0
                fills = p.get("exit_fills") or []
                realized = sum(float(f.get("pnl_usd") or 0) for f in fills)
                rem = float(p.get("units") or 0) * sg * (float(r["px_usd"]) - float(p["entry_px_usd"]))
                a["mtm_ret"] = (realized + rem) / t["notional0"] if t["notional0"] else None
                a["half_banked"] = bool(p.get("rail_half_count"))
            continue
        cp = None
        for ck, c in closed.items():
            if str(ck).split("#")[0] == t["sym"] and str(c.get("entry_utc")) == str(t["entry_utc"]): cp = c
        if cp is None: continue
        a.update(_actual_result(cp, t["notional0"])); a["closed"] = True
        _append(lp, {"utc": _iso(now), "event": "actual_exit", "key": key, "sym": t["sym"], "gross_ret_pct": a["gross_ret"] is not None and round(100 * a["gross_ret"], 3),
                     "net_ret_pct": a["net_ret"] is not None and round(100 * a["net_ret"], 3), "exit_rules": a["exit_rules"]})
    # 4) complete when both legs are closed
    for key, t in T.items():
        a, sh = t["actual"], t["shadow"]
        if t.get("complete") or not (a["closed"] and sh["closed"]): continue
        t["complete"] = True
        v7, v0 = a.get("gross_ret"), sh["realized"]
        rec = {"utc": _iso(now), "event": "trade_complete", "key": key, "sym": t["sym"], "entry_utc": t["entry_utc"], "notional_usd": t["notional0"],
               "v7_actual_gross_ret_pct": v7 is not None and round(100 * v7, 3), "v7_actual_net_ret_pct": a.get("net_ret") is not None and round(100 * a["net_ret"], 3),
               "v0_shadow_gross_ret_pct": round(100 * v0, 3), "delta_v7_minus_v0_gross_pct": v7 is not None and round(100 * (v7 - v0), 3),
               "v7_exit_rules": a.get("exit_rules"), "v0_shadow_exit_rule": sh.get("exit_rule"), "v0_shadow_exit_utc": sh.get("exit_utc"), "v7_exit_utc": a.get("exit_utc"), "paper_only": True}
        _append(lp, rec)
        _append(_p(reports, "shark_scorecard.jsonl"), {"ts": now.astimezone(SYD).isoformat(timespec="seconds"), "source": "shark_exit_ab", "action": "exit_ab_complete",
                "sydney_date": now.astimezone(SYD).date().isoformat(), "symbol": t["sym"], **{k: v for k, v in rec.items() if k not in ("event", "utc", "sym", "key")}})
    st["updated_utc"] = _iso(now)
    _save_json(sp, st)
    summ = {"updated_utc": _iso(now), "paper_only": True, "rule_doc": "SHARK_EXIT_RULE.md", "trades": [
        {"key": k, "sym": t["sym"], "entry_utc": t["entry_utc"], "complete": t.get("complete", False),
         "v7_actual": {x: t["actual"].get(x) for x in ("closed", "mtm_ret", "gross_ret", "net_ret", "exit_rules", "half_banked")},
         "v0_shadow": {x: t["shadow"].get(x) for x in ("closed", "mtm_ret", "realized", "half_banked", "exit_rule", "wm", "frac")}} for k, t in T.items()]}
    done = [t for t in T.values() if t.get("complete") and t["actual"].get("gross_ret") is not None]
    if done:
        summ["completed_n"] = len(done)
        summ["avg_v7_gross_ret_pct"] = round(100 * sum(t["actual"]["gross_ret"] for t in done) / len(done), 3)
        summ["avg_v0_shadow_gross_ret_pct"] = round(100 * sum(t["shadow"]["realized"] for t in done) / len(done), 3)
    _save_json(_p(reports, "shark_exit_ab_summary.json"), summ)
    print("SHARK-EXIT-AB ok trades=%d open_shadow=%d complete=%d" % (len(T), sum(1 for t in T.values() if not t["shadow"]["closed"]), sum(1 for t in T.values() if t.get("complete"))))
    return st
