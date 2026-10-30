#!/usr/bin/env python3
"""ESSENTIALS A$1m paper book — fundamentally strong energy / utilities / essential services, all markets. PAPER ONLY.

Authority: Brett 28 Oct 2026 09:35/09:40/09:41 WITA (pt1575u). Separate book (TREND-CORE style), NOT firm equity.
NAV A$1,000,000; A$50,000 spare cash never deployed; deploy A$950k = CORE A$850k + SPEC A$100k (10% NAV).
CORE: quality screen (ND/EBITDA, FCF>0, ROE, payout cover, PE, ADV), inv-vol weights, name cap 8%, market cap 40%.
      Hold months; quarterly rebalance; monthly fundamental-break review; protective stop -22% from entry
      OR close < SMA200*0.90 (trend break) -> review/exit at next open.
SPEC: higher-risk energy/essential names (uranium juniors, explorers, turnarounds), <=2%/name, hard stop -20%/name,
      sleeve halt at -30% of sleeve capital; no pump-chasing (skip 1m ret > +30% or 5d > +15%). Separate P&L.
Excluded: heavy AI data-centre / neocloud credit exposure (CEG, VST, TLN, NRG, EQIX, DLR, AMT, CCI, SBAC, IREN, CORZ...).
No Indonesian assets. Fills: market open now -> live yfinance price (labelled, may be ~15m delayed);
closed -> STAGED, filled by `fill-staged` at the next open at real live prices. Never backdated.
Commands: screen | enter | fill-staged | mark | status | write-status
"""
from __future__ import annotations
import argparse, csv, datetime as dt, json, math, os, sys, tempfile
try:
    import desk_doh as _doh; _doh.install()
except Exception as _e:
    print("desk_doh skipped:", _e, file=sys.stderr)
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(BASE, "state", "essentials.json")
FILLS = os.path.join(BASE, "state", "essentials_fills.csv")
HIST = os.path.join(BASE, "state", "essentials_history.csv")
LOGF = os.path.join(BASE, "state", "essentials_log.txt")
SCREEN = os.path.join(BASE, "state", "essentials_screen.json")
STATUS = os.path.join(BASE, "static", "essentials_status.json")
REG = "ESSENTIALS_BOOK_REGISTRATION.md"
UTC = dt.timezone.utc; WITA = dt.timezone(dt.timedelta(hours=8), "WITA")
NAV0, SPARE, SPEC_CAP = 1_000_000.0, 50_000.0, 100_000.0
CORE_CAP = NAV0 - SPARE - SPEC_CAP
NAME_CAP, MKT_CAP, SPEC_NAME_CAP = 0.08, 0.40, 0.02
CORE_STOP, TREND_BREAK, SPEC_STOP, SPEC_HALT = -0.22, 0.90, -0.20, -0.30
N_CORE_MAX, PER_MKT_MAX = 22, 5
# 2026-10-28 13:40 WITA Brett approve-all #1: utilities floor >= 15% of book NAV. Applied to NEW BUYS and REBALANCE
# targets only (build_targets + _fill reserve); never force-sells existing holdings to reach it.
UTIL_FLOOR = 0.15
EXCLUDE_AI = {"CEG", "VST", "TLN", "NRG", "EQIX", "DLR", "AMT", "CCI", "SBAC", "IREN", "CORZ", "CRWV", "OKLO", "SMR"}

