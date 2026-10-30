#!/usr/bin/env python3
"""Crypto exit A/B shadow tracker (PAPER forward test, 2 Oct 2026; Brett: "your call, maximise desk profits"; see SHARK_EXIT_RULE.md).

Every NEW HT crypto clip is tagged exit_variant="C3" by tfo_crypto.py buy and is exited on the C3 rail (2xATR14 entry stop,
+15% half, trail max(3%,3xATR14) BE floor, 40-calendar-day max hold). This module runs at the end of each
`tfo_crypto.py rails --execute` cycle on the live desk and, per C3 trade, simulates what the CURRENT default crypto rail
(V0 = tfo_trail_rail.decide(..., crypto=True) with no variant: -5% stop, +2% half, same trail, 30-day residual max hold)
would have done on the SAME live bids. Shadow results are gross (no fees); the actual C3 result is gross and net of fees.
R-multiples: C3 return / its own stop distance; V0 return / 0.05.

Writes ONLY its own files (never a book, never a ticket):
  reports/crypto_exit_ab_state.json, reports/crypto_exit_ab.jsonl, reports/crypto_exit_ab_summary.json,
  reports/shark_scorecard.jsonl (one 'exit_ab_complete' line per finished trade, source=crypto_exit_ab).
Never raises into the rail runner (tfo_crypto wraps the call). PAPER ONLY."""
import datetime as dt, json, os, tempfile
from zoneinfo import ZoneInfo
import tfo_trail_rail as trl

BASE = os.path.dirname(os.path.abspath(__file__)); R = os.path.join(BASE, "reports")
SYD, UTC = ZoneInfo("Australia/Sydney"), dt.timezone.utc
GUARD_SEC = 300  # mirrors the rail's 5-minute round-trip guard


def _p(reports, name): return os.path.join(reports, name)


def _save_json(path, obj):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix="." + os.path.basename(path), suffix=".tmp")
    with os.fdopen(fd, "w") as f: json.dump(obj, f, indent=1, default=str)
    os.replace(tmp, path)


def _append(path, rec):
    with open(path, "a") as f: f.write(json.dumps(rec, default=str) + "\n")


def _iso(t): return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_quote(pair, last):
    import tfo_crypto as tc
    q = tc.quote(pair)
    if q.get("tape") != "LIVE": return None
    px = float(q["bid"])
    if px <= 0 or (last and (px > 2.5 * last or px < last / 2.5)): return None
    return px


def _default_atr(pair):
    import tfo_crypto as tc
    return trl.kraken_atr_pct(tc.get, tc.kpair(pair))


def _actual_result(p, notional0):
    fills = p.get("exit_fills") or []
    pnl = sum(float(f.get("pnl_usd") or 0) for f in fills)
    fees = float(p.get("entry_fees_usd") or 0) + sum(float(f.get("fees_usd") or 0) for f in fills)
    last = fills[-1] if fills else {}
    return {"gross_ret": pnl / notional0 if notional0 else None, "net_ret": (pnl - fees) / notional0 if notional0 else None,
            "pnl_usd": round(pnl, 2), "fees_usd": round(fees, 2), "exit_rules": [f.get("exit_rule") or f.get("action") for f in fills],
            "exit_utc": last.get("utc"), "exit_px_usd": last.get("px_usd")}


def _r(x, d): return None if x is None or not d else round(x / d, 3)


