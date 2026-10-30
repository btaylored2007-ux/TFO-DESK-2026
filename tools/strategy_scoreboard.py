#!/usr/bin/env python3
"""Per-strategy scoreboard (Brett 2026-10-28 20:47 WITA: "keep strategies separate, so I can pick which one gets real money").
READ-ONLY over existing ledgers; every book is its own row, never merged. Missing data is None + a 'missing' note, never invented.
Writes reports/strategy_scoreboard.json.  PAPER ONLY."""
import csv, datetime as dt, glob, json, os
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "reports", "strategy_scoreboard.json")
WITA = dt.timezone(dt.timedelta(hours=8)); UTC = dt.timezone.utc
NOW = dt.datetime.now(UTC); NW = NOW.astimezone(WITA)
DAY0 = NW.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
WEEK0 = (NW - dt.timedelta(days=NW.weekday())).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
def P(*a): return os.path.join(BASE, *a)
def rj(p):
    try: return json.load(open(p))
    except Exception: return None
def ts(s):
    if not s: return None
    try:
        s = str(s).replace("Z", "+00:00")
        if len(s) == 10: return dt.datetime.fromisoformat(s).replace(tzinfo=WITA).astimezone(UTC)
        d = dt.datetime.fromisoformat(s[:32] if "+" in s[19:] else s[:26]); return d if d.tzinfo else d.replace(tzinfo=UTC)
    except Exception: return None
def fx():
    s = rj(P("static", "hunt48_short_status.json")) or {}
    for k in ("audusd", "q2usd", "audusd_now"):
        if isinstance(s.get(k), (int, float)) and 0.5 < s[k] < 1: return float(s[k]), "static/hunt48_short_status.json"
    t = rj(P("state", "trend_core.json")) or {}
    return (float(t["audusd"]), "state/trend_core.json") if t.get("audusd") else (None, "missing")
AUDUSD, FXSRC = fx()
def aud(x, ccy):
    if x is None: return None
    if ccy == "AUD": return round(x, 2)
    return round(x / AUDUSD, 2) if AUDUSD else None
def row(name, family, src, ccy="AUD", **k):
    r = dict(strategy=name, family=family, source=src, ledger_ccy=ccy, start_capital=None, start_capital_aud=None, pnl_today_aud=None, pnl_week_aud=None,
             pnl_since_start_aud=None, net_of_fees=None, trades=None, closed_trades=None, win_rate=None, max_drawdown_pct=None, days_live=None, status=None, open_positions=None, missing=[])
    r.update(k); r["start_capital_aud"] = aud(r["start_capital"], ccy) if r["start_capital"] is not None else None
    for f in ("start_capital", "pnl_today_aud", "pnl_week_aud", "pnl_since_start_aud", "trades", "win_rate", "max_drawdown_pct", "days_live"):
        if r[f] is None and f not in r["missing"]: r["missing"].append(f)
    return r
