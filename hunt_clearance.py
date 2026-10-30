#!/usr/bin/env python3
"""HUNT CLEARANCE PIPELINE (Brett 2026-10-27: "every trade setup reviewed by >=3 sub bots ... and another review from gpt and claude").
PAPER ONLY. Read-only on every engine/ledger. Writes only state/clearance/YYYYMMDD/ and nothing else.
Stages:
 1 SHORTLIST  plan_bench_YYYYMMDD.json of each book (main/claude/gpt) + top of fast screen (state/calendar/inputs/ranked*.csv) -> <=~50 names
 2 BREAKDOWN  per name state/clearance/D/<TICKER>.json + .md (yfinance info/financials/holders, sweep fields, Kraken for coins). Unknown = "UNKNOWN", never invented.
 3 REVIEWERS  RISK, BEHAVIOR, SPECIALIST: rules verdict (deterministic) + optional LLM veto (desk OpenAI key from launchd env, never printed).
              Final internal verdict per reviewer = rules PASS AND (LLM PASS or LLM unavailable). LLM can only veto, never rescue.
 4 EXTERNAL   external_prompt.md (cleared candidates + 3 verdicts) for GPT web (Brett) and Claude CLI; external_reviews.json holds GPT/CLAUDE verdicts.
 5 FINAL      trade only if RISK+BEHAVIOR+SPECIALIST PASS + GPT PASS + CLAUDE PASS (CLAUDE=PENDING_LOGIN temporarily waived, flagged).
              -> cleared_plan[_BOOK].json in the existing hunt48 plan schema {label,set,cut,cash_slots,note}; apply with
                 hunt48_short_paper.py apply-plan state/clearance/D/cleared_plan.json [--book NAME]
CLI: hunt_clearance.py run [--date YYYYMMDD] [--no-llm] [--no-claude]   | finalize [--date D]   (finalize re-reads external_reviews.json)
"""
import csv, datetime as dt, json, math, os, subprocess, sys, time, urllib.request
import setup_hash as SH   # review fix #2
import ibkr_costs as IC   # 2026-10-27 Brett's real IBKR fees + per-name borrow
from concurrent.futures import ThreadPoolExecutor
# 2026-10-28 Brett B3: hunt scripts honour the desk base path (env DESK_BASE, default = this folder)
BASE = os.environ.get("DESK_BASE", os.path.dirname(os.path.abspath(__file__))); WITA = dt.timezone(dt.timedelta(hours=8)); UTC = dt.timezone.utc
BOOKS = {"main": "hunt48_short", "claude": "hunt48_short_claude", "gpt": "hunt48_short_gpt"}
# audit H5 (Brett 27 Oct 19:35 WITA): RISK_TOL 1.10 -> 1.00 (no hidden +10%); risk measured at max_fill in risk_review
RISK_AUD = 2500.0; HUNT_MAIN_RISK_AUD = 5000.0; RISK_TOL = 1.00; CORR_MAX = 0.80; SHORTLIST_N = 50; COMM_USD = 1.0; SLIP = 0.0005; BORROW_PA = 0.03
CLAUDE_RETIRED = os.path.exists(os.path.join(BASE, "state", "config", "claude_retired.json")) or os.environ.get("TFO_CLAUDE_OFF") == "1"   # Brett 2026-10-29 08:33 WITA: Claude removed from every review; GPT + desk rules only
CLAUDE_BIN = None; LLM_MODEL = os.environ.get("CLEARANCE_LLM_MODEL", "gpt-4.1-mini")
U = "UNKNOWN"
def arg(k, d=None): return sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
D = arg("--date", dt.datetime.now(WITA).strftime("%Y%m%d")); OUT = os.path.join(BASE, "state", "clearance", D)
# ASX book (Brett 27 Oct 04:50 WITA): `--book asx` runs ONLY the separate A$1m ASX book, isolated in state/clearance/D/asx/ (US books' files untouched).
# Candidates = ledger ARMED (re-evaluated) + EVERY name passing asx_hunt48.review() at the A$2m floor, levels from asx_hunt48.levels(). Prices in AUD.
ASX_ONLY = arg("--book") == "asx"
if ASX_ONLY: BOOKS = {"asx": "hunt48_asx"}; OUT = os.path.join(OUT, "asx")
# ASIA books (Brett 2026-10-27 pt1481u): `--book jp|hk|sg|kr|tw` runs ONLY that separate A$1m book in state/clearance/D/<mkt>/ (others untouched).
# Candidates = asia_hunt48.clearance_plan(mkt): ledger BENCH (never-demoted) + ARMED with current levels, LOCAL ccy; ADV/risk converted to AUD.
ASIA_MKTS = ("jp", "hk", "sg", "kr", "tw", "lse")   # + lse London FTSE 350 book (Brett 2026-10-27 19:14 WITA pt1522u), same isolated flow
ASIA_MKT = arg("--book") if arg("--book") in ASIA_MKTS else None
if ASIA_MKT: BOOKS = {ASIA_MKT: f"hunt48_{ASIA_MKT}"}; OUT = os.path.join(OUT, ASIA_MKT)
os.makedirs(OUT, exist_ok=True)
def tkey(t): return t.get("ticker") or t.get("sym")
ASX_SLIP = 0.001; ASX_COMM_AUD = 10.0; ASX_THIN_ADV = 5e6; ASX_FLOOR = 2e6
def fnum(x):
    try:
        v = float(x); return None if math.isnan(v) else v
    except Exception: return None
def atomic(p, o):
    tmp = p + ".tmp"; open(tmp, "w").write(json.dumps(o, indent=1, default=str) if not isinstance(o, str) else o); os.replace(tmp, p)
def log(*a): print(dt.datetime.now(WITA).strftime("%H:%M:%S"), *a, flush=True)

# ---------- 1 SHORTLIST ----------
def load_sweep():
    rows = {}
    for f in ((f"ranked_{ASIA_MKT}.csv",) if ASIA_MKT else ()) + ("ranked_all_incl_excluded.csv", "ranked.csv", "ranked_oanda.csv", "ranked_asx.csv"):   # Asia file FIRST (its rows carry venue/fx/borrow)
        p = os.path.join(BASE, "state", "calendar", "inputs", f)
        if os.path.exists(p):
            for r in csv.DictReader(open(p)): rows.setdefault(r["sym"], r)
    return rows
def load_plans():
    plans = {}
    if ASX_ONLY: return {"asx": asx_plan()}
    if ASIA_MKT:
        import asia_hunt48 as AH; return {ASIA_MKT: AH.clearance_plan(ASIA_MKT)}
    for b, sd in BOOKS.items():
        p = os.path.join(BASE, "state", sd, f"plan_bench_{D}.json")
        if os.path.exists(p): plans[b] = json.load(open(p))
        lp = os.path.join(BASE, "state", sd, "ledger.json")   # include currently ARMED primaries (read-only)
        if os.path.exists(lp):
            L = json.load(open(lp)); have = {s["ticker"] for s in plans.get(b, {}).get("set", [])}
            plans.setdefault(b, {"label": L.get("book_label", b), "set": []})
            for t in L["trades"]:
                if t["status"] == "ARMED" and tkey(t) not in have:
                    plans[b]["set"].append({k: t.get(k) for k in ("ticker", "direction", "trigger", "max_fill", "stop", "t1", "t2", "shares", "atr", "bench", "scan_px", "scan_rank")} | {"_from_ledger": True})
    return plans
def asx_plan():
    import asx_hunt48 as A
    now = dt.datetime.now(UTC); rows = A.load_rows(); ann, earn, halted = A.load_events(now=now)
    vf = A.next_open_after_today(now); reg, revs = A.review(rows, ann, earn, halted, now, vf + dt.timedelta(hours=72))
    L = json.load(open(A.LEDGER)) if os.path.exists(A.LEDGER) else {"trades": []}; out, seen = [], set()
    def item(sym, d, lv, armed, score):
        r = rows.get(sym, {})
        return dict(ticker=sym, direction=d, trigger=lv["trigger"], max_fill=lv["max_fill"], stop=lv["stop"], t1=lv["t1"], t2=lv["t2"], shares=lv["shares"],
                    atr=fnum(r.get("atr")), bench=not armed, scan_px=fnum(r.get("px")), scan_rank=score, notional_aud=lv.get("notional_aud"), risk_aud_actual=lv.get("risk_aud_actual"),
                    cap_rule=lv.get("cap_rule"), adv_aud=fnum(r.get("adv_aud")), newly_eligible=bool((fnum(r.get("adv_aud")) or 0) < 5e6), _from_ledger=armed,
                    cost_R=lv.get("cost_R"))      # 2026-10-27: engine cost_R so SPECIALIST uses the SAME room_R (hunt48_room)
    for t in L["trades"]:
        if t["status"] == "ARMED" and t["sym"] in rows:
            lv = A.levels(rows[t["sym"]], t["direction"]) or t; out.append(item(t["sym"], t["direction"], lv, True, t.get("score"))); seen.add(t["sym"])
    for v in sorted([v for v in revs if v["passed"] and v["sym"] not in seen], key=lambda v: -v["score"]):
        lv = A.levels(rows[v["sym"]], v["dir"])
        if lv: out.append(item(v["sym"], v["dir"], lv, False, v["score"]))
    return dict(label="HUNT48_ASX A$1m (PAPER)", set=out, regime=reg, reviewed=len(revs), passed_engine=sum(v["passed"] for v in revs))
