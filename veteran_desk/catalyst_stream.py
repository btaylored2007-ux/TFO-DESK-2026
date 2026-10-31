"""Live news and catalyst stream for the paper desk. Advisory only.

Polls paid wires first (FMP, EODHD, Massive, Unusual Whales, EIA) and free
fallbacks (ASX announcements, EDGAR 8-K and Form 4, LSE RNS, HKEX and TSE
disclosure, newswire RSS, Fed and central-bank calendars, the macro calendar,
FDA PDUFA dates). Each item is routed to a per-seat queue under
state/catalyst_stream/. High-urgency items are also appended to that seat's
advisory wake file in the same cycle.

This module never arms, sizes, exits, or takes a feed lock. A vendor error
backs that vendor off and is swallowed. Keys are read from ~/.tfo_secrets at
call time and are never logged.

  python3 -m veteran_desk.catalyst_stream            # daemon (launchd KeepAlive)
  python3 -m veteran_desk.catalyst_stream once       # one cycle
  python3 -m veteran_desk.catalyst_stream status
"""
import datetime as dt, email.utils, gzip, hashlib, json, os, re, signal, sys, time, urllib.error, urllib.request, xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, wait
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UTC = dt.timezone.utc
NY = ZoneInfo("America/New_York")
ROOT = None
CYCLE_S = int(os.environ.get("CATALYST_CYCLE_S", "20"))
HTTP_TIMEOUT = 5
SLA_S = 60
E2E_CAP_S = 6 * 3600
WAKE_AT = 70
P95_MIN_N = 5
SAMPLE_CAP = 200
UA = "TFO paper research brett-desk research@example.com"
A = "{http://www.w3.org/2005/Atom}"
STOP = False
SECRETS = os.path.expanduser("~/.tfo_secrets")
KEYS = {"fmp": ("fmp.env", "FMP_API_KEY"), "eodhd": ("eodhd.env", "EODHD_API_KEY"), "massive": ("massive.env", "MASSIVE_API_KEY"),
        "unusual_whales": ("unusual_whales.env", "UNUSUAL_WHALES_API_KEY"), "eia": ("eia.env", "EIA_API_KEY")}
# Hand universe (pit.UNIVERSE). Used when pit cannot be imported. Ticker -> specialist seats.
HAND = {"energy": ["XOM", "CVX", "COP", "EOG", "OXY", "WDS.AX", "STO.AX", "XLE"],
        "essentials": ["PG", "KO", "PEP", "WMT", "COST", "WOW.AX", "COL.AX", "NEE", "DUK", "SO", "AGL.AX", "ORG.AX", "XLP", "XLU"],
        "banks": ["JPM", "BAC", "WFC", "C", "CBA.AX", "WBC.AX", "NAB.AX", "ANZ.AX", "XLF"],
        "healthcare": ["JNJ", "UNH", "LLY", "MRK", "ABBV", "PFE", "CSL.AX", "COH.AX", "XLV"],
        "tech": ["NVDA", "AMD", "AVGO", "TSM", "MSFT", "GOOGL", "AMZN", "META", "ORCL", "CRWV", "NBIS", "SMCI", "MU", "QQQ", "SMH", "XLK"],
        "crypto": ["KRAKEN:XBTUSD", "KRAKEN:ETHUSD", "KRAKEN:SOLUSD", "KRAKEN:XRPUSD", "KRAKEN:LINKUSD", "KRAKEN:ADAUSD"],
        "fx_cmdty": ["AUDUSD=X", "USDJPY=X", "EURUSD=X", "GBPUSD=X", "USDCAD=X", "NZDUSD=X", "AUDJPY=X", "CL=F", "BZ=F", "NG=F", "GC=F", "SI=F", "HG=F"],
        "macro": ["^TNX", "CL=F", "BZ=F", "NG=F", "AUDUSD=X", "USDJPY=X", "EURUSD=X", "GC=F", "^GSPC"]}
GROUP_SEATS = {"energy": ("energy", "news_filings", "price_action", "trend"),
               "essentials": ("essentials_utilities", "news_filings", "price_action", "trend"),
               "banks": ("banks", "news_filings", "price_action", "trend"),
               "healthcare": ("healthcare", "news_filings", "price_action", "trend"),
               "tech": ("tech_ai_infra", "price_action", "trend"),
               "crypto": ("crypto", "trend"),
               "fx_cmdty": ("fx_commodities", "macro", "price_action", "trend"),
               "macro": ("macro", "fx_commodities", "price_action", "trend")}
ALIAS = {"BTC": "KRAKEN:XBTUSD", "XBT": "KRAKEN:XBTUSD", "ETH": "KRAKEN:ETHUSD", "SOL": "KRAKEN:SOLUSD", "XRP": "KRAKEN:XRPUSD"}
HAND_CIK = {34088: "XOM", 93410: "CVX", 19617: "JPM", 70858: "BAC", 200406: "JNJ", 731766: "UNH", 80424: "PG", 21344: "KO", 59478: "LLY", 72971: "WFC", 806085: "LEHMQ"}
SECTOR_SEATS = {"energy": ("energy",), "financials": ("banks",), "financial services": ("banks",), "health care": ("healthcare",),
                "healthcare": ("healthcare",), "information technology": ("tech_ai_infra",), "technology": ("tech_ai_infra",),
                "communication services": ("tech_ai_infra",), "consumer staples": ("essentials_utilities",), "utilities": ("essentials_utilities",)}
EOD_SFX = ((".US", ""), (".AU", ".AX"), (".LSE", ".L"), (".TSE", ".T"), (".HK", ".HK"), (".KO", ".KS"), (".KQ", ".KQ"),
           (".TW", ".TW"), (".TWO", ".TWO"), (".SI", ".SI"), (".SS", ".SS"), (".SZ", ".SZ"), (".NSE", ".NS"))
ASIA_LSE = (".AU", ".LSE", ".TSE", ".HK", ".KO", ".KQ", ".TW", ".TWO", ".SI", ".SS", ".SZ", ".NSE")
# Monday or Wednesday US holidays shift the Wednesday 10:30 ET WPSR to Thursday.
EIA_HOLIDAYS = {"2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
                "2026-02-16", "2026-05-25", "2026-09-07"}
