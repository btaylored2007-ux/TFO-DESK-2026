"""TAYLOR HEDGE FUND engine (internal label vd-engine), v0 2026-10-30 (Brett 17:05 WITA orders). PAPER ONLY.
One daemon: feature store from EXISTING data (pit.sqlite Yahoo/IBKR bars, Kraken 24h sweep, news.json, factor rank, FRED/SEC in pit) ->
per-seat scorer (design weights + absolute floors + Marcus 17:10 rules) -> live top-10 JSON per seat -> debounced wake_queue -> additive
`desk run --seats=` (the sanctioned seat trigger; Marcus head_of_desk reviews every wake run) -> seat -> gate_v2 -> book.risk_plan.
HARD: never writes the book, rails or funder_queue. Engine grade only CAPS a seat grade (desk._engine_cap), never lifts.
Caps: <=6 wakes/seat/day, <=40/day total (WITA day, atomic, no A+ exemption). Stagger: <=2 seats per 5-min slot, released at :x2/:x7.
  python3 -m veteran_desk.vd_engine daemon | once | status | gpt2l <wake_id> | grok-note <wake_id> <json-file>"""
import datetime as dt, fcntl, json, math, os, re, subprocess, sys, threading, time, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from veteran_desk import pit as P
BASE = P.BASE; UTC = dt.timezone.utc; WITA = dt.timezone(dt.timedelta(hours=8))
ST = os.path.join(BASE, "state", "vd_engine"); VERSION = "thf-v0.2-par-20261031"
for d in ("top10", "grades", "wakes", "hb", "second_look/gpt", "second_look/grok", "runs"): os.makedirs(os.path.join(ST, d), exist_ok=True)
CAP_SEAT, CAP_TOTAL = 6, 40
# tfo_thfpar_20261031 (Brett pt2258u "fast, very fast"): review ALL eligible seats per round (was 2/slot, one serial run -> ~2h per seat).
# Seats inside one round run in parallel in desk.run (ThreadPool MAX_PARALLEL=4); model CLIs bounded by THF_LLM_CONCURRENCY (= Mac-wide TFO_CLI_GLOBAL_CAP, default 3, enforced in local_llm.ask); per-seat model timeout THF_SEAT_TIMEOUT_S.
SEATS_PER_SLOT = int(os.environ.get("THF_SEATS_PER_SLOT", "11"))
THF_LLM_CONCURRENCY = os.environ.get("THF_LLM_CONCURRENCY", os.environ.get("TFO_CLI_GLOBAL_CAP", "3")); THF_SEAT_TIMEOUT_S = os.environ.get("THF_SEAT_TIMEOUT_S", "180")
DEBOUNCE_H = dict(default=4.0, crypto=2.0); FEAT_EVERY_S, CYCLE_S = 900, 300
LLM_TIMEOUT_2L = 120   # tfo_fastllm5 (head trader 03:09 WITA): match VD 120s fast-path budget
def now(): return dt.datetime.now(UTC)
def wita(t=None): return (t or now()).astimezone(WITA)
def wj(p, o):
    os.makedirs(os.path.dirname(p), exist_ok=True); t = p + ".tmp%d" % os.getpid(); json.dump(o, open(t, "w"), indent=1, default=str); os.replace(t, p)
def rj(p, d=None):
    try: return json.load(open(p))
    except Exception: return d
def jl(p, o):
    with open(p, "a") as f: f.write(json.dumps(o, default=str) + "\n")
def log(msg):
    line = "%s %s" % (wita().strftime("%F %T WITA"), msg); print(line, flush=True)
    try: jl(os.path.join(ST, "engine_log.jsonl"), dict(wita=line[:24], msg=msg))
    except Exception: pass
# ---------------- seats: design §3 weights (P,V,N,F,X) ----------------
SEATS = {
 "crypto":               dict(name="Kai Navarro",      w=(.30, .25, .10, .00, .35)),
 "news_filings":         dict(name="Quinn Mercer",     w=(.15, .25, .45, .05, .10)),
 "essentials_utilities": dict(name="Nora Whitlock",    w=(.25, .10, .10, .40, .15)),
 "trend":                dict(name="Dex Romano · Trend", w=(.55, .15, .00, .10, .20)),
 "price_action":         dict(name="Dex Romano · Price Action", w=(.35, .30, .05, .00, .30)),
 "macro":                dict(name="Theo Ashby",       w=(.35, .05, .20, .00, .40)),
 "energy":               dict(name="Rex Calder",       w=(.30, .15, .15, .15, .25)),
 "tech_ai_infra":        dict(name="Nico Hart",        w=(.30, .20, .20, .15, .15)),
 "banks":                dict(name="Victor Crane",     w=(.20, .10, .15, .40, .15)),
 "fx_commodities":       dict(name="Lena Voss",        w=(.40, .05, .15, .05, .35)),
 "healthcare":           dict(name="Iris Mendel",      w=(.20, .15, .30, .25, .10)),
}
PILLARS = ("P", "V", "N", "F", "X")
# ---------------- pre-score filters (Marcus 17:10) ----------------
LEVERED = set("TQQQ SQQQ UPRO SPXU SPXL SPXS SOXL SOXS TNA TZA LABU LABD FAS FAZ TECL TECS UVXY SVXY VXX UVIX SVIX NUGT DUST JNUG JDST YINN YANG FNGU FNGD BOIL KOLD UCO SCO ERX ERY GUSH DRIP TMF TMV UDOW SDOW QLD QID SSO SDS NVDL NVDU TSLL TSLQ CONL MSTU MSTX ETHU BITX BITU SBIT TSLZ AMDL NAIL DPST WEBL WEBS CURE DFEN UTSL TBT UBT ROM REW USD SKF UYG DIG DUG BIB BIS UGL ZSL AGQ GLL".split())
PEGS = {"USDHKD=X", "HKD=X", "USDTRY=X", "TRY=X", "USDCNY=X", "CNY=X", "USDSAR=X", "SAR=X", "USDAED=X", "AED=X", "USDQAR=X", "USDBHD=X", "USDKWD=X", "USDARS=X", "USDEGP=X", "USDNGN=X", "EURDKK=X", "USDDKK=X"}
STABLE = ("USDT", "USDC", "DAI", "PYUSD", "USDG", "RLUSD", "USDD", "TUSD", "FDUSD", "EUR", "GBP", "AUD", "CAD", "CHF", "JPY", "USD", "USDE", "USDS", "USD1", "EURC", "EURT", "USDQ", "FRAX", "LUSD", "GUSD", "PAXG", "XAUT")
def pre_filter(sym, market, last_close, adv_aud, rows):
    """Returns reject reason or None. Applied BEFORE scoring."""
    s = sym.upper(); b = s.split(".")[0]
    if P.banned(s): return "banned (Indonesia)"
    if s in PEGS: return "managed peg"
    if market in ("US", "CA") and b in LEVERED: return "levered/inverse ETF"
    if re.search(r"(-P[A-Z]?$|\.PR[A-Z]?$|-PR[A-Z]?$|\^P)", s) or re.search(r"-P[A-Z]\.", s): return "preferred"
    if market == "US" and last_close is not None and last_close < 2.0: return "micro-cap / sub-US$2 price"
    if adv_aud is not None and adv_aud < 500_000: return "ADV under A$500k"
    if rows is not None and len(rows) >= 25:   # merger-arb pin: dead-flat tape
        c = [r[4] for r in rows[-21:]]
        rets = [abs(c[i] / c[i - 1] - 1) for i in range(1, len(c)) if c[i - 1]]
        if market not in ("FX", "CRYPTO", "FUT", "IDX") and rets and max(rets) < 0.004 and abs(c[-1] / c[0] - 1) < 0.01: return "merger-arb pin (flat tape)"
    return None
# ---------------- universe ----------------
OANDA_FX = ['EUR_USD','GBP_USD','USD_JPY','USD_CHF','AUD_USD','NZD_USD','USD_CAD','EUR_JPY','GBP_JPY','AUD_JPY','NZD_JPY','CAD_JPY','CHF_JPY','EUR_GBP',
 'EUR_CHF','EUR_AUD','EUR_CAD','EUR_NZD','GBP_AUD','GBP_CAD','GBP_CHF','GBP_NZD','AUD_NZD','AUD_CAD','AUD_CHF','NZD_CAD','NZD_CHF','CAD_CHF',
 'EUR_NOK','EUR_SEK','USD_NOK','USD_SEK','USD_MXN','USD_ZAR','USD_SGD','USD_HKD','USD_TRY','USD_CNH','USD_PLN','EUR_PLN','USD_CZK','EUR_CZK','USD_HUF',
 'EUR_HUF','USD_DKK','EUR_DKK','USD_THB','SGD_JPY','EUR_TRY','ZAR_JPY','TRY_JPY','HKD_JPY','GBP_SGD','EUR_SGD','AUD_SGD','NZD_SGD','CAD_SGD','CHF_HKD',
 'GBP_HKD','AUD_HKD','EUR_HKD','CAD_HKD','NZD_HKD','SGD_CHF','GBP_PLN','GBP_ZAR']