# market: (yf suffix rule handled by ticker, ccy, tz, open, close, cost_bp_buy, cost_bp_sell)
MKT = {
 "US":  ("USD", "America/New_York", "09:30", "16:00", 5, 5),
 "ASX": ("AUD", "Australia/Sydney", "10:00", "16:00", 8, 8),
 "LSE": ("GBP", "Europe/London", "08:00", "16:30", 58, 8),   # incl 0.5% stamp duty on buys
 "EU":  ("EUR", "Europe/Paris", "09:00", "17:30", 18, 8),    # incl FR/IT FTT approx where applicable
 "NO":  ("NOK", "Europe/Oslo", "09:00", "16:20", 8, 8),
 "JP":  ("JPY", "Asia/Tokyo", "09:00", "15:00", 8, 8),
 "HK":  ("HKD", "Asia/Hong_Kong", "09:30", "16:00", 18, 18),  # incl 0.1% stamp each side
 "SG":  ("SGD", "Asia/Singapore", "09:00", "17:00", 10, 10),
 "KR":  ("KRW", "Asia/Seoul", "09:00", "15:30", 5, 23),       # sell tax approx
 "TW":  ("TWD", "Asia/Taipei", "09:00", "13:30", 8, 38),      # 0.3% sell tax
}
BREAKS = {"JP": ("11:30", "12:30"), "HK": ("12:00", "13:00"), "SG": ("12:00", "13:00")}
CORE_UNIVERSE = {
 "US": ["XOM", "CVX", "COP", "KMI", "WMB", "OKE", "ET", "LNG", "CCJ", "NEE", "DUK", "SO", "AEP", "XEL", "SRE", "AWK",
        "WM", "RSG", "WCN", "UNP", "CSX", "T", "VZ", "TMUS", "PG", "KO", "PEP", "CL", "WMT", "COST"],
 "ASX": ["WDS.AX", "STO.AX", "ORG.AX", "APA.AX", "AGL.AX", "TLS.AX", "TCL.AX", "BXB.AX", "COL.AX", "WOW.AX", "WES.AX", "ALX.AX", "SOL.AX"],
 "LSE": ["SHEL.L", "BP.L", "NG.L", "SSE.L", "UU.L", "SVT.L", "CNA.L", "TSCO.L", "ULVR.L", "BATS.L", "DGE.L", "PNN.L", "RTO.L"],
 "EU":  ["TTE.PA", "ENI.MI", "ENEL.MI", "IBE.MC", "ENGI.PA", "VIE.PA", "REP.MC", "RED.MC", "TRN.MI", "SRG.MI", "DTE.DE", "EOAN.DE", "RWE.DE"],
 "NO":  ["EQNR.OL", "TEL.OL"],
 "JP":  ["9432.T", "9433.T", "9434.T", "1605.T", "9531.T", "9532.T", "9020.T", "9022.T", "9503.T", "9502.T", "2914.T"],
 "HK":  ["0002.HK", "0003.HK", "0006.HK", "0883.HK", "0857.HK", "0386.HK", "0941.HK", "1038.HK", "0066.HK"],
 "SG":  ["Z74.SI", "U96.SI", "C52.SI", "S63.SI", "BN4.SI"],
 "KR":  ["017670.KS", "030200.KS", "015760.KS", "036460.KS", "010950.KS"],
 "TW":  ["2412.TW", "3045.TW", "4904.TW", "6505.TW", "1216.TW"],
}
SPEC_UNIVERSE = {
 "ASX": ["PDN.AX", "BOE.AX", "DYL.AX", "LOT.AX", "BMN.AX", "KAR.AX", "BPT.AX", "STX.AX", "CVN.AX", "AEL.AX"],
 "US":  ["NXE", "UEC", "DNN", "UUUU", "LEU", "TELL", "NFE", "PCG"],
 "LSE": ["HBR.L", "ENOG.L", "YCA.L"],
 "JP":  ["9501.T"],
}

def now(): return dt.datetime.now(UTC)
def iso(t=None): return (t or now()).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
def wita(t=None): return (t or now()).astimezone(WITA).strftime("%Y-%m-%d %H:%M WITA")
def log(m):
    l = "%s %s" % (wita(), m); open(LOGF, "a").write(l + "\n"); print(l)
