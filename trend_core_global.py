#!/usr/bin/env python3
"""TREND-CORE GLOBAL widening + SPEC sleeve (PAPER ONLY). Brett 28 Oct 2026 09:40/09:41 WITA (pt1575u).

Same law as TREND-CORE/Trend18 R2: trend = 0.5*(px>SMA200) + 0.5*(12m excess>0); inv-vol (60d); scaled to 20% vol;
TMF duration gate unchanged (trend18 ^TNX rule); monthly rebalance (first US session); -30% NAV/unit kill (trend_core.restrike).
Budgets (fractions of book NAV): CORE+GLOBAL notional <= 0.85, SPEC notional <= 0.10, A$50k spare (5%) never deployed;
idle inside CORE budget -> MF basket DBMF/KMLM/CTA thirds.
SPEC: higher-beta legs (ETH, SOL via Kraken; ERX 2x energy, EDC 3x EM) under its own 20% vol risk-parity, <=0.10 NAV,
own stop: leg exits at next rebalance/session if -25% from entry; sleeve halt if spec sleeve -30% (flag, no new spec until review).
No pump-chasing: leg skipped if signal 1m return > +30%.
Execution (AMENDED Brett 28 Oct 2026 10:14 WITA, pt1580u "trade straight away"): crypto legs (XBT/ETH/SOL) fill NOW at live
Kraken quotes (`crypto-now`, tape LIVE/RECENT only); ETF legs + MF->0 execute at TONIGHT's US open 28 Oct ET (>=09:45 ET ~21:46 WITA)
via `rebalance-if-due`, ONLY after the one-shot rebook (state/trend_core_rebook_done.json) has run -> rebook first, then reweight.
ETF prices = today's-session 1m bars <=10 min old (fail closed, retry next fire). Weights recomputed from latest completed close
at each execution. Never backdates. Commands: plan | crypto-now [--dry] | rebalance-if-due [--force-dry] | status
"""
from __future__ import annotations
import argparse, datetime as dt, json, math, os, sys
try:
    import desk_doh as _doh; _doh.install()
except Exception: pass
BASE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, BASE)
import trend18, trend_core as tc  # noqa: E402
PLAN = os.path.join(BASE, "state", "trend_core_global_plan.json")
STATUS = os.path.join(BASE, "static", "trend_core_global_status.json")
CORE_BUDGET, SPEC_BUDGET, SIGMA = 0.85, 0.10, 0.20
SPEC_LEG_STOP, SPEC_SLEEVE_HALT = -0.25, -0.30
DUE = "2026-10-28"; CARD = "Executes 28 Oct US open"
REBOOK_DONE = os.path.join(BASE, "state", "trend_core_rebook_done.json")
#            name,      signal,     inst,     lev, kind,    group
LEGS = [("SPX", "^SP500TR", "UPRO", 3, "etf", "core"), ("NDX", "QQQ", "TQQQ", 3, "etf", "core"),
        ("BOND_L", "VUSTX", "TMF", 3, "etf", "core"), ("GOLD", "GLD", "UGL", 2, "etf", "core"),
        ("BTC", "BTC-USD", "XBT/USD", 1, "crypto", "core"),
        ("JAPAN", "EWJ", "EWJ", 1, "etf", "global"), ("AUSTRALIA", "EWA", "EWA", 1, "etf", "global"),
        ("UK", "EWU", "EWU", 1, "etf", "global"), ("KOREA", "EWY", "EWY", 1, "etf", "global"),
        ("TAIWAN", "EWT", "EWT", 1, "etf", "global"), ("CHINA_HK", "MCHI", "MCHI", 1, "etf", "global"),
        ("EM", "EEM", "EEM", 1, "etf", "global"), ("EAFE", "EFA", "EFA", 1, "etf", "global"),
        ("ENERGY", "XLE", "XLE", 1, "etf", "global"), ("COMMOD", "DBC", "DBC", 1, "etf", "global"),
        ("S_ETH", "ETH-USD", "ETH/USD", 1, "crypto", "spec"), ("S_SOL", "SOL-USD", "SOL/USD", 1, "crypto", "spec"),
        ("S_ENERGY2X", "XLE", "ERX", 2, "etf", "spec"), ("S_EM3X", "EEM", "EDC", 3, "etf", "spec")]