OANDA_CMDTY = {"XAU_USD": "GC=F", "XAG_USD": "SI=F", "XPT_USD": "PL=F", "XPD_USD": "PA=F", "XCU_USD": "HG=F", "WTICO_USD": "CL=F", "BCO_USD": "BZ=F", "NATGAS_USD": "NG=F"}
OANDA_PEG = {"USD_HKD", "USD_TRY", "USD_CNH", "EUR_TRY", "TRY_JPY", "HKD_JPY", "CHF_HKD", "GBP_HKD", "AUD_HKD", "EUR_HKD", "CAD_HKD", "NZD_HKD", "USD_DKK", "EUR_DKK"}
def oanda_yahoo(ins):
    if ins in OANDA_CMDTY: return OANDA_CMDTY[ins]
    a, b = ins.split("_"); return a + b + "=X"
MACRO_EXTRA = ["^GSPC", "^NDX", "^DJI", "^RUT", "^FTSE", "^GDAXI", "^FCHI", "^N225", "^HSI", "^AXJO", "^STOXX50E", "^TNX", "^FVX", "^TYX", "^IRX", "^VIX",
               "ES=F", "NQ=F", "YM=F", "RTY=F", "ZN=F", "ZB=F", "ZF=F", "ZT=F", "DX-Y.NYB"]
FUT_EXTRA = ["CL=F", "BZ=F", "NG=F", "GC=F", "SI=F", "HG=F", "PL=F", "PA=F", "ZW=F", "ZC=F", "ZS=F", "KC=F", "CC=F", "SB=F", "CT=F", "LE=F", "HE=F", "RB=F", "HO=F"]
def build_universe():
    """All names we have data for, per asset class, with the gate's A$500k floor. Returns (U, coverage_avail)."""
    from veteran_desk import scan_universe as SU
    J = SU.load() or {}; U = {}; avail = {}
    for x in J.get("names", []):
        m = x.get("market") or "?"; a = avail.setdefault(m, dict(available=0, cleared=0)); a["available"] += 1
        if x.get("liquid"): a["cleared"] += 1; U[x["sym"]] = dict(sym=x["sym"], market=m, cls="EQ", sector=x.get("sector"), adv_aud=x.get("adv20_aud"))
    K = rj(os.path.join(BASE, "state", "veteran_desk", "kraken_24h_all.json"), {}) or {}
    ps = K.get("pairs") or []; usd = [p for p in ps if str(p.get("alt", "")).endswith("USD")]
    a = avail.setdefault("CRYPTO (Kraken USD)", dict(available=len(usd), cleared=0))
    for p in usd:
        base = p["alt"][:-3]
        if any(base == s for s in STABLE) or not p.get("liquid") or (p.get("vol24h_aud") or 0) < 500_000: continue
        a["cleared"] += 1; U["KRAKEN:" + p["alt"]] = dict(sym="KRAKEN:" + p["alt"], market="CRYPTO", cls="CRYPTO", adv_aud=p.get("vol24h_aud"), k24=p)
    fx = [(i, oanda_yahoo(i)) for i in OANDA_FX]; a = avail.setdefault("FX (OANDA book)", dict(available=len(fx), cleared=0, pegs_excluded=0))
    for i, y in fx:
        if i in OANDA_PEG: a["pegs_excluded"] += 1; continue
        a["cleared"] += 1; U[y] = dict(sym=y, market="FX", cls="FX", oanda=i)
    a = avail.setdefault("Metals+energy (OANDA)", dict(available=len(OANDA_CMDTY), cleared=0))
    for i, y in OANDA_CMDTY.items(): a["cleared"] += 1; U[y] = dict(sym=y, market="FUT", cls="FUT", oanda=i)
    a = avail.setdefault("Futures+indices (Yahoo)", dict(available=0, cleared=0))
    for y in FUT_EXTRA + MACRO_EXTRA + list(P.UNIVERSE.get("macro", [])) + list(P.UNIVERSE.get("fx_cmdty", [])):
        if y in U: continue
        a["available"] += 1; a["cleared"] += 1; U[y] = dict(sym=y, market="IDX" if y.startswith("^") or y.startswith("DX") else ("FX" if y.endswith("=X") else "FUT"), cls="MACRO")
    for g in ("energy", "essentials", "banks", "healthcare", "tech"):   # seat hand lists (always scored)
        for y in P.UNIVERSE.get(g, []):
            U.setdefault(y, dict(sym=y, market=_mk(y), cls="EQ", sector=None, adv_aud=None, hand=g))
    return U, avail
def _mk(s):
    from veteran_desk import scan_universe as SU
    try: return SU.market_of(s)
    except Exception: return "?"
# ---------------- sessions (WITA) for 'never fire on a closed market' ----------------
SESS = {"US": (21.5, 28.0), "CA": (21.5, 28.0), "UK": (15.0, 23.5), "EU/UK": (15.0, 23.5), "NORDIC": (15.0, 23.5), "BME": (15.0, 23.5), "ASX": (7.0, 13.0),
        "JP": (8.0, 14.5), "KR": (8.0, 14.5), "TW": (9.0, 13.5), "HK": (9.5, 16.0), "SG": (9.0, 17.0), "IN": (11.75, 18.0), "CN": (9.5, 15.0), "NZ": (6.0, 12.75)}
TZ_SESS = {"US": ("America/New_York", 570, 960), "CA": ("America/Toronto", 570, 960), "UK": ("Europe/London", 480, 990), "EU/UK": ("Europe/London", 480, 990),
           "NORDIC": ("Europe/Stockholm", 540, 1050), "BME": ("Europe/Madrid", 540, 1050)}
def market_open(m, t=None, lead_h=0.25):
    t = wita(t); wd = t.weekday(); h = t.hour + t.minute / 60.0
    if m == "CRYPTO": return True
    if m in ("FX", "FUT", "IDX"): return not (wd == 5 and h >= 5) and not wd == 6 and not (wd == 0 and h < 5)
    if m in TZ_SESS:   # tfo_tsxsess_20261030: DST-aware local sessions (BNS.TO/TSX, US, UK/EU) - fixed WITA hours drift by 1h at each DST change
        from zoneinfo import ZoneInfo
        tz, a, b = TZ_SESS[m]; lt = t.astimezone(ZoneInfo(tz)); lm = lt.hour * 60 + lt.minute
        return lt.weekday() < 5 and a - lead_h * 60 <= lm <= b
    lo, hi = SESS.get(m, (None, None))
    if lo is None: return False
    for hh, day in ((h, wd), (h + 24, (wd - 1) % 7)):   # US session crosses WITA midnight
        if day < 5 and lo - lead_h <= hh <= hi: return True
    return False
# ---------------- features (completed daily bars, PIT) ----------------
def _completed(rows, m, t):
    """Drop a bar that may still be forming: today's (venue-local ~ WITA date for Asia, UTC date for crypto/US-EU)."""
    if not rows: return rows
    last = rows[-1][0]
    today = t.astimezone(UTC).date().isoformat() if m in ("CRYPTO", "US", "CA", "UK", "EU/UK", "NORDIC", "BME", "FX", "FUT", "IDX") else wita(t).date().isoformat()
    if last >= today: return rows[:-1]
    return rows
def ema(x, n):
    k = 2 / (n + 1); e = x[0]
    for v in x[1:]: e = v * k + e * (1 - k)
    return e
