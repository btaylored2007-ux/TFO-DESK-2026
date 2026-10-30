#!/usr/bin/env python3
"""V1 rotation of trend-failed, un-banked LONGS (Head Trader decision 2026-09-28 15:41 AEST). PAPER ONLY. Not a loop.

  tfo_rotation.py review                 read-only: trend filter on every un-banked long/short in the V1 ticket books
  tfo_rotation.py sell SYM [--execute]   sell ONE rotation candidate NOW via tfo_ticket.execute (the desk's normal exit/settle
                                         path: full costs, proceeds to powder); refused unless its print is a valid regular-session
                                         print (else it is queued)
  tfo_rotation.py run [--execute]        process reports/ht_rotation_queue.json: sell each queued name whose market now shows a
                                         valid regular-session print (run by the market-open wake BEFORE `tfo_watchlist.py fire`)
Rule: long, not half-banked, not bought today, trend filter FAILS (last <= SMA50 or 20d return <= 0; tfo_watchlist.trend_check),
and not near its +2% half (return from original fill < +1.5%). Re-checked on fresh data at sale time. exit_rule rotation_trend_fail.
Shorts are reported (mirrored filter) but never rotated by this tool. Crypto is out of scope (see tfo_crypto.py sell).

2026-09-29 13:03 AEST funder follow-up ("liquidate any stale positions", Head Trader decision) adds two exit rules, same ticket path,
same session/print/double guards, same queue (each queued item carries its rule):
  --rule venue_retired   long or short leftover on a venue V1 no longer opens (v1_venues.entry_block_reason), not half-banked
  --rule stale_flat      long, not half-banked, held >= STALE_MIN_DAYS calendar days, return from original fill <= STALE_MAX_RET_PCT
Never sold by these rules: half-banked runners on trail, PROTECT names (today's fresh entries + 8766.T post-split restore).
"""
import argparse, datetime as dt, json, os, sys
BASE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, BASE)
import tfo_ticket as tt, tfo_watchlist as wl, desk_lock
from zoneinfo import ZoneInfo
UTC, SYD = dt.timezone.utc, ZoneInfo("Australia/Sydney")
QUEUE = os.path.join(BASE, "reports", "ht_rotation_queue.json")
NEAR_HALF_PCT = 1.5
EXCLUDE_TODAY = {"8766.T", "9984.T", "6501.T", "0939.HK"}     # HT buys of 2026-09-28: never rotated by this tool
PROTECT = {"CSL.AX", "XLK", "IBIT", "8766.T"}                 # 2026-09-29 13:03 AEST: fresh entries / 8766.T restore, never sold here
RULES = ("rotation_trend_fail", "venue_retired", "stale_flat")
STALE_MIN_DAYS = 3.0
STALE_MAX_RET_PCT = 1.0
TZ = {"TO": "America/Toronto", "ST": "Europe/Stockholm", "L": "Europe/London", "TW": "Asia/Taipei", "AX": "Australia/Sydney",
      "SN": "America/Santiago", "HE": "Europe/Helsinki", "T": "Asia/Tokyo", "HK": "Asia/Hong_Kong", "US": "America/New_York",
      "DE": "Europe/Berlin", "PA": "Europe/Paris", "OL": "Europe/Oslo", "CO": "Europe/Copenhagen", "SW": "Europe/Zurich", "NS": "Asia/Kolkata"}

def trend(sym, q):
    tz = ZoneInfo(TZ.get(tt.venue(sym), "America/New_York")); last = q.get("px") if q else None
    qd = dt.datetime.fromisoformat(q["quote_utc"].replace("Z", "+00:00")).astimezone(tz).date() if q and q.get("quote_utc") else dt.datetime.now(tz).date()
    closes = [r[4] for r in wl.load_history([sym], before=qd).get(sym, [])]
    return wl.trend_check(closes, last), wl.trend_stats(closes, last)

def unbanked():
    for k in tt.BOOKS:
        d = tt.load(k)
        for sym, p, side, e0 in tt.positions(k, d):
            if not tt.half_banked(p): yield k, sym, p, side, e0

def evaluate_rule(sym, row, rule):
    """venue_retired / stale_flat (2026-09-29). -> (decision, reasons). Pure on the scan row; no I/O except v1_venues."""
    if sym in PROTECT: return "KEEP", ["protected (fresh entry / post-split restore)"]
    if row.get("half_banked"): return "KEEP", ["half-banked residual on trail (excluded)"]
    if row.get("status") == "NO_QUOTE": return "KEEP", ["no quote: %s" % row.get("why")]
    r0 = row.get("ret_orig_pct")
    if rule == "venue_retired":
        import v1_venues
        if v1_venues.liquidation_exempt(sym): return "KEEP", ["Tokyo/Asia leftover: runs off on its rails (funder 2026-09-29 16:22 AEST), never venue-swept"]
        vr = v1_venues.entry_block_reason(sym)
        if not vr: return "KEEP", ["venue still open to V1 entries"]
        return "ROTATE", ["venue_retired: %s" % vr, "ret %s%%, held %s d" % (r0, row.get("held_days"))]
    if rule == "stale_flat":
        if row["side"] != "long": return "KEEP", ["short (stale_flat rotates longs only)"]
        hd = row.get("held_days")
        if hd is None or hd < STALE_MIN_DAYS: return "KEEP", ["held %s d < %s d" % (hd, STALE_MIN_DAYS)]
        if r0 is None or r0 > STALE_MAX_RET_PCT: return "KEEP", ["ret %s%% > +%s%% (making progress)" % (r0, STALE_MAX_RET_PCT)]
        return "ROTATE", ["stale_flat: held %s d, ret %s%% from fill, not half-banked" % (hd, r0)]
    raise ValueError("unknown rule %r" % rule)