MATERIAL_8K = {"1.01", "1.03", "2.01", "2.02", "4.02", "5.02"}
DECISION_RE = re.compile(r"\b(rate decision|interest rate decision|cash rate|fomc|monetary policy decision|policy rate|bank rate|ocr decision|funds rate)\b", re.I)
SPEAKER_RE = re.compile(r"\b(speak|speech|speaks|remarks|testimony|press conference|presser)\b", re.I)
HALT_RE = re.compile(r"\b(trading halt|halted|bankruptcy|chapter 11|chapter 7|default|complete response letter)\b", re.I)
FDA_ACT_RE = re.compile(r"\bfda\b.{0,40}\b(approv|reject|refus)", re.I)
GUIDE_RE = re.compile(r"\b(guidance cut|cuts guidance|profit warning|downgrade|investigation|offering|dilution)\b", re.I)
KW = [(re.compile(r"\b(oil|crude|brent|wti|opec|natural gas|\blng\b|woodside|exxon|chevron)\b", re.I), ("energy", "fx_commodities")),
      (re.compile(r"\b(fed|fomc|ecb|\brba\b|\bboj\b|\bboe\b|powell|lagarde|central bank|rate decision)\b", re.I), ("macro", "fx_commodities")),
      (re.compile(r"\b(bitcoin|\bbtc\b|ethereum|ether|crypto|solana)\b", re.I), ("crypto",)),
      (re.compile(r"\b(fda|pdufa|drug approval)\b", re.I), ("healthcare",)),
      (re.compile(r"\b(jpmorgan|goldman|wells fargo|\bbank run\b)\b", re.I), ("banks",)),
      (re.compile(r"\b(nvidia|semiconductor|microsoft|neocloud|data cent(?:er|re))\b", re.I), ("tech_ai_infra",))]