def atomic(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w") as f: json.dump(obj, f, indent=2, default=str); f.write("\n")
    os.replace(tmp, path)

def is_open(mkt, t=None):
    ccy, tz, o, c, *_ = MKT[mkt]
    lt = (t or now()).astimezone(ZoneInfo(tz))
    if lt.weekday() >= 5: return False
    hm = lt.strftime("%H:%M")
    if not (o <= hm < c): return False
    b = BREAKS.get(mkt)
    return not (b and b[0] <= hm < b[1])

def market_of(tk):
    for m, u in list(CORE_UNIVERSE.items()) + list(SPEC_UNIVERSE.items()):
        if tk in u: return m
    return "US"

def fx_rates():
    """AUD per 1 local -> we store AUDxxx (local per 1 AUD)."""
    import yfinance as yf
    out = {"AUD": 1.0}
    for c in {v[0] for v in MKT.values()} - {"AUD"}:
        try:
            out[c] = float(yf.Ticker("AUD%s=X" % c).fast_info.last_price)
        except Exception as e:
            log("fx %s fail %s" % (c, e))
    return out

def _f(x):
    try:
        x = float(x); return x if math.isfinite(x) else None
    except Exception: return None

def fetch(tk):
    import yfinance as yf
    t = yf.Ticker(tk)
    info = {}
    try: info = t.info or {}
    except Exception as e: info = {"_err": str(e)[:80]}
    h = None
    try: h = t.history(period="14mo", auto_adjust=True)["Close"].dropna()
    except Exception: pass
    px = None
    try: px = _f(t.fast_info.last_price)
    except Exception: pass
    if px is None and h is not None and len(h): px = float(h.iloc[-1])
    d = {"tk": tk, "px": px, "ccy": info.get("currency"), "name": info.get("shortName"),
         "sector": info.get("sector"), "industry": info.get("industry"), "country": info.get("country")}
    ebitda, debt, cash = _f(info.get("ebitda")), _f(info.get("totalDebt")), _f(info.get("totalCash"))
    d["nd_ebitda"] = ((debt or 0) - (cash or 0)) / ebitda if ebitda and ebitda > 0 and debt is not None else None
    d["fcf"] = _f(info.get("freeCashflow")); d["mcap"] = _f(info.get("marketCap"))
    d["fcf_yield"] = d["fcf"] / d["mcap"] if d["fcf"] and d["mcap"] else None
    d["roe"] = _f(info.get("returnOnEquity")); d["payout"] = _f(info.get("payoutRatio"))
    d["pe"] = _f(info.get("trailingPE")) or _f(info.get("forwardPE")); d["dy"] = _f(info.get("dividendYield"))
    d["adv_local"] = (_f(info.get("averageVolume")) or 0) * (px or 0)
    if h is not None and len(h) > 210:
        r = h.pct_change().dropna()
        d["vol"] = float(r.iloc[-252:].std() * math.sqrt(252))
        d["sma200"] = float(h.iloc[-200:].mean())
        d["ret_6m"] = float(h.iloc[-1] / h.iloc[-126] - 1); d["ret_1m"] = float(h.iloc[-1] / h.iloc[-21] - 1)
        d["ret_5d"] = float(h.iloc[-1] / h.iloc[-6] - 1)
    d["missing"] = [k for k in ("nd_ebitda", "fcf", "roe", "payout", "pe", "vol") if d.get(k) is None]
    return d

def to_aud(px_local, ccy, fx):
    if px_local is None: return None
    if ccy == "GBp": return px_local / 100.0 / fx["GBP"]
    return px_local / fx[ccy]

def quality_score(d):
    """Pass/fail gates + composite. Missing fields are not imputed; they cost points and are listed."""
    why = []
    if d["tk"].split(".")[0] in EXCLUDE_AI: return None, ["excluded: AI data-centre/neocloud exposure"]
    if d.get("country") == "Indonesia" or d["tk"].endswith(".JK"): return None, ["excluded: Indonesia"]
    if not d.get("px") or not d.get("vol"): return None, ["no price/vol"]
    nd = d.get("nd_ebitda")
    if nd is not None and nd > 3.5 and d["industry"] not in ("Utilities - Regulated Electric", "Utilities - Regulated Water", "Utilities - Regulated Gas", "Utilities - Diversified"):
        return None, ["ND/EBITDA %.1f > 3.5" % nd]
    if nd is not None and nd > 6.0: return None, ["ND/EBITDA %.1f > 6 (utility)" % nd]
    if d.get("fcf") is not None and d["fcf"] <= 0 and not (d.get("industry") or "").startswith("Utilities"):
        return None, ["FCF <= 0"]
    if d.get("roe") is not None and d["roe"] < 0.06: return None, ["ROE %.1f%% < 6%%" % (d["roe"] * 100)]
    if d.get("payout") is not None and d["payout"] > 1.2: return None, ["payout %.0f%% > 120%% (dividend uncovered)" % (d["payout"] * 100)]
    if d.get("pe") is not None and (d["pe"] > 35 or d["pe"] <= 0): return None, ["PE %.1f out of range" % d["pe"]]
    s = 0.0
    s += (2.0 - min(nd, 6) / 3.0) if nd is not None else 0
    s += min((d.get("roe") or 0) * 10, 3)
    s += min(max(d.get("fcf_yield") or 0, 0) * 30, 3)
    s += (1.0 if (d.get("payout") or 0.6) < 0.8 else 0.3)
    s += (1.0 if d["px"] > d.get("sma200", 0) else -0.5)
    s -= 0.4 * len(d["missing"])
    return round(s, 3), why

def screen():
    import yfinance  # noqa
    fx = fx_rates()
    rows = []
    for m, u in CORE_UNIVERSE.items():
        for tk in u:
            try: d = fetch(tk)
            except Exception as e: d = {"tk": tk, "err": str(e)[:80], "missing": ["all"]}
            d["mkt"] = m; d["sleeve"] = "core"
            sc, why = quality_score(d) if d.get("px") else (None, ["fetch fail"])
            d["score"], d["why"] = sc, why
            d["adv_aud"] = to_aud(d.get("adv_local"), d.get("ccy") or MKT[m][0], fx) if d.get("adv_local") else None
            if sc is not None and (d["adv_aud"] or 0) < 5e6: d["score"], d["why"] = None, ["ADV < A$5m"]
            rows.append(d)
    spec = []
    for m, u in SPEC_UNIVERSE.items():
        for tk in u:
            try: d = fetch(tk)
            except Exception as e: d = {"tk": tk, "err": str(e)[:80], "missing": ["all"]}
            d["mkt"] = m; d["sleeve"] = "spec"
            d["adv_aud"] = to_aud(d.get("adv_local"), d.get("ccy") or MKT[m][0], fx) if d.get("adv_local") else None
            why = []
            if not d.get("px") or d.get("vol") is None: why.append("no price/vol")
            elif (d.get("adv_aud") or 0) < 1e6: why.append("ADV < A$1m")
            elif (d.get("ret_1m") or 0) > 0.30 or (d.get("ret_5d") or 0) > 0.15: why.append("pump filter (1m>30% or 5d>15%)")
            elif d["px"] < 0.9 * d.get("sma200", 0): why.append("below 0.9xSMA200 (broken trend)")
            d["why"] = why
            d["score"] = None if why else round((d.get("ret_6m") or 0) - 0.5 * (d.get("vol") or 0), 4)
            spec.append(d)
    out = {"utc": iso(), "wita": wita(), "fx_aud": fx, "core": rows, "spec": spec}
    atomic(SCREEN, out)
    return out

def build_targets(scr):
    core = sorted([d for d in scr["core"] if d.get("score") is not None], key=lambda d: -d["score"])
    picks, per_mkt = [], {}
    for d in core:
        if per_mkt.get(d["mkt"], 0) >= PER_MKT_MAX: continue
        picks.append(d); per_mkt[d["mkt"]] = per_mkt.get(d["mkt"], 0) + 1
        if len(picks) >= N_CORE_MAX: break
    iv = {d["tk"]: 1.0 / max(d["vol"], 0.08) for d in picks}
    w = {k: v / sum(iv.values()) for k, v in iv.items()}
    for _ in range(30):  # iterative caps (fraction of CORE sleeve; name cap is of NAV -> 0.08*NAV/CORE_CAP)
        ncap = NAME_CAP * NAV0 / CORE_CAP; mcap = MKT_CAP * NAV0 / CORE_CAP
        over = False
        for k in w:
            if w[k] > ncap + 1e-9: w[k] = ncap; over = True
        mk = {}
        for d in picks: mk[d["mkt"]] = mk.get(d["mkt"], 0) + w[d["tk"]]
        for m, s in mk.items():
            if s > mcap + 1e-9:
                for d in picks:
                    if d["mkt"] == m: w[d["tk"]] *= mcap / s
                over = True
        tot = sum(w.values()); w = {k: v / tot for k, v in w.items()}
        if not over: break
    w = apply_util_floor(w, picks)
    spec = sorted([d for d in scr["spec"] if d.get("score") is not None], key=lambda d: -d["score"])[:8]
    tg = []
    for d in picks:
        tg.append(dict(tk=d["tk"], mkt=d["mkt"], sleeve="core", name=d.get("name"), target_aud=round(w[d["tk"]] * CORE_CAP, 2),
                       w_nav=round(w[d["tk"]] * CORE_CAP / NAV0, 4), vol=d["vol"], ccy=d.get("ccy"), score=d["score"], missing=d["missing"],
                       sector=d.get("sector"), industry=d.get("industry")))
    if spec:
        each = min(SPEC_CAP / len(spec), SPEC_NAME_CAP * NAV0)
        for d in spec:
            tg.append(dict(tk=d["tk"], mkt=d["mkt"], sleeve="spec", name=d.get("name"), target_aud=round(each, 2), w_nav=round(each / NAV0, 4),
                           vol=d["vol"], ccy=d.get("ccy"), score=d["score"], missing=d["missing"]))
    return tg

def is_utility(d):
    return (d.get("sector") or "") == "Utilities" or (d.get("industry") or "").startswith("Utilities")

def apply_util_floor(w, picks, floor=UTIL_FLOOR):
    """w = CORE-sleeve weights. Lift utility picks so utilities >= floor x NAV (name cap kept), scale others down."""
    need = floor * NAV0 / CORE_CAP
    ncap = NAME_CAP * NAV0 / CORE_CAP
    ut = [d["tk"] for d in picks if is_utility(d)]
    if not ut: return w
    w = dict(w)
    for _ in range(30):
        u = sum(w[k] for k in ut)
        if u >= need - 1e-9: break
        free = [k for k in ut if w[k] < ncap - 1e-9]
        if not free: break
        add = need - u; fs = sum(w[k] for k in free) or 1.0
        for k in free: w[k] = min(ncap, w[k] + add * w[k] / fs)
        u = sum(w[k] for k in ut); o = [k for k in w if k not in ut]; os_ = sum(w[k] for k in o)
        if os_ > 0:
            for k in o: w[k] *= max(0.0, 1.0 - u) / os_
    return w

def _sector_lookup():
    try:
        s = json.load(open(SCREEN)); return {d["tk"]: d for d in s.get("core", []) + s.get("spec", [])}
    except Exception: return {}

def util_exposure(L, lookup=None):
    """Utilities share of NAV0: held (at mark) + staged targets. Reserve = what new non-utility buys must leave free."""
    lk = lookup if lookup is not None else _sector_lookup()
    def ut(x): return is_utility(x) if (x.get("sector") or x.get("industry")) else is_utility(lk.get(x["tk"], {}))
    held = sum(p["qty"] * (p.get("mark_aud") or p["entry_px_aud"]) for p in L["positions"].values() if ut(p))
    stg = sum(o.get("target_aud") or 0 for o in L.get("staged") or [] if ut(o))
    reserve = max(0.0, UTIL_FLOOR * NAV0 - held - stg)
    return dict(floor_pct=UTIL_FLOOR, held_aud=round(held, 2), staged_aud=round(stg, 2), held_pct=round(held / NAV0, 4),
                projected_pct=round((held + stg) / NAV0, 4), reserve_aud=round(reserve, 2), utility_fn=ut)

def load():
    if os.path.exists(LEDGER): return json.load(open(LEDGER))
    return {"book": "essentials", "label": "ESSENTIALS A$1m (energy/utilities/essential services, PAPER)", "mode": "PAPER",
            "paper_only": True, "ccy": "AUD", "in_firm_equity": False, "registration": REG,
            "funder_approval": "Brett 2026-10-28 09:35/09:40/09:41 WITA pt1575u", "seed_aud": NAV0, "spare_aud": SPARE,
            "core_cap_aud": CORE_CAP, "spec_cap_aud": SPEC_CAP, "cash_aud": NAV0, "costs_paid_aud": 0.0, "realised_aud": 0.0,
            "positions": {}, "staged": [], "fills": [], "spec_halt": False, "created_wita": wita()}
def save(L): L["updated_utc"] = iso(); L["updated_wita"] = wita(); atomic(LEDGER, L)

def _fill(L, o, px_local, fx, reason):
    ccy = o.get("ccy") or MKT[o["mkt"]][0]
    px_aud = to_aud(px_local, ccy, fx)
    if o.get("oneoff") == ONEOFF_TAG: return _fill_oneoff(L, o, px_local, px_aud, ccy, fx, reason)
    qty = math.floor(o["target_aud"] / px_aud) if px_aud < o["target_aud"] else 0
    room = L["cash_aud"] - SPARE
    qty = min(qty, math.floor(room / (px_aud * (1 + MKT[o["mkt"]][4] / 1e4)))) if room > 0 else 0   # trim last fills; spare never touched
    # 2026-10-28 (Brett via parent): size against the budget cap AT FILL TIME - deployed at current marks + the OTHER staged targets
    # + this fill <= NAV0 - SPARE (price drift on filled names can no longer push deployed+staged over the cap)
    others = sum(x.get("target_aud") or 0 for x in L.get("staged") or [] if x.get("tk") != o["tk"])
    dep_now = sum(p["qty"] * (p.get("mark_aud") or p["entry_px_aud"]) for p in L["positions"].values())
    budget = (NAV0 - SPARE) - dep_now - others
    ux = util_exposure(L)   # utilities floor: non-utility new buys must leave room to reach 15% (never sells anything)
    if not ux["utility_fn"](o): budget -= ux["reserve_aud"]
    qty = min(qty, math.floor(budget / (px_aud * (1 + MKT[o["mkt"]][4] / 1e4)))) if budget > 0 else 0
    if qty <= 0: log("skip %s: no room above spare / 1 share > target / budget cap" % o["tk"]); return None
    try:   # 2026-10-28 13:40 Brett approve-all #5: theme cap ENFORCE (new buys only)
        import theme_exposure as _te
        ok, why = _te.check_buy(o["tk"], qty * px_aud, ccy=("GBP" if ccy == "GBp" else ccy), hint="essential", book="ESSENTIALS")
    except Exception as e: ok, why = False, "theme cap check failed %s" % e
    if not ok: log("skip %s: %s" % (o["tk"], why)); return None
    notional = qty * px_aud; cost = notional * MKT[o["mkt"]][4] / 1e4
    if L["cash_aud"] - notional - cost < SPARE - 1e-6: log("skip %s: would breach spare" % o["tk"]); return None
    L["cash_aud"] = round(L["cash_aud"] - notional - cost, 2); L["costs_paid_aud"] = round(L["costs_paid_aud"] + cost, 2)
    L["positions"][o["tk"]] = dict(tk=o["tk"], mkt=o["mkt"], sleeve=o["sleeve"], name=o.get("name"), ccy=ccy, qty=qty,
                                   entry_px_local=px_local, entry_px_aud=px_aud, entry_fx=fx.get(ccy.upper() if ccy != "GBp" else "GBP"),
                                   entry_utc=iso(), entry_wita=wita(), mark_px_local=px_local, mark_aud=px_aud, cost_aud=round(cost, 2),
                                   stop_aud=px_aud * (1 + (SPEC_STOP if o["sleeve"] == "spec" else CORE_STOP)))
    f = dict(id="ES-%s" % o["tk"], utc=iso(), wita=wita(), reason=reason, tk=o["tk"], mkt=o["mkt"], sleeve=o["sleeve"], side="BUY", qty=qty,
             px_local=px_local, ccy=ccy, px_aud=round(px_aud, 6), notional_aud=round(notional, 2), cost_aud=round(cost, 2),
             price_source="yfinance fast_info.last_price (live session; may be ~15m delayed)", paper_only=True)
    L["fills"].append(f)
    new = not os.path.exists(FILLS)
    with open(FILLS, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(f)); new and w.writeheader(); w.writerow(f)
    log("FILL %s %s %s qty=%s px=%s %s A$%.0f cost A$%.2f" % (o["sleeve"], o["mkt"], o["tk"], qty, round(px_local, 4), ccy, notional, cost))
    return f

# 2026-10-29 14:56 WITA BRETT ORDER (one-off exception to quarterly rebalance): deploy ALL idle cash incl. the A$50k spare.
# Orders are STAGED with oneoff=ONEOFF_TAG, a buy limit, and a fixed A$ size. They fill only in a live session, on a FRESH
# Yahoo 1m bar (<= FRESH_SEC old), at the live price, and only if live <= limit. Spare/budget/util-reserve checks are bypassed
# for these tagged orders ONLY (cash may not go negative); theme cap still enforced. Future cash stays under the quarterly rule.
ONEOFF_TAG = "BRETT-20261029-1456-DEPLOY-ALL"
FRESH_SEC = 20 * 60

def fresh_px(tk):
    """Yahoo primary: last 1m bar close if bar age <= FRESH_SEC, else None (never fill on stale bars)."""
    import yfinance as yf
    try:
        h = yf.Ticker(tk).history(period="1d", interval="1m").dropna(subset=["Close"])
        if not len(h): return None, "no 1m bars"
        age = (now() - h.index[-1].to_pydatetime().astimezone(UTC)).total_seconds()
        if age > FRESH_SEC: return None, "stale bar age %ds" % age
        return _f(h["Close"].iloc[-1]), "yahoo 1m bar age %ds" % age
    except Exception as e: return None, "fresh_px err %s" % str(e)[:60]

def _fill_oneoff(L, o, px_local, px_aud, ccy, fx, reason):
    cb = MKT[o["mkt"]][4] / 1e4
    qty = math.floor(o["target_aud"] / (px_aud * (1 + cb)))
    qty = min(qty, math.floor(max(0.0, L["cash_aud"]) / (px_aud * (1 + cb))))
    if o.get("max_qty"): qty = min(qty, int(o["max_qty"]))   # 5% ADV cap fixed at staging
    if qty <= 0: log("skip %s (oneoff): qty 0" % o["tk"]); return None
    try:
        import theme_exposure as _te
        ok, why = _te.check_buy(o["tk"], qty * px_aud, ccy=("GBP" if ccy == "GBp" else ccy), hint="essential", book="ESSENTIALS")
    except Exception as e: ok, why = False, "theme cap check failed %s" % e
    if not ok: log("skip %s (oneoff): %s" % (o["tk"], why)); return None
    notional = qty * px_aud; cost = notional * cb
    L["cash_aud"] = round(L["cash_aud"] - notional - cost, 2); L["costs_paid_aud"] = round(L["costs_paid_aud"] + cost, 2)
    p = L["positions"].get(o["tk"])
    if p:   # add to existing holding: weighted-average entry, stop re-based on the blended entry
        q0 = p["qty"]; q1 = q0 + qty
        p["entry_px_local"] = (p["entry_px_local"] * q0 + px_local * qty) / q1
        p["entry_px_aud"] = (p["entry_px_aud"] * q0 + px_aud * qty) / q1
        p["qty"] = q1; p["cost_aud"] = round((p.get("cost_aud") or 0) + cost, 2)
        p["mark_px_local"] = px_local; p["mark_aud"] = px_aud
        p["stop_aud"] = p["entry_px_aud"] * (1 + (SPEC_STOP if p["sleeve"] == "spec" else CORE_STOP))
        p.setdefault("adds", []).append(dict(utc=iso(), wita=wita(), qty=qty, px_local=px_local, px_aud=px_aud, tag=ONEOFF_TAG))
    else:
        L["positions"][o["tk"]] = dict(tk=o["tk"], mkt=o["mkt"], sleeve=o["sleeve"], name=o.get("name"), ccy=ccy, qty=qty,
                                       entry_px_local=px_local, entry_px_aud=px_aud, entry_fx=fx.get(ccy.upper() if ccy != "GBp" else "GBP"),
                                       entry_utc=iso(), entry_wita=wita(), mark_px_local=px_local, mark_aud=px_aud, cost_aud=round(cost, 2),
                                       stop_aud=px_aud * (1 + (SPEC_STOP if o["sleeve"] == "spec" else CORE_STOP)))
    f = dict(id="ES-%s-%s" % (o["tk"], ONEOFF_TAG[:14]), utc=iso(), wita=wita(), reason=reason, tk=o["tk"], mkt=o["mkt"], sleeve=o["sleeve"],
             side="BUY", qty=qty, px_local=px_local, ccy=ccy, px_aud=round(px_aud, 6), notional_aud=round(notional, 2), cost_aud=round(cost, 2),
             price_source=o.get("_px_src", "yahoo"), limit_local=o.get("limit_local"), oneoff=ONEOFF_TAG, paper_only=True)
    L["fills"].append(f)
    new = not os.path.exists(FILLS)
    with open(FILLS, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "utc", "wita", "reason", "tk", "mkt", "sleeve", "side", "qty", "px_local", "ccy", "px_aud",
                                           "notional_aud", "cost_aud", "price_source"], extrasaction="ignore"); new and w.writeheader(); w.writerow(f)
    log("FILL ONEOFF %s %s qty=%s px=%s %s (lim %s) A$%.0f cost A$%.2f" % (o["mkt"], o["tk"], qty, round(px_local, 4), ccy, o.get("limit_local"), notional, cost))
    return f