def feats(c, u, t, bench):
    sym, m = u["sym"], u["market"]
    rows = _completed(P.bars_asof(c, sym, t, 300), m, t)
    if len(rows) < 60: return None, "under 60 completed bars"
    O = [r[1] or r[4] for r in rows]; H = [r[2] or r[4] for r in rows]; L = [r[3] or r[4] for r in rows]; C = [r[4] for r in rows]; V = [r[5] or 0 for r in rows]
    if not C[-1] or any(x is None for x in C[-60:]): return None, "bad closes"
    age_d = (t.date() - dt.date.fromisoformat(rows[-1][0])).days
    fx = 1.0
    sub = 100.0 if (sym.endswith(".L") or sym.endswith(".JO") or sym.endswith(".TA")) else 1.0   # LSE pence: traded value /100
    tr = [max(H[i] - L[i], abs(H[i] - C[i - 1]), abs(L[i] - C[i - 1])) for i in range(1, len(C))]; atr = sum(tr[-14:]) / 14 or 1e-9
    s20, s50 = sum(C[-20:]) / 20, sum(C[-50:]) / 50; s200 = sum(C[-200:]) / 200 if len(C) >= 200 else None; e20 = ema(C[-60:], 20)
    r = lambda k: (C[-1] / C[-1 - k] - 1) if len(C) > k and C[-1 - k] else 0.0
    vol20 = (sum((C[i] / C[i - 1] - 1) ** 2 for i in range(-20, 0)) / 20) ** .5 or 1e-9
    er = abs(C[-1] - C[-21]) / (sum(abs(C[i] - C[i - 1]) for i in range(-20, 0)) or 1e-9)
    up = (C[-1] > s50) + (s50 > (s200 or s50)) + (r(63) > 0); dn = (C[-1] < s50) + (s50 < (s200 or s50)) + (r(63) < 0)
    d = 1 if up >= dn else -1
    has_vol = sum(1 for v in V[-20:] if v) >= 15
    v20 = sum(V[-21:-1]) / 20 if has_vol else 0; med20 = sorted(V[-21:-1])[10] if has_vol else 0
    rvol = (V[-1] / med20) if med20 else None; rvol5 = (sum(V[-5:]) / 5 / v20) if v20 else None
    rng = (H[-1] - L[-1]) or 1e-9; cloc = (C[-1] - L[-1]) / rng   # close location in bar
    hi10, lo10, hi20p, lo20p = max(H[-10:]), min(L[-10:]), max(H[-21:-1]), min(L[-21:-1]); hi252, lo252 = max(H[-252:]), min(L[-252:])
    # structure stop (not spike lows): 2nd-lowest low / 2nd-highest high of last 10 bars, 0.25 ATR beyond, >= 1 ATR from close
    if d > 0: stop = min(sorted(L[-10:])[1] - .25 * atr, C[-1] - atr)
    else: stop = max(sorted(H[-10:], reverse=True)[1] + .25 * atr, C[-1] + atr)
    k_atr = abs(C[-1] - stop) / atr
    ext_atr = (C[-1] - s20) / atr; spike = (H[-1] - L[-1]) > 3 * atr and (rvol or 0) > 3
    stab = (C[-1] > C[-2] and L[-1] >= L[-2] * 0.999) if d > 0 else (C[-1] < C[-2] and H[-1] <= H[-2] * 1.001)   # stabilisation bar
    inside = H[-1] <= H[-2] and L[-1] >= L[-2]; nr7 = rng <= min(H[-i] - L[-i] for i in range(1, 8))
    adv_aud = u.get("adv_aud")
    if adv_aud is None and has_vol:
        try:
            from veteran_desk import book as BK; adv_aud = BK.aud(sym, v20 * C[-1] / sub, bench.get("_au", 0.65))
        except Exception: adv_aud = None
    rs = lambda b, k: (r(k) - bench[b][k]) if b in bench and k in bench[b] else None
    f = dict(sym=sym, market=m, cls=u["cls"], sector=u.get("sector"), last_date=rows[-1][0], bar_age_d=age_d, close=round(C[-1], 6), atr=round(atr, 6), atr_pct=round(atr / C[-1], 4),
             dir=d, ret1=round(r(1), 4), ret5=round(r(5), 4), ret20=round(r(20), 4), ret60=round(r(60), 4), mom12_1=round((C[-22] / C[-253] - 1) / (vol20 * 15.9), 3) if len(C) > 253 and C[-253] else None,
             mom6_1=round((C[-22] / C[-127] - 1) / (vol20 * 15.9), 3) if len(C) > 127 and C[-127] else None, er20=round(er, 3), stack=max(up, dn), s20=s20, s50=s50, s200=s200, e20=e20,
             above200=(C[-1] > s200) if s200 else None, rvol=round(rvol, 2) if rvol else None, rvol5=round(rvol5, 2) if rvol5 else None, has_vol=has_vol, cloc=round(cloc, 2), ext_atr=round(ext_atr, 2),
             spike=spike, stab=stab, inside=inside, nr7=nr7, hi10=hi10, lo10=lo10, hi20p=hi20p, lo20p=lo20p, hi252=hi252, lo252=lo252, stop=round(stop, 6), k_atr=round(k_atr, 2), adv_aud=adv_aud,
             rs_btc7=rs("KRAKEN:XBTUSD", 7) if m == "CRYPTO" else None, rs_btc30=rs("KRAKEN:XBTUSD", 30) if m == "CRYPTO" else None,
             rs_smh20=rs("SMH", 20), rs_kre20=rs("KRE", 20), rs_xle20=rs("XLE", 20), rs_spy20=rs("SPY", 20),
             gap=(O[-1] / C[-2] - 1) if C[-2] else 0.0, gap_hold=(C[-1] >= O[-1]) if O[-1] > C[-2] else (C[-1] <= O[-1]),
             prev_close=C[-2], prev_high=H[-2], prev_low=L[-2])
    if m == "CRYPTO" and u.get("k24"): f["chg_since_open"] = u["k24"].get("chg_pct_since_open")
    f["_rows"] = rows
    return f, None
# ---------------- seat signals (Marcus 17:10) ----------------
MR = {"ema20_pullback", "sma20_atr_pullback", "atr_stretch_stab", "gap_fill", "quality_pullback"}
def signals(seat, f, ctx):
    d = f["dir"]; S = {}; C = f["close"]; A = f["atr"]
    trend_up = f["stack"] >= 2
    if seat == "crypto":
        S["ema20_pullback"] = d > 0 and trend_up and abs(C - f["e20"]) <= 0.75 * A and f["stab"]
        S["vol_breakout"] = (f["rvol"] or 0) >= 2 and ((d > 0 and C > f["hi20p"]) or (d < 0 and C < f["lo20p"]))   # daily-bar proxy (no 1h bars in pit)
        S["rs_vs_btc"] = (f["rs_btc7"] or 0) * d > 0.02 and (f["rs_btc30"] or 0) * d > 0.05
    elif seat == "news_filings":
        nh = ctx["news"].get(f["sym"], 0)
        S["filing_or_news_surprise"] = nh > 0 and abs(f["ret1"]) > 1.5 * f["atr_pct"]
        S["post_event_gap_hold"] = nh > 0 and abs(f["gap"]) > f["atr_pct"] and f["gap_hold"] and (f["rvol"] or 0) > 2
        S["catalyst_base_breakout"] = nh > 0 and ((d > 0 and f["hi20p"] < C <= f["hi20p"] * 1.01) or (d < 0 and f["lo20p"] * .99 <= C < f["lo20p"]))
    elif seat == "healthcare":
        S["sma20_atr_pullback"] = trend_up and 0 <= d * (C - f["s20"]) / A <= 1.0 and f["stab"]
        S["failed_rally_short"] = d < 0 and f["prev_high"] >= f["s20"] and C < f["s20"] and C < f["prev_low"]
        S["gap_fill"] = abs(f["gap"]) > f["atr_pct"] and not f["gap_hold"] and f["stab"]
    elif seat == "tech_ai_infra":
        S["rs_vs_smh"] = (f["rs_smh20"] or 0) * d > 0.03 and ((d > 0 and C > f["s20"] > f["s50"]) or (d < 0 and C < f["s20"] < f["s50"])) and d * ((f["hi10"] if d > 0 else f["lo10"]) - C) / A >= -1e9 and abs(C - (f["hi10"] if d > 0 else f["lo10"])) / A >= 1.0
    elif seat == "banks":
        S["failed_bounce"] = (d < 0 and f["prev_high"] >= f["hi10"] * .99 and C < f["prev_low"]) or (d > 0 and f["prev_low"] <= f["lo10"] * 1.01 and C > f["prev_high"])
        S["credit_rs_vs_kre"] = (f["rs_kre20"] or 0) * d > 0.03
    elif seat == "essentials_utilities":
        S["quality_pullback"] = bool(f["above200"]) and d > 0 and abs(C - f["s20"]) <= 0.75 * A and f["lo10"] > f["lo20p"] * 0.995 and f["stab"]
        S["defensive_rotation"] = ctx["tnx_chg20"] is not None and ((ctx["tnx_chg20"] < 0 and d > 0) or (ctx["tnx_chg20"] > 0 and d < 0)) and trend_up
    elif seat == "energy":
        S["breakout_20d_52w"] = ((d > 0 and (C > f["hi20p"] or C >= f["hi252"] * .995)) or (d < 0 and (C < f["lo20p"] or C <= f["lo252"] * 1.005))) and (f["rvol"] or 0) >= 1.3
        S["xle_crude_corr_mom"] = (f["rs_xle20"] or 0) * d > -0.02 and ctx["crude_mom"] * d > 0
    elif seat == "macro":
        S["ratediff_momentum"] = f["market"] == "FX" and ctx["tnx_chg20"] is not None and trend_up and f["er20"] >= 0.3
        S["carry_atr_breakout"] = (d > 0 and C > f["hi20p"] + .25 * A) or (d < 0 and C < f["lo20p"] - .25 * A)
    elif seat == "fx_commodities":
        S["atr_stretch_stab"] = abs(f["ext_atr"]) >= 2.0 and f["stab"] and (f["ext_atr"] * d < 0)
        S["close_break_hold"] = (d > 0 and C > f["hi20p"] and f["prev_close"] > f["hi20p"] * .995) or (d < 0 and C < f["lo20p"] and f["prev_close"] < f["lo20p"] * 1.005)
        S["fx_carry_momentum"] = f["market"] == "FX" and f["er20"] >= 0.3 and f["stack"] == 3
    elif seat == "trend":
        S["mom_12_1_6_1"] = (f["mom12_1"] or 0) * d > 0.8 and (f["mom6_1"] or 0) * d > 0.5
        S["sma_50_200_stack"] = f["stack"] == 3
    elif seat == "price_action":
        S["rvol_1_5x"] = (f["rvol"] or 0) >= 1.5
        S["close_extreme_25"] = (d > 0 and f["cloc"] >= .75) or (d < 0 and f["cloc"] <= .25)
        S["stop_1atr_structure"] = f["k_atr"] >= 1.0
    for k in list(S):
        if k in MR and S[k] and not f["stab"]: S[k] = False   # mean reversion needs a stabilisation bar
    return {k: bool(v) for k, v in S.items()}
