#!/usr/bin/env python3
"""EVENT CALENDAR + TRIGGER v1.2 (PAPER ONLY) for the 48-72h catalyst hunt engine.
Brett 2026-10-27 01:36 WITA: unified market event calendar for ALL scanned names (US liquid ~3,870 + Kraken 142),
event -> queued review (existing ranker score + desk gates) -> staged PAPER bench setup (NOT armed, NOT fired);
plus event-risk blocks (earnings / binary events inside the hold window -> BLOCK or SIZE-DOWN).

Reads only: state/calendar/inputs/{ranked.csv,catcache_*.json,calendar_*.json} (box sweep copy), hunt48 ledgers (read-only),
public feeds (Nasdaq earnings calendar w/ times, SEC EDGAR current 8-K atom, ForexFactory macro, Kraken AssetPairs, RTT FDA page).
Writes only: state/calendar/* and static/event_calendar.json. Never touches keys, brokers, hunt48 ledgers or any order route.
"""
import csv, datetime as dt, glob, json, os, re, sys, html as _html, urllib.request, gzip, io
from zoneinfo import ZoneInfo
try:  # 2026-10-30 WITA feed audit: ISP DNS hijack of api.kraken.com -> same guarded process-local DoH hook as Kraken collectors (kraken.assetpairs feed)
    import kraken_dns as _kraken_dns; _kraken_dns.install()
except Exception as _kd_e:  # never block the calendar on this
    print("kraken_dns unavailable: %r" % _kd_e, file=sys.stderr)

BASE = os.environ.get("DESK_BASE", os.path.dirname(os.path.abspath(__file__)))
CD = os.path.join(BASE, "state", "calendar"); IN = os.path.join(CD, "inputs")
OUT_STATIC = os.path.join(BASE, "static", "event_calendar.json")
UTC = dt.timezone.utc; WITA = ZoneInfo("Asia/Makassar"); ET = ZoneInfo("America/New_York")
SEC_UA = __import__("json").load(open(__import__("os").path.expanduser("~/Applications/MultiVenueDesk/research_controls/sec_contact.json")))["user_agent"]
BROWSER_UA = "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/128 Safari/537.36"
HORIZON_H = 72
FOMC_UTC = dt.datetime(2026, 10, 28, 18, 0, tzinfo=UTC)          # Thu 29 Oct 02:00 WITA (Brett)
RISK_PRE_FOMC_AUD, RISK_POST_FOMC_AUD = 2500.0, 2500.0   # head trader 2026-10-27 04:36: no A$5k restore after FOMC
MAX_STAGED = 15; TOP_N = 3
# review fix #4 (Brett-approved hunt_review_20261027), US hunt only (opt-in args; ASX/Asia/LSE/OANDA/Kraken unchanged):
# stop distance never under 1.0 ATR (the 5% price cap may only trim 1.5 ATR down to 1 ATR); A$ risk held via shares; ATR cap 6% -> 8%
STOP_ATR_FLOOR = 1.0; US_MAX_ATR_PCT = 8.0
GATES = dict(min_adv_aud=5e6, min_adv_aud_asx=2e6, min_adv_usd=20e6, min_px=5.0, max_atr_pct=6.0, min_rsi_short=20, max_rsi_long=80, max_roc5_chase=15.0)
# R:R: OOS daily-bar test 2026-10-27 (plan48/edge_tests/rr_results.json, ~10.6k proxy setups, 1y) found 1R/2R targets did NOT
# improve expectancy vs the engine's ~0.45R/1.05R (both negative net; old scheme +0.02-0.03R better in all 4 IS/OOS x side cells).
# So targets stay engine-compatible and the dashboard shows the true breakeven win rate. Configurable for the shadow test.
T1_R, T2_R = float(os.environ.get("CAL_T1_R", 0.45)), float(os.environ.get("CAL_T2_R", 1.05))
COSTS = dict(ibkr_usd_side=1.0, slip_bp_side=5.0, borrow_pa=0.03, kraken_maker_pct=0.25, kraken_taker_pct=0.40)
HIGH_RISK_TYPES = {"earnings", "fda", "mna_target"}       # binary gap events: block new entries inside hold window
SIZE_DOWN_TYPES = {"guidance", "sec_8k_material", "token_unlock", "macro_high", "macro_ccy"}

def now_utc(): return dt.datetime.now(UTC)
def w(t): return t.astimezone(WITA).strftime("%a %d %b %H:%M WITA") if t else "date only"
def iso(t): return t.astimezone(UTC).isoformat() if t else None
def atomic(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True); tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(obj, f, indent=1, default=str)
    os.replace(tmp, path)
def fnum(x, d=None):
    try:
        v = float(x); return d if v != v else v
    except Exception: return d
def http(url, ua=BROWSER_UA, timeout=20, accept=None):
    rq = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "gzip", **({"Accept": accept} if accept else {})})
    with urllib.request.urlopen(rq, timeout=timeout) as r:
        b = r.read()
        if r.headers.get("Content-Encoding") == "gzip": b = gzip.decompress(b)
        return b.decode("utf-8", "replace")

FEEDS = {}
def feed(name, ok, note=""): FEEDS[name] = dict(ok=bool(ok), note=str(note)[:200], at=iso(now_utc()))

# ---------------------------------------------------------------- universe
def load_universe(indir=IN):
    rows = {}
    p = os.path.join(indir, "ranked_all_incl_excluded.csv")   # full sweep incl. all Kraken pairs; gate() honours 'excluded'
    if not os.path.exists(p): p = os.path.join(indir, "ranked.csv")
    with open(p) as f:
        for r in csv.DictReader(f): rows[r["sym"]] = r
    po = os.path.join(indir, "ranked_oanda.csv")     # OANDA instruments (fx_scan.py, same ranker; Yahoo proxies; PAPER only)
    if os.path.exists(po):
        with open(po) as f:
            for r in csv.DictReader(f):
                if "IDR" in r["sym"]: continue
                rows[r["sym"]] = r
    pk = os.path.join(indir, "ranked_kraken.csv")   # 2026-10-30 feedfix2: fresh Kraken-only rank (kraken_rank.py, DoH) when the sweep has none
    if os.path.exists(pk) and not any(r.get("venue") == "kraken" for r in rows.values()) and dt.datetime.now().timestamp() - os.path.getmtime(pk) < 26 * 3600:
        with open(pk) as f:
            for r in csv.DictReader(f): rows[r["sym"]] = r
    pa = os.path.join(indir, "ranked_asx.csv")       # ASX all listed incl. small caps, ADV>=A$5m (universe_asx.py, same ranker; PAPER only)
    if os.path.exists(pa):
        with open(pa) as f:
            for r in csv.DictReader(f):
                if is_indonesian(r): continue
                r["adv_usd"] = str(fnum(r.get("adv_aud"), 0) * AUDUSD_GATE); rows[r["sym"]] = r
    cats = sorted(glob.glob(os.path.join(indir, "catcache_*.json")))
    cat = json.load(open(cats[-1])) if cats else {}
    asof = dt.datetime.fromtimestamp(os.path.getmtime(p), UTC)
    feed("universe", True, f"{len(rows)} ranked ({sum(r['venue']=='kraken' for r in rows.values())} kraken, {sum(r['venue']=='oanda' for r in rows.values())} oanda, {sum(r['venue']=='asx' for r in rows.values())} asx), catcache {len(cat)} names, sweep mtime {w(asof)}")
    return rows, cat, asof

