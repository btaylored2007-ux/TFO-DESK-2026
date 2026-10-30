"""Veteran NEWS DESK (Brett 2026-10-28 17:01 WITA). Team: Quinn Mercer (chief), Ravi Sandhu (headline wire), Elena Park (macro/central banks),
Tom Barrett (geopolitics/energy shocks), Mara Quist (credit/stress). Code-only, free public feeds, PAPER. Outputs (info-only):
state/veteran_desk/news.json (regime flag, headlines, per-asset tags, positions at risk), news_alerts.jsonl, reports/veteran_desk/news_brief_<mkt>.md.
The regime flag may only TIGHTEN sizing in crisis (book.py halves risk); it never blocks Brett's tick. Delays: RSS 1-15 min, SEC 8-K atom ~minutes,
VIX/credit via Yahoo ~15 min delayed, FRED HY spread is previous-day."""
import datetime as dt, email.utils, json, os, re, sys, urllib.request, xml.etree.ElementTree as ET
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); UTC = dt.timezone.utc; OUT = os.path.join(BASE, "state", "veteran_desk", "news.json")
ALERTS = os.path.join(BASE, "state", "veteran_desk", "news_alerts.jsonl"); UA = {"User-Agent": "TFO paper research brett-desk research@example.com"}
TEAM = {"chief": "Quinn Mercer", "tech_desk_reader": "Nico Hart (reads)", "wire": "Ravi Sandhu", "macro": "Elena Park", "geo": "Tom Barrett", "credit": "Mara Quist"}
FEEDS = {"BBC Business": "https://feeds.bbci.co.uk/news/business/rss.xml", "CNBC": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
         "MarketWatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories", "Guardian Business": "https://www.theguardian.com/business/rss",
         "OilPrice": "https://oilprice.com/rss/main", "Federal Reserve": "https://www.federalreserve.gov/feeds/press_all.xml", "ECB": "https://www.ecb.europa.eu/rss/press.html",
         "RBA": "https://www.rba.gov.au/rss/rss-cb-media-releases.xml", "SEC 8-K": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&count=40&output=atom",
         "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss", "SEC Form 4": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&count=40&output=atom", "DCD (datacentre)": "https://www.datacenterdynamics.com/en/rss/"}
NOT_WIRED = {"ASX announcements": "no free machine feed (JS-rendered) - planned", "LSE RNS": "no free RSS - planned", "HKEX": "planned", "JPX/TDnet": "planned",
             "economic calendar": "no reliable free API - Fed/ECB/RBA press feeds used instead"}
DESK = {"macro": r"\b(fed|fomc|ecb|rba|boj|rate (cut|hike)|cpi|inflation|payrolls|jobs report|gdp|central bank|powell|lagarde|bullock|treasury yields?)\b",
        "geo": r"\b(war|missile|strike|sanction|opec|tariff|houthi|red sea|strait|shipping|pipeline|iran|russia|ukraine|israel|embargo)\b",
        "credit": r"\b(default|downgrade|credit spread|bankrupt|chapter 11|liquidity|bank run|margin call|vix|selloff|sell-off|crash|private credit|neocloud|coreweave|nebius|notes offering|convertible notes|credit facility|debt raise|gpu-backed|data center financing)\b"}
ASSET_KW = {"XOM": "exxon", "CVX": "chevron", "COP": "conocophillips", "OXY": "occidental", "WDS.AX": "woodside", "STO.AX": "santos", "JPM": "jpmorgan", "BAC": "bank of america",
            "WFC": "wells fargo", "C": "citigroup", "CBA.AX": "commonwealth bank", "WBC.AX": "westpac", "NAB.AX": "national australia bank", "ANZ.AX": r"\banz\b", "JNJ": "johnson & johnson",
            "UNH": "unitedhealth", "LLY": "eli lilly", "PFE": "pfizer", "MRK": "merck", "CSL.AX": r"\bcsl\b", "PG": "procter", "KO": "coca-cola", "WMT": "walmart", "WOW.AX": "woolworths",
            "CL=F": r"\b(oil|crude|brent|wti|opec)\b", "BZ=F": r"\b(brent|opec)\b", "NG=F": r"natural gas|\blng\b", "GC=F": r"\bgold\b", "AUDUSD=X": r"aussie dollar|\brba\b|australian dollar",
            "USDJPY=X": r"\byen\b|\bboj\b", "EURUSD=X": r"\becb\b|\beuro\b", "KRAKEN:XBTUSD": r"bitcoin|\bbtc\b", "KRAKEN:ETHUSD": r"ether(eum)?\b", "KRAKEN:SOLUSD": r"solana", "NVDA": r"nvidia", "AMD": r"\bamd\b", "AVGO": r"broadcom", "MSFT": r"microsoft", "META": r"\bmeta\b", "ORCL": r"oracle", "CRWV": r"coreweave", "NBIS": r"nebius", "SMCI": r"super micro", "MU": r"micron", "TSM": r"tsmc|taiwan semi", "GOOGL": r"alphabet|google", "AMZN": r"amazon|\baws\b"}
MARKETS = {"US": r"wall street|s&p|nasdaq|\bdow\b|\bfed\b|u\.s\.", "ASX": r"australia|asx|rba", "JP": r"japan|nikkei|boj|yen", "HK": r"hong kong|hang seng|china", "LSE": r"london|ftse|\bboe\b|britain|\buk\b", "CRYPTO": r"bitcoin|crypto|ether"}
A = "{http://www.w3.org/2005/Atom}"
def _get(u, t=12):
    from veteran_desk import feeds as _F   # follows 307/308 on py3.9
    return _F._OPENER.open(urllib.request.Request(u, headers=UA), timeout=t).read()
def now(): return dt.datetime.now(UTC)
def _ts(s):
    try: d = email.utils.parsedate_to_datetime(s); return d.astimezone(UTC) if d.tzinfo else d.replace(tzinfo=UTC)
    except Exception:
        try: return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)
        except Exception: return None
