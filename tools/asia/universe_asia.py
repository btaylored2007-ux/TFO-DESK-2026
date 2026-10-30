#!/usr/bin/env python3
"""ASIA UNIVERSE for the 48-72h hunt books (PAPER ONLY). Brett 2026-10-27 (pt1481u) "same setup for the Asian markets".
Full listed universe per market (official exchange lists), Yahoo daily bars, same ranker (run_scan.ind/score) as ASX/US.
Liquidity floor: A$2m-equivalent 20d average daily traded value, converted daily at Yahoo <CCY>AUD=X (same A$2m as ASX).
Sources (public, no keys):
  jp  JPX listed issues  https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx (domestic+foreign stocks; ETF/REIT/PRO excluded) -> .T
  hk  HKEX ListOfSecurities.xlsx (Category=Equity, HKD counter) incl. 'Shortsell Eligible' (= designated short-sell list) -> .HK
  sg  SGX api.sgx.com/securities/v1.1 (types stocks, reits, businesstrusts) -> .SI
  kr  KRX KIND corpList KOSPI (.KS) + KOSDAQ (.KQ)
  tw  TWSE ISIN list strMode=2 (.TW) + TPEx strMode=4 (.TWO), CFI ES* equities only
  sector: exchange list where given (jp 33-sector, kr 업종, tw 產業別), else yfinance .info sector for floor-passers (cached 14d)
  shorts: jp IBKR japan.txt; hk designated list AND IBKR hongkong.txt; sg/kr/tw long-only (see asia_markets.py)
  earnings: yfinance calendar for the top 60 + ARMED/bench names. No Indonesian assets. No announcement feed (no free one) -> no event bonus.
Writes only state/calendar/inputs/ranked_<mkt>.csv, ranked_<mkt>_report.json, <mkt>_events.json, state/calendar/<mkt>/* cache.
CLI: universe_asia.py jp|hk|sg|kr|tw|all"""
import sys, os, io, re, json, time, datetime as dt, warnings
import numpy as np, pandas as pd, requests, yfinance as yf
warnings.filterwarnings("ignore")
import asia_markets as AM
BASE = os.environ.get("DESK_BASE", os.path.dirname(os.path.abspath(__file__)))
CD = os.path.join(BASE, "state", "calendar"); IN = os.path.join(CD, "inputs")
sys.path.insert(0, os.environ.get("RANKER_DIR", os.path.join(CD, "ranker"))); import run_scan as RS
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/128 Safari/537.36"}; UTC = dt.timezone.utc
MIN_ADV_AUD = 2e6; SLIP_BP_SIDE = 10.0
EXCL_WORDS = ("INDONESIA", "JAKARTA", "TBK")
def get(u, **k): r = requests.get(u, headers=UA, timeout=90, **k); r.raise_for_status(); return r
def listing(mkt):
    """DataFrame sym, name, sector, lot, hk_shortsell"""
    if mkt == "jp":
        d = pd.read_excel(io.BytesIO(get("https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx").content))
        d = d[d["市場・商品区分"].astype(str).str.contains("株式") & ~d["市場・商品区分"].astype(str).str.contains("PRO")]
        return pd.DataFrame(dict(sym=d["コード"].astype(str).str.strip() + ".T", name=d["銘柄名"], sector=d["33業種区分"], lot=100, hk_shortsell=False))
    if mkt == "hk":
        d = pd.read_excel(io.BytesIO(get("https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx").content), header=2)
        d = d[(d["Category"] == "Equity") & (d["Trading Currency"].astype(str).str.strip() == "HKD")]
        return pd.DataFrame(dict(sym=d["Stock Code"].astype(int).map(lambda c: f"{c:04d}.HK"), name=d["Name of Securities"], sector="",
                                 lot=pd.to_numeric(d["Board Lot"].astype(str).str.replace(",", ""), errors="coerce").fillna(0).astype(int),
                                 hk_shortsell=d["Shortsell Eligible"].astype(str).str.strip().str.upper() == "Y"))
    if mkt == "sg":
        j = get("https://api.sgx.com/securities/v1.1?excludetypes=bonds&params=nc,cn,type").json()["data"]["prices"]
        j = [x for x in j if x.get("type") in ("stocks", "reits", "businesstrusts") and x.get("nc")]
        return pd.DataFrame(dict(sym=[x["nc"].strip() + ".SI" for x in j], name=[x.get("cn") or "" for x in j], sector="", lot=100, hk_shortsell=False))
    if mkt == "kr":
        out = []
        for mt, suf in (("stockMkt", ".KS"), ("kosdaqMkt", ".KQ")):
            t = get(f"https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&marketType={mt}").content.decode("euc-kr", "replace")
            k = pd.read_html(io.StringIO(t))[0]
            out.append(pd.DataFrame(dict(sym=k["종목코드"].astype(str).str.zfill(6) + suf, name=k["회사명"], sector=k["업종"], lot=1, hk_shortsell=False)))
        return pd.concat(out, ignore_index=True)
    if mkt == "tw":
        out = []
        for mode, suf in ((2, ".TW"), (4, ".TWO")):
            t = get(f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}").content.decode("big5", "replace")
            k = pd.read_html(io.StringIO(t), header=0)[0]; k = k[k["CFICode"].astype(str).str.startswith("ES")]
            cn = k["有價證券代號及名稱"].astype(str).str.split("\u3000", n=1)
            out.append(pd.DataFrame(dict(sym=cn.str[0].str.strip() + suf, name=cn.str[1].fillna(""), sector=k["產業別"].fillna(""), lot=1000, hk_shortsell=False)))
        return pd.concat(out, ignore_index=True)
