#!/usr/bin/env python3
"""value_deploy.py — deploy screened LONG candidates into the Fundamental Desk.

PAPER ONLY. Opens LONGS ONLY. Never opens a short (CM-051: the short book is a
cost, not the edge). Never touches an existing position.

SIZING, and why it differs from the book's original $40,000 long unit
---------------------------------------------------------------------
The freed capital came from CLOSING six shorts, not from new funding. Putting
$95k into two $40k names would raise single-name risk well above anything else in
this book. These are additions to an UNVALIDATED screen, so the correct response
to "more capital, same uncertain signal" is MORE NAMES, not BIGGER NAMES.
Unit is therefore $16,000, and no more than one name per sector.

Every entry is gated by:
  * the firm-wide halt (book_kill.firm_entries_allowed) — CM-047
  * this book's own kill (book_risk.frozen)
  * a live price, in USD, or the name does not open
2026-10-28 14:10 WITA Brett "Let it trade indefinitely" (fundamental auto-trade, HT build):
  * size per name = 6.5% of book equity (band 6-7%), max 15 OPEN positions, max 3 per sector (holdings count)
  * cash reserve 5% of book equity; only cash above it is deployed
  * staged amounts RESCALE across names that survive the gates (each capped at 7% equity and the liquidity cap)
  * theme-cap check (theme_exposure.check_buy, holdings basis) on every buy, cumulative within the run
  * liquidity: position <= 20% of 20-day average daily traded value; cost = 10bp commission + half-spread
    tier by ADV + square-root impact (sigma x sqrt(participation)) — honest small-cap slippage
  * fills ONLY at a current regular-session executable quote (fundamental_book.fetch_prices, executable True);
    NO yfinance last-price fallback, never backdated (entry_utc = the quote's own time)
  * 15% stop below entry recorded on every buy (fundamental_exits.LONG_STOP_ADVERSE enforces it)
  * idempotent: held names skipped, ledger lock, nothing bought unless cash > 5% reserve + 1% minimum clip
"""
import json, math, os, sys, time, warnings
warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(BASE, "reports", "fundamental_book.json")
RANKED = os.path.join(BASE, "reports", "value_rank.json")
RUNLOG = os.path.join(BASE, "logs", "value_deploy_auto.jsonl")
SLOT_PCT = 0.065            # 6-7% of book equity per name (Brett 14:11)
SLOT_MAX_PCT = 0.07
MIN_CLIP_PCT = 0.01
CASH_RESERVE_PCT = 0.05     # Brett 14:10 update: 5% cash
MAX_OPEN = 15               # Brett 14:11
MAX_PER_SECTOR = 3
SECTOR_CAPS = {"Basic Materials": 2}   # Brett 28 Oct 14:30 WITA: max 2 Basic Materials names incl. holdings


def sector_cap(sec):
    return SECTOR_CAPS.get(sec, MAX_PER_SECTOR)
MAX_PCT_ADV = 0.20          # Brett 14:12
COMMISSION_BPS = 10.0
STOP_PCT = 0.15
ADV_HIGH_FLAG = 0.05
MAX_SCAN = 120


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def price_ccy_and_fx(sym, rates=None):
    """(quote currency, USD per quoted unit). Raises fx.FXUnavailable — never assumes USD for a suffixed listing."""
    import fx as _fx
    ccy = _fx.quote_currency(sym)
    if not ccy:
        raise _fx.FXUnavailable("unknown quote currency for %s" % sym)
    if rates is None:
        rates, _ = _fx.fetch_usd_rates([ccy])
    return ccy, _fx.usd_per_quote_unit(ccy, rates)


def _atomic_dump(obj, path):
    tmp = path + ".tmp-value_deploy"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


def main(dry=True, preview=False):
    if dry or preview:
        return _main(True, preview)
    import desk_lock   # 2026-10-28 review fix: live write held no ledger lock while fundamental_book.py ticks
    with desk_lock.ledger_lock("fundamental"):
        return _main(dry)


