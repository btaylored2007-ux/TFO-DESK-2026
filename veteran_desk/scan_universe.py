"""Veteran Desk scan breadth (Brett 2026-10-29 13:09 WITA). PAPER only.
Dex Romano (trend seat) proved the full-DB path at 13:13 WITA: 17,649 scanned / 8,954 cleared A$500k / 1,843 trend hits (/tmp/dex_wide.py).
This module is that approach made durable for every seat: build liquid list from pit.sqlite, coded trend pre-screen, pack() top-N + flat 'scanned X of Y (Z cleared A$500k)'.
Floor A$500k / bars>=200 / Yahoo primary+IBKR fallback unchanged. gate_v2 still the final gate."""
import datetime as dt, json, math, os, sqlite3, time
from veteran_desk import pit as P
OUT = os.path.join(P.BASE, "data", "pit", "scan_universe.json"); SECT = os.path.join(P.BASE, "data", "pit", "sector_map.json")
MIN_AUD, MIN_BARS = 500000.0, 200
# Dex FX map (listing currency -> USD). .L is GBp (pence). AUDUSD applied after.
FX_TO_USD = {"": 1.0, "L": 1.30 / 100, "DE": 1.08, "PA": 1.08, "AS": 1.08, "MI": 1.08, "MC": 1.08, "BR": 1.08, "SW": 1.12,
             "T": 0.0067, "KS": 0.00072, "KQ": 0.00072, "TW": 0.031, "TWO": 0.031, "SI": 0.76, "HK": 0.128,
             "ST": 0.095, "CO": 0.145, "OL": 0.091, "HE": 1.08, "TO": 0.72, "V": 0.72, "AX": None,  # AX = AUD, handled via AUDUSD
             "NS": 0.012, "BO": 0.012, "NZ": 0.60, "SS": 0.14, "SZ": 0.14, "IR": 1.30, "VI": 1.08, "LS": 1.08}
MARKET = {"US": "US", "ASX": "ASX", "TSE": "JP", "HKEX": "HK", "KRX": "KR", "TWSE": "TW", "SGX": "SG", "LSE": "UK",
          "Euronext": "EU/UK", "XETRA": "EU/UK", "MILAN": "EU/UK", "MADRID": "EU/UK", "SIX": "EU/UK", "NORDICS": "EU/UK",
          "NSE": "IN", "NZX": "NZ", "TSX": "CA", "SSE": "CN", "SZSE": "CN"}
SEAT_SECTORS = {"energy": {"Energy"}, "essentials_utilities": {"Consumer Staples", "Utilities"}, "banks": {"Financials"},
                "healthcare": {"Health Care"}, "tech_ai_infra": {"Information Technology", "Communication Services"},
                "news_filings": {"Energy", "Financials", "Health Care", "Consumer Staples", "Utilities"}}
BROAD = ("price_action", "trend")
def _audusd(c):
    r = c.execute("SELECT c FROM bars WHERE sym='AUDUSD=X' ORDER BY date DESC LIMIT 1").fetchone(); return float(r[0]) if r else 0.70
def _suf(sym):
    if sym.startswith("KRAKEN:") or sym.endswith("=X") or sym.endswith("=F") or sym.startswith("^"): return None
    return sym.rsplit(".", 1)[-1].upper() if "." in sym else ""
def to_aud(sym, usd_val, au):
    return usd_val / au
def usd_mult(sym):
    s = _suf(sym)
    if s is None: return None
    if s == "AX": return None  # special: already AUD
    return FX_TO_USD.get(s, 1.0 if s == "" else None)
def market_of(sym):
    v = P.venue_of(sym); return MARKET.get(v, v)