def signals():
    import pandas as pd, yfinance as yf
    tick = sorted({l[1] for l in LEGS} | {"^IRX", "SPY", "TLT", "QQQ"})
    raw = yf.download(tick, period="3y", interval="1d", auto_adjust=True, progress=False, threads=True)["Close"]
    raw.index = pd.to_datetime(raw.index).tz_localize(None).normalize()
    idx = raw["QQQ"].dropna().index
    spx = raw["^SP500TR"].dropna(); spy = raw["SPY"].dropna()
    if spx.index[-1] < idx[-1]:
        ext = spy[spy.index > spx.index[-1]]; spx = pd.concat([spx, spx.iloc[-1] * ext / spy[spy.index <= spx.index[-1]].iloc[-1]])
    bond = raw["VUSTX"].dropna(); tlt = raw["TLT"].dropna()
    if bond.index[-1] < idx[-1]:
        ext = tlt[tlt.index > bond.index[-1]]; bond = pd.concat([bond, bond.iloc[-1] * ext / tlt[tlt.index <= bond.index[-1]].iloc[-1]])
    ser = {}
    for n, sg, *_ in LEGS:
        s = spx if sg == "^SP500TR" else bond if sg == "VUSTX" else raw[sg].dropna()
        ser[n] = s.reindex(s.index.union(idx)).ffill().reindex(idx)
    P = pd.DataFrame(ser); R = P.pct_change()
    irx = raw["^IRX"].dropna() / 100.0
    rf = ((1 + irx) ** (1 / 252) - 1).reindex(irx.index.union(idx)).ffill().reindex(idx).fillna(0); crf = (1 + rf).cumprod()
    d = idx[-1]
    # signal asof = last completed US close (monthly law uses prior month-end at execution; recomputed then)
    sma = P.rolling(200).mean().loc[d]; xm = P.loc[d] / P.shift(252).loc[d] - crf.loc[d] / crf.shift(252).loc[d]
    tr = (0.5 * (P.loc[d] > sma).astype(float) + 0.5 * (xm > 0).astype(float)).where(sma.notna(), 0.0)
    r1m = P.loc[d] / P.shift(21).loc[d] - 1
    vol = R.iloc[-60:].std() * math.sqrt(252)
    gate = trend18.tnx_gate(); gate_open = bool(gate.get("open", True)) if "error" not in gate else True
    b = tr / vol
    if not gate_open: b["BOND_L"] = 0.0
    lev = {l[0]: l[3] for l in LEGS}; grp = {l[0]: l[5] for l in LEGS}
    w = {}
    for g, budget in (("core+global", CORE_BUDGET), ("spec", SPEC_BUDGET)):
        names = [l[0] for l in LEGS if (grp[l[0]] == "spec") == (g == "spec")]
        if g == "spec":
            names = [n for n in names if not (r1m[n] > 0.30)]   # no pump-chasing
        act = b[names][b[names] > 0]
        if len(act):
            cov = R.iloc[-61:][act.index].fillna(0).cov().values * 252
            pv = float(math.sqrt(act.values @ cov @ act.values)); e = act * SIGMA / pv
            ww = {n: float(e[n]) / lev[n] for n in act.index}
            s = sum(ww.values())
            if s > budget: ww = {k: v * budget / s for k, v in ww.items()}
            w.update(ww)
    out = {"asof": d.date().isoformat(), "computed_wita": tc.wita(), "duration_gate": gate, "legs": []}
    for n, sg, inst, lv, kind, g in LEGS:
        out["legs"].append(dict(name=n, signal=sg, inst=inst, lev=lv, kind=kind, group=g, trend=float(tr[n]),
                                above_sma200=bool(P.loc[d, n] > sma[n]), xs12m=round(float(xm[n]), 4), r1m=round(float(r1m[n]), 4),
                                vol60=round(float(vol[n]), 4), notional_w=round(w.get(n, 0.0), 5)))
    cw = sum(l["notional_w"] for l in out["legs"] if l["group"] != "spec")
    out["sum_core_global"] = round(cw, 4); out["sum_spec"] = round(sum(l["notional_w"] for l in out["legs"] if l["group"] == "spec"), 4)
    out["mf_w"] = round(max(0.0, CORE_BUDGET - cw), 4)
    return out

