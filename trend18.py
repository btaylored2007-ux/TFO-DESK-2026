#!/usr/bin/env python3
"""trend18.py - DEEP18 levered risk-parity trend sleeve, R2 design. PAPER ONLY.

R2 UPGRADE (3 Oct 2026 18:05 AEST; TFO decision under Brett's "do what is best for the book";
spec research/deep18/DEEP18_ROUND2_20261003.md section 4, code research/deep18/r2/box_results/code):
  1. Duration gate: TMF raw weight x [^TNX < ^TNX 252 trading days earlier] (on top of trend), applied
     before the vol scaling exactly as r2lib.rp2(gates=...).
  2. Managed-futures basket: sleeve idle cash goes into DBMF / KMLM / CTA in equal thirds instead of
     T-bill accrual. Cap = scale x (base x (1 - sum trend notional) - $50k reserve) (R2 reserve 2.8%).
     Funded ONLY from cash already in the sleeve, plus free powder above the $50k reserve at monthly /
     refresh rebalances (room freed by exits). Never a reason to sell another desk position; --sell-staged
     and --fit are sized on the trend legs only. MF shrinks first when the trend legs need room.
  3. Mid-month refresh back to the last target weights (drift control, no new signal) on the middle US
     trading day of each month: sessions_of_month[len // 2] (fam_final.mids, 0-indexed), at that close.
  4. Next-close execution (R2 official lag-2 figure): signals always from the PRIOR completed US close;
     the monthly rebalance trades at the close of the FIRST US session of the month on the signal of the
     last US close of the previous month.

Funder approval: Brett Taylor, 3 Oct 2026 17:37 AEST ("do what is best for the book").
Spec: research/deep18/DEEP18_REPORT_20261003.md section 5. No broker orders are ever sent.
Fills are paper fills booked in this sleeve's ledger at real prices observed at fill time.

SIGNALS (report section 5, frozen; signal_now.py / lib.rp_trend "D bothx s0.2")
  Underlyings (total return, Yahoo adjusted closes on US trading days): S&P TR (^SP500TR),
  QQQ, VUSTX (TLT fallback), GLD, BTC-USD (aligned to US trading days); T-bill from ^IRX.
  trend_i = 0.5*[P > SMA200] + 0.5*[12m return > 12m T-bill return]        -> 0 / 0.5 / 1
  raw_i   = trend_i / 60d realised vol_i
  exposure = raw * sigma / sqrt(raw' C raw), C = 61-row (60-return) covariance x252, sigma 20%
  ETF notional_i = exposure_i / leverage_i, leverage UPRO 3, TQQQ 3, TMF 3, UGL 2, BTC 1
  R2: raw_TMF x= [^TNX < ^TNX 252 trading days earlier]   (duration gate, before the vol scaling)
  if sum(notional) > 97%: scale down pro rata
  target_usd_i = notional_i x base_usd x scale    (base_usd = firm equity unless --base-usd)
  R2: idle sleeve cash -> DBMF/KMLM/CTA equal thirds (see plan_orders); only the small residual
  sleeve cash (buffer, rounding) still accrues T-bill interest via cash_yield.accrue.

COSTS (hard rule): every fill pays 12.5bp per side (ETFs) or 25bp per side (BTC), in cash.

KILL RULE (pre-commitment; matches TREND18_SLEEVE_REGISTRATION.md): sleeve NAV/unit (flow-adjusted)
30% below its high-water mark -> sigma 10% and an intra-month `--reason kill` rebalance becomes
pending, until NAV/unit makes a new 6-month high. The kill reference re-arms at the clear (HWM since
the last clear). A review with Brett is flagged at the trigger and again when 20% is restored. The
check runs on every mark (hourly via desk_tick.sh) and at the start of every --execute. R2 backtest:
neutral (23.7% vs 23.6%, fam_final.py). It cannot prevent a one-day gap: realistic worst fall ~-50%.

MARK SANITY (TFO Risk 3 Oct 2026): a mark inside 0.6x-1.6x of the last mark is accepted. A larger move
on a levered ETF is accepted when it matches leverage x the move of its 1x underlying (SPY/QQQ/TLT/GLD,
fetched with every mark). Without that confirmation, a drop down to last/10 is accepted (the loss is
booked) and an unconfirmed jump above 1.6x or a drop below last/10 is refused. Every large move and every
refusal writes state/trend18_alerts.jsonl and shows on the dashboard; nothing fails silently.

FUNDING: paired capital_transfer with the powder book (gap_close_levered_book), the same
receipt shape tfo_crypto/tfo_buy use, so firm capital accounting nets it to zero. Never lets firm
long notional exceed firm equity less the reserve (no leverage).
FUNDING GAP RULE (Brett 3 Oct 2026 authority, enforced by TFO Risk 29 Oct 2026 14:08 WITA): Trend18 is funded
from FREED CASH ONLY (its own sells + powder above the $50k reserve), and only what is still needed at
execution. It NEVER sells another book's position (fundamental_book or any other): --sell-staged and
--sell-staged-only are refused. Every --execute re-computes the stage on live prices (state/trend18_exec_stage.json),
then resolve_funding():
  gap <= max(1% of the gross trend buys, A$2,000): trim the largest buy leg (BTC) to fit, reserve 100% intact;
      if a trim cannot close it, dip into the reserve for that amount only while >= 95% of the reserve stays.
  larger gap: scale every buy down pro rata (MF buys dropped first); logged and alerted.
The same rule is re-applied under the ledger locks on the fresh powder balance.

PRICES: ETFs from Alpaca market data feed=iex (keys from TREND18_ALPACA_ENV, ~/.secrets/alpaca_paper.env
or APCA_* env; never printed) or a --prices-file written by `trend18.py --fetch-prices` on the box.
'last' = latest IEX trade <=120s old (else IEX quote mid) while the market is open; 'close' = the IEX
daily bar of today's session after 16:00 ET. No stale or backdated fill prices. BTC: Kraken public
ticker via tfo_crypto.quote (buys at ask, sells at bid, LIVE tape, spread < 20bp).

Usage
  trend18.py --stage [--asof YYYY-MM-DD] [--scale F]          dry run: signals, targets, orders, room
  trend18.py --execute --reason entry|monthly|refresh|kill|manual --scale F [--price last|close]
             [--prices-file F] [--fit] [--mf-powder|--no-mf-powder]   (funding gap rule is automatic; --fit kept for old callers)
      monthly = first US session of the month (signal = last close of the previous month)
      refresh = middle US session of the month (same targets, drift control only)
  trend18.py --mark [--prices-file F]                         re-mark, accrue T-bill, kill check
  trend18.py --sell-staged-only ...   REFUSED since 29 Oct 2026 (never sells another book's position)
  trend18.py --fetch-prices last|close|asof [--asof D] --out F [--alpaca-env F]  (box helper)
  trend18.py --status
"""
import argparse, csv, datetime as dt, json, math, os, sys, tempfile, time, urllib.parse, urllib.request, uuid
try:
    import desk_doh; desk_doh.install(ibkr=False, oanda=False, extra=True)  # 2026-10-27 audit: Alpaca hosts
except Exception as _doh_e:
    print("desk_doh unavailable: %r" % _doh_e, file=sys.stderr)
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE, "state")
LEDGER = os.path.join(STATE_DIR, "trend18.json")
HIST = os.path.join(STATE_DIR, "trend18_history.csv")
FILLS = os.path.join(STATE_DIR, "trend18_fills.csv")
STAGE_OUT = os.path.join(STATE_DIR, "trend18_stage.json")
EXEC_STAGE = os.path.join(STATE_DIR, "trend18_exec_stage.json")     # the live re-computed stage written by every --execute
PLAN = os.path.join(STATE_DIR, "trend18_funding_plan.json")
ALERTS = os.path.join(STATE_DIR, "trend18_alerts.jsonl")
EXPO_PATH = os.path.join(STATE_DIR, "trend18_exposure.json")
LOGF = os.path.join(STATE_DIR, "trend18_log.txt")
REPORTS = os.path.join(BASE, "reports")
KEY = "trend18"
GCL = "gap_close_levered_book"
LOCK = "trend18"
POWDER_LOCK = "tfo_gcl_powder"
UTC = dt.timezone.utc
SYD = ZoneInfo("Australia/Sydney")
ET = ZoneInfo("America/New_York")

SLEEVES = [
    {"name": "SPX", "signal": "^SP500TR", "inst": "UPRO", "lev": 3, "kind": "etf"},
    {"name": "NDX", "signal": "QQQ", "inst": "TQQQ", "lev": 3, "kind": "etf"},
    {"name": "BOND_L", "signal": "VUSTX", "inst": "TMF", "lev": 3, "kind": "etf"},
    {"name": "GOLD", "signal": "GLD", "inst": "UGL", "lev": 2, "kind": "etf"},
    {"name": "BTC", "signal": "BTC-USD", "inst": "XBT/USD", "lev": 1, "kind": "crypto"},
]
TREND_ETFS = [s["inst"] for s in SLEEVES if s["kind"] == "etf"]
MF = ["DBMF", "KMLM", "CTA"]                      # R2 managed-futures basket, equal thirds
ETFS = TREND_ETFS + MF                            # everything priced from Alpaca IEX
GATE_TICKER, GATE_N = "^TNX", 252                 # R2 duration gate on TMF
LEV = {**{s["inst"]: s["lev"] for s in SLEEVES}, **{m: 1 for m in MF}}
SLEEVE_CASH_BUFFER = 2000.0                       # cash kept in the sleeve for costs / rounding
BTC = "XBT/USD"
# 2026-10-03 TFO Risk review: leverage-aware mark sanity, economic exposure and gap risk
UND = {"UPRO": "SPY", "TQQQ": "QQQ", "TMF": "TLT", "UGL": "GLD"}     # 1x underlying fetched with every mark
MARK_NORMAL_BAND = (0.6, 1.6)      # mark/last inside this: accept silently
MARK_HARD_FLOOR = 0.1              # unconfirmed drops accepted down to last/10 (book the loss, alert)
EXT_LEV = {"UPRO": 3, "SPXL": 3, "TQQQ": 3, "TMF": 3, "UGL": 2, "SSO": 2, "QLD": 2, "SOXL": 3, "TNA": 3, "TECL": 3,
           "FNGU": 3, "NUGT": 2, "BITX": 2, "ETHU": 2, "MSTU": 2, "NVDL": 2, "TSLL": 2, "UBT": 2, "TYD": 3}
GAP_SHOCK = {"SPX": -0.20, "NDX": -0.22, "BTC": -0.15}               # TFO Risk gap-risk scenario
SIGMA = 0.20
KILL_SIGMA = 0.10
KILL_DD = -0.30
KILL_RESET_DAYS = 182
VOLWIN, SMA_N, MOM_N, CAP = 60, 200, 252, 0.97
COST_SIDE = {"etf": 0.00125, "crypto": 0.0025}      # HARD RULE: 12.5bp / 25bp per side
RESERVE_USD = 50000.0
MIN_TRADE_USD = 250.0          # ignore dust rebalances (cost > benefit)
# 2026-10-29 TFO Risk 14:08 WITA (Brett 3 Oct authority): funding gap rule, see FUNDING GAP RULE in the docstring
SMALL_GAP_PCT_DEPLOY = 0.01    # small gap: <= 1% of the gross trend buys ...
SMALL_GAP_AUD = 2000.0         # ... or under A$2,000
RESERVE_MIN_INTACT = 0.95      # a small-gap reserve dip never leaves less than 95% of the $50k reserve ($47,500)
SMALL_GAP_PREFER = "trim"      # trim the largest buy leg first (reserve 100% intact); reserve dip only if a trim cannot close it
FX_AUDUSD_FALLBACK = 0.7022    # theme_exposure.DESK_FX
NO_OTHER_BOOK_SALES = ("REFUSED: Trend18 is funded from freed cash only and never sells another book's position "
                       "(Brett 3 Oct 2026 authority; TFO Risk 29 Oct 2026). Funding gaps are resolved by resolve_funding() at execution.")
PRICE_SYMS = ETFS + sorted(set(UND.values()))
# 2026-10-03 19:17 AEST Brett: funder fee lowered 0.047619%/day -> 0.0274%/day (~10%/yr) from the 4 Oct charge. The rate lives in
# funder_fee.RATE_SCHEDULE (single source); fallback constant only if that import fails. after-fee CAGR = (1+g)(1-r)^365 - 1.
try:
    from funder_fee import RATE_DAY as FUNDER_FEE_DAY, after_fee_cagr_pct as _after_fee_cagr_pct, BREAK_EVEN_PRE_FEE_PCT as FUNDER_BREAK_EVEN_PCT
except Exception:
    FUNDER_FEE_DAY = 0.000274
    FUNDER_BREAK_EVEN_PCT = 100 * ((1 - FUNDER_FEE_DAY) ** -365 - 1)
    def _after_fee_cagr_pct(g, rate=None): return 100 * ((1 + g / 100.0) * (1 - FUNDER_FEE_DAY) ** 365 - 1)
_FEE_TXT = "funder fee %.4f%%/day (pre-fee break-even %.1f%%)" % (100 * FUNDER_FEE_DAY, FUNDER_BREAK_EVEN_PCT)
R1G_LABEL = {"name": "R1 + bond gate", "cagr_pct": 21.0, "after_fee_cagr_pct": round(_after_fee_cagr_pct(21.0), 1), "p5_below_pct": 43,
             "p5_below_basis": "at the old 0.047619%/day fee (not rerun at 0.0274%; lower now)", "fee_pct_day": round(100 * FUNDER_FEE_DAY, 4),
             "basis": "DEEP18 round 2: R1 + duration gate, drift-aware 1988-2026 21.0%% pre-fee = %.1f%% after the %s; "
                      "P(5y below start after fee) ~43%% at the old 0.047619%%/day fee (R1 drift-aware)" % (_after_fee_cagr_pct(21.0), _FEE_TXT)}
R2_LABEL = {"name": "R2 (bond gate + MF basket)", "cagr_pct": 23.2, "after_fee_cagr_pct": round(_after_fee_cagr_pct(23.2), 1), "p5_below_pct": 35,
            "p5_below_basis": "at the old 0.047619%/day fee (not rerun at 0.0274%; lower now)", "fee_pct_day": round(100 * FUNDER_FEE_DAY, 4),
            "basis": "DEEP18 round 2 official: drift-aware, trade next close, 1988-2026 23.2%% pre-fee = %.1f%% after the %s; "
                     "P(5y below start after fee) 35%% at the old 0.047619%%/day fee" % (_after_fee_cagr_pct(23.2), _FEE_TXT)}
