#!/usr/bin/env python3
"""worldwide_gap_book.py — mark/rebalance Worldwide Gap Sleeve. PAPER ONLY.
Rules frozen in WORLDWIDE_GAP_PREREGISTRATION.md (2026-09-16).
"""
import json, os, time
import growth_book as gb

BASE = os.path.dirname(os.path.abspath(__file__))
BOOK = os.path.join(BASE, "reports", "worldwide_gap_book.json")
UNIVERSE = ["EWJ", "IWM", "QQQ", "VXUS"]
SAFE = "IEF"  # unused while all four stay positive; kept for shape
LOOKBACK = 252
TOP = 4
REBALANCE_TD = 21
COST_BPS_SIDE = 10.0

def load():
    with open(BOOK) as f:
        return json.load(f)

def save(b):
    b["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = BOOK + ".tmp"
    with open(tmp, "w") as f:
        json.dump(b, f, indent=2)
    os.replace(tmp, BOOK)

def fetch():
    import yfinance as yf
    raw = yf.download(UNIVERSE + ["IEF"], period="2y", interval="1d",
                      auto_adjust=True, progress=False, threads=True)
    return raw["Close"].dropna(how="all")

def choose(px):
    trail = px[UNIVERSE].iloc[-1] / px[UNIVERSE].iloc[-1 - LOOKBACK] - 1.0
    ranked = trail.sort_values(ascending=False)
    picks = [t for t in ranked.index[:TOP] if float(trail[t]) > 0]
    if not picks:
        return {"IEF": 1.0}, {}
    w = {t: 1.0 / len(picks) for t in picks}
    return w, {t: round(float(trail[t]) * 100, 2) for t in ranked.index[:5]}

def equity_of(b, last):
    return float(b.get("cash") or 0) + sum(
        u * float(last.get(t, 0)) for t, u in b.get("holdings", {}).items())

def tick(force=False):
    b = load()
    px = fetch()
    last = px.iloc[-1]
    eq = equity_of(b, last)
    b["peak_equity"] = max(float(b.get("peak_equity") or eq), eq)
    b["equity"] = round(eq, 2)
    b["drawdown_pct"] = round((eq / b["peak_equity"] - 1) * 100, 3) if b["peak_equity"] else 0
    due = force or not b.get("last_rebalance_date")
    if not due:
        # trading days
        dates = [d.strftime("%Y-%m-%d") for d in px.index]
        lr = b["last_rebalance_date"]
        if lr in dates:
            due = (len(dates) - 1 - dates.index(lr)) >= REBALANCE_TD
        else:
            due = True
    if due:
        targets, top5 = choose(px)
        old = {t: u * float(last[t]) for t, u in b.get("holdings", {}).items() if t in last}
        desired = {t: eq * w for t, w in targets.items()}
        rate = COST_BPS_SIDE / 10000.0
        turnover = sum(abs(desired.get(t, 0) - old.get(t, 0)) for t in set(desired)|set(old))
        # scale to fit costs
        scale = 1.0
        if sum(desired.values()) + turnover * rate > eq and eq > 0:
            # binary search omitted; simple shrink
            scale = max(0.0, (eq) / (sum(desired.values()) + turnover * rate + 1e-9))
        desired = {t: v * scale for t, v in desired.items()}
        turnover = sum(abs(desired.get(t, 0) - old.get(t, 0)) for t in set(desired)|set(old))
        cost = turnover * rate
        new_h = {t: desired[t] / float(last[t]) for t in desired if desired[t] > 0}
        b["holdings"] = new_h
        b["entry_px"] = {t: float(last[t]) for t in new_h}
        b["cash"] = round(eq - sum(u * float(last[t]) for t, u in new_h.items()) - cost, 2)
        b["costs_paid"] = round(float(b.get("costs_paid") or 0) + cost, 2)
        b["last_rebalance_date"] = px.index[-1].strftime("%Y-%m-%d")
        b["rebalances"] = int(b.get("rebalances") or 0) + 1
        b["equity"] = round(equity_of(b, last), 2)
        print("REBALANCED", targets, "cost", round(cost, 2), "equity", b["equity"])
    else:
        print("marked equity", b["equity"], "dd", b["drawdown_pct"])
    save(b)
    return 0

if __name__ == "__main__":
    raise SystemExit(tick())