def plan(write=True):
    sig = signals(); L = tc.load(); nav = float(L.get("equity_aud") or tc.NAV0)
    tg = [dict(inst=l["inst"], group=l["group"], w=l["notional_w"], target_aud=round(l["notional_w"] * nav, 0)) for l in sig["legs"]]
    for m in tc.MF: tg.append(dict(inst=m, group="mf", w=round(sig["mf_w"] / 3, 5), target_aud=round(sig["mf_w"] / 3 * nav, 0)))
    p = dict(book="trend_core", paper_only=True, status="STAGED: crypto now (live Kraken), ETFs at 28 Oct US open after rebook (live 1m prices)", card_label=CARD,
             nav_aud=nav, spare_aud=tc.SPARE_AUD, budgets=dict(core_global=CORE_BUDGET, spec=SPEC_BUDGET, spare=tc.SPARE_AUD / tc.NAV0),
             spec_rules=dict(leg_stop=SPEC_LEG_STOP, sleeve_halt=SPEC_SLEEVE_HALT, pump_filter="signal 1m > +30% skipped"),
             signals=sig, targets=tg, executed=False, due_date=DUE, plan_wita=tc.wita())
    if write:
        tc.atomic_json(PLAN, p); write_status(p)
    return p

def write_status(p=None):
    p = p or (json.load(open(PLAN)) if os.path.exists(PLAN) else {})
    try:  # SPEC sleeve P&L from the live trend_core ledger (separate from core)
        L = tc.load(); fx = float(L.get("audusd") or tc.audusd()[0]); sv = sc = 0.0; stops = []
        for i in [l[2] for l in LEGS if l[5] == "spec"]:
            q = float(L["holdings"].get(i, 0) or 0); m = float(L["mark_px_usd"].get(i, 0) or 0); e = float(L["entry_px_usd"].get(i, 0) or 0)
            if q and m and e:
                sv += q * m / fx; sc += q * e / fx
                if m / e - 1 <= SPEC_LEG_STOP: stops.append(i)
        p["spec_value_aud"] = round(sv, 2); p["spec_pnl_aud"] = round(sv - sc, 2); p["spec_stops_hit"] = stops
        p["spec_halt"] = bool(sc and sv / sc - 1 <= SPEC_SLEEVE_HALT)
    except Exception:
        pass
    st = dict(paper_only=True, status=p.get("status"), due_date=p.get("due_date"), executed=p.get("executed"),
              signal_asof=(p.get("signals") or {}).get("asof"), targets=p.get("targets"),
              spec_target_aud=sum(t["target_aud"] for t in p.get("targets") or [] if t["group"] == "spec"),
              spec_pnl_aud=p.get("spec_pnl_aud", 0.0), spec_value_aud=p.get("spec_value_aud", 0.0), spec_halt=p.get("spec_halt", False), spec_stops_hit=p.get("spec_stops_hit", []),
              updated_wita=tc.wita())
    st.update(card_label=p.get("card_label", CARD), crypto_executed=p.get("crypto_executed", False),
              crypto_executed_wita=p.get("crypto_executed_wita"), crypto_fills=p.get("crypto_fills", []))
    tc.atomic_json(STATUS, st); return st

def _lock():
    import desk_lock; return desk_lock.ledger_lock("trend_core", timeout=300)

def crypto_quotes(insts):
    """Live Kraken quotes; refuse unless entry tape LIVE/RECENT (never stale)."""
    import tfo_crypto
    px = {}
    for i in insts:
        q = tfo_crypto.quote(i); ok, mode, ref, why = tfo_crypto.entry_tape(q)
        if not ok or mode not in ("LIVE", "RECENT"):
            raise RuntimeError("%s tape %s (%s): refused, no stale fills" % (i, mode, why))
        px[i] = dict(px=q["mid"], utc=q["utc"], age_s=q["trade_age_sec"], src="kraken_mid_" + mode)
    return px