def ibkr_short(mkt):
    """IBKR public short-stock file -> {code: (fee_pa, available)}; None if market has no file / empty."""
    f = AM.MARKETS[mkt]["ibkr_file"]
    try:
        import urllib.request
        raw = urllib.request.urlopen(f"ftp://shortstock:@ftp2.interactivebrokers.com/{f}.txt", timeout=90).read().decode("utf-8", "replace")
    except Exception: return None, None
    out, asof = {}, None
    for l in raw.splitlines():
        if l.startswith("#BOF"): asof = l.split("|")[1]
        if l.startswith("#"): continue
        p = l.split("|")
        if len(p) < 8: continue
        a = p[7].strip(); a = int(a[1:]) * 10 if a.startswith(">") else int(float(a or 0))
        try: fee = float(p[6]) / 100
        except Exception: fee = None
        out[p[0].strip()] = (fee, a)
    return (out or None), asof
def code_of(mkt, sym):
    c = sym.split(".")[0]
    return str(int(c)) if mkt == "hk" else c
def sectors_yf(mkt, syms):
    p = os.path.join(CD, mkt, "sectors.json")
    try: C = json.load(open(p))
    except Exception: C = {}
    need = [s for s in syms if s not in C or time.time() - C[s].get("t", 0) > 14 * 86400]
    from concurrent.futures import ThreadPoolExecutor
    def one(s):
        try: i = yf.Ticker(s).info or {}; return s, dict(sector=i.get("sector") or i.get("industry") or "", country=i.get("country") or "", t=time.time())
        except Exception: return s, dict(sector="", country="", t=time.time() - 13 * 86400)
    with ThreadPoolExecutor(8) as ex:
        for s, v in ex.map(one, need): C[s] = v
    json.dump(C, open(p, "w")); return C
def earnings_dates(syms):
    out = {}
    from concurrent.futures import ThreadPoolExecutor
    def one(s):
        try:
            cal = yf.Ticker(s).calendar or {}; ed = cal.get("Earnings Date") if isinstance(cal, dict) else None
            if ed: return s, str(ed[0])[:10]
        except Exception: pass
        return s, None
    with ThreadPoolExecutor(8) as ex:
        for s, d in ex.map(one, syms):
            if d: out[s] = d
    return out
