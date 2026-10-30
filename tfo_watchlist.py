#!/usr/bin/env python3
"""TFO V1 Head Trader PRE-OPEN WATCHLIST (tfo-mandate + tfo-pre-open-hunt, 2026-09-25). PAPER ONLY. Token-free.

  tfo_watchlist.py tick                     launchd every 300s: build every market whose regular open is 20-30 min
                                            away (weekday, not a listed holiday) + refresh the crypto list every tick
  tfo_watchlist.py build MARKET             forced build: ASX TOKYO HK INDIA EUROPE(LONDON) US(TORONTO) B3 CRYPTO
  tfo_watchlist.py show [MARKET]            compact table from reports/ht_watchlist.json
  tfo_watchlist.py fire MARKET [--execute] [--top N] [--setup funder_watch_20260927|crypto_hunt|watchlist_momentum_equity|watchlist_momentum_crypto]
  tfo_watchlist.py report [--setup S] [--json]   per-setup trades / win rate / gross & net P&L from reports/ht_fills_tagged.jsonl
                                            at/after the first eligible regular-session print: re-check each top
                                            candidate through tfo_buy.py / tfo_crypto.py buy (they own every gate) and
                                            call them with --execute only when asked. Default is preview.

GATES ARE NOT RE-IMPLEMENTED. build calls tfo_buy.gates() / tfo_crypto.gates() themselves, fed a per-build read-only
quote cache (fundamental_book.fetch_prices, yfinance Ticker, Kraken get/quote/avg10_vol are memoised for the build).
Before the open, the tfo_buy fails that by definition need the regular-session print (session/tape/auction window,
size vs TODAY's $vol, session change) are listed as `deferred_to_open`; every other tfo_buy fail rejects. fire
re-runs the real tools, so at the open the buy tools are the only authority.
tick/build/show never ticket and never write a ledger: save/execute in the desk modules are swapped for a raising
guard while the watchlist runs, and the only files written are reports/ht_watchlist*.json (atomic).
Trend filter on every candidate (build and fire): last > 50d SMA and 20d return > 0, else reject.
News gate on EQUITY candidates (build and fire; yfinance .news/.calendar only): earnings within 1 trading day or a negative
keyword headline in the last 36h rejects; a fetch error passes with flag news_unchecked.
Vol sizing: clip <= 0.25% firm equity / max(5%, 2*ATR14/price) (rail stop stays -5%). Names held in fundamental_book or
convexity_sleeve are never re-bought ('legacy book holds name'). Every fill is setup-tagged in reports/ht_fills_tagged.jsonl.
Scores are 'beta' (momentum/gap/liquidity exposure; no tested edge). The $15k/day stretch never sizes a clip.
"""
import argparse, contextlib, datetime as dt, fcntl, json, math, os, re, subprocess, sys, tempfile, time
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v1_venues  # V1 entry venues: US/.T/.AX from 29 Sep Sydney; US/.AX only from 29 Sep 16:22 AEST (no Tokyo/Asia). Entries only; crypto ungated

BASE = os.path.dirname(os.path.abspath(__file__))
R = os.path.join(BASE, "reports")
OUT = os.path.join(R, "ht_watchlist.json")
HB = os.path.join(R, "ht_watchlist_heartbeat.json")
UTC, SYD = dt.timezone.utc, ZoneInfo("Australia/Sydney")
VERSION = "ht_watchlist v1 2026-09-25"
WINDOW_MIN = (20, 30)            # build when the regular open is 20..30 min away
MIN_CLIP_USD = 10000.0           # below this a clip is not worth the tape; disclosed, not a mandate gate
MAX_DEEP = 60                    # per market, deep-check (bid/ask + tfo_buy.gates) the most liquid N by 10d $vol
CRYPTO_N = 15                    # Kraken pairs deep-checked per tick
TOP_REJECTS = 20
RISK_PCT = 0.0075               # vol sizing: loss at the sizing stop ~= 0.75% of firm equity (HT 2026-09-25: keeps typical ETF-vol names near tier clip; trims only high-vol names)
MANDATE_STOP = 0.05             # long rail stop -5% (never widened; wider ATR shrinks size instead)
TAGS = os.path.join(R, "ht_fills_tagged.jsonl")
FUNDER_WATCH_SETUP = "funder_watch_20260927"
FUNDER_WATCH_PRIORITY = {
    "ASX": ("XRO.AX", "ALL.AX", "CSL.AX"),
    # "TOKYO" removed 2026-09-29 16:22 AEST (funder override: no more Tokyo or Asia markets); was 8035.T 6857.T 6501.T
    "US": ("RCL", "GOOGL", "ONON", "SCHW"),
}
FUNDER_WATCH_NAMES = {s for names in FUNDER_WATCH_PRIORITY.values() for s in names}
FUNDER_WATCH_BENCH = {"US": ("JPM",)}
SPLIT_RULES = {"8035.T": {"last_cum_date": "2026-09-28", "ex_date": "2026-09-29", "force_flatten_by": "2026-09-28 15:20 JST", "automatic_force_flatten": False}}
CORRELATED_TWIN_GROUPS = (frozenset(("8035.T", "6857.T")), frozenset(("RCL", "GOOGL")))
SETUPS = ("watchlist_momentum_equity", "watchlist_momentum_crypto", "crypto_hunt", FUNDER_WATCH_SETUP)
FIRE_MAX_LATE_MIN = 120          # fire --execute refuses a pre-open list more than 2h after the open
FRESH_REBUILD_MAX_MIN = 15       # ...unless the list was rebuilt AFTER the open and is <= 15 min old (a full in-session gate run)
SCORE_FORMULA = ("beta score = clamp(mom20%/ATR%,-3,3) + 0.5*clamp(gap%/ATR%,-2,2) [gap = pre-market gap, else prior-day "
                 "change; crypto: change vs UTC open] + 0.25*log10(ADV10$/1e8) - roundtrip_cost_bp/50 "
                 "[fees buy+sell via desk_costs + spread]; ATR% floored at 0.5. Label 'beta': no tested edge.")

MARKETS = {  # local exchange clock (tfo-pre-open-hunt table); fire_after = minutes after the open to first eligible print
    # 2026-09-28: DELAYED venues wait out the Yahoo delay (~15-20 min): at open+8 the delayed tape still shows a PRE-open bar, so every
    # name failed "not a regular-session executable print" at the pounce (HK 0939/2269, 28 Sep) and fire does not retry.
    "ASX":    {"tz": "Australia/Sydney",  "open": (10, 0), "fire_after": 22, "tape": "DELAYED"},
    "TOKYO":  {"tz": "Asia/Tokyo",        "open": (9, 0),  "fire_after": 20, "tape": "DELAYED"},
    "HK":     {"tz": "Asia/Hong_Kong",    "open": (9, 30), "fire_after": 20, "tape": "DELAYED"},
    "INDIA":  {"tz": "Asia/Kolkata",      "open": (9, 15), "fire_after": 20, "tape": "DELAYED"},
    "EUROPE": {"tz": "Europe/London",     "open": (8, 0),  "fire_after": 20, "tape": "DELAYED"},
    "US":     {"tz": "America/New_York",  "open": (9, 30), "fire_after": 8,  "tape": "LIVE (US regular-session print <60s); .TO DELAYED"},
    "B3":     {"tz": "America/Sao_Paulo", "open": (10, 0), "fire_after": 20, "tape": "DELAYED"},
}
ORDER = ["ASX", "TOKYO", "HK", "INDIA", "EUROPE", "B3", "US", "CRYPTO"]
ALIASES = {"LONDON": "EUROPE", "LSE": "EUROPE", "XETRA": "EUROPE", "NY": "US", "NYSE": "US", "TORONTO": "US", "TSX": "US",
           "JAPAN": "TOKYO", "TSE": "TOKYO", "NSE": "INDIA", "BRAZIL": "B3", "HKEX": "HK", "KRAKEN": "CRYPTO"}

# Liquid universe: large caps + major ETFs per venue ("theme: SYM ..."). Themes feed the two-clips-per-theme gate.
UNIVERSE_SPEC = {
    "US": """mega-tech: AAPL MSFT AMZN GOOGL META NFLX ORCL CRM ADBE NOW IBM UBER PLTR
        ev-auto: TSLA
        us-fin: JPM BAC WFC GS MS V MA BRK-B SCHW
        us-health: LLY UNH JNJ ABBV MRK
        us-consumer: WMT COST HD PG KO PEP DIS MCD ONON
        us-industrial: CAT GE BA RCL
        us-energy: XOM CVX
        semis: NVDA AVGO
        adr-china: BABA PDD JD
        adr-europe: NVO SAP AZN SHEL BP HSBC UL
        adr-asia: TM SONY MUFG INFY
        adr-latam: VALE PBR NU
        adr-mining: BHP RIO
        etf-sector: XLK XLF XLE XLV XLI XLY XLP XLU XLB XLC XLRE
        etf-index: IWM DIA
        etf-intl: EEM EFA FXI EWJ EWZ KWEB
        etf-metals: GLD SLV GDX
        etf-energy-cmdty: USO UNG
        etf-bonds: TLT IEF HYG LQD TIP
        etf-crypto: IBIT
        ca-banks: RY.TO TD.TO BMO.TO BNS.TO
        ca-energy: ENB.TO CNQ.TO SU.TO
        ca-other: SHOP.TO CNR.TO CP.TO BN.TO TRI.TO ATD.TO ABX.TO XIU.TO""",
    "ASX": """au-banks: CBA.AX NAB.AX WBC.AX ANZ.AX MQG.AX
        au-miners: BHP.AX RIO.AX FMG.AX NST.AX
        au-energy: WDS.AX STO.AX
        au-health: CSL.AX
        au-consumer: WES.AX WOW.AX COL.AX ALL.AX
        au-other: TLS.AX GMG.AX TCL.AX QBE.AX XRO.AX REA.AX
        au-etf: STW.AX IOZ.AX VAS.AX""",
    "TOKYO": """jp-auto: 7203.T 7267.T 6902.T
        jp-tech: 6758.T 6861.T 8035.T 6857.T 6501.T 7974.T 9984.T 6098.T
        jp-banks: 8306.T 8316.T 8766.T
        jp-trading: 8058.T 8001.T
        jp-other: 9432.T 4063.T 9983.T 4502.T
        jp-etf: 1306.T 1321.T""",
    "HK": """hk-internet: 0700.HK 9988.HK 3690.HK 9618.HK 1024.HK 9999.HK 1810.HK
        hk-fin: 1299.HK 0005.HK 0939.HK 1398.HK 2318.HK 0388.HK
        hk-other: 0941.HK 1211.HK 0883.HK 0016.HK 2269.HK
        hk-etf: 2828.HK 3033.HK""",
    "INDIA": """in-banks: HDFCBANK.NS ICICIBANK.NS SBIN.NS KOTAKBANK.NS AXISBANK.NS BAJFINANCE.NS
        in-it: TCS.NS INFY.NS
        in-other: RELIANCE.NS BHARTIARTL.NS ITC.NS LT.NS HINDUNILVR.NS MARUTI.NS SUNPHARMA.NS M&M.NS NTPC.NS TITAN.NS
        in-etf: NIFTYBEES.NS""",
    "EUROPE": """uk-energy: SHEL.L BP.L
        uk-pharma: AZN.L GSK.L
        uk-banks: HSBA.L BARC.L LLOY.L
        uk-staples: ULVR.L BATS.L DGE.L
        uk-miners: RIO.L GLEN.L
        uk-other: LSEG.L REL.L NG.L VOD.L ISF.L
        de-large: SAP.DE SIE.DE ALV.DE DTE.DE MBG.DE BMW.DE BAS.DE EXS1.DE
        fr-large: AIR.PA MC.PA OR.PA TTE.PA SAN.PA BNP.PA
        nl-large: INGA.AS
        ch-large: NESN.SW NOVN.SW ROG.SW UBSG.SW
        nordic: NOVO-B.CO
        es-it: SAN.MC IBE.MC ENEL.MI ISP.MI""",
    "B3": """br-commodity: PETR4.SA VALE3.SA PRIO3.SA SUZB3.SA
        br-banks: ITUB4.SA BBDC4.SA BBAS3.SA ITSA4.SA B3SA3.SA
        br-other: ABEV3.SA WEGE3.SA RENT3.SA ELET3.SA
        br-etf: BOVA11.SA""",
}