def live_px(tk):
    import yfinance as yf
    return _f(yf.Ticker(tk).fast_info.last_price)

def enter():
    L = load()
    if L["positions"] or L["staged"]: log("enter: already entered/staged - no-op"); return L
    scr = screen(); tg = build_targets(scr); fx = scr["fx_aud"]
    L["targets"] = tg; L["screen_utc"] = scr["utc"]
    for o in tg:
        if is_open(o["mkt"]):
            px = live_px(o["tk"])
            if px: _fill(L, o, px, fx, "entry_live"); continue
        o2 = dict(o, staged_wita=wita(), ref_close_local=next((d["px"] for d in scr["core"] + scr["spec"] if d["tk"] == o["tk"]), None),
                  status="STAGED next open (fill at live price then; ref close is NOT a fill)")
        L["staged"].append(o2); log("STAGE %s %s %s A$%.0f" % (o["sleeve"], o["mkt"], o["tk"], o["target_aud"]))
    save(L); mark(L); return L

def fill_staged():
    L = load()
    if not L["staged"]: return L
    fx = fx_rates(); keep = []
    for o in L["staged"]:
        if is_open(o["mkt"]) and o.get("oneoff") == ONEOFF_TAG:
            px, src = fresh_px(o["tk"])
            if px is None: log("hold %s (oneoff): %s" % (o["tk"], src)); keep.append(o); continue
            if o.get("limit_local") and px > o["limit_local"]:
                log("hold %s (oneoff): live %.4f > limit %.4f" % (o["tk"], px, o["limit_local"])); keep.append(o); continue
            o["_px_src"] = src
            if _fill(L, o, px, fx, "oneoff_next_open_limit"): continue
            o["status"] = "SKIPPED at fill (see log)"; L.setdefault("skipped", []).append(dict(o, wita=wita())); continue
        if is_open(o["mkt"]):
            px = live_px(o["tk"])
            if px and _fill(L, o, px, fx, "entry_next_open"): continue
        keep.append(o)
    L["staged"] = keep; save(L); return L

