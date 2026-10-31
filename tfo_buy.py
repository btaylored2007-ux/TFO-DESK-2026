#!/usr/bin/env python3
"""TFO Head Trader BUY tool (Brett mandate 2026-09-25). PAPER ONLY. Preview by default; --execute writes.
Usage: tfo_buy.py SYM USD --theme THEME [--label beta|edge] [--why "..."] [--execute]
Clip lands in fundamental_book (registered, engine exits gated off) tagged ht_clip; funded by paired
capital_transfer from powder (gap_close_levered_book). Rails then run via tfo_ticket.py."""
import argparse, csv, datetime as dt, json, os, sys, uuid
from zoneinfo import ZoneInfo
BASE = os.path.dirname(os.path.abspath(__file__)); R = os.path.join(BASE, "reports"); sys.path.insert(0, BASE)
import desk_lock, fundamental_book as fb
import desk_costs  # 2026-09-25: shared retail cost model; fees per fill (PAPER)
import quote_check  # 2026-09-29 HT: bid/ask consistency (both sides, bid<ask, last inside or within 0.5% of mid)
import v1_venues  # 2026-09-28 funder directive: V1 entry venues US/.T/.AX from 29 Sep (entries only)
import tfo_heat  # 2026-09-26 HT decision: heat at live stops (floored residuals 1% gap buffer)
import risk_caps  # 2026-10-04 21:30 AEDT TFO Risk audit (Head Trader adopted): planned-loss cap $5k per name at the -5% stop; entry_engine_equity theme $150k ($7.5k at the stop), Brett 2026-10-09 21:10
from paper_execution_price import eligible, receipt
from tfo_ticket import load, save, LOG, FACTS, CLOSED_NAMES, GCL, SYD, UTC, time_block, venue
BOOK = "fundamental_book"
CLIP_CAP_US = 600000.0          # Brett 2026-09-25 17:56: cap up to $600k
BASE_CLIP_US = 300000.0         # above this needs mega-liquidity (10d avg $ vol >= $2B) and spread <= 5bp
SPREAD_RETRIES, SPREAD_RETRY_SLEEP = 2, 2.0   # 2026-09-28: re-read a CROSSED Yahoo bid/ask snapshot (sides update non-atomically on .T/.HK)
HEAT_CAP_PCT = tfo_heat.HEAT_CAP_PCT  # shared 5% firm heat cap; powder still caps real clips
SHARK_EXIT_VARIANT = "V7"       # Brett 2026-10-02 17:16 AEST: paper forward test of shark exit V7 on NEW clips (SHARK_EXIT_RULE.md); None reverts new clips to the default rail
SHARK_EXIT_VARIANT_START = "2026-10-02"
SHARK_RISK_PCT = 0.005          # 2026-10-02 19:44 AEST Head Trader call (Brett delegated): NEW shark entries risk 0.5% firm equity (was 1%) to the -5% stop, all venues; crypto (tfo_crypto C3) stays 1%. Revert: 0.01
SHARK_SIZING_STOP = 0.05        # the -5% original-fill stop the risk is measured to (V7 / V0 rail stop; unchanged)
DAILY_TARGET_USD = 15000.0      # Brett 2026-09-25: $15k/Sydney day stretch; never sizes a clip
TWINS = {"SPY", "VOO", "IVV", "SPLG", "QQQ", "QQQM", "TQQQ", "SQQQ", "UPRO", "SSO", "SPXL", "ONEQ"}
CORRELATED_TWIN_GROUPS = (frozenset(("8035.T", "6857.T")), frozenset(("RCL", "GOOGL")))
SEMIS = {"NVDA","AMD","AVGO","MU","INTC","QCOM","TXN","AMAT","LRCX","KLAC","MRVL","ARM","TSM","ASML","SMH","SOXX","SOXL","SOXS","ON","NXPI","MCHP","ADI","MPWR","SMCI","TER","ENTG","QRVO","SWKS","GFS","WOLF","COHR","ALAB","CRDO"}

def shark_max_clip(eq):
    """Largest NEW shark clip: SHARK_RISK_PCT x firm equity / -5% stop (0.5% -> 10% of equity). Good-but-not-A+ = half of this (rule, not code)."""
    return SHARK_RISK_PCT * float(eq) / SHARK_SIZING_STOP