def funder_watch_meta(market, sym):
    names = FUNDER_WATCH_PRIORITY.get(market, ())
    if sym not in names:
        return {}
    meta = {"setup": FUNDER_WATCH_SETUP, "funder_watch": True, "hunt_priority": names.index(sym) + 1}
    if sym in SPLIT_RULES:
        meta["split_rule"] = SPLIT_RULES[sym]
        meta["split_note"] = "excluded: split 29 Sep, no auto-flatten"
    return meta

def funder_must_check(market):
    """Funder-watch priority + bench names for a market: always deep-checked and always shown in rejects."""
    return set(FUNDER_WATCH_PRIORITY.get(market, ())) | set(FUNDER_WATCH_BENCH.get(market, ()))

def top_rejects(rejects, market):
    """Top-N rejects by $vol, plus any funder-watch/bench reject beyond N (funder must see why a name failed)."""
    must = funder_must_check(market)
    return rejects[:TOP_REJECTS] + [r for r in rejects[TOP_REJECTS:] if r.get("symbol") in must]

def split_entry_reject(market, sym, day):
    if market == "TOKYO" and sym == "8035.T":
        return "excluded: split 29 Sep, no auto-flatten"
    return None

def universe(market):
    out = []
    for line in UNIVERSE_SPEC[market].strip().splitlines():
        theme, syms = line.split(":", 1)
        out += [(s, theme.strip()) for s in syms.split()]
    return out

def venue_of(sym):
    return "US" if "." not in sym else sym.rsplit(".", 1)[1]

# Weekday exchange closures (cheap, hand-kept; sources: NYSE/TSX/ASX/JPX/HKEX circular/NSE circular/LSE/Xetra/B3 2026).
HOLIDAYS_VALID_THROUGH = dt.date(2027, 1, 1)
HOLIDAYS = {
    "US": {"2026-11-26", "2026-12-25", "2027-01-01"},
    "TO": {"2026-10-12", "2026-12-25", "2026-12-28", "2027-01-01"},
    "AX": {"2026-12-25", "2026-12-28", "2027-01-01"},
    "T":  {"2026-10-12", "2026-11-03", "2026-11-23", "2026-12-31", "2027-01-01"},
    "HK": {"2026-10-01", "2026-10-19", "2026-12-25", "2027-01-01"},
    "NS": {"2026-10-02", "2026-10-20", "2026-11-10", "2026-11-24", "2026-12-25"},
    "L":  {"2026-12-25", "2026-12-28", "2027-01-01"},
    "DE": {"2026-12-24", "2026-12-25", "2026-12-31", "2027-01-01"},
    "PA": {"2026-12-25", "2027-01-01"}, "AS": {"2026-12-25", "2027-01-01"}, "MC": {"2026-12-25", "2027-01-01"},
    "MI": {"2026-12-24", "2026-12-25", "2026-12-31", "2027-01-01"},
    "SW": {"2026-12-24", "2026-12-25", "2026-12-31", "2027-01-01"},
    "CO": {"2026-12-24", "2026-12-25", "2026-12-31", "2027-01-01"},
    "SA": {"2026-10-12", "2026-11-02", "2026-11-20", "2026-12-24", "2026-12-25", "2026-12-31", "2027-01-01"},
}

# ---------------------------------------------------------------- clock (zoneinfo; DST-safe by construction)
def now_utc():
    return dt.datetime.now(UTC)

def open_on(market, day):
    m = MARKETS[market]; h, mi = m["open"]
    return dt.datetime(day.year, day.month, day.day, h, mi, tzinfo=ZoneInfo(m["tz"]))

def open_venues(market, day):
    vs = sorted({venue_of(s) for s, _ in universe(market)})
    return [v for v in vs if day.isoformat() not in HOLIDAYS.get(v, ())]

def is_trading_day(market, day):
    return day.weekday() < 5 and bool(open_venues(market, day))

def todays_open(market, now):
    day = now.astimezone(ZoneInfo(MARKETS[market]["tz"])).date()
    return open_on(market, day) if is_trading_day(market, day) else None

def next_open(market, now):
    day = now.astimezone(ZoneInfo(MARKETS[market]["tz"])).date()
    for i in range(0, 15):
        d = day + dt.timedelta(days=i)
        if is_trading_day(market, d) and open_on(market, d) > now:
            return open_on(market, d)
    return None

def minutes_to(t, now):
    return (t - now).total_seconds() / 60.0

def markets_due(now):
    due = []
    for mk in MARKETS:
        o = todays_open(mk, now)
        if o is not None and WINDOW_MIN[0] <= minutes_to(o, now) <= WINDOW_MIN[1] \
                and v1_venues.market_block_reason(mk, o) is None:   # removed markets: tick never wakes them
            due.append(mk)
    return due

def stamp(t):
    return {"utc": t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "sydney": t.astimezone(SYD).strftime("%Y-%m-%d %H:%M %Z")}

# ---------------------------------------------------------------- desk modules (imported, never modified)
_MODS = {}
def desk(name):
    if name not in _MODS:
        if BASE not in sys.path:
            sys.path.insert(0, BASE)
        with contextlib.redirect_stdout(sys.stderr):   # fundamental_book prints module warnings at import
            _MODS[name] = __import__(name)
    return _MODS[name]

class LedgerWriteRefused(RuntimeError):
    pass

@contextlib.contextmanager
def readonly_guard():
    """Swap every ledger writer / ticket function in the loaded desk modules for a raising guard."""
    def refuse(*a, **k):
        raise LedgerWriteRefused("tfo_watchlist is read-only: ledger write/ticket refused")
    names = {"tfo_ticket": ["save", "execute", "transfer"], "tfo_buy": ["save", "execute"],
             "tfo_crypto": ["save", "xfer", "cmd_buy", "cmd_rails", "cmd_mark", "log"]}
    saved = []
    for mod, attrs in names.items():
        m = sys.modules.get(mod)
        for a in attrs if m else ():
            if hasattr(m, a):
                saved.append((m, a, getattr(m, a))); setattr(m, a, refuse)
    try:
        yield
    finally:
        for m, a, f in saved:
            setattr(m, a, f)

class _Snap:
    """One yfinance read per symbol per build; tfo_buy.gates sees exactly what the watchlist saw."""
    FI = ("previousClose", "lastVolume", "tenDayAverageVolume", "lastPrice")
    def __init__(self, sym, ticker_cls):
        self.ticker = sym; self._fi = self._inf = self._efi = self._einf = None
        try:
            t = ticker_cls(sym)
        except Exception as e:
            self._efi = self._einf = e; return
        try:
            fi = t.fast_info; d = {}
            for k in self.FI:
                try: d[k] = fi[k]
                except Exception: d[k] = None
            self._fi = d
        except Exception as e:
            self._efi = e
        try:
            self._inf = dict(t.info or {})
        except Exception as e:
            self._einf = e
    @property
    def fast_info(self):
        if self._efi: raise self._efi
        return self._fi
    @property
    def info(self):
        if self._einf: raise self._einf
        return self._inf

@contextlib.contextmanager
def equity_cache(quotes, qfails, snaps):
    """Feed tfo_buy.gates from the build's cache: fundamental_book.fetch_prices + yfinance.Ticker."""
    fb = desk("fundamental_book"); import yfinance as yf
    real_fp, real_t = fb.fetch_prices, yf.Ticker
    def fp(symbols, throttle=0.35, require_executable=True):
        if all(s in quotes or s in qfails for s in symbols):
            return {s: quotes[s] for s in symbols if s in quotes}, {s: qfails[s] for s in symbols if s in qfails}
        return real_fp(symbols, throttle=throttle, require_executable=require_executable)
    def tk(sym, *a, **k):
        if sym not in snaps:
            snaps[sym] = _Snap(sym, real_t)
        return snaps[sym]
    fb.fetch_prices, yf.Ticker = fp, tk
    try:
        yield real_t
    finally:
        fb.fetch_prices, yf.Ticker = real_fp, real_t

@contextlib.contextmanager
def kraken_cache(tc, throttle=0.35):
    real_get, real_q, real_a = tc.get, tc.quote, tc.avg10_vol
    memo = {}
    def get(path, **params):
        key = (path, tuple(sorted(params.items())))
        if key not in memo:
            memo[key] = real_get(path, **params)
            if throttle and real_get.__module__ == "tfo_crypto": time.sleep(throttle)
        return memo[key]
    qm, am = {}, {}
    def quote(pair):
        if pair not in qm: qm[pair] = real_q(pair)
        return qm[pair]
    def avg10(pair):
        if pair not in am: am[pair] = real_a(pair)
        return am[pair]
    tc.get, tc.quote, tc.avg10_vol = get, quote, avg10
    try:
        yield
    finally:
        tc.get, tc.quote, tc.avg10_vol = real_get, real_q, real_a

# ---------------------------------------------------------------- firm, fees, metrics
def firm_snapshot():
    """Same numbers tfo_buy.gates/tfo_crypto.gates use (firm_state, ht_clip notional + crypto qty*entry, powder)."""
    tb = desk("tfo_buy")
    eq, dd, opn = tb.firm_state()
    try: seq = float(tb.sizing_eq())   # 2026-10-29: vol/shark sizing ex veteran_desk; fail closed -> 0 size
    except Exception: seq = 0.0
    fbk = tb.load(tb.BOOK)
    clips = [p for p in fbk["positions"].values() if p.get("ht_clip")]
    eq_clip = sum(float(p.get("notional_usd") or 0) for p in clips)
    try:
        cb = tb.load("tfo_ht_crypto"); cpos = cb.get("positions", {})
    except Exception:
        cb, cpos = {}, {}
    crest = (cb.get("resting_entries") or {}) if isinstance(cb, dict) else {}   # gate F3 17 Oct: resting post-only entries are committed
    cr = sum(p["qty"] * p["entry_px"] for p in cpos.values()) + sum(float(r.get("usd") or 0) for r in crest.values())
    try:  # 2026-09-26 HT decision: same heat-at-live-stops basis as tfo_buy/tfo_crypto gates (tfo_heat)
        heat = desk("tfo_heat").clip_heat(fbk["positions"], cb)[0]
    except Exception:
        heat = 0.05 * eq_clip + 0.05 * cr
    cap = tb.HEAT_CAP_PCT * eq
    g = tb.load(tb.GCL); unreserved = float(g["cash"]) - float(g.get("reserved_usd") or 0)
    day = (eq - opn) / opn if opn else None
    freezes = []
    if dd <= -10: freezes.append("firm DD freeze")
    if day is not None and day <= -0.03: freezes.append("Sydney-day -3% buy freeze")
    th_eq, th_cr = {}, {}
    for p in clips: th_eq[p.get("ht_theme")] = th_eq.get(p.get("ht_theme"), 0) + 1
    for p in list(cpos.values()) + list(crest.values()): th_cr[p.get("theme")] = th_cr.get(p.get("theme"), 0) + 1
    return {"firm_equity_usd": round(eq, 2), "sizing_equity_usd": round(seq, 2), "firm_drawdown_pct": dd, "sydney_day_chg_pct": None if day is None else round(100 * day, 3),
            "ht_equity_clip_notional_usd": round(eq_clip, 2), "crypto_open_usd": round(cr, 2),
            "heat_at_stops_usd": round(heat, 2), "heat_cap_usd": round(cap, 2), "heat_headroom_usd": round(cap - heat, 2),
            "headroom_notional_usd": round(max(0.0, (cap - heat) / 0.05), 2), "unreserved_powder_usd": round(unreserved, 2),
            "crypto_stack_room_usd": round(max(0.0, desk_attr("tfo_crypto", "STACK_CAP", 1000000.0) - cr), 2),
            "freezes": freezes, "held_equity": sorted(fbk["positions"]), "held_crypto": sorted(set(cpos) | set(crest)),
            "themes_equity": th_eq, "themes_crypto": th_cr}

def desk_attr(mod, attr, default):
    try: return getattr(desk(mod), attr)
    except Exception: return default

_NO_COSTS = []
def fee_est(sym, usd, px_usd, px=None, ccy=None, side="buy", when=None):
    """desk_costs.fill_fees if present on the desk; else a conservative fallback (disclosed in the output)."""
    if usd <= 0 or not px_usd:
        return 0.0, "none"
    if not _NO_COSTS:
        try:
            dc = desk("desk_costs")
            return float(dc.fill_fees(sym, side, usd / px_usd, px_usd, px, ccy, when)["fees_usd"]), "desk_costs"
        except ImportError:
            _NO_COSTS.append(1)
    if "/" in sym: return usd * 0.0040, "fallback"   # Kraken taker 0.40% (Brett real fees 13 Oct 2026; was 0.26%)
    if venue_of(sym) == "US": return max(1.0, 0.005 * usd / px_usd), "fallback"
    return usd * 0.006, "fallback"

