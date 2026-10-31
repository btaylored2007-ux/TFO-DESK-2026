#!/usr/bin/env python3
"""TFO Head Trader ticket tool (Brett mandate 2026-09-25). PAPER ONLY.

Preview by default. --execute writes. Only the Head Trader runs this.
Rails per NAME from its own original fill (USD) -- V1 variant C (Head Trader 2026-09-25, funder-authorised; see tfo_trail_rail.py):
  long: -5% STOP (vs original) until the +2% HALF (vs original, once); then the residual trails its high-water mark
        by max(3%, 3xATR14/price) with a breakeven floor (original fill); 30-CALENDAR-day max hold from fill (HT 2026-09-28); NO +5% flat cap.
  short: mirror (low-water mark), STOP at -4%, trail floor 4%. Residual exits ticket as FLAT with exit_rule.
Accounting per ticket, same step: book realises P&L, book cash/seed fall by proceeds,
powder (gap_close_levered_book) cash/seed rise by proceeds via paired capital_transfer.
Firm moves by P&L only. Never rewrites entry px/qty/date; rebase lives in rail_rebase_usd.
"""
import argparse, datetime as dt, json, os, sys, tempfile, uuid
from zoneinfo import ZoneInfo
BASE = os.path.dirname(os.path.abspath(__file__)); R = os.path.join(BASE, "reports")
sys.path.insert(0, BASE)
import desk_lock
import desk_costs  # 2026-09-25: shared retail cost model; fees per fill (PAPER)
import fundamental_book as fb
import tfo_trail_rail as trl  # V1 variant C exit rail (2026-09-25)
from paper_execution_price import eligible, chronological, receipt
SYD, LON, UTC = ZoneInfo("Australia/Sydney"), ZoneInfo("Europe/London"), dt.timezone.utc
GCL = "gap_close_levered_book"
BOOKS = {"fundamental_book": "fundamental", "fundamental_spec_book": "fundamental_spec", "convexity_sleeve": "convexity_sleeve"}
LOG = os.path.join(R, "tfo_head_trader_log.txt"); FACTS = os.path.join(R, "tfo_head_trader_facts.jsonl")
CLOSED_NAMES = {"AVAX", "ONDO", "LTC", "XRP", "SOXL", "HYPE", "2800.HK", "NEAR", "SUI", "ZEC", "TAO", "IAG", "RCL", "UNI_SUI"}  # 2026-09-30 Brett closed-book guard list
# 2026-09-28 HT #3: ledger marks refresh on this rail cycle (valuation only; tfo_equity_refresh --write semantics).
MARK_REFRESH_ENABLED = True
MARK_REFRESH_LOCKS = ("convexity_sleeve", "tfo_gcl_powder")   # taken after the fundamental locks (book -> powder order)
MARK_REFRESH_BACKUP_PREFIX = "equity-refresh-auto-"
MARK_REFRESH_KEEP_AUTO_BACKUPS = 288                             # ~24h of 5-minute cycles; manual backups never pruned
CORP_ACTIONS_PRECHECK = True   # 2026-09-29: tfo_corporate_actions.precheck() at the start of every --execute cycle (tests patch off)

def path(k): return os.path.join(R, k + ".json")
def load(k): return json.load(open(path(k)))
def save(k, d):
    fd, tmp = tempfile.mkstemp(dir=R, prefix="." + k, suffix=".tmp")
    with os.fdopen(fd, "w") as f: json.dump(d, f, indent=1, default=str)
    os.replace(tmp, path(k))

def venue(sym):
    if "." not in sym: return "US"
    return sym.rsplit(".", 1)[1]

def time_block(sym, now):
    s = now.astimezone(SYD); l = now.astimezone(LON); v = venue(sym)
    hm = lambda t: t.hour * 60 + t.minute
    if v == "L":
        if not (8 * 60 + 10 <= hm(l) < 16 * 60 + 20): return "London auction/edge window (08:00-08:10, 16:20-16:35 London, or shut)"
    if v == "AX" and hm(s) < 10 * 60 + 10: return "ASX opening auction"
    if v == "T":
        j = now.astimezone(ZoneInfo("Asia/Tokyo"))
        if hm(j) >= 15 * 60 + 20: return "Tokyo close auction window"
    return None

def half_banked(p):
    """V1 variant C: a prior +2% half-bank (rail_half_count / rail_rebase_usd / rail_trail) arms the residual trail."""
    return bool(p.get("rail_half_count") or p.get("rail_rebase_usd") or (p.get("rail_trail") or {}).get("half_banked"))