def main(mkt):
    t0 = time.time(); M = AM.MARKETS[mkt]; cache = os.path.join(CD, mkt); os.makedirs(cache, exist_ok=True); rep = dict(market=mkt, name=M["name"], feeds={})
    D = listing(mkt); rep["feeds"]["listing"] = len(D)
    D = D[~D.name.astype(str).str.upper().str.contains("|".join(EXCL_WORDS))].drop_duplicates("sym")
    syms = D.sym.tolist()
    pk = os.path.join(cache, f"ohlcv_{dt.date.today()}.pkl")
    if os.path.exists(pk) and time.time() - os.path.getmtime(pk) < 6 * 3600: d = pd.read_pickle(pk)
    else: d = RS.dl(syms + [M["idx"], M["fx"]], "1y", chunk=300); d.to_pickle(pk)
    C, H, L, O, V = d["Close"], d["High"], d["Low"], d["Open"], d["Volume"]
    fx = float(C[M["fx"]].dropna().iloc[-1])           # local -> AUD
    adv = (C * V).iloc[-20:].mean(); px = C.ffill().iloc[-1]
    have = [s for s in syms if s in C.columns and C[s].notna().sum() >= 60]
    floor_local = MIN_ADV_AUD / fx
    keep = [s for s in have if adv.get(s, 0) >= floor_local and px.get(s, 0) > 0]
    rep.update(listed=len(syms), with_bars=len(have), fx_local_to_aud=fx, floor_aud=MIN_ADV_AUD, floor_local=round(floor_local), floor_label=f"A$2m = {M['ccy']} {floor_local/1e6:,.1f}m 20d ADV",
               adv_ge_floor=len(keep), adv_ge_5m_aud=int(sum(adv.get(s, 0) * fx >= 5e6 for s in keep)))
    ix = C[M["idx"]].dropna(); reg = "long" if ix.iloc[-1] > ix.iloc[-50:].mean() else "short"
    sh, asof = (ibkr_short(mkt) if M["shorts"] else (None, None)); rep["feeds"]["ibkr_short_file"] = dict(asof=asof, n=len(sh or {}))
    meta = D.set_index("sym")
    if (meta.loc[keep, "sector"].astype(str).str.len() == 0).any():
        S = sectors_yf(mkt, [s for s in keep if not str(meta.at[s, "sector"])])
        for s, v in S.items():
            if s in meta.index and not str(meta.at[s, "sector"]): meta.at[s, "sector"] = v.get("sector") or ""
        indo = {s for s, v in S.items() if (v.get("country") or "").lower() == "indonesia"}
    else: indo = set()
    rows = []
    for s in keep:
        try:
            if s in indo: continue
            m = pd.concat([O[s], H[s], L[s], C[s], V[s]], axis=1, keys=list("ohlcv")).dropna(); m = m[(m.h >= m.l) & (m.c > 0)]
            if len(m) < 60 or m.index[-1].date() < dt.date.today() - dt.timedelta(days=6): continue
            r = RS.ind(m.o, m.h, m.l, m.c, m.v); mir = RS.ind(1 / m.o, 1 / m.l, 1 / m.h, 1 / m.c, m.v)
            p = float(r["px"]); adv_local = float(r["adv_usd"]); adv_aud = adv_local * fx    # bars are local ccy
            r["rs20"] = r["roc20"] - (ix.iloc[-1] / ix.iloc[-21] - 1) * 100; mir["rs20"] = -r["rs20"]
            cost = 2 * SLIP_BP_SIDE / 100 + 100 * AM.tick(mkt, p) / p + 2 * 100 * M["comm"]["pct"] + 100 * (M["sell_tax"] + 2 * M.get("stamp", 0))
            r["adv_usd"] = adv_aud; mir["adv_usd"] = adv_aud      # ranker liquidity penalty in AUD-ish scale (same as ASX)
            sl = RS.score(pd.Series(r), cost)[0]; ss = RS.score(pd.Series(mir), cost)[0]
            code = code_of(mkt, s); ib = (sh or {}).get(code)
            if mkt == "hk": can, src = bool(meta.at[s, "hk_shortsell"]) and bool(ib and ib[1] > 0), f"HKEX designated list={bool(meta.at[s, 'hk_shortsell'])}; IBKR hongkong.txt {asof}: {ib}"
            elif mkt == "jp": can, src = bool(ib and ib[1] > 0), f"IBKR japan.txt {asof}: {ib}"
            else: can, src = False, "LONG-ONLY market (no IBKR short selling)"
            d_ = reg if M["shorts"] else "long"
            r.update(sym=s, venue=mkt, etf=False, cls=f"{mkt}_equity", country=M["name"], sector=str(meta.at[s, "sector"] or ""), name=str(meta.at[s, "name"]),
                     lot=int(meta.at[s, "lot"] or 1), adv_local=adv_local, adv_aud=adv_aud, fx_aud=fx, score=sl, short_score=ss, dir=d_,
                     rank_score=(sl if d_ == "long" else ss), cost_pct=cost, tick=AM.tick(mkt, p), shortable=can, short_src=src,
                     borrow_pa=(ib[0] if ib else None), borrow_avail=(ib[1] if ib else 0), excluded=False)
            rows.append(r)
        except Exception: pass
    R = pd.DataFrame(rows)
    R = pd.concat([R, pd.DataFrame([dict(sym=M["idx"], venue=mkt, cls="index_ref", px=float(ix.iloc[-1]), m50=float(ix.iloc[-50:].mean()), excluded="index reference only")])], ignore_index=True)
    R.to_csv(os.path.join(IN, f"ranked_{mkt}.csv"), index=False)
    top = list(R[R.cls == f"{mkt}_equity"].nlargest(60, "rank_score").sym) if len(rows) else []
    try: led = [t["sym"] for t in json.load(open(os.path.join(BASE, "state", f"hunt48_{mkt}", "ledger.json")))["trades"] if t["status"] in ("ARMED", "BENCH", "FILLED")]
    except Exception: led = []
    earn = earnings_dates(sorted(set(top) | set(led))); rep["feeds"]["yf_earnings_dates"] = len(earn)
    json.dump(dict(generated_utc=dt.datetime.now(UTC).isoformat(), earnings=earn, halted=[]), open(os.path.join(IN, f"{mkt}_events.json"), "w"), indent=1)
    rep.update(run_at=dt.datetime.now(UTC).isoformat(), scored=len(rows), regime=reg, index=M["idx"], index_px=float(ix.iloc[-1]), index_50dma=float(ix.iloc[-50:].mean()),
               shortable_in_universe=int(sum(bool(r["shortable"]) for r in rows)), indonesian_excluded=len(indo), runtime_s=round(time.time() - t0, 1))
    json.dump(rep, open(os.path.join(IN, f"ranked_{mkt}_report.json"), "w"), indent=1, default=str)
    print(json.dumps(rep, default=str)); return rep
if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else "all"
    for m in (AM.MARKETS if a == "all" else [a]):
        try: main(m)
        except Exception as e: print(json.dumps(dict(market=m, error=str(e)[:300])))