def floor_k(x):
    return max(0.0, math.floor(x / 1000.0) * 1000.0)

def fnum(x):
    try:
        x = float(x); return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None

def bar_metrics(rows):
    """rows: [(date,o,h,l,c,v)] completed sessions -> prior-day %, 20d momentum %, ATR14 %, 10d mean c*v (native)."""
    out = {"prior_day_pct": None, "mom20_pct": None, "atr_pct": None, "adv10_native": None}
    if len(rows) >= 2:
        out["prior_day_pct"] = 100 * (rows[-1][4] / rows[-2][4] - 1)
    if len(rows) >= 21:
        out["mom20_pct"] = 100 * (rows[-1][4] / rows[-21][4] - 1)
    if len(rows) >= 15:
        trs = [max(r[2] - r[3], abs(r[2] - p[4]), abs(r[3] - p[4])) for p, r in zip(rows[-15:-1], rows[-14:])]
        out["atr_pct"] = 100 * (sum(trs) / len(trs)) / rows[-1][4]
    if rows:
        tail = rows[-10:]; out["adv10_native"] = sum(r[4] * r[5] for r in tail) / len(tail)
    return out

TREND_RULE = ("trend filter (every candidate, build AND fire): last price > SMA50 of the last 50 completed daily closes AND "
              "20d return = last / close 20 completed sessions before the latest close - 1 > 0; < 50 bars = reject")

def trend_check(closes, last):
    """[] = pass; else the reject reasons. closes: completed daily closes (oldest first), same units as `last`."""
    last = fnum(last)
    if len(closes) < 50 or not last:
        return ["trend filter: insufficient history" + ("" if last else " (no last price)")]
    sma = sum(closes[-50:]) / 50.0
    out = []
    if not last > sma: out.append("trend filter: below 50d SMA")
    if not last / closes[-21] - 1 > 0: out.append("trend filter: 20d return <= 0")
    return out

def trend_stats(closes, last):
    last = fnum(last)
    if len(closes) < 50 or not last: return {"sma50": None, "ret20_pct": None}
    return {"sma50": round(sum(closes[-50:]) / 50.0, 6), "ret20_pct": round(100 * (last / closes[-21] - 1), 2)}

def fire_history(mk, c, now):
    """Fresh completed daily bars + last price at fire time (no build cache). -> (rows, last)."""
    if mk == "CRYPTO":
        tc = desk("tfo_crypto")
        oh = [r for r in list(tc.get("OHLC", pair=tc.kpair(c["symbol"]), interval=1440).values())[0] if isinstance(r, list)][:-1]
        rows = [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[6])) for r in oh]
        return rows, tc.quote(c["symbol"])["last"]
    import yfinance as yf
    today = now.astimezone(ZoneInfo(MARKETS[mk]["tz"])).date()
    rows = load_history([c["symbol"]], before=today).get(c["symbol"], [])
    return rows, yf.Ticker(c["symbol"]).fast_info["lastPrice"]

def fire_trend(mk, c, now, hist=None):
    """Fresh trend re-check at fire time. -> (reasons, atr_pct)."""
    try:
        rows, last = hist or fire_history(mk, c, now)
        return trend_check([r[4] for r in rows], last), bar_metrics(rows)["atr_pct"]
    except Exception as e:
        return ["trend filter: insufficient history (re-check error: %s)" % e], None

def vol_rule():
    return ("vol sizing: sizing stop = max(%.0f%%, 2*ATR14/price); vol_size = %.2f%% of firm equity / sizing stop; clip = min(vol_size, "
            "tier cap, 5%% ADV10, heat room, crypto stack room, powder net fees). Rail stop stays -5%%; risk_usd = clip*5%% "
            "(heat basis), vol_risk_usd = clip*sizing stop." % (100 * MANDATE_STOP, 100 * RISK_PCT))

# ---------------------------------------------------------------- news gate (EQUITY only; yfinance Ticker.news + Ticker.calendar)
NEWS_KEYWORDS = ("downgrade", "cuts guidance", "lowers guidance", "profit warning", "halted", "trading halt", "SEC charges",
                 "fraud", "investigation", "bankruptcy", "chapter 11", "recall", "misses estimates", "delisting", "going concern",
                 "secondary offering", "share offering", "dilution")
NEWS_RE = re.compile(r"\b(" + "|".join(re.escape(k) for k in NEWS_KEYWORDS) + ")", re.I)
NEWS_WINDOW_H = 36
NEWS_RULE = ("news gate (equity only, build AND fire): reject if calendar earnings date is the session day or the next trading "
             "day, or a headline published in the last %dh matches (case-insensitive, word start): %s. A fetch error (or an "
             "empty news feed / empty calendar, which is how yfinance reports most failures) PASSES with flag news_unchecked."
             % (NEWS_WINDOW_H, ", ".join(NEWS_KEYWORDS)))

def fetch_news_cal(ticker):
    """Exactly one .news and one .calendar read per candidate. Errors are returned, never raised."""
    news = cal = None; errs = []
    try: news = ticker.news
    except Exception as e: errs.append("news: %s: %s" % (type(e).__name__, e))
    try: cal = ticker.calendar
    except Exception as e: errs.append("calendar: %s: %s" % (type(e).__name__, e))
    return news, cal, errs

def _news_item(item):
    """(title, pub_utc) for yfinance 1.5.x {'id','content':{title,pubDate..}} and the older flat format."""
    if not isinstance(item, dict): return None, None
    c = item.get("content") if isinstance(item.get("content"), dict) else item
    title = c.get("title") or ""; t = None
    pd = c.get("pubDate") or c.get("displayTime")
    try:
        if pd: t = dt.datetime.fromisoformat(str(pd).replace("Z", "+00:00"))
        elif item.get("providerPublishTime"): t = dt.datetime.fromtimestamp(float(item["providerPublishTime"]), UTC)
        if t is not None and t.tzinfo is None: t = t.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        t = None
    return title, t

def _earnings_dates(cal):
    if cal is None: return []
    v = cal.get("Earnings Date") if isinstance(cal, dict) else None
    if v is None and hasattr(cal, "loc"):                   # pre-0.2.3x DataFrame layout
        try: v = list(cal.loc["Earnings Date"])
        except Exception: v = None
    if v is None: return []
    out = []
    for d in (v if isinstance(v, (list, tuple)) else [v]):
        if isinstance(d, dt.datetime): out.append(d.date())
        elif isinstance(d, dt.date): out.append(d)
        elif hasattr(d, "date"): out.append(d.date())
        else:
            with contextlib.suppress(ValueError, TypeError): out.append(dt.date.fromisoformat(str(d)[:10]))
    return out

def next_trading_day(market, day):
    d = day + dt.timedelta(days=1)
    while not is_trading_day(market, d): d += dt.timedelta(days=1)
    return d