def sizing_eq():
    """2026-10-29 Brett: sleeve/clip SIZING base = firm equity ex veteran_desk (desk_caps.sizing_equity, fail closed).
    Firm risk (DD freeze, Sydney-day freeze, 5% heat cap) still uses firm_state() = full firm equity."""
    import desk_caps
    return float(desk_caps.sizing_equity()["sizing_equity_usd"])

def firm_state():
    rows = list(csv.DictReader(open(os.path.join(R, "firm_equity.csv"))))
    last = rows[-1]; eq = float(last["firm_equity_usd"]); dd = float(last["firm_drawdown_pct"])
    mid = dt.datetime.now(SYD).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    opn = None
    for r in rows:
        t = dt.datetime.fromisoformat(r["utc"].replace("Z", "+00:00"))
        if t >= mid: opn = float(r["firm_equity_usd"]); break
    return eq, dd, opn

def gates(sym, usd, theme):
    fails = []; now = dt.datetime.now(UTC); u = sym.upper()
    try:
        import book_kill
        _blocked, _why = book_kill.firm_halt_blocks_entry("tfo_buy")
        if _blocked: fails.append(_why)
    except Exception:
        pass
    _vr = v1_venues.entry_block_reason(sym, now)
    if _vr: fails.append(_vr)
    if u in CLOSED_NAMES or u.split(".")[0] in CLOSED_NAMES: fails.append("closed name")
    if u in TWINS: fails.append("SPY/QQQ twin")
    if u in SEMIS: fails.append("semis theme full (funder must name one)")
    d = load(BOOK)
    if sym in d["positions"]: fails.append("already held (no twin of a held name)")
    held_names = {str(k).upper() for k in d["positions"]}
    for pos in d["positions"].values():
        for field in ("symbol", "underlying", "ticker", "sym"):
            if isinstance(pos, dict) and pos.get(field): held_names.add(str(pos[field]).upper())
    for twins in CORRELATED_TWIN_GROUPS:
        if u in twins and (twins - {u}) & held_names:
            fails.append("correlated twin already held: %s" % sorted((twins - {u}) & held_names)[0])
            break
    clips = [p for p in d["positions"].values() if p.get("ht_clip")]
    if sum(1 for p in clips if p.get("ht_theme") == theme) >= 2: fails.append("theme already has two clips")
    for t in d.get("head_trader_tickets", []):
        if t.get("sym") == sym and (now - dt.datetime.fromisoformat(t["utc"].replace("Z", "+00:00"))).total_seconds() < 300: fails.append("round-trip inside 5 min")
    eq, dd, opn = firm_state()
    if dd <= -10: fails.append("firm DD freeze (%.2f%%)" % dd)
    if opn and (eq - opn) / opn <= -0.03: fails.append("Sydney-day -3% buy freeze")
    try: seq = sizing_eq()   # 2026-10-29: shark clip sizes off firm equity ex veteran_desk
    except Exception as _e: seq = 0.0; fails.append("sizing equity unavailable (%s)" % str(_e)[:80])
    if usd > shark_max_clip(seq) + 0.005: fails.append("shark clip over %.1f%% sizing-equity risk to the -5%% stop (max %.0f)" % (100 * SHARK_RISK_PCT, shark_max_clip(seq)))
    g = load(GCL); unreserved = float(g["cash"]) - float(g.get("reserved_usd") or 0)
    if usd > unreserved: fails.append("exceeds unreserved powder %.0f" % unreserved)
    try:   # 2026-10-28 13:40 Brett approve-all #5: theme cap ENFORCE (new buys only)
        import theme_exposure as _te; _ok, _why = _te.check_buy(sym, usd / _te.DESK_FX, ccy="USD" if venue(sym) == "US" else None, hint=theme, book="TFO main")
        if not _ok: fails.append(_why)
    except Exception as _e: fails.append("theme cap check failed (%s)" % str(_e)[:60])
    if venue(sym) == "US":   # tfo_usbook_cap_20261030 (Brett 19:35 WITA): main-book US beta cap 40% of main-book equity; new adds only
        try:
            import us_book_cap as _ubc; _uok, _uwhy, _ = _ubc.check(sym, usd, "long", book="tfo_buy", accumulate=False)
            if not _uok: fails.append(_uwhy)
        except Exception as _e: fails.append("us_book_cap check failed (%s)" % str(_e)[:60])
    if venue(sym) == "US" and usd > CLIP_CAP_US: fails.append("over $600k US clip cap")
    if venue(sym) != "US" and usd > BASE_CLIP_US: fails.append("non-US clip over $300k")
    try:
        cb = load("tfo_ht_crypto")
    except Exception: cb = {}
    heat = tfo_heat.clip_heat(d["positions"], cb)[0] + tfo_heat.PRE_HALF_PCT * usd
    if heat > HEAT_CAP_PCT * eq: fails.append("combined equity+crypto clip heat %.0f at stops would exceed %.0f%% firm (%.0f)" % (heat, 100 * HEAT_CAP_PCT, HEAT_CAP_PCT * eq))
    tb = time_block(sym, now)
    if tb: fails.append(tb)
    qs, qf = fb.fetch_prices([sym], require_executable=False); q = qs.get(sym)
    info = {}
    if not q: fails.append("no quote: %s" % qf.get(sym)); return fails, None, info
    us = venue(sym) == "US"
    tape = "LIVE" if (us and q.get("quote_current") and (q.get("age_sec") or 999) < 60) else "DELAYED"
    if q.get("session") != "regular" or not eligible(q): fails.append("not a regular-session executable print")
    if us and tape != "LIVE": fails.append("US print not under 60s")
    q["tape"] = tape
    fee_est = desk_costs.fill_fees(sym, "buy", usd / float(q["px_usd"]), float(q["px_usd"]), q.get("px"), q.get("ccy"), now)["fees_usd"]
    if usd + fee_est > unreserved: fails.append("clip + fees %.2f exceeds unreserved powder %.0f" % (fee_est, unreserved))
    try:
        import yfinance as yf
        t = yf.Ticker(sym); fi = t.fast_info; inf = t.info
        vol = float(fi["lastVolume"] or 0); avg10 = float(fi["tenDayAverageVolume"] or 0)
        bid, ask = float(inf.get("bid") or 0), float(inf.get("ask") or 0)
        px = float(q["px"])
        # 2026-10-29 infra (Iris): prefer last daily-bar close; previousClose only as sanity-checked fallback (JP/KR bogus drops)
        try:
            from veteran_desk import feeds as _FD
            _pc = _FD.prior_close(sym, yahoo_prev=fi.get("previousClose") if hasattr(fi, "get") else fi["previousClose"])
            prev = float(_pc["close"]) if _pc.get("close") else float(fi["previousClose"])
        except Exception:
            prev = float(fi["previousClose"])
        chg = px / prev - 1; dv_usd = vol * float(q["px_usd"])
        # 2026-09-29 23:59 AEST HT fix: bid/ask accepted only if both > 0, bid < ask and last trade (desk quote px) inside the
        # quote or within 0.5% of mid (quote_check). Any other snapshot is re-read SPREAD_RETRIES times, then fails closed
        # `quote_inconsistent` (supersedes the 2026-09-28 crossed-only re-read). Locked + last within 0.5% = 0.0bp pass (HT 2026-09-30).
        # 2026-09-30 HT: US names read Nasdaq real-time top-of-book first (quote_check.nasdaq_quote, fresh only); Yahoo .info
        # is the fallback when Nasdaq is unavailable/stale and the only source for non-US. Same consistency check either way.
        def _read(first=False):
            pq = quote_check.nasdaq_quote(sym) if us else None
            if pq: return pq["bid"], pq["ask"], "nasdaq"
            i2 = inf if first else yf.Ticker(sym).info
            return float(i2.get("bid") or 0), float(i2.get("ask") or 0), "yahoo"
        bid, ask, qsrc = _read(first=True)
        ok, qreason, spread_bp = quote_check.check(bid, ask, px)
        for _ in range(SPREAD_RETRIES):
            if ok: break
            import time; time.sleep(SPREAD_RETRY_SLEEP)
            bid, ask, qsrc = _read()
            ok, qreason, spread_bp = quote_check.check(bid, ask, px)
        locked = bool(ok and bid and ask and float(bid) == float(ask))  # 2026-09-30 HT: locked passes at 0bp but never qualifies the >$300k tier
        if not ok: spread_bp = None
        info = {"fees_est_usd": round(fee_est, 2), "session_chg_pct": round(100 * chg, 2), "day_dollar_vol": round(dv_usd), "vol_vs_10d": round(vol / avg10, 2) if avg10 else None, "spread_bp": round(spread_bp, 1) if spread_bp is not None else None, "quote_source": qsrc, "bid": bid, "ask": ask, **({"spread_locked": True} if locked else {})}
        if chg < -0.03: fails.append("down >3% on session")
        if chg > 0.05 and not (avg10 and vol >= 2 * avg10): fails.append("up >5% without 2x 10-day volume")
        if not ok: fails.append("%s (after %d re-reads)" % (qreason, SPREAD_RETRIES)); info["quote_inconsistent"] = True
        elif spread_bp >= 20: fails.append("spread %.1fbp >= 20bp" % spread_bp)
        avg10_dv = avg10 * float(q["px_usd"]); info["avg10_dollar_vol"] = round(avg10_dv)
        if usd > BASE_CLIP_US and not (avg10_dv >= 2e9 and spread_bp is not None and not locked and spread_bp <= 5): fails.append("clip >$300k needs 10d avg $vol >= $2B and spread <= 5bp")
        if usd > 0.05 * dv_usd: fails.append("size > 5%% of day dollar volume (%.0f)" % dv_usd)
    except Exception as e:
        fails.append("gate data unavailable: %s" % e)
    return fails, q, info