def days(t0): return round((NOW - t0).total_seconds() / 86400, 1) if t0 else None
def curve(path, tcol, ecol, ccy, dd_col=None, seed=None, flows=None, seed_first=False):
    """Equity curve -> today/week deltas (AUD), max DD (from dd column if present, else from curve), first ts."""
    try: R = list(csv.DictReader(open(path)))
    except Exception: return {}
    pts = [(ts(r.get(tcol)), float(r[ecol])) for r in R if r.get(ecol) not in (None, "") and ts(r.get(tcol))]
    if not pts: return {}
    if seed_first and seed is not None: pts = [(pts[0][0] - dt.timedelta(seconds=1), float(seed))] + pts   # first row already carries entry cost: start from seed
    # 2026-10-29 fix (Brett: Fundamental Today -A$102k): ledger capital transfers (e.g. HT FLAT WES.AX proceeds -> GCL powder,
    # US$72,033 out 28 Oct 23:25Z) stepped the equity curve down and were scored as day loss. Flows are capital, not P&L:
    # subtract cumulative signed flows (in +, out -) from each curve point so today/week deltas are flow-free.
    raw_last = pts[-1][1]
    if flows:
        fl = sorted(flows)
        def cum(t): return sum(v for (u, v) in fl if u <= t)
        pts = [(u, e - cum(u)) for (u, e) in pts]
    last = pts[-1][1]; last_raw = raw_last
    def at(t):
        b = [e for (u, e) in pts if u <= t]; return b[-1] if b else None
    d0, w0 = at(DAY0), at(WEEK0)
    if d0 is None and seed is not None and pts[0][0] >= DAY0: d0 = seed   # book opened today -> baseline = seed
    if w0 is None and seed is not None and pts[0][0] >= WEEK0: w0 = seed
    def jump(t):   # capital flow guard: any single step > 20% inside the window -> delta is not P&L
        w = [e for (u, e) in pts if u >= t]; return any(b > 0 and abs(a / b - 1) > 0.2 for b, a in zip(w, w[1:]))
    flow = []
    if jump(DAY0): d0 = None; flow.append("today")
    if jump(WEEK0): w0 = None; flow.append("week")
    if dd_col:
        try: mdd = min(float(r[dd_col]) for r in R if r.get(dd_col) not in (None, ""))
        except Exception: mdd = None
    else:
        pk, mdd = -1e18, 0.0
        for _, e in pts: pk = max(pk, e); mdd = min(mdd, (e / pk - 1) * 100 if pk > 0 else 0)
    return dict(first=pts[0][0], last=last, last_raw=last_raw, today=aud(last - d0, ccy) if d0 is not None else None, week=aud(last - w0, ccy) if w0 is not None else None,
                mdd=round(mdd, 2) if mdd is not None else None, last_utc=pts[-1][0].isoformat(), flow=flow)
rows = []
# ---- Hunt US (3 separate books: our picks / Claude / GPT) ----
def hunt_us(key, name, retired=False):
    p = P("state", key, "ledger.json"); L = rj(p)
    if not L: rows.append(row(name, "hunt", p, "USD", missing=["ledger unreadable"])); return
    T = L.get("trades") or []; done = [t for t in T if t.get("status") in ("CLOSED", "STOPPED")]
    net = lambda t: (t.get("realised_usd") or 0) - (t.get("fees_usd") or 0) - (t.get("borrow_usd") or 0)
    ex = lambda t: ts((t.get("exits") or [{}])[-1].get("utc"))
    done.sort(key=lambda t: ex(t) or NOW); cum, pk, mddA = 0.0, 0.0, 0.0
    for t in done: cum += net(t); pk = max(pk, cum); mddA = min(mddA, cum - pk)
    opn = [t for t in T if t.get("status") == "FILLED"]
    unrl = sum(((t["entry"] - t["last_px"]) if t.get("direction") == "short" else (t["last_px"] - t["entry"])) * (t.get("open_qty") or 0) - (t.get("fees_usd") or 0) - (t.get("borrow_usd") or 0) for t in opn if t.get("last_px") and t.get("entry"))
    t0 = min([ts(t.get("entry_utc")) for t in T if t.get("entry_utc")] or [None], key=lambda x: x or NOW)
    cap = L.get("sleeve_aud")
    real = sum(net(t) for t in done)
    rows.append(row(name, "hunt", "state/%s/ledger.json" % key, "USD", start_capital=None, start_capital_aud=None, notional_sleeve_aud=cap,
        pnl_today_aud=aud(sum(net(t) for t in done if (ex(t) or NOW) >= DAY0), "USD"), pnl_week_aud=aud(sum(net(t) for t in done if (ex(t) or NOW) >= WEEK0) + unrl, "USD"),
        pnl_since_start_aud=aud(real + unrl, "USD"), realised_aud=aud(real, "USD"), unrealised_aud=aud(unrl, "USD"), net_of_fees=True,
        trades=len(done) + len(opn), closed_trades=len(done), win_rate=round(sum(net(t) > 0 for t in done) / len(done), 3) if done else None,
        max_drawdown_pct=None, max_drawdown_aud=aud(mddA, "USD"), days_live=days(t0), open_positions=len(opn), status="retired" if retired else "running", **({"in_firm_equity": False} if retired else {}),
        notes="today = realised closes today only (open mark change not tracked); week/since include open unrealised; risk-sized (A$2.5k risk/trade) on a notional A$%s sleeve; DD in A$ on closed-trade sequence; open marks last_px_utc %s" % (cap, ",".join(sorted({str(t.get('last_px_utc')) for t in opn}))),
        missing=["start_capital (sleeve is notional; risk-sized)", "max_drawdown_pct (no NAV curve; see max_drawdown_aud)"]))