def news_gate(market, news, cal, errs, now, day):
    """-> (reasons, flags, record). `day` = the session's local date. Never raises; never blocks on a fetch error."""
    reasons, flags, rec = [], [], {}
    errs = list(errs or [])
    if not errs and not isinstance(news, list) or news == []:
        errs.append("news: empty feed (yfinance returns [] on most fetch failures)")
    if not errs and not cal:
        errs.append("calendar: empty (yfinance returns {} on auth/crumb failures; ETFs have none)")
    if errs:
        flags.append("news_unchecked"); rec["news_errors"] = errs
    try:
        hit = [d for d in _earnings_dates(cal) if d in (day, next_trading_day(market, day))]
        if hit:
            reasons.append("news gate: earnings within 1 trading day"); rec["earnings_date"] = hit[0].isoformat()
        cutoff = now - dt.timedelta(hours=NEWS_WINDOW_H)
        for item in news if isinstance(news, list) else []:
            title, t = _news_item(item)
            m = NEWS_RE.search(title) if (t is not None and t >= cutoff) else None
            if m:
                reasons.append("news gate: negative headline")
                rec.update(news_headline=title, news_keyword=m.group(1).lower(), news_pub_utc=t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
                break
    except Exception as e:                                   # malformed payload: pass, flagged
        if "news_unchecked" not in flags: flags.append("news_unchecked")
        rec.setdefault("news_errors", []).append("parse: %s" % e)
    return reasons, flags, rec

def fire_news(mk, c, now):
    import yfinance as yf
    day = now.astimezone(ZoneInfo(MARKETS[mk]["tz"])).date()
    try: news, cal, errs = fetch_news_cal(yf.Ticker(c["symbol"]))
    except Exception as e: news, cal, errs = None, None, ["ticker: %s" % e]
    return news_gate(mk, news, cal, errs, now, day)

def beta_score(mom20, atr, gap, adv_usd, cost_bp):
    a = max(atr or 0.0, 0.5)
    clamp = lambda x, lo, hi: max(lo, min(hi, x))
    return round(clamp((mom20 or 0.0) / a, -3, 3) + 0.5 * clamp((gap or 0.0) / a, -2, 2)
                 + 0.25 * math.log10(max(adv_usd or 0.0, 1e6) / 1e8) - (cost_bp or 0.0) / 50.0, 3)

def load_history(syms, before=None):
    """Daily bars via one yf.download call (API stable across yfinance 0.2.x..1.x). Drops bars dated >= `before`."""
    import yfinance as yf
    out = {}
    if not syms:
        return out
    try:
        df = yf.download(list(syms), period="6mo", interval="1d", auto_adjust=False, group_by="ticker", progress=False, threads=True)
    except Exception as e:
        print("history fail: %s" % e, file=sys.stderr); return out
    multi = hasattr(df.columns, "levels")
    for s in syms:
        try:
            if not multi: sub = df
            elif s in df.columns.get_level_values(0): sub = df[s]
            else: sub = df.xs(s, axis=1, level=1)
            rows = []
            for idx, r in sub.iterrows():
                vals = [fnum(r[k]) for k in ("Open", "High", "Low", "Close", "Volume")]
                d = idx.date() if hasattr(idx, "date") else idx
                if None in vals or vals[3] <= 0 or (before and d >= before): continue
                rows.append((d, *vals))
            out[s] = rows
        except Exception:
            continue
    return out

# ---------------------------------------------------------------- gate classification
# tfo_buy fails that need the regular-session print: deferred (re-checked by tfo_buy at fire) only while the quote
# is not a regular-session executable print. Everything else tfo_buy says is a hard reject.
DEFER_ON_OPEN = ("not a regular-session executable print", "US print not under 60s", "London auction/edge window",
                 "ASX opening auction", "Tokyo close auction window", "size > 5% of day dollar volume",
                 "down >3% on session", "up >5% without 2x 10-day volume")

def classify(fails):
    pre_open = any(f.startswith("not a regular-session executable print") for f in fails)
    if not pre_open:
        return list(fails), []
    hard = [f for f in fails if not f.startswith(DEFER_ON_OPEN)]
    return hard, [f for f in fails if f.startswith(DEFER_ON_OPEN)]

def tier_cap(sym, adv10_usd, spread_bp):
    tb = desk("tfo_buy")
    if venue_of(sym) != "US":
        return tb.BASE_CLIP_US                     # non-US <= $300k
    if adv10_usd and adv10_usd >= 2e9 and spread_bp is not None and spread_bp <= 5:
        return tb.CLIP_CAP_US                      # $600k only with 10d $vol >= $2B and spread <= 5bp
    return tb.BASE_CLIP_US

def vol_size(equity_usd, atr_pct, risk_pct=None):
    """-> (vol_size_usd, sizing_stop). sizing stop = max(5%, 2*ATR14/price); the rail stays at -5% (size shrinks instead)."""
    risk_pct = RISK_PCT if risk_pct is None else risk_pct
    stop = max(MANDATE_STOP, 2.0 * (atr_pct or 0.0) / 100.0)
    return risk_pct * float(equity_usd) / stop, stop

def shark_cap(equity_usd):
    """2026-10-02 HT call: NEW equity (shark) clips <= tfo_buy.shark_max_clip (0.5% firm risk to the -5% stop). Crypto is not capped here (C3 1%)."""
    try: return float(desk("tfo_buy").shark_max_clip(equity_usd))
    except Exception: return 0.005 * float(equity_usd) / MANDATE_STOP

def planned_loss_cap(mk, atr_pct):
    """2026-10-04 21:30 AEDT TFO Risk audit (Head Trader adopted; risk_caps.py): clip <= $5k / (actual stop + 0.5% slippage).
    Equity stop = the -5% rail; crypto stop = C3 2xATR14 (V0: -5%). The high-ATR crypto size-down is applied by
    tfo_crypto.py buy itself, not here, so it is never applied twice. None on any error (the tools still enforce the cap)."""
    try:
        sd = MANDATE_STOP
        if mk == "CRYPTO" and getattr(desk("tfo_crypto"), "CRYPTO_EXIT_VARIANT", None) == "C3" and fnum(atr_pct):
            sd = 2.0 * float(atr_pct) / 100.0
        return desk("risk_caps").planned_loss_cap_usd(sd)
    except Exception:
        return None

def size_fields(clip, vsize, stop, tier):
    return {"vol_size_usd": round(vsize, 2), "sizing_stop_pct": round(100 * stop, 2), "tier_cap_usd": tier,
            "risk_usd": round(clip * MANDATE_STOP, 2), "vol_risk_usd": round(clip * stop, 2)}

def legacy_held():
    """Names held in fundamental_book or convexity_sleeve (tfo_ticket.load, read-only). -> (set, sources)."""
    tb = desk("tfo_buy"); names, src = set(), {}
    for k in ("fundamental_book", "convexity_sleeve"):
        try: pos = (tb.load(k).get("positions") or {})
        except Exception as e: src[k] = "unreadable: %s" % type(e).__name__; continue
        src[k] = len(pos)
        for key, p in pos.items():
            names.add(str(key).upper())
            for f in ("symbol", "underlying", "ticker", "sym"):
                if isinstance(p, dict) and p.get(f): names.add(str(p[f]).upper())
    return names, src

def size_clip(sym, cap, liq_usd, firm, px_usd, px=None, ccy=None, extra_cap=None, vol_cap=None, loss_cap=None):
    lim = {"tier": cap, "5%_liquidity": liq_usd, "heat_headroom": firm["headroom_notional_usd"]}
    if extra_cap is not None: lim["crypto_stack"] = extra_cap
    if vol_cap is not None: lim["vol_0.25%_risk"] = vol_cap
    if loss_cap is not None: lim["planned_loss_$5k"] = loss_cap   # 2026-10-04 TFO Risk audit (HT adopted)
    pre = min(lim.values())
    fee, src = fee_est(sym, pre, px_usd, px, ccy)
    lim["powder_net_fees"] = firm["unreserved_powder_usd"] - fee
    binding = min(lim, key=lim.get)
    clip = floor_k(min(lim.values()))
    fee, src = fee_est(sym, clip, px_usd, px, ccy)
    return clip, binding, fee, src

def plan(cands, firm, crypto=False):
    """Cumulative plan in rank order: themes (existing + planned <= 2), heat headroom and powder shared."""
    room, powder = firm["headroom_notional_usd"], firm["unreserved_powder_usd"]
    th = dict(firm["themes_crypto"] if crypto else firm["themes_equity"])
    stack = firm["crypto_stack_room_usd"]
    occupied = {str(x).upper() for x in (firm.get("held_equity") or [])}
    planned = set()
    for c in cands:
        sym = str(c.get("symbol") or "").upper()
        twin_block = any(sym in twins and bool((twins - {sym}) & (occupied | planned)) for twins in CORRELATED_TWIN_GROUPS)
        ok = (not twin_block and th.get(c["theme"], 0) < 2 and c["clip_usd"] <= room and c["clip_usd"] + c["fees_est_usd"] <= powder and (not crypto or c["clip_usd"] <= stack))
        if twin_block: c["planning_note"] = "correlated twin already held/planned"
        c["planned"] = ok
        if ok:
            th[c["theme"]] = th.get(c["theme"], 0) + 1; room -= c["clip_usd"]; powder -= c["clip_usd"] + c["fees_est_usd"]
            if crypto: stack -= c["clip_usd"]
            planned.add(sym)
    return cands

# ---------------------------------------------------------------- builders
def resolve(market):
    m = market.upper(); m = ALIASES.get(m, m)
    if m not in MARKETS and m != "CRYPTO":
        raise SystemExit("unknown market %s (use %s)" % (market, " ".join(ORDER)))
    return m

def build_equity(market, now):
    o = todays_open(market, now)
    if o is None or now > o + dt.timedelta(hours=10):
        o = next_open(market, now)
    _vr = v1_venues.market_block_reason(market, o or now)
    if _vr:  # before any desk import / quote fetch: a removed market costs nothing
        return {"market": market, "status": "SKIPPED", "reason": _vr, "built_at": stamp(now), "candidates": [], "rejects": []}
    tb = desk("tfo_buy"); fb = desk("fundamental_book"); import yfinance as yf
    day = o.astimezone(ZoneInfo(MARKETS[market]["tz"])).date()
    venues = open_venues(market, day)
    uni = [(s, th) for s, th in universe(market) if venue_of(s) in venues
           and v1_venues.entry_block_reason(s, o) is None]   # e.g. .TO names leave the US list from 29 Sep
    skipped = sorted({venue_of(s) for s, _ in universe(market)} - set(venues))
    firm = firm_snapshot()
    syms = [s for s, _ in uni]
    with contextlib.redirect_stdout(sys.stderr):
        quotes, qfails = fb.fetch_prices(syms, require_executable=False)
    hist = load_history(syms, before=day)
    rows = {}
    for s, th in uni:
        q = quotes.get(s); bm = bar_metrics(hist.get(s, []))
        fx = (float(q["px_usd"]) / float(q["px"])) if q and fnum(q.get("px")) and fnum(q.get("px_usd")) else None
        adv_h = bm["adv10_native"] * fx if (bm["adv10_native"] and fx) else 0.0
        rows[s] = {"sym": s, "theme": th, "q": q, "bm": bm, "adv_hist_usd": adv_h}
    ranked = sorted(rows.values(), key=lambda r: -r["adv_hist_usd"])
    # 2026-09-28: funder-watch priority + bench names are ALWAYS deep-checked (never dropped by the $vol rank cut)
    must = funder_must_check(market)
    deep = ranked[:MAX_DEEP] + [r for r in ranked[MAX_DEEP:] if r["sym"] in must]
    shallow = [r for r in ranked[MAX_DEEP:] if r["sym"] not in must]
    cands, rejects, snaps, news_cache = [], [], {}, {}
    legacy, legacy_src = legacy_held()
    with readonly_guard(), equity_cache(quotes, qfails, snaps) as real_ticker:
        for r in deep:
            s, th, q, bm = r["sym"], r["theme"], r["q"], r["bm"]
            snap = snaps.setdefault(s, _Snap(s, real_ticker))
            fi = snap._fi or {}; inf = snap._inf or {}
            px_usd = fnum(q.get("px_usd")) if q else None
            avg10 = fnum(fi.get("tenDayAverageVolume"))
            adv10 = avg10 * px_usd if (avg10 and px_usd) else r["adv_hist_usd"]      # tfo_buy's own avg10 $vol formula
            bid, ask = fnum(inf.get("bid")) or 0.0, fnum(inf.get("ask")) or 0.0
            spread = (ask - bid) / ((ask + bid) / 2) * 1e4 if bid > 0 and ask > bid else (0.0 if bid > 0 and ask == bid else None)   # tfo_buy formula (locked quote = 0.0bp, 2026-09-28)
            pre, reg = fnum(inf.get("preMarketPrice")), fnum(inf.get("regularMarketPrice"))
            gap = 100 * (pre / reg - 1) if (pre and reg and venue_of(s) == "US" and now < o) else None
            reasons, why_size = [], None
            split_reject = split_entry_reject(market, s, day)
            if split_reject: reasons.append(split_reject)
            tier = tier_cap(s, adv10, spread); vsize, vstop = vol_size(firm.get("sizing_equity_usd", 0.0), bm["atr_pct"])
            vsize = min(vsize, shark_cap(firm.get("sizing_equity_usd", 0.0)))   # 2026-10-02: shark 0.5% risk cap
            if px_usd:
                clip, binding, fee, fsrc = size_clip(s, tier, 0.05 * (adv10 or 0), firm, px_usd, q.get("px"), q.get("ccy"), vol_cap=vsize,
                                                     loss_cap=planned_loss_cap(market, bm["atr_pct"]))
            else:
                clip, binding, fee, fsrc = 0.0, "no quote", 0.0, "none"
            if s.upper() in legacy:
                reasons.append("legacy book holds name")
            if px_usd and clip < MIN_CLIP_USD:
                reasons.append("sized clip $%.0f < $%.0f min (binding: %s)" % (clip, MIN_CLIP_USD, binding))
            gate_usd = clip if clip >= MIN_CLIP_USD else MIN_CLIP_USD
            try:
                fails, _, info = tb.gates(s, gate_usd, th)
            except LedgerWriteRefused:
                raise
            except Exception as e:
                fails, info = ["gate error: %s" % e], {}
            hard, deferred = classify(fails)
            if gap is not None and gap < -3 and "not a regular-session executable print" in " ".join(deferred):
                hard.append("pre-market gap %.1f%% < -3%% (session gate will fail at open)" % gap)
            reasons += hard
            closes = [b[4] for b in hist.get(s, [])]
            last = fnum(fi.get("lastPrice")) or (fnum(q.get("px")) if q else None)   # yfinance units, same as history
            if px_usd:
                reasons += trend_check(closes, last)
            flags, nrec = [], {}
            if px_usd and not reasons:                       # news only for would-be candidates: 1 news + 1 calendar read, cached
                if s not in news_cache: news_cache[s] = fetch_news_cal(real_ticker(s))
                nr, flags, nrec = news_gate(market, *news_cache[s], now, day)
                reasons += nr
            base = {"symbol": s, "venue": venue_of(s), "theme": th, "adv10_usd": round(adv10 or 0), "spread_bp": None if spread is None else round(spread, 1), **funder_watch_meta(market, s)}
            if reasons:
                rejects.append({**base, "gate_usd": gate_usd, "reasons": reasons, **nrec}); continue
            sell_fee, _ = fee_est(s, clip, px_usd, q.get("px"), q.get("ccy"), side="sell")
            cost_bp = 1e4 * (fee + sell_fee) / clip + (spread or 0)
            g = gap if gap is not None else bm["prior_day_pct"]
            score = beta_score(bm["mom20_pct"], bm["atr_pct"], g, adv10, cost_bp)
            tape = "LIVE" if venue_of(s) == "US" else "DELAYED"
            why = ("beta: 20d mom %s, gap %s, prior day %s, ATR %s, ADV10 $%.0fM, spread %sbp, rt cost %.0fbp; clip bound by %s"
                   % (pct(bm["mom20_pct"]), pct(gap), pct(bm["prior_day_pct"]), pct(bm["atr_pct"], sign=False), (adv10 or 0) / 1e6,
                      "?" if spread is None else "%.1f" % spread, cost_bp, binding))
            cands.append({**base, "score": score, "clip_usd": clip, **size_fields(clip, vsize, vstop, tier), "qty": round(clip / px_usd, 4), "ref_price": q.get("px"),
                          "ref_price_usd": round(px_usd, 6), "ccy": q.get("ccy"), "gap_pct": rnd(gap), "prior_day_pct": rnd(bm["prior_day_pct"]),
                          "mom20_pct": rnd(bm["mom20_pct"]), "atr_pct": rnd(bm["atr_pct"]), "fees_est_usd": round(fee, 2), "fee_model": fsrc,
                          "roundtrip_cost_bp": round(cost_bp, 1), "tape": tape, "label": "beta", "why": why, "deferred_to_open": deferred, "trend": "PASS", **trend_stats(closes, last), "flags": flags, **nrec,
                          "tfo_buy_info": info})
    for r in shallow:
        rejects.append({"symbol": r["sym"], "venue": venue_of(r["sym"]), "theme": r["theme"], "adv10_usd": round(r["adv_hist_usd"]),
                        "spread_bp": None, **funder_watch_meta(market, r["sym"]), "reasons": ["not deep-checked (10d $vol rank > %d)" % MAX_DEEP]})
    cands.sort(key=lambda c: -c["score"]); plan(cands, firm)
    rejects.sort(key=lambda x: -(x["adv10_usd"] or 0))
    return {"market": market, "status": "BUILT", "built_at": stamp(now), "open": {**stamp(o), "local": o.strftime("%Y-%m-%d %H:%M %Z")},
            "first_eligible": stamp(o + dt.timedelta(minutes=MARKETS[market]["fire_after"])), "minutes_to_open": round(minutes_to(o, now), 1),
            "tape": MARKETS[market]["tape"], "venues_open": venues, "venues_holiday": skipped, "universe_n": len(uni),
            "deep_checked_n": len(deep), "candidates": cands, "rejects_n": len(rejects), "rejects": top_rejects(rejects, market),
            "heat": {k: firm[k] for k in ("firm_equity_usd", "heat_at_stops_usd", "heat_cap_usd", "heat_headroom_usd", "headroom_notional_usd", "unreserved_powder_usd")},
            "freezes": firm["freezes"], "score_formula": SCORE_FORMULA, "trend_rule": TREND_RULE, "news_rule": NEWS_RULE, "vol_sizing": vol_rule(), "legacy_sources": legacy_src, "gate_source": "tfo_buy.gates (live module, cached quotes)", "funder_watch": {"setup": FUNDER_WATCH_SETUP, "priority": list(FUNDER_WATCH_PRIORITY.get(market, ())), "bench": list(FUNDER_WATCH_BENCH.get(market, ())), "split_rules": SPLIT_RULES}}

def build_crypto(now):
    tc = desk("tfo_crypto")
    if not os.path.exists(os.path.join(R, tc.KEY + ".json")):
        raise RuntimeError("reports/%s.json missing: watchlist never creates ledgers" % tc.KEY)
    firm = firm_snapshot()
    cands, rejects = [], []
    legacy, legacy_src = legacy_held()
    with readonly_guard(), kraken_cache(tc):
        pairs = tc.get("AssetPairs"); tick = tc.get("Ticker"); scan = []
        for k, v in pairs.items():
            if v.get("quote") not in ("ZUSD", "USD") or v.get("status", "online") != "online" or ".d" in k: continue
            t = tick.get(k); ws = v.get("wsname")
            if not t or not ws: continue
            bid, ask, opn = fnum(t["b"][0]), fnum(t["a"][0]), fnum(t["o"])
            if not bid or not ask or not opn: continue
            scan.append((float(t["v"][1]) * float(t["p"][1]), ws))
        scan.sort(reverse=True)
        held = firm["held_crypto"]
        uni = [ws for dv, ws in scan if ws not in tc.NOT_TRADES][:CRYPTO_N]
        uni += [p for p in held if p not in uni]
        for pair in uni:
            theme = "crypto-major" if pair.split("/")[0] in ("XBT", "BTC", "ETH", "XETH", "XXBT") else "crypto-alt"
            base = {"symbol": pair, "venue": "KRAKEN", "theme": theme}
            try:
                q = tc.quote(pair); a10 = tc.avg10_vol(pair)
                oh = [r for r in list(tc.get("OHLC", pair=tc.kpair(pair), interval=1440).values())[0] if isinstance(r, list)][:-1]
                bm = bar_metrics([(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[6])) for r in oh])
                closes = [float(r[4]) for r in oh]
                vsize, vstop = vol_size(firm.get("sizing_equity_usd", 0.0), bm["atr_pct"])
                clip, binding, fee, fsrc = size_clip(pair, tc.CLIP_CAP, 0.05 * q["dollar_vol24"], firm, q["ask"], extra_cap=firm["crypto_stack_room_usd"], vol_cap=vsize,
                                                     loss_cap=planned_loss_cap("CRYPTO", bm["atr_pct"]))
                reasons = ["legacy book holds name"] if pair.upper() in legacy else []
                if clip < MIN_CLIP_USD: reasons.append("sized clip $%.0f < $%.0f min (binding: %s)" % (clip, MIN_CLIP_USD, binding))
                fails, _, info = tc.gates(pair, clip if clip >= MIN_CLIP_USD else MIN_CLIP_USD, theme)
                c3max = info.get("c3_max_clip_usd")  # 2026-10-02 crypto C3: tfo_crypto caps risk at 1% firm to the 2xATR stop; size down, don't reject
                if c3max and clip > c3max and c3max - 1 >= MIN_CLIP_USD:
                    clip, binding = float(int(c3max) - 1), "C3 1% risk to 2xATR stop"
                    fails, _, info = tc.gates(pair, clip, theme)
            except LedgerWriteRefused:
                raise
            except Exception as e:
                rejects.append({**base, "adv10_usd": None, "spread_bp": None, "reasons": ["quote/gate error: %s" % e]}); continue
            reasons += fails + trend_check(closes, q["last"])
            base.update(adv10_usd=round(a10 * q["mid"]), spread_bp=None if q["spread_bp"] is None else round(q["spread_bp"], 1))
            if reasons:
                rejects.append({**base, "dollar_vol24": round(q["dollar_vol24"]), "reasons": reasons}); continue
            chg = 100 * (q["last"] / q["open_utc_day"] - 1) if q["open_utc_day"] else None
            cost_bp = 1e4 * 2 * fee / clip + (q["spread_bp"] or 0)
            score = beta_score(bm["mom20_pct"], bm["atr_pct"], chg, q["dollar_vol24"], cost_bp)
            why = ("beta: 20d mom %s, vs UTC open %s, ATR %s, 24h $vol $%.0fM (%sx 10d), spread %.1fbp, rt cost %.0fbp; clip bound by %s"
                   % (pct(bm["mom20_pct"]), pct(chg), pct(bm["atr_pct"], sign=False), q["dollar_vol24"] / 1e6, info.get("vol24_vs_10d"), q["spread_bp"], cost_bp, binding))
            cands.append({**base, "score": score, "clip_usd": clip, **size_fields(clip, vsize, vstop, tc.CLIP_CAP), "qty": round(clip / q["ask"], 6), "ref_price": q["ask"], "ref_price_usd": q["ask"],
                          "ccy": "USD", "gap_pct": rnd(chg), "prior_day_pct": rnd(bm["prior_day_pct"]), "mom20_pct": rnd(bm["mom20_pct"]),
                          "atr_pct": rnd(bm["atr_pct"]), "dollar_vol24": round(q["dollar_vol24"]), "fees_est_usd": round(fee, 2), "fee_model": fsrc,
                          "roundtrip_cost_bp": round(cost_bp, 1), "tape": q["tape"], "trade_age_sec": q["trade_age_sec"], "label": "beta", "why": why,
                          "deferred_to_open": [], "trend": "PASS", **trend_stats(closes, q["last"]), "tfo_crypto_info": info})
    cands.sort(key=lambda c: -c["score"]); plan(cands, firm, crypto=True)
    rejects.sort(key=lambda x: -(x.get("dollar_vol24") or 0))
    return {"market": "CRYPTO", "status": "BUILT", "built_at": stamp(now), "open": {"utc": None, "sydney": "24/7", "local": "24/7"},
            "tape": "LIVE (Kraken last trade <60s)", "universe_n": len(uni), "candidates": cands, "rejects_n": len(rejects), "rejects": rejects[:TOP_REJECTS],
            "heat": {k: firm[k] for k in ("firm_equity_usd", "heat_at_stops_usd", "heat_cap_usd", "heat_headroom_usd", "headroom_notional_usd", "unreserved_powder_usd", "crypto_stack_room_usd")},
            "freezes": firm["freezes"], "score_formula": SCORE_FORMULA, "trend_rule": TREND_RULE, "vol_sizing": vol_rule(), "legacy_sources": legacy_src, "gate_source": "tfo_crypto.gates (live module, cached Kraken reads)"}