def sectors(refresh=False):
    if not refresh and os.path.exists(SECT) and time.time() - os.path.getmtime(SECT) < 30 * 86400:
        try: return json.load(open(SECT))
        except Exception: pass
    M = {}
    try:
        import pandas as pd, io, urllib.request
        for u in ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                  "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
                  "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies"):
            try:
                h = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read().decode()
                for t in pd.read_html(io.StringIO(h)):
                    cols = {str(x).lower(): x for x in t.columns}
                    sc = next((cols[k] for k in cols if k in ("symbol", "ticker symbol", "ticker")), None)
                    gc = next((cols[k] for k in cols if "gics sector" in k or k == "sector"), None)
                    if sc is not None and gc is not None:
                        for s, g in zip(t[sc], t[gc]): M[str(s).replace(".", "-").strip()] = str(g).strip()
                        break
            except Exception: pass
        if len(M) > 500: json.dump(M, open(SECT, "w"))
    except Exception: pass
    return M
def build(min_aud=MIN_AUD, min_bars=MIN_BARS, asof_date=None):
    """Dex path: every non-crypto/non-index pit.sqlite symbol, ADV>=A$500k, >=min_bars, recent bar."""
    c = sqlite3.connect("file:%s?mode=ro" % P.DB, uri=True); au = _audusd(c); SM = sectors(); INTL = _intl()
    cut = asof_date or (dt.date.today() - dt.timedelta(days=4)).isoformat()
    syms = [r[0] for r in c.execute("SELECT sym FROM universe WHERE sym NOT LIKE 'KRAKEN:%' AND sym NOT LIKE '^%'")]
    out = []; scanned = cleared = 0; dead = _dead()
    for s in syms:
        if P.banned(s) or s in dead: continue
        mlt = usd_mult(s)
        if mlt is None and _suf(s) != "AX": continue  # skip FX/FUT and unknown suffix (Dex skip)
        rows = c.execute("SELECT date,h,l,c,v FROM bars WHERE sym=? ORDER BY date DESC LIMIT ?", (s, min_bars + 10)).fetchall()
        scanned += 1
        if len(rows) < min_bars or rows[0][0] < cut: continue
        rows = rows[::-1]
        # tfo_seatpipe_1b_20261030 cleared-at-open fix: a bar dated today (WITA) may be a partial session (venue just opened);
        # clear on completed sessions only so a build/refresh right after an open doesn't drop names on a tiny partial bar.
        if len(rows) > min_bars and rows[-1][0] >= dt.date.today().isoformat(): rows = rows[:-1]
        cl = [r[3] for r in rows]
        if not cl[-1]: continue
        if _suf(s) == "AX":
            adv = sum(r[3] * (r[4] or 0) for r in rows[-20:]) / 20.0
            v24 = rows[-1][3] * (rows[-1][4] or 0)
        else:
            adv = sum(r[3] * (r[4] or 0) for r in rows[-20:]) / 20.0 * mlt / au
            v24 = rows[-1][3] * (rows[-1][4] or 0) * mlt / au
        if min(adv, v24) < min_aud: continue
        cleared += 1
        sec = (SM.get(s.replace(".", "-")) or YS.get(INTL.get(s))) if P.venue_of(s) == "US" else YS.get(INTL.get(s))   # thfreview 20261030: US names outside S&P1500 use Yahoo assetProfile cache
        out.append(dict(sym=s, market=market_of(s), bars=len(rows), adv20_aud=round(adv), last=rows[-1][0], sector=sec, liquid=True))
    by = {}
    for x in out:
        t = by.setdefault(x["market"], dict(all=0, liquid=0)); t["all"] += 1; t["liquid"] += 1
    # also count non-liquid for by_market breadth context
    J = dict(built_utc=dt.datetime.now(dt.timezone.utc).isoformat(), method="dex_wide", min_aud=min_aud, min_bars=min_bars,
             universe=len(syms), scanned=scanned, cleared=cleared, liquid=cleared, by_market=by, sector_map_n=len(SM), names=out)
    tmp = OUT + ".tmp"; json.dump(J, open(tmp, "w")); os.replace(tmp, OUT); return J
