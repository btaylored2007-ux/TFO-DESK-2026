#!/usr/bin/env python3
"""CSP sleeve — PAPER forward, REPORT-ONLY (Brett 2026-10-28 05:27 WITA, [pt1549u]).
Never places orders anywhere. Each run: marks open would-be puts from the real Yahoo option chain
(buyback at ask), applies TP 50% / stop 3x credit / expiry-assign, then logs would-be entries
(sell at bid) for SPY QQQ AAPL MSFT under the pre-registered rules in
research/csp_sleeve_20261028/PREREG.md. Output: state/csp_sleeve/ledger.json + static/csp_sleeve.json."""
import json, math, os, time, datetime as dt
BASE = os.path.dirname(os.path.abspath(__file__))
SD = os.path.join(BASE, "state", "csp_sleeve"); LP = os.path.join(SD, "ledger.json"); SP = os.path.join(BASE, "static", "csp_sleeve.json")
NAMES = ["SPY", "QQQ", "AAPL", "MSFT"]; DD = dict(SPY=.34, QQQ=.28, AAPL=.31, MSFT=.28)
SLEEVE_AUD = 200000.0; CRASH_BUDGET_AUD = 30000.0; COMM = 0.65; TP = 0.5; STOPX = 3.0; R = 0.04
Q = dict(SPY=.016, QQQ=.008, AAPL=.006, MSFT=.01)
def ncdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def put_delta(S, K, T, v, q=0.0):
    if T <= 0 or v <= 0: return -1.0 if K > S else 0.0
    d1 = (math.log(S / K) + (R - q + v * v / 2) * T) / (v * math.sqrt(T)); return -math.exp(-q * T) * ncdf(-d1)
def wita(): return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M WITA")
def jload(p, fb):
    try: return json.load(open(p))
    except Exception: return fb
def atomic(p, o):
    os.makedirs(os.path.dirname(p), exist_ok=True); t = p + ".tmp"; json.dump(o, open(t, "w"), indent=1, default=str); os.replace(t, p)
def ivrank(series):
    s = [x for x in series if x == x][-252:]
    if len(s) < 252: return None
    lo, hi = min(s), max(s); return 100 * (s[-1] - lo) / (hi - lo + 1e-12)
IVR_MIN = 30   # Brett 2026-10-29 09:44 WITA: entry gate lowered 50 -> 30 (all other CSP rules unchanged)
def gate(name, ivr, vix, vix_med):
    if ivr is not None: return ivr >= IVR_MIN, "IVR %.0f (gate %d)" % (ivr, IVR_MIN)
    return (vix is not None and vix_med is not None and vix >= vix_med), "fallback VIX %.1f vs med %.1f" % (vix or 0, vix_med or 0)
def choose_put(S, chain_rows, T, q):
    """chain_rows: [{strike,bid,ask,iv}] -> row nearest 0.25 delta inside 0.20-0.30 with bid>0."""
    best = None
    for r in chain_rows:
        if not r.get("bid") or r["bid"] <= 0 or not r.get("iv"): continue
        d = abs(put_delta(S, r["strike"], T, r["iv"], q))
        if 0.20 <= d <= 0.30 and (best is None or abs(d - .25) < abs(best[1] - .25)): best = (r, d)
    return best