def pct(x, sign=True):
    return "n/a" if x is None else (("%+.1f%%" if sign else "%.1f%%") % x)

def rnd(x, n=2):
    return None if x is None else round(x, n)

# ---------------------------------------------------------------- output (atomic)
def atomic_write_json(path, obj):
    d = os.path.dirname(path); os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix="." + os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, indent=1, default=str); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError): os.unlink(tmp)
        raise

@contextlib.contextmanager
def wl_lock(name=".ht_watchlist.lock", block=True):
    os.makedirs(R, exist_ok=True)
    fh = open(os.path.join(R, name), "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | (0 if block else fcntl.LOCK_NB))
    except OSError:
        fh.close(); raise
    try:
        yield
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN); fh.close()

def read_doc():
    try:
        with open(OUT) as f: return json.load(f)
    except (OSError, ValueError):
        return {}

def publish(entries, now):
    with wl_lock():
        doc = read_doc() or {}
        doc.setdefault("markets", {})
        for e in entries: doc["markets"][e["market"]] = e
        try: firm = firm_snapshot()
        except Exception as e: firm = {"error": str(e)}
        doc.update({"schema": VERSION, "paper_only": True, "never_tickets": True, "updated_at": stamp(now), "firm": firm,
                    "score_formula": SCORE_FORMULA})
        atomic_write_json(OUT, doc)
    return doc

def heartbeat(now, mode, built, errors):
    nxt = []
    for mk in MARKETS:
        o = next_open(mk, now)
        if o and v1_venues.market_block_reason(mk, o) is None: nxt.append({"market": mk, "open": stamp(o)["sydney"], "minutes": round(minutes_to(o, now))})
    nxt.sort(key=lambda x: x["minutes"])
    hb = {"at": stamp(now), "mode": mode, "version": VERSION, "pid": os.getpid(), "built": built, "errors": errors,
          "next_opens": nxt, "holiday_table_valid_through": HOLIDAYS_VALID_THROUGH.isoformat(),
          "holiday_table_expired": now.date() > HOLIDAYS_VALID_THROUGH, "paper_only": True}
    atomic_write_json(HB, hb)
    return hb

def build_market(mk, now):
    try:
        return build_crypto(now) if mk == "CRYPTO" else build_equity(mk, now)
    except LedgerWriteRefused:
        raise
    except Exception as e:
        return {"market": mk, "status": "ERROR", "error": "%s: %s" % (type(e).__name__, e), "built_at": stamp(now), "candidates": [], "rejects": []}

# ---------------------------------------------------------------- modes
def cmd_tick():
    now = now_utc(); due = markets_due(now)
    entries = [build_market(mk, now) for mk in due + ["CRYPTO"]]
    publish(entries, now)
    errors = {e["market"]: e["error"] for e in entries if e["status"] == "ERROR"}
    hb = heartbeat(now, "tick", [e["market"] for e in entries if e["status"] == "BUILT"], errors)
    summ = " ".join("%s=%d" % (e["market"], len(e["candidates"])) for e in entries if e["status"] == "BUILT")
    print("WL %s tick due=[%s] cand %s%s next %s" % (stamp(now)["sydney"], ",".join(due), summ or "-",
          (" ERR " + json.dumps(errors)) if errors else "", hb["next_opens"][0]["market"] + " " + hb["next_opens"][0]["open"] if hb["next_opens"] else "-"))
    try:  # audit 2026-09-26: tag direct (non-fire) HT fills; never blocks the build/heartbeat above
        for rec in reconcile_tags(now): print("TAGGED direct fill %s %s setup %s ticket %s" % (rec["symbol"], rec["ts_sydney"], rec["setup"], rec["ticket_id"]))
    except Exception as e:
        print("tag reconcile skipped: %s: %s" % (type(e).__name__, e))

def cmd_build(market):
    now = now_utc(); mk = resolve(market)
    _o = None if mk == "CRYPTO" else next_open(mk, now)
    _vr = v1_venues.market_block_reason(mk, _o or now)
    if _vr:
        print("%s: %s; nothing built" % (mk, _vr)); return
    e = build_market(mk, now); publish([e], now)
    heartbeat(now, "build %s" % mk, [mk] if e["status"] == "BUILT" else [], {mk: e["error"]} if e["status"] == "ERROR" else {})
    cmd_show(mk)

def cmd_show(market=None):
    doc = read_doc()
    if not doc: print("no reports/ht_watchlist.json yet (run: tfo_watchlist.py build MARKET)"); return
    firm = doc.get("firm", {})
    print("HT WATCHLIST (PAPER, beta, never tickets) updated %s | firm $%s heat %s/%s room $%s powder $%s %s" % (
        doc.get("updated_at", {}).get("sydney"), fmt(firm.get("firm_equity_usd")), fmt(firm.get("heat_at_stops_usd")), fmt(firm.get("heat_cap_usd")),
        fmt(firm.get("headroom_notional_usd")), fmt(firm.get("unreserved_powder_usd")), ",".join(firm.get("freezes") or [])))
    keys = [resolve(market)] if market else [k for k in ORDER if k in doc.get("markets", {})]
    for k in keys:
        m = doc.get("markets", {}).get(k)
        if not m: print("%-7s (not built)" % k); continue
        if m["status"] != "BUILT": print("%-7s %s %s" % (k, m["status"], m.get("error", ""))); continue
        print("%-7s built %s | open %s | tape %s | cand %d rej %d%s" % (k, m["built_at"]["sydney"], m["open"]["sydney"], m["tape"],
              len(m["candidates"]), m.get("rejects_n", 0), (" | holiday " + ",".join(m["venues_holiday"])) if m.get("venues_holiday") else ""))
        for i, c in enumerate(m["candidates"], 1):
            print("  %2d %-12s %+6.2f %s$%9s %12s @ %-10.6g %5sbp adv $%7.0fM gap %6s %s" % (
                i, c["symbol"], c["score"], "*" if c.get("planned") else " ", fmt(c["clip_usd"]), fmt(c["qty"], 2), c["ref_price"] or 0,
                "?" if c["spread_bp"] is None else c["spread_bp"], (c["adv10_usd"] or 0) / 1e6, pct(c["gap_pct"]), c["label"]))
        for x in m["rejects"][:5]:
            print("     x %-12s %s" % (x["symbol"], "; ".join(x["reasons"])[:110]))