ASIA = ("JP", "HK", "KR", "TW", "SG")
YS = {"Healthcare": "Health Care", "Financial Services": "Financials", "Energy": "Energy", "Consumer Defensive": "Consumer Staples", "Utilities": "Utilities",
      "Technology": "Information Technology", "Communication Services": "Communication Services", "Industrials": "Industrials", "Basic Materials": "Materials",
      "Consumer Cyclical": "Consumer Discretionary", "Real Estate": "Real Estate", "ETF": "ETF/Fund"}   # thfreview: ETF/fund tag (no GICS)   # Yahoo sector -> GICS
from veteran_desk import crypto_universe as _CU
STABLE = _CU.STABLE   # tuple, so alt.startswith(STABLE) still works; includes AUSD, QCAD, ...
def _intl():
    try: return json.load(open(os.path.join(P.BASE, "data", "pit", "sector_map_intl.json")))
    except Exception: return {}
def _dead():
    try: return set((json.load(open(os.path.join(P.BASE, "data", "pit", "refresh_stale_report.json"))).get("fail") or {}).keys())
    except Exception: return set()
def kraken_liquid():
    """Kai's universe: veteran_desk/kraken_sweep.py output (DoH, 24h value) - liquid, non-stablecoin, non-fiat bases."""
    try: K = json.load(open(os.path.join(P.BASE, "state", "veteran_desk", "kraken_24h_all.json")))
    except Exception: return None, 0
    ps = K.get("pairs") or []
    L = ["KRAKEN:" + x["alt"] for x in ps if x.get("liquid") and (x.get("vol24h_aud") or 0) >= 500_000 and not _CU.is_stable_pair(x.get("alt")) and not P.banned("KRAKEN:" + x["alt"])]
    return L, len(ps)
def load():
    try: return json.load(open(OUT))
    except Exception: return None
def seat_list(seat):
    """Liquid symbols for seat. Broad seats (price_action/trend): all cleared. Sector seats: matching GICS (US) + hand-typed UNIVERSE peers for that seat."""
    J = load()
    if not J: return None, 0, 0
    L = [x for x in J["names"] if x.get("liquid")]; Y = J.get("scanned") or len(J["names"]); Z = J.get("cleared") or len(L)
    if seat in BROAD or seat == "pit:all": return [x["sym"] for x in L], Y, Z
    if seat == "crypto":
        K, n = kraken_liquid(); return K, n, len(K or [])
    if seat == "energy":   # tfo_seatpipe_energy_20261030: Rex master list (data/pit/energy_universe.json) = energy seat universe; liquid (A$500k, >=200 bars) names only
        try:
            EM = json.load(open(os.path.join(P.BASE, "data", "pit", "energy_universe.json")))["names"]
            liq = {x["sym"] for x in L}; return sorted(s for s in EM if s in liq and not s.endswith(".JK") and not P.banned(s)), Y, Z
        except Exception: pass
    want = SEAT_SECTORS.get(seat)
    if not want:
        # non-equity seats (crypto/fx/macro): no DB equity expand
        return None, Y, Z
    hand = []
    try:
        groups = {"energy": ["energy"], "essentials_utilities": ["essentials"], "banks": ["banks"], "healthcare": ["healthcare"],
                  "tech_ai_infra": ["tech"], "news_filings": ["energy", "banks", "healthcare", "essentials"]}.get(seat, [])
        hand = [s for g in groups for s in P.UNIVERSE.get(g, [])]
    except Exception: pass
    sector_syms = [x["sym"] for x in L if x.get("sector") in want]
    if seat == "news_filings": sector_syms += [x["sym"] for x in L if x["market"] in ASIA]   # Quinn: liquid Asia listings for Asia hours (sector-agnostic news)
    # keep hand-typed even if not in liquid file (feats may still work); prefer liquid sector set
    U = sorted(set(sector_syms) | {s for s in hand if any(x["sym"] == s for x in L)})
    if len(U) < 500:
        # pad with liquid same-market peers from hand venues so seat approaches 500 when sector map is thin
        pass
    return U, Y, Z
