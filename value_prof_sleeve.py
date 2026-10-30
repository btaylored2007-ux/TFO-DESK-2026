#!/usr/bin/env python3
"""Value + profitability sleeve, 20% of firm equity (Brett approved 2026-10-02, NO leverage). PAPER ONLY. STAGED TARGET.

STATUS (2026-10-02): STAGED. This module computes and LOGS the target portfolio and the dollars the sleeve may use.
It has NO execute path: it never moves cash, never writes a ledger, never opens or closes a position. Execution needs a
registered ledger + paired powder transfer + quote/cost layer (see VALUE_PROF_SLEEVE.md "What remains").

Definition = the research arm behind proposal A0 "Crypto cap $500k -> $1M, plus value 20%"
(research/engine60/code/portfolio_combo.py, series from run_valprof.py -> equity_factor.run(weights mom 0 / prof .5 / val .5)):
  * universe: CURRENT S&P 500 + S&P 400 members (research/engine60/data/universe.csv; survivorship-biased in the backtest)
  * eligible at the signal date: nominal price >= $5, 60-day median dollar volume >= $20M, 252 days of history
  * score = mean of within-sector percentile ranks of GP/Assets (financials excluded from prof) and Book/Market (equity > 0),
    each winsorised at 2.5/97.5%; SEC XBRL availability lags 90d annual flows, 60d balance sheet, 10d cover shares
  * hold: buy the top quintile; keep a held name while it stays in the top 40% (hysteresis)
  * weights: inverse 60-day volatility, per-name cap max(2%, 1/N) with capped redistribution
  * cadence: signals at the US month-end close, trade the next session close; monthly
  * sleeve size: 20% of firm equity, topped up / trimmed monthly from cash (backtest: firm 80% cap + $50k reserve; desk
    now 100% cap + $50k reserve, desk_caps.py). Buy dollars = min(20% x equity - sleeve value, invested room, powder - reserve).
Desk overlays (2026-10-02, on top of the research definition; they only REMOVE names):
  * never re-enter list: tfo_ticket.CLOSED_NAMES (e.g. RCL is an S&P 500 member and is excluded)
  * names already held in fundamental_book are NOT bought (no twin, no averaging down; existing positions untouched)
  * tfo_buy.SEMIS names excluded while the semis theme is full
Commands:
  value_prof_sleeve.py target [--write]   compute from the latest month-end in research data; --write logs
                                          reports/value_prof_sleeve/target_latest.json + target_log.jsonl
  value_prof_sleeve.py schedule            next signal date / trade date
"""
import argparse, datetime as dt, json, os, sys
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import desk_caps

RESEARCH = os.path.join(BASE, "research", "engine60", "code")
OUTDIR = os.path.join(BASE, "reports", "value_prof_sleeve")   # sub-dir; no ledger-shaped keys (see _guard_no_ledger_keys)
TOP_Q, KEEP_Q = 0.2, 0.4
WEIGHTS = {"mom": 0.0, "prof": 0.5, "val": 0.5}
MIN_PRICE, MIN_DV = 5.0, 20e6
LEDGER_KEYS = {"cash", "positions", "holdings", "desks", "equity", "nav", "marked_equity", "start_cash"}   # = book_coverage.LEDGER_HINTS
STATUS = "STAGED_TARGET_ONLY"


def cap_weights(wt, cap):
    """Capped redistribution exactly as equity_factor.run (clip-then-renormalise would breach the cap)."""
    wt = wt.copy()
    for _ in range(50):
        over = wt > cap + 1e-12
        if not over.any(): break
        excess = (wt[over] - cap).sum(); wt[over] = cap
        wt[~over] += excess * wt[~over] / wt[~over].sum()
    return wt


