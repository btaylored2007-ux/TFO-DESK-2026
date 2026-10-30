#!/usr/bin/env python3
"""TREND-CORE SHADOW profit-taking (PAPER, log-only, 2026-10-28 Brett pt1571u).

Never changes state/trend_core.json holdings/cash. Kept OUTSIDE book P&L.
Rules (shadow):
 (a) TRIM_STRETCHED: stretch = (px/SMA200 - 1) / monthly_sigma, monthly_sigma = std(daily ret, 252d)*sqrt(21).
     stretch > 2.5 -> shadow sell 1/3 of the (remaining shadow) qty; rest rides. Re-arms when stretch < 1.0.
 (b) TRAIL_WEEKLY: once per completed ISO week, if last weekly close <= peak-since-entry * (1-thr) -> shadow exit all.
     thr 15%; 3x ETFs (UPRO/TQQQ/TMF) and BTC 20%.
 (c) BTC_STOP: rule (b) on XBT/USD, labelled separately. Also SHADOW.
Shadow equity = real equity + sum(net proceeds - sold_qty*mark/fx). diff = shadow - real.
"""
import datetime as dt, json, os, sys, math
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import trend_core as tc

SHADOW = os.path.join(tc.STATE, "trend_core_shadow.json")
SLOG = os.path.join(tc.STATE, "trend_core_shadow_log.txt")
STRETCH_K = 2.5
REARM_K = 1.0
TRIM_FRAC = 1.0 / 3.0
TRAIL = {"UPRO": 0.20, "TQQQ": 0.20, "TMF": 0.20, "XBT/USD": 0.20}
TRAIL_DEFAULT = 0.15
YSYM = {"XBT/USD": "BTC-USD"}


def slog(msg):
    line = "%s %s\n" % (tc.wita(), msg)
    with open(SLOG, "a") as f:
        f.write(line)


def new_state():
    return {"book": "trend_core_shadow", "paper_only": True, "shadow_only": True, "in_book_pnl": False,
            "rules": {"stretch_k": STRETCH_K, "rearm_k": REARM_K, "trim_frac": TRIM_FRAC,
                      "trail_default": TRAIL_DEFAULT, "trail_3x_btc": 0.20},
            "pos": {}, "actions": [], "last_week_checked": None, "diff_aud": 0.0, "updated_wita": None}


def load():
    if os.path.exists(SHADOW):
        return json.load(open(SHADOW))
    return new_state()


def history(insts):
    """{inst: pandas Series of daily closes (1y+)}; bounded Yahoo call."""
    import pandas as pd, yfinance as yf
    syms = [YSYM.get(i, i) for i in insts]
    raw = tc.trend18._bounded(yf.download, syms, period="2y", interval="1d", auto_adjust=True, progress=False,
                              threads=True, timeout=tc.trend18.YF_TIMEOUT_S, cap=90)["Close"]
    out = {}
    for i, s in zip(insts, syms):
        col = raw[s] if hasattr(raw, "columns") and s in raw.columns else raw
        col = col.dropna()
        col.index = pd.to_datetime(col.index).tz_localize(None).normalize()
        out[i] = col
    return out


def stretch_of(closes, px):
    if closes is None or len(closes) < 220:
        return None, None, None
    sma = float(closes.iloc[-200:].mean())
    r = closes.pct_change().dropna().iloc[-252:]
    msig = float(r.std()) * math.sqrt(21)
    if not sma or not msig:
        return None, sma, msig
    return ((px / sma) - 1.0) / msig, sma, msig


def weekly_closes(closes):
    """Completed-week (W-FRI) closes; drop the in-progress week."""
    w = closes.resample("W-FRI").last().dropna()
    today = dt.date.today()
    if len(w) and w.index[-1].date() >= today:
        w = w.iloc[:-1]
    return w


def evaluate(L, S, hist, now_week, mark_fx):
    acts = []
    holdings = L.get("holdings") or {}
    marks = L.get("mark_px_usd") or {}
    entry_utc = None
    for f in L.get("fills") or []:
        if f.get("side") == "BUY":
            entry_utc = entry_utc or f.get("utc")
    entry_date = (L.get("entry_rebooked_utc") or entry_utc or "")[:10]
    for inst, q in holdings.items():
        q = float(q or 0)
        if q <= 0:
            continue
        px = float(marks.get(inst) or 0)
        if not px:
            continue
        P = S["pos"].setdefault(inst, {"sold_qty": 0.0, "proceeds_aud": 0.0, "trim_armed": True,
                                       "exited": False, "peak_px": px, "events": []})
        P["peak_px"] = max(float(P.get("peak_px") or px), px)
        live_qty = max(0.0, q - float(P["sold_qty"]))
        c = hist.get(inst)
        st, sma, msig = stretch_of(c, px) if c is not None else (None, None, None)
        P.update({"stretch": round(st, 3) if st is not None else None, "sma200": sma, "monthly_sigma": msig,
                  "mark": px, "real_qty": q, "shadow_qty": live_qty})
        # (a) trim when stretched
        if st is not None and not P["exited"]:
            if st > STRETCH_K and P["trim_armed"] and live_qty > 0:
                sq = live_qty * TRIM_FRAC
                acts.append(_sell(P, inst, sq, px, mark_fx, "TRIM_STRETCHED",
                                  "stretch %.2f > %.1f (SMA200 %.2f, 1m-sigma %.3f)" % (st, STRETCH_K, sma, msig)))
                P["trim_armed"] = False
            elif st < REARM_K and not P["trim_armed"]:
                P["trim_armed"] = True
                slog("REARM %s stretch %.2f < %.1f" % (inst, st, REARM_K))
        # (b)/(c) weekly trailing stop on completed-week closes since entry
        if c is not None and now_week and S.get("last_week_checked") != now_week and not P["exited"]:
            wc = weekly_closes(c)
            if entry_date:
                wc = wc[wc.index >= entry_date]
            thr = TRAIL.get(inst, TRAIL_DEFAULT)
            P["trail_thr"] = thr
            if len(wc):
                peak = max(float(wc.max()), float(P.get("entry_px") or 0) or 0,
                           float((L.get("entry_px_usd") or {}).get(inst) or 0))
                last = float(wc.iloc[-1])
                P["weekly_peak"] = peak; P["weekly_last"] = last
                P["weekly_dd_pct"] = round((last / peak - 1) * 100, 2)
                if last <= peak * (1 - thr) and live_qty > 0:
                    rule = "BTC_STOP" if inst == "XBT/USD" else "TRAIL_WEEKLY"
                    acts.append(_sell(P, inst, live_qty, px, mark_fx, rule,
                                      "weekly close %.2f <= peak %.2f x (1-%.0f%%)" % (last, peak, thr * 100)))
                    P["exited"] = True
        # diff for this inst: proceeds - what the sold qty is worth at today's mark
        sold = min(float(P["sold_qty"]), q)
        P["diff_aud"] = round(float(P["proceeds_aud"]) - sold * px / mark_fx, 2)
    if now_week:
        S["last_week_checked"] = now_week
    return acts