def fmt(x, nd=0):
    return "?" if x is None else ("{:,.%df}" % nd).format(x)

# 17 Oct 2026 (Brett 14:27 item 1): CRYPTO watchlist entries rest as post-only MAKER buys (tfo_crypto --post-only). A RESTING
# order counts as the slot's fire (never retried, no alternate bought on top). If it expires unfilled the move is missed, and it
# is logged in the crypto book's resting_log. US/ASX entries via tfo_buy are unchanged (no resting model there).
POST_ONLY_CRYPTO = True

def _resting_crypto(sym):
    """True if the HT crypto book holds or has a resting post-only entry for `sym`."""
    try:
        b = desk("tfo_crypto").book()
    except Exception:
        return False
    return sym in (b.get("resting_entries") or {}) or sym in (b.get("positions") or {})

PENDING_SETUPS = os.path.join(R, "ht_watchlist_pending_setups.jsonl")

def note_pending_setup(mk, c, line, setup, now):
    """gate F6: a resting order's setup, keyed by its ticket id, so reconcile_tags attributes the later maker fill correctly."""
    tid = line.rsplit("ticket ", 1)[-1].strip() if "ticket " in line else None
    if not tid: return None
    rec = {"ticket_id": tid, "symbol": c["symbol"], "market": mk, "setup": setup, "theme": c.get("theme"), "label": c.get("label"),
           "utc": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "resting_line": line, "paper_only": True}
    with wl_lock(".ht_fills_tagged.lock"):
        with open(PENDING_SETUPS, "a") as f: f.write(json.dumps(rec) + "\n"); f.flush(); os.fsync(f.fileno())
    return rec

def pending_setups():
    out = {}
    try:
        with open(PENDING_SETUPS) as f:
            for ln in f:
                with contextlib.suppress(ValueError):
                    r = json.loads(ln); out[r["ticket_id"]] = r
    except OSError:
        pass
    return out

def run_tool(argv):
    p = subprocess.run(argv, cwd=BASE, capture_output=True, text=True, timeout=300)
    info, logs, resting = {}, [], []
    for ln in p.stdout.splitlines():
        if ln.startswith("{") and not info:
            with contextlib.suppress(ValueError): info = json.loads(ln)
        if ln.startswith("LOG ") and ln.strip() != "LOG none": logs.append(ln.strip())
        if ln.startswith("RESTING "): resting.append(ln.strip())
    return {"rc": p.returncode, "info": info, "fails": info.get("gate_fails", ["no gate output: %s" % (p.stderr.strip()[-200:])]),
            "passed": "PREVIEW PASS" in p.stdout or bool(logs) or bool(resting), "logs": logs, "resting": resting, "stdout": p.stdout}

def tool_argv(mk, c, usd, execute):
    if mk == "CRYPTO":
        a = [sys.executable, os.path.join(BASE, "tfo_crypto.py"), "buy", c["symbol"], "%.0f" % usd]
    else:
        a = [sys.executable, os.path.join(BASE, "tfo_buy.py"), c["symbol"], "%.0f" % usd]
    a += ["--theme", c["theme"], "--label", c.get("label", "beta"), "--why", "watchlist: " + c.get("why", "")[:300]]
    if mk == "CRYPTO" and POST_ONLY_CRYPTO: a.append("--post-only")
    return a + (["--execute"] if execute else [])

RESIZABLE = ("size > 5% of day dollar volume", "size > 5% of 24h dollar volume", "clip >$300k needs")

def resize(usd, fails, info):
    if not fails or not all(f.startswith(RESIZABLE) for f in fails):
        return None
    new = usd
    for f in fails:
        if f.startswith("clip >$300k needs"): new = min(new, 300000.0)
        elif f.startswith("size > 5% of day"): new = min(new, 0.049 * float(info.get("day_dollar_vol") or 0))
        elif f.startswith("size > 5% of 24h"): new = min(new, 0.049 * float(info.get("dollar_vol24") or 0))
    new = floor_k(new)
    return new if MIN_CLIP_USD <= new < usd else None

# ---------------------------------------------------------------- setup tags + report (own append-only file; never a ledger)
def parse_log(line):
    """tfo_buy/tfo_crypto: LOG {date} {time} BUY {sym} {qty} {px} {tape} {book} {principal} {proceeds} {pnl} [fees]"""
    t = line.split()
    try:
        d = {"syd": "%s %s" % (t[1], t[2]), "action": t[3], "symbol": t[4], "qty": float(t[5]), "px_usd": float(t[6]), "tape": t[7],
             "book": t[8], "principal_usd": float(t[9])}
        d["fees_usd"] = float(t[12]) if len(t) > 12 else None      # present only once the fee patch prints it
        return d
    except (IndexError, ValueError):
        return None

def ticket_for(sym, syd):
    """Ticket id from the facts line tfo_buy/tfo_crypto append next to the LOG line (read-only)."""
    try:
        with open(os.path.join(R, "tfo_head_trader_facts.jsonl")) as f:
            lines = f.readlines()[-200:]
    except OSError:
        return None
    for ln in reversed(lines):
        with contextlib.suppress(ValueError):
            j = json.loads(ln)
            if j.get("syd") == syd and str(j.get("fact", "")).startswith("BUY %s " % sym): return j.get("ticket")
    return None

def tag_fill(mk, c, line, setup, now):
    p = parse_log(line) or {}
    notional = p.get("principal_usd") or ((p.get("qty") or 0) * (p.get("px_usd") or 0)) or None
    rec = {"ts": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "ts_sydney": now.astimezone(SYD).strftime("%Y-%m-%d %H:%M %Z"),
           "market": mk, "symbol": p.get("symbol") or c["symbol"], "side": "long" if p.get("action", "BUY") == "BUY" else p.get("action"),
           "qty": p.get("qty"), "fill_px_usd": p.get("px_usd"), "notional_usd": notional,
           "risk_usd": round(notional * MANDATE_STOP, 2) if notional else None, "setup": setup, "theme": c.get("theme"),
           "label": c.get("label"), "book": p.get("book"), "tape": p.get("tape"), "fees_usd": p.get("fees_usd"),
           "ticket_id": ticket_for(p.get("symbol") or c["symbol"], p.get("syd")), "log_line": line, "paper_only": True}
    with wl_lock(".ht_fills_tagged.lock"):
        with open(TAGS, "a") as f:
            f.write(json.dumps(rec) + "\n"); f.flush(); os.fsync(f.fileno())
    return rec

def read_tags():
    out = []
    try:
        with open(TAGS) as f:
            for ln in f:
                with contextlib.suppress(ValueError): out.append(json.loads(ln))
    except OSError:
        pass
    return out

# audit 2026-09-26: fills that bypass `fire` (direct tfo_buy.py / tfo_crypto.py buy one-shots from the hunts) were never tagged.
# The 5-min tick now reconciles them from the LOG: read-only on LOG/facts/ledgers, append-only to TAGS, dedupe on the exact
# LOG line, and only lines >= RECONCILE_MIN_AGE_MIN old so a running `fire` always tags its own fills first. Never tickets.
DIRECT_SETUPS = {"CRYPTO": "ht_direct_crypto", "EQUITY": "ht_direct_equity"}
TAGS_SINCE_SYD = "2026-09-25 18:00"      # HT tickets began 2026-09-25 17:51 AEST (first HT BUY 18:05)
RECONCILE_MIN_AGE_MIN = 10
_SUFFIX_MARKET = {"AX": "ASX", "T": "TOKYO", "HK": "HK", "NS": "INDIA", "BO": "INDIA", "SA": "B3", "TO": "US", "V": "US"}

def _direct_market(sym):
    if "/" in sym: return "CRYPTO"
    if "." not in sym: return "US"
    return _SUFFIX_MARKET.get(sym.rsplit(".", 1)[1].upper(), "EUROPE")

def reconcile_tags(now=None, min_age_min=RECONCILE_MIN_AGE_MIN):
    now = now or now_utc(); out = []
    try:
        with open(os.path.join(R, "tfo_head_trader_log.txt")) as f: lines = [ln.strip() for ln in f if ln.strip()]
    except OSError:
        return out
    todo = []
    have = {x for t in read_tags() for x in (t.get("log_line"), t.get("superseded_log_line")) if x}   # 2026-09-28: a superseded (backdated) original is already tagged
    for ln in lines:
        p = parse_log(ln)
        if not p or p["action"] != "BUY" or ln in have or p["syd"] < TAGS_SINCE_SYD: continue
        try: t_syd = dt.datetime.strptime(p["syd"], "%Y-%m-%d %H:%M").replace(tzinfo=SYD)
        except ValueError: continue
        if (now - t_syd).total_seconds() < min_age_min * 60: continue
        todo.append((ln, p, t_syd))
    if not todo: return out
    books = {}; pending = pending_setups()
    for ln, p, t_syd in todo:
        sym = p["symbol"]; crypto = "/" in sym; bk = "tfo_ht_crypto" if crypto else (p.get("book") or "fundamental_book")
        if bk not in books:
            try:
                with open(os.path.join(R, bk + ".json")) as f: books[bk] = json.load(f)
            except (OSError, ValueError): books[bk] = {}
        tid = ticket_for(sym, p["syd"])
        pend = pending.get(tid) if tid else None   # gate F6: setup recorded when the post-only order rested
        tk = next((x for x in (books[bk].get("head_trader_tickets") or []) if tid and x.get("ticket_id") == tid), {}) if tid else {}
        ts = _utc(tk.get("utc")) or t_syd.astimezone(UTC)
        notional = p.get("principal_usd") or ((p.get("qty") or 0) * (p.get("px_usd") or 0)) or None
        mk = _direct_market(sym)
        out.append({"ts": ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "ts_sydney": ts.astimezone(SYD).strftime("%Y-%m-%d %H:%M %Z"),
                    "market": mk, "symbol": sym, "side": "long", "qty": p.get("qty"), "fill_px_usd": p.get("px_usd"), "notional_usd": notional,
                    "risk_usd": round(notional * MANDATE_STOP, 2) if notional else None,
                    "setup": (pend or {}).get("setup") or DIRECT_SETUPS["CRYPTO" if crypto else "EQUITY"], "theme": tk.get("theme"), "label": tk.get("label"),
                    "book": p.get("book"), "tape": p.get("tape"), "fees_usd": p.get("fees_usd"), "ticket_id": tid, "log_line": ln, "paper_only": True})
    with wl_lock(".ht_fills_tagged.lock"):
        have = {x for t in read_tags() for x in (t.get("log_line"), t.get("superseded_log_line")) if x}   # 2026-09-28: a superseded (backdated) original is already tagged
        out = [r for r in out if r["log_line"] not in have]
        if out:
            with open(TAGS, "a") as f:
                for r in out: f.write(json.dumps(r) + "\n")
                f.flush(); os.fsync(f.fileno())
    return out

def _utc(s):
    try: return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError): return None