hunt_us("hunt48_short", "Hunt US — our picks"); hunt_us("hunt48_short_claude", "Hunt US — Claude picks (retired)", retired=True); hunt_us("hunt48_short_gpt", "Hunt US — GPT picks")
# ---- Hunt Asia/LSE books ----
for m, nm in (("asx", "ASX"), ("jp", "Japan"), ("hk", "Hong Kong"), ("kr", "Korea"), ("tw", "Taiwan"), ("sg", "Singapore"), ("lse", "London")):
    L = rj(P("state", "hunt48_" + m, "ledger.json")) or {}; T = L.get("trades") or []
    fills = [t for t in T if t.get("status") in ("FILLED", "CLOSED", "STOPPED")]
    t0 = min([ts(t.get("recorded_utc")) for t in T if t.get("recorded_utc")] or [None], key=lambda x: x or NOW)
    rows.append(row("Hunt %s" % nm, "hunt", "state/hunt48_%s/ledger.json" % m, "AUD", start_capital=L.get("capital_aud"),
        pnl_today_aud=0.0 if not fills else None, pnl_week_aud=0.0 if not fills else None, pnl_since_start_aud=0.0 if not fills else None, net_of_fees=True,
        trades=len(fills), closed_trades=sum(t.get("status") in ("CLOSED", "STOPPED") for t in fills), win_rate=None, max_drawdown_pct=0.0 if not fills else None,
        days_live=days(t0), open_positions=sum(t.get("status") == "FILLED" for t in fills), status="running, 0 fills" if not fills else "running",
        notes="statuses: " + ", ".join("%s %d" % (s, sum(t.get('status') == s for t in T)) for s in sorted({t.get('status') for t in T}))))
# ---- equity-curve books ----
def ledger_flows(L, book):
    """Signed capital_transfer flows (USD, + into book, - out) from ledger capital_events; capital, never P&L."""
    out = []
    for e in (L.get("capital_events") or []):
        if not isinstance(e, dict) or e.get("type") != "capital_transfer" or e.get("usd") in (None, ""): continue
        t = ts(e.get("utc")); v = abs(float(e["usd"]))
        if not t: continue
        if e.get("to") == book: out.append((t, v))
        elif e.get("from") == book: out.append((t, -v))
    return out
def eqbook(name, fam, ledger, hist, tcol, ecol, ccy, seed_key, eq_key, dd_col=None, t0_key=None, extra=None):
    L = rj(P(*ledger)) or {}; L0 = rj(P(*ledger)) or {}; C = curve(P(*hist), tcol, ecol, ccy, dd_col, L0.get(seed_key), ledger_flows(L, ledger[-1].rsplit(".", 1)[0])) if hist else {}
    seed = L.get(seed_key); eq = L.get(eq_key) if L.get(eq_key) is not None else C.get("last")
    if C.get("last_raw") is not None and L.get(eq_key) is None: eq = C["last_raw"]
    t0 = ts(L.get(t0_key)) if t0_key else C.get("first")
    r = row(name, fam, "/".join(ledger) + (" + " + "/".join(hist) if hist else ""), ccy, start_capital=seed,
            pnl_today_aud=C.get("today"), pnl_week_aud=C.get("week"), pnl_since_start_aud=aud(eq - seed, ccy) if (eq is not None and seed is not None) else None,
            net_of_fees=True, max_drawdown_pct=C.get("mdd"), days_live=days(t0 or C.get("first")), status=L.get("status") or "running", last_mark_utc=C.get("last_utc"))
    if C.get("flow"): r["missing"] += ["pnl_%s (capital flow >20%% step in window; not P&L)" % w for w in C["flow"]]
    if extra: extra(r, L)
    rows.append(r)