# ---------------------------------------------------------------- event builders (each returns list of dicts)
def ev(kind, sym, when, title, src, conf="confirmed", date_only=False, **kw):
    return dict(type=kind, sym=sym, when_utc=iso(when), when_wita=(w(when) if not date_only else (when.astimezone(ET).strftime("%a %d %b") + " (time TBC)")),
                date_only=date_only, title=title, source=src, confidence=conf, **kw)

def earnings_nasdaq(days, fetch=http):
    """Nasdaq calendar: exact session (pre-market / after-hours) per name."""
    out = []
    for d in days:
        try:
            j = json.loads(fetch(f"https://api.nasdaq.com/api/calendar/earnings?date={d.isoformat()}", accept="application/json"))
            rows = ((j.get("data") or {}).get("rows")) or []
            for r in rows:
                tm = r.get("time", "")
                if "pre" in tm: t = dt.datetime.combine(d, dt.time(8, 0), ET); sess = "BMO"
                elif "after" in tm: t = dt.datetime.combine(d, dt.time(16, 5), ET); sess = "AMC"
                else: t = dt.datetime.combine(d, dt.time(12, 0), ET); sess = "TBC"
                out.append(ev("earnings", r.get("symbol", "").replace("/", "-").upper(), t, f"Earnings {sess}{' (after 16:00 ET; exact minute TBC)' if sess=='AMC' else ' (before 09:30 ET)' if sess=='BMO' else ''} (EPS est {r.get('epsForecast') or 'n/a'}, {r.get('noOfEsts') or '?'} ests)",
                              "nasdaq.earnings", date_only=(sess == "TBC"), session=sess))
            feed(f"nasdaq.earnings.{d}", True, f"{len(rows)} rows")
        except Exception as e: feed(f"nasdaq.earnings.{d}", False, e)
    return out

# 2026-10-30 eodhd: EODHD earnings calendar is now the PRIMARY earnings source (key confirmed 20:53 WITA);
# Nasdaq fills names EODHD lacks; yfinance catcache stays the date-only fallback.
_EOD_SFX = {".US": "", ".AU": ".AX", ".LSE": ".L"}
def earnings_eodhd(start, end):
    """EODHD calendar/earnings for [start, end] (ET dates). Returns (events, {sym: next report date})."""
    out, nxt = [], {}
    try:
        import fundamental_feed as _ff
        r = _ff._eodhd("calendar/earnings", {"from": start.isoformat(), "to": end.isoformat()})
        if not r.ok: raise RuntimeError(r.reason)
        rows = (r.data or {}).get("earnings") or []
        for x in rows:
            code = str(x.get("code") or ""); sym = None
            for sfx, rep in _EOD_SFX.items():
                if code.endswith(sfx): sym = code[: -len(sfx)].replace("/", "-").upper() + rep; break
            if not sym: continue
            try: d = dt.date.fromisoformat(str(x.get("report_date"))[:10])
            except Exception: continue
            if sym not in nxt or d < nxt[sym]: nxt[sym] = d
            ba = str(x.get("before_after_market") or "")
            if ba.startswith("Before"): t = dt.datetime.combine(d, dt.time(8, 0), ET); sess = "BMO"
            elif ba.startswith("After"): t = dt.datetime.combine(d, dt.time(16, 5), ET); sess = "AMC"
            else: t = dt.datetime.combine(d, dt.time(12, 0), ET); sess = "TBC"
            out.append(ev("earnings", sym, t, f"Earnings {sess} (EODHD; EPS est {x.get('estimate') if x.get('estimate') is not None else 'n/a'})",
                          "eodhd.earnings", date_only=(sess == "TBC"), session=sess))
        feed("eodhd.earnings", True, f"{len(rows)} rows {start}..{end}, {len(nxt)} mapped names")
    except Exception as e: feed("eodhd.earnings", False, e)
    return out, nxt

def earnings_catcache(cat, now):
    """Fallback/coverage: yfinance next_earn date for every scanned name (date only)."""
    out = []
    for s, c in cat.items():
        ne = c.get("next_earn")
        if not ne: continue
        try: d = dt.date.fromisoformat(str(ne)[:10])
        except Exception: continue
        t = dt.datetime.combine(d, dt.time(12, 0), ET)
        if -1 <= (t - now).total_seconds() / 86400 <= 10:
            out.append(ev("earnings", s, t, "Earnings (date from yfinance; session unknown)", "yf.catcache", conf="date-only", date_only=True, session="TBC"))
    return out

ITEM_MAP = {"1.01": ("sec_8k_material", "material agreement"), "1.02": ("sec_8k_material", "agreement terminated"), "1.03": ("sec_8k_material", "bankruptcy"),
            "2.01": ("mna", "acquisition/disposal completed"), "2.02": ("earnings_release", "results"), "2.05": ("sec_8k_material", "restructuring costs"),
            "2.06": ("sec_8k_material", "impairment"), "3.01": ("sec_8k_material", "delisting notice"), "4.02": ("sec_8k_material", "non-reliance/restatement"),
            "5.02": ("mgmt_change", "officer/director change"), "7.01": ("reg_fd", "Reg FD"), "8.01": ("other", "other events")}