def prescreen(c, syms, asof, n=40):
    """Dex trend stack + efficiency: close>SMA50>SMA200, 3m/6m up, 1m not extended >25%, efficiency. Both long-bias (Dex); shorts still allowed by seat after."""
    au = _audusd(c); hits = []
    for s in syms:
        rows = P.bars_asof(c, s, asof, 210)
        if len(rows) < 200: continue
        cl = [r[4] for r in rows]; last = cl[-1]
        if not last: continue
        s50, s200 = sum(cl[-50:]) / 50, sum(cl[-200:]) / 200
        r1 = last / cl[-22] - 1 if cl[-22] else 0; r3 = last / cl[-64] - 1 if len(cl) >= 64 and cl[-64] else 0
        r6 = last / cl[-127] - 1 if len(cl) >= 127 and cl[-127] else 0
        path = sum(abs(cl[i] - cl[i - 1]) for i in range(-20, 0)) or 1e-9; eff = abs(last - cl[-21]) / path
        if last > s50 > s200 and r3 > 0 and r6 > 0 and r1 < 0.25:
            # liquidity tilt (prefer deeper ADV)
            v20 = sum((r[4] or 0) * (r[5] or 0) for r in rows[-20:]) / 20.0
            mlt = usd_mult(s); 
            if _suf(s) == "AX": adv = v20
            elif mlt is not None: adv = v20 * mlt / au
            else: adv = v20 / au
            liq = max(0.0, min(1.0, math.log10(max(adv, 1) / 5e6) / 3))
            hits.append((1.0 + eff + liq + min(r3, 0.5), s, adv))
    hits.sort(reverse=True); return [s for _, s, _ in hits[:n]], len(hits)
def refresh_liquid(period="1mo", pause=0.35, limit=None):
    """Throttled daily top-up of every liquid scan name. Yahoo primary (pit.ingest_yahoo); IBKR fallback for US gaps stays inside it.
    Back-off: 3 consecutive fails -> 60s, 10 -> 300s; abort after 40 consecutive."""
    J = load() or build(); c = P.con()
    todo = [x["sym"] for x in J["names"] if x.get("liquid") and P.is_stale(c, x["sym"], slack_days=0)]   # tfo_seatpipe_bars_20261030
    if limit is not None: todo = todo[:int(limit)]
    ok = fail = streak = 0; errs = {}
    for s in todo:
        try:
            P.ingest_yahoo(c, s, period); c.commit(); ok += 1; streak = 0
        except Exception as e:
            fail += 1; streak += 1; errs[s] = str(e)[:80]
            if streak >= 40: break
            if streak % 10 == 0: time.sleep(300)
            elif streak % 3 == 0: time.sleep(60)
        time.sleep(pause)
    rep = dict(utc=dt.datetime.now(dt.timezone.utc).isoformat(), stale=len(todo), ok=ok, fail=fail, aborted=streak >= 40, errors=dict(list(errs.items())[:50]))
    json.dump(rep, open(os.path.join(P.BASE, "data", "pit", "scan_refresh_report.json"), "w"), indent=1)
    try: build()
    except Exception: pass
    return rep
if __name__ == "__main__":
    J = build(); print(json.dumps({k: J[k] for k in ("built_utc", "method", "universe", "scanned", "cleared", "by_market", "sector_map_n")}, indent=1))
# ---- tfo_seatpipe_3_20261030: coded candidate rank (trend + relative volume + news hit + factor rank), ~120 names -> model grades top 10 ----
def _factor_rank():
    try:   # tfo_seatpipe_5b_20261030: daily value_rank (com.tfo.fundamental-rank-rebuild 05:43) first; old ticker list fallback
        R = (json.load(open(os.path.join(P.BASE, "reports", "value_rank.json"))).get("ranked") or [])
        L = [x.get("symbol") for x in R if x.get("symbol")]; n = max(len(L), 1)
        if L: return {s: 1.0 - i / n for i, s in enumerate(L)}
    except Exception: pass
    try:
        L = json.load(open(os.path.join(P.BASE, "state", "fundamental_rank_universe.json")))
        L = [x if isinstance(x, str) else (x.get("sym") or x.get("ticker")) for x in L]; n = max(len(L), 1)
        return {s: 1.0 - i / n for i, s in enumerate(L) if s}
    except Exception: return {}