def mark(L=None):
    L = L or fill_staged()
    try: fx = fx_rates()
    except Exception: fx = None
    import yfinance as yf
    for tk, p in L["positions"].items():
        try:
            px = live_px(tk)
            if px and fx:
                p["mark_px_local"] = px; p["mark_aud"] = to_aud(px, p["ccy"], fx); p["mark_utc"] = iso()
        except Exception as e: log("mark %s fail %s" % (tk, e))
        p["stop_hit"] = bool(p.get("mark_aud") and p["mark_aud"] <= p["stop_aud"])
    spec_cost = sum(p["qty"] * p["entry_px_aud"] for p in L["positions"].values() if p["sleeve"] == "spec")
    spec_val = sum(p["qty"] * p["mark_aud"] for p in L["positions"].values() if p["sleeve"] == "spec")
    if spec_cost and (spec_val / spec_cost - 1) <= SPEC_HALT and not L.get("spec_halt"):
        L["spec_halt"] = True; log("SPEC SLEEVE HALT: sleeve %.1f%%" % ((spec_val / spec_cost - 1) * 100))
    for p in L["positions"].values():
        if p.get("stop_hit"): log("STOP FLAG %s (%s) mark A$%.4f <= stop A$%.4f -> exit at next open review" % (p["tk"], p["sleeve"], p["mark_aud"], p["stop_aud"]))
    save(L); write_status(L)
    new = not os.path.exists(HIST); st = json.load(open(STATUS))
    with open(HIST, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["utc", "nav_aud", "cash_aud", "deployed_aud", "core_pnl_aud", "spec_pnl_aud"])
        new and w.writeheader()
        w.writerow({k: st.get(k) for k in ["nav_aud", "cash_aud", "deployed_aud", "core_pnl_aud", "spec_pnl_aud"]} | {"utc": iso()})
    return L