def etf_quotes(insts, now_utc):
    import trend_core_rebook as rb
    return {k: dict(px=v["px"], utc=v["bar_utc"], age_s=v["age_s"], src=v["kind"]) for k, v in rb.live_prices(insts, now_utc).items()}

def _orders(L, targets, px, fx):
    out = []
    for t in targets:
        i = t["inst"]; pr = px[i]["px"]
        dq = t["target_aud"] * fx / pr - float(L["holdings"].get(i, 0) or 0)
        if abs(dq * pr / fx) < 400: continue
        out.append((i, dq, pr, "crypto" if "/" in i else "etf", t["group"]))
    out.sort(key=lambda o: o[1])  # sells first
    return out

def _book(L, orders, px, fx, reason):
    fills = []
    _halt = False
    try:
        import book_kill
        _halt, _why = book_kill.firm_halt_blocks_entry("trend_core_global")
    except Exception:
        _halt, _why = False, "ok"
    for i, dq, pr, kind, grp in orders:
        if _halt and dq > 0:
            tc.log("ENTRY REFUSED firm halt %s: %s" % (i, _why)); continue
        notional = abs(dq) * pr / fx; cost = notional * tc.COST[kind]
        cash = L["cash_aud"] + (notional if dq < 0 else -notional) - cost
        if dq > 0 and cash < tc.SPARE_AUD - 1:   # A$50k spare never deployed: clip buy
            dq = max(0.0, (L["cash_aud"] - tc.SPARE_AUD) / (1 + tc.COST[kind]) * fx / pr); notional = dq * pr / fx; cost = notional * tc.COST[kind]; cash = L["cash_aud"] - notional - cost
        if dq == 0: continue
        L["cash_aud"] = round(cash, 2); L["costs_paid_aud"] = round(L["costs_paid_aud"] + cost, 2)
        L["holdings"][i] = float(L["holdings"].get(i, 0)) + dq
        if dq > 0: L["entry_px_usd"][i] = pr
        L["mark_px_usd"][i] = pr; L.setdefault("mark_source", {})[i] = px[i]["src"]
        f = dict(id="TC-GLB-%s-%s" % (i.replace("/", ""), tc.iso()), utc=tc.iso(), wita=tc.wita(), reason=reason,
                 inst=i, side="BUY" if dq > 0 else "SELL", shares=round(dq, 6), px_usd=pr, px_utc=px[i]["utc"], px_src=px[i]["src"],
                 notional_aud=round(notional, 2), cost_aud=round(cost, 2), audusd=fx, leg=grp, paper_only=True)
        L["fills"].append(f); tc._append_fill({k: v for k, v in f.items() if k not in ("px_utc", "px_src")}); fills.append(f)
    assert L["cash_aud"] >= tc.SPARE_AUD - 1.0, "spare breach"
    return fills

def _deploy_log(msg):
    with open(os.path.expanduser("~/.tfo_swing/DEPLOY_LOG.txt"), "a") as fh:
        fh.write("%s [pt1580u] %s | PAPER only\n" % (tc.wita(), msg))

def crypto_now(dry=False):
    """Crypto legs only (crypto trades 24/7): fill to recomputed targets at live Kraken quotes."""
    p0 = json.load(open(PLAN)) if os.path.exists(PLAN) else {}
    if p0.get("crypto_executed") or p0.get("executed"): print("crypto already executed", p0.get("crypto_executed_wita")); return []
    with _lock():
        p = plan(write=False); L = tc.load(); fx, fxs = tc.audusd()
        ct = [t for t in p["targets"] if "/" in t["inst"]]
        px = crypto_quotes([t["inst"] for t in ct])
        orders = _orders(L, ct, px, fx)
        print("crypto orders:", [(o[0], round(o[1], 6), o[2]) for o in orders])
        if dry: return orders
        fills = _book(L, orders, px, fx, "global_rebalance_crypto")
        tc.restrike(L); tc.save(L); tc._hist(L); tc.write_status(L)
        p.update(crypto_executed=True, crypto_executed_wita=tc.wita(), crypto_fills=fills,
                 status="CRYPTO EXECUTED %s (live Kraken); ETFs execute 28 Oct US open after rebook" % tc.wita())
        tc.atomic_json(PLAN, p); write_status(p)
        msg = "TREND-CORE GLOBAL crypto legs filled now: " + "; ".join("%s %s %.6f@%.2f A$%.0f" % (f["inst"], f["side"], f["shares"], f["px_usd"], f["notional_aud"]) for f in fills) + "; cash A$%.2f" % L["cash_aud"]
        tc.log(msg); _deploy_log(msg); print(msg); return fills