def size(K, S, credit, name, fx, open_pos):
    cash_usd = SLEEVE_AUD * fx - sum(p["K"] * 100 * p["n"] for p in open_pos)
    stress_used = sum(p["stress_aud"] for p in open_pos)
    per = max(0.0, 100 * (K - S * (1 - DD[name])) - credit * 100) / fx
    n = int(min(cash_usd // (K * 100), (CRASH_BUDGET_AUD - stress_used) // per if per > 0 else 99))
    return max(n, 0), per
def manage(p, ask, S, today):
    """returns exit reason or None"""
    if today >= p["exp"]: return "assigned" if S < p["K"] else "expired"
    if ask is not None and ask <= TP * p["credit"]: return "tp"
    if ask is not None and ask >= STOPX * p["credit"]: return "stop"
    return None
def fetch(name):
    import yfinance as yf
    tk = yf.Ticker(name); S = float(tk.history(period="5d")["Close"].iloc[-1]); today = dt.date.today()
    exps = [e for e in tk.options if (dt.date.fromisoformat(e) - today).days >= 7]
    return tk, S, exps
def run(now_fn=wita):
    import yfinance as yf
    L = jload(LP, {"open": [], "closed": [], "days": [], "realized_aud": 0.0})
    h = yf.download(["^VIX", "^VXN", "AUDUSD=X"] + NAMES, period="2y", progress=False, auto_adjust=False)["Close"].ffill()
    fx = float(h["AUDUSD=X"].iloc[-1]); vix = h["^VIX"]; today = dt.date.today().isoformat()
    lr = {n: h[n].apply(math.log).diff() for n in NAMES}
    rv = {n: lr[n].rolling(60).std() * math.sqrt(252) for n in NAMES}
    ivp = {"SPY": list(vix / 100), "QQQ": list(h["^VXN"] / 100)}
    for n in ("AAPL", "MSFT"): ivp[n] = list((vix / 100) * (rv[n] / rv["SPY"]).clip(.8, 2.5))
    vix_med = float(vix.iloc[-252:].median()); events = []
    for p in list(L["open"]):
        try:
            tk, S, _ = fetch(p["name"]); ch = tk.option_chain(p["exp"]).puts; row = ch[ch.strike == p["K"]]
            ask = float(row.ask.iloc[0]) if len(row) and row.ask.iloc[0] > 0 else None
        except Exception as e: S, ask = p.get("S_mark"), None
        p["S_mark"] = S; p["mark_ask"] = ask; p["upnl_aud"] = round(((p["credit"] - (ask if ask is not None else p["credit"])) * 100 * p["n"] - COMM * p["n"]) / fx, 2)
        why = manage(p, ask, S, today)
        if why:
            cost = (max(p["K"] - S, 0) if why in ("assigned", "expired") else ask) * 100 * p["n"] + (COMM * p["n"] if why in ("tp", "stop") else 0)
            pnl = round((p["credit"] * 100 * p["n"] - COMM * p["n"] - cost) / fx, 2)
            L["open"].remove(p); L["closed"].append(dict(p, exit=today, why=why, pnl_aud=pnl)); L["realized_aud"] = round(L["realized_aud"] + pnl, 2)
            events.append("CLOSE %s %s %s A$%.0f" % (p["name"], p["K"], why, pnl))
    held = {p["name"] for p in L["open"]}
    for n in NAMES:
        if n in held: continue
        ok, why = gate(n, ivrank(ivp[n]), float(vix.iloc[-1]), vix_med)
        if not ok: events.append("SKIP %s gate %s" % (n, why)); continue
        try:
            tk, S, exps = fetch(n); e = min(exps, key=lambda x: abs((dt.date.fromisoformat(x) - dt.date.today()).days - 30))
            T = (dt.date.fromisoformat(e) - dt.date.today()).days / 365
            ch = tk.option_chain(e).puts; rows = [dict(strike=float(r.strike), bid=float(r.bid), ask=float(r.ask), iv=float(r.impliedVolatility)) for r in ch.itertuples()]
        except Exception as ex: events.append("SKIP %s chain error %s" % (n, str(ex)[:60])); continue
        b = choose_put(S, rows, T, Q[n])
        if not b: events.append("SKIP %s no 0.20-0.30 delta put" % n); continue
        r, d = b; k, per = size(r["strike"], S, r["bid"], n, fx, L["open"])
        if k < 1: events.append("SKIP %s size 0 (cash-secure/crash budget) K=%s" % (n, r["strike"])); continue
        L["open"].append(dict(name=n, entry=today, exp=e, K=r["strike"], n=k, credit=r["bid"], delta=round(d, 3), iv=round(r["iv"], 3), S_entry=S, stress_aud=round(per * k, 2), gate=why, S_mark=S, mark_ask=r["ask"]))
        events.append("SELL %dx %s %s P %s @ bid %.2f (d %.2f, %s)" % (k, n, e, r["strike"], r["bid"], d, why))
    upnl = round(sum(p.get("upnl_aud", 0) for p in L["open"]), 2)
    day = dict(date=today, at=now_fn(), fx=fx, realized_aud=L["realized_aud"], unrealized_aud=upnl, sleeve_pnl_aud=round(L["realized_aud"] + upnl, 2), open=len(L["open"]), events=events)
    L["days"] = [x for x in L["days"] if x["date"] != today] + [day]; L["updated"] = now_fn(); atomic(LP, L)
    atomic(SP, dict(updated=L["updated"], mode="PAPER report-only (no orders)", backtest="MODELLED 2007-2026: FAIL vs pre-registered rule (see research/csp_sleeve_20261028/REPORT.md)",
                    sleeve_aud=SLEEVE_AUD, sleeve_pnl_aud=day["sleeve_pnl_aud"], realized_aud=L["realized_aud"], unrealized_aud=upnl,
                    open=[{k: p.get(k) for k in ("name", "exp", "K", "n", "credit", "mark_ask", "S_mark", "upnl_aud", "delta")} for p in L["open"]], today=events))
    return day
def summary_line():
    s = jload(SP, None)
    if not s: return "CSP sleeve (PAPER): no run yet"
    op = ", ".join("%s %s P x%s" % (p["name"], p["K"], p["n"]) for p in s.get("open", [])) or "none"
    return "CSP sleeve (PAPER, report-only): P&L A$%+.0f (realized %+.0f) · open puts: %s · as of %s" % (s["sleeve_pnl_aud"], s["realized_aud"], op, s["updated"])
if __name__ == "__main__": print(json.dumps(run(), indent=1, default=str))