def write_status(L=None):
    L = L or load()
    rows, by_mkt, sl = [], {}, {"core": [0, 0], "spec": [0, 0]}
    for p in L["positions"].values():
        val = p["qty"] * (p.get("mark_aud") or p["entry_px_aud"]); cb = p["qty"] * p["entry_px_aud"]
        sl[p["sleeve"]][0] += val; sl[p["sleeve"]][1] += cb
        by_mkt[p["mkt"]] = by_mkt.get(p["mkt"], 0) + val
        rows.append(dict(inst=p["tk"], mkt=p["mkt"], sleeve=p["sleeve"], name=p.get("name"), qty=p["qty"], ccy=p["ccy"],
                         entry_px_local=p["entry_px_local"], mark_px_local=p.get("mark_px_local"), value_aud=round(val, 2),
                         upl_aud=round(val - cb, 2), stop_aud=round(p["stop_aud"], 4), stop_hit=p.get("stop_hit", False), status="FILLED", entry_wita=p["entry_wita"]))
    for o in L["staged"]:
        rows.append(dict(inst=o["tk"], mkt=o["mkt"], sleeve=o["sleeve"], name=o.get("name"), qty=None, value_aud=0, upl_aud=None,
                         target_aud=o["target_aud"], ref_close_local=o.get("ref_close_local"), status="STAGED", staged_wita=o.get("staged_wita")))
    dep = sl["core"][0] + sl["spec"][0]; nav = L["cash_aud"] + dep
    st = dict(book="essentials", label=L["label"], paper_only=True, ccy="AUD", in_firm_equity=False, nav_aud=round(nav, 2), seed_aud=NAV0,
              cash_aud=L["cash_aud"], spare_aud=SPARE, deployed_aud=round(dep, 2), staged_aud=round(sum(o["target_aud"] for o in L["staged"]), 2),
              vs_seed_aud=round(nav - NAV0, 2), core_value_aud=round(sl["core"][0], 2), core_pnl_aud=round(sl["core"][0] - sl["core"][1], 2),
              spec_value_aud=round(sl["spec"][0], 2), spec_pnl_aud=round(sl["spec"][0] - sl["spec"][1], 2), spec_cap_aud=SPEC_CAP,
              spec_halt=L.get("spec_halt", False), costs_paid_aud=L["costs_paid_aud"], by_market_aud={k: round(v, 2) for k, v in by_mkt.items()},
              holdings=rows, rules=dict(core_stop=CORE_STOP, trend_break="close < 0.90 x SMA200", spec_stop=SPEC_STOP, spec_halt=SPEC_HALT,
              name_cap=NAME_CAP, market_cap=MKT_CAP, spec_name_cap=SPEC_NAME_CAP, rebalance="quarterly (next 2027-01-04); monthly fundamental-break review (next 2026-11-30)"),
              updated_utc=L.get("updated_utc"), updated_wita=L.get("updated_wita"), registration=REG)
    try:
        ux = util_exposure(L); ux.pop("utility_fn", None)
        st["utilities_floor"] = dict(ux, rule="utilities >= 15% NAV on new buys/rebalances only; no forced sells")
    except Exception as e: st["utilities_floor"] = {"error": str(e)[:80]}
    atomic(STATUS, st); return st

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["screen", "enter", "fill-staged", "mark", "status", "write-status"])
    a = ap.parse_args()
    if a.cmd == "screen":
        s = screen(); tg = build_targets(s)
        for o in tg: print(o["sleeve"], o["mkt"], o["tk"], o["w_nav"], o["missing"])
    elif a.cmd == "enter": enter()
    elif a.cmd == "fill-staged": fill_staged(); write_status()
    elif a.cmd == "mark": mark()
    st = write_status()
    print("ESSENTIALS NAV A$%s cash A$%s deployed A$%s staged A$%s core P&L A$%s spec P&L A$%s" % (
        st["nav_aud"], st["cash_aud"], st["deployed_aud"], st["staged_aud"], st["core_pnl_aud"], st["spec_pnl_aud"]))

if __name__ == "__main__":
    main()