SIG_NEED = {"price_action": 3, "trend": 2}   # all PA conditions; trend needs both
def floors(seat, f, sig, ctx):
    """Absolute floors (GPT fix #2). Returns list of failed floors."""
    bad = []
    need = SIG_NEED.get(seat, 1)
    if sum(sig.values()) < need: bad.append("no seat signal (%d/%d)" % (sum(sig.values()), need))
    if seat == "trend" and f["er20"] < 0.3: bad.append("ER<0.3")
    if seat in ("price_action", "news_filings") and f["has_vol"] and (f["rvol"] or 0) < 1.5: bad.append("RVOL<1.5")
    if seat == "news_filings" and not ctx["news"].get(f["sym"]): bad.append("no fresh news/filing")
    if f["cls"] == "CRYPTO" and f["spike"]: bad.append("one-candle spike")
    return bad
def rfit(f):
    m = 1.0; notes = []
    ap = f["atr_pct"]
    if ap > 0.12: m *= .5; notes.append("ATR%>12")
    elif ap > 0.08: m *= .75; notes.append("ATR%>8")
    if abs(f["ext_atr"]) > 3: m *= .7; notes.append("extended >3ATR from 20d")
    elif abs(f["ext_atr"]) > 2: m *= .85; notes.append("extended >2ATR from 20d")
    if f["ret20"] * f["dir"] >= .10: m *= .85; notes.append("1-month run >=10%")
    if abs(f["ret1"]) >= .10: m *= .6; notes.append("24h move >=10%")
    if f["spike"]: m *= .7; notes.append("thin spike candle")
    if f["bar_age_d"] > (2 if f["cls"] == "CRYPTO" else 4): m *= .5; notes.append("stale bar %dd" % f["bar_age_d"])
    return round(max(.3, m), 3), notes
def raw_pillars(seat, f, ctx):
    d = f["dir"]
    Pp = 0.4 * (f["ret60"] * d) / max(f["atr_pct"], 1e-4) / 10 + 0.3 * f["er20"] * 3 + 0.3 * f["stack"] + 0.2 * (1 - (abs((f["hi252"] if d > 0 else f["lo252"]) - f["close"]) / f["close"]) * 5)
    if seat == "trend": Pp += (f["mom12_1"] or 0) * d * .5 + (f["mom6_1"] or 0) * d * .5
    Vv = ((f["rvol"] or 1) - 1) + ((f["rvol5"] or 1) - 1) + (abs(f["cloc"] - .5) * 2 if (f["cloc"] - .5) * d > 0 else 0) if f["has_vol"] else 0.0
    if f["cls"] == "CRYPTO" and f.get("chg_since_open") is not None: Vv += max(-1, min(1, d * f["chg_since_open"] / 5))
    Nn = math.log1p(ctx["news"].get(f["sym"], 0))
    Ff = ctx["factor"].get(f["sym"], 0.0)
    Xx = 0.0
    sig = signals(seat, f, ctx); Xx += sum(sig.values()) + (0.25 if f["nr7"] or f["inside"] else 0)
    return dict(P=Pp, V=Vv, N=Nn, F=Ff, X=Xx), sig
def zs(vals):
    xs = [v for v in vals if v is not None and math.isfinite(v)]
    if len(xs) < 3: return [0.0 for _ in vals]
    mu = sum(xs) / len(xs); sd = (sum((x - mu) ** 2 for x in xs) / len(xs)) ** .5 or 1.0
    return [max(-3, min(3, ((v if v is not None and math.isfinite(v) else mu) - mu) / sd)) for v in vals]
def grade(score, nz, R, fl):
    if fl: return "B" if score >= 70 else "C"
    if score >= 92 and nz >= 3 and R >= 0.92: return "A+"
    if score >= 85 and nz >= 2 and R >= 0.85: return "A"
    if score >= 70: return "B"
    return "C"
def seat_universe(seat, U, F):
    from veteran_desk import scan_universe as SU
    eq = [s for s, u in U.items() if u["cls"] == "EQ" and s in F]
    if seat == "crypto": return [s for s in F if U[s]["cls"] == "CRYPTO"]
    if seat in ("price_action", "news_filings"): return eq
    if seat == "trend": return [s for s in F if U[s]["cls"] in ("EQ", "CRYPTO", "FX", "FUT", "MACRO")]
    if seat == "macro": return [s for s in F if U[s]["cls"] in ("MACRO", "FX") or U[s]["market"] in ("IDX",) or s in P.UNIVERSE.get("macro", [])]
    if seat == "fx_commodities": return [s for s in F if U[s]["cls"] in ("FX", "FUT") or s in P.UNIVERSE.get("fx_cmdty", [])]
    want = SU.SEAT_SECTORS.get(seat) or set()
    hand = {"energy": "energy", "essentials_utilities": "essentials", "banks": "banks", "healthcare": "healthcare", "tech_ai_infra": "tech"}.get(seat)
    out = [s for s in eq if (U[s].get("sector") in want) or U[s].get("hand") == hand]
    if seat == "energy": out += [s for s in ("CL=F", "BZ=F", "NG=F", "RB=F", "HO=F") if s in F]
    return sorted(set(out))
# ---------------- pre-score history (gate's own outcomes) + natural size ----------------
def history_check(seat, f):
    try:
        from veteran_desk import gate_v2 as G, cost_tables as CT
        import numpy as np
        A = G.arrays(f["_rows"]); T = G.TH; side = "LONG" if f["dir"] > 0 else "SHORT"
        k = max(f["k_atr"], T["stop_min_atr"]); tgt_R = 2.0
        Rr = G.outcomes(A, side, k, tgt_R, T["horizon"], CT.est_rt_rate(f["sym"], P.venue_of(f["sym"]), float(A["c"][-1])))
        st = A["trend"]; cur = st[-1]; sel = np.isfinite(Rr) & (st == cur); sel[-T["horizon"]:] = False
        nb = int(sel.sum()); exp = float(np.mean(Rr[sel])) if nb else None
        nmin = 100 if f["cls"] == "CRYPTO" else T["base_n_min"]
        mult = 1.0; why = []
        if k < T["stop_tight_soft"]: mult *= .5; why.append("tight stop")
        if exp is not None and nb >= T["base_n_min"]:
            if exp <= T["base_half_exp"]: mult *= .5
            elif exp < T["base_soft_exp"]: mult *= .75
        elif nb < T["base_n_min"]: mult *= .75; why.append("n<%d" % T["base_n_min"])
        if f["er20"] < T["choppy_er_soft"]: mult *= T["chop_mult"]; why.append("choppy")
        nat = 0.02 * mult
        ok = exp is not None and nb >= nmin and exp >= 0 and nat >= 0.02 - 1e-9
        return dict(n=nb, exp_R=round(exp, 3) if exp is not None else None, n_min=nmin, natural_pct=round(nat * 100, 2), size_cuts=why, wake_ok=ok, setup_family="%s/%s/trend%d" % (seat, side, int(cur)))
    except Exception as e:
        return dict(error="%s: %s" % (type(e).__name__, str(e)[:100]), wake_ok=False)
def earnings_block(sym, days=10):
    """No hold through a print inside the hold window. Reads existing calendar files if any; unknown = not blocked (flagged)."""
    try:
        E = CTX_CACHE.get("earn")
        if E is None:
            E = {}
            for fn in ("state/calendar/earnings.json", "state/calendar/earnings_calendar.json", "data/calendar/earnings.json"):
                j = rj(os.path.join(BASE, fn))
                if isinstance(j, dict): E.update({k.upper(): v for k, v in (j.get("dates") or j).items() if isinstance(v, str)})
            CTX_CACHE["earn"] = E
        d = E.get(sym.upper())
        if not d: return None
        dd = (dt.date.fromisoformat(d[:10]) - wita().date()).days
        return d if 0 <= dd <= days else None
    except Exception: return None
CTX_CACHE = {}
# ---------------- context ----------------
def context(c, t):
    news = {}
    N = rj(os.path.join(BASE, "state", "veteran_desk", "news.json"), {}) or {}
    for k, v in (N.get("asset_tags") or {}).items(): news[k] = news.get(k, 0) + len(v or [])
    try:
        cut = (t - dt.timedelta(hours=24)).isoformat()
        for line in open(os.path.join(BASE, "state", "veteran_desk", "news_alerts.jsonl")).read().splitlines()[-2000:]:
            try:
                a = json.loads(line); s = a.get("ticker") or a.get("sym")
                if s and str(a.get("utc") or a.get("published_utc") or "") >= cut: news[s] = news.get(s, 0) + 1
            except Exception: pass
    except Exception: pass
    try:   # SEC filings already in pit (free EDGAR) - last 3 days count as news for Quinn/Iris/Nico
        cut = (t - dt.timedelta(days=3)).isoformat()
        for s, n in c.execute("select sym,count(*) from filings where filed_utc>=? group by sym", (cut,)).fetchall(): news[s] = news.get(s, 0) + n
    except Exception: pass
    from veteran_desk import scan_universe as SU
    fac = SU._factor_rank()
    tnx = P.bars_asof(c, "^TNX", t, 25); tnx_chg20 = (tnx[-1][4] - tnx[-21][4]) if len(tnx) >= 21 else None
    cm = []
    for s in ("CL=F", "BZ=F", "NG=F"):
        b = P.bars_asof(c, s, t, 25)
        if len(b) >= 21 and b[-21][4]: cm.append(b[-1][4] / b[-21][4] - 1)
    return dict(news=news, factor=fac, tnx_chg20=tnx_chg20, crude_mom=sum(cm) / len(cm) if cm else 0.0)