# Brett 28 Oct 14:30 WITA: delayed (Yahoo ~15-20 min) current-regular-session quotes are fillable PAPER for these
# venues. The delay is LOGGED on every fill and charged as adverse slippage: sigma_daily * sqrt(delay / 6.5h session).
DELAYED_OK_SUFFIXES = (".AX", ".T", ".L")   # .T/.L added with the Fundamental JP/LSE venue override (Yahoo-delayed too)
SESSION_SEC = 23400.0
PREVIEW_ASSUMED_DELAY_S = 1200.0   # Yahoo ASX/TSE/LSE ~20 min


def fillable(sym, q):
    """(ok, delay_s, price_class). Live executable always; delayed PAPER only for DELAYED_OK_SUFFIXES."""
    if not q or not q.get("px_usd"): return False, None, None
    if q.get("_preview"):   # what-if on closing prints: assume the typical Yahoo delay, not the overnight age
        d = PREVIEW_ASSUMED_DELAY_S if str(sym).upper().endswith(DELAYED_OK_SUFFIXES) else 0.0
        return True, d, "PREVIEW (assumed delay %ds)" % d if d else "PREVIEW"
    if q.get("executable") is True: return True, float(q.get("age_sec") or 0.0), "CURRENT"
    if q.get("paper_executable") is True and str(sym).upper().endswith(DELAYED_OK_SUFFIXES):
        return True, float(q.get("age_sec") or 0.0), q.get("paper_price_class") or "DELAYED PAPER"
    return False, None, None


def delay_slip_bps(sigma_daily, delay_s):
    """Honest adverse slippage for a stale print: one-sigma move over the delay (never negative)."""
    d = max(0.0, float(delay_s or 0.0)) if delay_s and delay_s > 60 else 0.0
    return 1e4 * float(sigma_daily or 0.03) * math.sqrt(d / SESSION_SEC)


def spread_bps(adv_usd):
    """Half-spread tier by 20d ADV (US$). Small caps pay more."""
    if adv_usd >= 50e6: return 3.0
    if adv_usd >= 10e6: return 8.0
    if adv_usd >= 2e6: return 20.0
    if adv_usd >= 500e3: return 40.0
    return 75.0


def cost_bps(adv_usd, sigma_daily, notional_usd):
    """commission + half-spread + square-root impact (Y=1): sigma * sqrt(participation)."""
    part = notional_usd / adv_usd if adv_usd else 1.0
    impact = 1e4 * float(sigma_daily or 0.03) * math.sqrt(max(part, 0.0))
    return COMMISSION_BPS + spread_bps(adv_usd) + impact, part


def liquidity(sym, usd_per_unit):
    """(adv20_usd, sigma_daily) from 20 daily bars — volume data only, NEVER used as a fill price."""
    import yfinance as yf
    h = yf.Ticker(sym).history(period="2mo", auto_adjust=False).tail(21)
    if len(h) < 10:
        raise ValueError("fewer than 10 daily bars")
    tv = (h["Close"] * h["Volume"]).tail(20)
    r = h["Close"].pct_change().dropna().tail(20)
    return float(tv.mean()) * usd_per_unit, float(r.std() or 0.03)