MF_FUNDED_FOR_R2 = 0.80        # show R2 figures only once MF >= 80% of its target
PRICE_FILE_MAX_AGE_S = 600
ALPACA_DATA = "https://data.alpaca.markets"
ALPACA_PAPER = "https://paper-api.alpaca.markets"
RULE_TEXT = ("DEEP18 trend sleeve, R2 design: 0.5xSMA200 + 0.5x excess 12m momentum; inverse 60d vol; 60d covariance "
             "to 20% vol; leverage UPRO3/TQQQ3/TMF3/UGL2/BTC1; 97% notional cap; TMF also needs the duration gate "
             "(^TNX below its level 252 trading days earlier); signals at the last US close of the month, traded at "
             "the next close (first session of the month); mid-month refresh to the same targets on the middle "
             "session; idle sleeve cash in DBMF/KMLM/CTA thirds (never funded by selling desk positions); costs "
             "12.5bp/side ETF, 25bp/side BTC; kill at -30% NAV/unit -> 10% vol until a new 6-month high, review "
             "with Brett; the kill reference re-arms at the clear; checked on every mark and at the start of every execute. "
             "LEVERED: economic exposure ~2.5x the trend legs' notional. Realistic worst fall about -50% (Oct 1987 in sample: "
             "-41.6% that month, max DD -49.4%). PAPER ONLY.")

def now_utc(): return dt.datetime.now(UTC)
def iso(t=None): return (t or now_utc()).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
def syd(t=None, fmt="%Y-%m-%d %H:%M %Z"): return (t or now_utc()).astimezone(SYD).strftime(fmt)
def parse_ts(s):
    s = str(s).replace("Z", "+00:00")
    if "." in s:  # Alpaca sends nanoseconds; fromisoformat takes <= 6 digits
        head, rest = s.split(".", 1)
        frac = "".join(ch for ch in rest if ch.isdigit())
        tz = rest[len(frac):]
        s = head + "." + frac[:6] + tz
    return dt.datetime.fromisoformat(s)

def atomic_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix="." + os.path.basename(path), suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=1, default=str); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)

def log(line):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(LOGF, "a") as f: f.write("%s %s\n" % (syd(), line))

# ------------------------------------------------------------------ US session calendar (shared with shadow_nasdaq2x)
def _cal():
    sys.path.insert(0, BASE)
    import shadow_nasdaq2x as s
    return s

def is_session(d): return _cal().is_session(d)
def last_session_of_month(d): return _cal().last_session_of_month(d)
def sessions_of_month(d):
    x = d.replace(day=1); out = []
    while x.month == d.month:
        if is_session(x): out.append(x)
        x += dt.timedelta(days=1)
    return out
def first_session_of_month(d): return sessions_of_month(d)[0]
def mid_session_of_month(d):
    """R2 refresh day (fam_final.mids): the US trading days of the month, element len // 2 (0-indexed)."""
    s = sessions_of_month(d); return s[len(s) // 2]

# ------------------------------------------------------------------ Alpaca market data (keys never printed)
def alpaca_headers(env_path=None):
    kid, sec = os.environ.get("APCA_API_KEY_ID"), os.environ.get("APCA_API_SECRET_KEY")
    if not (kid and sec):
        for p in (env_path, os.environ.get("TREND18_ALPACA_ENV"), os.path.expanduser("~/.secrets/alpaca_paper.env"),
                  "/home/box/.secrets/alpaca_paper.env"):
            if p and os.path.isfile(p):
                env = {}
                for ln in open(p):
                    ln = ln.strip()
                    if ln and not ln.startswith("#") and "=" in ln:
                        k, v = ln.split("=", 1); env[k.strip().replace("export ", "")] = v.strip().strip('"').strip("'")
                kid, sec = env.get("APCA_API_KEY_ID"), env.get("APCA_API_SECRET_KEY")
                if kid and sec: break
    if not (kid and sec): return None
    return {"APCA-API-KEY-ID": kid, "APCA-API-SECRET-KEY": sec, "User-Agent": "TFO-trend18/1.0"}

def _aget(h, url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=20) as r:
        return json.loads(r.read().decode())

def fetch_alpaca(mode, syms, asof=None, env_path=None):
    """{sym: {px, utc, kind}} from Alpaca feed=iex. Refuses stale/backdated prices for execution modes."""
    h = alpaca_headers(env_path)
    if not h: raise RuntimeError("no Alpaca paper keys on this machine (set TREND18_ALPACA_ENV or use --prices-file)")
    now = now_utc(); et = now.astimezone(ET); out = {}; js = ",".join(syms)
    if mode == "last":
        clk = _aget(h, ALPACA_PAPER + "/v2/clock")
        if not clk.get("is_open"): raise RuntimeError("US market is closed: 'last' price refused (no stale fills)")
        tr = _aget(h, ALPACA_DATA + "/v2/stocks/trades/latest?symbols=%s&feed=iex" % js).get("trades", {})
        qu = _aget(h, ALPACA_DATA + "/v2/stocks/quotes/latest?symbols=%s&feed=iex" % js).get("quotes", {})
        for s in syms:
            t, q = tr.get(s), qu.get(s)
            age = (now - parse_ts(t["t"])).total_seconds() if t else 1e9
            if t and age <= 120 and t.get("p", 0) > 0:
                out[s] = {"px": float(t["p"]), "utc": t["t"], "kind": "iex_last_trade", "age_s": round(age, 1)}
                continue
            if q and q.get("bp", 0) > 0 and q.get("ap", 0) >= q["bp"]:
                mid = (q["ap"] + q["bp"]) / 2; spr = (q["ap"] - q["bp"]) / mid * 1e4
                qage = (now - parse_ts(q["t"])).total_seconds()
                if spr <= 50 and qage <= 120:
                    out[s] = {"px": round(mid, 4), "utc": q["t"], "kind": "iex_quote_mid", "spread_bp": round(spr, 1),
                              "age_s": round(qage, 1)}
                    continue
            raise RuntimeError("%s: no IEX trade <=120s and no tight fresh IEX quote" % s)
    elif mode in ("close", "asof"):
        if mode == "close":
            d = et.date()
            cal = _aget(h, ALPACA_PAPER + "/v2/calendar?start=%s&end=%s" % (d.isoformat(), d.isoformat()))
            if not cal or cal[0].get("date") != d.isoformat() or (et.hour, et.minute) < (16, 1):
                raise RuntimeError("'close' needs today's US session to have closed (after 16:01 ET)")
        else:
            d = dt.date.fromisoformat(asof)
        bars = _aget(h, ALPACA_DATA + "/v2/stocks/bars?symbols=%s&timeframe=1Day&start=%s&end=%s&feed=iex&adjustment=raw"
                     % (js, d.isoformat(), d.isoformat())).get("bars", {})
        for s in syms:
            b = (bars.get(s) or [None])[-1]
            if not b or parse_ts(b["t"]).astimezone(ET).date() != d:
                raise RuntimeError("%s: no IEX daily bar for %s" % (s, d))
            out[s] = {"px": float(b["c"]), "utc": b["t"], "kind": "iex_daily_close", "session": d.isoformat()}
    else:
        raise ValueError(mode)
    return out

# 2026-10-21: network timeouts. yfinance's fast_info has no timeout and a dead Yahoo
# connection hung `trend18.py --mark` (and with it the whole desk tick). Every Yahoo
# call now carries timeout= where yfinance supports it AND runs under a hard
# wall-clock cap; a timeout raises, which the existing fallbacks already handle.
import socket as _socket, threading as _threading
YF_TIMEOUT_S = 15
YF_HARD_CAP_S = 45
_socket.setdefaulttimeout(30)          # stdlib sockets (urllib paths) never wait forever

def _bounded(fn, *a, cap=None, **kw):
    cap = YF_HARD_CAP_S if cap is None else cap
    box = {}
    def w():
        try: box["v"] = fn(*a, **kw)
        except BaseException as e: box["e"] = e   # noqa: BLE001 - re-raised
    t = _threading.Thread(target=w, daemon=True); t.start(); t.join(cap)
    if t.is_alive(): raise TimeoutError("Yahoo call exceeded %ss" % cap)
    if "e" in box: raise box["e"]
    return box["v"]

def yahoo_prices(syms, asof=None):
    import yfinance as yf
    out = {}
    if asof:
        d = dt.date.fromisoformat(asof)
        df = _bounded(yf.download, syms, start=(d - dt.timedelta(days=7)).isoformat(), end=(d + dt.timedelta(days=1)).isoformat(),
                      auto_adjust=False, progress=False, threads=True, timeout=YF_TIMEOUT_S)["Close"]
        for s in syms:
            col = df[s] if hasattr(df, "columns") and s in df.columns else df
            col = col.dropna(); col = col[[x.date() == d for x in col.index]]
            if len(col): out[s] = {"px": float(col.iloc[-1]), "utc": d.isoformat(), "kind": "yahoo_close"}
    else:
        for s in syms:
            try:
                p = float(_bounded(lambda: yf.Ticker(s).fast_info["last_price"], cap=YF_TIMEOUT_S))
                if p > 0: out[s] = {"px": p, "utc": iso(), "kind": "yahoo_last"}
            except Exception: pass
    return out

def load_prices_file(path, need_fresh=True):
    d = json.load(open(path))
    age = (now_utc() - parse_ts(d["generated_utc"])).total_seconds()
    if need_fresh and age > PRICE_FILE_MAX_AGE_S:
        raise RuntimeError("prices file %s is %.0fs old (> %ds): refused" % (path, age, PRICE_FILE_MAX_AGE_S))
    return d["prices"], d.get("mode"), d

def kraken_btc(asof=None):
    if asof:
        y = yahoo_prices(["BTC-USD"], asof).get("BTC-USD")
        if not y: raise RuntimeError("no BTC-USD close for %s" % asof)
        return {"bid": y["px"], "ask": y["px"], "mid": y["px"], "tape": "ASOF", "spread_bp": 0.0, "utc": y["utc"],
                "source": "Yahoo BTC-USD daily close (stage only)"}
    sys.path.insert(0, BASE)
    import tfo_crypto  # existing crypto booking path: Kraken public Ticker+Trades
    return tfo_crypto.quote(BTC)

# ------------------------------------------------------------------ signals
def tnx_gate(asof=None):
    """R2 duration gate inputs for --fetch-prices (audit copy; the signal itself is recomputed in compute_signals).
    ^TNX is an index (not on Alpaca): yfinance if importable, else Yahoo's public chart endpoint."""
    closes = None; src = None
    try:
        import yfinance as yf
        h = _bounded(yf.Ticker(GATE_TICKER).history, period="3y", interval="1d", auto_adjust=False, timeout=YF_TIMEOUT_S)["Close"].dropna()
        closes = [(i.date().isoformat(), float(v)) for i, v in h.items()]; src = "yfinance"
    except Exception:
        try:
            url = "https://query1.finance.yahoo.com/v8/finance/chart/%5ETNX?range=3y&interval=1d"
            r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20)
            j = json.load(r)["chart"]["result"][0]
            ts = j["timestamp"]; cl = j["indicators"]["quote"][0]["close"]
            closes = [(dt.datetime.fromtimestamp(t, ET).date().isoformat(), float(c)) for t, c in zip(ts, cl) if c is not None]; src = "yahoo chart api"
        except Exception as e:
            return {"error": "no ^TNX source: %s" % str(e)[:120]}
    if asof: closes = [x for x in closes if x[0] <= asof]
    if len(closes) <= GATE_N: return {"error": "not enough ^TNX history"}
    d, v = closes[-1]; d0, v0 = closes[-1 - GATE_N]
    return {"ticker": GATE_TICKER, "asof": d, "tnx": round(v, 3), "ref_date": d0, "tnx_252_ago": round(v0, 3),
            "open": bool(v < v0), "source": src, "rule": "TMF allowed only if ^TNX < its value 252 trading days earlier"}

def compute_signals(asof=None, sigma=SIGMA):
    import numpy as np, pandas as pd, yfinance as yf
    tick = [s["signal"] for s in SLEEVES] + ["^IRX", "SPY", "TLT", GATE_TICKER]
    raw = _bounded(yf.download, tick, period="3y", interval="1d", auto_adjust=True, progress=False, threads=True,
                   timeout=YF_TIMEOUT_S, cap=120)["Close"]
    raw.index = pd.to_datetime(raw.index).tz_localize(None).normalize()
    cutoff = pd.Timestamp(asof) if asof else None
    if cutoff is not None: raw = raw[raw.index <= cutoff]
    spx = raw["^SP500TR"].dropna()
    us_idx = raw["QQQ"].dropna().index                     # US trading days
    if spx.index[-1] < us_idx[-1]:                         # index print lags: extend with SPY TR returns
        spy = raw["SPY"].dropna(); ext = spy[spy.index > spx.index[-1]]
        if len(ext):
            spx = pd.concat([spx, spx.iloc[-1] * ext / spy[spy.index <= spx.index[-1]].iloc[-1]])
    bond = raw["VUSTX"].dropna()
    if bond.index[-1] < us_idx[-1]:
        tlt = raw["TLT"].dropna(); ext = tlt[tlt.index > bond.index[-1]]
        if len(ext): bond = pd.concat([bond, bond.iloc[-1] * ext / tlt[tlt.index <= bond.index[-1]].iloc[-1]])
    series = {"SPX": spx, "NDX": raw["QQQ"].dropna(), "BOND_L": bond, "GOLD": raw["GLD"].dropna(), "BTC": raw["BTC-USD"].dropna()}
    idx = us_idx
    P = pd.DataFrame({k: s.reindex(s.index.union(idx)).ffill().reindex(idx) for k, s in series.items()})
    R = P.pct_change()
    irx = raw["^IRX"].dropna() / 100.0
    rf = ((1 + irx) ** (1 / 252) - 1).reindex(irx.index.union(idx)).ffill().reindex(idx).fillna(0)
    crf = (1 + rf).cumprod()
    d = idx[-1]
    sma = P.rolling(SMA_N).mean().loc[d]
    xm = (P.loc[d] / P.shift(MOM_N).loc[d]) - (crf.loc[d] / crf.shift(MOM_N).loc[d])
    above = P.loc[d] > sma
    tr = 0.5 * above.astype(float) + 0.5 * (xm > 0).astype(float)
    tr = tr.where(sma.notna(), 0.0)
    vol = R.iloc[-VOLWIN:].std() * math.sqrt(252)
    # R2 duration gate (r2lib final_r2.gate): TNX below its value 252 TNX observations earlier, ffilled to d
    tnx = raw[GATE_TICKER].dropna()
    g_series = (tnx < tnx.shift(GATE_N)).astype(float).where(tnx.shift(GATE_N).notna())
    g_at = g_series.reindex(g_series.index.union(idx)).ffill().reindex(idx)
    gate_open = bool(g_at.loc[d] == 1.0) if not pd.isna(g_at.loc[d]) else True
    tnx_d = float(tnx[tnx.index <= d].iloc[-1]); tnx_prev = float(tnx.shift(GATE_N)[tnx.index <= d].iloc[-1])
    b = tr / vol
    if not gate_open: b["BOND_L"] = 0.0
    act = b[b > 0]
    e = pd.Series(0.0, index=P.columns); pv = None
    if len(act):
        cov = R.iloc[-(VOLWIN + 1):][act.index].fillna(0).cov().values * 252
        pv = float(math.sqrt(act.values @ cov @ act.values))
        e.loc[act.index] = act * sigma / pv
    lev = pd.Series({s["name"]: s["lev"] for s in SLEEVES})
    w = e / lev
    capped = bool(w.sum() > CAP)
    if capped: w = w * CAP / w.sum()
    out = {"asof": d.date().isoformat(), "sigma": sigma, "raw_portfolio_vol": pv, "capped": capped,
           "duration_gate": {"open": gate_open, "tnx": round(tnx_d, 3), "tnx_252_ago": round(tnx_prev, 3),
                             "rule": "TMF allowed only if ^TNX < its value 252 trading days earlier"},
           "sum_notional": round(float(w.sum()), 6), "data_last": {k: s.index[-1].date().isoformat() for k, s in series.items()},
           "sleeves": []}
    for s in SLEEVES:
        n = s["name"]
        out["sleeves"].append({"name": n, "signal": s["signal"], "inst": s["inst"], "lev": s["lev"], "kind": s["kind"],
                               "px_signal": round(float(P.loc[d, n]), 4), "sma200": round(float(sma[n]), 4),
                               "above_sma200": bool(above[n]), "xs12m": round(float(xm[n]), 4), "trend": float(tr[n]),
                               "gate": (gate_open if n == "BOND_L" else None),
                               "vol60": round(float(vol[n]), 4), "exposure": round(float(e[n]), 4),
                               "notional_w": round(float(w[n]), 6)})
    return out