def update(rows, book, now=None, reports=R, quote_fn=None, atr_fn=None):
    """rows: [{"pair", "px" (live bid), "tape", "last_mark"}] from this rails cycle; book: the tfo_ht_crypto ledger dict (read only)."""
    now = now or dt.datetime.now(UTC)
    sp = _p(reports, "crypto_exit_ab_state.json"); lp = _p(reports, "crypto_exit_ab.jsonl")
    st = json.load(open(sp)) if os.path.exists(sp) else {"created_utc": _iso(now), "trades": {}}
    pos = book.get("positions") or {}
    closed = book.get("closed") or []
    rowmap = {r.get("pair"): r for r in rows or []}
    quote_fn = quote_fn or _default_quote
    atr_fn = atr_fn or _default_atr
    T = st["trades"]
    for pair, p in pos.items():  # 1) register new C3-tagged clips
        if p.get("exit_variant") != trl.C3: continue
        key = "%s|%s" % (pair, p.get("entry_utc"))
        if key in T: continue
        e0 = float(p.get("orig_entry") or p["entry_px"])
        T[key] = {"pair": pair, "entry_utc": p.get("entry_utc"), "e0": e0, "c3_stop_dist": p.get("c3_stop_dist"),
                  "notional0": float((p.get("entry_ticket") or {}).get("principal_usd") or float(p["qty"]) * float(p["entry_px"])),
                  "shadow": {"variant": "V0", "half_banked": False, "wm": None, "atr14_pct": None, "atr_date": None, "frac": 1.0,
                             "realized": 0.0, "closed": False, "last_px": e0, "last_act_utc": p.get("entry_utc"), "events": []},
                  "actual": {"variant": "C3", "closed": False}, "complete": False}
        _append(lp, {"utc": _iso(now), "event": "open", "key": key, "pair": pair, "e0_usd": e0, "c3_stop_dist": p.get("c3_stop_dist"),
                     "notional_usd": T[key]["notional0"], "paper_only": True})
    for key, t in T.items():  # 2) step each open V0 shadow on this cycle's live bid
        sh = t["shadow"]
        if t.get("complete") or sh["closed"]: continue
        pair, e0 = t["pair"], t["e0"]
        r = rowmap.get(pair)
        live = r is not None and str((pos.get(pair) or {}).get("entry_utc")) == str(t["entry_utc"])
        if live:
            px = r.get("px") if r.get("tape") == "LIVE" else None
            lm = r.get("last_mark")
            if px is not None and lm and (px > 2.5 * lm or px < lm / 2.5): px = None
        else:
            try: px = quote_fn(pair, sh.get("last_px"))
            except Exception: px = None
        if px is None: continue
        px = float(px)
        lt = sh.get("last_act_utc")
        if lt and (now - trl._parse(lt)).total_seconds() < GUARD_SEC: sh["last_px"] = px; continue
        if sh["half_banked"]:
            today = now.astimezone(SYD).date().isoformat()
            if sh.get("atr_date") != today:
                try:
                    a = atr_fn(pair)
                    if a: sh["atr14_pct"], sh["atr_date"] = round(float(a), 6), today
                except Exception: pass
        dec = trl.decide(e0, px, "long", sh["half_banked"], sh.get("wm"), sh.get("atr14_pct"), trl.held(t["entry_utc"], now, True), crypto=True)
        r0 = px / e0 - 1.0
        if dec["act"] == "HALF":
            sh["realized"] += 0.5 * r0; sh["frac"] = 0.5; sh["half_banked"] = True; sh["wm"] = px; sh["last_act_utc"] = _iso(now)
            ev = {"utc": _iso(now), "event": "shadow_half", "key": key, "pair": pair, "px_usd": px, "ret_orig_pct": round(100 * r0, 3), "rule": dec["rule"]}
            sh["events"].append(ev); _append(lp, ev)
        elif dec["act"] in ("STOP", "FLAT"):
            sh["realized"] += sh["frac"] * r0; sh["frac"] = 0.0; sh["closed"] = True; sh["last_act_utc"] = _iso(now)
            sh["exit_rule"], sh["exit_utc"], sh["exit_px_usd"] = dec["rule"], _iso(now), px
            ev = {"utc": _iso(now), "event": "shadow_exit", "key": key, "pair": pair, "px_usd": px, "ret_orig_pct": round(100 * r0, 3),
                  "rule": dec["rule"], "shadow_total_ret_pct": round(100 * sh["realized"], 3)}
            sh["events"].append(ev); _append(lp, ev)
        elif sh["half_banked"] and dec.get("wm") is not None:
            sh["wm"] = dec["wm"]
        sh["last_px"] = px
        sh["mtm_ret"] = sh["realized"] + sh["frac"] * r0
    for key, t in T.items():  # 3) actual C3 leg: MTM while open; result once it appears in book["closed"]
        a = t["actual"]
        if a["closed"]: continue
        p = pos.get(t["pair"])
        if p is not None and str(p.get("entry_utc")) == str(t["entry_utc"]):
            r = rowmap.get(t["pair"])
            if r and r.get("px") is not None and t["notional0"]:
                realized = sum(float(f.get("pnl_usd") or 0) for f in (p.get("exit_fills") or []))
                a["mtm_ret"] = (realized + float(p["qty"]) * (float(r["px"]) - float(p["entry_px"]))) / t["notional0"]
                a["half_banked"] = bool(p.get("rebase") or (p.get("trail") or {}).get("half_banked"))
            continue
        cp = None
        for c in closed:
            cpos = (c or {}).get("position") or {}
            if c.get("pair") == t["pair"] and str(cpos.get("entry_utc")) == str(t["entry_utc"]): cp = cpos
        if cp is None: continue
        a.update(_actual_result(cp, t["notional0"])); a["closed"] = True
        _append(lp, {"utc": _iso(now), "event": "actual_exit", "key": key, "pair": t["pair"], "gross_ret_pct": a["gross_ret"] is not None and round(100 * a["gross_ret"], 3),
                     "net_ret_pct": a["net_ret"] is not None and round(100 * a["net_ret"], 3), "exit_rules": a["exit_rules"]})
    for key, t in T.items():  # 4) complete when both legs are closed
        a, sh = t["actual"], t["shadow"]
        if t.get("complete") or not (a["closed"] and sh["closed"]): continue
        t["complete"] = True
        c3, v0 = a.get("gross_ret"), sh["realized"]
        rec = {"utc": _iso(now), "event": "trade_complete", "key": key, "pair": t["pair"], "entry_utc": t["entry_utc"], "notional_usd": t["notional0"],
               "c3_stop_dist": t.get("c3_stop_dist"),
               "c3_actual_gross_ret_pct": c3 is not None and round(100 * c3, 3), "c3_actual_net_ret_pct": a.get("net_ret") is not None and round(100 * a["net_ret"], 3),
               "v0_shadow_gross_ret_pct": round(100 * v0, 3), "delta_c3_minus_v0_gross_pct": c3 is not None and round(100 * (c3 - v0), 3),
               "c3_R": _r(c3, t.get("c3_stop_dist")), "v0_R": _r(v0, 0.05),
               "c3_exit_rules": a.get("exit_rules"), "v0_shadow_exit_rule": sh.get("exit_rule"), "v0_shadow_exit_utc": sh.get("exit_utc"), "c3_exit_utc": a.get("exit_utc"), "paper_only": True}
        _append(lp, rec)
        _append(_p(reports, "shark_scorecard.jsonl"), {"ts": now.astimezone(SYD).isoformat(timespec="seconds"), "source": "crypto_exit_ab", "action": "exit_ab_complete",
                "sydney_date": now.astimezone(SYD).date().isoformat(), "symbol": t["pair"], **{k: v for k, v in rec.items() if k not in ("event", "utc", "pair", "key")}})
    st["updated_utc"] = _iso(now)
    _save_json(sp, st)
    summ = {"updated_utc": _iso(now), "paper_only": True, "rule_doc": "SHARK_EXIT_RULE.md (crypto C3 section)", "trades": [
        {"key": k, "pair": t["pair"], "entry_utc": t["entry_utc"], "complete": t.get("complete", False), "c3_stop_dist": t.get("c3_stop_dist"),
         "c3_actual": {x: t["actual"].get(x) for x in ("closed", "mtm_ret", "gross_ret", "net_ret", "exit_rules", "half_banked")},
         "v0_shadow": {x: t["shadow"].get(x) for x in ("closed", "mtm_ret", "realized", "half_banked", "exit_rule", "wm", "frac")}} for k, t in T.items()]}
    done = [t for t in T.values() if t.get("complete") and t["actual"].get("gross_ret") is not None]
    if done:
        summ["completed_n"] = len(done)
        summ["avg_c3_gross_ret_pct"] = round(100 * sum(t["actual"]["gross_ret"] for t in done) / len(done), 3)
        summ["avg_v0_shadow_gross_ret_pct"] = round(100 * sum(t["shadow"]["realized"] for t in done) / len(done), 3)
        rs = [_r(t["actual"]["gross_ret"], t.get("c3_stop_dist")) for t in done]; rs = [x for x in rs if x is not None]
        if rs: summ["avg_c3_R"] = round(sum(rs) / len(rs), 3)
        summ["avg_v0_R"] = round(sum(t["shadow"]["realized"] / 0.05 for t in done) / len(done), 3)
    _save_json(_p(reports, "crypto_exit_ab_summary.json"), summ)
    print("CRYPTO-EXIT-AB ok trades=%d open_shadow=%d complete=%d" % (len(T), sum(1 for t in T.values() if not t["shadow"]["closed"]), sum(1 for t in T.values() if t.get("complete"))))
    return st