def rail_detail(entry0, px, side, p, now, atr_fetch=None, sym=""):
    """Variant C decision + the position's updated rail_trail state (persisted only by --execute)."""
    st = dict(p.get("rail_trail") or {}); banked = half_banked(p)
    wk = "lwm_usd" if side == "short" else "hwm_usd"
    if banked and atr_fetch: trl.refresh_atr(st, atr_fetch, now.astimezone(SYD).date().isoformat())
    # 2026-10-02 shark exit test: positions tagged exit_variant=V7 (new tfo_buy clips) use the V7 rail; untagged = unchanged
    var = p.get("exit_variant") if p.get("exit_variant") == trl.V7 else None
    sess = trl.sessions_after(trl.entry_time(p), now, venue(sym)) if var else None
    # 2026-09-28 HT: max hold = 30 calendar days from the ORIGINAL fill; convexity_sleeve stores it as opened_utc
    dec = trl.decide(entry0, px, side, banked, st.get(wk), st.get("atr14_pct"), trl.held(trl.entry_time(p), now, False), variant=var, held_sessions=sess)
    if banked:
        if not st.get(wk): st["seeded"] = "current_px_at_switch"
        st.update({"half_banked": True, wk: dec["wm"], "trail_pct": round(dec["trail_pct"], 6), "level_usd": round(dec["level"], 8),
                   "rule": trl.rule_text(var), "updated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ")})
    return dec, st

def rail(entry0, rebase, px, side):
    """Back-compat wrapper (stored-state-free): variant C with a trail seeded at px when already half-banked."""
    dec, _ = rail_detail(entry0, px, side, {"rail_rebase_usd": rebase}, dt.datetime.now(UTC))
    return dec["act"], dec["r0"]

# 2026-09-29 bad-tick guard (8766.T incident, PAPER). Tokio Marine went ex a 15-for-1 split on 29 Sep; the ledger still
# carried the pre-split qty/basis, so the real post-split print (JPY 528 = $3.35) read as -93%. The 2.5x check blocked the
# 10:33 cycle, but the book marker then wrote that print into mark_usd, so at 10:38 the 2.5x check compared the price with
# itself and a STOP filled at -$239k. A rail now refuses any print implying a >30% one-print move vs the last good mark
# (either direction) OR an adverse >30% move vs the ORIGINAL fill (long: below, short: above) -- the entry leg cannot be
# contaminated by the marker. Rules/levels are unchanged; the row is BLOCKED (watchdog alerts) for a human to check the
# quote, FX, or an unapplied split. The speculative small-cap sleeve keeps only the 2.5x check.
from bad_tick_guard import BAD_TICK_MAX_MOVE, BAD_TICK_EXEMPT_BOOKS, SPLIT_RATIOS, split_hint, bad_tick  # shared with the markers
import bad_tick_guard as btg

def positions(k, d):
    for sym, p in (d.get("positions") or {}).items():
        side = p.get("side", "long")
        if k == "convexity_sleeve":
            e0 = p.get("rail_orig_entry_usd") or (float(p["premium_usd"]) / float(p["units"]))
        else:
            e0 = p.get("rail_orig_entry_usd") or float(p["entry_px_usd"])
        yield sym, p, side, e0