# ------------------------------------------------------------------ ledgers
def new_ledger():
    return {"book": KEY, "label": "DEEP18 Trend Sleeve (levered risk-parity trend)", "kind": "sleeve", "mode": "PAPER",
            "paper_only": True, "label_alpha_beta": "beta + known trend premium (report section 4); NOT alpha",
            "registration": "research/deep18/DEEP18_REPORT_20261003.md section 5",
            "funder_approval": "Brett Taylor 2026-10-03 17:37 AEST: run DEEP18 as a PAPER sleeve; half at Mon 5 Oct US close, rest at 30 Oct",
            "rule": RULE_TEXT, "created_utc": iso(), "start": 0.0, "seed_usd": 0.0, "cash": 0.0, "equity": 0.0, "peak": 0.0,
            "holdings": {}, "entry_px": {}, "mark_px_usd": {}, "mark_utc": {}, "mark_source": {},
            "units": 0.0, "nav_per_unit": 1.0, "peak_nav_per_unit": 1.0, "drawdown_pct": 0.0,
            "kill": {"active": False, "pending_rebalance": False}, "review_flag": None,
            "scale_current": 0.0, "sigma_current": SIGMA, "costs_paid_usd": 0.0, "realised_usd": 0.0,
            "cash_interest_total_usd": 0.0, "cash_yield": {}, "capital_events": [], "fills": [], "rebalances": [],
            "last_signals": None}

def load_ledger():
    if not os.path.exists(LEDGER): return new_ledger()
    return json.load(open(LEDGER))

def save_ledger(L):
    L["rule"] = RULE_TEXT        # always the current rule text (TFO Risk 3 Oct 2026)
    L["registration"] = "TREND18_SLEEVE_REGISTRATION.md; research/deep18/DEEP18_ROUND2_20261003.md section 4 (R2)"
    L["label"] = "DEEP18 Trend Sleeve R2 (LEVERED risk-parity trend)"
    L["updated"] = iso(); atomic_json(LEDGER, L)

def powder_path(): return os.path.join(REPORTS, GCL + ".json")
def load_powder(): return json.load(open(powder_path()))
def save_powder(g):
    fd, tmp = tempfile.mkstemp(dir=REPORTS, prefix="." + GCL, suffix=".tmp")
    with os.fdopen(fd, "w") as f: json.dump(g, f, indent=1, default=str)
    os.replace(tmp, powder_path())

def powder_available(g):
    unres = float(g.get("cash") or 0) - float(g.get("reserved_usd") or 0)
    return unres, unres - RESERVE_USD

def firm_equity():
    """2026-10-29 Brett: Trend18 sizes off firm equity EX veteran_desk (desk_caps.sizing_equity: books are never
    merged; base floats with the pre-28-Oct composition). Fails closed when the per-book breakdown is missing/stale."""
    sys.path.insert(0, BASE)
    import desk_caps
    se = desk_caps.sizing_equity(REPORTS)
    return float(se["sizing_equity_usd"]), se["source"], se["state"]

def firm_long_notional():
    """Gross long marked notional across every registered book (book_coverage, the firm's single list)."""
    sys.path.insert(0, BASE)
    import io, contextlib, book_coverage
    with contextlib.redirect_stdout(io.StringIO()):
        g = book_coverage.gather(verbose=False)
    longs = sum(p["fallback_usd"] for p in g["positions"] if p.get("side", "long") == "long")
    shorts = sum(p["fallback_usd"] for p in g["positions"] if p.get("side") == "short")
    overlap = [{"book": p["book"], "sym": p["sym"], "usd": round(p["fallback_usd"], 2)} for p in g["positions"]
               if p["book"] != KEY and (p["sym"] in ETFS + ["SGOV", BTC, "BTC/USD", "BTC-USD", "XXBTZUSD"]
                                        or str(p.get("ticker")) in ("BTC-USD",))]
    return longs, shorts, overlap, g

def mark_value(L):
    return sum(float(u) * float(L["mark_px_usd"].get(s) or L["entry_px"].get(s) or 0) for s, u in L["holdings"].items())

def restrike(L):
    L["equity"] = round(float(L["cash"]) + mark_value(L), 2)
    if L["units"] > 1e-9:
        L["nav_per_unit"] = L["equity"] / L["units"]
    L["peak_nav_per_unit"] = max(float(L.get("peak_nav_per_unit") or 1.0), float(L["nav_per_unit"]))
    # kill reference: HWM since the last kill clear (re-armed at the clear; without this the rule flaps
    # CLEAR -> KILL the next day because a 6-month high can still sit >30% under the all-time HWM)
    L["kill_ref_peak_nav"] = max(float(L.get("kill_ref_peak_nav") or 1.0), float(L["nav_per_unit"]))
    L["drawdown_pct"] = round((L["nav_per_unit"] / L["peak_nav_per_unit"] - 1) * 100, 3)
    L["peak"] = round(L["peak_nav_per_unit"] * L["units"], 2)       # flow-adjusted dollar high-water mark
    L["drawdown_basis"] = "NAV per unit (units issued/redeemed at NAV on every capital transfer): flows are not P&L"

def xfer(L, g, usd, utc, tag, note):
    """usd > 0: powder -> trend18; usd < 0: trend18 -> powder. Paired receipts (same utc, opposite usd)."""
    usd = round(usd, 2)
    if abs(usd) < 0.005: return
    restrike(L)
    nav = L["nav_per_unit"] if L["units"] > 1e-9 else 1.0
    ev = {"type": "capital_transfer", "utc": utc, "from": GCL if usd > 0 else KEY, "to": KEY if usd > 0 else GCL,
          "tag": tag, "note": note, "paper_only": True}
    before = float(g["cash"]); g["cash"] = round(before - usd, 2)
    g.setdefault("capital_events", []).append({**ev, "usd": -usd, "cash_before": before, "cash_after": g["cash"]})
    g["seed_usd"] = round(float(g["seed_usd"]) - usd, 2); g["start"] = round(float(g["start"]) - usd, 2)
    g["equity"] = g["cash"] if not (g.get("holdings") or {}) else round(float(g.get("equity") or 0) - usd, 2)
    g["updated"] = utc
    L["capital_events"].append({**ev, "usd": usd, "nav_per_unit": round(nav, 8), "units_delta": round(usd / nav, 8)})
    L["cash"] = round(float(L["cash"]) + usd, 2)
    L["start"] = round(float(L["start"]) + usd, 2); L["seed_usd"] = L["start"]
    L["units"] = float(L["units"]) + usd / nav
    if L["units"] < 1e-9: L["units"] = 0.0
    try:
        import audit
        audit.capital("trend18", {"type": "capital_transfer", "usd": usd, "tag": tag, "note": note, "paper_only": True})
    except Exception:
        pass

def accrue_cash(L):
    c = float(L.get("cash") or 0)
    if c <= 0: return 0.0
    try:
        sys.path.insert(0, BASE); import cash_yield
        cy = L.setdefault("cash_yield", {})
        prior = cy.get("cash_yield_last_epoch")
        if isinstance(prior, (int, float)) and time.time() - prior > 7200:   # same no-back-credit rule as cash_yield_books
            cy.pop("cash_yield_last_epoch", None); cy["cash_yield_gap_skipped"] = {"since_epoch": prior, "resumed_epoch": time.time()}
        got = float(cash_yield.accrue(cy, c) or 0.0)
    except Exception as e:
        print("CASH-YIELD unavailable (%s) - accrued ZERO" % e); return 0.0
    if got > 0:
        L["cash"] = round(c + got, 6); L["cash_interest_total_usd"] = round(float(L.get("cash_interest_total_usd") or 0) + got, 6)
    return got

HIST_HDR = ["utc", "syd", "event", "equity_usd", "cash_usd", "units", "nav_per_unit", "peak_nav_per_unit", "drawdown_pct",
            "kill_active", "sigma", "scale"] + ["%s_usd" % s["inst"].replace("/", "") for s in SLEEVES] + ["%s_usd" % m for m in MF]

def hist_append(L, event):
    new = not os.path.exists(HIST)
    with open(HIST, "a", newline="") as f:
        w = csv.writer(f)
        if new: w.writerow(HIST_HDR)
        vals = [round(float(L["holdings"].get(i, 0)) * float(L["mark_px_usd"].get(i) or 0), 2) for i in [s["inst"] for s in SLEEVES] + MF]
        w.writerow([iso(), syd(), event, L["equity"], round(L["cash"], 2), round(L["units"], 6), round(L["nav_per_unit"], 6),
                    round(L["peak_nav_per_unit"], 6), L["drawdown_pct"], L["kill"].get("active"), L["sigma_current"],
                    L["scale_current"]] + vals)

def alert(kind, text, L):
    rec = {"utc": iso(), "syd": syd(), "kind": kind, "text": text, "nav_per_unit": L.get("nav_per_unit"),
           "drawdown_pct": L.get("drawdown_pct"), "paper_only": True}
    with open(ALERTS, "a") as f: f.write(json.dumps(rec) + "\n")
    print("!!! TREND18 %s: %s" % (kind, text)); log("ALERT %s %s" % (kind, text))

def six_month_high(L):
    if not os.path.exists(HIST): return None
    cut = now_utc() - dt.timedelta(days=KILL_RESET_DAYS); best = None
    for r in csv.DictReader(open(HIST)):
        try:
            if parse_ts(r["utc"]) >= cut and float(r["units"]) > 0:
                v = float(r["nav_per_unit"]); best = v if best is None else max(best, v)
        except Exception: pass
    return best

def kill_check(L):
    """-30% NAV/unit from HWM -> sigma 10% (pending intra-month rebalance) until a new 6-month high."""
    if L["units"] <= 1e-9: return
    k = L.setdefault("kill", {"active": False, "pending_rebalance": False})
    dd = L["nav_per_unit"] / float(L.get("kill_ref_peak_nav") or L["peak_nav_per_unit"]) - 1
    if not k.get("active") and dd <= KILL_DD:
        k.update(active=True, pending_rebalance=True, triggered_utc=iso(), nav_at_trigger=L["nav_per_unit"], dd_at_trigger=round(dd * 100, 2))
        L["sigma_current"] = KILL_SIGMA
        L["review_flag"] = {"utc": iso(), "reason": "KILL: sleeve %.1f%% below its high-water mark; sigma halved to 10%% until a new 6-month high. Review with Brett." % (dd * 100)}
        alert("KILL_TRIGGERED", L["review_flag"]["reason"], L)
    elif k.get("active"):
        hi = six_month_high(L)
        if hi is not None and L["nav_per_unit"] > hi and parse_ts(k["triggered_utc"]) < now_utc() - dt.timedelta(days=1):
            k.update(active=False, pending_rebalance=False, cleared_utc=iso(), nav_at_clear=L["nav_per_unit"])
            L["kill_ref_peak_nav"] = L["nav_per_unit"]      # re-arm: next kill needs a fresh -30% from here
            L["sigma_current"] = SIGMA
            L["review_flag"] = {"utc": iso(), "reason": "Kill cleared at a new 6-month NAV high; sigma back to 20% from the next rebalance. Review with Brett."}
            alert("KILL_CLEARED_REVIEW", L["review_flag"]["reason"], L)

# ------------------------------------------------------------------ planning
def mf_cap_usd(sig_or_w, base_usd, scale):
    w = sum(s["notional_w"] for s in sig_or_w["sleeves"])
    return max(0.0, scale * (base_usd * (1 - w) - RESERVE_USD))

def scale_up_guard(sig, scale, prev_scale):
    """Brett 17 Oct 2026 14:27 (item 9): the 30 Oct second-half funding (any scale-UP above the current scale) happens only if
    the trend signal is still on, meaning at least one sleeve has trend > 0 and a positive target weight on the signal used for
    that execution. With every sleeve off, the extra capital would only flow to the MF basket, so the scale-up is refused (the run can still go ahead at
    the current scale). Returns None when allowed, otherwise the refusal text."""
    if scale <= float(prev_scale or 0): return None
    on = [x["name"] for x in (sig or {}).get("sleeves", []) if float(x.get("trend") or 0) > 0 and float(x.get("notional_w") or 0) > 0]
    if on: return None
    return ("SECOND-HALF GUARD: scale-up %.2f -> %.2f refused, trend signal OFF on every sleeve (asof %s); "
            "rerun at --scale %.2f" % (float(prev_scale or 0), scale, (sig or {}).get("asof"), float(prev_scale or 0)))