def headlines():
    H, health = [], {}
    for src, u in FEEDS.items():
        try:
            root = ET.fromstring(_get(u)); n = 0
            for it in list(root.iter("item")) + list(root.iter(A + "entry")):
                ti = (it.findtext("title") or it.findtext(A + "title") or "").strip(); le = it.find(A + "link")
                ln = it.findtext("link") or (le.get("href") if le is not None else "")
                pub = _ts(it.findtext("pubDate") or it.findtext(A + "updated") or "")
                if ti and pub: H.append(dict(title=ti[:220], link=(ln or "").strip(), source=src, published_utc=pub.isoformat(), available_at_utc=now().isoformat())); n += 1
                if n >= 25: break
            health[src] = "ok"
        except Exception as e: health[src] = "fail: " + str(e)[:60]
    try:   # tfo_massive_20261030: Benzinga (if entitled) / Massive reference news as an extra source
        from veteran_desk import massive as _MS
        _mn = _MS.news(limit=25); H += [dict(h, published_utc=_ts(h["published_utc"]).isoformat(), available_at_utc=now().isoformat()) for h in _mn if _ts(h["published_utc"])]
        health["massive"] = "ok" if _mn else "empty/not entitled"
    except Exception as e: health["massive"] = "fail: " + str(e)[:60]
    cut = now() - dt.timedelta(hours=36); H = [h for h in H if dt.datetime.fromisoformat(h["published_utc"]) >= cut]
    for h in H:
        t = h["title"].lower(); h["desk"] = [k for k, r in DESK.items() if re.search(r, t)] or ["wire"]; h["owner"] = TEAM[h["desk"][0]]
        h["assets"] = [a for a, r in ASSET_KW.items() if re.search(r, t)]; h["markets"] = [m for m, r in MARKETS.items() if re.search(r, t)]
        h["score"] = 2 * len(h["assets"]) + (3 if "credit" in h["desk"] else 0) + (2 if "geo" in h["desk"] else 0) + (1 if "macro" in h["desk"] else 0)
    H.sort(key=lambda h: (h["score"], h["published_utc"]), reverse=True); return H, health
REGIME_REQUIRED = ("vix", "spx_5d_pct", "spx_efficiency20", "hyg_lqd_20d_pct", "hy_oas_pct")
def _closes(yf, s):   # tfo_seatpipe_regimefb_20261030: yfinance empty/429 -> direct Yahoo chart v8 (pit.yahoo_chart) closes
    try: c = yf.Ticker(s).history(period="2mo").Close; assert len(c) > 21; return c
    except Exception:
        if os.environ.get("PYTEST_CURRENT_TEST"): raise
        import pandas as pd; from veteran_desk import pit as _P; return pd.Series([r[4] for r in _P.yahoo_chart(s, "3mo")])