def plan(led, ranked, quotes, liq, theme_check, slot_usd, deploy_usd, sector_of_held, max_new, venue_block=None, entry_guard=None):
    """Pure planner (tested). Returns (orders, skipped)."""
    held = set(led["positions"])
    sectors = dict(sector_of_held)
    orders, skipped = [], []
    for r in ranked:
        if len(orders) >= max_new:
            break
        s = r["symbol"]
        if s in held: continue
        ginfo = {}
        if entry_guard:   # HT 30 Oct: re-entry after stop-out + earnings proximity (new entries only)
            why, ginfo = entry_guard(s, r)
            if why: skipped.append((s, why)); continue
        sec = r.get("sector") or "unknown"
        if sectors.get(sec, 0) >= sector_cap(sec):
            skipped.append((s, "sector %s already has %d" % (sec, sector_cap(sec)))); continue
        vb = venue_block(s) if venue_block else None
        if vb: skipped.append((s, "venue: " + vb[:80])); continue
        q = quotes.get(s)
        f_ok, delay_s, pclass = fillable(s, q)
        if not f_ok:
            skipped.append((s, "no current regular-session executable quote (market closed or delayed)")); continue
        L = liq.get(s)
        if not L: skipped.append((s, "no 20d ADV")); continue
        adv, sig = L
        cap_liq = MAX_PCT_ADV * adv
        if cap_liq < MIN_CLIP_PCT * slot_usd / SLOT_PCT:
            skipped.append((s, "20%% of ADV US$%.0f below minimum clip" % cap_liq)); continue
        ok, why = theme_check(s, min(slot_usd, cap_liq), q, sec)
        if not ok: skipped.append((s, why)); continue
        orders.append({"sym": s, "row": r, "q": q, "adv": adv, "sigma": sig, "cap_liq": cap_liq, "sector": sec,
                       "delay_s": round(delay_s or 0.0, 1), "price_class": pclass, "guard_info": ginfo})
        sectors[sec] = sectors.get(sec, 0) + 1
    # rescale: spread deployable cash over survivors, each <= 7% equity and <= 20% ADV
    if orders:
        eq = slot_usd / SLOT_PCT
        left, todo = deploy_usd, list(orders)
        while todo:
            each = left / len(todo)
            capped = [o for o in todo if min(SLOT_MAX_PCT * eq, o["cap_liq"]) < each]
            if not capped:
                for o in todo: o["usd"] = each
                break
            for o in capped:
                o["usd"] = min(SLOT_MAX_PCT * eq, o["cap_liq"]); left -= o["usd"]; todo.remove(o)
        for o in orders:
            o["usd"] = round(min(o["usd"], SLOT_MAX_PCT * eq, o["cap_liq"]), 2)
            o["cost_bps"], o["pct_adv"] = cost_bps(o["adv"], o["sigma"], o["usd"])
            o["delay_slip_bps"] = round(delay_slip_bps(o["sigma"], o.get("delay_s")), 1)
            o["cost_bps"] += o["delay_slip_bps"]
        orders = [o for o in orders if o["usd"] >= MIN_CLIP_PCT * eq]
    return orders, skipped


