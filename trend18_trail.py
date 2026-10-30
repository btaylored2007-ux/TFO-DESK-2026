#!/usr/bin/env python3
"""TREND18 LIVE weekly trailing stop on 3x ETFs (UPRO, TQQQ). PAPER ONLY.

Authority: Brett 28 Oct 2026 13:40 WITA approve-all #2 (was SHADOW-only in trend_core_shadow.py on TREND-CORE).
Rule (existing trend_core_shadow TRAIL_WEEKLY, 3x ETF threshold): once per completed W-FRI week, if the last completed
weekly close <= peak x (1 - 20%) -> exit the WHOLE holding. peak = max(completed weekly closes since first entry,
average entry px). Exit is a paper SELL booked through trend18.book_fill at a live regular-session IEX last trade only
(US closed -> PENDING, never a stale/backdated fill). A stopped ticker is not re-bought by trend18 rebalances until
Brett clears it (state/trend18_trail.json stopped[]). No live orders.
"""
import datetime as dt, json, os, sys
BASE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, BASE)
STATE = os.path.join(BASE, "state", "trend18_trail.json")
LOGF = os.path.join(BASE, "state", "trend18_log.txt")
TRAIL = {"UPRO": 0.20, "TQQQ": 0.20, "XBT/USD": 0.20}
# 28 Oct 2026 13:47 WITA (Brett open item B1): Trend18 BTC stop rail LIVE (paper). Reference = trend_core_shadow BTC_STOP
# (weekly W-FRI close <= peak x (1-20%), peak = max(weekly closes since entry, avg entry)). Exit = paper SELL at Kraken LIVE-tape bid.
YSYM = {"XBT/USD": "BTC-USD"}
KIND = {"XBT/USD": "crypto"}
RULE = {"XBT/USD": "BTC_STOP_20"}
MODE = "LIVE_PAPER"

def load():
    try: return json.load(open(STATE))
    except Exception:
        return {"book": "trend18_trail", "paper_only": True, "mode": MODE, "authority": "Brett 28 Oct 13:40 approve-all",
                "rule": "weekly close <= peak x (1-20%) -> exit all (3x ETF + BTC stop)", "pos": {}, "pending": [], "stopped": [],
                "exits": [], "last_week_checked": None}

def save(S):
    import trend18 as t
    S["updated_utc"] = t.iso(); t.atomic_json(STATE, S)

def stopped_tickers(path=None):
    try: return {str(x).upper() for x in json.load(open(path or STATE)).get("stopped", [])}
    except Exception: return set()

def entry_date(L, inst):
    d = [f["utc"][:10] for f in L.get("fills") or [] if f.get("inst") == inst and f.get("side") == "BUY"]
    return min(d) if d else None

def weekly_closes(closes, today=None):
    w = closes.resample("W-FRI").last().dropna()
    today = today or dt.date.today()
    if len(w) and w.index[-1].date() >= today: w = w.iloc[:-1]
    return w

def levels(L, hist, audusd, today=None):
    """{inst: peak/stop/last/dist}; hist = {inst: daily close Series}."""
    out = {}
    for inst, thr in TRAIL.items():
        q = float((L.get("holdings") or {}).get(inst) or 0)
        if q <= 0 or inst not in hist: continue
        ed = entry_date(L, inst); wc = weekly_closes(hist[inst], today)
        if ed: wc = wc[wc.index >= ed]
        avg = float((L.get("entry_px") or {}).get(inst) or 0)
        px = float((L.get("mark_px_usd") or {}).get(inst) or (hist[inst].iloc[-1]))
        peak = max([avg] + [float(x) for x in wc.values])
        stop = peak * (1 - thr)
        last_w = float(wc.iloc[-1]) if len(wc) else None
        out[inst] = dict(qty=q, thr=thr, entry_date=ed, avg_entry_usd=round(avg, 4), peak_usd=round(peak, 4),
                         stop_usd=round(stop, 4), stop_aud=round(stop / audusd, 4), mark_usd=round(px, 4), mark_aud=round(px / audusd, 4),
                         last_week_close_usd=round(last_w, 4) if last_w else None, last_week=str(wc.index[-1].date()) if len(wc) else None,
                         dist_pct=round((px / stop - 1) * 100, 2), dist_usd=round(px - stop, 4), dist_aud=round((px - stop) / audusd, 4),
                         triggered=bool(last_w is not None and last_w <= stop))
    return out