def shortlist(plans, sweep):
    names = []
    for P in plans.values():
        for s in P["set"]:
            if s["ticker"] not in names: names.append(s["ticker"])
    top = sorted((r for r in sweep.values() if r.get("excluded") not in ("True", "true", "1")), key=lambda r: -(fnum(r.get("rank_score")) or -99))
    for r in ([] if (ASX_ONLY or ASIA_MKT) else top):
        if len(names) >= max(SHORTLIST_N, len(names)) and len(names) >= SHORTLIST_N: break
        if r["sym"] not in names: names.append(r["sym"])
    return names

# ---------- 2 BREAKDOWN ----------
def asset_class(tk, sw):
    v = (sw or {}).get("venue", "")
    if v == "kraken" or "/" in tk or tk.endswith("-USD"): return "crypto"
    if v == "oanda" or "_" in tk: return "fx_commodity"
    if (sw or {}).get("etf") in ("True", "true"): return "etf"
    return "equity"
def yf_sym(tk, sw):
    if "/" in tk: return tk.split("/")[0].replace("XBT", "BTC") + "-USD"
    if (sw or {}).get("venue") == "asx" and not tk.endswith(".AX"): return tk + ".AX"
    return tk
def trend(series):
    try:
        v = [fnum(x) for x in series if fnum(x) is not None]
        if len(v) < 2: return U
        return "rising" if v[0] > v[-1] else "falling" if v[0] < v[-1] else "flat"   # yfinance columns newest first
    except Exception: return U
def breakdown(tk, sw):
    import yfinance as yf
    ac = asset_class(tk, sw); ys = yf_sym(tk, sw); b = dict(ticker=tk, asset_class=ac, yf_symbol=ys, asof_utc=dt.datetime.now(UTC).isoformat(timespec="seconds"), sources=["sweep ranked.csv"], unknowns=[])
    sw = sw or {}
    def g(d, k):
        v = d.get(k); return v if v not in (None, "", "nan") else U
    b["sweep"] = {k: sw.get(k) or U for k in ("px", "atr_pct", "adx", "rsi", "roc5", "roc20", "roc60", "m20", "m50", "m200", "relvol", "adv_usd", "rank_score", "dir", "sector", "country",
                                               "next_earn", "earn_days", "last_surprise_pct", "short_pct_float", "days_to_cover", "form4_14d", "insider_open_mkt_buys", "cat_notes", "funding", "oi", "unlock_7d_pct", "cost_pct", "venue", "adv_aud", "fx_aud", "lot", "shortable", "short_src", "borrow_pa", "borrow_avail")}
    info, closes, dates = {}, [], []
    try:
        T = yf.Ticker(ys); info = T.info or {}; b["sources"].append("yfinance.info")
        h = T.history(period="9mo", interval="1d", auto_adjust=True)
        _cs = h["Close"].dropna(); closes = [float(x) for x in _cs.tolist()]
        dates = [str(getattr(i, "date", lambda: i)()) for i in _cs.index]   # review #2/§2: dates so corr() aligns by DATE, not array position
        if len(closes) > 60:
            b["technical"] = dict(last=closes[-1], hi_52w=g(info, "fiftyTwoWeekHigh"), lo_52w=g(info, "fiftyTwoWeekLow"), ma50=sum(closes[-50:]) / 50, ma150=sum(closes[-150:]) / min(150, len(closes)),
                                  ret_5d=closes[-1] / closes[-6] - 1, ret_20d=closes[-1] / closes[-21] - 1, dist_from_20d_low=closes[-1] / min(closes[-20:]) - 1, dist_from_20d_high=closes[-1] / max(closes[-20:]) - 1)
            b["technical"]["structure"] = ("downtrend" if closes[-1] < b["technical"]["ma50"] < b["technical"]["ma150"] else "uptrend" if closes[-1] > b["technical"]["ma50"] > b["technical"]["ma150"] else "mixed")
    except Exception as e: b["unknowns"].append(f"yfinance: {str(e)[:80]}")
    b["_closes"] = closes[-61:]; b["_dates"] = dates[-61:] if len(dates) == len(closes) else []
    if ac in ("equity", "etf"):
        b["business"] = dict(name=g(info, "longName"), sector=g(info, "sector") if ac == "equity" else "ETF:" + str(g(info, "category")), industry=g(info, "industry"),
                             summary=(info.get("longBusinessSummary") or U)[:600], country=g(info, "country"))
        if ac == "etf":
            try:
                fd = yf.Ticker(ys).funds_data; th = fd.top_holdings
                b["etf_holdings"] = [dict(sym=i, name=str(r.iloc[0]), weight=float(r.iloc[-1])) for i, r in th.head(10).iterrows()]; b["sources"].append("yfinance.funds_data")
            except Exception: b["etf_holdings"] = U; b["unknowns"].append("etf holdings")
            b["etf"] = dict(aum=g(info, "totalAssets"), expense=g(info, "netExpenseRatio"), ytd=g(info, "ytdReturn"))
        else:
            fin = dict(revenue_ttm=g(info, "totalRevenue"), rev_growth_yoy=g(info, "revenueGrowth"), gross_margin=g(info, "grossMargins"), op_margin=g(info, "operatingMargins"),
                       profit_margin=g(info, "profitMargins"), earnings_growth=g(info, "earningsGrowth"), total_debt=g(info, "totalDebt"), total_cash=g(info, "totalCash"),
                       debt_to_equity=g(info, "debtToEquity"), fcf=g(info, "freeCashflow"))
            try:
                q = yf.Ticker(ys).quarterly_income_stmt
                for row, key in (("Total Revenue", "rev_trend_4q"), ("Operating Income", "opinc_trend_4q"), ("Net Income", "ni_trend_4q")):
                    fin[key] = [fnum(x) for x in q.loc[row].tolist()[:4]] if row in q.index else U
                fin["rev_direction"] = trend(fin["rev_trend_4q"]) if fin["rev_trend_4q"] != U else U; b["sources"].append("yfinance.quarterly_income_stmt")
            except Exception: b["unknowns"].append("quarterly financials")
            b["financials"] = fin
            b["valuation"] = dict(mcap=g(info, "marketCap"), trailing_pe=g(info, "trailingPE"), forward_pe=g(info, "forwardPE"), ps=g(info, "priceToSalesTrailing12Months"),
                                  pb=g(info, "priceToBook"), ev_ebitda=g(info, "enterpriseToEbitda"), vs_sector=U)   # sector medians filled in post-pass
            b["ownership"] = dict(insiders_pct=g(info, "heldPercentInsiders"), institutions_pct=g(info, "heldPercentInstitutions"), form4_14d=b["sweep"]["form4_14d"],
                                  insider_open_mkt_buys=b["sweep"]["insider_open_mkt_buys"])
        b["short_interest"] = dict(short_pct_float=g(info, "shortPercentOfFloat") if g(info, "shortPercentOfFloat") != U else b["sweep"]["short_pct_float"],
                                   short_ratio_days=g(info, "shortRatio"), borrow_fee=((IC.borrow(ys) or {}).get("fee_pa") if IC.borrow(ys) else U), borrow_ok=(IC.has_borrow(ys, 1) if IC.borrow(ys) else U), borrow_note="IBKR short-stock file + paper gateway tick 236 (state/ibkr_borrow.json)")
        if ASIA_MKT and sw.get("venue") == ASIA_MKT: b["short_interest"].update(borrow_fee=(fnum(sw.get("borrow_pa")) if sw.get("borrow_pa") else U), borrow_ok=str(sw.get("shortable")) == "True", borrow_note=str(sw.get("short_src")))
        elif IC.borrow(ys) is None: b["unknowns"].append("borrow fee/availability")
        ed = None
        try:
            et = info.get("earningsTimestamp") or info.get("earningsTimestampStart")
            if et: ed = dt.datetime.fromtimestamp(et, UTC).isoformat()
        except Exception: pass
        if (sw.get("venue") == "asx" or (ASIA_MKT and sw.get("venue") == ASIA_MKT)) and _stale_or_none(ed):   # 2026-10-28 Brett: ASX/Asia last-report-only feed -> real NEXT report date
            nx = next_report_date(ys)
            if nx: ed = nx; b["sources"].append("yfinance.get_earnings_dates (next report)")
        _v = sw.get("venue")
        if _v in ("asx", "jp", "lse") and (_v == "asx" or ASIA_MKT == _v):   # 2026-10-30 asiafix: EODHD next report (data only; none -> unchanged/UNKNOWN, gate unchanged)
            try:   # GPT review: use EODHD when Yahoo is missing/stale OR when EODHD's future date is EARLIER than Yahoo's (conservative)
                import eodhd_earnings as EE; nx = EE.next_dates(_v).get(ys)
                if _eodhd_pick(ed, nx): ed = nx; b["sources"].append("eodhd.calendar/earnings (next report)")
            except Exception: pass
        b["calendar"] = dict(next_earnings_utc=ed or b["sweep"]["next_earn"], ex_dividend=g(info, "exDividendDate"), dividend_ps=g(info, "lastDividendValue"), headlines=sw.get("headlines") or U, cat_notes=b["sweep"]["cat_notes"])
        b["liquidity"] = dict(adv_usd=fnum(sw.get("adv_usd")) or (fnum(info.get("averageVolume")) or 0) * (closes[-1] if closes else 0) or U, avg_vol=g(info, "averageVolume"), relvol=b["sweep"]["relvol"])
    elif ac == "crypto":
        b["project"] = dict(name=g(info, "name") if info.get("name") else g(info, "shortName"), description=(info.get("description") or U)[:400])
        b["supply"] = dict(circulating=g(info, "circulatingSupply"), max_supply=g(info, "maxSupply"), unlock_7d_pct=b["sweep"]["unlock_7d_pct"])
        b["derivs"] = dict(funding=b["sweep"]["funding"], open_interest=b["sweep"]["oi"])
        b["volume"] = dict(vol24_usd=sw.get("vol24_usd") or g(info, "volume24Hr"), exchange_concentration=U); b["unknowns"].append("exchange concentration (needs CoinGecko tickers)")
        try:
            pair = tk.replace("/", ""); r = json.load(urllib.request.urlopen(f"https://api.kraken.com/0/public/Ticker?pair={pair}", timeout=10))
            v = list(r.get("result", {}).values())
            if v: b["kraken"] = dict(last=fnum(v[0]["c"][0]), vol_24h_base=fnum(v[0]["v"][1]), spread=fnum(v[0]["a"][0]) - fnum(v[0]["b"][0])); b["sources"].append("kraken public ticker")
        except Exception: b["unknowns"].append("kraken ticker")
        b["liquidity"] = dict(adv_usd=fnum(sw.get("adv_usd")) or fnum(sw.get("vol24_usd")) or U)
    else:
        b["macro"] = dict(drivers=U, carry=U, events="see state/calendar (ECB/BEA 29 Oct high-impact)", note="free macro data not wired; carry from OANDA financing not read (OANDA desk untouched)")
        b["unknowns"] += ["macro drivers", "carry"]; b["liquidity"] = dict(adv_usd=fnum(sw.get("adv_usd")) or U)
    b["sector_key"] = (b.get("business", {}).get("sector") if ac in ("equity", "etf") else {"crypto": "CRYPTO", "fx_commodity": "FX"}[ac])
    if b["sector_key"] in (None, U, "ETF:UNKNOWN"): b["sector_key"] = sw.get("sector") or ("UNKNOWN:" + tk)
    if sw.get("venue") == "asx" and sw.get("sector"): b["sector_key"] = "ASX:" + sw["sector"]
    if ASIA_MKT and sw.get("venue") == ASIA_MKT and sw.get("sector"): b["sector_key"] = ASIA_MKT.upper() + ":" + sw["sector"]   # same key as asia_hunt48 1/sector   # same GICS group as asx_hunt48 1/sector rule
    if ac == "etf" and "commodit" in str(b["sector_key"]).lower(): b["sector_key"] = "COMMODITIES"
    return b