def _main(dry=True, preview=False):
    """preview=True: market-closed WHAT-IF using last/closing quotes — NEVER writes (dry forced)."""
    if preview: dry = True
    led = json.load(open(LEDGER))
    _rank_doc = json.load(open(RANKED)); ranked = _rank_doc["ranked"]
    rank_pos = {r["symbol"]: i + 1 for i, r in enumerate(ranked)}

    # --- gates ---------------------------------------------------------------
    br = led.get("book_risk") or {}
    if br.get("frozen"):
        print("REFUSED: this book is FROZEN (%s) — exits only." % br.get("frozen_reason"))
        return
    try:
        import book_kill as _kill
        ok, why = _kill.firm_entries_allowed("TFO Fundamental Desk", verbose=False)
        if not ok:
            print("REFUSED: %s" % why)
            return
    except Exception as e:   # 2026-10-28 review fix: fail CLOSED (was fail-open "entries NOT blocked")
        print("REFUSED: firm halt gate unavailable (%s) — entries blocked" % type(e).__name__)
        return

    equity = float(led.get("equity") or 0); cash = float(led.get("cash") or 0)
    reserve = CASH_RESERVE_PCT * equity; slot = SLOT_PCT * equity
    deployable = cash - reserve
    n_open = sum(1 for p in led["positions"].values() if float(p.get("units") or 0) > 0)
    max_new = max(0, min(MAX_OPEN - n_open, int(round(deployable / slot)) if slot else 0))   # names sized ~6.5%, never below slot/2
    print("equity US$%s cash US$%s reserve 5%% US$%s deployable US$%s slot 6.5%% US$%s open %d/%d"
          % (tuple("{:,.0f}".format(x) for x in (equity, cash, reserve, max(deployable, 0), slot)) + (n_open, MAX_OPEN)))
    if deployable < MIN_CLIP_PCT * equity or max_new == 0:
        print("nothing to deploy (idempotent no-op)")
        return {"orders": [], "skipped": []}

    rank_by = {r["symbol"]: r for r in ranked}
    sector_of_held = {}
    for s, p in led["positions"].items():
        if float(p.get("units") or 0) <= 0: continue
        sec = (rank_by.get(s) or {}).get("sector") or p.get("sector") or (p.get("screen") or {}).get("sector") or "unknown"
        sector_of_held[sec] = sector_of_held.get(sec, 0) + 1

    import v1_venues, fundamental_book as fb, theme_exposure as te
    cands = [r for r in ranked if r["symbol"] not in led["positions"] and not v1_venues.fundamental_entry_block_reason(r["symbol"])][:MAX_SCAN]
    quotes, failed = fb.fetch_prices([r["symbol"] for r in cands], require_executable=not preview)
    if preview:   # what-if only: mark last quotes as plannable; dry is forced so nothing can fill
        quotes = {k: dict(v, executable=True, _preview=True, _preview_px_class=v.get("paper_price_class")) for k, v in quotes.items()}
        print("PREVIEW (market-closed what-if at last quotes; NOT fillable, nothing written)")
    liq = {}
    for r in cands:
        q = quotes.get(r["symbol"])
        if fillable(r["symbol"], q)[0]:
            try: liq[r["symbol"]] = liquidity(r["symbol"], float(q["fx_usd"]))
            except Exception: pass
    cfg = te.load_cfg(); d = te.build(cfg=cfg); fxa = te.fx_audusd(cfg)
    T = {t["theme"]: t for t in d["themes"]}
    def theme_check(sym, usd, q, sec):
        ok, why = te.check_buy(sym, usd / fxa, ccy=q.get("ccy"), hint=sec, d=d, cfg=cfg, book="Fundamental auto")
        if ok:   # cumulative within this run
            th, _u, _l, loss = te.stress_one(te.pos("Fundamental auto", sym, usd / fxa, ccy=q.get("ccy"), hint=sec), cfg)
            T[th]["current_stress_loss_aud"] += loss
        return ok, why
    import value_entry_guards as veg
    _stops = veg.stop_outs(led)
    _earn = veg.earnings_map([r["symbol"] for r in cands], led=led)
    if _earn.get("_error"): print("  earnings fetch failed (%s) — affected names allowed as earnings_unknown" % _earn["_error"])
    def entry_guard(sym, row):
        why, info = veg.reentry_block(sym, row, _stops)
        if why: return why, {}
        why2, info2 = veg.earnings_block(sym, _earn.get(sym))
        info.update(info2)
        return why2, info
    orders, skipped = plan(led, cands, quotes, liq, theme_check, slot, deployable / (1 + COMMISSION_BPS / 1e4), sector_of_held, max_new,
                           entry_guard=entry_guard)

    print("%-9s %-28s %-20s %10s %7s %6s %7s %7s" % ("SYM", "NAME", "SECTOR", "USD", "%ADV", "COSTbp", "PX", "STOP"))
    spend = 0.0
    for o in orders:
        px_usd = float(o["q"]["px_usd"]); px_local = float(o["q"]["px"])
        o["cost_usd"] = o["usd"] * o["cost_bps"] / 1e4
        o["fill_usd"] = px_usd * (1 + (o["cost_bps"] - COMMISSION_BPS) / 1e4)   # spread+impact in the fill price
        o["units"] = o["usd"] / o["fill_usd"]; o["stop_px"] = px_local * (1 - STOP_PCT)
        spend += o["usd"] + o["usd"] * COMMISSION_BPS / 1e4
        print("%-9s %-28s %-20s %10s %6.2f%% %6.0f %7.2f %7.2f" % (o["sym"], (o["row"].get("name") or "")[:28], o["sector"][:20],
              "{:,.0f}".format(o["usd"]), 100 * o["pct_adv"], o["cost_bps"], px_local, o["stop_px"]))
    for s, why in skipped[:25]:
        print("  skip %-9s %s" % (s, why))
    print("  %d buys US$%s incl. commission; cash US$%s -> US$%s (reserve US$%s)" % (len(orders), "{:,.0f}".format(spend),
          "{:,.0f}".format(cash), "{:,.0f}".format(cash - spend), "{:,.0f}".format(reserve)))
    hi = [o["sym"] for o in orders if o["pct_adv"] > ADV_HIGH_FLAG]
    try:
        import datetime as _dt
        _st = {"when": _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8))).strftime("%d %b %H:%M WITA"),
               "mode": "preview" if preview else ("dry" if dry else "LIVE PAPER"), "buys": [[o["sym"], o["usd"]] for o in orders],
               "note": "%d skipped; reserve US$%s" % (len(skipped), "{:,.0f}".format(reserve))}
        _atomic_dump(_st, os.path.join(BASE, "static", "fundamental_autodeploy.json"))
    except Exception: pass
    if hi: print("  HIGH PARTICIPATION (>5%% of 20d ADV, up to the 20%% cap): %s" % ", ".join(hi))
    if not orders or cash - spend < reserve - 1:
        if orders: print("  REFUSED: would breach the 5% cash reserve.")
        return {"orders": orders, "skipped": skipped}
    if dry:
        print("  DRY RUN — nothing written. Re-run with --live to deploy (PAPER).")
        return {"orders": orders, "skipped": skipped}

    from paper_execution_price import receipt
    for o in orders:
        q = o["q"]; fxu = float(q["fx_usd"]); fill_local = o["fill_usd"] / fxu
        comm = o["usd"] * COMMISSION_BPS / 1e4
        led["positions"][o["sym"]] = {
            "side": "long", "notional_usd": o["usd"],
            "entry_px": fill_local, "entry_px_usd": o["fill_usd"], "entry_ccy": q.get("ccy"),
            "entry_fx_usd": fxu, "units": o["units"], "entry_utc": _utc(), "entry_quote_utc": q.get("quote_utc"),
            "entry_quote_delay_s": o.get("delay_s"), "entry_price_class": o.get("price_class"),
            "entry_delay_slip_bps": o.get("delay_slip_bps"),
            "entry_price_evidence": receipt(q), "entry_quote_px": float(q["px"]),
            "entry_cost_bps": round(o["cost_bps"], 1), "entry_pct_adv20": round(o["pct_adv"], 4), "adv20_usd": round(o["adv"]),
            "entry_fees_usd": round(comm, 2),
            "stop_pct": STOP_PCT, "stop_px": round(fill_local * (1 - STOP_PCT), 6), "stop_px_usd": round(o["fill_usd"] * (1 - STOP_PCT), 6),
            "mark": float(q["px"]), "mark_usd": float(q["px_usd"]), "mark_fx_usd": fxu,
            "mark_utc": q.get("quote_utc"), "mark_stale": False, "unrealised_usd": 0.0,
            "sector": o["sector"],
            "source": "value_deploy AUTO (Brett 28 Oct 14:10 'let it trade indefinitely'); value_rank screen, NO validated edge",
            "screen": {k: o["row"].get(k) for k in ("pb", "pe", "ev_ebitda", "roe", "de", "dy", "sector", "score", "value_score", "quality_score", "mcap_usd")},
            # HT 30 Oct (e): provenance on every entry ticket
            "entry_rank": rank_pos.get(o["sym"]), "entry_score": o["row"].get("score"),
            "value_rank_generated_utc": _rank_doc.get("generated_utc"),
            "entry_quote_source": q.get("source", "Yahoo public chart"), "entry_fund_source": o["row"].get("fund_source") or "Yahoo (value_hunt baseline)",
            **(o.get("guard_info") or {}),
        }
        led["cash"] = round(led["cash"] - o["usd"] - comm, 2)
        led["costs_paid_usd"] = round(led.get("costs_paid_usd", 0.0) + comm + o["usd"] * (o["cost_bps"] - COMMISSION_BPS) / 1e4, 4)
    led.setdefault("deployments", []).append(
        {"utc": _utc(), "n": len(orders), "slot_pct": SLOT_PCT, "reserve_pct": CASH_RESERVE_PCT,
         "symbols": [o["sym"] for o in orders], "usd": [o["usd"] for o in orders],
         "source": "value_deploy auto", "note": "Brett 28 Oct 14:10 fundamental auto-trade; PAPER"})
    _atomic_dump(led, LEDGER)
    os.makedirs(os.path.dirname(RUNLOG), exist_ok=True)
    with open(RUNLOG, "a") as f:
        f.write(json.dumps({"utc": _utc(), "bought": [[o["sym"], o["usd"], round(o["cost_bps"], 1), o.get("delay_s"), o.get("price_class")] for o in orders]}) + "\n")
    print("  DEPLOYED %d longs (PAPER). cash now US$%s" % (len(orders), "{:,.2f}".format(led["cash"])))
    return {"orders": orders, "skipped": skipped}


if __name__ == "__main__":
    main(dry="--live" not in sys.argv, preview="--preview" in sys.argv)