def sleeve_dollars(firm_equity, invested, firm_cash, powder_unreserved, sleeve_value=0.0, pct=None, cap_pct=None, reserve=None):
    """Monthly top-up/trim amount. Positive = buy (bounded by room and powder - reserve, never leverage); negative = trim to
    target (sleeve's OWN names only). Mirrors portfolio_combo.sim: mv = w*nav - hold; mv = min(mv, max(room, 0)) if mv > 0."""
    pct = desk_caps.VALUE_PROF_SLEEVE_PCT if pct is None else pct
    reserve = desk_caps.CASH_RESERVE_USD if reserve is None else reserve
    target = pct * float(firm_equity)
    mv = target - float(sleeve_value)
    room = desk_caps.invested_room(firm_equity, invested, firm_cash, cap_pct, reserve)
    room = min(room, max(0.0, float(powder_unreserved) - reserve))
    if mv > 0:
        buy = max(0.0, min(mv, room))
        binding = "target" if buy >= mv - 0.005 else ("room/powder-reserve" if room < mv else "target")
        return {"target_usd": round(target, 2), "move_usd": round(buy, 2), "shortfall_usd": round(mv - buy, 2), "room_usd": round(room, 2), "binding": binding}
    return {"target_usd": round(target, 2), "move_usd": round(mv, 2), "shortfall_usd": 0.0, "room_usd": round(room, 2), "binding": "trim" if mv < 0 else "at_target"}


def month_ends(dates):
    import pandas as pd
    s = pd.Series(dates, index=dates)
    return list(s.groupby([dates.year, dates.month]).max())


def schedule(today=None):
    """Next US month-end signal (last weekday of the month; NYSE holidays not removed) and the trade session after it."""
    today = today or dt.date.today()
    def last_weekday(y, m):
        d = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))
        while d.weekday() >= 5: d -= dt.timedelta(days=1)
        return d
    sig = last_weekday(today.year, today.month)
    if sig < today:
        y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        sig = last_weekday(y, m)
    tr = sig + dt.timedelta(days=1)
    while tr.weekday() >= 5: tr += dt.timedelta(days=1)
    return {"next_signal_us_close": sig.isoformat(), "next_trade_us_close": tr.isoformat(),
            "note": "signal at US month-end close, trade at next US session close (research cadence); NYSE holidays not removed"}