POS = {"beat": 2, "beats": 2, "surge": 2, "upgrade": 2, "approved": 2, "approval": 2, "record": 1, "buyback": 1.5, "profit": 1, "growth": 1}
NEG = {"miss": -2, "plunge": -2, "downgrade": -2, "bankruptcy": -3, "halt": -2, "halted": -2, "default": -3, "fraud": -3, "recall": -2, "reject": -2, "rejected": -2, "dilution": -1.5, "warning": -2, "investigation": -2}
HEADLINE_PAID = ("fmp_news", "fmp_press", "eodhd_news", "massive_news", "uw_news")
RSS_WIRES = (("bbc", "https://feeds.bbci.co.uk/news/business/rss.xml"), ("cnbc", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
             ("marketwatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"), ("guardian", "https://www.theguardian.com/business/rss"),
             ("oilprice", "https://oilprice.com/rss/main"), ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss"))
CB_FEEDS = (("fed", "https://www.federalreserve.gov/feeds/press_all.xml"), ("fed_speeches", "https://www.federalreserve.gov/feeds/speeches.xml"),
            ("fed_monetary", "https://www.federalreserve.gov/feeds/press_monetary.xml"), ("ecb", "https://www.ecb.europa.eu/rss/press.html"),
            ("boe", "https://www.bankofengland.co.uk/rss/news"), ("rba", "https://www.rba.gov.au/rss/rss-cb-media-releases.xml"),
            ("boc", "https://www.bankofcanada.ca/content_type/press-releases/feed/"))
LSE_URLS = ("https://www.investegate.co.uk/Rss.aspx",)
HKEX_URLS = ("https://www.hkexnews.hk/listedco/listconews/sehk/ncfeed.xml", "https://www1.hkexnews.hk/listedco/listconews/sehk/ncfeed.xml")
TDNET_URLS = ("https://webapi.yanoshin.jp/webapi/tdnet/list/recent.rss",)
EPIC_SKIP = {"THE", "AND", "FOR", "NEW", "ALL", "PLC", "LTD", "INC", "RNS", "PDF"}

class Inactive(Exception):
    pass
class VendorError(Exception):
    pass

def base():
    return ROOT or os.path.join(BASE, "state", "catalyst_stream")
def _p(*parts):
    return os.path.join(base(), *parts)
def now_utc():
    return dt.datetime.now(UTC)
def redact(s, secrets=()):
    t = str(s if s is not None else "")
    t = re.sub(r"(api[_-]?key|apikey|api_token|access_token|bearer)\s*[=:]\s*\S+", r"\1=<redacted>", t, flags=re.I)
    t = re.sub(r"Bearer\s+\S+", "Bearer <redacted>", t)
    for sec in secrets:
        if sec and len(sec) >= 6 and sec in t: t = t.replace(sec, "<redacted>")
    return t[:240]
def read_key(fname, var):
    """Runtime only. Never log the return value."""
    k = os.environ.get(var)
    if k and k.strip(): return k.strip().strip('"').strip("'")
    path = os.path.join(SECRETS, fname if str(fname).endswith(".env") else str(fname) + ".env")
    try:
        for ln in open(path):
            s = ln.strip()
            if s.startswith(var + "="): return s.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception: return None
    return None
def load_json(path, default):
    try: return json.load(open(path))
    except Exception: return default
def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(obj, f, indent=1, default=str)
    os.replace(tmp, path)
def parse_time(s):
    if not s: return None
    raw = str(s).strip()
    if not raw: return None
    try:
        t = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        if "T" not in t and re.match(r"\d{4}-\d{2}-\d{2} ", t): t = t.replace(" ", "T", 1)
        d = dt.datetime.fromisoformat(t)
        if d.tzinfo is None: d = d.replace(tzinfo=UTC)
        return d.astimezone(UTC)
    except Exception: pass
    try:
        d = email.utils.parsedate_to_datetime(raw)
        if d is None: return None
        if d.tzinfo is None: d = d.replace(tzinfo=UTC)
        return d.astimezone(UTC)
    except Exception: return None
def _text(body):
    if isinstance(body, str): return body
    b = body or b""
    if b[:2] == b"\x1f\x8b":
        try: b = gzip.decompress(b)
        except Exception: pass
    return b.decode("utf-8", "replace")
def _loads(body):
    try: return json.loads(_text(body))
    except Exception: raise VendorError("bad json")
def _rows(js):
    if isinstance(js, list): return js
    if isinstance(js, dict):
        for k in ("data", "results", "earnings"):
            v = js.get(k)
            if isinstance(v, list): return v
            if isinstance(v, dict) and isinstance(v.get("data"), list): return v["data"]
    return []
def http_get(url, headers, timeout):
    req = urllib.request.Request(url, headers=dict({"User-Agent": UA, "Accept": "application/json, application/xml, text/xml, */*"}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
def call(get, url, headers, secrets=(), timeout=HTTP_TIMEOUT):
    try: status, body = get(url, headers or {}, timeout)
    except Exception as e: raise VendorError(redact(type(e).__name__, secrets)) from None
    try: status = int(status)
    except Exception: raise VendorError("bad status")
    if status != 200: raise VendorError("HTTP %s" % status)
    return body
def desk_sym(code):
    c = str(code or "").upper().strip()
    if not c: return ""
    for sfx, rep in EOD_SFX:
        if c.endswith(sfx):
            base_ = c[: -len(sfx)]
            return base_ + rep if rep else base_
    return c
def lexicon(title):
    words = re.findall(r"[a-z']+", (title or "").lower())
    if not words: return 0.0
    s = 0.0
    for w in words: s += POS.get(w, 0) + NEG.get(w, 0)
    return max(-1.0, min(1.0, s / 4.0))
def score_sentiment(item):
    lex = lexicon(item.get("title"))
    v = item.get("vendor_sentiment")
    if isinstance(v, (int, float)):
        v = max(-1.0, min(1.0, float(v)))
        return round((lex + v) / 2.0, 3)
    return round(lex, 3)
def _soon(src, now, hours):
    t = parse_time(src)
    if not t or now is None: return False
    d = (t - now).total_seconds()
    return -3600 <= d <= hours * 3600
def score_urgency(item, now):
    k = item.get("kind") or "news"
    title = item.get("title") or ""
    base_u = {"news": 20, "press_release": 45, "8k": 60, "form4": 40, "earnings": 40, "flow": 50, "cb_decision": 55,
              "cb_speaker": 45, "economic": 35, "eia": 45, "pdufa": 65, "asx": 40, "rns": 40, "hkex": 40, "tdnet": 40}.get(k, 20)
    u = base_u
    if HALT_RE.search(title) or FDA_ACT_RE.search(title): u = max(u, 90)
    if GUIDE_RE.search(title): u = max(u, 75)
    if set(item.get("items") or []) & MATERIAL_8K: u = max(u, 85)
    try:
        if k == "flow" and float(item.get("premium") or 0) >= 250000: u = max(u, 80)
    except Exception: pass
    if k in ("earnings", "cb_decision", "cb_speaker", "eia", "economic") and _soon(item.get("source_utc"), now, 6): u = max(u, 80)
    if k == "pdufa" and _soon(item.get("source_utc"), now, 24): u = max(u, 85)
    u += int(min(10, abs(float(item.get("sentiment") or 0)) * 10))
    return max(0, min(100, int(u)))
def load_universe():
    try:
        from veteran_desk import pit as P
        src = P.UNIVERSE
    except Exception:
        src = HAND
    idx = {}
    for g, seats in GROUP_SEATS.items():
        for sym in src.get(g) or []:
            idx.setdefault(str(sym).upper(), set()).update(seats)
    return idx
def seats_for(item, index):
    seats = set()
    kind = item.get("kind") or "news"
    if kind == "pdufa": seats.add("healthcare")
    if kind == "eia": seats.update(("energy", "fx_commodities"))
    if kind in ("cb_decision", "cb_speaker"): seats.update(("macro", "fx_commodities"))
    if kind == "economic":
        seats.add("macro")
        if item.get("ccy"): seats.add("fx_commodities")
    if kind in ("8k", "form4", "press_release", "asx", "rns", "hkex", "tdnet", "earnings"): seats.add("news_filings")
    if kind == "flow": seats.add("price_action")
    for t in item.get("tickers") or []:
        key = ALIAS.get(str(t).upper(), str(t).upper())
        seats.update(index.get(key) or ())
        if key.endswith(".HK"):
            seats.update(index.get(key[:-3].lstrip("0") + ".HK") or ())
    sec = str(item.get("sector") or "").strip().lower()
    if sec in SECTOR_SEATS: seats.update(SECTOR_SEATS[sec])
    blob = item.get("title") or ""
    for rx, extra in KW:
        if rx.search(blob): seats.update(extra)
    route = "matched"
    if not seats:
        seats.add("news_filings"); route = "unmatched_wire"
    return sorted(seats), route
def _item(**k):
    k.setdefault("tickers", [])
    k["title"] = str(k.get("title") or "")[:220]
    k["link"] = str(k.get("link") or "")[:300]
    return k
def _xml_items(raw):
    root = ET.fromstring(_text(raw))
    out = []
    for it in list(root.iter("item")) + list(root.iter(A + "entry")):
        title = (it.findtext("title") or it.findtext(A + "title") or "").strip()
        link = it.findtext("link") or ""
        le = it.find(A + "link")
        if le is not None and le.get("href"): link = le.get("href")
        pub = it.findtext("pubDate") or it.findtext(A + "updated") or it.findtext(A + "published") or ""
        summ = it.findtext("summary") or it.findtext(A + "summary") or it.findtext("description") or ""
        if title: out.append((title, (link or "").strip(), pub, summ))
    return out
def parse_rss(raw, source, kind="news"):
    items = []
    for title, link, pub, _s in _xml_items(raw):
        t = parse_time(pub)
        items.append(_item(kind=kind, title=title, link=link, source=source, source_utc=t.isoformat() if t else None, tier="free"))
    return items
def parse_fmp_news(raw, source, kind):
    items = []
    for r in _rows(_loads(raw)):
        if not isinstance(r, dict) or not r.get("title"): continue
        sym = desk_sym(r.get("symbol") or r.get("ticker") or "")
        t = parse_time(r.get("publishedDate") or r.get("date"))
        items.append(_item(kind=kind, title=r.get("title"), link=r.get("url") or r.get("link") or "", tickers=[sym] if sym else [],
                           source=source, source_utc=t.isoformat() if t else None, tier="paid", sector=r.get("sector")))
    return items
def parse_fmp_earnings(raw, now):
    items = []
    for r in _rows(_loads(raw)):
        if not isinstance(r, dict): continue
        sym = desk_sym(r.get("symbol") or "")
        day = str(r.get("date") or "")[:10]
        if not sym or not day: continue
        when = "bmo" if "bmo" in str(r.get("time") or "").lower() or str(r.get("time") or "").lower().startswith("before") else ("amc" if "amc" in str(r.get("time") or "").lower() else "tbc")
        hour = 12 if when == "bmo" else (21 if when == "amc" else 16)
        try: src = dt.datetime.fromisoformat(day + "T%02d:00:00+00:00" % hour)
        except Exception: src = None
        items.append(_item(kind="earnings", title="Earnings %s %s (FMP)" % (sym, when.upper()), link="", tickers=[sym], source="fmp_earnings",
                           source_utc=src.isoformat() if src else None, tier="paid"))
    return items
def parse_fmp_econ(raw):
    items = []
    for r in _rows(_loads(raw)):
        if not isinstance(r, dict): continue
        title = r.get("event") or r.get("title") or ""
        if not title: continue
        if SPEAKER_RE.search(title): kind = "cb_speaker"
        elif DECISION_RE.search(title): kind = "cb_decision"
        else: kind = "economic"
        impact = str(r.get("impact") or "")
        if kind == "economic" and impact.lower() not in ("high", "medium", "3", "2"): continue
        t = parse_time(r.get("date"))
        ccy = r.get("currency") or r.get("country")
        items.append(_item(kind=kind, title=title, link="", source="fmp_economic", source_utc=t.isoformat() if t else None, tier="paid", ccy=ccy, impact=impact))
    return items
def parse_fmp_8k(raw):
    items = []
    for r in _rows(_loads(raw)):
        if not isinstance(r, dict): continue
        sym = desk_sym(r.get("symbol") or "")
        t = parse_time(r.get("acceptedDate") or r.get("fillingDate") or r.get("filingDate") or r.get("date"))
        items.append(_item(kind="8k", title="8-K %s" % (sym or r.get("formType") or ""), link=r.get("link") or r.get("finalLink") or "",
                           tickers=[sym] if sym else [], source="fmp_8k", source_utc=t.isoformat() if t else None, tier="paid"))
    return items
def parse_eodhd_news(raw):
    items = []
    js = _loads(raw)
    rows = js if isinstance(js, list) else _rows(js)
    for r in rows:
        if not isinstance(r, dict) or not r.get("title"): continue
        syms = []
        for s in r.get("symbols") or r.get("tickers") or []:
            d = desk_sym(s if isinstance(s, str) else (s.get("code") or s.get("symbol") or ""))
            if d: syms.append(d)
        sent = r.get("sentiment") if isinstance(r.get("sentiment"), dict) else {}
        pol = sent.get("polarity")
        t = parse_time(r.get("date"))
        items.append(_item(kind="news", title=r.get("title"), link=r.get("link") or "", tickers=syms[:6], source="eodhd_news",
                           source_utc=t.isoformat() if t else None, tier="paid", vendor_sentiment=pol if isinstance(pol, (int, float)) else None))
    return items
def parse_eodhd_earnings(raw):
    items = []
    for r in _rows(_loads(raw)):
        if not isinstance(r, dict): continue
        code = str(r.get("code") or "")
        if not any(code.upper().endswith(s) for s in ASIA_LSE): continue
        sym = desk_sym(code)
        day = str(r.get("report_date") or "")[:10]
        if not sym or not day: continue
        try: src = dt.datetime.fromisoformat(day + "T12:00:00+00:00")
        except Exception: src = None
        items.append(_item(kind="earnings", title="Earnings %s (EODHD %s)" % (sym, code), link="", tickers=[sym], source="eodhd_earnings",
                           source_utc=src.isoformat() if src else None, tier="paid"))
    return items
def parse_massive(raw, source):
    items = []
    js = _loads(raw)
    for r in (js.get("results") or []) if isinstance(js, dict) else []:
        title = (r.get("title") or "").strip()
        pub = r.get("published_utc") or r.get("published")
        if not title or not pub: continue
        ticks = []
        for t in r.get("tickers") or []:
            if isinstance(t, str): ticks.append(t.upper())
            elif isinstance(t, dict) and (t.get("ticker") or t.get("name")): ticks.append(str(t.get("ticker") or t.get("name")).upper())
        tm = parse_time(pub)
        items.append(_item(kind="news", title=title, link=r.get("article_url") or r.get("url") or "", tickers=ticks[:6], source=source,
                           source_utc=tm.isoformat() if tm else None, tier="paid"))
    return items
def parse_uw_news(raw):
    items = []
    for r in _rows(_loads(raw)):
        if not isinstance(r, dict): continue
        title = r.get("headline") or r.get("title") or ""
        if not title: continue
        sym = str(r.get("ticker") or r.get("symbol") or "").upper()
        t = parse_time(r.get("created_at") or r.get("published_at") or r.get("date"))
        items.append(_item(kind="news", title=title, link=r.get("url") or "", tickers=[sym] if sym else [], source="uw_news",
                           source_utc=t.isoformat() if t else None, tier="paid"))
    return items
def parse_uw_flow(raw):
    items = []
    for r in _rows(_loads(raw)):
        if not isinstance(r, dict): continue
        sym = str(r.get("ticker") or "").upper()
        if not sym: continue
        prem = r.get("total_premium")
        t = parse_time(r.get("created_at") or r.get("executed_at"))
        title = "Flow %s %s premium %s" % (sym, r.get("type") or r.get("alert_rule") or "", prem)
        items.append(_item(kind="flow", title=title, link="", tickers=[sym], source="uw_flow", source_utc=t.isoformat() if t else None,
                           tier="paid", premium=prem))
    return items
def parse_edgar(raw, kind, cik_map):
    items = []
    for title, link, pub, summ in _xml_items(raw):
        m = re.search(r"\((\d{6,10})\)", title)
        cik = int(m.group(1)) if m else None
        sym = cik_map.get(cik) if cik else None
        found = sorted(set(re.findall(r"Item\s+(\d\.\d\d)", summ or "")))
        t = parse_time(pub)
        items.append(_item(kind=kind, title=re.sub(r"\s+", " ", title)[:220], link=link, tickers=[sym] if sym else [], source="edgar_" + kind,
                           source_utc=t.isoformat() if t else None, tier="free", items=found, cik=cik))
    return items
def parse_asx(raw):
    items = []
    html = _text(raw)
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) < 3: continue
        code = re.sub(r"<[^>]+>", "", tds[0]).strip()
        if not re.fullmatch(r"[A-Z0-9]{3,6}", code): continue
        m = re.search(r"(\d{2}/\d{2}/\d{4}).*?(\d{1,2}:\d{2}\s*[ap]m)", re.sub(r"<[^>]+>", " ", tds[1]), re.S | re.I)
        if not m: continue
        try:
            from zoneinfo import ZoneInfo as _Z
            t = dt.datetime.strptime(m.group(1) + " " + m.group(2).replace(" ", ""), "%d/%m/%Y %I:%M%p").replace(tzinfo=_Z("Australia/Sydney")).astimezone(UTC)
        except Exception: t = None
        a = re.search(r"<a[^>]*>(.*?)</a>", tr, re.S)
        head = re.sub(r"<[^>]+>", " ", a.group(1) if a else tds[-1])
        head = re.sub(r"\s+", " ", head).strip()[:180]
        ps = "price-sensitive" in tr.lower() or "price sensitive" in tr.lower()
        items.append(_item(kind="asx", title=("PS: " if ps else "") + head, link="", tickers=[code + ".AX"], source="asx_announcements",
                           source_utc=t.isoformat() if t else None, tier="free", price_sensitive=ps))
    return items
def _epic(title):
    m = re.search(r"\b([A-Z0-9]{2,5})\.L\b", title or "")
    if m: return m.group(1) + ".L"
    m = re.match(r"([A-Z]{2,5})\b", (title or "").strip())
    if m and m.group(1) not in EPIC_SKIP: return m.group(1) + ".L"
    return ""
def parse_lse(raw):
    items = []
    for title, link, pub, _s in _xml_items(raw):
        t = parse_time(pub); epic = _epic(title)
        items.append(_item(kind="rns", title=title, link=link, tickers=[epic] if epic else [], source="lse_rns", source_utc=t.isoformat() if t else None, tier="free"))
    return items
def parse_hkex(raw):
    items = []
    try: rows = _xml_items(raw)
    except Exception: rows = []
    if not rows:
        for m in re.finditer(r"(\d{4,5})[^0-9]{0,12}([^<\n]{8,140})", _text(raw)):
            rows.append((m.group(2).strip(), "", "", ""))
    for title, link, pub, _s in rows:
        codes = re.findall(r"\b(\d{4,5})\b", title)
        ticks = []
        for c in codes[:3]:
            try: ticks.append(str(int(c)) + ".HK")
            except Exception: pass
        t = parse_time(pub)
        items.append(_item(kind="hkex", title=title, link=link, tickers=ticks, source="hkex_disclosure", source_utc=t.isoformat() if t else None, tier="free"))
    return items
def parse_tdnet(raw):
    items = []
    for title, link, pub, _s in _xml_items(raw):
        ticks = []
        for c in re.findall(r"\b(\d{4})\b", title):
            if 1990 <= int(c) <= 2035: continue
            ticks.append(c + ".T")
        t = parse_time(pub)
        items.append(_item(kind="tdnet", title=title, link=link, tickers=ticks[:3], source="tdnet_yanoshin", source_utc=t.isoformat() if t else None, tier="free"))
    return items
def parse_fda(raw):
    s = _text(raw)
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", s, flags=re.S)
    t = re.sub(r"<[^>]+>", "|", t)
    t = re.sub(r"\s*\|[\s|]*", "|", t)
    items = []
    for m in re.finditer(r"\(\|([A-Z.\-]{1,6})\|[^|]*\)\|([^|]{2,120})\|(\d\d/\d\d/\d{4})\|[^|]*\|(pending)", t):
        try: day = dt.datetime.strptime(m.group(3), "%m/%d/%Y").date()
        except Exception: continue
        src = dt.datetime(day.year, day.month, day.day, 20, 0, tzinfo=UTC)
        items.append(_item(kind="pdufa", title="PDUFA %s: %s" % (m.group(1), m.group(2).strip()[:80]), link="", tickers=[m.group(1)],
                           source="rtt_fda", source_utc=src.isoformat(), tier="free"))
    return items
def parse_ff(raw):
    items = []
    js = _loads(raw)
    if not isinstance(js, list): return items
    for e in js:
        if not isinstance(e, dict): continue
        title = e.get("title") or ""
        if SPEAKER_RE.search(title): kind = "cb_speaker"
        elif DECISION_RE.search(title): kind = "cb_decision"
        elif e.get("impact") == "High": kind = "economic"
        else: continue
        t = parse_time(e.get("date"))
        items.append(_item(kind=kind, title=title, link="", source="ff_calendar", source_utc=t.isoformat() if t else None, tier="free_fallback", ccy=e.get("country")))
    return items
def next_eia(now):
    """Next WPSR: Wednesday 10:30 ET, Thursday when that Wednesday or its Monday is a US holiday."""
    local = now.astimezone(NY).date()
    for i in range(0, 16):
        day = local + dt.timedelta(days=i)
        if day.weekday() != 2: continue
        monday = day - dt.timedelta(days=2)
        rel = day + dt.timedelta(days=1) if (day.isoformat() in EIA_HOLIDAYS or monday.isoformat() in EIA_HOLIDAYS) else day
        stamp = dt.datetime(rel.year, rel.month, rel.day, 10, 30, tzinfo=NY)
        if stamp > now: return stamp.astimezone(UTC)
    return None
def parse_eia_schedule(raw, now):
    best = None
    for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", _text(raw)):
        try: day = dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except Exception: continue
        stamp = dt.datetime(day.year, day.month, day.day, 10, 30, tzinfo=NY).astimezone(UTC)
        if stamp > now and (best is None or stamp < best): best = stamp
    return best
def eia_item(when, value, tier, source):
    title = "EIA weekly petroleum status release %s ET" % when.astimezone(NY).strftime("%Y-%m-%d %H:%M")
    if value is not None: title += " (latest crude stocks %s)" % value
    return _item(kind="eia", title=title, link="https://www.eia.gov/petroleum/supply/weekly/schedule.php", tickers=["CL=F", "BZ=F"],
                 source=source, source_utc=when.isoformat(), tier=tier)
def parse_calendar_file(path, now):
    """Local macro/earnings file written by the slow calendar job. Read only."""
    try: cal = json.load(open(path))
    except Exception: return [], []
    earn, macro = [], []
    for e in cal.get("next72") or []:
        if not isinstance(e, dict): continue
        title = e.get("title") or ""
        src = e.get("when_utc")
        if e.get("type") == "earnings" and e.get("sym"):
            earn.append(_item(kind="earnings", title=title or ("Earnings " + e["sym"]), tickers=[e["sym"]], source="calendar_latest", source_utc=src, tier="free_fallback"))
        elif e.get("sym") == "MACRO" or e.get("type", "").startswith("macro"):
            kind = "cb_decision" if DECISION_RE.search(title) else ("cb_speaker" if SPEAKER_RE.search(title) else "economic")
            macro.append(_item(kind=kind, title=title, tickers=[], source="calendar_latest", source_utc=src, tier="free_fallback", ccy=e.get("ccy")))
    return earn, macro
def cik_map(get, secrets_key=None):
    """Hand CIKs plus a cached SEC company_tickers file. A failed refresh keeps the cache."""
    path = _p("sec_cik_cache.json")
    cached = load_json(path, {})
    m = dict(HAND_CIK)
    try:
        for k, v in (cached.get("map") or {}).items(): m[int(k)] = v
    except Exception: pass
    stale = True
    try: stale = (time.time() - os.path.getmtime(path)) > 86400
    except Exception: stale = True
    if not stale or get is None: return m
    try:
        body = call(get, "https://www.sec.gov/files/company_tickers.json", {"User-Agent": UA, "Accept": "application/json"}, secrets=(secrets_key or "",))
        js = _loads(body)
        got = {}
        for v in (js.values() if isinstance(js, dict) else []):
            if isinstance(v, dict) and v.get("cik_str") and v.get("ticker"): got[int(v["cik_str"])] = str(v["ticker"]).upper().replace(".", "-")
        if got:
            write_json(path, {"map": {str(k): v for k, v in got.items()}})
            m.update(got)
    except Exception: pass
    return m
def _backoff_open(state, name, ts):
    try: return float((state.get(name) or {}).get("next_ok") or 0) > ts
    except Exception: return False
def _backoff_fail(state, name, err, ts):
    fails = int((state.get(name) or {}).get("fails") or 0) + 1
    delay = min(900, 30 * (2 ** (fails - 1)))
    state[name] = {"fails": fails, "next_ok": ts + delay, "last_err": redact(err)[:160]}
def _backoff_ok(state, name):
    state.pop(name, None)
def _run(name, fn):
    """Worker. Does not touch backoff state (the caller records that on the main thread)."""
    try:
        items = fn() or []
        return name, "ok", list(items), None
    except Inactive as e:
        return name, "inactive", [], redact(str(e))
    except Exception as e:
        msg = redact(e.args[0] if getattr(e, "args", None) else type(e).__name__)
        return name, "fail", [], msg
def _dedupe_key(item):
    title = re.sub(r"[^a-z0-9]+", " ", (item.get("title") or "").lower()).strip()
    ticks = ",".join(sorted(str(t).upper() for t in (item.get("tickers") or [])))
    day = str(item.get("source_utc") or item.get("fetched_utc") or "")[:10]
    return hashlib.sha1(("%s|%s|%s|%s" % (item.get("kind"), ticks, title, day)).encode()).hexdigest()[:20]
def _append(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f: f.write(json.dumps(obj, default=str) + "\n")
def _trim(path, keep=1200, limit=800000):
    try:
        if os.path.getsize(path) < limit: return
        lines = open(path).read().splitlines()[-keep:]
        tmp = path + ".tmp"
        with open(tmp, "w") as f: f.write("\n".join(lines) + ("\n" if lines else ""))
        os.replace(tmp, path)
    except Exception: pass
def tail(path, n):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2); f.seek(max(0, f.tell() - 65536)); data = f.read().decode("utf-8", "replace")
    except Exception: return []
    rows = []
    for ln in data.splitlines():
        try: rows.append(json.loads(ln))
        except Exception: continue
    return rows[-n:]
def p95(xs):
    if not xs: return None
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(__import__("math").ceil(0.95 * len(s))) - 1))
    return round(s[i], 3)
def for_seat(seat, n=12):
    """Local read of one seat's advisory queue. Missing files return None. Never raises on a bad line."""
    if not seat: return None
    q = tail(_p("queues", seat + ".jsonl"), n)
    w = tail(_p("wake", seat + ".jsonl"), 5)
    if not q and not w: return None
    return dict(advisory_only=True, blocks_trade=False, blocks_feed=False,
                note="Advisory catalyst input. Does not arm, size, exit, or block a feed.", items=q, wake=w)
def attach(out, seat):
    """Seat pack hook. Swallows every error so a dead stream cannot block a pack."""
    try:
        adv = for_seat(seat)
        if adv: out["catalyst_stream"] = adv
    except Exception:
        out["catalyst_stream_note"] = "unavailable"
    return out
def _vendors(ctx):
    """name -> zero-arg fetch. Each one isolates its own HTTP."""
    def k(name):
        spec = KEYS[name]
        key = ctx["key"](spec[0], spec[1])
        if not key: raise Inactive("%s key missing" % name)
        return key
    def fmp(path, params):
        key = k("fmp")
        q = dict(params); q["apikey"] = key
        import urllib.parse
        return call(ctx["get"], "https://financialmodelingprep.com/stable/" + path + "?" + urllib.parse.urlencode(q), {}, secrets=(key,))
    def eodhd(path, params):
        key = k("eodhd")
        import urllib.parse
        q = dict(params); q.update(api_token=key, fmt="json")
        return call(ctx["get"], "https://eodhd.com/api/" + path + "?" + urllib.parse.urlencode(q), {}, secrets=(key,))
    today = ctx["now"].date()
    end = today + dt.timedelta(days=7)
    def fmp_news(): return parse_fmp_news(fmp("news/stock-latest", {"page": 0, "limit": 40}), "fmp_news", "news")
    def fmp_press(): return parse_fmp_news(fmp("news/press-releases-latest", {"page": 0, "limit": 20}), "fmp_press", "press_release")
    def fmp_earn(): return parse_fmp_earnings(fmp("earnings-calendar", {"from": today.isoformat(), "to": end.isoformat()}), ctx["now"])
    def fmp_econ(): return parse_fmp_econ(fmp("economic-calendar", {"from": today.isoformat(), "to": end.isoformat()}))
    def fmp_8k(): return parse_fmp_8k(fmp("sec-filings-search/form-type", {"formType": "8-K", "from": (today - dt.timedelta(days=2)).isoformat(), "to": today.isoformat(), "page": 0, "limit": 40}))
    def eod_news(): return parse_eodhd_news(eodhd("news", {"limit": 40}))
    def eod_earn(): return parse_eodhd_earnings(eodhd("calendar/earnings", {"from": today.isoformat(), "to": (today + dt.timedelta(days=21)).isoformat()}))
    def massive():
        key = k("massive")
        h = {"Authorization": "Bearer " + key, "Accept": "application/json"}
        err = None
        for path, src in (("/benzinga/v2/news?limit=30&sort=published.desc", "massive_benzinga"), ("/v2/reference/news?limit=30&order=desc&sort=published_utc", "massive_news")):
            try:
                body = call(ctx["get"], "https://api.massive.com" + path, h, secrets=(key,))
                items = parse_massive(body, src)
                if items: return items
            except VendorError as e: err = e
        if err: raise err
        return []
    def uw_news():
        key = k("unusual_whales")
        body = call(ctx["get"], "https://api.unusualwhales.com/api/news/headlines?limit=40", {"Authorization": "Bearer " + key}, secrets=(key,))
        return parse_uw_news(body)
    def uw_flow():
        key = k("unusual_whales")
        body = call(ctx["get"], "https://api.unusualwhales.com/api/option-trades/flow-alerts?limit=30", {"Authorization": "Bearer " + key}, secrets=(key,))
        return parse_uw_flow(body)
    def eia():
        key = k("eia")
        import urllib.parse
        q = urllib.parse.urlencode([("api_key", key), ("frequency", "weekly"), ("data[0]", "value"), ("facets[series][]", "WCESTUS1"),
                                     ("sort[0][column]", "period"), ("sort[0][direction]", "desc"), ("length", "2")])
        body = call(ctx["get"], "https://api.eia.gov/v2/petroleum/stoc/wstk/data/?" + q, {}, secrets=(key,))
        js = _loads(body)
        data = ((js.get("response") or {}).get("data") or []) if isinstance(js, dict) else []
        value = data[0].get("value") if data else None
        when = None
        try:
            sched = call(ctx["get"], "https://www.eia.gov/petroleum/supply/weekly/schedule.php", {"Accept": "text/html"}, secrets=())
            when = parse_eia_schedule(sched, ctx["now"])
        except Exception: when = None
        when = when or next_eia(ctx["now"])
        if not when: return []
        return [eia_item(when, value, "paid", "eia_wpsr")]
    def first_ok(urls, parser):
        err = None
        for u in urls:
            try:
                body = call(ctx["get"], u, {"Accept": "application/rss+xml, application/xml, text/xml, text/html"}, secrets=())
                items = parser(body) or []
                if items: return items
            except VendorError as e: err = e
            except Exception: err = VendorError("parse")
        if err: raise err
        return []
    def edgar(form, kind):
        import urllib.parse
        u = "https://www.sec.gov/cgi-bin/browse-edgar?" + urllib.parse.urlencode({"action": "getcurrent", "type": form, "count": 40, "output": "atom"})
        body = call(ctx["get"], u, {"User-Agent": UA, "Accept": "application/atom+xml"}, secrets=())
        return parse_edgar(body, kind, ctx["ciks"])
    def rss():
        items, err = [], None
        for name, u in RSS_WIRES:
            try: items.extend(parse_rss(call(ctx["get"], u, {"Accept": "application/rss+xml"}, secrets=()), "rss_" + name))
            except Exception as e: err = e if isinstance(e, VendorError) else VendorError("parse")
        if items: return items
        if err: raise err
        return []
    def cb():
        items, err = [], None
        for name, u in CB_FEEDS:
            try:
                for it in parse_rss(call(ctx["get"], u, {"Accept": "application/rss+xml"}, secrets=()), "cb_" + name, kind="economic"):
                    if SPEAKER_RE.search(it["title"]): it["kind"] = "cb_speaker"
                    elif DECISION_RE.search(it["title"]): it["kind"] = "cb_decision"
                    else: continue
                    it["tier"] = "free_fallback"
                    items.append(it)
            except VendorError as e: err = e
        if items: return items
        if err: raise err
        return []
    def ff():
        return parse_ff(call(ctx["get"], "https://nfs.faireconomy.media/ff_calendar_thisweek.json", {"Accept": "application/json"}, secrets=()))
    def asx():
        items, err = [], None
        for u in ("https://www.asx.com.au/asx/v2/statistics/todayAnns.do", "https://www.asx.com.au/asx/v2/statistics/prevBusDayAnns.do"):
            try: items.extend(parse_asx(call(ctx["get"], u, {"Accept": "text/html"}, secrets=())))
            except VendorError as e: err = e
        if items: return items
        if err: raise err
        return []
    def fda():
        return parse_fda(call(ctx["get"], "https://www.rttnews.com/corpinfo/fdacalendar.aspx", {"Accept": "text/html"}, secrets=()))
    def eia_rule():
        when = next_eia(ctx["now"])
        return [eia_item(when, None, "free_fallback", "eia_rule")] if when else []
    def cal_earn():
        return parse_calendar_file(ctx["calendar"], ctx["now"])[0]
    def cal_macro():
        return parse_calendar_file(ctx["calendar"], ctx["now"])[1]
    return {"fmp_news": fmp_news, "fmp_press": fmp_press, "fmp_earnings": fmp_earn, "fmp_economic": fmp_econ, "fmp_8k": fmp_8k,
            "eodhd_news": eod_news, "eodhd_earnings": eod_earn, "massive_news": massive, "uw_news": uw_news, "uw_flow": uw_flow, "eia_wpsr": eia,
            "edgar_form4": lambda: edgar("4", "form4"), "asx_announcements": asx, "lse_rns": lambda: first_ok(LSE_URLS, parse_lse),
            "hkex_disclosure": lambda: first_ok(HKEX_URLS, parse_hkex), "tdnet": lambda: first_ok(TDNET_URLS, parse_tdnet), "fda_pdufa": fda,
            "rss_newswire": rss, "cb_calendar": cb, "ff_calendar": ff, "edgar_8k": lambda: edgar("8-K", "8k"),
            "calendar_earnings": cal_earn, "calendar_macro": cal_macro, "eia_rule": eia_rule}

PHASE1 = ("fmp_news", "fmp_press", "fmp_earnings", "fmp_economic", "fmp_8k", "eodhd_news", "eodhd_earnings", "massive_news", "uw_news", "uw_flow", "eia_wpsr",
          "edgar_form4", "asx_announcements", "lse_rns", "hkex_disclosure", "tdnet", "fda_pdufa")
def _need_fallback(results):
    def ok(names): return any(results.get(n, {}).get("status") == "ok" for n in names)
    out = []
    if not ok(HEADLINE_PAID): out.append("rss_newswire")
    if not ok(("fmp_8k",)): out.append("edgar_8k")
    if not ok(("fmp_earnings", "eodhd_earnings")): out.append("calendar_earnings")
    if not ok(("fmp_economic",)): out.extend(("cb_calendar", "ff_calendar", "calendar_macro"))
    if not ok(("eia_wpsr",)): out.append("eia_rule")
    return out
def _stamp(item, index, now, fetched):
    item = dict(item)
    item["sentiment"] = score_sentiment(item)
    item["urgency"] = score_urgency(item, now)
    seats, route = seats_for(item, index)
    item["seats"] = seats
    item["route"] = route
    item["fetched_utc"] = fetched
    item["advisory_only"] = True
    item["blocks_trade"] = False
    item["blocks_feed"] = False
    return item
def _latency(item, seat_dt):
    fetched = parse_time(item.get("fetched_utc")) or seat_dt
    hop = round(max(0.0, (seat_dt - fetched).total_seconds()), 3)
    src = parse_time(item.get("source_utc"))
    e2e = None
    if src is not None:
        raw = (seat_dt - src).total_seconds()
        if raw >= 0: e2e = round(raw, 3)
    item["seat_utc"] = seat_dt.isoformat()
    item["hop_s"] = hop
    item["e2e_s"] = e2e if e2e is not None and e2e <= E2E_CAP_S else None
    item["e2e_raw_s"] = round(e2e, 3) if e2e is not None else None
    return hop, (e2e if e2e is not None and e2e <= E2E_CAP_S else None)
def cycle(now=None, get=None, key_fn=None, universe=None, calendar_path=None):
    """One pass. Never raises. Never touches the book, the engine wake queue, or another feed's lock."""
    t0 = time.monotonic()
    frozen = now is not None
    now = now or now_utc()
    if get is None and (os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("CATALYST_NO_NETWORK") == "1"):
        def get(url, headers, timeout): raise VendorError("network disabled")
    get = get or http_get
    key_fn = key_fn or read_key
    fetched = now.isoformat()
    ctx = {"get": get, "key": key_fn, "now": now, "ciks": dict(HAND_CIK), "calendar": calendar_path or os.path.join(BASE, "state", "calendar", "calendar_latest.json")}
    try: ctx["ciks"] = cik_map(get)
    except Exception: ctx["ciks"] = dict(HAND_CIK)
    if universe is not None: index = universe
    else:
        try: index = load_universe()
        except Exception: index = {}
    fns = _vendors(ctx)
    state = load_json(_p("backoff.json"), {})
    if not isinstance(state, dict): state = {}
    ts = now.timestamp()
    results = {}
    def _note(name, status, items, err):
        results[name] = {"status": status, "n": len(items), "err": err, "items": items}
        if status == "ok": _backoff_ok(state, name)
        elif status == "fail": _backoff_fail(state, name, err or "fail", ts)
    def run_names(names, budget):
        ex = ThreadPoolExecutor(max_workers=8)
        futs = {}
        try:
            for n in names:
                if n not in fns: continue
                if _backoff_open(state, n, ts):
                    _note(n, "backoff", [], (state.get(n) or {}).get("last_err"))
                    continue
                futs[ex.submit(_run, n, fns[n])] = n
            done, pending = wait(futs, timeout=budget) if futs else ((), ())
            for fut in done:
                try: name, status, items, err = fut.result()
                except Exception as e: name, status, items, err = futs[fut], "fail", [], redact(e)
                _note(name, status, items, err)
            for fut in pending:
                name = futs[fut]
                _note(name, "fail", [], "cycle budget")
                fut.cancel()
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
    run_names(PHASE1, 22)
    run_names(_need_fallback(results), 8)
    seen = load_json(_p("seen.json"), {})
    if not isinstance(seen, dict): seen = {}
    cutoff = (now - dt.timedelta(hours=72)).isoformat()
    seen = {k: v for k, v in seen.items() if str(v) >= cutoff}
    lat = load_json(_p("latency.json"), {})
    hops = list(lat.get("hop") or []) if isinstance(lat, dict) else []
    e2es = list(lat.get("e2e") or []) if isinstance(lat, dict) else []
    queued, woke, deduped = {}, {}, 0
    seat_dt = now if frozen else now_utc()
    for name, rec in results.items():
        tier = "free_fallback" if name in ("rss_newswire", "cb_calendar", "ff_calendar", "edgar_8k", "calendar_earnings", "calendar_macro", "eia_rule") else None
        for raw in rec.get("items") or []:
            if not isinstance(raw, dict) or not raw.get("title"): continue
            if tier and raw.get("tier") == "free": raw["tier"] = tier
            item = _stamp(raw, index, now, fetched)
            key = _dedupe_key(item)
            if key in seen:
                deduped += 1
                continue
            seen[key] = seat_dt.isoformat()
            hop, e2e = _latency(item, seat_dt)
            hops.append(hop)
            if e2e is not None: e2es.append(e2e)
            item["id"] = key
            urgent = item["urgency"] >= WAKE_AT
            for seat in item["seats"]:
                row = dict(item)
                try: _append(_p("queues", seat + ".jsonl"), row)
                except Exception: continue
                queued[seat] = queued.get(seat, 0) + 1
                if urgent:
                    wake = dict(row, wake_id="cat-%s-%s-%s" % (now.strftime("%Y%m%dT%H%M%SZ"), seat, key[:8]))
                    try: _append(_p("wake", seat + ".jsonl"), wake); _append(_p("wake_queue.jsonl"), wake)
                    except Exception: pass
                    woke[seat] = woke.get(seat, 0) + 1
    hops, e2es = hops[-SAMPLE_CAP:], e2es[-SAMPLE_CAP:]
    ph, pe = p95(hops), p95(e2es)
    alert = bool((len(hops) >= P95_MIN_N and ph is not None and ph > SLA_S) or (len(e2es) >= P95_MIN_N and pe is not None and pe > SLA_S))
    latency = {"p95_hop_s": ph, "p95_e2e_s": pe, "n_hop": len(hops), "n_e2e": len(e2es), "alert": alert, "sla_s": SLA_S}
    try: write_json(_p("seen.json"), seen)
    except Exception: pass
    try: write_json(_p("backoff.json"), state)
    except Exception: pass
    try: write_json(_p("latency.json"), {"hop": hops, "e2e": e2es})
    except Exception: pass
    try:
        write_json(_p("latency_alert.json"), dict(latency, utc=now.isoformat(), advisory_only=True, blocks_trade=False,
                                                   note="p95 source-to-seat (items published within 6h) or in-process hop above 60s. Advisory. Does not block trades or feeds."))
    except Exception: pass
    for seat in list(queued) + list(woke):
        _trim(_p("queues", seat + ".jsonl")); _trim(_p("wake", seat + ".jsonl"))
    vendors = {n: {"status": r["status"], "n": r["n"], "err": r["err"]} for n, r in results.items()}
    out = dict(ok=True, advisory_only=True, blocks_trade=False, blocks_feed=False, cycle_s=round(time.monotonic() - t0, 3),
               queued=queued, woke=woke, deduped=deduped, vendors=vendors, latency=latency, utc=now.isoformat())
    try:
        write_json(_p("heartbeat.json"), dict(out, pid=os.getpid(), queue_dir=_p("queues"), wake_dir=_p("wake")))
    except Exception: pass
    return out
def daemon(cycles=None, sleep=None):
    """Stay up. launchd KeepAlive restarts the process if it exits. One bad cycle does not exit."""
    sleep = sleep or time.sleep
    n = 0
    while not STOP and (cycles is None or n < cycles):
        t1 = time.monotonic()
        try: cycle()
        except Exception as e:
            try: write_json(_p("heartbeat.json"), dict(ok=False, advisory_only=True, blocks_trade=False, blocks_feed=False, err=redact(e), utc=now_utc().isoformat(), pid=os.getpid()))
            except Exception: pass
        n += 1
        if STOP or (cycles is not None and n >= cycles): return
        sleep(max(0.0, CYCLE_S - (time.monotonic() - t1)))
def status():
    return load_json(_p("heartbeat.json"), {})
def main(argv=None):
    global STOP
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "daemon"
    if cmd == "once":
        r = cycle()
        print(json.dumps({k: r[k] for k in ("ok", "cycle_s", "queued", "woke", "deduped", "latency", "vendors")}, default=str)[:4000])
        return 0
    if cmd == "status":
        print(json.dumps(status(), default=str)[:4000]); return 0
    def _stop(*_a):
        global STOP
        STOP = True
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    daemon()
    return 0
if __name__ == "__main__":
    sys.exit(main())