def check(L, S, hist, audusd, week_label, today=None):
    lv = levels(L, hist, audusd, today); S["pos"] = lv; new = []
    if week_label and S.get("last_week_checked") != week_label:
        for inst, r in lv.items():
            if r["triggered"] and inst not in S["pending"] and inst not in S["stopped"]:
                S["pending"].append(inst); new.append(inst)
        S["last_week_checked"] = week_label
    return new

def execute_pending(L, S, quote_fn, btc_quote_fn=None):
    """quote_fn(insts) -> {inst: {px, utc, kind}} live regular-session prices or raises. Books paper SELLs."""
    import trend18 as t
    if not S["pending"]: return []
    q = {}; etf = [i for i in S["pending"] if KIND.get(i, "etf") == "etf"]; cry = [i for i in S["pending"] if KIND.get(i) == "crypto"]
    if etf:
        try: q.update(quote_fn(etf))
        except Exception as e: t.log("TRAIL pending %s: no live price (%s) -> wait for US open" % (etf, str(e)[:80]))
    if cry and btc_quote_fn:
        try:
            b = btc_quote_fn()
            if b.get("tape") == "LIVE" and float(b.get("bid") or 0) > 0:
                for i in cry: q[i] = {"px": float(b["bid"]), "utc": b.get("utc"), "kind": "kraken_live_bid"}
            else: t.log("TRAIL pending %s: Kraken tape %s -> wait" % (cry, b.get("tape")))
        except Exception as e: t.log("TRAIL pending %s: no Kraken quote (%s) -> wait" % (cry, str(e)[:80]))
    if not q: return []
    done = []
    for inst in list(S["pending"]):
        held = float(L["holdings"].get(inst, 0) or 0); p = q.get(inst)
        if held <= 0: S["pending"].remove(inst); continue
        if not p: continue
        f = t.book_fill(L, {"order_qty": -held, "px": p["px"], "kind": KIND.get(inst, "etf"), "inst": inst}, p.get("utc") or t.iso(),
                        RULE.get(inst, "TRAIL_WEEKLY_20"), {"kind": p.get("kind"), "source": "trend18_trail"})
        if f:
            S["pending"].remove(inst); S["stopped"].append(inst); S["exits"].append(f); done.append(f)
    return done

def completed_week_label(today=None):
    today = today or dt.date.today()
    d = today - dt.timedelta(days=((today.weekday() - 4) % 7) or 7)
    y, w, _ = d.isocalendar(); return "%d-W%02d" % (y, w)

def _audusd():
    try: return float(json.load(open(os.path.join(BASE, "state", "trend_core.json"))).get("audusd") or 0.7022)
    except Exception: return 0.7022

def run(execute=True):
    import trend18 as t, desk_lock, pandas as pd, yfinance as yf
    with desk_lock.ledger_lock(t.LOCK):
        L = t.load_ledger(); S = load(); fx = _audusd()
        raw = t._bounded(yf.download, [YSYM.get(i, i) for i in TRAIL], period="1y", interval="1d", auto_adjust=False, progress=False,
                         threads=True, timeout=t.YF_TIMEOUT_S, cap=60)["Close"]
        hist = {}
        for i in TRAIL:
            y = YSYM.get(i, i)
            if y in raw.columns:
                c = raw[y].dropna(); c.index = pd.to_datetime(c.index).tz_localize(None).normalize(); hist[i] = c
        new = check(L, S, hist, fx, completed_week_label())
        for i in new: t.log("TRAIL TRIGGER %s weekly close %.2f <= stop %.2f -> PENDING exit at live price" % (i, S["pos"][i]["last_week_close_usd"], S["pos"][i]["stop_usd"]))
        if execute and S["pending"]:
            done = execute_pending(L, S, lambda s: t.fetch_alpaca("last", s), btc_quote_fn=lambda: t.kraken_btc())
            if done: t.save_ledger(L)
        S["audusd"] = fx; save(S)
    return S

if __name__ == "__main__":
    S = run(execute="--no-exec" not in sys.argv)
    for i, r in S["pos"].items():
        print("%s stop US$%.2f / A$%.2f (peak US$%.2f x0.80) mark US$%.2f -> %.2f%% above stop%s" % (
            i, r["stop_usd"], r["stop_aud"], r["peak_usd"], r["mark_usd"], r["dist_pct"], " TRIGGERED" if r["triggered"] else ""))
    print("pending", S["pending"], "stopped", S["stopped"])