def sec_8k(tick_by_cik, fetch=http, count=100):
    out = []
    try:
        x = fetch(f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&count={count}&output=atom", ua=SEC_UA)
        for e in re.findall(r"<entry>(.*?)</entry>", x, re.S):
            title = _html.unescape(re.sub(r"<.*?>", "", (re.search(r"<title>(.*?)</title>", e, re.S) or [None, ""])[1]))
            cikm = re.search(r"\((\d{6,10})\)", title); upd = re.search(r"<updated>(.*?)</updated>", e)
            summ = _html.unescape((re.search(r"<summary[^>]*>(.*?)</summary>", e, re.S) or [None, ""])[1])
            link = (re.search(r'href="([^"]+)"', e) or [None, ""])[1]
            if not cikm: continue
            sym = tick_by_cik.get(int(cikm.group(1)))
            if not sym: continue
            items = sorted(set(re.findall(r"Item (\d\.\d\d)", summ)))
            kinds = [ITEM_MAP.get(i, ("other", i)) for i in items] or [("other", "8-K")]
            kind = next((k for k, _ in kinds if k in ("mna", "sec_8k_material")), kinds[0][0])
            t = dt.datetime.fromisoformat(upd.group(1)) if upd else now_utc()
            out.append(ev(kind, sym, t, f"8-K items {','.join(items) or '?'}: {'; '.join(l for _, l in kinds)}", "sec.8k.atom", link=link, past=True))
        feed("sec.8k.atom", True, f"{len(out)} filings mapped to tickers")
    except Exception as e: feed("sec.8k.atom", False, e)
    return out

def sec_tickers(fetch=http):
    p = os.path.join(CD, "sec_company_tickers.json")
    try:
        if not os.path.exists(p) or (now_utc().timestamp() - os.path.getmtime(p)) > 86400:
            j = json.loads(fetch("https://www.sec.gov/files/company_tickers.json", ua=SEC_UA)); atomic(p, j)
        j = json.load(open(p)); m = {int(v["cik_str"]): v["ticker"].replace(".", "-").upper() for v in j.values()}
        feed("sec.company_tickers", True, f"{len(m)}"); return m
    except Exception as e: feed("sec.company_tickers", False, e); return {}

# v1.2 (2026-10-27): OFFICIAL schedule anchors, each verified against the issuer's own page on 2026-10-27 WITA.
# Added only if the ForexFactory feed has no matching item within +-15 min (feed outage / missing EIA / BoJ not on FF list).
OFFICIAL_EVENTS = [  # (utc, kind, ccy, title, match-regex, source url, confidence, central_bank)
    (dt.datetime(2026,10,28,0,30,tzinfo=UTC), "macro_high", "AUD", "AUD CPI (ABS, Sep qtr) 11:30 AEDT", r"CPI", "abs.gov.au/release-calendar/future-releases/202610", "confirmed", False),
    (dt.datetime(2026,10,28,1,0,tzinfo=UTC), "macro_high", "NZD", "NZD RBNZ OCR / Monetary Policy Review 14:00 NZDT", r"Cash Rate|RBNZ", "rbnz.govt.nz (MPR Oct 2026 event page)", "confirmed", True),
    (dt.datetime(2026,10,28,2,0,tzinfo=UTC), "macro_high", "NZD", "NZD RBNZ media conference 15:00 NZDT", r"Press Conference", "rbnz.govt.nz", "confirmed", True),
    (dt.datetime(2026,10,28,13,45,tzinfo=UTC), "macro_high", "CAD", "CAD BoC rate announcement + MPR 09:45 ET", r"Overnight Rate|BOC", "bankofcanada.ca/2026/10/interest-rate-announcement-and-monetary-policy-report-october-28-2026/", "confirmed", True),
    (dt.datetime(2026,10,28,14,30,tzinfo=UTC), "macro_high", "CAD", "CAD BoC press conference 10:30 ET", r"Press Conference", "bankofcanada.ca", "confirmed", True),
    (dt.datetime(2026,10,28,14,30,tzinfo=UTC), "macro_med", "USD", "USD EIA Weekly Petroleum Status Report (crude inventories) 10:30 ET - oil longs", r"Crude|EIA|Petroleum", "eia.gov/petroleum/supply/weekly/schedule.php (standard Wed 10:30 ET; no Oct 28 exception)", "confirmed", False),
    (dt.datetime(2026,10,28,18,0,tzinfo=UTC), "macro_high", "USD", "USD FOMC decision 14:00 ET (press conf 14:30 ET)", r"FOMC|Federal Funds", "federalreserve.gov", "confirmed", True),
    (dt.datetime(2026,10,29,12,30,tzinfo=UTC), "macro_high", "USD", "USD GDP advance Q3 + Personal Income/Outlays (PCE) Sep, 08:30 ET (one risk window)", r"GDP|PCE", "bea.gov/news/schedule (BEA 26-36, 26-37)", "confirmed", False),
    (dt.datetime(2026,10,29,13,15,tzinfo=UTC), "macro_high", "EUR", "EUR ECB monetary policy decision 14:15 CET", r"Refinancing|Monetary Policy Statement", "ecb.europa.eu/press/govcdec/mopo", "confirmed", True),
    (dt.datetime(2026,10,29,13,45,tzinfo=UTC), "macro_high", "EUR", "EUR ECB press conference 14:45 CET", r"Press Conference", "ecb.europa.eu", "confirmed", True),
    (dt.datetime(2026,10,30,2,20,tzinfo=UTC), "macro_high", "JPY", "JPY BoJ MPM (29-30 Oct) statement - official time UNDECIDED, usually 11:20-13:00 JST (window start shown); Ueda presser ~15:30 JST. Applies to JP225_USD too", r"BOJ|Policy Rate", "boj.or.jp/en/about/calendar/ + mopo/mpmsche_minu", "window", True),
]
def official_anchors(out):
    add = []
    for t, kind, c, title, rx, src, conf, cb in OFFICIAL_EVENTS:
        if any(e.get("ccy") == c and re.search(rx, e["title"]) and abs((dt.datetime.fromisoformat(e["when_utc"]) - t).total_seconds()) <= 900 for e in out if e.get("when_utc")):
            continue
        add.append(ev(kind, "MACRO", t, title, "official:" + src, conf=conf, ccy=c, central_bank=cb))
    return add

def macro_ff(fetch=http):
    out = []
    try:
        x = json.loads(fetch("https://nfs.faireconomy.media/ff_calendar_thisweek.json"))
        for e in x:
            c = e.get("country"); imp = e.get("impact")
            if imp not in ("High", "Medium") or not c or c == "All": continue
            if c != "USD" and imp != "High" and not re.search(r"CPI|PMI|GDP|Employment|Unemployment|Rate|Retail", e.get("title", "")): continue
            t = dt.datetime.fromisoformat(e["date"])
            cb = bool(re.search(r"Rate Statement|Cash Rate|Policy Rate|Funds Rate|Official Bank Rate|Main Refinancing|Overnight Rate|Monetary Policy", e.get("title", "")))
            out.append(ev("macro_high" if imp == "High" else "macro_med", "MACRO", t, f"{c} {e['title']} (f {e.get('forecast') or '-'} / p {e.get('previous') or '-'})", "forexfactory", ccy=c, central_bank=cb))
        feed("forexfactory.macro", True, f"{len(out)} high/med (all currencies; non-USD medium only CPI/PMI/GDP/jobs/rates)")
    except Exception as e: feed("forexfactory.macro", False, e)
    if not any("FOMC" in e["title"] or "Federal Funds" in e["title"] for e in out):
        out.append(ev("macro_high", "MACRO", FOMC_UTC, "FOMC decision (fallback; press conf +30m)", "desk.static"))
    out += official_anchors(out)
    out = [dict(e, title=e["title"] + " [prev=feed vintage; BEA latest Q2 = 2.2%]") if "Advance GDP q/q" in e["title"] else e for e in out]
    return out

def crypto_events(rows, fetch=http):
    out = []
    for s, r in rows.items():
        if r.get("venue") != "kraken": continue
        u = fnum(r.get("unlock_7d_pct"))
        if u and u >= 0.5:
            out.append(ev("token_unlock", s, now_utc() + dt.timedelta(hours=1), f"Token unlock {u:.2f}% of unlocked supply within 7d (DefiLlama; exact day TBC)", "defillama.via_sweep", conf="window-7d", date_only=True, unlock_pct=u))
        # NOTE: sweep 'funding' column = Kraken futures 'fundingRate', which is ABSOLUTE (USD/contract), not a rate
        # (ETH showed "4.05%/8h"). Not used until the sweep stores relativeFundingRate. See IMPROVEMENTS.
    # new Kraken listings: diff AssetPairs vs snapshot
    p = os.path.join(CD, "kraken_pairs_snapshot.json")
    try:
        j = json.loads(fetch("https://api.kraken.com/0/public/AssetPairs"))["result"]
        cur = sorted(k for k, v in j.items() if v.get("status", "online") == "online")
        if os.path.exists(p):
            prev = set(json.load(open(p))["pairs"])
            for k in [k for k in cur if k not in prev][:50]:
                out.append(ev("crypto_listing", j[k].get("altname", k), now_utc(), f"New Kraken pair online: {j[k].get('wsname', k)}", "kraken.assetpairs", past=True))
        atomic(p, dict(at=iso(now_utc()), pairs=cur)); feed("kraken.assetpairs", True, f"{len(cur)} online pairs")
    except Exception as e: feed("kraken.assetpairs", False, e)
    return out

def fda_rtt(fetch=http):
    out = []
    try:
        s = fetch("https://www.rttnews.com/corpinfo/fdacalendar.aspx")
        t = re.sub(r"<script.*?</script>|<style.*?</style>", "", s, flags=re.S); t = _html.unescape(re.sub(r"<[^>]+>", "|", t)); t = re.sub(r"\s*\|[\s|]*", "|", t)
        for m in re.finditer(r"\(\|([A-Z.\-]{1,6})\|[^|]*\)\|([^|]{2,120})\|(\d\d/\d\d/\d{4})\|[^|]*\|(pending)", t):
            d = dt.datetime.strptime(m.group(3), "%m/%d/%Y").date()
            out.append(ev("fda", m.group(1), dt.datetime.combine(d, dt.time(16, 0), ET), f"FDA decision pending: {m.group(2)[:60]}", "rttnews.fda", conf="date-only", date_only=True))
        feed("rttnews.fda", True, f"{len(out)} pending (page 1 only - thin)")
    except Exception as e: feed("rttnews.fda", False, e)
    return out

def catcache_state_events(cat, now):
    """Non-timed fundamentals from the sweep: analyst revisions, insider buys, guidance/M&A headlines, short interest, recent earnings surprise."""
    out = []
    for s, c in cat.items():
        notes = " | ".join(c.get("cat_notes") or [])
        m = re.search(r"7d analyst up(\d+)/down(\d+)", notes)
        if m and (int(m.group(1)) + int(m.group(2))) >= 2 and int(m.group(1)) != int(m.group(2)):
            out.append(ev("analyst", s, now, f"Analyst revisions 7d: up {m.group(1)} / down {m.group(2)}", "yf.upgrades_downgrades", past=True, bias=("long" if int(m.group(1)) > int(m.group(2)) else "short")))
        ib = (c.get("_ins") or {}).get("insider_open_mkt_buys") or 0
        if ib >= 1: out.append(ev("insider_buy", s, now, f"Insider open-market buys 14d: {ib}", "sec.form4.via_sweep", past=True, bias="long"))
        for k, lab, b in (("guide up", "guidance", "long"), ("guide cut", "guidance", "short"), ("M&A", "mna", None), ("FDA", "fda_news", None)):
            if k in notes: out.append(ev(lab, s, now, f"Headline flag: {k}", "yf.news.via_sweep", past=True, **({"bias": b} if b else {})))
        si, dtc = fnum(c.get("short_pct_float")), fnum(c.get("days_to_cover"))
        if si and si >= 0.20 and dtc and dtc >= 5:
            out.append(ev("short_interest", s, now, f"Short interest {si*100:.0f}% float, {dtc:.1f} days to cover (squeeze risk for shorts)", "yf.info", past=True))
        le, sp = c.get("last_earn"), fnum(c.get("last_surprise_pct"))
        if le and sp is not None:
            try: d = dt.date.fromisoformat(str(le)[:10])
            except Exception: d = None
            if d and 0 <= (now.date() - d).days <= 7 and abs(sp) >= 10:
                # no directional bias: OOS check (edge_tests/pead_results.json) found no 4-day PEAD net of costs, and shorting
                # post-earnings drops <-5% LOST 1.07%/4d vs SPY (n=408, t=-3.2) -> treated as a reversal-risk gate, not a signal
                out.append(ev("post_earnings", s, dt.datetime.combine(d, dt.time(16, 0), ET), f"Reported {d} surprise {sp:+.1f}% (post-earnings window)", "yf.catcache", past=True, surprise_pct=sp, earn_date=str(d)))
    return out

def news_bot_events(indir=IN):
    out = []
    fs = sorted(glob.glob(os.path.join(indir, "calendar_*.json")))
    if not fs: feed("newsbot", False, "no calendar file"); return out
    j = json.load(open(fs[-1]))
    for e in j.get("events", []):
        out.append(dict(type="news", sym="MARKET", when_utc=None, when_wita=str(e.get("time_aedt")) + " (AEDT text)", date_only=True,
                        title=str(e.get("event"))[:240], touches=str(e.get("touches"))[:160], source="newsbot:" + os.path.basename(fs[-1]), confidence="text"))
    # 2026-10-30 feedfix2: flag a stale newsbot file instead of reporting ok (file name stamp calendar_YYYY-MM-DD_HHMM; box routine output)
    _nb_m = re.search(r"calendar_(\d{4}-\d{2}-\d{2})_(\d{4})", os.path.basename(fs[-1]))
    _nb_age_h = ((now_utc() - dt.datetime.strptime(_nb_m.group(1) + _nb_m.group(2), "%Y-%m-%d%H%M").replace(tzinfo=UTC)).total_seconds() / 3600) if _nb_m else None
    if _nb_age_h is not None and _nb_age_h > 36:
        feed("newsbot", False, f"STALE {_nb_age_h:.0f}h: {len(out)} items from {os.path.basename(fs[-1])} (box newsbot routine not delivering to state/calendar/inputs)"); return out
    feed("newsbot", True, f"{len(out)} items from {os.path.basename(fs[-1])}")
    return out

# ---------------------------------------------------------------- review + staging
def regime_from(rows):
    def gt(s, a, b):
        r = rows.get(s); return None if not r else fnum(r.get(a), 0) > fnum(r.get(b), 0)
    eq = gt("SPY", "px", "m50"); cr = gt("XBT/USD", "px", "m50")   # NB 'BTC' in the US list is the Grayscale ETF
    return dict(ibkr=("long" if eq else "short") if eq is not None else "unknown", kraken=("long" if cr else "short") if cr is not None else "unknown",
                asx=asx_regime(rows), note="px vs 50dMA (same as sweep regime; asx = ^AXJO)")

def is_indonesian(r): return (r.get("country") or "").lower() == "indonesia" or (r.get("sym") or "").endswith(".JK")

def gate(r, direction, max_atr_pct=None):
    why = []; atr_cap = GATES["max_atr_pct"] if max_atr_pct is None else float(max_atr_pct)
    px, adv, atrp = fnum(r.get("px"), 0), fnum(r.get("adv_usd") or r.get("vol24_usd"), 0), fnum(r.get("atr_pct"), 99)
    rsi, roc5 = fnum(r.get("rsi"), 50), fnum(r.get("roc5"), 0)
    if str(r.get("excluded", "")).strip() not in ("", "False", "false", "0", "nan"): why.append(f"excluded:{r.get('excluded')}")
    if is_indonesian(r): why.append("Indonesian asset")
    if r.get("venue") == "oanda" and r.get("cls") in SHADOW_ONLY_CLASSES:
        why.append(f"shadow-only class {r.get('cls')}: proxy setups negative in both IS and OOS halves after costs (edge_tests/fx_results.json)")
    if r.get("venue") == "oanda":
        stop_pct = min(1.5 * atrp, 5.0); cp = fnum(r.get("cost_pct"), 1)
        if stop_pct > 0 and cp / stop_pct > 0.15: why.append(f"spread+financing {cp:.2f}% = {cp/stop_pct:.2f}R of stop (>0.15R)")
    if r.get("venue") == "ibkr" and px < GATES["min_px"]: why.append(f"px {px:.2f}<5")
    if r.get("venue") == "ibkr" and adv / AUDUSD_GATE < GATES["min_adv_aud"]: why.append(f"ADV A${adv/AUDUSD_GATE/1e6:.1f}M<A$5M")   # Brett 27 Oct: A$5m floor US (unchanged)
    if r.get("venue") == "asx" and adv / AUDUSD_GATE < GATES["min_adv_aud_asx"]: why.append(f"ADV A${adv/AUDUSD_GATE/1e6:.1f}M<A$2M")   # Brett 27 Oct 04:50 WITA: ASX-only floor A$2m
    if r.get("venue") == "asx": why += asx_gate(r, direction)
    if r.get("venue") == "kraken" and adv < 1e6: why.append("24h vol <$1M")
    if atrp > atr_cap: why.append(f"ATR {atrp:.1f}%>{atr_cap:g}%")
    if direction == "long" and roc5 > GATES["max_roc5_chase"]: why.append(f"pump-chase: 5d +{roc5:.0f}%")
    if direction == "long" and rsi > GATES["max_rsi_long"]: why.append(f"RSI {rsi:.0f} overbought")
    if direction == "short" and rsi < GATES["min_rsi_short"]: why.append(f"RSI {rsi:.0f} washed out")
    if direction == "short" and roc5 < -GATES["max_roc5_chase"]: why.append(f"crash-chase: 5d {roc5:.0f}%")
    return why

def make_q2usd(rows):
    def f(q):
        if q == "USD": return 1.0
        if f"USD_{q}" in rows: return 1.0 / fnum(rows[f"USD_{q}"]["px"], float("inf"))
        if f"{q}_USD" in rows: return fnum(rows[f"{q}_USD"]["px"], 0)
        return None
    return f

def cluster_of(sym, r):
    if r.get("venue") == "oanda" and r.get("cls", "").startswith("metal"): return ["metals"]
    if sym in ("SLV", "GLD", "SIVR", "PSLV", "IAU", "GDX", "GDXJ", "PPLT", "PALL"): return ["metals"]
    if r.get("venue") == "oanda" and r.get("cls", "").startswith("fx"): return ["ccy:" + c for c in sym.split("_")]
    return []

CLUSTER_CAP = 2
SHADOW_ONLY_CLASSES = {"fx_exotic", "soft"}   # avgR IS/OOS: exotic -0.17/-0.20 (n=424), soft -0.23/-0.21 (n=93)
REALISED_WIN = None
def realised_winners(base=None):
    """Closed PAPER hunt48 (all books) + calendar bench trades: R = net P&L / initial risk; avg over winners (R>0)."""
    base = base or BASE; Rs = []
    for p in glob.glob(os.path.join(base, "state", "hunt48_short*", "ledger.json")):
        try:
            L = json.load(open(p)); fx = L.get("audusd_armed", 0.7022)
            for t in L.get("trades", []):
                if t.get("status") in ("CLOSED", "STOPPED") and t.get("risk_cap_aud"):
                    Rs.append((t.get("realised_usd", 0) - t.get("fees_usd", 0) - t.get("borrow_usd", 0)) / (t["risk_cap_aud"] * fx))
        except Exception: pass
    try:
        for t in json.load(open(os.path.join(base, "state", "calendar", "paper_bench_ledger.json"))).get("trades", []):
            if t.get("status") == "CLOSED" and t.get("risk_aud") and t.get("net_aud") is not None: Rs.append(t["net_aud"] / t["risk_aud"])
    except Exception: pass
    w = [r for r in Rs if r > 0]
    return dict(n_closed=len(Rs), n_win=len(w), avg_win_R=(round(sum(w) / len(w), 3) if w else None))

def us_borrow_pa(sym):
    """review fix #8: per-name IBKR borrow (state/ibkr_borrow.json via ibkr_costs); unknown name / cache error -> flat COSTS borrow_pa."""
    try:
        import ibkr_costs as IC
        b = IC.borrow(sym, "us")
        if b and b.get("fee_pa") is not None: return float(b["fee_pa"])
    except Exception: pass
    return COSTS["borrow_pa"]
def stage_levels(r, direction, risk_aud, audusd, quote_to_usd=None, t2_r=None, atr_floor=None, fixed_pct=None, maxfill_atr=None):
    # 2026-10-27 Brett pt1486u: t2_r = per-book T2 override (hunt48 ASX/Asia pass hunt48_room.T2_R=1.5); None -> shared T2_R (US/crypto/FX unchanged)
    T2 = T2_R if t2_r is None else float(t2_r)
    px, atr = fnum(r["px"]), fnum(r["atr"])
    sgn = 1 if direction == "long" else -1
    if direction == "short": trig = min(fnum(r["lo10"], px), fnum(r["pdl"], px)) - 0.1 * atr
    else: trig = max(fnum(r["hi20"], px), fnum(r["pdh"], px)) + 0.1 * atr
    maxfill = trig * (1 + sgn * 0.0015)
    if maxfill_atr: maxfill = trig + sgn * float(maxfill_atr) * atr   # Brett 28 Oct 13:15 WITA hunt fix 3: hunt48 band = trigger + k*ATR (stop/size off max_fill, risk cap kept)
    rdist = min(1.5 * atr, 0.05 * trig)
    if atr_floor: rdist = max(float(atr_floor) * atr, rdist)   # review fix #4 (US): never a sub-1-ATR stop; shares shrink instead
    stop = maxfill - sgn * rdist
    t1, t2 = maxfill + sgn * T1_R * rdist, maxfill + sgn * T2 * rdist
    if fixed_pct:   # Brett 2026-10-28 07:12 WITA hunt books: (tp, sl) fractions of entry; T1 = T2 = target (full exit), shares sized off the sl
        rdist = fixed_pct[1] * maxfill; stop = maxfill - sgn * rdist; t1 = t2 = maxfill + sgn * fixed_pct[0] * maxfill
    kr = r.get("venue") == "kraken"; oa = r.get("venue") == "oanda"
    risk_usd = risk_aud * audusd
    ax = r.get("venue") == "asx"
    q2usd = (quote_to_usd or (lambda q: 1.0))(r["sym"].split("_")[-1]) if oa else (audusd if ax else 1.0)
    if oa and not q2usd: return None
    shares = int(risk_usd // (rdist * q2usd)) if not kr else round(risk_usd / rdist, 6)
    notional = shares * maxfill * q2usd
    if ax: cost_usd = asx_cost_usd(notional, px, audusd)
    elif oa: cost_usd = notional * fnum(r.get("cost_pct"), 0.1) / 100
    elif kr: cost_usd = notional * (COSTS["kraken_maker_pct"] + COSTS["kraken_taker_pct"]) / 100
    else: cost_usd = 2 * COSTS["ibkr_usd_side"] + notional * 2 * COSTS["slip_bp_side"] / 1e4 + (notional * us_borrow_pa(r["sym"]) * 3 / 365 if direction == "short" else 0)
    cost_R = cost_usd / risk_usd if risk_usd else 0
    # Break-even win rate uses the ACTUAL realised average winner R (closed paper hunt/bench trades), not an assumed
    # full 0.75R (GPT review: T1-then-scratch winners earn only 0.225R). No realised winners yet -> None + range shown.
    W = REALISED_WIN or {}
    be = (1 + cost_R) / (W["avg_win_R"] + 1) if W.get("n_win") else None
    if fixed_pct:
        _w = fixed_pct[0] / fixed_pct[1]; be = (1 + cost_R) / (_w + 1); W = dict(W, avg_win_R=_w, n_win=W.get("n_win") or 1)
    be_full = (1 + cost_R) / (0.5 * T1_R + 0.5 * T2 + 1); be_t1 = (1 + cost_R) / (0.5 * T1_R + 1)
    be_text = (f"needs {100*be:.0f}% win rate (realised avg winner {W['avg_win_R']:.2f}R, n={W['n_win']})" if be is not None else
               f"break-even win rate unknown: no realised winners yet (range {100*be_full:.0f}% if every winner hits T2 .. {100*be_t1:.0f}% if winners are T1-only)")
    return dict(direction=direction, trigger=round(trig, 4), max_fill=round(maxfill, 4), stop=round(stop, 4), t1=round(t1, 4), t2=round(t2, 4),
                shares=shares, atr=round(atr, 4), risk_aud=risk_aud, notional_usd=round(notional), pct_adv=round(100 * notional / max(fnum(r.get("adv_usd") or r.get("vol24_usd"), 1), 1), 3),
                est_cost_usd=round(cost_usd, 2), cost_R=round(cost_R, 3), rr_t1=T1_R, rr_t2=T2, breakeven_winrate=(round(be, 3) if be is not None else None),
                breakeven_range=[round(be_full, 3), round(be_t1, 3)], breakeven_text=be_text, realised_winners=W,
                breakeven_note="uses realised avg winner R from closed paper trades; range = all-T2 vs T1-only winners, net of est. costs",
                levels_rule=("fixed +%g%%/-%g%%" % (100 * fixed_pct[0], 100 * fixed_pct[1])) if fixed_pct else "atr")

def review(events, rows, cat, now, audusd, held=None, regime=None, horizon_h=HORIZON_H):
    """Every event on a scanned name -> review row. Returns (reviews, staged, risk_flags)."""
    held = held or {}; regime = regime or regime_from(rows)
    pre_fomc = now < FOMC_UTC + dt.timedelta(minutes=45)
    risk_aud = RISK_PRE_FOMC_AUD if pre_fomc else RISK_POST_FOMC_AUD
    by_sym = {}
    for e in events:
        if e["sym"] in rows: by_sym.setdefault(e["sym"], []).append(e)
    reviews, staged, flags = [], [], []
    hold_end = now + dt.timedelta(hours=horizon_h)
    q2u = make_q2usd(rows)
    # currency macro -> every OANDA instrument quoting that currency (index CFDs by quote ccy, metals/commods by USD)
    for e in events:
        c = e.get("ccy")
        if not c or e["type"] != "macro_high": continue
        for s, r in rows.items():
            if r.get("venue") == "oanda" and c in s.split("_"):
                by_sym.setdefault(s, []).append(dict(e, sym=s, type="macro_ccy", title=("CENTRAL BANK " if e.get("central_bank") else "") + e["title"]))
    for s, evs in by_sym.items():
        r = rows[s]
        upcoming = [e for e in evs if e.get("when_utc") and not e.get("past") and now - dt.timedelta(hours=2) <= dt.datetime.fromisoformat(e["when_utc"]) <= hold_end]
        recent = [e for e in evs if e.get("past")]
        if not upcoming and not recent: continue
        hi_risk = [e for e in upcoming if e["type"] in HIGH_RISK_TYPES]
        # recent (already-filed) material 8-K / guidance also size down (was upcoming-only -> AIR 8-K 1.01 stayed full A$2.5k).
        # One 50% cut per name from the immutable pre-FOMC base, never compounded across multiple events.
        sz_down = [e for e in upcoming if e["type"] in SIZE_DOWN_TYPES] + [e for e in recent if e["type"] in ("sec_8k_material", "guidance")]
        biases = [e.get("bias") for e in recent if e.get("bias")]
        rank_dir = r.get("dir") or ""
        direction = rank_dir if rank_dir in ("long", "short") else (max(set(biases), key=biases.count) if biases else "long")
        conflict = bool(biases) and any(b != direction for b in biases) and all(b != direction for b in biases)
        score = fnum(r.get("rank_score"), 0)
        why = gate(r, direction)
        if hi_risk: why.append("BLOCK: " + ", ".join(f"{e['type']} {e['when_wita']}" for e in hi_risk) + " inside hold window")
        pe = [e for e in recent if e["type"] == "post_earnings"]
        if pe and direction == "short" and fnum(r.get("roc5"), 0) <= -5:
            why.append(f"post-earnings reversal risk: 5d {fnum(r.get('roc5'),0):.0f}% after report (shorting such drops lost 1.07%/4d OOS, t-3.2)")
        if conflict: why.append(f"event bias {biases} conflicts with ranker dir {direction}")
        reg = regime.get({"kraken": "kraken", "ibkr": "ibkr", "asx": "asx"}.get(r.get("venue"), "ibkr" if r.get("cls") == "index" else "none"))
        if reg in ("long", "short") and reg != direction: why.append(f"regime filter: {reg}-only")
        if score < 3.0: why.append(f"rank_score {score:.2f}<3.0")
        rv = dict(sym=s, venue=r.get("venue"), dir=direction, rank_score=round(score, 2), events=[f"{e['type']}: {e['title']}" for e in upcoming + recent][:6],
                  upcoming=len(upcoming), passed=not why, why=why, size_mult=(0.5 if sz_down and not why else 1.0),
                  size_reason=(sorted({e["type"] for e in sz_down}) if sz_down else []))
        reviews.append(rv)
        if s in held:
            for e in hi_risk + sz_down:
                flags.append(dict(sym=s, book=held[s], event=e["title"], when=e["when_wita"], action=("CUT/AVOID before event (binary gap > stop)" if e in hi_risk else "size-down 50% or tighten")))
        if rv["passed"] and s in held: rv["why"].append(f"already in hunt book {held[s]}"); rv["passed"] = False
        if rv["passed"]:
            lv = stage_levels(r, direction, risk_aud * rv["size_mult"], audusd, q2u)
            if lv is None: rv["why"].append("no USD conversion for quote ccy"); rv["passed"] = False; continue
            staged.append(dict(sym=s, venue=r.get("venue"), score=rv["rank_score"] + 0.25 * len(recent), events=rv["events"], status="STAGED_BENCH (not armed)", **lv))
    staged.sort(key=lambda x: -x["score"])
    used, keep = {}, []
    for h in held:                      # names already armed/open in the hunt books count toward cluster caps
        for c in cluster_of(h, rows.get(h, {})): used[c] = used.get(c, 0) + 1
    for x in staged:
        _y = str(rows[x["sym"]].get("yahoo") or "")
        if x.get("venue") == "oanda" and ("*" in _y or "/" in _y): continue   # 2026-10-29: synthetic cross (e.g. XAU_JPY=GC=F*USDJPY=X) has no hourly bars for the bench -> dropped, never takes a top-3 slot
        cl = cluster_of(x["sym"], rows[x["sym"]])
        if any(used.get(c, 0) >= CLUSTER_CAP for c in cl): continue
        for c in cl: used[c] = used.get(c, 0) + 1
        keep.append(x)
    staged = keep[:MAX_STAGED]
    # Brett 27 Oct (pt1470u): ASX runs as a SEPARATE A$1m book (asx_hunt48.py, own 3 slots) -> ASX rows never take the shared top-3
    _nt = [x for x in staged if x.get("venue") != "asx"][:TOP_N]
    for x in staged: x["top3"] = x in _nt; x.update(book="hunt48_asx (separate A$1m book)") if x.get("venue") == "asx" else None
    reviews.sort(key=lambda x: (not x["passed"], -x["rank_score"]))
    return reviews, staged, flags

def held_names(base=BASE):
    held = {}
    for p in glob.glob(os.path.join(base, "state", "hunt48_short*", "ledger.json")):
        try:
            L = json.load(open(p))
            for t in L.get("trades", []):
                if t.get("status") in ("ARMED", "FILLED", "PARTIAL"): held[t["ticker"]] = f"{os.path.basename(os.path.dirname(p))}:{t['status']}"
        except Exception: pass
    return held

def extension_flags(held, cat, now, already, days=14, eod_next=None):
    """Hunt trades may extend up to 10 trading days: flag any armed/held name whose next earnings falls inside that span.
    2026-10-30: EODHD report date preferred; yfinance next_earn fallback."""
    out = []; eod_next = eod_next or {}
    for s, book in held.items():
        src = "EODHD" if s in eod_next else "yfinance"
        ne = eod_next.get(s) or (cat.get(s) or {}).get("next_earn")
        if s in already or not ne: continue
        try: d = dt.date.fromisoformat(str(ne)[:10])
        except Exception: continue
        dd = (d - now.astimezone(ET).date()).days
        if 0 <= dd <= days:
            out.append(dict(sym=s, book=book, event=f"Earnings {d} ({src} date)", when=d.strftime("%a %d %b"),
                            action=("CUT/AVOID: inside 72h hold" if dd <= 3 else f"NO EXTENSION past {d - dt.timedelta(days=1)} (earnings in {dd}d)")))
    return out

def build(now=None, fetch=http, indir=IN, write=True, online=True):
    now = now or now_utc(); FEEDS.clear()
    rows, cat, asof = load_universe(indir)
    audusd = 0.7022
    events = []
    if online:
        days = [(now.astimezone(ET) + dt.timedelta(days=i)).date() for i in range(0, 4)]
        eod_ev, eod_next = earnings_eodhd(days[0], days[0] + dt.timedelta(days=14))
        _eod_syms = {e["sym"] for e in eod_ev if dt.datetime.fromisoformat(e["when_utc"]).astimezone(ET).date() <= days[-1]}
        events += [e for e in eod_ev if e["sym"] in _eod_syms and dt.datetime.fromisoformat(e["when_utc"]).astimezone(ET).date() <= days[-1]]
        events += [e for e in earnings_nasdaq(days, fetch) if e["sym"] not in _eod_syms]
        events += sec_8k(sec_tickers(fetch), fetch)
        events += macro_ff(fetch)
        events += crypto_events(rows, fetch)
        events += fda_rtt(fetch)
    else:
        eod_next = {}
        events += [ev("macro_high", "MACRO", FOMC_UTC, "FOMC decision (offline fallback)", "desk.static")]
    have = {(e["sym"], e["type"]) for e in events if e["type"] == "earnings"}
    events += [e for e in earnings_catcache(cat, now) if (e["sym"], "earnings") not in have]
    events += catcache_state_events(cat, now)
    events += news_bot_events(indir)
    events += asx_events(indir, now)
    universe = set(rows)
    for e in events: e["scanned"] = e["sym"] in universe
    held = held_names()
    global REALISED_WIN; REALISED_WIN = realised_winners()
    reviews, staged, flags = review(events, rows, cat, now, audusd, held=held)
    flags += extension_flags(held, cat, now, {f["sym"] for f in flags}, eod_next=eod_next)
    end = now + dt.timedelta(hours=HORIZON_H)
    def in72(e):
        if not e.get("when_utc"): return False
        t = dt.datetime.fromisoformat(e["when_utc"]); return now - dt.timedelta(hours=2) <= t <= end
    next72 = sorted([e for e in events if in72(e) and not e.get("past") and (e["scanned"] or e["sym"] == "MACRO")], key=lambda e: e["when_utc"])
    out = dict(generated_utc=iso(now), generated_wita=w(now), paper_only=True, horizon_h=HORIZON_H, sweep_asof_wita=w(asof),
               universe=dict(total=len(rows), us=sum(r["venue"] == "ibkr" for r in rows.values()), kraken=sum(r["venue"] == "kraken" for r in rows.values()), oanda=sum(r["venue"] == "oanda" for r in rows.values()), asx=sum(r["venue"] == "asx" and r.get("cls") != "index_ref" for r in rows.values())),
               regime=regime_from(rows), risk_per_trade_aud=(RISK_PRE_FOMC_AUD if now < FOMC_UTC + dt.timedelta(minutes=45) else RISK_POST_FOMC_AUD),
               counts=dict(events_total=len(events), next72=len(next72), next72_earnings=sum(e["type"] == "earnings" for e in next72),
                           next72_macro=sum(e["sym"] == "MACRO" for e in next72), recent_signals=sum(bool(e.get("past")) and e["scanned"] for e in events),
                           reviewed=len(reviews), passed=sum(r["passed"] for r in reviews), staged=len(staged), risk_flags=len(flags)),
               feeds=FEEDS, next72=next72[:900], risk_flags=flags, staged=staged, reviews=reviews[:300],
               rules=dict(trigger="any upcoming (<=72h) or new (sweep/8-K) event on a scanned name -> review", review="existing ranker rank_score>=3 + desk gates + regime + no conflict",
                          block="earnings/FDA/M&A-target inside hold window -> no new entry; held names flagged CUT/AVOID", size_down="8-K material/guidance/unlock/high macro in window -> 50% size",
                          staging="STAGED_BENCH only - never armed or fired by this module; arming stays with hunt engine apply-plan + max 3 open",
                          rr=f"T1 {T1_R}R / T2 {T2_R}R (fix); stop min(1.5ATR,5%)", news_macro=news_bot_events.__doc__ or ""))
    if write:
        atomic(os.path.join(CD, "calendar_latest.json"), out); atomic(OUT_STATIC, out)
        atomic(os.path.join(CD, "history", now.astimezone(WITA).strftime("calendar_%Y%m%d_%H%M.json")), out)
        with open(os.path.join(CD, "review_queue.jsonl"), "a") as f:
            for rv in reviews: f.write(json.dumps(dict(at=iso(now), **rv)) + "\n")
    return out

# ---------------------------------------------------------------- ASX extension (Brett 27 Oct 04:2x WITA; additive, PAPER ONLY)
AUDUSD_GATE = 0.7022          # same fixed rate build() uses; converts A$5m ADV floor -> US$ for US names
ASX_BROKER_AUD_SIDE, ASX_SLIP_BP_SIDE = 10.0, 10.0
def asx_tick(p): return 0.001 if p < 0.10 else (0.005 if p < 2.0 else 0.01)
def asx_cost_usd(notional_usd, px, audusd):
    """A$10/side brokerage + 10bp/side slippage + half a tick per side (spread), round trip, in USD."""
    n_aud = notional_usd / audusd
    return audusd * (2 * ASX_BROKER_AUD_SIDE + n_aud * (2 * ASX_SLIP_BP_SIDE / 1e4 + (asx_tick(px) / px if px else 0)))
def asx_regime(rows):
    r = rows.get("^AXJO")
    if not r: return "unknown"
    return "long" if fnum(r.get("px"), 0) > fnum(r.get("m50"), 0) else "short"
ASX_COST_R_MAX = 0.20   # 2026-10-28 07:18 Brett: ASX book cost cap 0.15 -> 0.20R (US stays 0.15)
def asx_gate(r, direction):
    why = []
    if direction == "short" and str(r.get("asx_shortable")) != "True":
        why.append("ASX short not permitted: not on short-sell list (ASIC short-position proxy) -> long-only for this name")
    px = fnum(r.get("px"), 0); atrp = fnum(r.get("atr_pct"), 99); stop_pct = min(1.5 * atrp, 5.0)
    cp = 2 * ASX_SLIP_BP_SIDE / 100 + (100 * asx_tick(px) / px if px else 99)
    if stop_pct > 0 and cp / stop_pct > ASX_COST_R_MAX: why.append(f"ASX slippage+tick {cp:.2f}% = {cp/stop_pct:.2f}R of stop (>{ASX_COST_R_MAX}R)")
    return why
def asx_events(indir=IN, now=None):
    """ASX company announcements (price-sensitive only) as recent events; yfinance ASX earnings dates as date-only earnings."""
    out = []; p = os.path.join(indir, "asx_events.json")
    if not os.path.exists(p): feed("asx.events", False, "no asx_events.json (run universe_asx.py)"); return out
    j = json.load(open(p)); now = now or now_utc()
    for a in j.get("announcements", []):
        if not a.get("price_sensitive"): continue
        t = dt.datetime.fromisoformat(a["when_utc"])
        if (now - t).total_seconds() > 4 * 86400: continue
        out.append(ev("asx_price_sensitive", a["code"] + ".AX", t, f"ASX price-sensitive: {a['headline']}", "asx.announcements", past=True))
    for c, d in (j.get("earnings") or {}).items():
        try: dd = dt.date.fromisoformat(d)
        except Exception: continue
        t = dt.datetime.combine(dd, dt.time(9, 0), ZoneInfo("Australia/Sydney"))
        if -1 <= (t - now).total_seconds() / 86400 <= 10:
            out.append(ev("earnings", c + ".AX", t, "ASX result/earnings date (yfinance; time TBC, usually pre-open AEDT)", "yf.asx", conf="date-only", date_only=True, session="TBC"))
    feed("asx.events", True, f"{len(out)} events ({sum(e['type']=='asx_price_sensitive' for e in out)} price-sensitive anns, {sum(e['type']=='earnings' for e in out)} earnings) from {j.get('generated_utc','?')[:16]}Z")
    return out

if __name__ == "__main__":
    o = build(online="--offline" not in sys.argv)
    print(json.dumps(o["counts"]), "| feeds ok:", sum(v["ok"] for v in o["feeds"].values()), "/", len(o["feeds"]))
    for s in o["staged"][:5]: print("STAGED", s["sym"], s["dir"] if "dir" in s else s["direction"], s["score"], s["trigger"], s["stop"], s["t1"], s["t2"], s["breakeven_text"])