def trade_pnl(tag, later_ts, books):
    """Gross P&L (realised exits after this entry + open mark), and net when every fee field exists."""
    crypto = "/" in tag["symbol"]; b = books.get("tfo_ht_crypto" if crypto else "fundamental_book") or {}
    key = "pair" if crypto else "sym"; t0 = _utc(tag["ts"]); tid = tag.get("ticket_id")
    realised, fees, fees_known, exits = 0.0, tag.get("fees_usd"), tag.get("fees_usd") is not None, 0
    # 2026-09-26 HT decision #5: pre-patch fills carry no fees_usd -> ESTIMATED fee via the same desk_costs model V2 uses
    # (reporting only; nothing is written back to any ledger).
    side0 = tag.get("side") or "long"
    # 2026-09-26 custodian: prefer BOOKED fees (13th LOG field, or V1_FEE_BACKFILL_20260926 tags on ledger fills); estimate only where nothing is booked
    booked_x, booked_e = _booked_fees(b, tag["symbol"], tid, t0)
    if not fees_known and booked_e is not None: fees, fees_known = booked_e, True
    fees_est, n_est = (float(fees), 0) if fees_known else (_est_fill_fee(tag["symbol"], "BUY", side0, tag.get("qty"), tag.get("fill_px_usd"), when=t0), 1)
    for tk in b.get("head_trader_tickets") or []:
        t = _utc(tk.get("utc"))
        if tk.get(key) != tag["symbol"] or tk.get("action") not in ("HALF", "FLAT", "STOP") or not t or not t0: continue
        if t < t0 - dt.timedelta(minutes=2) or (later_ts and t >= later_ts): continue
        realised += float(tk.get("pnl_usd") or 0); exits += 1
        fx = tk.get("fees_usd") if tk.get("fees_usd") is not None else booked_x.get(tk.get("ticket_id"))
        if fx is not None: tk = dict(tk, fees_usd=fx)
        if tk.get("fees_usd") is None:
            fees_known = False; n_est += 1
            fees_est += _est_fill_fee(tag["symbol"], tk["action"], tk.get("side") or side0, tk.get("qty"), tk.get("px_usd"),
                                      tk.get("px_native"), tk.get("ccy"), t)
        else:
            fees = (fees or 0) + float(tk["fees_usd"]); fees_est += float(tk["fees_usd"])
    pos = (b.get("positions") or {}).get(tag["symbol"]); unreal, status = 0.0, "closed" if exits else "unknown"
    if pos and (not tid or (pos.get("entry_ticket") or {}).get("ticket_id") in (tid, None)):
        status = "open"
        if crypto:
            mk = (b.get("mark_px_usd") or {}).get(tag["symbol"])
            unreal = pos["qty"] * (mk - pos["entry_px"]) if mk else None
        else:
            mk = pos.get("mark_usd")
            unreal = float(pos["units"]) * (float(mk) - float(pos["entry_px_usd"])) if mk else None
    est = {"fees_est": round(fees_est, 2), "fills_fee_estimated": n_est}
    if status == "unknown" or unreal is None:
        return dict(est, status=status, gross=None, net=None, net_est=None)
    gross = realised + unreal
    return dict(est, status=status, gross=round(gross, 2), net=round(gross - fees, 2) if fees_known else None,
                net_est=round(gross - fees_est, 2))

def _booked_fees(b, sym, tid, t0):
    """Booked exit fees by ticket_id and the booked entry fee for this tag, read from ledger positions/closed (open and closed)."""
    ex, entry = {}, None
    cl = b.get("closed") or {}
    recs = list((b.get("positions") or {}).items()) + (list(cl.items()) if isinstance(cl, dict) else [(c.get("pair") or c.get("symbol"), c.get("position") or c) for c in cl])
    for k, p in recs:
        if not isinstance(p, dict): continue
        for x in p.get("exit_fills") or []:
            if x.get("ticket_id") and x.get("fees_usd") is not None: ex[x["ticket_id"]] = float(x["fees_usd"])
        if str(k).split("#")[0] != sym: continue
        et = p.get("entry_ticket") or {}; f = p.get("entry_fees_usd", et.get("fees_usd"))
        if f is None: continue
        if tid and et.get("ticket_id") == tid: entry = float(f)
        elif entry is None:
            te = _utc(p.get("entry_utc"))
            if te and t0 and abs((te - t0).total_seconds()) < 600: entry = float(f)
    return ex, entry

def _est_fill_fee(sym, action, pos_side, qty, px_usd, px_native=None, ccy=None, when=None):
    """ESTIMATED fee for a fill that pre-dates the fee patch: desk_costs.fill_fees (V2's model); 0 if unpriceable."""
    try:
        if not qty or not px_usd: return 0.0
        dc = desk("desk_costs")
        return float(dc.fill_fees(sym, dc.side_for(action, pos_side), qty, px_usd, px_native, ccy, when)["fees_usd"])
    except Exception:
        return 0.0

def cmd_report(setup=None, as_json=False):
    tags = [t for t in read_tags() if not setup or t.get("setup") == setup]
    tb = desk("tfo_buy"); books = {}
    for k in ("fundamental_book", "tfo_ht_crypto"):
        with contextlib.suppress(Exception): books[k] = tb.load(k)
    rows = {}
    for i, t in enumerate(tags):
        later = [_utc(x["ts"]) for x in tags[i + 1:] if x["symbol"] == t["symbol"]]
        r = trade_pnl(t, later[0] if later else None, books)
        s = rows.setdefault(t.get("setup") or "untagged", {"trades": 0, "open": 0, "closed": 0, "priced": 0, "wins": 0,
                                                           "gross_usd": 0.0, "net_usd": 0.0, "net_complete": True, "notional_usd": 0.0,
                                                           "fees_est_usd": 0.0, "net_est_usd": 0.0, "fills_fee_estimated": 0})
        s["fees_est_usd"] += r.get("fees_est") or 0; s["fills_fee_estimated"] += r.get("fills_fee_estimated") or 0
        s["trades"] += 1; s["notional_usd"] += t.get("notional_usd") or 0
        s[r["status"]] = s.get(r["status"], 0) + 1
        if r["gross"] is not None:
            s["priced"] += 1; s["gross_usd"] += r["gross"]; s["wins"] += r["gross"] > 0; s["net_est_usd"] += r["net_est"]
            if r["net"] is None: s["net_complete"] = False
            else: s["net_usd"] += r["net"]
        else:
            s["net_complete"] = False
    out = {}
    for k, s in rows.items():
        n = s["priced"]
        out[k] = {"trades": s["trades"], "open": s["open"], "closed": s["closed"], "priced": n, "unknown": s.get("unknown", 0),
                  "win_rate": round(s["wins"] / n, 4) if n else None, "gross_pnl_usd": round(s["gross_usd"], 2) if n else None,
                  "net_pnl_usd": round(s["net_usd"], 2) if (n and s["net_complete"]) else None,
                  "mean_gross_per_trade_usd": round(s["gross_usd"] / n, 2) if n else None,
                  "mean_net_per_trade_usd": round(s["net_usd"] / n, 2) if (n and s["net_complete"]) else None,
                  "notional_usd": round(s["notional_usd"], 2),
                  "fees_est_usd": round(s["fees_est_usd"], 2), "fills_fee_estimated": s["fills_fee_estimated"],
                  "net_pnl_est_usd": round(s["net_est_usd"], 2) if n else None,
                  "fees_est_basis": "BOOKED fees (13th LOG field / V1_FEE_BACKFILL_20260926 ledger tags); desk_costs.fill_fees estimate only for fills with no booked cost (count in fills_fee_estimated)"}
    if as_json:
        print(json.dumps(out, indent=1))
    else:
        print("HT SETUP REPORT (PAPER; gross = realised exits + open marks; net null until every fill carries fees) %d tagged fills" % len(tags))
        print("%-28s %6s %5s %6s %8s %12s %12s %10s %11s %13s" % ("setup", "trades", "open", "win%", "priced", "gross$", "net$", "mean$", "fees$", "net$(+est)"))
        for k, v in sorted(out.items()):
            print("%-28s %6d %5d %6s %8d %12s %12s %10s %11s %13s" % (k, v["trades"], v["open"], "-" if v["win_rate"] is None else "%.0f" % (100 * v["win_rate"]),
                  v["priced"], fmt(v["gross_pnl_usd"], 2), "null" if v["net_pnl_usd"] is None else fmt(v["net_pnl_usd"], 2), fmt(v["mean_gross_per_trade_usd"], 2),
                  fmt(v["fees_est_usd"], 2), "null" if v["net_pnl_est_usd"] is None else fmt(v["net_pnl_est_usd"], 2)))
        n_est = sum(v["fills_fee_estimated"] for v in out.values())
        print("fees$ = BOOKED costs (13th LOG field and the V1_FEE_BACKFILL_20260926 ledger backfill); %d fill(s) with no booked cost are estimated with desk_costs.fill_fees and included in net$(+est)." % n_est)
    return out

# ---------------------------------------------------------------- staged-plan fire (Head Trader 2026-09-29 23:59 AEST: ONE fire list)
# 29 Sep US open: TFO Equity staged reports/preopen_watchlist_US_20260929.json (ISRG/TMO/SHEL/PG $135k) but fire read the
# auto ht_watchlist.json list and bought META $155k / AAPL $271k. Rule now: a staged plan for (market, local open date) is
# THE list. Primaries in rank order; an alternate only replaces a primary that FAILS a gate; each clip capped at the planned
# size (never up-sized); same tfo_buy gates. Retries are idempotent via a per-plan fire-state file. Auto list only when no plan.
FIRE_SOURCE_LOG = os.path.join(R, "fire_source_log.jsonl")

def staged_plan_path(mk, o):
    day = o.astimezone(ZoneInfo(MARKETS[mk]["tz"])).strftime("%Y%m%d")
    return os.path.join(R, "preopen_watchlist_%s_%s.json" % (mk, day))

def load_staged_plan(mk, o):
    """-> (plan_doc, path) when a staged plan exists for this market and local open date with primaries; else (None, path)."""
    p = staged_plan_path(mk, o)
    try:
        d = json.load(open(p))
    except (OSError, ValueError):
        return None, p
    day = o.astimezone(ZoneInfo(MARKETS[mk]["tz"])).date().isoformat()
    prim = ((d.get("plan") or {}).get("primaries")) or []
    if str(d.get("market", "")).upper() != mk or str(d.get("date_local")) != day or not prim or not d.get("candidates"):
        return None, p
    return d, p

def plan_queue(doc):
    """-> (primaries, alternates): candidate dicts in fire order (primaries in plan order; alternates by rank)."""
    cands = {str(c.get("symbol")): c for c in doc.get("candidates") or [] if c.get("symbol")}
    prim = [cands[s] for s in doc["plan"]["primaries"] if s in cands]
    ps = {c["symbol"] for c in prim}
    alts = sorted([c for c in cands.values() if c["symbol"] not in ps and str(c.get("role") or "alternate").lower().startswith("alternate")],
                  key=lambda c: (c.get("rank") or 999))
    return prim, alts

def _plan_c(c):
    return {"symbol": c["symbol"], "theme": c.get("theme") or "us-other", "label": c.get("label") or "beta",
            "why": "preopen plan #%s: %s" % (c.get("rank"), c.get("why") or ""), "setup": c.get("setup"),
            "ref_price_usd": fnum(c.get("ref_price")) if venue_of(c["symbol"]) == "US" else None}

def _fire_state_path(mk, o):
    return staged_plan_path(mk, o).replace("preopen_watchlist_", "preopen_fire_state_")

def _log_source(rec):
    try:
        with open(FIRE_SOURCE_LOG, "a") as f: f.write(json.dumps(rec) + "\n")
    except OSError:
        pass

def _held_today(sym, now):
    """True if fundamental_book holds `sym` from an entry dated today (Sydney) -> a prior run of this plan filled it."""
    try:
        p = (desk("tfo_buy").load("fundamental_book").get("positions") or {}).get(sym)
    except Exception:
        return False
    if not p or not p.get("entry_utc"): return False
    t = dt.datetime.fromisoformat(str(p["entry_utc"]).replace("Z", "+00:00"))
    return t.astimezone(SYD).date() == now.astimezone(SYD).date()

def _plan_attempt(mk, c, cap_usd, execute, runner, now, fill_setup):
    """One gate-checked attempt at <= cap_usd. -> (status, logs); status FILLED | PREVIEW_PASS | FAIL."""
    sym = c["symbol"]
    _cvr = v1_venues.entry_block_reason(sym, now)
    if _cvr: print("SKIP %s: %s" % (sym, _cvr)); return "FAIL", []
    legacy, _ = legacy_held()
    if sym.upper() in legacy: print("SKIP %s: legacy book holds name" % sym); return "FAIL", []
    tr, atr = fire_trend(mk, c, now)
    if tr: print("SKIP %s: %s" % (sym, "; ".join(tr))); return "FAIL", []
    firm = firm_snapshot()
    vsize, _ = vol_size(firm.get("sizing_equity_usd", 0.0), atr)
    if mk != "CRYPTO": vsize = min(vsize, shark_cap(firm.get("sizing_equity_usd", 0.0)))   # 2026-10-02: shark 0.5% risk cap
    fee, _src = fee_est(sym, cap_usd, c.get("ref_price_usd") or 0)
    usd = floor_k(min(cap_usd, vsize, firm["headroom_notional_usd"], firm["unreserved_powder_usd"] - fee, planned_loss_cap(mk, atr) or float("inf")))
    if usd < MIN_CLIP_USD: print("SKIP %s: plan cap/vol/heat/powder sizing leaves $%.0f" % (sym, usd)); return "FAIL", []
    nr, nflags, nrec = fire_news(mk, c, now)
    if nr: print("SKIP %s: %s" % (sym, "; ".join(nr))); return "FAIL", []
    if nflags: print("NOTE %s: %s (fetch failed or empty; not blocking)" % (sym, ",".join(nflags)))
    r = runner(tool_argv(mk, c, usd, False))
    if not r["passed"]:
        new = resize(usd, r["fails"], r["info"])          # resize only ever shrinks
        if new and new < usd: usd = new; r = runner(tool_argv(mk, c, usd, False))
    if not r["passed"]: print("SKIP %s $%.0f: %s" % (sym, usd, "; ".join(r["fails"]))); return "FAIL", []
    px = fnum(r["info"].get("px_usd"))
    if px and c.get("ref_price_usd") and not (1 / 2.5 <= px / c["ref_price_usd"] <= 2.5):
        print("SKIP %s: quote %.6g >2.5x off plan ref %.6g" % (sym, px, c["ref_price_usd"])); return "FAIL", []
    if not execute: print("PREVIEW PASS %s $%.0f (plan cap $%.0f) theme %s (%s)" % (sym, usd, cap_usd, c["theme"], c["label"])); return "PREVIEW_PASS", []
    r = runner(tool_argv(mk, c, usd, True))
    for ln in r["logs"]: tag_fill(mk, c, ln, fill_setup, now)
    if not r["logs"] and r.get("resting"):
        print("\n".join(r["resting"])); return "FILLED", list(r["resting"])   # resting post-only entry = this slot's fire
    if not r["logs"]: print("NOFILL %s: %s" % (sym, "; ".join(r["fails"]) or r["stdout"][-200:])); return "FAIL", []
    return "FILLED", r["logs"]