def bench_rets(c, t):
    B = {}
    for s in ("KRAKEN:XBTUSD", "SMH", "SOXX", "KRE", "XLE", "SPY"):
        b = P.bars_asof(c, s, t, 40)
        if len(b) >= 31: B[s] = {k: b[-1][4] / b[-1 - k][4] - 1 for k in (7, 20, 30) if b[-1 - k][4]}
    try: B["_au"] = P.bars_asof(c, "AUDUSD=X", t, 1)[-1][4]
    except Exception: B["_au"] = 0.65
    return B
# ---------------- feature store ----------------
STORE = dict(F={}, U={}, avail={}, rejects={}, built_utc=None, secs=None, feat_errors={})
def build_store():
    t0 = time.time(); t = now(); c = P.con()
    U, avail = build_universe(); B = bench_rets(c, t); F = {}; rej = {}; err = {}
    for s, u in U.items():
        try:
            f, why = feats(c, u, t, B)
            if not f: rej[s] = why; continue
            pr = pre_filter(s, u["market"], f["close"], f.get("adv_aud"), f["_rows"])
            if pr: rej[s] = pr; continue
            F[s] = f
        except Exception as e: err[s] = "%s: %s" % (type(e).__name__, str(e)[:80])
    STORE.update(F=F, U=U, avail=avail, rejects=rej, feat_errors=dict(list(err.items())[:200]), n_err=len(err), built_utc=t.isoformat(), secs=round(time.time() - t0, 1), ctx=context(c, t))
    try: c.close()
    except Exception: pass
    log("feature store built: %d scored-ready of %d universe (%d filtered, %d errors) in %.0fs" % (len(F), len(U), len(rej), len(err), time.time() - t0))
# ---------------- scoring ----------------
def score_all():
    t = now(); F, U, ctx = STORE["F"], STORE["U"], STORE["ctx"]; out = {}
    for seat, cfg in SEATS.items():
        syms = seat_universe(seat, U, F); rows = []
        for s in syms:
            raw, sig = raw_pillars(seat, F[s], ctx); rows.append([s, raw, sig])
        Z = {p: zs([r[1][p] for r in rows]) for p in PILLARS}
        comp = [sum(cfg["w"][i] * Z[p][j] for i, p in enumerate(PILLARS)) for j in range(len(rows))]
        order = sorted(range(len(rows)), key=lambda j: comp[j]); pct = {}
        for rk, j in enumerate(order): pct[j] = 100.0 * (rk + 1) / max(len(rows), 1)
        res = []
        for j, (s, raw, sig) in enumerate(rows):
            f = F[s]; R, rn = rfit(f); sc = round(pct[j] * R, 1)
            nz = sum(1 for i, p in enumerate(PILLARS) if cfg["w"][i] > 0 and Z[p][j] > 1)
            fl = floors(seat, f, sig, ctx); g = grade(sc, nz, R, fl)
            res.append(dict(sym=s, side="LONG" if f["dir"] > 0 else "SHORT", score=sc, pct=round(pct[j], 1), grade=g, r_fit=R, r_notes=rn, pillars_z={p: round(Z[p][j], 2) for p in PILLARS},
                            pillars_gt1=nz, signals=[k for k, v in sig.items() if v], floors_failed=fl, close=f["close"], stop=f["stop"], k_atr=f["k_atr"], atr_pct=f["atr_pct"],
                            last_bar=f["last_date"], bar_age_d=f["bar_age_d"], market=f["market"], rvol=f["rvol"], ret20=f["ret20"], news=ctx["news"].get(s, 0)))
        res.sort(key=lambda x: -x["score"])
        for x in res[:25]:
            if x["grade"] in ("A", "A+"):
                x["history"] = history_check(seat, F[x["sym"]]); x["earnings_block"] = earnings_block(x["sym"])
                x["market_open"] = market_open(x["market"], t)
                x["wake_ok"] = bool(x["history"].get("wake_ok")) and not x["earnings_block"] and x["market_open"] and x["bar_age_d"] <= (2 if x["market"] == "CRYPTO" else 4)
        out[seat] = dict(seat=seat, trader=cfg["name"], weights=dict(zip(PILLARS, cfg["w"])), scanned=len(syms), top10=res[:10],
                         n_A=sum(1 for x in res if x["grade"] == "A"), n_Aplus=sum(1 for x in res if x["grade"] == "A+"), n_B=sum(1 for x in res if x["grade"] == "B"),
                         grades={x["sym"].upper(): x["grade"] for x in res}, utc=t.isoformat(), version=VERSION)
    return out
def marcus_view(S):
    """Marcus (Head of Desk): desk-wide top 10 across seats + cross-seat overlaps / opposite sides."""
    allr = [dict(x, seat=k) for k, v in S.items() for x in v["top10"]]
    allr.sort(key=lambda x: (-{"A+": 3, "A": 2, "B": 1}.get(x["grade"], 0), -x["score"]))
    by = {}
    for x in allr: by.setdefault(x["sym"], []).append((x["seat"], x["side"]))
    ov = [dict(sym=s, seats=v, conflict=len({b for _, b in v}) > 1) for s, v in by.items() if len(v) > 1]
    seen, top = set(), []
    for x in allr:
        if x["sym"] in seen: continue
        seen.add(x["sym"]); top.append(x)
        if len(top) == 10: break
    return dict(seat="head_of_desk", trader="Marcus Hale", top10=top, overlaps=ov, utc=now().isoformat())
# ---------------- wake broker ----------------
STATE_P = os.path.join(ST, "broker_state.json"); COUNT_P = os.path.join(ST, "wake_counts.json"); QUEUE_P = os.path.join(ST, "wake_queue.jsonl")
def _lock(name):
    f = open(os.path.join(ST, "." + name + ".lock"), "w"); fcntl.flock(f, fcntl.LOCK_EX); return f
def counts():
    d = wita().date().isoformat(); C = rj(COUNT_P, {}) or {}
    if C.get("day") != d: C = dict(day=d, total=0, seat={})
    return C
def broker(S):
    """Hysteresis (A/A+ on 2 consecutive cycles), debounce (4h, crypto 2h unless A->A+ or +5), rejection memory. Adds pending candidates."""
    t = now(); B = rj(STATE_P, {}) or {}; prev = B.get("prev", {}); deb = B.get("debounce", {}); pend = B.get("pending", {}); supp = B.get("suppressed", {})
    cur = {}; suppressed_counts = {}
    for seat, v in S.items():
        for x in v["top10"]:
            key = seat + "|" + x["sym"]
            if x["grade"] not in ("A", "A+"): continue
            cur[key] = dict(grade=x["grade"], score=x["score"])
            why = None
            if not x.get("wake_ok"): why = "wake gate: " + ("history<0 or natural<2%" if not (x.get("history") or {}).get("wake_ok") else "earnings in window" if x.get("earnings_block") else "market closed/stale mark")
            elif key not in prev: why = "hysteresis: first A cycle"
            else:
                last = deb.get(key)
                if last:
                    hrs = (t - dt.datetime.fromisoformat(last["utc"])).total_seconds() / 3600
                    lim = DEBOUNCE_H["crypto"] if seat == "crypto" else DEBOUNCE_H["default"]
                    if hrs < lim and not (last["grade"] == "A" and x["grade"] == "A+") and x["score"] < last["score"] + 5: why = "debounce %.1fh<%.0fh" % (hrs, lim)
                if supp.get(key) == wita().date().isoformat(): why = "rejection memory (structural gate reject today)"
            if why: suppressed_counts[seat] = suppressed_counts.get(seat, 0) + 1; x["wake_block"] = why; continue
            P_ = pend.setdefault(seat, {})
            if x["sym"] not in P_: P_[x["sym"]] = dict(x, queued_utc=t.isoformat())
    B.update(prev=cur, debounce=deb, pending=pend, suppressed=supp, suppressed_counts=suppressed_counts, utc=t.isoformat()); wj(STATE_P, B); return B