def rebalance_if_due(force_dry=False, now_utc=None):
    """Due date + US session >=09:45 ET + rebook done: ETF legs (and crypto if not yet done) to targets at live prices."""
    p = json.load(open(PLAN)) if os.path.exists(PLAN) else None
    if not p or p.get("executed"): tc.log("global rebalance-if-due: no pending plan"); print("no pending plan"); return
    import zoneinfo
    now_utc = now_utc or tc.now_utc()
    ny = now_utc.astimezone(zoneinfo.ZoneInfo("America/New_York"))
    due = dt.date.fromisoformat(p["due_date"])
    try: sess = tc.trend18.is_session(ny.date())
    except Exception: sess = ny.weekday() < 5
    open_now = sess and "09:45" <= ny.strftime("%H:%M") < "15:45"
    if not force_dry and (ny.date() < due or not open_now):
        msg = "global rebalance-if-due: not due/closed (NY %s)" % ny.strftime("%Y-%m-%d %H:%M"); tc.log(msg); print(msg); return
    if not force_dry and not os.path.exists(REBOOK_DONE):
        tc.log("global rebalance-if-due: waiting for trend_core_rebook"); print("waiting for trend_core_rebook (rebook first, then reweight)"); return
    with _lock():
        p = json.load(open(PLAN))
        if p.get("executed"): tc.log("global rebalance-if-due: no pending plan (locked)"); print("no pending plan"); return
        keep = {k: p[k] for k in ("due_date", "crypto_executed", "crypto_executed_wita", "crypto_fills", "card_label") if k in p}
        p = plan(write=False) | keep   # recompute on latest completed close + live NAV
        L = tc.load(); fx, fxs = tc.audusd()
        tg = [t for t in p["targets"] if not ("/" in t["inst"] and p.get("crypto_executed"))]
        etfs = [t["inst"] for t in tg if "/" not in t["inst"] and (t["target_aud"] > 0 or float(L["holdings"].get(t["inst"], 0) or 0))]
        try:
            px = etf_quotes(etfs, now_utc)
            px.update(crypto_quotes([t["inst"] for t in tg if "/" in t["inst"]]))
        except Exception as e:
            tc.log("global rebalance: price guard failed, retry next fire - %s" % e); print("price guard failed -", e); return
        orders = _orders(L, [t for t in tg if t["inst"] in px], px, fx)
        print("orders:", [(o[0], round(o[1], 4), o[2]) for o in orders])
        if force_dry: return orders
        fills = _book(L, orders, px, fx, "global_rebalance")
        tc.restrike(L); tc.save(L); tc._hist(L)
        p.update(executed=True, executed_wita=tc.wita(), etf_fills=fills, status="EXECUTED at live prices %s" % tc.wita()); tc.atomic_json(PLAN, p); write_status(p); tc.write_status(L)
        msg = "TREND-CORE GLOBAL reweight EXECUTED (US open, after rebook): %d fills; equity A$%.2f cash A$%.2f" % (len(fills), L["equity_aud"], L["cash_aud"])
        tc.log(msg); _deploy_log(msg); print(msg)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["plan", "crypto-now", "rebalance-if-due", "status"]); ap.add_argument("--force-dry", action="store_true"); ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    if a.cmd == "plan":
        p = plan()
        for t in p["targets"]: print("%-8s %-7s w=%.4f A$%s" % (t["inst"], t["group"], t["w"], t["target_aud"]))
        print("gate", p["signals"]["duration_gate"].get("open"), "asof", p["signals"]["asof"], "core+global", p["signals"]["sum_core_global"], "spec", p["signals"]["sum_spec"])
    elif a.cmd == "crypto-now": crypto_now(a.dry)
    elif a.cmd == "rebalance-if-due": rebalance_if_due(a.force_dry)
    else: print(json.dumps(write_status(), indent=1)[:3000])