# Brett 28 Oct 2026 ~11:41 WITA (tech-risk cut): the 30 Oct second-half deploy (any scale-UP above scale_current) adds NO
# UPRO/TQQQ. Held first-half qty is kept (no forced sale); the freed money is NOT re-spread above any other leg's normal target:
# UGL/BTC/TMF(gate) still go only to their normal targets, the MF basket only to its normal cap; anything left stays sleeve cash.
# Config: state/trend18_no_add.json {"enabled": true, "no_add_tickers": ["UPRO","TQQQ"], "applies_to": "second_half_scale_up"}
NO_ADD_CFG = os.path.join(STATE_DIR, "trend18_no_add.json")
NO_ADD_DEFAULT = {"enabled": True, "no_add_tickers": ["UPRO", "TQQQ"], "applies_to": "second_half_scale_up",
                  "reason": "Brett tech-risk cut 28 Oct"}

def no_add_tickers(scale, L, cfg_path=None):
    """Tickers that may not be ADDED on this run (empty unless the run is a scale-up above the ledger's scale_current)."""
    try: cfg = json.load(open(cfg_path or NO_ADD_CFG))
    except Exception: cfg = NO_ADD_DEFAULT
    if not cfg.get("enabled"): return set()
    if cfg.get("applies_to", "second_half_scale_up") == "second_half_scale_up" and not (scale > float((L or {}).get("scale_current") or 0)):
        return set()
    return {str(t).upper() for t in cfg.get("no_add_tickers", [])}

def plan_orders(sig, base_usd, scale, L, etf_px, btc_q, powder_avail=0.0, mf_powder=False, cap_room=None, mf_enabled=True, no_add=None):
    """Trend legs first (exact report section 5 targets). Then the R2 MF basket from what is left:
    sleeve cash + current MF value (+ free powder above the reserve when mf_powder) - trend need - buffer,
    capped at the R2 idle-cash target and at the firm invested-cap room. MF never creates a funding gap."""
    no_add = no_add_tickers(scale, L) if no_add is None else {str(t).upper() for t in no_add}
    try:   # 2026-10-28 13:40 Brett approve-all #2: a TRAIL_WEEKLY-stopped 3x ETF is not re-bought (target 0) until cleared
        import trend18_trail as _tt; trail_stopped = _tt.stopped_tickers()
    except Exception: trail_stopped = set()
    rows = []
    def row(inst, kind, tgt_usd, w, px_b, px_s, step):
        cur = float(L["holdings"].get(inst, 0.0))
        tgt_q = (math.floor(tgt_usd / px_b) if step == 1 else math.floor(tgt_usd / px_b * 1e5) / 1e5) if tgt_usd > 0 else 0
        dq = tgt_q - cur
        fill_px = px_b if dq > 0 else px_s
        notional = abs(dq) * fill_px
        if notional < MIN_TRADE_USD and tgt_q > 0: dq, notional = 0, 0.0
        return {"inst": inst, "kind": kind, "leg": "mf" if inst in MF else "trend", "target_w": w, "target_usd": round(tgt_usd, 2),
                "px": round(fill_px, 4), "target_qty": tgt_q, "current_qty": cur, "order_qty": dq,
                "side": "BUY" if dq > 0 else ("SELL" if dq < 0 else "-"), "notional_usd": round(notional, 2),
                "cost_usd": round(notional * COST_SIDE[kind], 2)}
    for s in sig["sleeves"]:
        inst, kind = s["inst"], s["kind"]
        tgt = s["notional_w"] * base_usd * scale
        if inst.upper() in trail_stopped and not float(L["holdings"].get(inst, 0.0)): tgt = 0.0
        if kind == "etf":
            p = float(etf_px[inst]["px"])
            if inst in no_add:
                held = float(L["holdings"].get(inst, 0.0))
                if math.floor(tgt / p) > held:   # would ADD -> hold the first-half qty exactly (no add, no forced sale)
                    rows.append(dict(row(inst, kind, held * p, s["notional_w"], p, p, 1), no_add=True,
                                     normal_target_usd=round(tgt, 2), note="no-add (Brett tech-risk cut 28 Oct): held, not added"))
                    continue
            rows.append(row(inst, kind, tgt, s["notional_w"], p, p, 1))
        else:
            rows.append(row(inst, kind, tgt, s["notional_w"], float(btc_q["ask"]), float(btc_q["bid"]), 0))
    def flows(rs):
        b = sum(r["notional_usd"] for r in rs if r["side"] == "BUY"); se = sum(r["notional_usd"] for r in rs if r["side"] == "SELL")
        c = sum(r["cost_usd"] for r in rs if r["side"] != "-"); bc = sum(r["cost_usd"] for r in rs if r["side"] == "BUY")
        return b, se, c, b + bc - (se - (c - bc))
    tb, ts, tc, trend_need = flows(rows)
    cash = float(L.get("cash") or 0)
    mf_val = sum(float(L["holdings"].get(m, 0)) * float(etf_px[m]["px"]) for m in MF if m in etf_px)
    trend_net_buy = tb - ts
    budget = cash + mf_val - trend_need - SLEEVE_CASH_BUFFER
    if mf_powder: budget += max(0.0, powder_avail)
    cap = mf_cap_usd(sig, base_usd, scale)
    if cap_room is not None:   # firm long notional must stay <= equity - reserve: MF adds long notional
        cap = min(cap, max(0.0, cap_room - trend_net_buy + mf_val))
    mf_missing = [m for m in MF if m not in etf_px]
    if mf_missing: mf_enabled = False      # never trade a lopsided basket; held legs without a price abort below
    mf_total = max(0.0, min(budget / (1 + COST_SIDE["etf"]), cap)) if mf_enabled else 0.0
    for m in MF:
        p = float(etf_px[m]["px"]) if m in etf_px else None
        if p is None:
            if float(L["holdings"].get(m, 0)): raise RuntimeError("no price for held MF leg %s" % m)
            continue
        rows.append(row(m, "etf", mf_total / 3.0, round(mf_total / 3.0 / base_usd, 6) if base_usd else 0, p, p, 1))
    b, se, c, need = flows(rows)
    return {"rows": rows, "buys_usd": round(b, 2), "sells_usd": round(se, 2), "costs_usd": round(c, 2),
            "cash_need_usd": round(need - cash, 2), "trend_cash_need_usd": round(trend_need - cash, 2),
            "trend_buys_usd": round(tb, 2), "trend_sells_usd": round(ts, 2),
            "mf_target_usd": round(mf_total, 2), "mf_cap_usd": round(mf_cap_usd(sig, base_usd, scale), 2),
            "mf_current_usd": round(mf_val, 2), "mf_budget_usd": round(budget, 2), "mf_powder_allowed": bool(mf_powder),
            "mf_missing_prices": mf_missing, "no_add_tickers": sorted(no_add)}

def fit_orders(plan, avail):
    """Scale TREND buys down pro rata so cash need <= avail. MF buys are dropped first (they replace T-bills)."""
    for r in plan["rows"]:
        if r["leg"] == "mf" and r["side"] == "BUY":
            r.update(order_qty=0, notional_usd=0.0, cost_usd=0.0, side="-", fit_note="MF buy dropped first")
    sells_net = sum(r["notional_usd"] - r["cost_usd"] for r in plan["rows"] if r["side"] == "SELL")
    buy_gross = sum(r["notional_usd"] + r["cost_usd"] for r in plan["rows"] if r["side"] == "BUY")
    room = avail + sells_net
    f = 1.0
    if buy_gross > room and buy_gross > 0:
        f = max(0.0, room / buy_gross)
        for r in plan["rows"]:
            if r["side"] != "BUY": continue
            q = r["order_qty"] * f
            q = math.floor(q) if r["kind"] == "etf" else math.floor(q * 1e5) / 1e5
            r["order_qty"] = q; r["notional_usd"] = round(q * r["px"], 2); r["cost_usd"] = round(r["notional_usd"] * COST_SIDE[r["kind"]], 2)
            r["fit_factor"] = round(f, 4)
            if q <= 0: r["side"] = "-"
    plan["buys_usd"] = round(sum(r["notional_usd"] for r in plan["rows"] if r["side"] == "BUY"), 2)
    plan["costs_usd"] = round(sum(r["cost_usd"] for r in plan["rows"] if r["side"] != "-"), 2)
    plan["fit_factor"] = round(f, 4)
    return plan, f

# ------------------------------------------------------------------ funding gap rule (2026-10-29)
def audusd_now():
    try:
        sys.path.insert(0, BASE); import theme_exposure as _te
        return float(_te.fx_audusd({})), "theme_exposure.fx_audusd"
    except Exception:
        return FX_AUDUSD_FALLBACK, "fallback DESK_FX"

def _set_qty(r, q):
    q = max(0.0, q)
    q = math.floor(q + 1e-9) if r["kind"] == "etf" else math.floor(q * 1e5 + 1e-6) / 1e5
    r["order_qty"] = q; r["notional_usd"] = round(q * r["px"], 2); r["cost_usd"] = round(r["notional_usd"] * COST_SIDE[r["kind"]], 2)
    r["target_qty"] = round(float(r.get("current_qty") or 0) + q, 5)
    if q <= 0: r["side"] = "-"; r["order_qty"] = 0

def _recalc(plan, sleeve_cash):
    rs = plan["rows"]
    def sm(side, key, trend_only=False):
        return sum(r[key] for r in rs if r["side"] == side and (not trend_only or r.get("leg") != "mf"))
    b, s, bc, sc = sm("BUY", "notional_usd"), sm("SELL", "notional_usd"), sm("BUY", "cost_usd"), sm("SELL", "cost_usd")
    tb, ts, tbc, tsc = sm("BUY", "notional_usd", 1), sm("SELL", "notional_usd", 1), sm("BUY", "cost_usd", 1), sm("SELL", "cost_usd", 1)
    plan.update(buys_usd=round(b, 2), sells_usd=round(s, 2), costs_usd=round(bc + sc, 2), cash_need_usd=round(b + bc - (s - sc) - sleeve_cash, 2),
                trend_buys_usd=round(tb, 2), trend_sells_usd=round(ts, 2), trend_cash_need_usd=round(tb + tbc - (ts - tsc) - sleeve_cash, 2))
    return plan

def _gaps(plan, avail, cap_room):
    gc = max(0.0, plan["cash_need_usd"] - avail)
    gk = max(0.0, (plan["buys_usd"] - plan["sells_usd"]) - cap_room) if cap_room is not None else 0.0
    return round(gc, 2), round(gk, 2)

def _try_trim(plan, r, sleeve_cash, avail, cap_room):
    """Cut the buy leg r by the smallest qty step that closes the cash and cap gaps; restore and return None if it cannot."""
    snap = dict(r); px = float(r["px"]); c = COST_SIDE[r["kind"]]; step = 1.0 if r["kind"] == "etf" else 1e-5
    gc, gk = _gaps(plan, avail, cap_room)
    cut = math.ceil(max(gc / (px * (1 + c)), gk / px) / step - 1e-9) * step
    for _ in range(10):
        if cut > float(snap["order_qty"]) + 1e-12: break
        _set_qty(r, float(snap["order_qty"]) - cut); _recalc(plan, sleeve_cash)
        if _gaps(plan, avail, cap_room) == (0.0, 0.0):
            r["fund_note"] = "trimmed %s by %s to close a $%.2f funding gap (freed cash only; no other-book sale)" % (r["inst"], ("%.5f" % cut) if step < 1 else int(cut), max(gc, gk))
            return {"inst": r["inst"], "qty_before": snap["order_qty"], "qty_after": r["order_qty"], "cut_qty": round(cut, 5),
                    "cut_usd": round(snap["notional_usd"] - r["notional_usd"], 2)}
        cut += step
    r.clear(); r.update(snap); _recalc(plan, sleeve_cash)
    return None