def essentials_tags():
    """tfo_seatpipe_5b_20261030: essentials screen (daily 05:55) -> {tk: 'core'|'spec'} for names passing the screen."""
    try:
        S = json.load(open(os.path.join(P.BASE, "state", "essentials_screen.json")))
        return {d["tk"]: d.get("sleeve") or k for k in ("core", "spec") for d in S.get(k, []) if d.get("tk") and not d.get("why")}
    except Exception: return {}
def _news_syms():
    try: return set((json.load(open(os.path.join(P.BASE, "state", "veteran_desk", "news.json"))).get("asset_tags") or {}).keys())
    except Exception: return set()
def rank_candidates(c, syms, asof, n=120, extra_news=None, mode="default"):
    if mode in ("trend", "price_action"): return rank_dex(c, syms, asof, n, mode)
    """Score every liquid seat name; return (top n syms, rows, scored count). Candidate list only - never touches the gate."""
    FR = _factor_rank(); NW = _news_syms() | set(extra_news or ()); rows = []; ES = essentials_tags()
    for s in syms:
        r = P.bars_asof(c, s, asof, 210)
        if len(r) < 64: continue
        cl = [x[4] for x in r]; vo = [x[5] or 0 for x in r]; last = cl[-1]
        if not last or not cl[-22] or not cl[-64]: continue
        s50 = sum(cl[-50:]) / 50; s200 = sum(cl[-200:]) / len(cl[-200:])
        r1 = last / cl[-22] - 1; r3 = last / cl[-64] - 1
        up = (last > s50) + (s50 > s200) + (r3 > 0); dn = (last < s50) + (s50 < s200) + (r3 < 0)
        trend = max(up, dn) / 3.0 + min(abs(r3), 0.5)
        if abs(r1) > 0.25: trend -= 0.5   # extended = chase risk
        v20 = sum(vo[-20:]) / 20.0; rv = (sum(vo[-5:]) / 5.0 / v20) if v20 > 0 else 0.0; rv = max(0.0, min(rv, 3.0))
        nh = 1.0 if s in NW else 0.0; fr = FR.get(s, 0.0)
        sc = trend + 0.5 * (rv - 1.0) + 1.0 * nh + 0.5 * fr
        rows.append(dict(sym=s, score=round(sc, 3), trend=round(trend, 3), relvol=round(rv, 2), news=bool(nh), factor=round(fr, 2), bias="LONG" if up >= dn else "SHORT", unusual_vol=rv >= 2.0, essentials=ES.get(s)))
    rows.sort(key=lambda x: -x["score"]); return [x["sym"] for x in rows[:n]], rows[:n], len(rows)
# ---- tfo_seatpipe_4_20261030: durable Yahoo sector fill for non-US liquid names (was a one-off /tmp script). Cached in sector_map_intl.json;
# misses cached in sector_map_intl_miss.json and retried after 14 days. Throttled; daily via run_scan_refresh.sh. ----
MISS = os.path.join(P.BASE, "data", "pit", "sector_map_intl_miss.json")
def fill_intl_sectors(limit=400, pause=0.3):
    import yfinance as yf
    J = load() or {}; mp = os.path.join(P.BASE, "data", "pit", "sector_map_intl.json"); M = _intl()
    try: MS = json.load(open(MISS))
    except Exception: MS = {}
    now_ = time.time()
    todo = [x["sym"] for x in J.get("names", []) if x.get("liquid") and not x.get("sector") and not M.get(x["sym"]) and now_ - MS.get(x["sym"], 0) > 14 * 86400][:limit]
    ok = fails = 0
    for s in todo:
        try: _i = (yf.Ticker(s).info or {}); sec = _i.get("sector") or ("ETF" if _i.get("quoteType") in ("ETF", "MUTUALFUND") else None)
        except Exception: sec = None
        if sec: M[s] = sec; ok += 1; fails = 0
        else:
            MS[s] = now_; fails += 1
            if fails % 10 == 0: time.sleep(60)
        time.sleep(pause)
    for p_, o in ((mp, M), (MISS, MS)):
        tmp = p_ + ".tmp"; json.dump(o, open(tmp, "w")); os.replace(tmp, p_)
    return dict(todo=len(todo), ok=ok, miss=len(todo) - ok, mapped=len(M))