def scan(only=None):
    now = dt.datetime.now(UTC); out = []
    for k in BOOKS:
        d = load(k); syms = [s for s in d.get("positions", {}) if not only or s in only]
        if not syms: continue
        qs, fails = fb.fetch_prices(syms, require_executable=False)
        for sym, p, side, e0 in positions(k, d):
            if sym not in syms: continue
            q = qs.get(sym); row = {"book": k, "sym": sym, "side": side, "entry0_usd": round(e0, 6)}
            if not q: row.update(status="NO_QUOTE", why=fails.get(sym)); out.append(row); continue
            px = float(q["px_usd"]); last = float(p.get("mark_usd") or 0)
            if k == "convexity_sleeve": last = last / float(p["units"]) if p.get("units") else 0
            dec, st = rail_detail(e0, px, side, p, now, (lambda s=sym: trl.equity_atr_pct(s)), sym)
            act, r0 = dec["act"], dec["r0"]
            row.update(px_usd=round(px, 6), px_native=q.get("px"), ccy=q.get("ccy"), ret_orig_pct=round(100 * r0, 2), rail=act,
                       quote_utc=q.get("quote_utc"), age_sec=q.get("age_sec"), session=q.get("session"), cls=q.get("paper_price_class"))
            row.update(exit_rule=dec["rule"], rail_level_usd=dec["level"] and round(dec["level"], 6), half_banked=half_banked(p))
            if p.get("exit_variant"): row["exit_variant"] = p.get("exit_variant")  # 2026-10-02 shark exit test audit
            _et = trl.entry_time(p); _age = trl.held(_et, now, False); _due = trl.max_hold_due_utc(_et)  # 2026-09-28 audit fields
            row.update(entry_time_utc=_et, held_days=None if _age is None else round(_age, 2),
                       max_hold_due_utc=_due and _due.strftime("%Y-%m-%dT%H:%M:%SZ"))
            if row["half_banked"]:
                row.update(trail_pct=st.get("trail_pct"), trail_wm_usd=st.get("hwm_usd") or st.get("lwm_usd"), atr14_pct=st.get("atr14_pct"), trail_seeded=st.get("seeded"), _trail=st)
            us = venue(sym) == "US"
            tape = "LIVE" if (us and q.get("quote_current") and (q.get("age_sec") or 999) < 60) else "DELAYED"
            row["tape"] = tape
            why = None
            if px <= 0 or (last and (px > 2.5 * last or px < last / 2.5)): why = "price sanity (>2.5x last good)"
            elif k not in BAD_TICK_EXEMPT_BOOKS and bad_tick(px, side, last, e0):
                why = bad_tick(px, side, last, e0); row["bad_tick"] = True   # 2026-09-29: >30% one-print move guard
            elif q.get("session") != "regular" or not eligible(q):
                # Open session but no current-session 1m paper bar yet (thin name): wait, do not treat as STALE/SHUT.
                if (q.get("session") == "regular" and not q.get("paper_executable") and not q.get("executable")
                        and q.get("fx_timestamp_verified") is True and not q.get("paper_price_class")):
                    why = "awaiting first regular-session print (no tape yet)"
                else:
                    why = "not a regular-session executable print (STALE/SHUT)"
            elif us and tape != "LIVE": why = "US print not under 60s"
            elif not chronological(q, p): why = "delayed bar predates entry or repeats exit bar"
            row["_trail_ok"] = why is None  # water marks only move on sane, regular-session, current prints
            if why: pass
            elif time_block(sym, now): why = time_block(sym, now)
            else:
                lt = p.get("rail_last_ticket_utc")
                if lt and (now - dt.datetime.fromisoformat(lt.replace("Z", "+00:00"))).total_seconds() < 300: why = "round-trip/double guard (<5 min since last ticket)"
            row["status"] = ("BLOCKED" if why else "READY") if act else "HOLD"
            if why: row["why"] = why
            row["_q"] = q
            out.append(row)
    return out

def transfer(book, k, gcl, usd, utc, tag, note):
    usd = round(usd, 2)
    if usd <= 0: return
    ev = {"type": "capital_transfer", "utc": utc, "from": k, "to": GCL, "note": note, "tag": tag, "paper_only": True}
    book.setdefault("capital_events", []).append({**ev, "usd": -usd})
    before = float(gcl.get("cash") or 0)
    gcl["cash"] = round(before + usd, 2)
    gcl.setdefault("capital_events", []).append({**ev, "usd": usd, "cash_before": before, "cash_after": gcl["cash"]})
    book["cash"] = round(float(book["cash"]) - usd, 2)
    book["seed_usd"] = round(float(book["seed_usd"]) - usd, 2)
    if book.get("start") is not None: book["start"] = round(float(book["start"]) - usd, 2)
    gcl["seed_usd"] = round(float(gcl["seed_usd"]) + usd, 2)
    if gcl.get("start") is not None: gcl["start"] = round(float(gcl["start"]) + usd, 2)
    gcl["equity"] = gcl["cash"] if not (gcl.get("holdings") or {}) else round(float(gcl.get("equity") or 0) + usd, 2)
    gcl["updated"] = utc