def resolve_funding(plan, sleeve_cash, avail, cap_room, audusd, reserve_usd=RESERVE_USD, prefer=None):
    """Brett 3 Oct 2026 authority (TFO Risk 29 Oct 2026): close a Trend18 funding gap WITHOUT selling any other book.
    avail = powder cash above the reserve; cap_room = (firm equity - reserve) - firm long notional (None = unchecked).
    Gap <= max(1% of the gross trend buys, A$2k): trim the largest buy leg (reserve 100% intact) or, if a trim cannot close
    it, dip into the reserve for the cash gap only while >= 95% of the reserve stays. Otherwise every buy is scaled down pro
    rata (MF buys dropped first). Mutates plan rows; returns (plan, decision)."""
    prefer = prefer or SMALL_GAP_PREFER
    _recalc(plan, sleeve_cash)
    pre = {"cash_need_usd": plan["cash_need_usd"], "buys_usd": plan["buys_usd"], "sells_usd": plan["sells_usd"]}
    gc, gk = _gaps(plan, avail, cap_room)
    deploy = sum(r["notional_usd"] for r in plan["rows"] if r["side"] == "BUY" and r.get("leg") != "mf")
    thr_aud = round(SMALL_GAP_AUD * audusd, 2)
    thr = round(max(SMALL_GAP_PCT_DEPLOY * deploy, thr_aud), 2)
    dip_max = round((1 - RESERVE_MIN_INTACT) * reserve_usd, 2)
    d = {"utc": iso(), "authority": "Brett 3 Oct 2026: Trend18 funded from freed cash only, never forced sales of other books, only what is still needed at execution (TFO Risk 29 Oct 2026 14:08 WITA)",
         "rule": "gap <= max(1% of gross trend buys, A$2,000): trim the largest buy leg, else a reserve dip keeping >= 95% of the reserve; larger gap: buys scaled pro rata",
         "audusd": round(audusd, 4), "deploy_usd": round(deploy, 2), "gap_cash_usd": gc, "gap_cap_usd": gk, "gap_usd": max(gc, gk),
         "small_threshold_usd": thr, "small_threshold_basis": "max(1%% of deploy $%.2f, A$2,000 = US$%.2f)" % (SMALL_GAP_PCT_DEPLOY * deploy, thr_aud),
         "powder_avail_after_reserve_usd": round(avail, 2), "reserve_usd": reserve_usd, "reserve_dip_max_usd": dip_max, "prefer": prefer,
         "mode": "NONE", "reserve_dip_usd": 0.0, "fit_factor": 1.0, "trim": None, "other_book_sales": [], "pre": pre}
    gap = max(gc, gk)
    if gap > 0.005 and gap <= thr:
        legs = sorted([r for r in plan["rows"] if r["side"] == "BUY"], key=lambda r: -r["notional_usd"])
        for opt in (("trim", "dip") if prefer == "trim" else ("dip", "trim")):
            if opt == "trim" and legs:
                t = _try_trim(plan, legs[0], sleeve_cash, avail, cap_room)
                if t: d.update(mode="TRIM_LARGEST", trim=t); break
            if opt == "dip" and gk <= 0.005 and gc <= dip_max and (avail + reserve_usd - gc) >= RESERVE_MIN_INTACT * reserve_usd - 1e-6:
                d.update(mode="RESERVE_DIP", reserve_dip_usd=round(math.ceil(gc * 100) / 100.0, 2)); break
    if gap > 0.005 and d["mode"] == "NONE":
        for r in plan["rows"]:
            if r.get("leg") == "mf" and r["side"] == "BUY":
                r.update(order_qty=0, notional_usd=0.0, cost_usd=0.0, side="-", fit_note="MF buy dropped first (funding gap)")
        _recalc(plan, sleeve_cash); f = 1.0
        if _gaps(plan, avail, cap_room) != (0.0, 0.0):
            buys = [r for r in plan["rows"] if r["side"] == "BUY"]
            bg = sum(r["notional_usd"] + r["cost_usd"] for r in buys); bn = sum(r["notional_usd"] for r in buys)
            sn = sum(r["notional_usd"] - r["cost_usd"] for r in plan["rows"] if r["side"] == "SELL")
            so = sum(r["notional_usd"] for r in plan["rows"] if r["side"] == "SELL")
            f = (avail + sleeve_cash + sn) / bg if bg > 0 else 1.0
            if cap_room is not None and bn > 0: f = min(f, (cap_room + so) / bn)
            f = max(0.0, min(1.0, f))
            for r in buys:
                _set_qty(r, float(r["order_qty"]) * f); r["fit_factor"] = round(f, 4)
            _recalc(plan, sleeve_cash)
        d.update(mode="PRO_RATA", fit_factor=round(f, 4))
    post_gc, post_gk = _gaps(plan, avail, cap_room)
    draw = max(0.0, plan["cash_need_usd"])
    dip = round(max(0.0, plan["cash_need_usd"] - avail), 2)
    d.update(post={"cash_need_usd": plan["cash_need_usd"], "buys_usd": plan["buys_usd"], "sells_usd": plan["sells_usd"],
                   "gap_cash_usd": post_gc, "gap_cap_usd": post_gk},
             powder_draw_usd=round(draw, 2), reserve_dip_usd=dip if d["mode"] == "RESERVE_DIP" else 0.0,
             reserve_after_usd=round(reserve_usd - (dip if d["mode"] == "RESERVE_DIP" else 0.0), 2))
    d["reserve_intact_pct"] = round(100.0 * d["reserve_after_usd"] / reserve_usd, 2) if reserve_usd else 100.0
    if d["mode"] == "NONE": d["summary"] = "no gap: powder above the reserve covers the cash need $%.2f" % draw
    elif d["mode"] == "TRIM_LARGEST":
        t = d["trim"]; d["summary"] = "small gap $%.2f <= $%.2f: trimmed %s %s -> %s (-$%.2f); reserve 100%% intact; no other-book sale" % (gap, thr, t["inst"], t["qty_before"], t["qty_after"], t["cut_usd"])
    elif d["mode"] == "RESERVE_DIP":
        d["summary"] = "small gap $%.2f <= $%.2f: reserve dip $%.2f (reserve $%.2f = %.2f%% intact); no other-book sale" % (gap, thr, d["reserve_dip_usd"], d["reserve_after_usd"], d["reserve_intact_pct"])
    else:
        d["summary"] = "gap $%.2f > small threshold $%.2f (or no small fix fits): every buy scaled x%.4f pro rata; buys $%.2f -> $%.2f; no other-book sale" % (gap, thr, d["fit_factor"], pre["buys_usd"], plan["buys_usd"])
    plan["fit_factor"] = d["fit_factor"]; plan["funding_mode"] = d["mode"]
    return plan, d

def print_funding(d):
    print("FUNDING (freed cash only; never another book's sale): gap $%s (cash $%s, cap $%s) vs small threshold $%s [%s, AUDUSD %.4f]" % (
          "{:,.2f}".format(d["gap_usd"]), "{:,.2f}".format(d["gap_cash_usd"]), "{:,.2f}".format(d["gap_cap_usd"]), "{:,.2f}".format(d["small_threshold_usd"]),
          d["small_threshold_basis"], d["audusd"]))
    print("   -> %s: %s" % (d["mode"], d["summary"]))
    print("   powder draw $%s; reserve after $%s (%.2f%% intact)" % ("{:,.2f}".format(d["powder_draw_usd"]), "{:,.2f}".format(d["reserve_after_usd"]), d["reserve_intact_pct"]))

def funding_candidates():
    """Existing positions ranked weakest / most redundant with trend18 first. Report only; never sells."""
    sys.path.insert(0, BASE)
    try:
        import tfo_buy; twins, semis = set(tfo_buy.TWINS), set(tfo_buy.SEMIS)
    except Exception: twins, semis = set(), set()
    fb = json.load(open(os.path.join(REPORTS, "fundamental_book.json")))
    out = []
    for sym, p in fb.get("positions", {}).items():
        if p.get("side", "long") != "long": continue
        e0 = float(p.get("rail_orig_entry_usd") or p["entry_px_usd"]); px = float(p.get("mark_usd") or e0)
        ret = px / e0 - 1; usd = float(p["units"]) * px; theme = str(p.get("ht_theme") or p.get("sector") or "")
        venue = "US" if "." not in sym else sym.rsplit(".", 1)[1]
        redundant = None
        if sym in twins or sym in semis or theme in ("mega-tech", "semis", "us-tech", "nasdaq") or sym in ("META", "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"):
            redundant = "duplicates TQQQ/UPRO (US mega-cap tech / Nasdaq beta)"
        elif "gold" in theme.lower():
            redundant = "duplicates UGL (gold)"
        near_stop = ret <= -0.03
        score = (0 if redundant and venue == "US" else 1 if near_stop and venue == "US" else 2 if near_stop else 3 if redundant else 4, ret)
        try:  # what tfo_ticket books (desk_costs: commission + SEC/TAF, no spread)
            import desk_costs
            desk_fee = float(desk_costs.fill_fees(sym, "sell", float(p["units"]), px, p.get("mark"), p.get("entry_ccy"), now_utc())["fees_usd"])
        except Exception:
            desk_fee = None
        # HARD RULE cost model: 12.5bp per side. tfo_ticket books desk_costs; trend18 books the true-up to 12.5bp in the sleeve.
        fee = max(usd * COST_SIDE["etf"], desk_fee or 0.0)
        fee_src = "12.5bp model (hard rule); desk_costs books $%.2f, sleeve books the true-up" % (desk_fee or 0.0)
        out.append({"sym": sym, "venue": venue, "book": "fundamental_book", "usd": round(usd, 2), "ret_from_fill_pct": round(ret * 100, 2),
                    "theme": theme, "redundant": redundant, "near_stop": near_stop, "entry_utc": p.get("entry_utc"),
                    "est_exit_cost_usd": round(fee, 2), "est_exit_cost_src": fee_src,
                    "desk_costs_fee_usd": None if desk_fee is None else round(desk_fee, 2),
                    "rail_stop_px_usd": round(e0 * 0.95, 4), "_score": score})
    out.sort(key=lambda r: r["_score"])
    for r in out: r.pop("_score")
    return out

def build_funding_plan(shortfall, buffer_usd, cands):
    raise SystemExit(NO_OTHER_BOOK_SALES)   # 2026-10-29: other books are never staged for sale to fund Trend18
    picked, cum = [], 0.0
    for c in cands:
        if cum >= shortfall + buffer_usd: break
        picked.append({**c, "action": "FLAT", "when": "US: paired with the trend18 entry at the Mon 5 Oct close (06:50 AEDT Tue)" if c["venue"] == "US" else "ASX: Mon 5 Oct after the opening auction (10:10 AEDT)"})
        cum += c["usd"] - c["est_exit_cost_usd"]
    return {"created_utc": iso(), "shortfall_usd": round(shortfall, 2), "buffer_usd": round(buffer_usd, 2),
            "covered_usd": round(cum, 2), "sales": picked, "authority": "Brett authorised liquidating existing positions to make room for trend18 (3 Oct 2026); sell only what is still needed at execution time",
            "rule": "Sold only by trend18.py --sell-staged at a live regular-session price via tfo_ticket (FLAT), and only while the funding gap remains. No backdated fills."}

def firm_inputs(g):
    unres, avail = powder_available(g)
    longs, shorts, overlap, _ = firm_long_notional()
    E, src, _ = firm_equity()
    return {"avail": avail, "longs": longs, "shorts": shorts, "overlap": overlap, "E": E, "src": src,
            "cap_room": (E - RESERVE_USD) - longs}

def room_report(plan, L, g, base_usd, fi=None):
    fi = fi or firm_inputs(g)
    avail, longs, E = fi["avail"], fi["longs"], fi["E"]
    net_buy = plan["buys_usd"] - plan["sells_usd"]
    invested_after = longs + net_buy
    cap = E - RESERVE_USD
    gap_cash = max(0.0, plan["cash_need_usd"] - avail)
    gap_cap = max(0.0, invested_after - cap)
    return {"firm_equity_usd": round(E, 2), "firm_equity_source": fi["src"], "powder_cash_usd": round(float(g["cash"]), 2),
            "powder_reserved_usd": float(g.get("reserved_usd") or 0), "reserve_usd": RESERVE_USD,
            "powder_available_after_reserve_usd": round(avail, 2), "sleeve_cash_usd": round(float(L.get("cash") or 0), 2),
            "cash_need_usd": plan["cash_need_usd"], "cash_shortfall_usd": round(gap_cash, 2),
            "firm_long_notional_usd": round(longs, 2), "firm_short_notional_usd": round(fi["shorts"], 2),
            "firm_long_after_usd": round(invested_after, 2), "invested_cap_usd": round(cap, 2),
            "invested_cap_excess_usd": round(gap_cap, 2), "existing_overlap_positions": fi["overlap"],
            "mf_target_usd": plan.get("mf_target_usd"), "mf_cap_usd": plan.get("mf_cap_usd"),
            "needed_from_sales_usd": round(max(gap_cash, gap_cap), 2),
            "note": "needed_from_sales covers the TREND legs only; the MF basket is sized from cash that exists and never triggers a sale"}

# ------------------------------------------------------------------ booking
def book_fill(L, r, utc, tag, quote_meta):
    q, px, kind, inst = float(r["order_qty"]), float(r["px"]), r["kind"], r["inst"]
    if q == 0: return None
    notional = abs(q) * px; fee = round(notional * COST_SIDE[kind], 2)
    cur = float(L["holdings"].get(inst, 0.0)); avg = float(L["entry_px"].get(inst) or 0)
    realised = 0.0
    if q > 0:
        L["cash"] = round(float(L["cash"]) - notional - fee, 2)
        newq = cur + q; L["entry_px"][inst] = (cur * avg + q * px) / newq
        L["holdings"][inst] = newq
    else:
        sold = -q; realised = sold * (px - avg)
        L["cash"] = round(float(L["cash"]) + notional - fee, 2)
        newq = cur - sold
        if newq <= 1e-9: L["holdings"].pop(inst, None); L["entry_px"].pop(inst, None)
        else: L["holdings"][inst] = newq
        L["realised_usd"] = round(float(L.get("realised_usd") or 0) + realised, 2)
    L["costs_paid_usd"] = round(float(L.get("costs_paid_usd") or 0) + fee, 2)
    L["mark_px_usd"][inst] = px; L["mark_utc"][inst] = utc; L["mark_source"][inst] = quote_meta.get("kind") or quote_meta.get("source")
    fill = {"fill_id": "T18-" + uuid.uuid4().hex[:10], "utc": utc, "syd": syd(), "tag": tag, "inst": inst, "side": "BUY" if q > 0 else "SELL",
            "qty": abs(q), "px_usd": px, "notional_usd": round(notional, 2), "cost_usd": fee,
            "cost_rule": "%.1fbp per side (%s)" % (COST_SIDE[kind] * 1e4, kind), "realised_usd": round(realised, 2),
            "price_source": quote_meta, "paper_only": True}
    L["fills"].append(fill)
    new = not os.path.exists(FILLS)
    with open(FILLS, "a", newline="") as f:
        w = csv.writer(f)
        if new: w.writerow(["utc", "syd", "fill_id", "tag", "inst", "side", "qty", "px_usd", "notional_usd", "cost_usd", "realised_usd", "price_kind"])
        w.writerow([utc, fill["syd"], fill["fill_id"], tag, inst, fill["side"], abs(q), px, fill["notional_usd"], fee, fill["realised_usd"],
                    quote_meta.get("kind") or quote_meta.get("source")])
    log("FILL %s %s %s %.6g @ %.6g notional %.2f cost %.2f" % (tag, fill["side"], inst, abs(q), px, notional, fee))
    try:
        import audit
        _pl = {"id": fill["fill_id"], "inst": inst, "side": fill["side"], "qty": abs(q), "px_usd": px, "tag": tag, "paper_only": True}
        if q > 0:
            audit.log("paper_order", "trend18", _pl)
            audit.log("fill", "trend18", _pl)
        else:
            audit.log("stop" if "stop" in str(tag).lower() else "exit", "trend18", _pl)
    except Exception:
        pass
    return fill