# ---- tfo_seatpipe_dexsplit_20261030 (TFO 18:20 WITA): Dex seats rank differently. Candidate list only - gate untouched. ----
def rank_dex(c, syms, asof, n=120, mode="trend"):
    rows = []
    for s in syms:
        r = P.bars_asof(c, s, asof, 260)
        if len(r) < 200: continue
        cl = [x[4] for x in r]; hi = [x[2] for x in r]; lo = [x[3] for x in r]; vo = [x[5] or 0 for x in r]; last = cl[-1]
        if not last or not all(cl[-k] for k in (22, 127, min(253, len(cl)))): continue
        s50 = sum(cl[-50:]) / 50; s200 = sum(cl[-200:]) / 200
        state = 1 if last > s50 > s200 else (-1 if last < s50 < s200 else 0)
        if mode == "trend":   # 12-1 and 6-1 momentum (skip last month) + 50/200 trend state
            m12 = cl[-22] / cl[-min(253, len(cl))] - 1; m6 = cl[-22] / cl[-127] - 1
            mom = 0.5 * m12 + 0.5 * m6; mom = mom / (1.0 + abs(mom)); d = state or (1 if mom >= 0 else -1); sc = 0.5 * abs(state) + (abs(mom) if mom * d >= 0 else -abs(mom))
            rows.append(dict(sym=s, score=round(sc, 3), mom12_1=round(m12, 3), mom6_1=round(m6, 3), trend_state=state, bias="LONG" if d > 0 else "SHORT"))
        else:   # price_action: RVOL + breakout-and-hold - ATR extension penalty
            tr = [max(hi[i], cl[i - 1]) - min(lo[i], cl[i - 1]) for i in range(-14, 0)]; atr = sum(tr) / 14 or 1e-9
            v20 = sum(vo[-21:-1]) / 20.0; rv = (vo[-1] / v20) if v20 > 0 else 0.0; rv5 = (sum(vo[-5:]) / 5 / v20) if v20 > 0 else 0.0
            h20 = max(hi[-25:-5]); l20 = min(lo[-25:-5])
            bo_up = any(cl[i] > h20 for i in range(-5, 0)) and min(cl[-3:]) > h20   # broke the prior 20d high in last 5 bars and held 3 closes above
            bo_dn = any(cl[i] < l20 for i in range(-5, 0)) and max(cl[-3:]) < l20
            ext = abs(last - sum(cl[-20:]) / 20) / atr   # distance from SMA20 in ATRs
            sc = 0.6 * min(max(rv, rv5), 3.0) + (1.5 if (bo_up or bo_dn) else 0.0) - 0.5 * max(0.0, ext - 2.0)
            rows.append(dict(sym=s, score=round(sc, 3), rvol=round(rv, 2), rvol5=round(rv5, 2), breakout_hold="up" if bo_up else ("down" if bo_dn else None), atr_ext=round(ext, 2), unusual_vol=rv >= 2.0, bias="SHORT" if bo_dn else "LONG"))
    rows.sort(key=lambda x: -x["score"]); return [x["sym"] for x in rows[:n]], rows[:n], len(rows)