def execute(row):
    k, sym, q, act = row["book"], row["sym"], row["_q"], row["rail"]
    now = dt.datetime.now(UTC); utc = now.strftime("%Y-%m-%dT%H:%M:%SZ"); tid = "HT-" + uuid.uuid4().hex[:10]
    with desk_lock.ledger_lock(BOOKS[k]), desk_lock.ledger_lock("tfo_gcl_powder"):
        d = load(k); gcl = load(GCL); p = d["positions"].get(sym)
        if not p: return None
        if p.get("rail_last_ticket_utc") and (now - dt.datetime.fromisoformat(p["rail_last_ticket_utc"].replace("Z", "+00:00"))).total_seconds() < 300: return None
        px = float(q["px_usd"]); side = p.get("side", "long"); units0 = float(p["units"])
        if k not in BAD_TICK_EXEMPT_BOOKS:  # 2026-09-29 defence in depth: re-check against the ledger as loaded under the lock
            _last = float(p.get("mark_usd") or 0)
            if k == "convexity_sleeve": _last = _last / units0 if units0 else 0
            _e0 = p.get("rail_orig_entry_usd") or (float(p["premium_usd"]) / units0 if k == "convexity_sleeve" else float(p["entry_px_usd"]))
            _bt = bad_tick(px, side, _last, _e0)
            if _bt:
                print("BAD-TICK refused %s %s %s: %s" % (k, sym, act, _bt)); return None
        sold = units0 / 2 if act == "HALF" else units0
        if k == "convexity_sleeve":
            e0 = p.get("rail_orig_entry_usd") or float(p["premium_usd"]) / units0
            principal = float(p["premium_usd"]) * (sold / units0); proceeds = sold * px; pnl = proceeds - principal
            p["premium_usd"] = round(float(p["premium_usd"]) - principal, 2); p["units"] = units0 - sold
            p["mark_usd"] = round(p["units"] * px, 2)
        else:
            e0 = p.get("rail_orig_entry_usd") or float(p["entry_px_usd"])
            principal = sold * float(p["entry_px_usd"])
            pnl = (sold * (float(p["entry_px_usd"]) - px)) if side == "short" else (sold * px - principal)
            proceeds = principal + pnl
            p["units"] = units0 - sold; p["notional_usd"] = round(float(p["notional_usd"]) * (p["units"] / units0), 2)
            p["mark"], p["mark_usd"] = q["px"], round(px, 6)
            if p.get("unrealised_usd") is not None:
                p["unrealised_usd"] = round((sold and (p["units"] * ((float(p["entry_px_usd"]) - px) if side == "short" else (px - float(p["entry_px_usd"])))) ), 2)
        # desk_costs 2026-09-25: exit fees. Book cash receives proceeds net of fees; net proceeds go to powder.
        # fundamental books carry fees in costs_paid_usd (their equity identity nets it); others net realised.
        fee = desk_costs.fill_fees(sym, desk_costs.side_for(act, side), sold, px, q.get("px"), q.get("ccy"), now)
        fees = round(fee["fees_usd"], 2)
        p.setdefault("rail_orig_entry_usd", e0)
        if act == "HALF": p["rail_rebase_usd"] = px; p["rail_half_count"] = int(p.get("rail_half_count") or 0) + 1
        if act == "HALF": p["rail_trail"] = {"half_banked": True, ("lwm_usd" if side == "short" else "hwm_usd"): px, "seeded": "half_fill", "half_utc": utc, "rule": trl.rule_text(p.get("exit_variant"))}  # variant C (V7-tagged: +10% half)
        p["rail_last_ticket_utc"] = utc; p["last_paper_exit_source_utc"] = q.get("quote_utc")
        d["cash"] = round(float(d["cash"]) + proceeds - fees, 2)
        d["realised_usd"] = round(float(d.get("realised_usd") or 0) + pnl, 2)
        if k in ("fundamental_book", "fundamental_spec_book"):
            d["costs_paid_usd"] = round(float(d.get("costs_paid_usd") or 0) + fees, 4)
        else:
            d["realised_usd"] = round(d["realised_usd"] - fees, 2)
            d["fees_paid_usd"] = round(float(d.get("fees_paid_usd") or 0) + fees, 2)
        tk = {"ticket_id": tid, "utc": utc, "action": act, "sym": sym, "side": side, "qty": sold, "px_usd": px, "px_native": q.get("px"),
              "ccy": q.get("ccy"), "tape": row["tape"], "principal_usd": round(principal, 2), "proceeds_usd": round(proceeds, 2),
              "pnl_usd": round(pnl, 2), "ret_orig_pct": row["ret_orig_pct"], "by": "TFO Head Trader", "paper_only": True, **receipt(q),
              "fees_usd": fees, "fee_detail": fee["breakdown"], "fee_schedule": fee["schedule"], "net_proceeds_usd": round(proceeds - fees, 2)}
        if row.get("exit_rule"): tk["exit_rule"] = row["exit_rule"]; tk["rail_level_usd"] = row.get("rail_level_usd")  # variant C audit
        if p.get("exit_variant"): tk["exit_variant"] = p.get("exit_variant")  # 2026-10-02 shark exit test
        p.setdefault("exit_fills", []).append(tk); d.setdefault("head_trader_tickets", []).append(tk)
        if p["units"] <= 1e-9:
            if k == "convexity_sleeve":
                d.setdefault("closed", []).append({"symbol": sym, **{x: p.get(x) for x in ("instrument",)}, "closed_utc": utc, "exit_usd": round(proceeds, 2), "pnl_usd": round(pnl, 2), "reason": "Head Trader " + act, "ticket_id": tid, "position": p})
            else:
                key = sym if sym not in d.setdefault("closed", {}) else "%s#%s" % (sym, tid)
                d["closed"][key] = p
            del d["positions"][sym]
        if d.get("unrealised_usd") is not None and k != "convexity_sleeve":
            d["unrealised_usd"] = round(sum(x.get("unrealised_usd") or 0 for x in d["positions"].values()), 2)
        transfer(d, k, gcl, proceeds - fees, utc, "HT_%s_%s" % (act, sym), "Head Trader %s %s: proceeds to powder; book seed/cash cut by proceeds; firm moves by P&L only. PAPER" % (act, sym))
        # keep the stored equity honest until the book's own marker re-strikes it
        if d.get("equity") is not None: d["equity"] = round(float(d["equity"]) - proceeds, 2)
        save(k, d); save(GCL, gcl)
    syd = now.astimezone(SYD).strftime("%Y-%m-%d %H:%M")
    line = "LOG %s %s %s %.6g %.6g %s %s %.2f %.2f %.2f %.2f" % (syd, act, sym, sold, px, row["tape"], k, principal, proceeds, pnl, fees)
    open(LOG, "a").write(line + "\n")
    open(FACTS, "a").write(json.dumps({"syd": syd, "fact": "%s %s %s from original fill %.2f%%; %s tape; proceeds %.2f to powder; pnl %.2f; fees %.2f" % (act, sym, k, row["ret_orig_pct"], row["tape"], proceeds - fees, pnl, fees), "ticket": tid, "fees_usd": fees}) + "\n")
    try:  # 2026-10-02 shark slippage LOGGING ONLY (shark_slippage.py): live bid/ask next to the paper fill; after the save; never raises
        import shark_slippage; shark_slippage.record(act, k, sym, side, sold, {**q, "tape": row.get("tape")}, {**tk, "ht_clip": bool(p.get("ht_clip"))}, ledger_log=LOG)
    except Exception: pass
    try:  # hash-chained audit after the ledger save; never blocks the exit that already booked
        import audit
        who = "fundamental" if k in ("fundamental_book", "fundamental_spec_book") else ("kraken" if k in ("tfo_ht_crypto", "kraken") else "tfo")
        audit.log("stop" if act in ("STOP", "TRAIL") else "exit", who, {
            "ticket": tid, "sym": sym, "action": act, "qty": sold, "px": px, "book": k, "paper_only": True})
    except Exception:
        pass
    return line