def select(adj, close, vol, spl, u, fs, d, held=(), EF=None):
    """Target weights at signal date d; same steps as equity_factor.run's month-end block (mom weight 0)."""
    import numpy as np, pandas as pd
    j = adj.index.get_loc(d)
    if j < 260: raise ValueError("need 260 sessions of history")
    rets = adj.ffill().pct_change(fill_method=None)
    dv = (close * vol).rolling(60, min_periods=40).median()
    ratio = spl.replace(0, 1.0)
    F = ratio[::-1].cumprod()[::-1].shift(-1).fillna(1.0)
    nom = close * F.reindex_like(close).fillna(1.0)
    p0, p21, p126, p252 = adj.iloc[j], adj.iloc[j - 21], adj.iloc[j - 126], adj.iloc[j - 252]
    elig = (nom.iloc[j] >= MIN_PRICE) & (dv.iloc[j] >= MIN_DV) & p252.notna() & p0.notna()
    el = elig[elig].index
    if len(el) < 50: raise ValueError("fewer than 50 eligible names")
    f = pd.DataFrame(index=el)
    f["m12"] = p21[el] / p252[el] - 1; f["m6"] = p21[el] / p126[el] - 1
    f["sector"] = u.reindex(el)["sector"].fillna("NA")
    g = lambda c: f.groupby("sector")[c].rank(pct=True)
    f["mom"] = (g("m12") + g("m6")) / 2
    a = EF.asof_value(fs, "assets", d).val; e = EF.asof_value(fs, "equity", d).val
    sh = EF.asof_value(fs, "shares", d); gp = EF.asof_value(fs, "gp", d).val
    f["gpa"] = (gp.reindex(el) / a.reindex(el)).where(a.reindex(el) > 0)
    Fe = pd.Series({t: F[t].asof(sh.loc[t, "end"]) if (t in sh.index and t in F) else 1.0 for t in el})
    mcap = sh.reindex(el).val * Fe.reindex(el) * close.iloc[j][el]
    f["bm"] = (e.reindex(el) / mcap).where(e.reindex(el) > 0)
    for c in ["gpa", "bm"]:
        lo, hi = f[c].quantile([0.025, 0.975]); f[c] = f[c].clip(lo, hi)
    f["prof"] = g("gpa"); f["val"] = g("bm")
    f.loc[f.sector.str.contains("Financ"), "prof"] = np.nan
    w = pd.DataFrame(WEIGHTS, index=f.index)
    comp = (f[["mom", "prof", "val"]] * w).sum(axis=1) / (f[["mom", "prof", "val"]].notna() * w).sum(axis=1)
    comp[f[["prof", "val"]].isna().all(axis=1)] = np.nan
    sc = comp.dropna().rank(pct=True)
    keep = [t for t in held if t in sc.index and sc[t] >= 1 - KEEP_Q]
    new = list(sc[sc >= 1 - TOP_Q].index)
    names = sorted(set(keep) | set(new))
    v = rets[names].iloc[j - 60:j + 1].std()
    wt = (1 / v).replace([np.inf], np.nan).dropna(); wt = wt / wt.sum()
    wt = cap_weights(wt, max(0.02, 1.0 / len(wt)))
    detail = f.reindex(wt.index)[["sector", "gpa", "bm", "prof", "val"]].assign(score_pct=sc.reindex(wt.index), weight=wt)
    return wt, detail, {"eligible": int(len(el)), "scored": int(len(sc))}


def overlays(names):
    """Desk exclusions: closed names, names held in fundamental_book, full semis theme. Returns {name: reason}."""
    out = {}
    try:
        from tfo_ticket import CLOSED_NAMES
    except Exception: CLOSED_NAMES = set()
    try:
        held = set(json.load(open(os.path.join(BASE, "reports", "fundamental_book.json")))["positions"])
    except Exception: held = set()
    try:
        import tfo_buy; semis = set(tfo_buy.SEMIS)
    except Exception: semis = set()
    for n in names:
        u = str(n).upper()
        if u in CLOSED_NAMES or u.split(".")[0] in CLOSED_NAMES: out[n] = "closed name (never re-enter)"
        elif n in held or u in {h.upper() for h in held}: out[n] = "already held in fundamental_book (no twin / no averaging down)"
        elif u in semis: out[n] = "semis theme full (tfo_buy.SEMIS)"
    return out


def _guard_no_ledger_keys(obj):
    bad = LEDGER_KEYS & set(obj)
    if bad: raise ValueError("refusing to write ledger-shaped keys %s (book_coverage would see an unclaimed ledger)" % sorted(bad))