def evaluate(sym, row, rule="rotation_trend_fail"):
    """-> (decision, reasons). decision in ROTATE/KEEP; row from tfo_ticket.scan (fresh quote)."""
    if rule != "rotation_trend_fail": return evaluate_rule(sym, row, rule)
    q = row.get("_q") or {}
    if sym in EXCLUDE_TODAY: return "KEEP", ["bought today (excluded)"]
    if row.get("half_banked"): return "KEEP", ["half-banked residual on trail (excluded)"]
    if row.get("status") == "NO_QUOTE": return "KEEP", ["no quote: %s" % row.get("why")]
    fails, st = trend(sym, q)
    r0 = row.get("ret_orig_pct")
    if row["side"] != "long":
        mirror = bool(st["sma50"] and st["ret20_pct"] is not None and q.get("px") < st["sma50"] and st["ret20_pct"] < 0)
        return "KEEP", ["short (tool rotates longs only); mirrored trend %s (sma50 %s, 20d %s%%), ret %s%%" % ("PASS" if mirror else "FAIL", st["sma50"], st["ret20_pct"], r0)]
    if not fails: return "KEEP", ["trend PASS (sma50 %s, 20d %s%%), ret %s%%" % (st["sma50"], st["ret20_pct"], r0)]
    if r0 is not None and r0 >= NEAR_HALF_PCT: return "KEEP", ["trend FAIL but near +2%% half (ret %s%%)" % r0]
    return "ROTATE", fails + ["sma50 %s, 20d %s%%, last %s, ret %s%%" % (st["sma50"], st["ret20_pct"], q.get("px"), r0)]

def load_queue():
    try: return json.load(open(QUEUE))
    except (OSError, ValueError): return {"note": "V1 rotation_trend_fail sales queued for their market's next regular session (tfo_rotation.py run). PAPER.", "items": {}}

def save_queue(qd):
    tmp = QUEUE + ".tmp"; json.dump(qd, open(tmp, "w"), indent=1, default=str); os.replace(tmp, QUEUE)

def sell(sym, execute, from_queue=False, rule="rotation_trend_fail"):
    rows = [r for r in tt.scan({sym}) if r["sym"] == sym]
    if not rows: return "SKIP %s: not held in a V1 ticket book" % sym, None
    row = rows[0]; dec, why = evaluate(sym, row, rule)
    if dec != "ROTATE": return "SKIP %s: %s" % (sym, "; ".join(why)), "drop"
    blocker = row.get("why") if row.get("status") != "NO_QUOTE" else row.get("why")
    # tfo_ticket.scan sets `why` for session/tape/chronology/auction/double-guard even when no rail fired
    if blocker:
        return "QUEUE %s: %s (%s)" % (sym, blocker, "; ".join(why)), "queue"
    row.update(rail="FLAT", status="READY", exit_rule=rule, rail_level_usd=None)
    if not execute: return "PREVIEW SELL %s %s %s @ %s (%s)" % (row["book"], sym, row["side"], row.get("px_native"), "; ".join(why)), None
    line = tt.execute(row)
    if not line: return "NOFILL %s (position gone or double guard)" % sym, None
    with desk_lock.ledger_lock(tt.BOOKS[row["book"]]):      # annotate the exit ticket (audit only)
        d = tt.load(row["book"]); tks = d.get("head_trader_tickets") or []
        tk = next((t for t in reversed(tks) if t.get("sym") == sym and t.get("action") == "FLAT"), None)
        if tk is not None:
            tk.update({"exit_rule": rule, "rotation_reasons": why, "rotation_by": "TFO Head Trader decision 2026-09-28 15:41 AEST" if rule == "rotation_trend_fail"
                       else "TFO Head Trader decision 2026-09-29 13:03 AEST (funder: liquidate stale positions)", "rotation_from_queue": from_queue})
            c = d.get("closed") or {}
            for rec in (c.values() if isinstance(c, dict) else [x.get("position", x) for x in c]):
                for f in rec.get("exit_fills") or []:
                    if f.get("ticket_id") == tk["ticket_id"]: f.update({"exit_rule": rule, "rotation_reasons": why})
            tt.save(row["book"], d)
    return line, "sold"

def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="c", required=True)
    rv = sp.add_parser("review"); rv.add_argument("--rule", choices=RULES, default="rotation_trend_fail")
    s = sp.add_parser("sell"); s.add_argument("sym"); s.add_argument("--execute", action="store_true"); s.add_argument("--rule", choices=RULES, default="rotation_trend_fail")
    r = sp.add_parser("run"); r.add_argument("--execute", action="store_true")
    a = ap.parse_args()
    if a.c == "review":
        names = [x[1] for x in unbanked()]
        for row in tt.scan(set(names)):
            dec, why = evaluate(row["sym"], row, a.rule); print("%-6s %-22s %-14s %-5s %s" % (dec, row["book"], row["sym"], row["side"], "; ".join(why)))
        return
    qd = load_queue(); now = dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    todo = [a.sym] if a.c == "sell" else sorted(qd["items"])
    for sym in todo:
        rule = a.rule if a.c == "sell" else (qd["items"].get(sym) or {}).get("exit_rule", "rotation_trend_fail")
        msg, act = sell(sym, a.execute, from_queue=(a.c == "run"), rule=rule); print(msg)
        if not a.execute: continue
        if act == "queue":
            it = qd["items"].setdefault(sym, {"queued_utc": now, "exit_rule": rule}); it.update(last_check_utc=now, last_status=msg)
        elif act in ("sold", "drop") and sym in qd["items"]:
            it = qd["items"].pop(sym); qd.setdefault("done", []).append({**it, "sym": sym, "done_utc": now, "result": msg})
    if a.execute: save_queue(qd)

if __name__ == "__main__":
    main()