def release_slot():
    """Release <=2 seats per 5-min slot at :x2/:x7 WITA (off :x0/:x5), <=3 candidates per wake, respecting the hard caps atomically."""
    t = wita()
    if t.minute % 5 != 2: return None
    if _round_busy(): return None   # tfo_thfpar_20261031: hold pending while a round runs; next slot releases every eligible seat at once, revalidated fresh (no stale backlog)
    with _lock("broker"):
        B = rj(STATE_P, {}) or {}; slot = t.strftime("%Y%m%d-%H%M")
        if B.get("last_slot") == slot: return None
        B["last_slot"] = slot; pend = B.get("pending", {})
        C = counts(); chosen = {}
        order = sorted([s for s in pend if pend[s]], key=lambda s: min(v["queued_utc"] for v in pend[s].values()))
        for seat in order:
            if len(chosen) >= SEATS_PER_SLOT or C["total"] >= CAP_TOTAL: break
            if C["seat"].get(seat, 0) >= CAP_SEAT: continue
            cands = sorted(pend[seat].values(), key=lambda x: (x["grade"] != "A+", -x["score"]))[:3]
            # TTL: drop stale candidates (2 cycles of 15 min feature refresh = 30 min) - revalidated against the current top10 file
            cur = {x["sym"]: x for x in (rj(os.path.join(ST, "top10", seat + ".json"), {}) or {}).get("top10", [])}
            cands = [cur[x["sym"]] for x in cands if x["sym"] in cur and cur[x["sym"]]["grade"] in ("A", "A+") and cur[x["sym"]].get("wake_ok")]
            pend[seat] = {}
            if not cands: continue
            chosen[seat] = cands; C["seat"][seat] = C["seat"].get(seat, 0) + 1; C["total"] += 1
            for x in cands: B.setdefault("debounce", {})[seat + "|" + x["sym"]] = dict(utc=now().isoformat(), grade=x["grade"], score=x["score"])
        wj(STATE_P, B)
        if not chosen: return None
        wj(COUNT_P, C)
    wid = "THF-" + slot + "-" + "-".join(sorted(chosen))[:60]
    W = dict(wake_id=wid, utc=now().isoformat(), wita=t.strftime("%F %H:%M WITA"), seats={s: dict(candidates=[{k: v for k, v in x.items() if k != "history"} | dict(history=x.get("history")) for x in c],
             top10=(rj(os.path.join(ST, "top10", s + ".json"), {}) or {}).get("top10", [])[:10]) for s, c in chosen.items()}, status="queued", counts_after=C, version=VERSION)
    wj(os.path.join(ST, "wakes", wid + ".json"), W)
    jl(QUEUE_P, dict(wake_id=wid, utc=W["utc"], wita=W["wita"], seats=list(chosen), cands={s: [(x["sym"], x["side"], x["grade"], x["score"]) for x in c] for s, c in chosen.items()}, status="queued"))
    for s, c in chosen.items():
        for x in c: jl(os.path.join(BASE, "state", "veteran_desk", "engine_contrib.jsonl"), dict(utc=W["utc"], wita=W["wita"], engine="thf-" + s, seat=s, kind="wake_" + x["grade"], ticker=x["sym"], score=x["score"], grade=x["grade"], wake_id=wid))
    log("WAKE %s seats=%s caps day total %d/%d" % (wid, ",".join(chosen), C["total"], CAP_TOTAL))
    second_looks(W)
    return W
# ---------------- wake runner (one at a time; never takes the batch pipeline lock; defers while it runs) ----------------
RUNNER = dict(proc=None, wid=None, t0=None)
def _round_busy():
    """tfo_thfpar_20261031: True while a wake process runs or a queued wake is waiting."""
    p = RUNNER.get("proc")
    if p is not None and p.poll() is None: return True
    try: Q = [json.loads(l) for l in open(QUEUE_P).read().splitlines() if l.strip()]
    except Exception: return False
    done = {q["wake_id"] for q in Q if q.get("status") != "queued"}
    return any(q.get("status") == "queued" and q["wake_id"] not in done for q in Q)
PIPE_LOCK = os.path.join(BASE, "state", "veteran_desk", ".pipeline.lock")
PY = sys.executable
def runner_tick():
    p = RUNNER["proc"]
    if p is not None:
        if p.poll() is None:
            if time.time() - RUNNER["t0"] > 1800: p.kill(); _finish(RUNNER["wid"], "killed_timeout_30m")
            return
        _finish(RUNNER["wid"], "done rc=%s" % p.returncode); RUNNER.update(proc=None, wid=None)
    if os.path.isdir(PIPE_LOCK) and os.environ.get("THF_DEFER_PIPE", "0") == "1": return   # tfo_thfindep_20261030: THF wakes book into state/thf (isolated) so they no longer wait behind VD batch runs; THF_DEFER_PIPE=1 restores old deferral
    if os.environ.get("THF_DISPATCH", "1") != "1": return
    Q = [json.loads(l) for l in open(QUEUE_P).read().splitlines()] if os.path.exists(QUEUE_P) else []
    done = {q["wake_id"] for q in Q if q.get("status") != "queued"}
    todo = [q for q in Q if q.get("status") == "queued" and q["wake_id"] not in done]
    if not todo: return
    q = todo[0]; wid = q["wake_id"]
    env = dict(os.environ, VD_ENGINE_WAKE=wid, PYTHONUNBUFFERED="1", THF_DESK="1", VET_LLM_CONCURRENCY=THF_LLM_CONCURRENCY, VET_LLM_TIMEOUT=THF_SEAT_TIMEOUT_S, TMPDIR=_wake_tmp(wid))   # tfo_thfpar_20261031: bounded parallel seats + per-seat timeout + private TMPDIR per round   # tfo_thffresh_20261030: wakes book into the THF fresh book
    lf = open(os.path.join(BASE, "logs", "vd_engine_wakes.log"), "a"); lf.write("\n%s START %s seats=%s\n" % (wita().strftime("%F %T WITA"), wid, ",".join(q["seats"]))); lf.flush()
    RUNNER.update(proc=subprocess.Popen([PY, "-m", "veteran_desk.desk", "run", "--seats=" + ",".join(q["seats"])], cwd=BASE, env=env, stdout=lf, stderr=subprocess.STDOUT), wid=wid, t0=time.time())
    jl(QUEUE_P, dict(wake_id=wid, utc=now().isoformat(), status="running", pid=RUNNER["proc"].pid)); log("RUN %s pid %d" % (wid, RUNNER["proc"].pid))
def _wake_tmp(wid):
    d = os.path.join(os.path.dirname(QUEUE_P), "tmp", re.sub(r"[^A-Za-z0-9_.-]", "_", wid)); os.makedirs(d, exist_ok=True); return d
def _finish(wid, st):
    res = rj(os.path.join(ST, "runs", wid + ".json"), {}) or {}
    summ = dict(proposals=[(r["seat"], r["proposal"].get("ticker"), r["proposal"].get("side"), r["proposal"].get("grade"), r["gate"]["passed"], r["gate"]["reasons"][:2]) for r in res.get("proposals", [])],
                seats={k: v.get("action") for k, v in (res.get("seats") or {}).items()}, queued=res.get("queued"), caps={k: v.get("engine_grade_cap") for k, v in (res.get("seats") or {}).items() if v.get("engine_grade_cap")})
    jl(QUEUE_P, dict(wake_id=wid, utc=now().isoformat(), status="finished", result=st, **summ))
    W = rj(os.path.join(ST, "wakes", wid + ".json"), {}) or {}; W.update(status="finished", result=st, outcome=summ); wj(os.path.join(ST, "wakes", wid + ".json"), W)
    for s, tk, sd, g, ok, why in summ["proposals"]:
        jl(os.path.join(BASE, "state", "veteran_desk", "engine_contrib.jsonl"), dict(utc=now().isoformat(), engine="thf-" + s, seat=s, kind="fill_queued" if ok else "gate_reject", ticker=tk, grade=g, wake_id=wid, meta=dict(reasons=why)))
        if not ok and any(k in " ".join(why) for k in ("history", "ADV", "ccy", "floor")):
            with _lock("broker"):
                B = rj(STATE_P, {}) or {}; B.setdefault("suppressed", {})[s + "|" + str(tk)] = wita().date().isoformat(); wj(STATE_P, B)
    log("FINISH %s %s %s" % (wid, st, json.dumps(summ, default=str)[:300]))
    refund_failed(wid, W, summ)
def refund_failed(wid, W, summ):
    """tfo_wakerefund_20261030: a seat whose model round failed (no action back: queue/timeout/error) does not use up a wake against CAP_SEAT/CAP_TOTAL."""
    try:
        failed = [s for s in (W.get("seats") or {}) if not (summ.get("seats") or {}).get(s)]
        if not failed or W.get("refunded"): return []
        day = (W.get("wita") or "")[:10]
        with _lock("broker"):
            C = counts()
            if C.get("day") != day: return []
            for s in failed:
                if C["seat"].get(s, 0) > 0: C["seat"][s] -= 1; C["total"] = max(0, C["total"] - 1)
            C.setdefault("refunds", []).append(dict(wake_id=wid, seats=failed, utc=now().isoformat())); wj(COUNT_P, C)
        W["refunded"] = failed; wj(os.path.join(ST, "wakes", wid + ".json"), W); log("REFUND %s seats=%s (model round failed)" % (wid, ",".join(failed)))
        return failed
    except Exception as e: log("REFUND err %s" % type(e).__name__); return []