def _sell(P, inst, qty, px, fx, rule, why):
    kind = "crypto" if inst == "XBT/USD" else "etf"
    gross = qty * px / fx
    cost = gross * tc.COST[kind]
    P["sold_qty"] = float(P["sold_qty"]) + qty
    P["proceeds_aud"] = float(P["proceeds_aud"]) + gross - cost
    a = {"wita": tc.wita(), "utc": tc.iso(), "inst": inst, "rule": rule, "side": "SHADOW_SELL",
         "qty": round(qty, 6), "px_usd": px, "net_aud": round(gross - cost, 2), "why": why, "shadow_only": True}
    P["events"].append(a)
    slog("SHADOW %s %s qty=%.6f px=%.4f net=A$%.2f | %s" % (rule, inst, qty, px, gross - cost, why))
    return a


def completed_week_label(today=None):
    today = today or dt.date.today()
    # most recent Friday strictly before today (completed US week by WITA morning Sat)
    d = today - dt.timedelta(days=((today.weekday() - 4) % 7) or 7)
    y, w, _ = d.isocalendar()
    return "%d-W%02d" % (y, w)


def run(hist=None, now_week=None):
    L = tc.load()
    S = load()
    fx = float(L.get("audusd") or tc.audusd()[0])
    insts = [i for i, q in (L.get("holdings") or {}).items() if float(q or 0) > 0]
    if hist is None:
        try:
            hist = history(insts)
        except Exception as e:
            slog("history fail: %s (rules skipped this tick)" % str(e)[:160])
            hist = {}
    if now_week is None:
        now_week = completed_week_label() if hist else None
    acts = evaluate(L, S, hist, now_week, fx)
    S["actions"].extend(acts)
    S["diff_aud"] = round(sum(float(p.get("diff_aud") or 0) for p in S["pos"].values()), 2)
    real = float(L.get("equity_aud") or 0)
    S["real_equity_aud"] = real
    S["shadow_equity_aud"] = round(real + S["diff_aud"], 2)
    S["updated_wita"] = tc.wita(); S["updated_utc"] = tc.iso()
    S["audusd"] = fx
    tc.atomic_json(SHADOW, S)
    slog("tick diff=A$%.2f real=A$%.2f shadow=A$%.2f acts=%d week=%s" % (
        S["diff_aud"], real, S["shadow_equity_aud"], len(acts), now_week))
    # merge summary into status (no change to book fields)
    try:
        st = json.load(open(tc.STATUS))
        st["shadow"] = summary(S)
        tc.atomic_json(tc.STATUS, st)
    except Exception as e:
        slog("status merge fail: %s" % e)
    return S, acts


def summary(S=None):
    S = S or load()
    return {"shadow_only": True, "in_book_pnl": False, "diff_aud": S.get("diff_aud"),
            "real_equity_aud": S.get("real_equity_aud"), "shadow_equity_aud": S.get("shadow_equity_aud"),
            "n_actions": len(S.get("actions") or []), "last_actions": (S.get("actions") or [])[-5:],
            "last_week_checked": S.get("last_week_checked"),
            "per_inst": {k: {x: v.get(x) for x in ("stretch", "trim_armed", "exited", "weekly_dd_pct", "trail_thr",
                                                    "shadow_qty", "diff_aud")} for k, v in (S.get("pos") or {}).items()},
            "rules": "trim 1/3 if >2.5x 1m-sigma above SMA200 · weekly trail 15% (3x ETF/BTC 20%) · BTC stop · SHADOW only",
            "updated_wita": S.get("updated_wita")}


if __name__ == "__main__":
    S, a = run()
    print("TREND-CORE SHADOW diff A$%.2f (shadow %.2f vs real %.2f), %d new actions" % (
        S["diff_aud"], S["shadow_equity_aud"], S["real_equity_aud"], len(a)))