def md_of(b):
    L = [f"# {b['ticker']} ({b['asset_class']}) breakdown {D}", f"_as of {b['asof_utc']}; sources: {', '.join(b['sources'])}; UNKNOWN = not available from free data, never estimated_", ""]
    for k, v in b.items():
        if k in ("ticker", "asof_utc", "sources", "_closes", "_dates"): continue
        if isinstance(v, dict):
            L.append(f"## {k}"); L += [f"- {kk}: {vv}" for kk, vv in v.items()]
        else: L.append(f"**{k}**: {v}")
    return "\n".join(L)

# ---------- 3 REVIEWERS ----------
def corr(a, b, da=None, db=None):
    """Return-correlation. Review 27 Oct §2: when both date lists are given, align on COMMON dates (US vs ASX/Asia
    calendars, holidays and missing bars no longer shift one series against the other). Without dates: legacy tail alignment."""
    if da and db and len(da) == len(a) and len(db) == len(b):
        mb_ = dict(zip(db, b)); common = [(x, mb_[d]) for d, x in zip(da, a) if d in mb_]
        a = [x for x, _ in common]; b = [y for _, y in common]
    n = min(len(a), len(b))
    if n < 30: return None
    a, b = a[-n:], b[-n:]
    if any(not x for x in a[:-1]) or any(not y for y in b[:-1]): return None   # zero/None close -> no ratio (fail: no corr evidence)
    ra = [a[i + 1] / a[i] - 1 for i in range(n - 1)]; rb = [b[i + 1] / b[i] - 1 for i in range(n - 1)]
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb); c = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = math.sqrt(sum((x - ma) ** 2 for x in ra)); vb = math.sqrt(sum((y - mb) ** 2 for y in rb))
    return c / (va * vb) if va and vb else None