# ---------------- second looks (advisory, non-blocking) ----------------
GROK_Q = os.path.join(ST, "grok_second_look_queue.jsonl")
def second_looks(W):
    try: subprocess.Popen([PY, "-m", "veteran_desk.vd_engine", "gpt2l", W["wake_id"]], cwd=BASE, stdout=open(os.path.join(BASE, "logs", "vd_engine_2l.log"), "a"), stderr=subprocess.STDOUT, start_new_session=True)
    except Exception as e: log("gpt2l spawn failed %s" % e)
    req = dict(kind="grok_second_look_request", wake_id=W["wake_id"], utc=W["utc"], wita=W["wita"], status="pending", reply_to="state/vd_engine/second_look/grok/%s.json (via: python3 -m veteran_desk.vd_engine grok-note %s <file>)" % (W["wake_id"], W["wake_id"]),
               ask="Independent second look, advisory only (never authorises a trade). For each candidate: agree/disagree with side, what would kill it, any X/tape read, factual errors (wrong stop side, thin liquidity). Reply JSON {wake_id, notes:[{sym, call:LONG|SHORT|PASS, confidence:0-1, kill_case, fact_flags:[]}]}",
               candidates={s: [dict(sym=x["sym"], side=x["side"], grade=x["grade"], score=x["score"], close=x["close"], stop=x["stop"], signals=x["signals"], last_bar=x["last_bar"]) for x in v["candidates"]] for s, v in W["seats"].items()})
    jl(GROK_Q, req)
    url = _secret("vd_grok2l_url"); key = _secret("vd_grok2l_key")
    if url and key:   # parallel to the revguard pattern, its OWN URL (never vd_revguard_url)
        def post():
            try:
                import urllib.request
                r = urllib.request.Request(url, data=json.dumps(req).encode(), headers={"Content-Type": "application/json", "Authorization": "Bearer " + key}, method="POST")
                urllib.request.urlopen(r, timeout=15).read(); jl(GROK_Q, dict(wake_id=W["wake_id"], status="posted", utc=now().isoformat()))
            except Exception as e: jl(GROK_Q, dict(wake_id=W["wake_id"], status="post_failed", err=type(e).__name__, utc=now().isoformat()))
        threading.Thread(target=post, daemon=True).start()
    else: jl(GROK_Q, dict(wake_id=W["wake_id"], status="queued_no_webhook", note="parent to wire Grok second-look routine (secrets/vd_grok2l_url + vd_grok2l_key)", utc=now().isoformat()))
def _secret(n):
    try: return open(os.path.join(BASE, "secrets", n)).read().strip() or None
    except Exception: return None
def gpt2l(wid):
    W = rj(os.path.join(ST, "wakes", wid + ".json"), {}) or {}
    from veteran_desk import desk as D
    c = {s: [dict(sym=x["sym"], side=x["side"], grade=x["grade"], score=x["score"], close=x["close"], stop=x["stop"], signals=x["signals"], r_notes=x["r_notes"], history=x.get("history"), last_bar=x["last_bar"]) for x in v["candidates"]] for s, v in W.get("seats", {}).items()}
    prompt = ("You are a second-look reviewer for a PAPER trading desk. Advisory only; you cannot authorise or block. For each candidate say what would kill it in under 40 words, "
              "your call LONG/SHORT/PASS, confidence 0-1, and any hard fact error (wrong stop side, thin liquidity). Reply ONE JSON object {notes:[{sym,call,confidence,kill_case,fact_flags:[]}]}.\nCANDIDATES:" + json.dumps(c, default=str))
    t0 = time.time(); txt = D._llm("codex", prompt, LLM_TIMEOUT_2L, effort="low")
    j = D.parse_json(txt) if txt and not txt.startswith("__ERR__") else None
    o = dict(wake_id=wid, model="grok_bot/file_queue", utc=now().isoformat(), secs=round(time.time() - t0, 1), status="ok" if j else "timeout_or_error", notes=(j or {}).get("notes"), raw=None if j else (txt or "")[:300])
    wj(os.path.join(ST, "second_look", "gpt", wid + ".json"), o); _merge(wid)
def grok_note(wid, path):
    j = rj(path) or {}
    o = dict(wake_id=wid, model="Grok Bot (subscription routine)", utc=now().isoformat(), status="ok" if j.get("notes") else "empty", notes=j.get("notes"))
    wj(os.path.join(ST, "second_look", "grok", wid + ".json"), o); jl(GROK_Q, dict(wake_id=wid, status="answered", utc=o["utc"])); _merge(wid)
def _merge(wid):
    g = rj(os.path.join(ST, "second_look", "gpt", wid + ".json")); k = rj(os.path.join(ST, "second_look", "grok", wid + ".json"))
    W = rj(os.path.join(ST, "wakes", wid + ".json"), {}) or {}; M = {}
    for s, v in W.get("seats", {}).items():
        for x in v["candidates"]:
            a = next((n for n in (g or {}).get("notes") or [] if n.get("sym") == x["sym"]), None); b = next((n for n in (k or {}).get("notes") or [] if n.get("sym") == x["sym"]), None)
            if a and b: st = "factual_kill" if (a.get("fact_flags") and b.get("fact_flags")) else "confirm" if a.get("call") == b.get("call") == x["side"] else "split"
            elif a or b: st = "single"
            else: st = "none"
            M[x["sym"]] = dict(seat=s, merge=st, gpt=a, grok=b)
    wj(os.path.join(ST, "second_look", wid + ".merge.json"), dict(wake_id=wid, utc=now().isoformat(), merge=M, advisory=True))
# ---------------- gate overlord (plumbing only, design §7.3) ----------------
FORBID = ("RISK FAIL", "BEHAVIOR FAIL", "SPECIALIST FAIL", "factual_kill", "HALT", "kill FLAT", "ADV hard", "heat hard", "grade B", "BLOCKED")
def gate_overlord():
    t = now(); acts = []; restage = []
    try:
        Bk = rj(os.path.join(BASE, "state", "thf", "thf_book.json"), {}) or {}
        for seat, v in (Bk.get("seats") or {}).items():
            for k, p in (v.get("positions") or {}).items():
                if p.get("status") != "armed" or not p.get("armed_utc"): continue
                age_h = (t - dt.datetime.fromisoformat(p["armed_utc"])).total_seconds() / 3600
                m = _mk(p.get("ticker", ""))
                if age_h > 8 and p.get("grade") in ("A", "A+"):
                    acts.append(dict(cls="armed_unfilled", wake_id=k, seat=seat, ticker=p.get("ticker"), age_h=round(age_h, 1), market=m, market_open=market_open(m, t), entry=p.get("entry"), stop=p.get("stop"), action="report_to_marcus (book keeps it armed; engine never edits the book)"))
    except Exception as e: acts.append(dict(cls="error", err=str(e)[:120]))
    # restagefix 20261030: scan today AND yesterday (WITA) dirs - US session crosses WITA midnight
    _Ds = [os.path.join(BASE, "state", "clearance", (wita() - dt.timedelta(days=k)).strftime("%Y%m%d")) for k in (0, 1)]
    try:
        for D, fn in [(D, f) for D in _Ds if os.path.isdir(D) for f in os.listdir(D)]:
            if not fn.endswith(".json"): continue
            j = rj(os.path.join(D, fn), {}) or {}; txt = json.dumps(j, default=str)
            fin = str(j.get("final") or (j.get("clearance") or {}).get("final") or "")
            fin_utc = j.get("finalized_utc") or j.get("final_utc") or j.get("asof_utc")
            if fin != "CLEARED" or not fin_utc: continue
            m = _mk(j.get("ticker") or fn[:-5]); lo, hi = SESS.get(m, (None, None))
            if lo is None: continue
            fw = dt.datetime.fromisoformat(str(fin_utc).replace("Z", "+00:00")).astimezone(WITA); fh = fw.hour + fw.minute / 60
            if (t - fw).total_seconds() > 36 * 3600: continue
            # in-session (late) clearance; wrap past WITA midnight (old check fh<=lo+6 missed US 00:00-04:00 WITA)
            if any(lo <= hh <= hi for hh in (fh, fh + 24)):
                if any(x in txt for x in FORBID): continue
                restage.append(dict(cls="late_clearance", ticker=j.get("ticker") or fn[:-5], market=m, finalized_wita=fw.strftime("%F %H:%M"), action="restage_queue for next legal window"))
    except Exception as e: acts.append(dict(cls="error", err=str(e)[:120]))
    dr = rj(os.path.join(BASE, "state", "veteran_desk", "seat_drought.json"), {}) or {}; B = rj(STATE_P, {}) or {}; ann = {}
    for seat, v in (dr.get("seats") or dr if isinstance(dr, dict) else {}).items():
        if isinstance(v, dict) and v.get("drought") and (B.get("suppressed_counts") or {}).get(seat): ann[seat] = "drought_plumbing (A/A+ suppressed by budget/debounce/plumbing, not seat silence)"
    o = dict(utc=t.isoformat(), wita=wita().strftime("%F %H:%M WITA"), stuck=acts, restage=restage, drought_annotations=ann, rules="plumbing only; never clears RISK/BEHAVIOR/SPECIALIST; no write to book/stops/sizes/grades/rails")
    wj(os.path.join(ST, "gate_overlord.json"), o)
    if restage:
        R = rj(os.path.join(ST, "restage_queue.json"), {}) or {}
        for r in restage: R.setdefault(r["ticker"] + "|" + r["finalized_wita"][:10], r)
        wj(os.path.join(ST, "restage_queue.json"), R)
    return o