def ess(r, L):
    f = L.get("fills") or []; r.update(trades=len(f), closed_trades=0, open_positions=len(L.get("positions") or {}), costs_paid_aud=L.get("costs_paid_aud"),
        notes="fills = entry fills; no round-trips closed yet -> win rate n/a"); r["missing"] = [m for m in r["missing"] if m != "trades"] + ["win_rate (no closed trades)"]
eqbook("ESSENTIALS A$1m", "core", ("state", "essentials.json"), ("state", "essentials_history.csv"), "utc", "nav_aud", "AUD", "seed_aud", "nav_aud_missing", None, "created_wita", ess)
def tc(r, L):
    r.update(open_positions=len([k for k, v in (L.get("holdings") or L.get("qty") or {}).items() if v]) or None, costs_paid_aud=L.get("costs_paid_aud"), trades=None,
             notes="monthly rebalance trend book; trade count not kept per round-trip")
eqbook("TREND-CORE A$1m", "core", ("state", "trend_core.json"), ("state", "trend_core_history.csv"), "utc", "equity_aud", "AUD", "seed_aud", "equity_aud", "drawdown_pct", "created_utc", tc)
eqbook("Trend18 / DEEP18 R2", "core", ("state", "trend18.json"), ("state", "trend18_history.csv"), "utc", "equity_usd", "USD", "seed_usd", "equity", "drawdown_pct", "created_utc",
       lambda r, L: r.update(open_positions=len(L.get("holdings") or {}), notes="US$ ledger -> A$ at %s (%s); DD is NAV-per-unit" % (AUDUSD, FXSRC)))
def v2(r, L):
    C = L.get("closed") or []; n = [c.get("net_pnl_usd") for c in C if c.get("net_pnl_usd") is not None]
    r.update(trades=len(C) + len(L.get("positions") or {}), closed_trades=len(C), win_rate=round(sum(x > 0 for x in n) / len(n), 3) if n else None,
             open_positions=len(L.get("positions") or {}), notes="separate V2 fund; US$ -> A$ at %s" % AUDUSD)
eqbook("TFO V2 Systematic", "systematic", ("reports", "v2", "tfo_v2_book.json"), ("reports", "v2", "tfo_v2_firm_equity.csv"), "utc", "equity_usd", "USD", "seed_usd", "equity", "drawdown_pct", "created_utc", v2)
def fund(r, L):
    C = L.get("closed") or {}; n = [ (c.get("net_pnl_usd") if isinstance(c, dict) else None) for c in (C.values() if isinstance(C, dict) else C)]
    n = [x for x in n if x is not None]
    r.update(open_positions=len(L.get("positions") or {}), closed_trades=len(C) if C else None, trades=(len(C) + len(L.get("positions") or {})) if C else None,
             win_rate=round(sum(x > 0 for x in n) / len(n), 3) if n else None, realised_usd=L.get("realised_usd"), costs_paid_usd=L.get("costs_paid_usd"),
             notes="seed_usd includes later capital events (equity curve starts lower); since-start = equity - ledger seed")