def sell_staged(venue_filter, need_usd, execute):
    """RETIRED 2026-10-29: Trend18 never sells another book's position (Brett 3 Oct authority; TFO Risk 29 Oct)."""
    raise SystemExit(NO_OTHER_BOOK_SALES)
    if need_usd <= 0: print("FUNDING: no gap; staged sales not needed"); return 0.0, []
    if not os.path.exists(PLAN): print("FUNDING: no funding plan at %s" % PLAN); return 0.0, []
    plan = json.load(open(PLAN)); raised = 0.0; done = []
    sys.path.insert(0, BASE); import tfo_ticket
    for s in plan.get("sales", []):
        if raised >= need_usd: break
        if venue_filter and s["venue"] != venue_filter: continue
        if s.get("executed_utc"): continue
        rows = [r for r in tfo_ticket.scan({s["sym"]}) if r["sym"] == s["sym"] and r["book"] == s["book"]]
        if not rows: print("FUNDING: %s no longer held; skipped" % s["sym"]); continue
        r = rows[0]
        if r.get("rail") and r.get("status") == "READY":
            print("FUNDING: %s has its own rail %s READY; leave it to the rail runner" % (s["sym"], r["rail"])); continue
        why = r.get("why")
        if r.get("status") == "NO_QUOTE" or why:
            print("FUNDING: %s BLOCKED (%s); not sold" % (s["sym"], why or r.get("status"))); continue
        est = float(r["px_usd"]) * float(json.load(open(os.path.join(REPORTS, "fundamental_book.json")))["positions"][s["sym"]]["units"])
        if not execute:
            print("FUNDING PREVIEW: would FLAT %s ~$%.0f at %.4f (%s, ret %.2f%%); exit cost at 12.5bp ~$%.2f (tfo_ticket books desk_costs, sleeve books the true-up)"
                  % (s["sym"], est, r["px_usd"], r.get("tape"), r["ret_orig_pct"], est * COST_SIDE["etf"])); raised += est; continue
        r = {**r, "rail": "FLAT", "status": "READY", "exit_rule": "trend18_funding_flat (Brett-authorised liquidation for trend18, 3 Oct 2026)"}
        line = tfo_ticket.execute(r)
        if line:
            print(line); raised += est; s["executed_utc"] = iso(); s["exec_line"] = line; done.append(s["sym"])
            try:   # LOG syd(2 tokens) act sym sold px tape book principal proceeds pnl fees
                tok = line.split(); proceeds, fees = float(tok[-3]), float(tok[-1])
                s["proceeds_usd"], s["fees_booked_usd"] = proceeds, fees
                s["cost_trueup_usd"] = round(max(0.0, proceeds * COST_SIDE["etf"] - fees), 2); s["trueup_applied"] = False
                print("FUNDING: %s exit cost true-up to 12.5bp: $%.2f (booked in the sleeve at the next execute)" % (s["sym"], s["cost_trueup_usd"]))
            except Exception as e:
                s["cost_trueup_usd"] = round(est * COST_SIDE["etf"], 2); s["trueup_applied"] = False; s["trueup_note"] = "parse fail %s; full 12.5bp" % e
            log("FUNDING SALE %s" % line)
    if execute and done: atomic_json(PLAN, plan)
    return raised, done

# ------------------------------------------------------------------ commands
def get_prices(args, asof=None, execute=False):
    if args.prices_file:
        px, mode, meta = load_prices_file(args.prices_file, need_fresh=execute)
        if execute and mode not in ("last", "close"): raise RuntimeError("execution needs a 'last' or 'close' prices file")
        return {s: px[s] for s in PRICE_SYMS if s in px}, "file:%s (%s)" % (args.prices_file, mode)
    mode = "asof" if asof else args.price
    try:
        return fetch_alpaca(mode, PRICE_SYMS, asof=asof, env_path=args.alpaca_env), "alpaca iex (%s)" % mode
    except Exception as e:
        if execute and not args.allow_yahoo: raise
        print("   price note: Alpaca unavailable here (%s) -> Yahoo %s" % (str(e)[:90], "close" if asof else "last"))
        return yahoo_prices(PRICE_SYMS, asof), "yahoo (%s)" % ("close " + asof if asof else "last")

def print_signals(sig):
    print("Signals as of the US close %s (sigma %.0f%%, raw portfolio vol %.3f, ETF notional sum %.3f%s)"
          % (sig["asof"], sig["sigma"] * 100, sig["raw_portfolio_vol"] or 0, sig["sum_notional"], ", CAPPED at 97%" if sig["capped"] else ""))
    print("  %-7s %-9s %10s %10s %6s %8s %5s %6s %8s %8s" % ("sleeve", "underly", "px", "sma200", ">sma", "xs12m", "trend", "vol60", "expo", "w_ETF"))
    for s in sig["sleeves"]:
        print("  %-7s %-9s %10.2f %10.2f %6s %8.3f %5.1f %6.3f %8.3f %8.4f" % (s["name"], s["signal"], s["px_signal"], s["sma200"],
              "on" if s["above_sma200"] else "off", s["xs12m"], s["trend"], s["vol60"], s["exposure"], s["notional_w"]))
    g = sig.get("duration_gate")
    if g: print("  Duration gate (TMF): %s - ^TNX %.3f vs %.3f 252 trading days earlier" % ("OPEN" if g["open"] else "CLOSED", g["tnx"], g["tnx_252_ago"]))

def print_plan(plan, base_usd, scale):
    print("Targets: base $%s x scale %.2f = $%s" % ("{:,.0f}".format(base_usd), scale, "{:,.0f}".format(base_usd * scale)))
    print("  %-8s %8s %12s %10s %12s %10s %12s %5s %12s %9s" % ("inst", "w", "target $", "px", "target qty", "held", "order qty", "side", "notional", "cost"))
    for r in plan["rows"]:
        print("  %-8s %8.4f %12s %10.2f %12s %10s %12s %5s %12s %9.2f" % (r["inst"], r["target_w"], "{:,.0f}".format(r["target_usd"]), r["px"],
              ("%.5f" % r["target_qty"]) if r["kind"] == "crypto" else "{:,}".format(int(r["target_qty"])),
              ("%.5f" % r["current_qty"]) if r["kind"] == "crypto" else "{:,}".format(int(r["current_qty"])),
              ("%.5f" % r["order_qty"]) if r["kind"] == "crypto" else "{:,}".format(int(r["order_qty"])),
              r["side"], "{:,.0f}".format(r["notional_usd"]), r["cost_usd"]))
    print("  buys $%s  sells $%s  costs $%s  cash need (after sleeve cash) $%s" % tuple("{:,.2f}".format(x) for x in (plan["buys_usd"], plan["sells_usd"], plan["costs_usd"], plan["cash_need_usd"])))
    if "mf_target_usd" in plan:
        print("  MF basket (DBMF/KMLM/CTA thirds): target $%s = min(budget $%s, R2 idle cap $%s); held $%s; powder top-up %s" % (
              "{:,.0f}".format(plan["mf_target_usd"]), "{:,.0f}".format(max(0, plan["mf_budget_usd"])), "{:,.0f}".format(plan["mf_cap_usd"]),
              "{:,.0f}".format(plan["mf_current_usd"]), "ON" if plan["mf_powder_allowed"] else "OFF (sleeve cash only)"))

def cmd_stage(args):
    L = load_ledger()
    sigma = KILL_SIGMA if L.get("kill", {}).get("active") else SIGMA
    sig = compute_signals(args.asof, sigma)
    etf_px, src = get_prices(args, asof=args.asof or None)
    missing = [s for s in TREND_ETFS + [m for m in MF if float(L["holdings"].get(m, 0))] if s not in etf_px]
    if missing: raise SystemExit("no price for %s" % missing)
    if [m for m in MF if m not in etf_px]: print("   note: no price for %s -> MF basket not traded this run" % [m for m in MF if m not in etf_px])
    btc = kraken_btc(args.asof)
    g = load_powder(); fi = firm_inputs(g)
    E, esrc = fi["E"], fi["src"]
    base = args.base_usd or E
    scale = args.scale if args.scale is not None else (L.get("scale_current") or 0.5)
    mf_powder = bool(args.mf_powder)
    plan = plan_orders(sig, base, scale, L, etf_px, btc, powder_avail=fi["avail"], mf_powder=mf_powder, cap_room=fi["cap_room"])
    print_signals(sig); print("ETF prices: %s; BTC: %s %.2f (bid %.2f / ask %.2f)" % (src, btc.get("source"), btc["mid"], btc["bid"], btc["ask"]))
    print("Base: %s" % esrc); print_plan(plan, base, scale)
    room = room_report(plan, L, g, base, fi)
    print("ROOM: powder cash $%s, available after $50k reserve $%s; cash shortfall $%s" % ("{:,.0f}".format(room["powder_cash_usd"]), "{:,.0f}".format(room["powder_available_after_reserve_usd"]), "{:,.0f}".format(room["cash_shortfall_usd"])))
    print("      firm long notional $%s -> $%s after; cap (equity - reserve) $%s; excess $%s; overlap %s" % ("{:,.0f}".format(room["firm_long_notional_usd"]), "{:,.0f}".format(room["firm_long_after_usd"]), "{:,.0f}".format(room["invested_cap_usd"]), "{:,.0f}".format(room["invested_cap_excess_usd"]), room["existing_overlap_positions"] or "none"))
    # 2026-10-29 TFO Risk (Brett 3 Oct authority): no other-book sales; the gap is closed by resolve_funding (preview here;
    # --execute re-computes everything on live prices and fresh powder)
    import copy
    pre_plan = copy.deepcopy(plan)
    fx, fxsrc = audusd_now()
    plan, fplan = resolve_funding(plan, float(L.get("cash") or 0), fi["avail"], fi["cap_room"], fx)
    fplan["audusd_source"] = fxsrc
    print_funding(fplan)
    if fplan["mode"] != "NONE": print_plan(plan, base, scale)
    room_after = room_report(plan, L, g, base, fi)
    if getattr(args, "write_plan", False): print("   --write-plan ignored: there is no other-book sell plan any more (2026-10-29)")
    out = {"generated_utc": iso(), "dry_run": True, "signals": sig, "prices_source": src,
           "btc_quote": {k: btc.get(k) for k in ("bid", "ask", "mid", "utc", "source", "tape")}, "base_usd": base, "scale": scale,
           "orders": plan, "orders_pre_funding": pre_plan, "room": room, "room_after_funding": room_after,
           "funding_candidates": [], "funding_plan": fplan,
           "note": "DRY RUN preview only. --execute re-computes signals, live prices, powder and the funding resolution; nothing here is reused."}
    try:
        if not any(float(q) for q in L["holdings"].values()):
            proj = {"holdings": {r["inst"]: (r["target_qty"], r["px"]) for r in plan["rows"] if r["target_qty"]},
                    "sold": [x["sym"] for x in (fplan or (json.load(open(PLAN)) if os.path.exists(PLAN) else {})).get("sales", [])],
                    "signals": sig, "scale": scale}
            ex = exposure_snapshot(L, projected=proj)
        else:
            ex = exposure_snapshot(L)
        out["exposure"] = ex
        print("EXPOSURE (%s): sleeve notional $%s -> economic $%s (%.2fx, %s%% of firm equity); firm notional $%s (%s%%) -> economic $%s (%s%%)" % (
              ex["basis"], "{:,.0f}".format(ex["sleeve"]["notional_usd"]), "{:,.0f}".format(ex["sleeve"]["economic_usd"]), ex["sleeve"]["economic_x_notional"] or 0,
              ex["sleeve"]["economic_pct_firm"], "{:,.0f}".format(ex["firm"]["notional_usd"]), ex["firm"]["notional_pct"],
              "{:,.0f}".format(ex["firm"]["economic_usd"]), ex["firm"]["economic_pct"]))
        gr = ex["gap_risk"]
        print("GAP RISK (%s): sleeve $%s (%s%% of sleeve), firm $%s (%s%% of equity)" % (gr["scenario"], "{:,.0f}".format(gr["sleeve_loss_usd"]),
              gr["sleeve_loss_pct_sleeve"], "{:,.0f}".format(gr["firm_loss_usd"]), gr["firm_loss_pct"]))
        print("LIVE BOOK LABEL: %s (backtest %.1f%%, P(5y below start) ~%d%%); MF %s%% of target" % (ex["regime"]["name"], ex["regime"]["cagr_pct"],
              ex["regime"]["p5_below_pct"], ex["regime"]["mf_funded_pct"]))
    except Exception as e:
        print("exposure snapshot fail: %s" % e)
    atomic_json(STAGE_OUT, out); print("stage written: %s (DRY RUN: nothing booked)" % STAGE_OUT)
    return out

DOUBLE_FIRE_SESSIONS = 3
def recent_full_rebalance(L, session, n=DOUBLE_FIRE_SESSIONS):
    """An entry/manual/monthly rebalance in the last n US sessions before `session` (-> that record) else None."""
    prior = []; d = session - dt.timedelta(days=1)
    while len(prior) < n:
        if is_session(d): prior.append(d.isoformat())
        d -= dt.timedelta(days=1)
    for r in reversed(L.get("rebalances") or []):
        if r.get("reason") in ("entry", "manual", "monthly") and r.get("session") in prior: return r
    return None

