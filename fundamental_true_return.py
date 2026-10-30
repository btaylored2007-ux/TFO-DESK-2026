"""Fundamental book TRUE since-inception return (Brett 2026-10-28 13:53 WITA "fix it"). PAPER, read-only on the book.
Net contributed = $500k opening seed + every inflow - every outflow in capital_events (never the rebased seed_usd).
True P&L = equity - net contributed; this already contains the chapter_closed archived loss and the repair debits
because those hit equity but are not capital flows. TWR chains fundamental_equity.csv between flows; MWR = IRR.
Writes reports/fundamental_true_return.json. Never edits fills or the book."""
import csv, datetime as dt, json, os, time
BASE = os.path.dirname(os.path.abspath(__file__))
BOOK = os.path.join(BASE, "reports", "fundamental_book.json"); CURVE = os.path.join(BASE, "reports", "fundamental_equity.csv")
OUT = os.path.join(BASE, "reports", "fundamental_true_return.json")
OPENING_SEED_USD = 500000.0; FALLBACK_AUDUSD = 0.7022
FLOW_TYPES = ("capital_transfer", "injection", "idle_cash_deploy_in_desk", "leaderboard_prize")

def _ts(s):
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")) if s else None

def audusd(now=None):
    """AUDUSD with provenance: desk last_marks (<=72h) -> fx_last_good.json -> fallback 0.7022 (labelled)."""
    now = now or time.time()
    for name in ("last_marks.json", "fx_last_good.json"):
        p = os.path.join(BASE, "reports", name)
        try:
            v = json.load(open(p)).get("AUDUSD=X"); v = v.get("rate") if isinstance(v, dict) else v
            age = now - os.path.getmtime(p)
            if v and 0.4 < float(v) < 1.2 and age < 72 * 3600:
                return {"rate": round(float(v), 5), "source": "reports/" + name, "age_s": int(age), "fallback": False}
        except Exception:
            pass
    return {"rate": FALLBACK_AUDUSD, "source": "FALLBACK constant 0.7022 (no fresh rate)", "age_s": None, "fallback": True}

def flows(book):
    out = []
    for e in book.get("capital_events") or []:
        if e.get("type") in FLOW_TYPES:
            u = float(e.get("usd", e.get("amount_usd", 0)) or 0)
            if u: out.append((_ts(e.get("utc")), u, e.get("type"), e.get("tag")))
    return out

def twr(curve, fl):
    """Chain per-interval returns; a flow stamped in (t0,t1] is treated as at t0 (start of interval)."""
    fl = sorted([f for f in fl if f[0]], key=lambda f: f[0]); i = 0; g = 1.0; prev = None
    for t, e in curve:
        if prev is None:
            prev = (t, e); continue
        F = 0.0
        while i < len(fl) and fl[i][0] <= t:
            if fl[i][0] > prev[0]: F += fl[i][1]
            i += 1
        base = prev[1] + F
        if base > 0: g *= e / base
        prev = (t, e)
    return g - 1

def irr(cfs):
    """cfs: [(years_from_start, amount)] investor view (contribution negative). Bisection."""
    def npv(r): return sum(a / (1 + r) ** y for y, a in cfs)
    lo, hi = -0.99, 10.0
    if npv(lo) * npv(hi) > 0: return None
    for _ in range(200):
        m = (lo + hi) / 2
        if npv(lo) * npv(m) <= 0: hi = m
        else: lo = m
    return (lo + hi) / 2

def compute(book, curve):
    fl = flows(book); inflow = OPENING_SEED_USD + sum(u for _, u, _, _ in fl if u > 0); outflow = -sum(u for _, u, _, _ in fl if u < 0)
    undated = [f for f in fl if not f[0]]
    net = inflow - outflow; eq = float(book["equity"]); pnl = eq - net
    seed = float(book.get("seed_usd") or 0); ch = (book.get("chapter_closed") or [{}])[0].get("archived", {})
    t0 = _ts(book.get("opened_utc")); now = _ts(book.get("updated")) or dt.datetime.now(dt.timezone.utc)
    # undated flows: placed at the nearest dated neighbour (index order) so TWR/IRR still sees them
    dated = []; last = t0
    for e in book.get("capital_events") or []:
        if e.get("type") in FLOW_TYPES:
            u = float(e.get("usd", e.get("amount_usd", 0)) or 0); t = _ts(e.get("utc")) or last; last = t
            if u: dated.append((t, u, e.get("type"), e.get("tag")))
    tw = twr(curve, dated) if curve else None
    yrs = lambda t: max((t - t0).total_seconds(), 0) / (365.25 * 86400)
    cfs = [(0.0, -OPENING_SEED_USD)] + [(yrs(t), -u) for t, u, _, _ in dated] + [(yrs(now), eq)]
    mw = irr(cfs); span = yrs(now)
    peak_capital = OPENING_SEED_USD; run = OPENING_SEED_USD
    for t, u, _, _ in sorted(dated, key=lambda f: f[0]): run += u; peak_capital = max(peak_capital, run)
    fx = audusd()
    return {
        "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "paper_only": True,
        "method": "net_contributed = 500,000 opening seed + inflows - outflows (capital_events types %s); true P&L = equity - net_contributed" % (FLOW_TYPES,),
        "opening_seed_usd": OPENING_SEED_USD, "inflows_usd": round(inflow, 2), "outflows_usd": round(outflow, 2),
        "net_contributed_usd": round(net, 2), "n_flows": len(fl), "n_undated_flows": len(undated),
        "equity_usd": round(eq, 2), "cash_usd": round(float(book.get("cash") or 0), 2), "invested_mv_usd": round(float(book.get("long_mv_usd") or 0) + float(book.get("short_mv_usd") or 0), 2),
        "true_pnl_usd": round(pnl, 2), "true_pnl_aud": round(pnl / fx["rate"], 2),
        "true_pnl_pct_of_opening_capital": round(100 * pnl / 600000.0, 3),
        "twr_pct": None if tw is None else round(100 * tw, 3),
        "mwr_irr_annualised_pct": None if mw is None else round(100 * mw, 2),
        "mwr_period_pct": None if mw is None else round(100 * ((1 + mw) ** span - 1), 3),
        "rebased_view": {"label": "REBASED (since 19 Sep chapter reset, vs seed_usd) - NOT since inception",
                         "seed_usd": seed, "pnl_usd": round(eq - seed, 2), "pnl_aud": round((eq - seed) / fx["rate"], 2),
                         "pnl_pct": round(100 * (eq / seed - 1), 3) if seed else None},
        "seed_vs_net_contributed_gap_usd": round(seed - net, 2),
        "contributed_capital_usd_field": book.get("contributed_capital_usd"),
        "contributed_capital_usd_field_note": "STALE field: never reduced for outbound transfers; use net_contributed_usd",
        "chapter_closed_archived": ch,
        "fx": fx, "book_updated": book.get("updated"), "span_years": round(span, 4),
    }

def load_curve():
    out = []
    try:
        for r in csv.DictReader(open(CURVE)):
            try: out.append((_ts(r["utc"]), float(r["equity_usd"])))
            except Exception: pass
    except FileNotFoundError:
        pass
    return out

def main():
    r = compute(json.load(open(BOOK)), load_curve())
    tmp = OUT + ".tmp"; json.dump(r, open(tmp, "w"), indent=1); os.replace(tmp, OUT); return r

if __name__ == "__main__":
    print(json.dumps(main(), indent=1))