# ---------------- sub-desk plugin interface (v0: shadow only) ----------------
SUBDESK_TEMPLATES = ("news_scout", "flow_scout", "quant_screen", "short_scout", "event_scout")
SUBDESK_P = os.path.join(ST, "subdesks.json")
def _sd_news_scout(seat, S, F, ctx): return [x["sym"] for x in S[seat]["top10"] if x.get("news")][:5]
def _sd_flow_scout(seat, S, F, ctx): return [s for s in seat_universe(seat, STORE["U"], F) if (F[s].get("rvol") or 0) >= 3][:5]
def _sd_short_scout(seat, S, F, ctx): return [x["sym"] for x in S[seat]["top10"] if x["side"] == "SHORT"][:5]
def _sd_quant_screen(seat, S, F, ctx): return [x["sym"] for x in S[seat]["top10"] if x["pillars_gt1"] >= 3][:5]
def _sd_event_scout(seat, S, F, ctx): return [x["sym"] for x in S[seat]["top10"] if earnings_block(x["sym"], 20)][:5]
SD_FN = dict(news_scout=_sd_news_scout, flow_scout=_sd_flow_scout, short_scout=_sd_short_scout, quant_screen=_sd_quant_screen, event_scout=_sd_event_scout)
def subdesks(S):
    spec = rj(SUBDESK_P)
    if spec is None:
        spec = dict(note="v0 plugin interface: seats request sub-desks by adding {id, seat, template} here; whitelist=%s; max 4/seat; all start SHADOW 5 trading days; candidates only, no wake/gate/book path" % (SUBDESK_TEMPLATES,),
                    subdesks=[dict(id="kai-flow", seat="crypto", template="flow_scout", mode="shadow", created_utc=now().isoformat()), dict(id="dexpa-flow", seat="price_action", template="flow_scout", mode="shadow", created_utc=now().isoformat())])
        wj(SUBDESK_P, spec)
    for d in spec.get("subdesks", [])[:44]:
        if d.get("template") not in SD_FN or d.get("seat") not in S: continue
        try: c = SD_FN[d["template"]](d["seat"], S, STORE["F"], STORE["ctx"])
        except Exception as e: c = []; d["err"] = str(e)[:80]
        jl(os.path.join(ST, "subdesks.jsonl"), dict(utc=now().isoformat(), id=d["id"], seat=d["seat"], template=d["template"], mode=d.get("mode", "shadow"), candidates=c))
# ---------------- coverage ----------------
def coverage():
    F, U, rej = STORE["F"], STORE["U"], STORE["rejects"]; by = {}
    for s, u in U.items():
        m = u["market"] if u["cls"] == "EQ" else {"CRYPTO": "CRYPTO (Kraken USD)", "FX": "FX (OANDA book)", "FUT": "Metals+energy (OANDA)" if u.get("oanda") else "Futures+indices (Yahoo)", "MACRO": "Futures+indices (Yahoo)"}[u["cls"]]
        b = by.setdefault(m, dict(universe=0, scored=0, filtered={}, no_data=0, stale=0, no_volume=0))
        b["universe"] += 1
        if s in F:
            b["scored"] += 1
            if F[s]["bar_age_d"] > (2 if u["cls"] == "CRYPTO" else 4): b["stale"] += 1
            if not F[s]["has_vol"]: b["no_volume"] += 1
        else:
            r = rej.get(s) or "error"
            if "bars" in r or "closes" in r or r == "error": b["no_data"] += 1
            else: b["filtered"][r] = b["filtered"].get(r, 0) + 1
    A = STORE["avail"]
    for m, b in by.items():
        a = A.get(m) or {}; b["available"] = a.get("available", b["universe"]); b["cleared_A500k"] = a.get("cleared", b["universe"])
        b["pct_scored_of_cleared"] = round(100 * b["scored"] / max(b["cleared_A500k"], 1), 1)
    last = {}
    for s, f in F.items(): last[f["last_date"]] = last.get(f["last_date"], 0) + 1
    sect = sum(1 for u in U.values() if u["cls"] == "EQ" and u.get("sector"))
    gaps = [dict(sev="red" if max(last or {"": 0}) < (wita().date() - dt.timedelta(days=1)).isoformat() else "amber", what="PIT daily bars end %s (newest bar across scored names); intraday bars not in pit" % max(last or {"n/a": 0})),
            dict(sev="amber", what="Yahoo EU/UK quotes run 15-20 min late"),
            dict(sev="amber", what="FX: %d of %d OANDA-book pairs scored have no volume (OTC) - volume pillar off for them" % (by.get("FX (OANDA book)", {}).get("no_volume", 0), by.get("FX (OANDA book)", {}).get("scored", 0))),
            dict(sev="amber", what="Sector tags on %d of %d cleared equities (sector seats only see tagged names + hand lists)" % (sect, sum(1 for u in U.values() if u["cls"] == "EQ"))),
            dict(sev="amber", what="No borrow data in the engine (shorts checked by gate/book only)"),
            dict(sev="amber", what="No 1h/5m bars: RVOL is daily (not time-of-day); Kai 1h breakout uses daily proxy"),
            dict(sev="amber", what="Earnings calendar: %s" % ("loaded" if CTX_CACHE.get("earn") else "no earnings file found - print-in-window block unverified"))]
    o = dict(utc=now().isoformat(), store_built_utc=STORE["built_utc"], store_secs=STORE["secs"], by_market=by, bar_dates=dict(sorted(last.items())[-6:]), gaps=gaps,
             totals=dict(universe=len(U), scored=len(F), filtered=sum(1 for r in rej.values() if "bars" not in r), errors=STORE.get("n_err", 0)))
    wj(os.path.join(ST, "coverage.json"), o); return o
# ---------------- daemon ----------------
def heartbeat(**k):
    wj(os.path.join(ST, "hb", "engine.json"), dict(utc=now().isoformat(), wita=wita().strftime("%F %T WITA"), pid=os.getpid(), boot=BOOT, version=VERSION, **k))
BOOT = now().isoformat()
def cycle(n):
    if not STORE["built_utc"] or (now() - dt.datetime.fromisoformat(STORE["built_utc"])).total_seconds() > FEAT_EVERY_S: build_store(); coverage()
    S = score_all()
    for seat, v in S.items():
        wj(os.path.join(ST, "grades", seat + ".json"), dict(utc=v["utc"], grades=v.pop("grades")))
        wj(os.path.join(ST, "top10", seat + ".json"), v)
    wj(os.path.join(ST, "top10", "head_of_desk.json"), marcus_view(S))
    with _lock("broker"): broker(S)
    first = os.path.join(ST, "first_top10.json")
    if not os.path.exists(first): wj(first, dict(utc=now().isoformat(), wita=wita().strftime("%F %T WITA"), seats={k: [(x["sym"], x["side"], x["grade"], x["score"]) for x in v["top10"]] for k, v in S.items()}))
    try: subdesks(S)
    except Exception as e: log("subdesks err %s" % e)
    if n % 3 == 0:
        try: gate_overlord()
        except Exception as e: log("gate_overlord err %s" % e)
    return S
def daemon():
    log("ENGINE START %s pid %d" % (VERSION, os.getpid())); n = 0; last = 0; fails = 0; last_ok = None
    while True:
        try:
            if time.time() - last >= CYCLE_S:
                S = cycle(n); n += 1; last = time.time(); fails = 0; last_ok = now().isoformat()
            release_slot(); runner_tick()
            heartbeat(cycles=n, last_cycle_ok_utc=last_ok, fails=fails, store_built_utc=STORE["built_utc"], scored=len(STORE["F"]), runner=RUNNER["wid"], counts=counts())
        except Exception as e:
            fails += 1; log("cycle error %s: %s\n%s" % (type(e).__name__, e, traceback.format_exc()[-600:])); last = time.time()
            heartbeat(cycles=n, last_cycle_ok_utc=last_ok, fails=fails, error=str(e)[:200])
            if fails >= 3: time.sleep(60)
        time.sleep(15)
if __name__ == "__main__":
    a = sys.argv[1:]; cmd = a[0] if a else "status"
    if cmd == "daemon": daemon()
    elif cmd == "once":
        os.environ["THF_DISPATCH"] = "0"; S = cycle(0); print(json.dumps({k: [(x["sym"], x["side"], x["grade"], x["score"]) for x in v["top10"]] for k, v in S.items()}, indent=0)); print(json.dumps(coverage()["totals"]))
    elif cmd == "gpt2l": gpt2l(a[1])
    elif cmd == "grok-note": grok_note(a[1], a[2])
    elif cmd == "status": print(json.dumps(dict(hb=rj(os.path.join(ST, "hb", "engine.json")), counts=counts()), indent=1, default=str))