eqbook("Fundamental Desk", "fundamental", ("reports", "fundamental_book.json"), ("reports", "fundamental_equity.csv"), "utc", "equity_usd", "USD", "seed_usd", "equity", "drawdown_pct", "opened_utc", fund)
eqbook("Fundamental SPEC sleeve", "fundamental", ("reports", "fundamental_spec_book.json"), ("reports", "fundamental_spec_equity.csv"), "utc", "equity_usd", "USD", "seed_usd", "equity", "drawdown_pct", "opened_utc", fund)
# ---- OANDA ----
S = rj(P("state", "oanda_aggro", "stats.json")) or {}
H = S.get("history") or []
rows.append(row("OANDA AGGRO", "fx/commodities", "state/oanda_aggro/stats.json", "AUD", start_capital=S.get("start_nav"),
    pnl_today_aud=None, pnl_week_aud=round(S["nav"] - S["week_open_nav"], 2) if S.get("nav") and S.get("week_open_nav") else None,
    pnl_since_start_aud=round(S["nav"] - S["start_nav"], 2) if S.get("nav") and S.get("start_nav") else None, net_of_fees=True,
    open_positions=len(S.get("open") or []), status="killed" if S.get("killed") else "running",
    notes="broker NAV incl A$%s funder top-up in start_nav; weekly history %s; closed trades not itemised in stats" % (S.get("funder_topup_aud"), [h.get("week") for h in H]),
    missing=["pnl_today (no intraday NAV history)", "trades/win_rate (see OANDA sleeve row / broker)", "max_drawdown_pct", "days_live (weekly history only: since %s)" % (H[0]["week"] if H else "?")]))
N = rj(P("state", "oanda", "net_r.json")) or {}; TR = N.get("trades") or []
rows.append(row("OANDA sleeve designs (OXL/OXF/OXP/OXB/OXT)", "fx/commodities", "state/oanda/net_r.json", "AUD",
    pnl_since_start_aud=N.get("net_aud"), net_of_fees=True, trades=N.get("n"), closed_trades=N.get("n"),
    win_rate=round(sum((t.get("realized_pl_aud") or 0) + (t.get("financing_aud") or 0) > 0 for t in TR) / len(TR), 3) if TR else None,
    pnl_week_aud=round(sum((t.get("realized_pl_aud") or 0) + (t.get("financing_aud") or 0) for t in TR if (ts(t.get("close")) or NOW) >= WEEK0), 2) if TR else None,
    days_live=days(min([ts(t.get("open")) for t in TR if ts(t.get("open"))] or [None], key=lambda x: x or NOW)), status="running",
    notes="closed trades only (asof %s); open trades excluded" % N.get("asof_utc"), missing=["start_capital (shares OANDA practice account)", "pnl_today", "max_drawdown_pct"]))
# ---- Veteran Desk: one row per seat ----
V = rj(P("reports", "veteran_desk_book.json")) or {}
VN = {}   # 2026-10-30 dashfix: same seat->trader name map as veteran_desk/dash_server.py
for _f in glob.glob(P("veteran_desk", "seats", "*.json")):
    try: _j = rj(_f) or {}; VN[os.path.basename(_f)[:-5]] = _j.get("trader_name") or _j.get("name")
    except Exception: pass
VN.update(trend="Dex Romano · Trend", price_action="Dex Romano · Price Action")
for k, s in (V.get("seats") or {}).items():
    C = s.get("closed") or []; n = [c.get("pnl_aud_net", c.get("pnl_aud")) for c in C]; n = [x for x in n if x is not None]
    hwm = s.get("pnl_hwm_aud") or 0; cur = s.get("pnl_since_inception_aud")
    rows.append(row("Veteran Desk — %s" % (VN.get(k) or k), "veteran_desk", "reports/veteran_desk_book.json seats.%s" % k, "AUD", seat=k, start_capital=s.get("seed_aud"),
        pnl_today_aud=s.get("today_pnl_aud"), pnl_week_aud=cur, pnl_since_start_aud=cur, net_of_fees=True, trades=len(C) + len(s.get("positions") or {}), closed_trades=len(C),
        win_rate=round(sum(x > 0 for x in n) / len(n), 3) if n else None, max_drawdown_pct=round(min(0, (cur - max(hwm, 0)) / s["seed_aud"] * 100), 3) if cur is not None and s.get("seed_aud") else None,
        days_live=days(ts(V.get("created_utc"))), open_positions=sum(1 for _p in (s.get("positions") or {}).values() if _p.get("status") == "open"), armed_entries=sum(1 for _p in (s.get("positions") or {}).values() if _p.get("status") == "armed"), avg_slippage_bps=s.get("avg_slippage_bps"), status="running",
        notes="desk created %s; week = since start (desk < 1 week old); DD vs P&L high-water" % V.get("created_utc")))