def cmd_execute(args):
    now = now_utc(); et = now.astimezone(ET); session = et.date()
    if not is_session(session): raise SystemExit("not a US session day (%s ET)" % session)
    # TFO Risk: re-mark and run the kill check BEFORE anything else, so a crash since the last hourly mark
    # sets the kill (sigma 10%) for this very rebalance
    print("PRE-EXECUTE MARK + KILL CHECK:"); cmd_mark(args, event="pre_execute_mark")
    L = load_ledger()
    if L.get("kill", {}).get("active"): print("KILL ACTIVE: this rebalance runs at sigma 10%")
    reason = args.reason
    # R2 next-close semantics: monthly trades at the close of the FIRST US session of the month on the signal of the
    # last close of the previous month; the refresh trades at the close of the middle session back to the last targets
    if reason == "monthly" and session != first_session_of_month(session) and not args.force:
        raise SystemExit("monthly rebalance only on the first US session of the month (%s) per R2 next-close; use --force to override" % first_session_of_month(session))
    if reason == "refresh" and session != mid_session_of_month(session) and not args.force:
        raise SystemExit("mid-month refresh only on the middle US session of the month (%s); use --force to override" % mid_session_of_month(session))
    _dbl = recent_full_rebalance(L, session) if reason == "monthly" else None
    if _dbl and not args.force:   # Brett 28 Oct 14:57 WITA: 30 Oct manual deploy must not double-fire as the 2 Nov monthly
        raise SystemExit("monthly refused: a %s rebalance already executed for session %s (within %d sessions); use --force to override" % (_dbl["reason"], _dbl["session"], DOUBLE_FIRE_SESSIONS))
    if reason == "refresh" and L.get("kill", {}).get("pending_rebalance"):
        raise SystemExit("kill rebalance pending: run --reason kill instead of a refresh")
    if reason == "kill" and not L.get("kill", {}).get("pending_rebalance"):
        raise SystemExit("no kill rebalance pending")
    if any(r.get("session") == session.isoformat() and r.get("reason") == reason for r in L["rebalances"]) and not args.force:
        raise SystemExit("a %s rebalance already executed for session %s (use --force)" % (reason, session))
    sigma = KILL_SIGMA if L.get("kill", {}).get("active") else SIGMA
    scale = args.scale if args.scale is not None else float(L.get("scale_current") or 0)
    if scale <= 0 or scale > 1: raise SystemExit("--scale must be in (0, 1]")
    # signals: ALWAYS the prior completed US close (R2 official lag-2 = trade at the next close after the signal close)
    asof = args.signal_asof
    if not asof:
        d = session - dt.timedelta(days=1)
        while not is_session(d): d -= dt.timedelta(days=1)
        asof = d.isoformat()
    if reason == "refresh":
        sig = L.get("last_signals")
        if not sig or not sig.get("sleeves"): raise SystemExit("no stored target weights to refresh to")
        print("REFRESH: back to the targets of the %s signal (no new signal; drift control only)" % sig.get("asof"))
    else:
        sig = compute_signals(asof, sigma)
        if sig["asof"] != asof: raise SystemExit("signal data ends %s, expected %s: refusing (stale data)" % (sig["asof"], asof))
    _g = scale_up_guard(sig, scale, L.get("scale_current"))
    if _g: raise SystemExit(_g)
    etf_px, src = get_prices(args, execute=True)
    missing = [s for s in TREND_ETFS + [m for m in MF if float(L["holdings"].get(m, 0))] if s not in etf_px]
    if missing: raise SystemExit("no live price for %s" % missing)
    if [m for m in MF if m not in etf_px]: print("   note: no live price for %s -> MF basket not traded this run (trend legs unaffected)" % [m for m in MF if m not in etf_px])
    try: btc = kraken_btc()   # live Kraken only (no asof): fail CLOSED on any error / missing price
    except Exception as e: raise SystemExit("no live Kraken BTC price (%s): nothing booked (fail closed)" % e)
    if not (btc and float(btc.get("bid") or 0) > 0 and float(btc.get("ask") or 0) > 0):
        raise SystemExit("no live Kraken BTC price: nothing booked (fail closed)")
    if btc.get("tape") != "LIVE" or not btc.get("spread_bp") or btc["spread_bp"] >= 20:
        raise SystemExit("Kraken BTC tape not LIVE or spread >= 20bp: %s" % {k: btc.get(k) for k in ("tape", "spread_bp", "trade_age_sec")})
    g = load_powder(); fi = firm_inputs(g); E, esrc = fi["E"], fi["src"]; base = args.base_usd or E
    mf_powder = args.mf_powder if args.mf_powder is not None else (reason in ("monthly", "refresh"))
    plan = plan_orders(sig, base, scale, L, etf_px, btc, powder_avail=fi["avail"], mf_powder=mf_powder, cap_room=fi["cap_room"])
    print("MF basket: target $%s (cap $%s, held $%s; powder top-up %s)" % ("{:,.0f}".format(plan["mf_target_usd"]), "{:,.0f}".format(plan["mf_cap_usd"]),
          "{:,.0f}".format(plan["mf_current_usd"]), "ON" if mf_powder else "OFF (sleeve cash only)"))
    btc_row = [r for r in plan["rows"] if r["inst"] == BTC][0]
    if btc_row["notional_usd"] > 0.05 * float(btc.get("dollar_vol24") or 0):
        raise SystemExit("BTC order > 5% of Kraken 24h dollar volume")
    print_signals(sig); print("ETF prices: %s" % src); print_plan(plan, base, scale)
    room = room_report(plan, L, g, base, fi)
    need = room["needed_from_sales_usd"]
    print("ROOM: available after reserve $%s, cash need $%s, firm long after $%s vs cap $%s -> gap $%s" % tuple("{:,.0f}".format(x) for x in (room["powder_available_after_reserve_usd"], room["cash_need_usd"], room["firm_long_after_usd"], room["invested_cap_usd"], need)))
    sales = []   # 2026-10-29 TFO Risk (Brett 3 Oct authority): Trend18 never sells another book's position
    if getattr(args, "sell_staged", False): raise SystemExit(NO_OTHER_BOOK_SALES)
    fx, fxsrc = audusd_now()
    plan, fdec = resolve_funding(plan, float(L.get("cash") or 0), fi["avail"], fi["cap_room"], fx)
    fdec["audusd_source"] = fxsrc
    fit = fdec["fit_factor"]
    print_funding(fdec)
    if fdec["mode"] != "NONE": print_plan(plan, base, scale)
    try:
        atomic_json(EXEC_STAGE, {"generated_utc": iso(), "dry_run": bool(getattr(args, "dry_run", False)), "reason": reason, "session": session.isoformat(),
                                 "signal_asof": sig.get("asof"), "prices_source": src, "btc_quote": {k: btc.get(k) for k in ("bid", "ask", "mid", "utc", "source", "tape", "spread_bp")},
                                 "base_usd": base, "scale": scale, "orders": plan, "room": room, "funding_plan": fdec,
                                 "note": "re-computed live at execution (2026-10-29 rule); the --stage dry run is never reused"})
    except Exception as e: print("exec stage write fail: %s" % e)
    if not getattr(args, "dry_run", False):
        log("FUNDING %s %s" % (fdec["mode"], fdec["summary"]))
        if fdec["mode"] == "PRO_RATA": alert("FUNDING_PRO_RATA", fdec["summary"], L)
    if args.dry_run: print("DRY RUN: nothing booked"); return
    sys.path.insert(0, BASE); import desk_lock
    utc = iso(); tag = "T18_%s_%s" % (reason.upper(), session.isoformat())
    with desk_lock.ledger_lock(LOCK), desk_lock.ledger_lock(POWDER_LOCK):
        L = load_ledger(); g = load_powder()
        if not os.path.exists(LEDGER): save_ledger(L)
        accrue_cash(L)
        sells = [r for r in plan["rows"] if r["side"] == "SELL"]; buys = [r for r in plan["rows"] if r["side"] == "BUY"]
        try:   # 2026-10-28 13:40 Brett approve-all #5: theme cap ENFORCE blocks new buys in capped themes (sells untouched)
            import theme_exposure as _te; _td = _te.build() if _te.enforcing() else None; _fx = float(L.get("audusd") or _te.DESK_FX)
            _keep = []
            for r in buys:
                ok, why = _te.check_buy(r["inst"], r["notional_usd"] / _fx, ccy="USD", d=_td, book="Trend18") if _td else (True, "")
                if ok: _keep.append(r)
                else: log("THEME CAP BLOCK %s BUY %s: %s" % (tag, r["inst"], why)); print("   " + why)
            buys = _keep
        except Exception as e:
            raise SystemExit("theme cap check failed (%s); nothing booked (fail closed)" % e)
        try:   # tfo_usbook_cap_20261030 (Brett 19:35 WITA): main-book US beta cap; same-run sells credited first; sells never blocked
            import us_book_cap as _ubc; _ubc.reset_run()
            for r in sells: _ubc.credit(-_ubc.leg_usd(r["inst"], r["notional_usd"], "long"))
            _keep = []
            for r in buys:
                ok, why, _ = _ubc.check(r["inst"], r["notional_usd"], "long", book="Trend18")
                if ok: _keep.append(r)
                else: log("US BOOK CAP BLOCK %s BUY %s: %s" % (tag, r["inst"], why)); print("   " + why)
            buys = _keep
        except Exception as e:
            raise SystemExit("us_book_cap check failed (%s); nothing booked (fail closed)" % e)
        unres, avail = powder_available(g)
        # 12.5bp true-up on old (pre-29 Oct) funding sales (tfo_ticket books desk_costs commission only; the sleeve pays the rest)
        fplan = json.load(open(PLAN)) if os.path.exists(PLAN) else {}
        trueups = [x for x in fplan.get("sales", []) if x.get("executed_utc") and not x.get("trueup_applied") and x.get("cost_trueup_usd")]
        trueup = round(sum(float(x["cost_trueup_usd"]) for x in trueups), 2)
        # 2026-10-29: re-apply the funding gap rule on the FRESH powder balance under the locks (never another book's sale)
        lk, ldec = resolve_funding({"rows": sells + buys}, float(L["cash"]) - trueup, avail, fi["cap_room"], fx)
        if ldec["mode"] != "NONE":
            print("UNDER THE LOCK: %s: %s" % (ldec["mode"], ldec["summary"])); log("FUNDING UNDER LOCK %s %s" % (ldec["mode"], ldec["summary"]))
            if ldec["mode"] == "PRO_RATA": fit = min(fit, ldec["fit_factor"])
        sells = [r for r in lk["rows"] if r["side"] == "SELL"]; buys = [r for r in lk["rows"] if r["side"] == "BUY"]
        cash_need = sum(r["notional_usd"] + r["cost_usd"] for r in buys) - sum(r["notional_usd"] - r["cost_usd"] for r in sells) - float(L["cash"]) + trueup
        dip = cash_need - avail
        if dip > 0.01 and not (ldec["mode"] == "RESERVE_DIP" and dip <= ldec["reserve_dip_max_usd"] + 0.01
                                and unres - cash_need >= RESERVE_MIN_INTACT * RESERVE_USD - 0.01):
            raise SystemExit("under the lock: cash need %.2f > powder available %.2f after reserve and no allowed reserve dip; nothing booked" % (cash_need, avail))
        if cash_need > 0:
            xfer(L, g, math.ceil(cash_need * 100) / 100.0, utc, tag, "trend18 %s funded from powder%s; paired; firm unchanged; no other-book sale. PAPER"
                 % (reason, (" incl. reserve dip $%.2f" % max(0.0, dip)) if dip > 0.005 else ""))
        if float(g["cash"]) - float(g.get("reserved_usd") or 0) < RESERVE_MIN_INTACT * RESERVE_USD - 0.01:
            raise RuntimeError("powder below 95%% of the reserve after the transfer (bug): %.2f; nothing saved" % float(g["cash"]))
        if trueup:
            L["cash"] = round(float(L["cash"]) - trueup, 2)
            L["costs_paid_usd"] = round(float(L.get("costs_paid_usd") or 0) + trueup, 2)
            L.setdefault("funding_cost_trueups", []).append({"utc": utc, "usd": trueup, "syms": [x["sym"] for x in trueups],
                                                             "rule": "12.5bp/side hard rule minus desk_costs fees booked by tfo_ticket"})
            for x in trueups: x["trueup_applied"] = True; x["trueup_applied_utc"] = utc
            atomic_json(PLAN, fplan)
            log("FUNDING COST TRUE-UP %.2f for %s" % (trueup, [x["sym"] for x in trueups]))
        fills = []
        for r in sells + buys:
            meta = etf_px.get(r["inst"]) if r["kind"] == "etf" else {"kind": "kraken_%s" % ("ask" if r["side"] == "BUY" else "bid"), "utc": btc.get("utc"),
                                                                    "spread_bp": btc.get("spread_bp"), "source": btc.get("source")}
            f = book_fill(L, r, utc, tag, {**(meta or {}), "prices_source": src})
            if f: fills.append(f)
        if L["cash"] < -0.01: raise RuntimeError("negative sleeve cash after fills (bug): %s" % L["cash"])
        L.setdefault("mark_ref_px", {})
        for r in buys + sells:      # 1x underlying reference for the leverage-aware mark check
            u = UND.get(r["inst"])
            if u: L["mark_ref_px"][r["inst"]] = float(etf_px[u]["px"]) if etf_px.get(u) else None
        L["scale_current"] = scale; L["sigma_current"] = sigma
        if reason == "kill" or (reason in ("monthly", "entry", "manual") and L.get("kill", {}).get("active") and sigma == KILL_SIGMA):
            L["kill"]["pending_rebalance"] = False
        if reason != "refresh": L["last_signals"] = sig
        restrike(L)
        L["rebalances"].append({"utc": utc, "syd": syd(), "session": session.isoformat(), "reason": reason, "scale": scale, "sigma": sigma,
                                "signal_asof": sig["asof"], "base_usd": round(base, 2), "fit_factor": fit,
                                "mf_target_usd": plan.get("mf_target_usd"), "mf_powder": mf_powder, "fills": [f["fill_id"] for f in fills],
                                "funding_sales": sales, "prices_source": src,
                                "funding": {k: fdec.get(k) for k in ("mode", "gap_usd", "small_threshold_usd", "trim", "reserve_dip_usd", "fit_factor", "summary")},
                                "funding_under_lock": {k: ldec.get(k) for k in ("mode", "gap_usd", "reserve_dip_usd", "fit_factor", "summary")}})
        save_ledger(L); save_powder(g)
    hist_append(L, tag)
    try: exposure_snapshot(L)
    except Exception as e: print("exposure snapshot fail: %s" % e)
    print("BOOKED %d fill(s); sleeve equity $%.2f cash $%.2f NAV/unit %.6f" % (len(fills), L["equity"], L["cash"], L["nav_per_unit"]))
    log("EXECUTE %s fills=%d equity=%.2f" % (tag, len(fills), L["equity"]))

def mark_check(inst, p, last, und_now=None, und_ref=None):
    """-> (accept, alert_kind or None, message). See MARK SANITY in the module docstring."""
    if not last or last <= 0: return True, None, "first mark"
    r = p / last
    if MARK_NORMAL_BAND[0] <= r <= MARK_NORMAL_BAND[1]: return True, None, ""
    lev = LEV.get(inst, 1)
    if inst in UND and und_now and und_ref:
        ru = und_now / und_ref - 1; exp = max(-0.99, lev * ru); tol = 0.10 + 0.25 * abs(exp)
        txt = "%s %.4f vs last %.4f (%+.1f%%); %dx %s %+.2f%% -> expected %+.1f%%" % (inst, p, last, (r - 1) * 100, lev, UND[inst], ru * 100, exp * 100)
        if abs((r - 1) - exp) <= tol: return True, "MARK_LARGE_MOVE_CONFIRMED", txt + ": consistent, accepted"
        return False, "MARK_REFUSED", txt + ": INCONSISTENT with the underlying, refused (stale mark kept)"
    txt = "%s %.4f vs last %.4f (%+.1f%%), no underlying confirmation" % (inst, p, last, (r - 1) * 100)
    if MARK_HARD_FLOOR <= r < MARK_NORMAL_BAND[0]: return True, "MARK_LARGE_DROP_UNCONFIRMED", txt + ": drop accepted (loss booked; verify)"
    return False, "MARK_REFUSED", txt + ": refused (stale mark kept)"

def mark_holdings(L, px):
    """Apply marks through mark_check. Mutates L; returns [(kind, msg)] alerts (caller writes them)."""
    out = []; L.setdefault("mark_ref_px", {}); stale = L.setdefault("mark_stale", {})
    for s in list(L["holdings"]):
        p = px.get(s)
        if not (p and float(p.get("px") or 0) > 0): continue
        p_new = float(p["px"]); last = float(L["mark_px_usd"].get(s) or 0)
        und = UND.get(s); und_now = float(px[und]["px"]) if und and px.get(und) else None
        ok, kind, msg = mark_check(s, p_new, last, und_now, L["mark_ref_px"].get(s))
        if kind: out.append((kind, msg))
        if not ok:
            stale[s] = {"utc": iso(), "refused_px": p_new, "kept_px": last, "why": msg}; continue
        L["mark_px_usd"][s] = p_new; L["mark_utc"][s] = p.get("utc"); L["mark_source"][s] = p.get("kind"); stale.pop(s, None)
        if und: L["mark_ref_px"][s] = und_now     # None when the underlying was not fetched -> no stale reference
    return out