def build_target():
    sys.path.insert(0, RESEARCH)
    import pandas as pd
    import equity_factor as EF
    adj, close, vol, spl = EF.prices()
    u = pd.read_csv(os.path.join(os.path.dirname(RESEARCH), "data", "universe.csv")).set_index("ticker")
    fs = EF.fundamentals(set(adj.columns))
    me = month_ends(adj.index)
    last_bar = adj.index[-1]
    sig = [d for d in me if d < last_bar or (d == last_bar and d.month != (last_bar + pd.Timedelta(days=3)).month)]
    d = sig[-1]
    prev = load_latest()
    held = list(((prev or {}).get("sleeve_names_held") or []))   # empty until the sleeve actually holds names
    wt, detail, meta = select(adj, close, vol, spl, u, fs, d, held, EF)
    excl = overlays(list(wt.index))
    exp = desk_caps.firm_exposure(exclude_books=desk_caps.SIZING_EXCLUDED_BOOKS)   # 2026-10-29 ex veteran_desk
    try:
        g = json.load(open(os.path.join(BASE, "reports", "gap_close_levered_book.json")))
        powder = float(g["cash"]) - float(g.get("reserved_usd") or 0)
    except Exception: powder = 0.0
    sz = sleeve_dollars(exp["firm_equity_usd"], exp["invested_usd"], exp["firm_cash_usd"], powder, sleeve_value=0.0)
    w_ok = wt.drop(index=list(excl), errors="ignore")
    rows = []
    for t, r in detail.iterrows():
        rows.append({"ticker": t, "sector": r.sector, "weight": round(float(r.weight), 6), "score_pct": round(float(r.score_pct), 4),
                     "gp_assets": None if pd.isna(r.gpa) else round(float(r.gpa), 4), "book_to_market": None if pd.isna(r.bm) else round(float(r.bm), 4),
                     "excluded": excl.get(t), "staged_usd": 0.0 if t in excl else round(float(r.weight) * sz["move_usd"], 2)})
    rows.sort(key=lambda x: -x["weight"])
    out = {"sleeve": "value_prof_sleeve", "status": STATUS, "paper_only": True, "leverage": "none",
           "approved": "Brett 2026-10-02: value+profitability sleeve 20% of firm equity; NO leverage; staged paper target",
           "computed_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "signal_date": d.date().isoformat(), "data_last_bar": last_bar.date().isoformat(),
           "next": schedule(), "definition": "research/engine60/code/equity_factor.py run(weights mom0/prof.5/val.5), top quintile, keep top 40%, inv-vol, cap max(2%,1/N), monthly",
           "universe_meta": meta, "n_target_names": int(len(wt)), "n_excluded": len(excl),
           "target_weight_excluded": round(float(wt.reindex(list(excl)).sum()) if excl else 0.0, 4),
           "firm": exp, "powder_unreserved_usd": round(powder, 2), "sizing": sz,
           "staged_total_usd": round(sum(r["staged_usd"] for r in rows), 2),
           "sleeve_names_held": [], "sleeve_value_usd": 0.0,
           "execution": "NONE. Staged only: no cash moved, no positions opened. Remaining: registered ledger, paired powder transfer, quotes/costs, launchd monthly job.",
           "targets": rows}
    _guard_no_ledger_keys(out)
    return out


def load_latest():
    try: return json.load(open(os.path.join(OUTDIR, "target_latest.json")))
    except Exception: return None


def write(out):
    _guard_no_ledger_keys(out)
    os.makedirs(OUTDIR, exist_ok=True)
    tmp = os.path.join(OUTDIR, ".target_latest.json.tmp")
    with open(tmp, "w") as f: json.dump(out, f, indent=1, default=str)
    os.replace(tmp, os.path.join(OUTDIR, "target_latest.json"))
    summ = {k: out[k] for k in ("computed_utc", "signal_date", "data_last_bar", "n_target_names", "n_excluded", "staged_total_usd", "status")}
    summ.update({"sizing": out["sizing"], "invested_pct": out["firm"]["invested_pct"]})
    with open(os.path.join(OUTDIR, "target_log.jsonl"), "a") as f: f.write(json.dumps(summ) + "\n")


def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="c", required=True)
    t = sp.add_parser("target"); t.add_argument("--write", action="store_true")
    sp.add_parser("schedule")
    a = ap.parse_args()
    if a.c == "schedule": print(json.dumps(schedule(), indent=1)); return
    out = build_target()
    if a.write: write(out)
    brief = {k: v for k, v in out.items() if k != "targets"}
    brief["top10"] = [(r["ticker"], r["weight"], r["excluded"]) for r in out["targets"][:10]]
    brief["excluded"] = {r["ticker"]: r["excluded"] for r in out["targets"] if r["excluded"]}
    print(json.dumps(brief, indent=1, default=str))


if __name__ == "__main__":
    main()