# ---- Crypto sleeves (closed) + others with unusable seed ----
for f, nm in (("crypto_clip_book.json", "Crypto clip (DOGE/ADA/NEAR)"), ("crypto_mid_clip_book.json", "Crypto mid clip (LINK/TAO/HYPE)"), ("crypto_liq_beta_book.json", "Crypto liq beta (BTC/ETH/XRP/SOL)"), ("gap_close_levered_book.json", "Gap-Close Levered")):
    L = rj(P("reports", f)) or {}
    rows.append(row(nm, "crypto" if "crypto" in f else "equity", "reports/" + f, "USD", status=L.get("status") or ("closed" if L.get("closed") else "running"),
        equity_usd=L.get("equity"), peak_usd=L.get("peak") or L.get("peak_equity"), deploy_basis_usd=L.get("deploy_basis_usd"), contributed_capital_usd=L.get("contributed_capital_usd"),
        max_drawdown_pct=L.get("drawdown_pct"), days_live=days(ts(L.get("created_utc"))) if L.get("created_utc") else None,
        notes="ledger seed_usd=%s is a recycled-capital accounting residual (negative/invalid as a start capital) -> P&L not computed here; see capital_events (%d)" % (L.get("seed_usd"), len(L.get("capital_events") or [])),
        missing=["start_capital (ledger seed invalid)", "pnl (needs capital_events reconciliation)", "trades", "win_rate"]))
# ---- 2026-10-29 Brett: real ledgers for forward books that the tracker showed as 0 (read-only) ----
H = os.path.expanduser("~")
E5 = rj(os.path.join(H, ".tfo_edge50", "status.json")) or {}
for bk in ("US_ML", "ASX_MOM", "CRYPTO_TR"):
    b = E5.get(bk)
    if not isinstance(b, dict): continue
    mp = os.path.join(H, ".tfo_edge50", bk + "_marks.csv"); C = curve(mp, "date", "equity_aud", "AUD", seed=100000.0)
    rows.append(row("Edge50 %s" % bk, "edge50", "~/.tfo_edge50/status.json + %s_marks.csv" % bk, "AUD", start_capital=100000.0, pnl_since_start_aud=b.get("pnl_aud"),
        pnl_today_aud=C.get("today"), pnl_week_aud=C.get("week"), max_drawdown_pct=C.get("mdd"), open_positions=b.get("n_pos"), net_of_fees=True,
        days_live=days(ts(b.get("inception"))), status="running", notes="last mark %s (daily close marks)" % b.get("last_mark")))
H10 = (rj(os.path.join(H, ".tfo_honest10", "status.json")) or {}).get("books") or {}
for bk, b in H10.items():
    sc = (b or {}).get("scorecard") or {}; C = curve(os.path.join(H, ".tfo_honest10", bk + "_equity.csv"), "date", "equity_aud", "AUD", seed=1000000.0, seed_first=True)
    try:   # inception this week (date column = local week): week = since-start, incl. day-1 entry cost
        _R = list(csv.DictReader(open(os.path.join(H, ".tfo_honest10", bk + "_equity.csv"))))
        if _R and dt.date.fromisoformat(_R[0]["date"]) >= (NW - dt.timedelta(days=NW.weekday())).date(): C["week"] = round(float(_R[-1]["equity_aud"]) - 1000000.0, 2)
    except Exception: pass
    rows.append(row("Honest10 %s (outside firm equity)" % bk, "honest10", "~/.tfo_honest10/status.json + %s_equity.csv" % bk, "AUD", start_capital=1000000.0, pnl_since_start_aud=sc.get("pnl_aud"),
        pnl_today_aud=C.get("today"), pnl_week_aud=C.get("week"), max_drawdown_pct=C.get("mdd"), open_positions=len(b.get("positions") or []), net_of_fees=True,
        days_live=days(ts(sc.get("started"))), status="running", in_firm_equity=False, notes="OUTSIDE FIRM EQUITY (Honest10 docs; not in War Room total); last mark %s; monthly rebalance (few trades by design); %s" % (sc.get("last_mark"), sc.get("verdict"))))