def risk_size(usd, theme=None):
    """2026-10-04 21:30 AEDT TFO Risk audit (Head Trader adopted): clip <= $5k / (-5% shark stop + 0.5% slippage) = ~$90.9k.
    Sized from the actual rail stop (SHARK_SIZING_STOP), not ATR. Never sizes up.
    Brett 2026-10-09 21:10 AEDT: theme entry_engine_equity (auto_fire) -> min($150k, $7,500 at the -5% stop) = $150k
    (risk_caps.theme_params). Every other theme keeps the $5k cap."""
    return risk_caps.size_entry(usd, SHARK_SIZING_STOP, **risk_caps.theme_params(theme))

def _cap_log_path():
    return risk_caps.LOG_PATH if os.path.abspath(R) == os.path.abspath(os.path.join(BASE, "reports")) else os.path.join(R, "risk_cap_log.jsonl")

def execute(sym, usd, theme, label, why, q, risk_cap=None):
    try:
        import book_kill
        _blocked, _why = book_kill.firm_halt_blocks_entry("tfo_buy")
        if _blocked: raise SystemExit("refused: " + _why)
    except SystemExit:
        raise
    except Exception:
        pass
    now = dt.datetime.now(UTC); utc = now.strftime("%Y-%m-%dT%H:%M:%SZ"); tid = "HT-" + uuid.uuid4().hex[:10]
    v1_venues.require_entry_allowed(sym, now)  # hard stop even if a caller skipped gates()
    _cap = risk_caps.planned_loss_cap_usd(SHARK_SIZING_STOP, **risk_caps.theme_params(theme))  # Brett 2026-10-09: entry-engine theme $150k
    if usd > _cap + 0.5: raise SystemExit("refused: clip $%.0f over the $%.0f planned-loss cap (TFO Risk audit 4 Oct 2026%s); size via main()/risk_size" % (usd, _cap, "; entry engine Brett 2026-10-09" if risk_caps.is_entry_engine(theme) else ""))
    px_usd = float(q["px_usd"]); units = usd / px_usd
    # desk_costs 2026-09-25: entry fees are funded from powder on top of the clip (clip notional unchanged);
    # book seed rises by clip+fees and costs_paid_usd by fees, so the book's equity identity still holds.
    fee = desk_costs.fill_fees(sym, "buy", units, px_usd, q.get("px"), q.get("ccy"), now); fees = round(fee["fees_usd"], 2)
    with desk_lock.ledger_lock("fundamental"), desk_lock.ledger_lock("tfo_gcl_powder"):
        d = load(BOOK); g = load(GCL)
        ev = {"type": "capital_transfer", "utc": utc, "from": GCL, "to": BOOK, "tag": "HT_BUY_%s" % sym, "paper_only": True,
              "note": "Head Trader BUY %s clip funded from powder; paired; firm unchanged. PAPER" % sym}
        fund = round(usd + fees, 2)
        before = float(g["cash"]); g["cash"] = round(before - fund, 2)
        g.setdefault("capital_events", []).append({**ev, "usd": -fund, "cash_before": before, "cash_after": g["cash"]})
        g["seed_usd"] = round(float(g["seed_usd"]) - fund, 2); g["start"] = round(float(g["start"]) - fund, 2); g["equity"] = g["cash"]; g["updated"] = utc
        d.setdefault("capital_events", []).append({**ev, "usd": fund})
        d["seed_usd"] = round(float(d["seed_usd"]) + fund, 2)
        d["costs_paid_usd"] = round(float(d.get("costs_paid_usd") or 0) + fees, 4)
        tk = {"ticket_id": tid, "utc": utc, "action": "BUY", "sym": sym, "side": "long", "qty": units, "px_usd": px_usd, "px_native": q.get("px"),
              "ccy": q.get("ccy"), "tape": q["tape"], "principal_usd": usd, "by": "TFO Head Trader", "theme": theme, "label": label, "why": why, "paper_only": True, **receipt(q),
              "fees_usd": fees, "fee_detail": fee["breakdown"], "fee_schedule": fee["schedule"]}
        d["positions"][sym] = {"side": "long", "notional_usd": usd, "entry_px": q.get("px"), "entry_ccy": q.get("ccy"), "entry_fx_usd": q.get("fx_usd"),
            "entry_px_usd": px_usd, "units": units, "entry_utc": utc, "mark": q.get("px"), "mark_usd": px_usd, "mark_utc": q.get("quote_utc"),
            "mark_stale": False, "unrealised_usd": 0.0, "sector": theme, "ht_clip": True, "ht_theme": theme, "ht_label": label, "ht_why": why,
            "rail_orig_entry_usd": px_usd, "rail_last_ticket_utc": utc, "entry_ticket": tk, "entry_fees_usd": fees,
            **({"exit_variant": SHARK_EXIT_VARIANT, "exit_variant_start": SHARK_EXIT_VARIANT_START} if SHARK_EXIT_VARIANT else {})}
        if SHARK_EXIT_VARIANT: tk["exit_variant"] = SHARK_EXIT_VARIANT
        if risk_cap: tk["risk_cap"] = {**{k: risk_cap.get(k) for k in ("rule", "usd_requested", "usd", "bound", "binds", "stop_dist", "slip_allowance", "planned_loss_usd", "planned_loss_cap_usd")}, **{k: risk_cap[k] for k in ("planned_loss_at_stop_usd", "target_usd", "loss_basis") if k in risk_cap}}
        d.setdefault("head_trader_tickets", []).append(tk)
        if d.get("equity") is not None: d["equity"] = round(float(d["equity"]) + usd, 2)
        save(BOOK, d); save(GCL, g)
    syd = now.astimezone(SYD).strftime("%Y-%m-%d %H:%M")
    line = "LOG %s BUY %s %.6g %.6g %s %s %.2f %.2f %.2f %.2f" % (syd, sym, units, px_usd, q["tape"], BOOK, usd, 0, 0, fees)
    open(LOG, "a").write(line + "\n")
    open(FACTS, "a").write(json.dumps({"syd": syd, "fact": "BUY %s $%.0f %s (%s) theme %s: %s; fees %.2f" % (sym, usd, q["tape"], label, theme, why, fees), "ticket": tid, "fees_usd": fees}) + "\n")
    try:  # 2026-10-02 shark slippage LOGGING ONLY (shark_slippage.py): live bid/ask next to the paper fill; after the save; never raises
        import shark_slippage; shark_slippage.record("BUY", BOOK, sym, "long", units, q, {**tk, "ht_clip": True}, ledger_log=LOG)
    except Exception: pass
    return line

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("sym"); ap.add_argument("usd", type=float); ap.add_argument("--theme", required=True)
    ap.add_argument("--label", default="beta", choices=["beta", "edge"]); ap.add_argument("--why", default=""); ap.add_argument("--execute", action="store_true")
    a = ap.parse_args()
    rk = risk_size(a.usd, a.theme); usd = rk["usd"] if rk["bound"] else a.usd   # 2026-10-04 TFO Risk audit: planned-loss cap (entry-engine theme: Brett 2026-10-09 $150k)
    fails, q, info = gates(a.sym, usd, a.theme)
    print(json.dumps({"sym": a.sym, "usd": usd, "usd_requested": a.usd, "theme": a.theme, "px_usd": q and q.get("px_usd"), "tape": q and q.get("tape"), **info, "risk_cap": rk, "gate_fails": fails}, default=str))
    print(risk_caps.describe(rk))
    if a.execute: risk_caps.log_bind(rk, "tfo_buy", a.sym, executed=not fails, path=_cap_log_path(), extra={"theme": a.theme, "why": (a.why or "")[:200]})
    if fails: print("LOG none"); return
    print(execute(a.sym, usd, a.theme, a.label, a.why, q, risk_cap=rk) if a.execute else "PREVIEW PASS (not executed)")

if __name__ == "__main__":
    main()