def fire_plan(mk, doc, path, o, execute, runner, now, setup=None):
    """Fire a staged plan. Idempotent across retries: a slot filled earlier (state file, or held from an entry today) is
    never re-bought and never triggers an alternate."""
    sp = _fire_state_path(mk, o)
    try: st = json.load(open(sp))
    except (OSError, ValueError): st = {"plan": os.path.basename(path), "market": mk, "slots": {}, "paper_only": True}
    prim, alts = plan_queue(doc)
    used = {v.get("filled_by") for v in st["slots"].values() if v.get("filled_by")}
    failed = set()                                         # alternates that failed a gate this run: not retried for later slots
    out = []
    for p in prim:
        slot = st["slots"].setdefault(p["symbol"], {})
        if slot.get("filled_by"):
            print("DONE %s slot filled earlier by %s" % (p["symbol"], slot["filled_by"])); continue
        if _held_today(p["symbol"], now):
            slot.update(filled_by=p["symbol"], note="held from an entry today"); used.add(p["symbol"])
            print("DONE %s already bought today; not re-bought, no alternate" % p["symbol"]); continue
        cap = fnum(p.get("planned_usd")) or 0.0
        if cap <= 0: print("SKIP %s: plan size 0" % p["symbol"]); continue
        tries = [p] + [a for a in alts if a["symbol"] not in used and a["symbol"] not in failed and not _held_today(a["symbol"], now)]
        for c0 in tries:
            c = _plan_c(c0)
            ccap = cap if c0 is p else min(cap, fnum(c0.get("alt_usd_if_substituted")) or cap)
            if c0 is not p: print("SUB %s -> alternate %s (cap $%.0f)" % (p["symbol"], c0["symbol"], ccap))
            status, logs = _plan_attempt(mk, c, ccap, execute, runner, now, setup or c.get("setup") or "preopen_hunt_momentum_equity")
            if status == "FILLED":
                out += logs; used.add(c0["symbol"]); slot.update(filled_by=c0["symbol"], utc=now.strftime("%Y-%m-%dT%H:%M:%SZ")); break
            if status == "PREVIEW_PASS":
                used.add(c0["symbol"]); break                  # preview: reserve the name so later slots don't double-count it
            if c0 is not p: failed.add(c0["symbol"])
        else:
            slot["last_fail_utc"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    if execute:
        st["updated_utc"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        tmp = sp + ".tmp"; json.dump(st, open(tmp, "w"), indent=1); os.replace(tmp, sp)
    print("\n".join(out) if out else "LOG none")
    return out

def cmd_fire(market, execute, top, runner=run_tool, setup=None):
    mk = resolve(market); now = now_utc(); out = []
    _vr = v1_venues.market_block_reason(mk, now)
    if _vr:  # removed market: exit before lock, ledger read or quote fetch
        print("%s: %s; LOG none" % (mk, _vr)); return []
    try:
        lk = wl_lock(".ht_watchlist_fire.lock", block=False); lk.__enter__()
    except OSError:
        print("another fire is running; LOG none"); return []
    try:
        doc = read_doc(); m = (doc.get("markets") or {}).get(mk)
        if mk == "CRYPTO":
            built = m and m.get("status") == "BUILT" and (now - dt.datetime.fromisoformat(m["built_at"]["utc"].replace("Z", "+00:00"))).total_seconds() < 900
            if not built:
                m = build_market("CRYPTO", now); publish([m], now)
        else:
            o = todays_open(mk, now)
            if o is None:
                print("%s closed today (weekend/holiday); LOG none" % mk); return []
            first = o + dt.timedelta(minutes=MARKETS[mk]["fire_after"])
            plan, _plan_path = load_staged_plan(mk, o)
            if plan:
                print("SOURCE %s: staged plan %s (primaries %s)" % (mk, os.path.relpath(_plan_path, BASE), ",".join(plan["plan"]["primaries"])))
                _log_source({"utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "market": mk, "source": "staged_plan", "path": os.path.relpath(_plan_path, BASE), "execute": bool(execute)})
                if execute and now < first:
                    print("%s: WAIT first eligible print %s (now %s); LOG none" % (mk, stamp(first)["sydney"], stamp(now)["sydney"])); return []
                if execute and now > o + dt.timedelta(minutes=FIRE_MAX_LATE_MIN):
                    print("%s: staged plan is stale (> %d min after open); LOG none" % (mk, FIRE_MAX_LATE_MIN)); return []
                return fire_plan(mk, plan, _plan_path, o, execute, runner, now, setup)
            if not m or m.get("status") != "BUILT" or m["open"]["utc"] != stamp(o)["utc"]:
                print("%s: no watchlist built for today's open %s (run build %s); LOG none" % (mk, stamp(o)["sydney"], mk)); return []
            if execute and now < first:
                print("%s: WAIT first eligible print %s (now %s); LOG none" % (mk, stamp(first)["sydney"], stamp(now)["sydney"])); return []
            built = dt.datetime.fromisoformat(m["built_at"]["utc"].replace("Z", "+00:00"))
            fresh_rebuild = built >= o and (now - built) <= dt.timedelta(minutes=FRESH_REBUILD_MAX_MIN)   # 2026-09-28: "rebuild first" now works
            if execute and now > o + dt.timedelta(minutes=FIRE_MAX_LATE_MIN) and not fresh_rebuild:
                print("%s: pre-open list is stale (> %d min after open); rebuild first; LOG none" % (mk, FIRE_MAX_LATE_MIN)); return []
            print("SOURCE %s: auto list reports/ht_watchlist.json (no staged plan at %s)" % (mk, os.path.relpath(_plan_path, BASE)))
            _log_source({"utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "market": mk, "source": "auto_ht_watchlist", "execute": bool(execute)})
        default_setup = setup or ("watchlist_momentum_crypto" if mk == "CRYPTO" else "watchlist_momentum_equity")
        if setup == "crypto_hunt" and mk != "CRYPTO":
            print("setup crypto_hunt is CRYPTO-only; LOG none"); return []
        for c in (m.get("candidates") or [])[:top]:
            _cvr = v1_venues.entry_block_reason(c["symbol"], now)
            if _cvr:
                print("SKIP %s: %s" % (c["symbol"], _cvr)); continue
            legacy, _ = legacy_held()
            if c["symbol"].upper() in legacy:
                print("SKIP %s: legacy book holds name" % c["symbol"]); continue
            if mk == "CRYPTO" and POST_ONLY_CRYPTO and _resting_crypto(c["symbol"]):   # gate F5: already resting/held = already fired
                print("DONE %s: post-only entry resting or held; not re-posted" % c["symbol"]); continue
            tr, atr = fire_trend(mk, c, now)
            if tr:
                print("SKIP %s: %s" % (c["symbol"], "; ".join(tr))); continue
            firm = firm_snapshot()
            vsize, vstop = vol_size(firm.get("sizing_equity_usd", 0.0), atr)
            if mk != "CRYPTO": vsize = min(vsize, shark_cap(firm.get("sizing_equity_usd", 0.0)))   # 2026-10-02: shark 0.5% risk cap
            cap = firm["headroom_notional_usd"]
            if mk == "CRYPTO": cap = min(cap, firm["crypto_stack_room_usd"])
            usd = floor_k(min(c["clip_usd"], vsize, cap, firm["unreserved_powder_usd"] - c["fees_est_usd"], planned_loss_cap(mk, atr) or float("inf")))   # 2026-10-04 TFO Risk cap
            if usd < MIN_CLIP_USD:
                print("SKIP %s: vol/heat/powder sizing leaves $%.0f" % (c["symbol"], usd)); continue
            if mk != "CRYPTO":
                nr, nflags, nrec = fire_news(mk, c, now)
                if nr:
                    print("SKIP %s: %s %s" % (c["symbol"], "; ".join(nr), json.dumps({k: v for k, v in nrec.items() if k != "news_errors"}))); continue
                if nflags: print("NOTE %s: %s (fetch failed or empty; not blocking)" % (c["symbol"], ",".join(nflags)))
            fill_setup = setup or c.get("setup") or default_setup
            r = runner(tool_argv(mk, c, usd, False))
            if not r["passed"]:
                new = resize(usd, r["fails"], r["info"])
                if new:
                    usd = new; r = runner(tool_argv(mk, c, usd, False))
            if not r["passed"]:
                print("SKIP %s $%.0f: %s" % (c["symbol"], usd, "; ".join(r["fails"]))); continue
            px = fnum(r["info"].get("px_usd") or r["info"].get("ask"))
            if px and c.get("ref_price_usd") and not (1 / 2.5 <= px / c["ref_price_usd"] <= 2.5):
                print("SKIP %s: quote %.6g >2.5x off watchlist ref %.6g" % (c["symbol"], px, c["ref_price_usd"])); continue
            if not execute:
                print("PREVIEW PASS %s $%.0f theme %s (%s)" % (c["symbol"], usd, c["theme"], c["label"])); continue
            r = runner(tool_argv(mk, c, usd, True))
            out += r["logs"] + ([] if r["logs"] else list(r.get("resting") or []))
            for ln in r["logs"]:
                tag_fill(mk, c, ln, fill_setup, now)
            if not r["logs"] and r.get("resting"):                # resting post-only entry: fired, not a NOFILL
                for ln in r["resting"]: note_pending_setup(mk, c, ln, fill_setup, now)
                continue
            if not r["logs"]: print("NOFILL %s: %s" % (c["symbol"], "; ".join(r["fails"]) or r["stdout"][-200:]))
        print("\n".join(out) if out else "LOG none")
        return out
    finally:
        lk.__exit__(None, None, None)

def main(argv=None):
    ap = argparse.ArgumentParser(description="TFO V1 pre-open watchlist (PAPER)")
    sp = ap.add_subparsers(dest="c", required=True)
    sp.add_parser("tick"); b = sp.add_parser("build"); b.add_argument("market")
    s = sp.add_parser("show"); s.add_argument("market", nargs="?")
    f = sp.add_parser("fire"); f.add_argument("market"); f.add_argument("--execute", action="store_true"); f.add_argument("--top", type=int, default=5)
    f.add_argument("--setup", choices=SETUPS, help="setup tag for ht_fills_tagged.jsonl (crypto_hunt: CRYPTO from the 2-hourly hunt)")
    rp = sp.add_parser("report"); rp.add_argument("--setup"); rp.add_argument("--json", action="store_true")
    sp.add_parser("reconcile", help="tag HT BUY fills that bypassed fire (direct tfo_buy/tfo_crypto one-shots); never tickets")
    a = ap.parse_args(argv)
    if a.c == "tick": cmd_tick()
    elif a.c == "build": cmd_build(a.market)
    elif a.c == "show": cmd_show(a.market)
    elif a.c == "report": cmd_report(a.setup, a.json)
    elif a.c == "reconcile":
        recs = reconcile_tags(); print("\n".join("TAGGED %s %s %s" % (r["symbol"], r["ts_sydney"], r["setup"]) for r in recs) or "reconcile: nothing untagged")
    else: cmd_fire(a.market, a.execute, a.top, setup=a.setup)

if __name__ == "__main__":
    main()