for tid in ("ns_cf7", "ns_s01"):
    hp = P("state", "deep19_fwd", "%s_shadow_history.csv" % tid); jj = rj(P("state", "deep19_fwd", "%s_shadow.json" % tid)) or {}
    try: R0 = list(csv.DictReader(open(hp))); st0 = float(R0[0]["value_usd"])
    except Exception: R0, st0 = [], None
    C = curve(hp, "date", "after_fee_usd", "USD", "dd_pct", seed=st0)
    rows.append(row("DEEP19 %s (shadow, $0 desk capital)" % tid, "deep19", "state/deep19_fwd/%s_shadow_history.csv" % tid, "USD", start_capital=st0,
        pnl_since_start_aud=aud(float(R0[-1]["after_fee_usd"]) - st0, "USD") if R0 and st0 else None, pnl_today_aud=C.get("today"), pnl_week_aud=C.get("week"),
        max_drawdown_pct=C.get("mdd"), net_of_fees=True, days_live=days(ts(R0[0]["date"])) if R0 else None, status="shadow",
        notes="paper shadow, not in firm equity; last bar %s" % (R0[-1]["date"] if R0 else None)))
# ---- rows we only have from strategy_tracker (marked, not recomputed) ----
ST = rj(P("static", "strategy_tracker.json")) or {}
have = ("OANDA", "TREND-CORE A$1m", "Hunt ", "ESSENTIALS A$1m", "Trend18", "  └ Hunt")
for t in ST.get("rows") or []:
    if any(str(t.get("strategy", "")).startswith(h) for h in have): continue
    if str(t.get("strategy", "")).startswith(("Honest10", "DEEP19")): continue   # real ledger rows above
    if str(t.get("strategy", "")).startswith(("OXD1", "Semis/shark")):   # 2026-10-29: no launchd job, no ledger, inventory verdict CUT -> not running
        t = dict(t, status="Not running (no job/ledger; inventory CUT)")
    rows.append(row(t.get("strategy"), "tracker_only", "static/strategy_tracker.json -> " + str(t.get("src")), "AUD", pnl_since_start_aud=t.get("pnl_aud"), trades=t.get("trades"),
        win_rate=t.get("win_rate"), open_positions=t.get("open"), status=t.get("status"), notes="not recomputed from a ledger here (tracker figure as published %s)" % ST.get("updated_wita")))
out = dict(paper_only=True, generated_utc=NOW.isoformat(), generated_wita=NW.strftime("%a %d %b %Y %H:%M WITA"), audusd=AUDUSD, audusd_source=FXSRC,
           windows=dict(today_from_wita=DAY0.astimezone(WITA).isoformat(), week_from_wita=WEEK0.astimezone(WITA).isoformat()),
           rule="Each book/strategy separate; never merged. Read-only. None = missing data (see row.missing); nothing invented.", rows=rows)
if __name__ == "__main__":
    t = OUT + ".tmp"; json.dump(out, open(t, "w"), indent=1, default=str); os.replace(t, OUT)
    for r in rows:
        f = lambda x: "—" if x is None else (f"{x:,.0f}" if isinstance(x, (int, float)) and abs(x) >= 10 else str(x))
        print("%-44s cap %-12s today %-9s week %-9s since %-10s n %-4s win %-6s mdd %-7s days %s" % (r["strategy"][:44], f(r.get("start_capital_aud")), f(r["pnl_today_aud"]), f(r["pnl_week_aud"]), f(r["pnl_since_start_aud"]), f(r["trades"]), f(r["win_rate"]), f(r["max_drawdown_pct"]), f(r["days_live"])))