def hold_end(s): return dt.datetime.fromisoformat(s.get("time_exit_utc") or "2026-10-29T22:00:00+08:00".replace("+08:00", "+08:00"))
def risk_review(cands, BR, audusd, eligible=None, cap=None):
    """rank order; one per sector; corr<=0.8 vs already-passed; risk per trade <= book cap (US main A$5k, other books A$2.5k; +10% tol); direction balance & heat noted."""
    RISK_AUD = cap or globals()["RISK_AUD"]   # 2026-10-29 20:17 Brett: US main hunt book A$5k; other books A$2.5k
    passed, out = [], {}
    for s in cands:
        tk = s["ticker"]; b = BR[tk]; why, ok = [], True; lg = s.get("direction", "short") == "long"
        # audit H5: risk at the WORST permitted fill (max_fill), not the trigger; oversize -> resized down (lot-rounded), never waved through
        wf = s.get("max_fill") or s["trigger"]; Rps = abs(s["stop"] - wf); rusd = Rps * s["shares"]; raud = rusd / audusd; resized = None
        if raud > RISK_AUD * RISK_TOL + 0.5:
            lot = int(fnum(s.get("lot")) or 1) or 1; q = int((RISK_AUD * RISK_TOL * audusd / Rps) // lot * lot) if Rps > 0 else 0
            if q >= lot:
                why.append(f"resized {s['shares']} -> {q} sh: risk at max_fill A${raud:,.0f} > A${RISK_AUD:,.0f} cap"); s["shares"] = resized = q; raud = Rps * q / audusd
            else: ok = False; why.append(f"risk A${raud:,.0f} at max_fill > A${RISK_AUD:,.0f} cap (cannot resize)")
        if ok: why.append(f"risk A${raud:,.0f} at max_fill within A${RISK_AUD:,.0f}")
        sec = b["sector_key"]; clash = [p["ticker"] for p in passed if BR[p["ticker"]]["sector_key"] == sec]
        if clash: ok = False; why.append(f"sector cap: {sec} already taken by higher-ranked {clash[0]}")
        elif str(sec).startswith("UNKNOWN"): ok = False; why.append("sector UNKNOWN -> cannot apply one-per-sector cap")
        for p in passed:
            c = corr(b["_closes"], BR[p["ticker"]]["_closes"], b.get("_dates"), BR[p["ticker"]].get("_dates"))
            if c is not None and c > CORR_MAX and (p.get("direction", "short") == s.get("direction", "short")): ok = False; why.append(f"corr {c:.2f} with {p['ticker']} (same direction)"); break
        nd = sum(p.get("direction", "short") == s.get("direction", "short") for p in passed)
        why.append(f"direction balance: {nd} same-direction ahead (engine 3rd slot forces opposite direction)")
        out[tk] = dict(verdict="PASS" if ok else "FAIL", reasons=why, risk_aud=round(raud), **({"resized_shares": resized} if resized else {}))
        if ok and (eligible is None or tk in eligible): passed.append(s)   # only a fully-cleared name consumes a sector/corr slot
    heat = sum(out[p["ticker"]]["risk_aud"] for p in passed[:3])
    return out, dict(max_concurrent_heat_aud=heat, cleared_by_risk=len(passed))
LAST_REPORT_MAX_D = 45   # review fix #2: a last report <=45d old means the next quarterly report is >~45d out (beyond hold + 16d ext)
def _eodhd_pick(ed, nx, now=None):
    """2026-10-30 asiafix: True -> use EODHD next-report date nx instead of ed. Data only. Yes when nx exists and ed is missing,
    stale, any PAST date (an upcoming report beats a published one), or a later future date (earlier = conservative)."""
    if not nx: return False
    if _stale_or_none(ed, now): return True
    today = (now or dt.datetime.now(UTC)).astimezone(UTC).date().isoformat(); e = str(ed)[:10]
    return e < today or nx < e
def _stale_or_none(ed, now=None):
    """True if no date, or only a past report older than LAST_REPORT_MAX_D (a recent past report is kept: post-earnings path)."""
    if not ed: return True
    try:
        e = dt.datetime.fromisoformat(str(ed)); e = e if e.tzinfo else e.replace(tzinfo=UTC)
        return e < (now or dt.datetime.now(UTC)) - dt.timedelta(days=LAST_REPORT_MAX_D)
    except Exception: return True
def next_report_date(ys, now=None, dates=None):
    """Earliest scheduled report date >= now-12h from yfinance get_earnings_dates (date-only ISO, so the hold check stays fail-closed on session).
    None if nothing upcoming is published (-> truly UNKNOWN)."""
    now = now or dt.datetime.now(UTC)
    try:
        if dates is None:
            import yfinance as yf   # 2026-10-28 fix: yf was only imported inside another function -> NameError swallowed -> always None (UNKNOWN)
            df = yf.Ticker(ys).get_earnings_dates(limit=12); dates = list(df.index) if df is not None else []
        fut = sorted(d.date() for d in (dt.datetime.fromisoformat(str(x)[:19]) for x in dates) if d.date() >= (now - dt.timedelta(hours=12)).date())
        return fut[0].isoformat() if fut else None
    except Exception: return None
def behavior_review(s, b, now):
    sw = b["sweep"]; why, ok = [], True; lg = s.get("direction", "short") == "long"
    rsi, roc5, atrp = fnum(sw["rsi"]), fnum(sw["roc5"]), fnum(sw["atr_pct"])
    if rsi is not None:
        if (not lg and rsi < 30) or (lg and rsi > 70): ok = False; why.append(f"RSI {rsi:.0f}: {'oversold short' if not lg else 'overbought long'} = chasing an extended move")
        else: why.append(f"RSI {rsi:.0f} ok")
    elif not lg and (sw.get("venue") == "asx" or (ASIA_MKT and sw.get("venue") == ASIA_MKT)): ok = False; why.append("RSI UNKNOWN on a hunt48 short -> FAIL (B1 fail closed, Rules Auditor 27 Oct)")
    else: why.append("RSI UNKNOWN")
    if roc5 is not None and atrp:
        ext = (-roc5 if not lg else roc5) / atrp
        if ext > 2.5: ok = False; why.append(f"5d move {roc5:+.1f}% = {ext:.1f} ATR in trade direction (FOMO/recency)")
        else: why.append(f"5d move {roc5:+.1f}% ({ext:.1f} ATR) not extended")
    t = b.get("technical", {})
    ext5 = ((-roc5 if not lg else roc5) / atrp) if (roc5 is not None and atrp) else 0
    if t and not lg and isinstance(t.get("dist_from_20d_low"), float) and t["dist_from_20d_low"] < 0.01:
        if ext5 > 1.5: ok = False; why.append(f"at 20d low after {ext5:.1f} ATR 5d drop: shorting into an exhausted low")
        else: why.append("at 20d low (breakdown) but 5d move not extended: ok")
    if t and lg and isinstance(t.get("dist_from_20d_high"), float) and t["dist_from_20d_high"] > -0.01:
        if ext5 > 1.5: ok = False; why.append(f"at 20d high after {ext5:.1f} ATR 5d run: buying an exhausted high")
        else: why.append("at 20d high (breakout) but not extended: ok")
    sp = fnum(s.get("scan_px")); 
    if sp and s.get("trigger") and abs(sp - s["trigger"]) / sp > 0.04: why.append(f"trigger {abs(sp-s['trigger'])/sp:.1%} from scan px (note)")
    ne = b.get("calendar", {}).get("next_earnings_utc") if b.get("calendar") else None
    try:
        e = dt.datetime.fromisoformat(str(ne)) if ne and ne != U and "T" in str(ne) else (dt.datetime.fromisoformat(str(ne) + "T12:00:00+00:00") if ne and ne != U else None)
        if e and e.tzinfo is None: e = e.replace(tzinfo=UTC)
        end = dt.datetime.fromisoformat(s["time_exit_utc"]) if s.get("time_exit_utc") else now + dt.timedelta(days=3)
        xd = 2 if (ASX_ONLY or ASIA_MKT) else 16   # audit H6: US engine may extend MAX_EXT_DAYS=10 NY sessions (~16 calendar days incl. holidays)
        # EARNINGS POST-REPORT (Brett approved 27 Oct 2026 ~23:20 WITA): a report already PUBLISHED (exact timestamp >= 2h ago, same grace as
        # event_calendar / engine REPORT_GRACE_H) is a confirmed catalyst, not a pending binary event -> no block. Upcoming earnings, an exact
        # stamp inside the 2h grace, or a date-only date (session unknown) inside [now-12h, hold+ext] still FAIL (fail closed).
        pub = bool(e) and "T" in str(ne) and e + dt.timedelta(hours=2) <= now
        if e and not pub and now - dt.timedelta(hours=12) <= e <= end + dt.timedelta(days=xd):
            ok = False; why.append(f"earnings {e.date()} inside hold window (to {end.date()} +{xd}d ext)")
        elif e and pub and e >= now - dt.timedelta(hours=12): why.append(f"post-earnings: reported {e.isoformat(timespec='minutes')} (published, catalyst confirmed): may arm")
        elif e: why.append(f"earnings {e.date()} outside hold")
    except Exception:
        e = None; why.append(f"earnings date unparseable ({ne})")
    # Review fix #2 (hunt_review_20261027, Brett-approved): UNKNOWN earnings = FAIL for single stocks (was a note only).
    # A single stock clears only with a parseable date: upcoming outside the hold, or a report at most LAST_REPORT_MAX_D days old
    # (yfinance often returns the LAST report; a stale last report says nothing about the next one -> UNKNOWN). ETFs: note only.
    if b["asset_class"] == "equity":
        if e is None:
            ok = False; why.append(f"earnings date UNKNOWN on a single stock ({ne or 'none on file'}) -> FAIL (fail closed)")
        elif e < now - dt.timedelta(days=LAST_REPORT_MAX_D):
            ok = False; why.append(f"earnings UNKNOWN: only a stale last report on file ({e.date()}, >{LAST_REPORT_MAX_D}d ago; next report may fall in the hold) -> FAIL")
    elif b["asset_class"] == "etf" and (not ne or ne == U): why.append("earnings date UNKNOWN")
    return dict(verdict="PASS" if ok else "FAIL", reasons=why, next_earnings_utc=ne if ne and ne != U else None)
DIV_HOLD_D = 7   # 3 NY sessions + weekend (calendar days) for the ex-dividend check
def _exdiv(v):
    if v in (None, "", U): return None
    try:
        if isinstance(v, (int, float)) or str(v).replace(".", "", 1).isdigit(): return dt.datetime.fromtimestamp(float(v), UTC)
        x = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")); return x if x.tzinfo else x.replace(tzinfo=UTC)
    except Exception: return None
def short_dividend_cost(b, now):
    """-> (per-share cost or None = FAIL, note). Ex-div unknown/none upcoming -> 0. Ex-div in hold with no amount -> FAIL (fail closed)."""
    cal = b.get("calendar") or {}; x = _exdiv(cal.get("ex_dividend"))
    if x is None or not (now <= x <= now + dt.timedelta(days=DIV_HOLD_D)): return 0.0, None
    dv = fnum(cal.get("dividend_ps"))
    if dv is None: return None, f"ex-dividend {x.date()} inside the hold, amount unknown -> FAIL (short pays it)"
    return dv, f"ex-dividend {x.date()} inside the hold: short pays {dv:.4f}/share (added to cost)"
def specialist_review(s, b):
    why, ok = [], True; lg = s.get("direction", "short") == "long"; sw = b["sweep"]
    e, st, t1, t2, atr = s["trigger"], s["stop"], s["t1"], s.get("t2"), s.get("atr") or 0
    risk = abs(st - e)
    if (st <= e) if not lg else (st >= e): return dict(verdict="FAIL", reasons=["stop on wrong side of entry"])
    r1 = abs(e - t1) / risk if risk else 0; r2 = abs(e - t2) / risk if (risk and t2) else 0
    why.append(f"R: T1 {r1:.2f}R, T2 {r2:.2f}R")
    import hunt48_room as _HR
    _fx = bool(t2) and _HR.is_fixed(s.get("max_fill") or e, st, t2)   # Brett 2026-10-28: fixed +3%/-8% = 0.375R by design
    if _fx: why.append("fixed +3%/-8% rule: T2>=1R check replaced by room-to-target")
    elif r2 < 1.0: ok = False; why.append("T2 < 1.0R: payoff too thin for 48-72h hold")
    if atr:
        k = risk / atr
        if k < 0.5: ok = False; why.append(f"stop {k:.2f} ATR: inside noise")
        elif k > 2.5 and _fx: why.append(f"stop {k:.2f} ATR (fixed -8% stop set by funder; width not judged)")   # 2026-10-28 health (a): fixed shape not re-judged
        elif k > 2.5: ok = False; why.append(f"stop {k:.2f} ATR: too wide")
        else: why.append(f"stop {k:.2f} ATR ok")
    asx = sw.get("venue") == "asx"
    am = ASIA_MKT if (ASIA_MKT and sw.get("venue") == ASIA_MKT) else None
    px = e; q = max(s["shares"], 1); bsym = (tkey(s) + ".AX") if asx and not tkey(s).endswith(".AX") else tkey(s)
    if am:      # Asia: IBKR schedule per market (asia_markets.side_cost: HK stamp both sides, KR/TW sell tax) + 10bp/side + half tick/side + borrow
        import asia_markets as AMK
        bpa = fnum(sw.get("borrow_pa")) or 0.0
        _spb = fnum(sw.get("spread_bp")) if sw.get("spread_bp") not in (None, "") else None   # LSE quoted-spread estimate (same number the engine uses)
        cost = (AMK.side_cost(am, q, px, "buy", spread_bp=_spb) + AMK.side_cost(am, q, px, "sell", spread_bp=_spb)) / q + 2 * ASX_SLIP * px + AMK.tick(am, px) + (0 if lg else bpa * px * 3 / 365)
        if not lg and not (str(sw.get("shortable")) == "True" and (fnum(sw.get("borrow_avail")) or 0) >= q): ok = False; why.append("no confirmed borrow / not shortable (" + str(sw.get("short_src"))[:80] + ")")
        elif not lg: why.append(f"IBKR borrow {bpa:.2%}/yr")
    else:
        comm = (2 * IC.asx_side_aud(q, px)) if asx else (IC.us_side(q, px, "buy") + IC.us_side(q, px, "sell"))
        cost = (2 * (ASX_SLIP if asx else SLIP) * px + comm / q + (0 if lg else IC.borrow_pa(bsym, "asx" if asx else "us") * px * 3 / 365))
        if not lg and not IC.has_borrow(bsym, q, "asx" if asx else "us"): ok = False; why.append("no borrow (IBKR: no/insufficient shares available)")
        elif not lg: why.append(f"IBKR borrow {IC.borrow_pa(bsym, 'asx' if asx else 'us'):.2%}/yr")
    if not lg:   # review fix #8: a short pays the dividend if ex-div falls inside the hold -> added to per-share cost
        dv, why_d = short_dividend_cost(b, dt.datetime.now(UTC))
        if why_d: why.append(why_d)
        if dv is None: ok = False
        else: cost += dv
    hurdle = cost / abs(e - t1) if abs(e - t1) else 9
    # Default 25% of T1. London-only: match cost_R_max 0.30 / T1_R 0.45 ≈ 67% (Brett 2026-10-27 19:44 WITA). Other books stay 25%.
    hurdle_max = 0.25
    if am:
        _crm = AMK.MARKETS[am].get("cost_R_max")
        if _crm is not None:
            import event_calendar as _EC
            hurdle_max = float(_crm) / float(_EC.T1_R)
    why.append(f"cost hurdle {hurdle:.0%} of T1 move (cap {hurdle_max:.0%})")
    if hurdle > hurdle_max: ok = False; why.append(f"costs eat >{hurdle_max:.0%} of T1 move")
    room_R = None
    import hunt48_room as HR   # 2026-10-27 Rules Auditor 08:40: SINGLE room definition = hunt48_room.room (same number the engine writes); fail closed
    if asx or am:
        rt = dict(direction="long" if lg else "short", max_fill=s.get("max_fill") or e, stop=st, t2=t2, cost_R=fnum(s.get("cost_R")))
        rw = HR.room(rt, HR.daily(b.get("yf_symbol") or tkey(s)))
    else:   # audit H2+M4 (Brett 27 Oct 19:35 WITA): US books too - room at max_fill, cost_R at max_fill <= 0.15, >= 200 daily bars, fail closed
        mf = fnum(s.get("max_fill")) or e; Rm = abs(mf - st)
        cm = (2 * SLIP * mf + (IC.us_side(q, mf, "buy") + IC.us_side(q, mf, "sell")) / q + (0 if lg else IC.borrow_pa(bsym, "us") * mf * 3 / 365))
        rt = dict(direction="long" if lg else "short", max_fill=mf, stop=st, t2=t2, cost_R=(cm / Rm) if Rm else None)
        rw = HR.room(rt, HR.daily(b.get("yf_symbol") or tkey(s)), max_cost_R=HR.US_MAX_COST_R, min_bars=HR.US_MIN_BARS)
    room_R = rt["room_R"]
    why.append(f"room_R {room_R:.2f}R = {rt['room_basis']}")
    if rw: ok = False; why.append(("ROOM GATE (+3% target: S/R before target or cost >= target): " if rt.get("room_rule") else "ROOM GATE (legacy <1R): ") + rw)
    adv = (b.get("liquidity") or {}).get("adv_usd"); adva = fnum(sw.get("adv_aud"))
    if (asx or am) and adva:      # ASX/Asia book: A$2m floor; position <= 2% ADV if ADV<A$5m else 5% (Brett 27 Oct 04:50 WITA)
        part = s["shares"] * px * ((fnum(sw.get("fx_aud")) or 1) if am else 1) / adva; lim = 0.02 if adva < ASX_THIN_ADV else 0.05
        if adva < ASX_FLOOR: ok = False; why.append(f"ADV A${adva/1e6:.1f}m < A$2m floor")
        elif part > lim * 1.001: ok = False; why.append(f"position {part:.2%} of ADV > {lim:.0%} cap")
        else: why.append(f"ADV A${adva/1e6:.1f}m{' (thin, 2% cap)' if adva < ASX_THIN_ADV else ''}, pos {part:.2%}")
    elif isinstance(adv, (int, float)):
        part = s["shares"] * px / adv if adv else 9
        if adv < 20e6: ok = False; why.append(f"ADV ${adv/1e6:.1f}m < $20m")
        elif part > 0.02: ok = False; why.append(f"position {part:.1%} of ADV")
        else: why.append(f"ADV ${adv/1e6:.0f}m, pos {part:.2%}")
    else: why.append("ADV UNKNOWN")
    if b["asset_class"] in ("equity", "etf") and not lg:
        si = fnum((b.get("short_interest") or {}).get("short_pct_float"))
        if si is not None:
            si = si * 100 if si < 1 else si
            if si > 20: ok = False; why.append(f"short interest {si:.0f}% float: squeeze risk / hard borrow")
            else: why.append(f"SI {si:.1f}% float ok")
        else: why.append("SI UNKNOWN; borrow per IBKR file (see above)")
        tech = b.get("technical", {}).get("structure")
        if tech == "uptrend": ok = False; why.append("short against daily uptrend (px>50d>150d)")
        elif tech: why.append(f"daily structure {tech}")
    if b["asset_class"] == "crypto":
        f = fnum(sw.get("funding"))
        if f is not None and ((not lg and f < -0.0005) or (lg and f > 0.0005)): ok = False; why.append(f"funding {f} crowded in trade direction")
        u = fnum(sw.get("unlock_7d_pct"))
        if u and u > 2 and lg: ok = False; why.append(f"unlock {u}% in 7d vs long")
    return dict(verdict="PASS" if ok else "FAIL", reasons=why, room_R=room_R)

def openai_key():
    k = os.environ.get("OPENAI_API_KEY")
    if not k:
        try: k = subprocess.run(["launchctl", "getenv", "OPENAI_API_KEY"], capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception: k = None
    return k or None
# 2026-10-27 audit: when OPENAI_API_KEY is absent, fill external GPT verdicts via the logged-in Codex CLI
# (ChatGPT app). Same prompt language as external_prompt.md; PASS/FAIL line parse; fail closed to PENDING.
CODEX_BIN = None  # removed 2026-10-30 22:29 (Brett): Grok only
CODEX_MODEL = os.environ.get("CLEARANCE_CODEX_MODEL", "gpt-6-astra")
CODEX_EFFORT = os.environ.get("CLEARANCE_CODEX_EFFORT", "ultra")
CODEX_TIMEOUT_S = int(os.environ.get("CLEARANCE_CODEX_TIMEOUT_S", "120"))   # 2026-10-28 20:47 WITA Brett trim advisory: 900 -> 120s; timeout -> PENDING (defers to other reviewer)
GPT_STATE = {"status": "not_tried", "source": None, "n": 0, "err": None}
SETUP_ONLY_RULES = ("Judge ONLY setup quality: trend, catalyst, volume, liquidity, chart structure and event risk (earnings, news, squeeze, correlation). "
    "The target/stop shape (+3% target, -8% stop, 48-72h exit) is FIXED BY THE FUNDER and is NOT yours to judge: do NOT assess or mention "
    "the target/stop shape, R-multiple, reward:risk or break-even win rate, and never FAIL a name for it. "
    "A FAIL must cite a specific setup reason (trend, catalyst, volume, liquidity, structure or event risk). Say UNKNOWN where you cannot tell.")
def setup_reason(reason):
    """True if a FAIL reason cites a setup factor. Shape-only FAILs (R-multiple, reward:risk, break-even, target/stop) are not valid."""
    import re
    r = (reason or "").lower()
    setup = re.search(r"trend|catalyst|volume|liquid|adv|spread|structure|chart|support|resist|breakout|breakdown|range|gap|momentum|rsi|overextend|extended|chase|earnings|event|news|squeeze|borrow|correlat|halt|dividend|guidance|downgrade|upgrade|sector|regime|macro|volatil|atr|fundamental|thesis|weak|strong|thin|illiquid|risk-off|selloff|sell-off|downtrend|uptrend|exhaust", r)
    return bool(setup)
def parse_gpt_verdicts(text):
    """Parse `BOOK TICKER PASS|FAIL reason` lines (case-insensitive book/ticker; book in BOOKS keys). Returns {book:{tk:{GPT,reason}}}."""
    import re
    out = {}
    for line in (text or "").splitlines():
        ln = re.sub(r"[`*]", "", line).strip().lstrip("-•>0123456789.) ").strip()   # 2026-10-28 bug #2: tolerate markdown bullets/backticks/bold/numbering
        m = re.match(r"^\s*([A-Za-z0-9_]+)\s*[:|]?\s+([A-Za-z0-9./_-]+)\s*[:|]?\s+(PASS|FAIL)\b\s*[:|—-]?\s*(.*)$", ln, re.I)
        if not m: continue
        book, tk, verd, reason = m.group(1).lower(), m.group(2).upper(), m.group(3).upper(), (m.group(4) or "").strip()
        if book not in BOOKS: continue
        if verd == "FAIL" and not setup_reason(reason): verd, reason = "UNKNOWN", "ignored FAIL (no setup reason; shape is funder-fixed): " + reason
        out.setdefault(book, {})[tk] = {"GPT": verd, "reason": reason[:300]}
    return out
def gpt_via_codex(prompt_text, out_dir=None):
    """Run Codex CLI on the external-review prompt. Returns parsed verdicts or {} on any failure (fail closed). Never prints keys."""
    GPT_STATE.update(status="codex_try", source="codex", n=0, err=None)
    if "--no-llm" in sys.argv:
        GPT_STATE["status"] = "disabled (--no-llm)"; return {}
    out_dir = out_dir or OUT
    os.makedirs(out_dir, exist_ok=True)
    try:   # tfo_grokqueue_20261030 Brett 22:29 WITA: Codex removed; reviewer goes to the Grok Bot file queue
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from veteran_desk import llm_backend as _LB
        full = (prompt_text or "") + "\n\n" + SETUP_ONLY_RULES + "\n\nIMPORTANT: Reply with ONLY lines of the form `BOOK TICKER PASS|FAIL reason` (one per candidate). No preamble."
        text, rid, st = _LB.grok_call("hunt_gpt_review", full, CODEX_TIMEOUT_S, seat="hunt_clearance_gpt", desk="hunt")
        if st not in ("ok", "reused"):
            GPT_STATE.update(status="PENDING", source="grok_queue", err=f"awaiting_grok {rid}"); return {}
        open(os.path.join(out_dir, "gpt_grok_raw.md"), "w").write(text)
        parsed = parse_gpt_verdicts(text)
        GPT_STATE.update(status="ok (grok)" if parsed else "PENDING", source="grok_queue", n=sum(len(v) for v in parsed.values()),
                         err=None if parsed else "no PASS/FAIL lines parsed")
        return parsed
    except Exception as e:
        GPT_STATE.update(status="PENDING", err=f"{type(e).__name__}: {e}"[:200]); return {}
def apply_gpt_verdicts(ext, parsed):
    """Write GPT PASS/FAIL into external_reviews in place; leave CLAUDE and unset books alone. Returns n applied."""
    n = 0
    for book, rows in (parsed or {}).items():
        for tk, v in rows.items():
            slot = ext.setdefault(book, {}).setdefault(tk, {"GPT": "PENDING", "CLAUDE": "PENDING"})
            if slot.get("GPT") in (None, "PENDING"):
                slot["GPT"] = v["GPT"]
                if v.get("reason"): slot["GPT_reason"] = v["reason"]
                n += 1
    return n
LLM_STATE = {"status": "not_tried"}
def llm_review(role, items):
    """one batched call per reviewer. returns {ticker:{verdict,reason}} or None. key never printed/logged."""
    if "--no-llm" in sys.argv: LLM_STATE["status"] = "disabled (--no-llm)"; return None
    if LLM_STATE["status"].startswith(("401", "error", "no key")): return None
    # tfo_grokall_20261030 (Brett 22:29 WITA: everything goes to Grok): OpenAI API path removed; batched review goes through the Grok Bot file queue
    brief = {"RISK": "portfolio risk officer: correlation, sector concentration, direction balance, heat, per-trade risk cap A$5k on the US main hunt book, A$2.5k on every other hunt book (Brett 2026-10-29 20:17)",
             "BEHAVIOR": "trading-psychology reviewer: chasing, FOMO, recency bias, extended moves, earnings or binary events inside a 48-72h hold",
             "SPECIALIST": "setup specialist (equities, crypto, FX): entry trigger quality, stop logic vs structure/ATR, R multiples, cost & borrow hurdle"}[role]
    prompt = (f"You are the {brief}. PAPER desk, 48-72h holds. For each candidate give PASS or FAIL with one short reason. Use only the data given; if critical data is UNKNOWN say so. "
              "Reply with ONE JSON object {TICKER:{\"verdict\":\"PASS|FAIL\",\"reason\":\"...\"}} and nothing else.\nCANDIDATES:\n" + json.dumps(items, default=str))
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from veteran_desk import llm_backend as LB
        out, rid, st = LB.grok_call("grok_queue", prompt, 600, "high", seat="hunt_clearance_" + role.lower(), desk="hunt_clearance")
        if st not in ("ok", "reused"): LLM_STATE["status"] = f"{st} (grok queue {rid}) -> rules-only"; return None
        t = out.strip(); t = t[t.find("{"): t.rfind("}") + 1]
        LLM_STATE["status"] = "ok (grok_queue)"; return json.loads(t)
    except Exception as e: LLM_STATE["status"] = f"error {type(e).__name__} -> rules-only"; return None

def claude_probe():
    return "RETIRED", "Claude retired by Brett 2026-10-29 08:33 WITA"  # 2026-10-29 Brett: Claude retired (fix: run() unpacks (status, note))
    if CLAUDE_RETIRED: return "RETIRED", "Claude retired by Brett 2026-10-29 08:33 WITA (state/config/claude_retired.json)"
    if "--no-claude" in sys.argv or not os.path.exists(CLAUDE_BIN): return "PENDING_LOGIN", "not run / binary missing"
    try:
        r = subprocess.run([CLAUDE_BIN, "-p", "--model", "opus", "Reply with the single word OK"], capture_output=True, text=True, timeout=90)
        txt = (r.stdout + r.stderr)[:300]
        if r.returncode == 0 and "OK" in r.stdout: return "READY", txt.strip()
        return "PENDING_LOGIN", txt.strip().replace("\n", " ")
    except Exception as e: return "PENDING_LOGIN", str(e)[:200]

# ---------- driver ----------
def run():
    t0 = time.time(); now = dt.datetime.now(UTC)
    sweep = load_sweep(); plans = load_plans(); names = shortlist(plans, sweep)
    if ASX_ONLY: atomic(os.path.join(OUT, "asx_plan_input.json"), plans["asx"])
    if ASIA_MKT: atomic(os.path.join(OUT, f"{ASIA_MKT}_plan_input.json"), plans[ASIA_MKT])
    log(f"shortlist {len(names)} names (plans {sum(len(p['set']) for p in plans.values())} rows, sweep {len(sweep)})")
    BR = {}
    def job(tk):
        try: return tk, breakdown(tk, sweep.get(tk))
        except Exception as e: return tk, dict(ticker=tk, asset_class="UNKNOWN", sector_key="UNKNOWN:" + tk, sweep={}, _closes=[], unknowns=[f"breakdown failed {e}"], sources=[], asof_utc=now.isoformat())
    with ThreadPoolExecutor(8) as ex:
        for tk, b in ex.map(job, names): BR[tk] = b
    # valuation vs sector (median of shortlisted peers with same sector)
    from statistics import median
    for b in BR.values():
        v = b.get("valuation")
        if not v: continue
        peers = [fnum(x["valuation"]["forward_pe"]) for x in BR.values() if x.get("valuation") and x["sector_key"] == b["sector_key"] and fnum(x["valuation"]["forward_pe"])]
        fpe = fnum(v["forward_pe"])
        v["vs_sector"] = (f"fwd PE {fpe:.1f} vs shortlist-sector median {median(peers):.1f} (n={len(peers)})" if fpe and len(peers) >= 3 else "UNKNOWN (fewer than 3 sector peers in shortlist)")
    for tk, b in BR.items():
        atomic(os.path.join(OUT, tk.replace("/", "_") + ".json"), {k: v for k, v in b.items() if k not in ("_closes", "_dates")}); atomic(os.path.join(OUT, tk.replace("/", "_") + ".md"), md_of(b))
    log(f"breakdowns written {len(BR)} ({time.time()-t0:.0f}s)")
    cl_status, cl_note = claude_probe(); log("claude CLI:", cl_status)
    results = {}
    for book, P in plans.items():
        L = json.load(open(os.path.join(BASE, "state", BOOKS[book], "ledger.json")))
        audusd = 1.0 if book == "asx" else ((__import__("asia_hunt48").local_per_aud(book) or 1.0) if ASIA_MKT else L.get("audusd_armed", 0.70)); te = {tkey(t): t.get("time_exit_utc") for t in L["trades"]}   # ASX levels already AUD
        cands = sorted([dict(s, time_exit_utc=te.get(s["ticker"])) for s in P["set"] if s.get("trigger") and s.get("stop")], key=lambda s: (bool(s.get("bench")), -(fnum(s.get("scan_rank")) or 0)))
        BV = {s["ticker"]: behavior_review(s, BR[s["ticker"]], now) for s in cands}; SV = {s["ticker"]: specialist_review(s, BR[s["ticker"]]) for s in cands}
        rk, meta = risk_review(cands, BR, audusd, cap=(HUNT_MAIN_RISK_AUD if book == "main" else RISK_AUD), eligible={tk for tk in BV if BV[tk]["verdict"] == "PASS" and SV[tk]["verdict"] == "PASS"})
        rows = {}
        for s in cands:
            tk = s["ticker"]
            rows[tk] = dict(ticker=tk, direction=s.get("direction", "short"), bench=bool(s.get("bench")), setup={k: s.get(k) for k in ("trigger", "max_fill", "stop", "t1", "t2", "shares", "atr", "notional_aud", "risk_aud_actual", "cap_rule", "adv_aud", "newly_eligible") if k in s},
                            RISK=rk[tk], BEHAVIOR=BV[tk], SPECIALIST=SV[tk], setup_hash=SH.setup_hash(s))   # review fix #2: verdict bound to these levels
        for role in ("RISK", "BEHAVIOR", "SPECIALIST"):
            items = {tk: dict(direction=r["direction"], setup=r["setup"], rules=r[role], sector=BR[tk]["sector_key"], sweep={k: BR[tk]["sweep"].get(k) for k in ("rsi", "roc5", "roc20", "atr_pct", "adx", "short_pct_float", "next_earn")},
                              technical=BR[tk].get("technical"), unknowns=BR[tk].get("unknowns")) for tk, r in rows.items()}
            lv = llm_review(role, items)
            for tk, r in rows.items():
                l = (lv or {}).get(tk)
                r[role]["llm"] = l if l else ("UNAVAILABLE: " + LLM_STATE["status"])
                if l and str(l.get("verdict", "")).upper() == "FAIL" and r[role]["verdict"] == "PASS": r[role]["verdict"] = "FAIL"; r[role]["reasons"].append("LLM veto: " + str(l.get("reason"))[:200])
        for r in rows.values(): r["internal"] = "PASS" if all(r[k]["verdict"] == "PASS" for k in ("RISK", "BEHAVIOR", "SPECIALIST")) else "FAIL"
        results[book] = dict(label=P.get("label", book), risk_meta=meta, rows=rows, plan_src=f"state/{BOOKS[book]}/plan_bench_{D}.json (+ ARMED ledger primaries)")
        for tk, r in rows.items(): atomic(os.path.join(OUT, f"verdict_{book}_{tk.replace('/','_')}.json"), r)
    meta = dict(date=D, run_utc=now.isoformat(timespec="seconds"), runtime_s=round(time.time() - t0, 1), llm=LLM_STATE["status"], claude=cl_status, claude_note=cl_note[:300], shortlist=names)
    atomic(os.path.join(OUT, "internal_verdicts.json"), dict(meta=meta, books=results))
    write_external(results, meta); finalize(); log(f"done {time.time()-t0:.0f}s")
def write_external(results, meta):
    ext_p = os.path.join(OUT, "external_reviews.json")
    ext = json.load(open(ext_p)) if os.path.exists(ext_p) else {}
    L = [f"# External clearance review {D} (PAPER desk, 48-72h US-session swing setups)", "",
         "You are an independent reviewer. For EACH candidate below reply exactly one line: `BOOK TICKER PASS|FAIL reason`.",
         SETUP_ONLY_RULES,   # 2026-10-28 07:36 Brett: setup-quality only; shape fixed by funder
         f"Internal LLM status: {meta['llm']}. Breakdowns: state/clearance/{D}/<TICKER>.md", ""]
    for book, R in results.items():
        L.append(f"## Book {book} ({R['label']}) - risk meta {R['risk_meta']}")
        for tk, r in R["rows"].items():
            if r["internal"] != "PASS" and not (ASX_ONLY or ASIA_MKT): continue   # 2026-10-28 bug #2: ASX/Asia prompt listed 0 names when the desk failed all -> GPT replied nothing (PENDING). List every row; desk verdict shown.
            ev = ext.setdefault(book, {}).setdefault(tk, {"GPT": "PENDING", "CLAUDE": "RETIRED" if meta["claude"] == "RETIRED" else ("PENDING_LOGIN" if meta["claude"] == "PENDING_LOGIN" else "PENDING")})
            if r.get("setup_hash") and ev.get("setup_hash") and ev["setup_hash"] != r["setup_hash"]:   # review fix #2: levels changed -> old external verdicts void
                ev.update(GPT="PENDING", CLAUDE="RETIRED" if meta["claude"] == "RETIRED" else ("PENDING_LOGIN" if meta["claude"] == "PENDING_LOGIN" else "PENDING"), voided=f"setup changed {ev['setup_hash']} -> {r['setup_hash']}")
            if r.get("setup_hash"): ev["setup_hash"] = r["setup_hash"]
            b = json.load(open(os.path.join(OUT, tk.replace("/", "_") + ".json")))
            L.append(f"### {book} {tk} {r['direction'].upper()} setup {r['setup']} (desk {r['internal']})")
            for k in ("RISK", "BEHAVIOR", "SPECIALIST"): L.append(f"- {k}: {r[k]['verdict']} - {'; '.join(r[k]['reasons'])}")
            L.append(f"- fundamentals: {json.dumps({k: b.get(k) for k in ('business', 'financials', 'valuation', 'short_interest', 'calendar', 'technical', 'etf_holdings', 'project', 'supply', 'macro') if b.get(k)}, default=str)[:1500]}")
        n = sum(r["internal"] == "PASS" for r in R["rows"].values()); L.append(f"_{n} of {len(R['rows'])} cleared internally_\n")
    prompt = "\n".join(L); atomic(os.path.join(OUT, "external_prompt.md"), prompt)
    # Fill GPT via Codex when the OpenAI API key is absent (2026-10-27 audit). Fail closed: leave PENDING.
    if not openai_key() and "--no-llm" not in sys.argv:
        parsed = gpt_via_codex(prompt, OUT)
        n = apply_gpt_verdicts(ext, parsed)
        log(f"gpt codex: {GPT_STATE['status']} applied={n} err={GPT_STATE.get('err')}")
        meta["gpt"] = dict(GPT_STATE)
    else:
        meta["gpt"] = dict(status="api_key_present_skip_codex" if openai_key() else "disabled", source="openai_api" if openai_key() else None)
    if meta.get("claude") == "READY":   # 2026-10-28: Claude actually reviews (was probe only)
        n = apply_claude_verdicts(ext, claude_review(prompt, OUT)); log(f"claude: {CLAUDE_STATE['status']} applied={n}"); meta["claude_review"] = dict(CLAUDE_STATE)
    atomic(ext_p, ext)
AI_FAIL = ("FAIL", "FAILED", "VETO", "BLOCK", "BLOCKED", "REJECT")
def ai_ok(internal, g, c):
    """2026-10-28 07:16 WITA Brett: desk clearance PASS AND neither AI reviewer FAIL AND at least one AI reviewer PASS.
    An unavailable reviewer (PENDING / PENDING_LOGIN / missing) defers to the other's verdict; both unavailable -> False (fail closed)."""
    # 2026-10-28 07:36 Brett OVERRIDE 'make them overrule it, don't cancel trades': GPT/Claude are ADVISORY ONLY on every hunt book.
    # Desk PASS (every gate) -> CLEARED whatever the AI says; verdicts are recorded on the trade as ai_gpt / ai_claude.
    return internal == "PASS"
def parse_claude_verdicts(text):
    out = {}
    for book, rows in parse_gpt_verdicts(text).items():
        for tk, v in rows.items(): out.setdefault(book, {})[tk] = {"CLAUDE": v["GPT"], "reason": v.get("reason", "")}
    return out
CLAUDE_STATE = {"status": "not_tried"}
CLAUDE_TIMEOUT_S = int(os.environ.get("CLEARANCE_CLAUDE_TIMEOUT_S", "120"))   # 20:47 trim: 300 -> 120s   # 2026-10-28 health (d): 900 -> 300s; expired login/hang -> PENDING, GPT + desk carry on
def claude_review(prompt_text, out_dir=None):
    CLAUDE_STATE.update(status="RETIRED"); return {}  # 2026-10-29 Brett: Claude retired
    if CLAUDE_RETIRED: CLAUDE_STATE.update(status="RETIRED"); return {}
    """Claude CLI (logged in) external review, same prompt/line format as GPT. {} on any failure (fail closed). Never prints keys."""
    if "--no-claude" in sys.argv or not os.path.exists(CLAUDE_BIN): CLAUDE_STATE.update(status="PENDING_LOGIN"); return {}
    out_dir = out_dir or OUT
    try:
        full = (prompt_text or "") + "\n\n" + SETUP_ONLY_RULES + "\n\nIMPORTANT: Reply with ONLY lines of the form `BOOK TICKER PASS|FAIL reason` (one per candidate). No preamble."
        r = subprocess.run([CLAUDE_BIN, "-p", "--model", "opus"], input=full, capture_output=True, text=True, timeout=CLAUDE_TIMEOUT_S, cwd=out_dir)
        open(os.path.join(out_dir, "claude_raw.md"), "w").write(r.stdout or "")
        parsed = parse_claude_verdicts(r.stdout or "")
        CLAUDE_STATE.update(status="ok" if parsed else "PENDING", n=sum(len(v) for v in parsed.values()), err=None if parsed else f"rc={r.returncode} no lines parsed")
        return parsed
    except Exception as e:
        CLAUDE_STATE.update(status="PENDING", err=f"{type(e).__name__}"[:80]); return {}
def apply_claude_verdicts(ext, parsed):
    n = 0
    for book, rows in (parsed or {}).items():
        for tk, v in rows.items():
            slot = ext.setdefault(book, {}).setdefault(tk, {"GPT": "PENDING", "CLAUDE": "PENDING"})
            if slot.get("CLAUDE") in (None, "PENDING", "PENDING_LOGIN"):
                slot["CLAUDE"] = v["CLAUDE"]
                if v.get("reason"): slot["CLAUDE_reason"] = v["reason"]
                n += 1
    return n
def finalize():
    iv = json.load(open(os.path.join(OUT, "internal_verdicts.json"))); ext = json.load(open(os.path.join(OUT, "external_reviews.json")))
    summary = {}
    for book, R in iv["books"].items():
        P = json.load(open(os.path.join(BASE, "state", BOOKS[book], f"plan_bench_{D}.json"))) if os.path.exists(os.path.join(BASE, "state", BOOKS[book], f"plan_bench_{D}.json")) else {"set": []}
        if book == "asx": P = json.load(open(os.path.join(OUT, "asx_plan_input.json")))
        if ASIA_MKT and book == ASIA_MKT: P = json.load(open(os.path.join(OUT, f"{ASIA_MKT}_plan_input.json")))
        src = {s["ticker"]: s for s in P["set"]}; keep, cut, flags = [], [], []
        armed = {tkey(t) for t in json.load(open(os.path.join(BASE, "state", BOOKS[book], "ledger.json")))["trades"] if t["status"] == "ARMED"}   # cut_trade() exits on non-ARMED
        for tk, r in R["rows"].items():
            e = ext.get(book, {}).get(tk, {}); g, c = e.get("GPT", "PENDING"), ("RETIRED" if CLAUDE_RETIRED else e.get("CLAUDE", "PENDING"))
            ok = ai_ok(r["internal"], g, c)   # 2026-10-28 Brett 07:16: desk PASS + no AI FAIL + >=1 AI PASS (other may be unavailable); both down -> BLOCKED
            # review fix #2: CLEARED only for the exact setup reviewed (plan row at finalize == reviewed levels, and the external
            # verdicts were given on that same hash when they carry one). No hash / mismatch -> BLOCKED (fail closed).
            hz = SH.setup_hash(src.get(tk)) if tk in src else r.get("setup_hash")
            if ok and (not r.get("setup_hash") or hz != r["setup_hash"] or (e.get("setup_hash") and e["setup_hash"] != r["setup_hash"])):
                ok = False; r.setdefault("RISK", {}).setdefault("reasons", []).append(f"setup hash mismatch: reviewed {r.get('setup_hash')} vs plan {hz} vs external {e.get('setup_hash')} -> BLOCKED"); flags.append(f"{tk}: setup hash mismatch -> BLOCKED")
            if ok and (str(g).upper() != "PASS" or str(c).upper() != "PASS"): flags.append(f"{tk}: AI advisory GPT={g} CLAUDE={c} (desk PASS -> CLEARED)")
            r["final"] = dict(verdict="CLEARED" if ok else "BLOCKED", GPT=g, CLAUDE=c)
            if ok and tk in src: keep.append(dict(src[tk], cleared=D, ai_gpt=dict(verdict=g, reason=e.get("GPT_reason", "")), ai_claude=dict(verdict=c, reason=e.get("CLAUDE_reason", "")), setup_hash=r["setup_hash"], **({"next_earnings_utc": str(r["BEHAVIOR"]["next_earnings_utc"])} if r["BEHAVIOR"].get("next_earnings_utc") else {}), **({"shares": r["RISK"]["resized_shares"], "risk_aud_actual": r["RISK"]["risk_aud"], "resized_h5": True} if r["RISK"].get("resized_shares") else {})))
            elif not ok and tk in armed: cut.append(dict(ticker=tk, reason=f"clearance {D}: internal {r['internal']} " + " ".join(f"{k}={r[k]['verdict']}" for k in ("RISK", "BEHAVIOR", "SPECIALIST")) + f" GPT={g} CLAUDE={c}"))
        out = dict(label=P.get("label", R["label"]), set=keep, cut=cut, note=f"hunt_clearance {D}: {len(keep)} cleared of {len(R['rows'])}; " + "; ".join(flags))
        if "cash_slots" in P: out["cash_slots"] = P["cash_slots"]
        atomic(os.path.join(OUT, "cleared_plan.json" if book == "main" else f"cleared_plan_{book}.json"), out)
        summary[book] = {tk: dict(internal=r["internal"], R=r["RISK"]["verdict"], B=r["BEHAVIOR"]["verdict"], S=r["SPECIALIST"]["verdict"], final=r["final"]["verdict"], setup_hash=r.get("setup_hash"),
                                  why=[x for k in ("RISK", "BEHAVIOR", "SPECIALIST") if r[k]["verdict"] == "FAIL" for x in r[k]["reasons"] if any(w in x for w in ("cap", ">", "<", "FOMO", "chas", "earnings", "exhausted", "veto", "squeeze", "uptrend", "thin", "noise", "wide", "corr", "UNKNOWN", "ADV", "costs", "wrong"))])
                         for tk, r in R["rows"].items()}
    atomic(os.path.join(OUT, "summary.json"), dict(meta=iv["meta"], books=summary)); return summary
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run": run()
    elif cmd == "finalize": print(json.dumps(finalize(), indent=1))