def regime():
    try: import yfinance as yf
    except Exception: yf = None   # import failure -> every price input missing -> unknown
    g = {}
    try: v = yf.Ticker("^VIX").history(period="5d"); g["vix"] = round(float(v.Close.iloc[-1]), 2)
    except Exception: pass
    if "vix" not in g:   # yfinance empty -> FRED VIXCLS (prior-day close)
        try:
            from veteran_desk import feeds as _F; d, v = _F.fred_last("VIXCLS"); g["vix"] = round(v, 2); g["vix_src"] = "fred:VIXCLS " + d
        except Exception: pass
    try:
        s = _closes(yf, "^GSPC"); g["spx_5d_pct"] = round(float(s.iloc[-1] / s.iloc[-6] - 1) * 100, 2)
        g["spx_efficiency20"] = round(float(abs(s.iloc[-1] - s.iloc[-21]) / (s.diff().abs().iloc[-20:].sum() or 1)), 3)
    except Exception: pass
    try: h = _closes(yf, "HYG"); l = _closes(yf, "LQD"); r = h / l; g["hyg_lqd_20d_pct"] = round(float(r.iloc[-1] / r.iloc[-21] - 1) * 100, 2)
    except Exception: pass
    try:
        txt = _get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2").decode().strip().splitlines()
        last = [x for x in txt[1:] if x.split(",")[1] not in (".", "")][-1].split(","); g["hy_oas_pct"] = float(last[1]); g["hy_oas_date"] = last[0]
    except Exception: pass
    miss = [k for k in REGIME_REQUIRED if g.get(k) is None]   # FAIL-SAFE (TFO HT 22:55): any missing input -> 'unknown', never a defaulted 'calm'
    if miss: g["missing"] = miss; return "unknown", g
    V, M, O, C, E = g["vix"], g["spx_5d_pct"], g["hy_oas_pct"], g["hyg_lqd_20d_pct"], g["spx_efficiency20"]
    if V > 35 or O > 6.5 or M < -8: return "crisis", g
    if V > 25 or O > 5 or M < -4 or C < -2: return "risk-off", g
    if V > 18 or E < .25: return "choppy", g
    return "calm", g
def held_positions():
    try: D = json.load(open(os.path.join(BASE, "reports", "veteran_desk_book.json")))
    except Exception: return []
    return [dict(p, seat=k) for k, S in (D.get("seats") or {}).items() for p in S.get("positions", {}).values()]
def run():
    H, health = headlines(); flag, gauges = regime(); held = held_positions(); tags = {}
    for h in H:
        for a in h["assets"]: tags.setdefault(a, []).append(dict(title=h["title"], source=h["source"], link=h["link"], published_utc=h["published_utc"], desk=h["desk"]))
    at_risk = [dict(seat=p["seat"], ticker=p["ticker"], side=p["side"], headlines=tags[p["ticker"]][:3]) for p in held if p["ticker"] in tags]
    prev = set()
    try: prev = set(json.load(open(OUT)).get("alerted", []))
    except Exception: pass
    new = [(r["ticker"], h["title"]) for r in at_risk for h in r["headlines"] if (r["ticker"] + h["title"]) not in prev]
    os.makedirs(os.path.dirname(ALERTS), exist_ok=True)
    with open(ALERTS, "a") as f:
        for t, ti in new: f.write(json.dumps(dict(utc=now().isoformat(), ticker=t, headline=ti, urgent=True)) + "\n")
    out = dict(updated_utc=now().isoformat(), team=TEAM, regime=dict(flag=flag, gauges=gauges, owner=TEAM["credit"], sizing_effect="crisis halves new-trade risk (Marcus enforces); never blocks a tick"),
               top5=H[:5], headlines=H[:60], asset_tags=tags, positions_at_risk=at_risk, alerted=sorted(prev | {t + ti for t, ti in new}), new_alerts=new,
               feed_health=health, not_wired=NOT_WIRED, delays="RSS 1-15 min; SEC 8-K minutes; VIX/credit ETFs ~15 min (Yahoo); FRED HY spread prior day", info_only=True)
    d = os.path.join(BASE, "reports", "veteran_desk"); os.makedirs(d, exist_ok=True)
    for m in MARKETS:
        L = [h for h in H if m in h["markets"]][:8]
        open(os.path.join(d, f"news_brief_{m}.md"), "w").write(f"# {m} pre-open news brief - {TEAM['chief']} (regime: {flag})\n\n" + ("\n".join(f"- [{h['source']}] {h['title']} ({h['published_utc'][:16]}Z) {h['link']}" for h in L) or "- nothing market-specific"))
    t = OUT + ".tmp"; json.dump(out, open(t, "w"), indent=1); os.replace(t, OUT); return out
if __name__ == "__main__":
    o = run(); print(o["regime"]["flag"], o["regime"]["gauges"], len(o["headlines"]), {k: v for k, v in o["feed_health"].items() if v != "ok"})