def fetch_marks(L0, args):
    px = {}
    etfs = [s for s in L0["holdings"] if s in ETFS and float(L0["holdings"][s])]
    syms = etfs + sorted({UND[s] for s in etfs if s in UND})
    if syms:
        try:
            if args.prices_file: px = load_prices_file(args.prices_file, need_fresh=False)[0]
            else: px = yahoo_prices(syms)          # mfl 2026-10-30: Yahoo primary for marks
        except Exception as e:
            print("trend18 primary mark fetch fail: %s" % e); px = {}
        gap = [s for s in syms if s not in px]
        if gap and not args.prices_file:           # fill any gap from the other source, never leave MARK_MISSING silently
            try: px.update({k: v for k, v in (fetch_alpaca("last", gap, env_path=args.alpaca_env) or {}).items() if k not in px})
            except Exception as e: print("trend18 alpaca gap-fill fail: %s" % e)
    if BTC in L0["holdings"]:
        try:
            q = kraken_btc()
            if q.get("tape") != "BAD": px[BTC] = {"px": q["mid"], "utc": q["utc"], "kind": "kraken_mid"}
        except Exception as e: print("BTC mark fail: %s" % e)
    return px

def cmd_mark(args, px=None, event="mark"):
    """Re-mark (leverage-aware sanity), accrue residual cash, kill check, exposure snapshot. Never trades."""
    sys.path.insert(0, BASE); import desk_lock
    L0 = load_ledger()
    if not L0["holdings"] and not L0.get("cash"):
        with desk_lock.ledger_lock(LOCK):
            L = load_ledger(); restrike(L); save_ledger(L)
        # empty sleeve: keep the --stage projected exposure snapshot (the panel shows the post-entry picture)
        print("trend18: empty sleeve (registered, $0); nothing to mark"); return L
    if px is None: px = fetch_marks(L0, args)
    with desk_lock.ledger_lock(LOCK):
        L = load_ledger(); got = accrue_cash(L)
        alerts = mark_holdings(L, px)
        missing = [s for s in L["holdings"] if float(L["holdings"][s]) and s not in px]
        restrike(L)
        for kind, msg in alerts: alert(kind, msg, L)
        if missing: alert("MARK_MISSING", "no price for %s this mark; previous marks kept" % missing, L)
        kill_check(L); save_ledger(L)
    hist_append(L, event)
    try: exposure_snapshot(L)
    except Exception as e: print("exposure snapshot fail: %s" % e)
    print("trend18 equity $%.2f cash $%.2f NAV/unit %.6f dd %.2f%% kill %s interest +%.4f" % (L["equity"], L["cash"], L["nav_per_unit"], L["drawdown_pct"], L["kill"].get("active"), got))
    return L

def _shock_leg(inst):
    return {"UPRO": "SPX", "TQQQ": "NDX", BTC: "BTC"}.get(inst)

def exposure_snapshot(L, projected=None):
    """Notional vs economic (leverage-weighted) exposure, sleeve and firm; gap-risk scenario; R1G/R2 label.
    projected: {"holdings": {inst: (qty, px)}, "sold": {sym}} to show the post-entry picture before funding."""
    hold = {i: (float(q), float(L["mark_px_usd"].get(i) or L["entry_px"].get(i) or 0)) for i, q in L["holdings"].items() if float(q)}
    sold = set()
    if projected: hold = projected["holdings"]; sold = set(projected.get("sold") or [])
    sl_notional = sum(q * p for q, p in hold.values())
    sl_econ = sum(q * p * LEV.get(i, 1) for i, (q, p) in hold.items())
    sl_loss = sum(q * p * max(-1.0, LEV.get(i, 1) * GAP_SHOCK[_shock_leg(i)]) for i, (q, p) in hold.items() if _shock_leg(i))
    longs, shorts, overlap, g = firm_long_notional()
    oth = [p for p in g["positions"] if p["book"] != KEY and p.get("side", "long") == "long" and p["sym"] not in sold]
    def is_crypto(p): return "/" in p["sym"] or str(p.get("ticker") or "").endswith("-USD") or "crypto" in p["book"]
    o_notional = sum(p["fallback_usd"] for p in oth)
    o_econ = sum(p["fallback_usd"] * EXT_LEV.get(p["sym"], 1) for p in oth)
    o_loss = sum(p["fallback_usd"] * max(-1.0, EXT_LEV.get(p["sym"], 1) * (GAP_SHOCK["BTC"] if is_crypto(p) else GAP_SHOCK["SPX"])) for p in oth)
    E = firm_equity()[0]
    sleeve_eq = (sl_notional + float(L.get("cash") or 0)) if not projected else sl_notional
    sig = L.get("last_signals") or {}
    if projected and projected.get("signals"): sig = projected["signals"]
    scale = float(L.get("scale_current") or 0) or float((projected or {}).get("scale") or 0)
    mf_val = sum(q * p for i, (q, p) in hold.items() if i in MF)
    mf_tgt = mf_cap_usd(sig, E, scale) if sig.get("sleeves") and scale else 0.0
    mf_ratio = (mf_val / mf_tgt) if mf_tgt > 0 else 0.0
    lab = R2_LABEL if mf_ratio >= MF_FUNDED_FOR_R2 else R1G_LABEL
    pct = lambda x: round(100 * x / E, 1) if E else None
    snap = {"utc": iso(), "basis": "projected after the staged entry (not funded yet)" if projected else "live ledger marks",
            "firm_equity_usd": round(E, 2),
            "sleeve": {"notional_usd": round(sl_notional, 2), "economic_usd": round(sl_econ, 2), "notional_pct_firm": pct(sl_notional),
                       "economic_pct_firm": pct(sl_econ), "economic_x_notional": round(sl_econ / sl_notional, 2) if sl_notional else None,
                       "economic_x_sleeve_equity": round(sl_econ / sleeve_eq, 2) if sleeve_eq else None, "levered": True},
            "firm": {"notional_usd": round(o_notional + sl_notional, 2), "economic_usd": round(o_econ + sl_econ, 2),
                     "notional_pct": pct(o_notional + sl_notional), "economic_pct": pct(o_econ + sl_econ),
                     "other_books_notional_usd": round(o_notional, 2)},
            "gap_risk": {"scenario": "S&P -20% / NDX -22% / BTC -15% in one day (Oct-1987 type)",
                         "sleeve_loss_usd": round(sl_loss, 2), "sleeve_loss_pct_sleeve": round(100 * sl_loss / sleeve_eq, 1) if sleeve_eq else None,
                         "firm_loss_usd": round(sl_loss + o_loss, 2), "firm_loss_pct": pct(sl_loss + o_loss),
                         "assumptions": "levered ETFs = leverage x index move (daily reset, floored at -100%); TMF/UGL/MF 0; other books: equities -20% (S&P proxy, beta 1), crypto -15%"},
            "regime": {**lab, "mf_value_usd": round(mf_val, 2), "mf_target_usd": round(mf_tgt, 2), "mf_funded_pct": round(100 * mf_ratio, 1),
                       "rule": "R2 figures shown only once the MF basket is >= 80% of its target"}}
    atomic_json(EXPO_PATH, snap)
    return snap

def cmd_sell_staged_only(args):
    raise SystemExit(NO_OTHER_BOOK_SALES)
    L = load_ledger(); g = load_powder()
    st = json.load(open(STAGE_OUT)) if os.path.exists(STAGE_OUT) else None
    need = args.need_usd if args.need_usd is not None else ((st or {}).get("room") or {}).get("needed_from_sales_usd", 0)
    raised, done = sell_staged(args.venue, need, execute=args.execute_sales)
    print("raised ~$%.0f from %s" % (raised, done or "nothing"))

def _regime_now(snap):
    """Exposure-snapshot regime with the CURRENT label text/fee figures overlaid (snapshots written before a fee change keep old text)."""
    snap = dict(snap or {}); lab = R2_LABEL if snap.get("name") == R2_LABEL["name"] else R1G_LABEL
    return {**snap, **lab}


def summary():
    """Dashboard payload (read-only)."""
    try:
        if not os.path.exists(LEDGER): return {"status": "NOT_STARTED", "note": "trend18 ledger not created yet"}
        L = json.load(open(LEDGER))
        hold = []
        for inst in [s["inst"] for s in SLEEVES] + MF:
            q = float(L["holdings"].get(inst, 0)); px = float(L["mark_px_usd"].get(inst) or 0)
            avg = float(L["entry_px"].get(inst) or 0)
            hold.append({"inst": inst, "qty": q, "px": px, "value_usd": round(q * px, 2), "avg_cost": round(avg, 4),
                         "upl_usd": round(q * (px - avg), 2) if q else 0.0, "mark_utc": L["mark_utc"].get(inst),
                         "weight_pct": round(100 * q * px / L["equity"], 2) if L.get("equity") else 0.0})
        sig = L.get("last_signals") or {}
        st = json.load(open(STAGE_OUT)) if os.path.exists(STAGE_OUT) else {}
        pnl = float(L.get("equity") or 0) - float(L.get("start") or 0)
        expo = json.load(open(EXPO_PATH)) if os.path.exists(EXPO_PATH) else None
        if not any(float(q) for q in (L.get("holdings") or {}).values()) and st.get("exposure"): expo = st["exposure"]
        alerts = []
        if os.path.exists(ALERTS):
            for ln in open(ALERTS).read().splitlines()[-50:]:
                try: alerts.append(json.loads(ln))
                except Exception: pass
        mark_alerts = [a for a in alerts if str(a.get("kind", "")).startswith("MARK_")][-5:]
        return {"status": "OK", "label": L.get("label"), "equity_usd": L.get("equity"), "cash_usd": L.get("cash"), "funded_usd": L.get("start"),
                "pnl_usd": round(pnl, 2), "nav_per_unit": L.get("nav_per_unit"), "drawdown_pct": L.get("drawdown_pct"),
                "kill": L.get("kill"), "review_flag": L.get("review_flag"), "sigma": L.get("sigma_current"), "scale": L.get("scale_current"),
                "costs_paid_usd": L.get("costs_paid_usd"), "cash_interest_usd": L.get("cash_interest_total_usd"), "holdings": hold,
                "signals": [{k: x.get(k) for k in ("name", "inst", "trend", "vol60", "notional_w")} for x in sig.get("sleeves", [])],
                "duration_gate": sig.get("duration_gate") or (((st.get("signals") or {}).get("duration_gate")) if st else None),
                "mf_usd": round(sum(h["value_usd"] for h in hold if h["inst"] in MF), 2),
                "mf_target_usd": ((st.get("orders") or {}).get("mf_target_usd")) if st else None,
                "signal_asof": sig.get("asof"), "rebalances": (L.get("rebalances") or [])[-3:], "updated": L.get("updated"),
                "next": "Entry: half at the Mon 5 Oct US close (06:50 AEDT Tue 6 Oct); mid-month refresh Fri 16 Oct close (06:50 AEDT Sat 17 Oct); "
                        "rest at the first November close, Mon 2 Nov, on the 30 Oct signal (07:50 AEDT Tue 3 Nov; US back on EST)",
                "stage": {"generated_utc": st.get("generated_utc"), "orders": [{k: r.get(k) for k in ("inst", "side", "order_qty", "notional_usd", "px")} for r in (st.get("orders") or {}).get("rows", [])],
                          "room": st.get("room"), "funding_plan": st.get("funding_plan")} if st else None,
                "levered": True, "exposure": expo, "mark_alerts": mark_alerts, "stale_marks": L.get("mark_stale") or {},
                "regime": _regime_now((expo or {}).get("regime")), "gap_risk": (expo or {}).get("gap_risk"),
                "rule": RULE_TEXT}
    except Exception as e:
        return {"status": "UNAVAILABLE", "error": str(e)[:200]}

def main():
    ap = argparse.ArgumentParser(description="DEEP18 trend sleeve (PAPER ONLY)")
    m = ap.add_mutually_exclusive_group(required=True)
    m.add_argument("--stage", action="store_true"); m.add_argument("--execute", action="store_true")
    m.add_argument("--mark", action="store_true"); m.add_argument("--status", action="store_true")
    m.add_argument("--sell-staged-only", action="store_true"); m.add_argument("--fetch-prices", choices=["last", "close", "asof"])
    ap.add_argument("--asof"); ap.add_argument("--signal-asof"); ap.add_argument("--scale", type=float)
    ap.add_argument("--base-usd", type=float); ap.add_argument("--price", choices=["last", "close"], default="last")
    ap.add_argument("--prices-file"); ap.add_argument("--alpaca-env"); ap.add_argument("--allow-yahoo", action="store_true")
    ap.add_argument("--reason", choices=["entry", "monthly", "refresh", "kill", "manual"], default="monthly")
    ap.add_argument("--mf-powder", dest="mf_powder", action="store_true", default=None,
                    help="let the MF basket also use free powder above the $50k reserve (default: on for monthly/refresh, off otherwise)")
    ap.add_argument("--no-mf-powder", dest="mf_powder", action="store_false")
    ap.add_argument("--sell-staged", action="store_true"); ap.add_argument("--fit", action="store_true")
    ap.add_argument("--force", action="store_true"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--write-plan", action="store_true"); ap.add_argument("--venue", default="AX")
    ap.add_argument("--need-usd", type=float); ap.add_argument("--execute-sales", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.fetch_prices:
        px = fetch_alpaca(a.fetch_prices, PRICE_SYMS, asof=a.asof, env_path=a.alpaca_env)
        miss = [s for s in PRICE_SYMS if s not in px]
        if miss: print("WARNING: no Alpaca price for %s" % miss, file=sys.stderr)
        doc = {"generated_utc": iso(), "mode": a.fetch_prices, "source": "alpaca feed=iex", "prices": px,
               "gate": tnx_gate(a.asof)}
        if a.out: atomic_json(a.out, doc)
        print(json.dumps(doc)); return
    if a.stage: cmd_stage(a)
    elif a.execute: cmd_execute(a)
    elif a.mark: cmd_mark(a)
    elif a.sell_staged_only: cmd_sell_staged_only(a)
    else: print(json.dumps(summary(), indent=1, default=str))

if __name__ == "__main__":
    main()