def persist_trails(rows):
    """--execute only: store updated high/low-water marks + daily ATR where the engine keeps rail state (position.rail_trail)."""
    by = {}
    for r in rows:
        if r.get("_trail"): by.setdefault(r["book"], []).append(r)
    for k, rs in by.items():
        with desk_lock.ledger_lock(BOOKS[k]):
            d = load(k); changed = False
            for r in rs:
                p = (d.get("positions") or {}).get(r["sym"])
                if p is None or not half_banked(p): continue
                if r.get("_trail_ok"): p["rail_trail"] = r["_trail"]  # water mark moves only on an executable print
                else:  # keep the daily ATR cache only; the water mark waits for a regular-session print
                    t = p.setdefault("rail_trail", {"half_banked": True, "rule": trl.RULE_TEXT})
                    for f in ("atr14_pct", "atr_date", "atr_error"):
                        if f in r["_trail"]: t[f] = r["_trail"][f]
                changed = True
            if changed: save(k, d)

def refresh_marks(cache):
    """2026-09-28 HT #3: after the rail cycle, re-strike ledger marks (tfo_equity_refresh.refresh(write=True)).
    Light: reuses this cycle's native quotes (no duplicate Yahoo calls); only symbols the rail did not quote
    (powder/probe holdings, mark_ticker aliases) are fetched. Valuation only: no fills, flows or entry edits.
    Never raises into the rail runner."""
    if not MARK_REFRESH_ENABLED:
        return None
    try:
        import copy, tfo_equity_refresh as ter
        fetched = []
        def fetcher(sym):
            q = cache.get(sym)
            if q: return copy.deepcopy(q)
            fetched.append(sym); return ter.fetch_quote(sym)
        rep = ter.refresh(write=True, reports=ter.REPORTS, fetcher=fetcher, extra_locks=MARK_REFRESH_LOCKS, backup_prefix=MARK_REFRESH_BACKUP_PREFIX)
        pruned = ter.prune_backups(MARK_REFRESH_BACKUP_PREFIX, MARK_REFRESH_KEEP_AUTO_BACKUPS, reports=ter.REPORTS)
        unav = sorted(s for b in rep["books"].values() for s in b.get("unavailable") or [])
        msg = "MARK-REFRESH ok %s quotes=%d reused=%d fetched=%d unavailable=%s pruned=%d" % (
            rep["checked_at"], len(rep["quotes"]), len(rep["quotes"]) - len(fetched), len(fetched), ",".join(unav) or "-", pruned)
    except Exception as e:
        msg = "MARK-REFRESH skipped (%s: %s)" % (type(e).__name__, str(e)[:200])
    print(msg)
    return msg

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--execute", action="store_true"); ap.add_argument("--only", nargs="*"); ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.execute and CORP_ACTIONS_PRECHECK:   # 2026-09-29: splits/dividends applied BEFORE any rail evaluates (never raises)
        try:
            import tfo_corporate_actions as tca; tca.precheck(reports=R)
        except Exception as e: print("CORP-ACTIONS precheck unavailable: %s" % e)
    rows = scan(set(a.only) if a.only else None)
    lines = []
    if a.execute: persist_trails(rows)
    for r in rows:
        if a.execute and r.get("status") == "READY":
            ln = execute(r)
            r["executed"] = bool(ln)
            if ln: lines.append(ln)
    if a.execute and not a.only:  # 2026-10-02 shark exit A/B: V0 shadow for V7-tagged entries (own files only; never raises)
        try:
            import shark_exit_ab; shark_exit_ab.update(rows, reports=R)
        except Exception as e: print("SHARK-EXIT-AB unavailable: %s" % e)
    qcache = {r["sym"]: r["_q"] for r in rows if r.get("_q")}
    for r in rows: r.pop("_q", None); r.pop("_trail", None); r.pop("_trail_ok", None)
    if a.execute and not a.only:  # 2026-09-28 blocked-rail watchdog (report-only; never raises)
        try:
            import rail_watchdog; rail_watchdog.update("equity", rows)
        except Exception as e: print("RAIL-WATCHDOG unavailable: %s" % e)
    # custodian 2026-09-25: per-leg rail decision evidence for the cycle receipt (decisions unchanged)
    if a.execute and not a.only:
        ev = {"utc": dt.datetime.now(UTC).isoformat(), "execute": bool(a.execute), "paper_only": True, "runner": "tfo_ticket.py", "books": list(BOOKS), "rows": rows}
        fd, tmp = tempfile.mkstemp(dir=R, prefix=".ht_equity_rails_latest", suffix=".tmp")
        with os.fdopen(fd, "w") as f: json.dump(ev, f, indent=1, default=str)
        os.replace(tmp, os.path.join(R, "ht_equity_rails_latest.json"))
    if a.execute and not a.only: refresh_marks(qcache)  # 2026-09-28 HT #3: War Room marks keep pace with the rail cycle
    if a.json: print(json.dumps(rows, indent=1, default=str))
    else:
        for r in rows:
            if r.get("rail") or r.get("status") == "NO_QUOTE":
                print("%-8s %-22s %-10s %-5s %7s%% %-8s %s" % (r.get("status"), r["book"], r["sym"], r.get("rail") or "-", r.get("ret_orig_pct"), r.get("tape", ""), r.get("why", "")))
        for r in rows:
            if r.get("half_banked") and r.get("px_usd") is not None:
                print("TRAIL    %-22s %-10s %-5s px %.6g wm %.6g level %.6g (%s) trail %.2f%% atr %s rule %s" % (r["book"], r["sym"], r["side"], r["px_usd"], r.get("trail_wm_usd") or 0,
                      r.get("rail_level_usd") or 0, r.get("exit_rule") or "hold", 100 * (r.get("trail_pct") or 0), r.get("atr14_pct"), trl.RULE_TEXT))
    print("\n".join(lines) if lines else "LOG none")

if __name__ == "__main__":
    main()
