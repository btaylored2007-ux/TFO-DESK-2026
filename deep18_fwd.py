#!/usr/bin/env python3
"""DEEP18 round-3 PAPER forward tests (FT1-FT5). SHADOW LEDGERS ONLY.

Funder decision relayed 2026-10-03 (Brett delegated the calls): wire all five round-3 candidates
(research/deep18/DEEP18_ROUND3_20261003.md section 9) as SHADOW forward tests with a War Room panel.
  * NOT firm capital. No orders are placed, previewed or queued. Not a registered book, not in firm equity,
    firm_risk, desk caps or funder_fee.py. trend18's ledger and every desk file are READ-ONLY here.
  * Only files written: state/deep18_fwd/*_shadow.json, *_shadow_history.csv, prices_snapshot_shadow.csv,
    run_log_shadow.txt (paths contain "shadow" -> book_coverage AUDIT_SKIP; JSON has no ledger-shaped keys).

Tests
  FT1 ft1_tmf_me  (H44c) TMF over the last 3 NYSE sessions of each month       } PAPER overlays on trend18's idle cash
  FT3 ft3_pension (H03b) month-end SPY/TLT rebalance (|SPY-TLT MTD| >= 3%)     } (the MF basket DBMF/KMLM/CTA), via a
  FT5 ft5_asx     (H26)  50% IOZ.AX + 50% STW.AX, first 15 ASX sessions Apr/Oct } read-only link to state/trend18.json
  FT2 ft2_glide   (K08)  R2 + drawdown dry-powder glide                         } separate WHOLE-BOOK shadows of R2, compared
  FT4 ft4_sig24   (K02r) R2 at sigma 24% + 4% BTAL + 10% bills from half the MF } with an R2 reference shadow (r2ref).
                                                                                  MUTUALLY EXCLUSIVE: only one could ever be adopted.
Overlay accounting: notional = trend18's live MF-basket value (read-only) when > 0, else the R2 MODEL idle cash
  (r2ref shadow value x (1 - sum of trend weights - 2.8% reserve)), clipped only by liquidity (1% of 60d $ADV per
  instrument). Daily P&L = position x (instrument return - MF basket return) - costs. Costs per side = 12.5bp +
  sqrt impact (sigma_d x sqrt(trade/ADV)) on the instrument, plus 12.5bp to sell/rebuy the MF basket (the swap).
  "After fee": the funder fee per calendar day also compounds on the overlay's cumulative P&L (FEE_SCHEDULE: 0.047619%/day
  for 3 Oct 2026, 0.0274%/day from 4 Oct 2026 -- Brett lowered it 3 Oct 19:17 AEST; mirrors funder_fee.RATE_SCHEDULE).
Book shadows: notional $1,787,506.89 (trend18 stage base = firm equity 2026-10-03), full scale, R2 engine =
  trend18.compute_signals (same code as the live sleeve) at sigma 0.20 (r2ref, ft2) or 0.24 (ft4); signal at the
  last US close of the month traded at the next close, mid-month refresh, idle (1 - sum w - 2.8%) in MF thirds,
  12.5bp/side ETF, 25bp/side BTC, -30% kill -> 10% vol until a new 6-month high. First trade = the Oct 2 signal at
  the next close (Mon 5 Oct), exactly like trend18's own entry. After-fee value = value x prod(1 - fee rate of each calendar day) (0.0274%/day from 4 Oct).
Prices: Yahoo daily bars via yfinance (adjusted closes for returns, close x volume for $ADV), fetched on the Mac;
  --prices-file accepts a snapshot fetched elsewhere (e.g. on the box) in the prices_snapshot_shadow.csv format.
Start: the latest final close at the first run (US 2026-10-02 / ASX 2026-10-02). Never backdated.
Commands: deep18_fwd.py run [--prices-file F] | status      (DEEP18_FWD_STATE_DIR overrides the state dir for tests)
"""
import argparse, csv, datetime as dt, json, math, os, sys
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.environ.get("DEEP18_FWD_STATE_DIR") or os.path.join(BASE, "state", "deep18_fwd")
TREND18_LEDGER = os.path.join(BASE, "state", "trend18.json")      # READ-ONLY
ET, SYD, UTC = ZoneInfo("America/New_York"), ZoneInfo("Australia/Sydney"), dt.timezone.utc
STATUS = "SHADOW_PAPER_NOT_IN_FIRM_EQUITY"
START_NOT_BEFORE = dt.date(2026, 10, 2)
# Funder fee per calendar day, mirrors funder_fee.RATE_SCHEDULE (append only; history kept):
#   0.047619%/day (0.3333%/wk / 7) for 3 Oct 2026; 0.0274%/day (~10%/yr) from 4 Oct 2026 (Brett, 3 Oct 2026 19:17 AEST).
FEE_SCHEDULE = [(dt.date(2026, 10, 2), (4000.0 / 1.2e6) / 7), (dt.date(2026, 10, 4), 0.000274)]
FEE_DAY = FEE_SCHEDULE[-1][1]          # current rate (0.0274%/day)

def fee_rate(day):
    r = FEE_SCHEDULE[0][1]
    for eff, rate in FEE_SCHEDULE:
        if day >= eff: r = rate
    return r

def fee_factor(d0, d1):
    """prod(1 - fee rate) over calendar days d0 < x <= d1 (1.0 if d1 <= d0)."""
    f, x = 1.0, d0 + dt.timedelta(days=1)
    while x <= d1: f *= 1 - fee_rate(x); x += dt.timedelta(days=1)
    return f
COST_SIDE = {"etf": 0.00125, "crypto": 0.0025}
SWAP_SIDE = 0.00125
ADV_FRAC = 0.01
RES_FRAC = 0.028
MIN_TRADE_USD = 250.0
SHADOW_NOTIONAL_USD = 1_787_506.89
KILL_DD, KILL_SIGMA, KILL_HIGH_N = -0.30, 0.10, 126
MF = ["DBMF", "KMLM", "CTA"]
R2_INST = {"UPRO": "UPRO", "TQQQ": "TQQQ", "TMF": "TMF", "UGL": "UGL", "XBT/USD": "BTC-USD"}
US_FINAL_AFTER = (16, 20)       # ET
ASX_FINAL_AFTER = (16, 30)      # Sydney
LEDGER_KEYS = {"cash", "positions", "holdings", "desks", "equity", "nav", "marked_equity", "start_cash"}
SYMS = sorted(set(["TMF", "SPY", "TLT", "IOZ.AX", "STW.AX", "AUDUSD=X", "^IRX", "BTAL", "UPRO", "TQQQ", "UGL", "BTC-USD"] + MF))

OVERLAYS = {
 "ft1_tmf_me": dict(fid="FT1", rank=3, name="FT1 TMF month-end (H44c)", insts=["TMF"], venue="US", exp_sr=0.10,
                    min_months=12, min_events=12, eval_months=24, kill_t=-1.5, kill_t_after="6 months",
                    rule="Hold TMF over the last 3 NYSE sessions of each month: buy at the close of the 4th-last session, sell at the month-end close.",
                    kill_rule="forward t <= -1.5 after 6 months; costs > 50% of gross edge after 6 events; fill > 30bp worse than model close (n/a on paper: fills = model close)"),
 "ft3_pension": dict(fid="FT3", rank=4, name="FT3 Pension month-end rebalance (H03b)", insts=["SPY", "TLT"], venue="US", exp_sr=0.15,
                    min_months=12, min_events=8, eval_months=24, kill_t=-1.5, kill_t_after="6 months",
                    rule="At the 4th-last session close: D = SPY MTD - TLT MTD (total return). D <= -3% -> SPY, D >= +3% -> TLT, held for the last 3 sessions; else stay in MF.",
                    kill_rule="forward t <= -1.5 after 6 months; costs > 50% of gross edge after 6 events"),
 "ft5_asx":    dict(fid="FT5", rank=5, name="FT5 ASX first 15 sessions Apr/Oct (H26)", insts=["IOZ.AX", "STW.AX"], venue="ASX", exp_sr=0.08,
                    min_months=12, min_events=2, eval_months=36, eval_events=6, kill_t=-1.5, kill_t_after="4 events",
                    rule="Hold 50% IOZ.AX + 50% STW.AX over the first 15 ASX sessions of April and October (AUD; FX P&L reported separately).",
                    kill_rule="costs > 50% of gross edge; forward t <= -1.5 after 4 events"),
}
BOOKS = {
 "ft2_glide": dict(fid="FT2", rank=1, name="FT2 Drawdown dry-powder glide (K08) on R2", sigma=0.20, glide=True, btal=False, exp_sr=0.20,
                   min_months=36, eval_months=36, dd_kill_pp=10.0,
                   rule="Month-end: after-fee value V vs its HWM H. V <= 0.80H -> 50% of idle cash into R2's risky sleeves pro rata; V <= 0.70H -> 100%; restore at V >= 0.95H. 25bp per move (12.5bp/side).",
                   kill_rule="shadow DD > 10pp deeper than the R2 reference at any point; forward active t <= -1.5 after 12 months"),
 "ft4_sig24": dict(fid="FT4", rank=2, name="FT4 R2 sigma-24 + 4% BTAL (K02r)", sigma=0.24, glide=False, btal=True, exp_sr=0.15,
                   min_months=36, eval_months=36, dd_kill_pp=8.0,
                   rule="R2 with a 24% vol target; 4% BTAL and 10% bills funded from half the MF sleeve (min(4%, idle x 0.5 x 4/14), min(10%, idle x 0.5 x 10/14)).",
                   kill_rule="shadow DD > 8pp deeper than the R2 reference; BTAL 60d $ADV < $3m"),
}
REF = dict(fid="REF", name="R2 reference shadow (sigma 20%, same engine)", sigma=0.20, glide=False, btal=False)
ORDER = ["ft2_glide", "ft4_sig24", "ft1_tmf_me", "ft3_pension", "ft5_asx"]

# ------------------------------------------------------------------------------------------------ calendars
def _nyse():
    sys.path.insert(0, BASE)
    import shadow_nasdaq2x as s
    return s

def _easter(y): return _nyse()._easter(y)

def asx_holidays(y):
    e = _easter(y); h = {e - dt.timedelta(days=2), e + dt.timedelta(days=1), dt.date(y, 4, 25)}
    d = dt.date(y, 6, 1); d += dt.timedelta(days=(0 - d.weekday()) % 7); h.add(d + dt.timedelta(weeks=1))   # King's Birthday
    for md in ((1, 1), (1, 26)):
        x = dt.date(y, *md); h.add(x if x.weekday() < 5 else x + dt.timedelta(days=7 - x.weekday()))
    c = dt.date(y, 12, 25)
    if c.weekday() == 5: h |= {dt.date(y, 12, 27), dt.date(y, 12, 28)}
    elif c.weekday() == 6: h |= {dt.date(y, 12, 26), dt.date(y, 12, 27)}
    elif c.weekday() == 4: h |= {c, dt.date(y, 12, 28)}
    else: h |= {c, dt.date(y, 12, 26)}
    return {x for x in h if x.weekday() < 5}

def is_session(d, venue):
    if venue == "US": return _nyse().is_session(d)
    return d.weekday() < 5 and d not in asx_holidays(d.year)

def sessions_between(a, b, venue):
    out, d = [], a
    while d <= b:
        if is_session(d, venue): out.append(d)
        d += dt.timedelta(days=1)
    return out

def next_session(d, venue):
    d += dt.timedelta(days=1)
    while not is_session(d, venue): d += dt.timedelta(days=1)
    return d

def prev_session(d, venue):
    d -= dt.timedelta(days=1)
    while not is_session(d, venue): d -= dt.timedelta(days=1)
    return d

def month_sessions(d, venue):
    a = d.replace(day=1); b = (a + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    return sessions_between(a, b, venue)

def final_session(venue, now=None):
    now = now or dt.datetime.now(UTC)
    loc = now.astimezone(ET if venue == "US" else SYD); d = loc.date()
    cut = US_FINAL_AFTER if venue == "US" else ASX_FINAL_AFTER
    if is_session(d, venue) and (loc.hour, loc.minute) >= cut: return d
    return prev_session(d, venue)

# ------------------------------------------------------------------------------------------------ signals (frozen, pre-registered)
def sig_ft1(px, d):
    n = next_session(d, "US")
    return {"TMF": 1.0} if n in month_sessions(n, "US")[-3:] else {}

def sig_ft3(px, d):
    n = next_session(d, "US"); m = month_sessions(n, "US")
    if len(m) < 6 or n not in m[-3:]: return {}, None
    sig_d = m[-4]; prev = prev_session(m[0], "US")
    a = px.get("adj", {})
    try:
        D = (a["SPY"][sig_d] / a["SPY"][prev] - 1) - (a["TLT"][sig_d] / a["TLT"][prev] - 1)
    except KeyError:
        return {}, None
    return ({"SPY": 1.0} if D <= -0.03 else ({"TLT": 1.0} if D >= 0.03 else {})), round(D, 5)

def sig_ft5(px, d):
    n = next_session(d, "ASX")
    return {"IOZ.AX": 0.5, "STW.AX": 0.5} if n.month in (4, 10) and n in month_sessions(n, "ASX")[:15] else {}

# ------------------------------------------------------------------------------------------------ prices
def fetch_prices(period="1y"):
    """{'adj': {sym: {date: v}}, 'close': {...}, 'vol': {...}} from Yahoo via yfinance."""
    import yfinance as yf
    df = yf.download(SYMS, period=period, interval="1d", auto_adjust=False, actions=False, progress=False, group_by="column", threads=True)
    out = {"adj": {}, "close": {}, "vol": {}}
    if df is None or df.empty: return out
    for s in SYMS:
        for key, col in (("adj", "Adj Close"), ("close", "Close"), ("vol", "Volume")):
            try: ser = df[(col, s)]
            except KeyError: continue
            rows = {}
            for ts, v in ser.items():
                if v == v and v is not None and (key == "vol" or v > 0): rows[ts.date()] = float(v)
            if rows: out[key][s] = rows
    return out

def save_snapshot(px, path):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["date", "symbol", "adj_close", "close", "volume"])
        for s in sorted(px["adj"]):
            for d in sorted(px["adj"][s]):
                w.writerow([d.isoformat(), s, px["adj"][s][d], px["close"].get(s, {}).get(d, ""), px["vol"].get(s, {}).get(d, "")])
    os.replace(tmp, path)

def load_snapshot(path):
    out = {"adj": {}, "close": {}, "vol": {}}
    for r in csv.DictReader(open(path)):
        d = dt.date.fromisoformat(r["date"]); s = r["symbol"]
        out["adj"].setdefault(s, {})[d] = float(r["adj_close"])
        if r["close"]: out["close"].setdefault(s, {})[d] = float(r["close"])
        if r["volume"]: out["vol"].setdefault(s, {})[d] = float(r["volume"])
    return out

def _ret(px, s, a, b):
    p = px["adj"].get(s, {})
    return p[b] / p[a] - 1 if a in p and b in p else None

def _last_le(series, d):
    ks = [k for k in series if k <= d]
    return series[max(ks)] if ks else None

def liq(px, s, d, n=60):
    """(60d mean $ADV in local ccy, 60d daily vol) up to d."""
    c, v, a = px["close"].get(s, {}), px["vol"].get(s, {}), px["adj"].get(s, {})
    ds = sorted(x for x in c if x <= d and x in v)[-n:]
    adv = sum(c[x] * v[x] for x in ds) / len(ds) if ds else 0.0
    da = sorted(x for x in a if x <= d)[-(n + 1):]
    rs = [a[da[i]] / a[da[i - 1]] - 1 for i in range(1, len(da))]
    if len(rs) > 2:
        m = sum(rs) / len(rs); sd = math.sqrt(sum((r - m) ** 2 for r in rs) / (len(rs) - 1))
    else: sd = 0.02
    return adv, sd

def mf_ret(px, a, b):
    rs = [_ret(px, s, a, b) for s in MF]
    rs = [r for r in rs if r is not None]
    return sum(rs) / len(rs) if rs else 0.0

def rf_annual(px, d):
    v = _last_le(px["adj"].get("^IRX", {}), d)
    return (v / 100.0) if v is not None else 0.04

# ------------------------------------------------------------------------------------------------ state io
def _paths(tid):
    return os.path.join(STATE_DIR, "%s_shadow.json" % tid), os.path.join(STATE_DIR, "%s_shadow_history.csv" % tid)

def _guard(obj):
    bad = LEDGER_KEYS & set(obj)
    if bad: raise ValueError("refusing ledger-shaped keys %s in shadow state" % sorted(bad))

def load(tid):
    p, _ = _paths(tid)
    try:
        with open(p) as f: return json.load(f)
    except Exception: return None

def save(tid, st, rows=()):
    _guard(st); os.makedirs(STATE_DIR, exist_ok=True); p, h = _paths(tid)
    tmp = p + ".tmp"
    with open(tmp, "w") as f: json.dump(st, f, indent=1, default=str)
    os.replace(tmp, p)
    if rows:
        fields = list(rows[0].keys()); new = not os.path.exists(h)
        if not new:
            with open(h) as f: fields = next(csv.reader(f))
        with open(h, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            if new: w.writeheader()
            for r in rows: w.writerow(r)

def history(tid):
    _, h = _paths(tid)
    if not os.path.exists(h): return []
    with open(h) as f: return list(csv.DictReader(f))

def log(line):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, "run_log_shadow.txt"), "a") as f:
        f.write("%s %s\n" % (dt.datetime.now(SYD).strftime("%Y-%m-%d %H:%M %Z"), line))

def trend18_idle_usd():
    """READ-ONLY link: trend18's MF basket value (its idle cash) from state/trend18.json."""
    try:
        with open(TREND18_LEDGER) as f: L = json.load(f)
        h, p = L.get("holdings") or {}, L.get("mark_px_usd") or {}
        return round(sum(float(h.get(s, 0) or 0) * float(p.get(s, 0) or 0) for s in MF), 2), float(L.get("nav_per_unit") or 1.0), L.get("updated")
    except Exception:
        return 0.0, None, None

# ------------------------------------------------------------------------------------------------ R2 whole-book shadow engine
_SIG_CACHE = {}
def r2_signals(asof, sigma, signal_fn=None):
    key = (asof.isoformat(), sigma)
    if key not in _SIG_CACHE:
        if signal_fn is None:
            sys.path.insert(0, BASE); import trend18
            signal_fn = trend18.compute_signals
        s = signal_fn(asof=asof.isoformat(), sigma=sigma)
        if s.get("asof") != asof.isoformat():
            raise RuntimeError("R2 signals as of %s unavailable (got %s)" % (asof, s.get("asof")))
        _SIG_CACHE[key] = {"asof": s["asof"], "w": {R2_INST[x["inst"]]: float(x["notional_w"]) for x in s["sleeves"]},
                           "gate_open": (s.get("duration_gate") or {}).get("open"), "capped": s.get("capped")}
    return _SIG_CACHE[key]

def book_value(st): return st["bills_usd"] + sum(st["pos_usd"].values())

def book_targets(st, cfg, v):
    """Target $ per instrument (and bills) from the stored R2 trend weights + test modifications."""
    w = dict(st.get("trend_w") or {})
    if st["r2_kill"]["active"]:
        w = {k: x * KILL_SIGMA / cfg["sigma"] for k, x in w.items()}
    sw = sum(w.values()); idle = max(0.0, 1.0 - sw - RES_FRAC)
    tgt = {k: x for k, x in w.items()}; bills = RES_FRAC
    if cfg.get("glide") and st.get("glide_m", 0) > 0 and sw > 0:
        mv = st["glide_m"] * idle
        for k in tgt: tgt[k] += mv * w[k] / sw
        idle -= mv
    if cfg.get("btal"):
        bt = min(0.04, idle * 0.5 * 4 / 14); bl = min(0.10, idle * 0.5 * 10 / 14)
        tgt["BTAL"] = bt; bills += bl; idle -= bt + bl
    for m in MF: tgt[m] = tgt.get(m, 0.0) + idle / 3
    return {k: x * v for k, x in tgt.items()}, bills * v, idle

def book_rebalance(st, cfg, v, events, why):
    tgt, bills_tgt, _ = book_targets(st, cfg, v); cost = 0.0; traded = 0.0
    for k in set(tgt) | set(st["pos_usd"]):
        cur = st["pos_usd"].get(k, 0.0); t = tgt.get(k, 0.0); dlt = t - cur
        if abs(dlt) < MIN_TRADE_USD and t > 0: continue
        c = abs(dlt) * COST_SIDE["crypto" if k == "BTC-USD" else "etf"]
        st["pos_usd"][k] = t; st["bills_usd"] -= dlt + c; cost += c; traded += abs(dlt)
    st["pos_usd"] = {k: x for k, x in st["pos_usd"].items() if abs(x) > 0.005}
    events.append("%s rebalance: traded %.0f, cost %.0f" % (why, traded, cost))
    return cost

def new_book(tid, cfg, start, px, signal_fn=None):
    s = r2_signals(start, cfg["sigma"], signal_fn)
    st = {"id": tid, "kind": "book_shadow", "status": STATUS, "label": cfg["name"], "paper_shadow_only": True, "in_firm_equity": False,
          "orders": "NONE", "start_date": start.isoformat(), "started_utc": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
          "notional_usd": SHADOW_NOTIONAL_USD, "sigma": cfg["sigma"], "bills_usd": SHADOW_NOTIONAL_USD, "pos_usd": {},
          "trend_w": None, "pending_w": s["w"], "pending_signal_asof": s["asof"], "pending_trade_date": next_session(start, "US").isoformat(),
          "signal_asof": None, "gate_open": s["gate_open"], "after_fee_usd": SHADOW_NOTIONAL_USD, "af_hwm_usd": SHADOW_NOTIONAL_USD,
          "peak_usd": SHADOW_NOTIONAL_USD, "max_dd_pct": 0.0, "glide_m": 0.0, "glide_months_on": 0, "glide_episodes": 0,
          "glide_episodes_completed": 0, "r2_kill": {"active": False, "ref_usd": SHADOW_NOTIONAL_USD, "since": None},
          "recent_values": [SHADOW_NOTIONAL_USD], "costs_usd": 0.0, "last_date": start.isoformat(), "sessions": 1,
          "kill": {"active": False, "reason": ""}}
    row = book_row(st, start, 0.0, ["START %.0f in bills at the %s close; R2 sigma %.0f%% signal as of %s, first trade at the %s close"
                                    % (SHADOW_NOTIONAL_USD, start, 100 * cfg["sigma"], s["asof"], st["pending_trade_date"])], cfg)
    return st, [row]

def book_row(st, d, cost, events, cfg):
    v = book_value(st)
    _, _, idle = book_targets(st, cfg, v) if st.get("trend_w") else ({}, 0, 0.0)
    return {"date": d.isoformat(), "value_usd": round(v, 2), "after_fee_usd": round(st["after_fee_usd"], 2),
            "ret_since_start_pct": round(100 * (v / st["notional_usd"] - 1), 4), "dd_pct": round(100 * (v / st["peak_usd"] - 1), 4),
            "bills_usd": round(st["bills_usd"], 2), "mf_usd": round(sum(st["pos_usd"].get(m, 0) for m in MF), 2),
            "btal_usd": round(st["pos_usd"].get("BTAL", 0.0), 2),
            "risky_usd": round(sum(x for k, x in st["pos_usd"].items() if k not in MF and k != "BTAL"), 2),
            "model_idle_usd": round(v * max(0.0, 1 - sum((st.get("trend_w") or st.get("pending_w") or {}).values()) - RES_FRAC), 2),
            "glide_m": st.get("glide_m", 0.0), "r2_kill": st["r2_kill"]["active"], "cost_usd": round(cost, 2), "events": "; ".join(events)}

def advance_book(tid, cfg, st, px, upto, signal_fn=None):
    rows = []
    last = dt.date.fromisoformat(st["last_date"])
    adj = px["adj"]
    for d in sessions_between(last + dt.timedelta(days=1), upto, "US"):
        if d not in adj.get("SPY", {}):
            if any(k > d for k in adj.get("SPY", {})): continue
            break
        prev = dt.date.fromisoformat(st["last_date"]); events = []; cost = 0.0
        v0 = book_value(st)
        st["bills_usd"] *= (1 + rf_annual(px, prev) / 365.0) ** (d - prev).days
        for k in list(st["pos_usd"]):
            r = _ret(px, k, prev, d)
            if r is None:
                p = adj.get(k, {}); a = _last_le(p, prev); b = _last_le(p, d)
                r = (b / a - 1) if a and b else 0.0
                events.append("%s mark carried from last bar" % k)
            st["pos_usd"][k] *= 1 + r
        v = book_value(st)
        st["after_fee_usd"] *= (v / v0 if v0 > 0 else 1.0) * fee_factor(prev, d)
        st["af_hwm_usd"] = max(st["af_hwm_usd"], st["after_fee_usd"])
        st["peak_usd"] = max(st["peak_usd"], v); st["max_dd_pct"] = min(st["max_dd_pct"], round(100 * (v / st["peak_usd"] - 1), 4))
        st["recent_values"] = (st.get("recent_values") or [])[-(KILL_HIGH_N - 1):] + [v]
        need_rb = None
        # R2 internal kill, mirrors trend18 (-30% vs kill reference -> 10% vol until a new 6-month high)
        k = st["r2_kill"]; k["ref_usd"] = max(k["ref_usd"], v) if not k["active"] else k["ref_usd"]
        if not k["active"] and v / k["ref_usd"] - 1 <= KILL_DD:
            k.update(active=True, since=d.isoformat()); need_rb = "R2 KILL (10% vol)"; events.append("R2 kill: -30% from reference")
        elif k["active"] and len(st["recent_values"]) >= 2 and v >= max(st["recent_values"][:-1]):
            k.update(active=False, since=None, ref_usd=v); need_rb = "R2 kill cleared"; events.append("R2 kill cleared at a new 6-month high")
        if st.get("pending_trade_date") == d.isoformat():
            st["trend_w"] = st.pop("pending_w"); st["signal_asof"] = st.pop("pending_signal_asof"); st.pop("pending_trade_date")
            need_rb = "monthly (signal %s)" % st["signal_asof"]
        ms = month_sessions(d, "US")
        if st.get("trend_w") and d == ms[len(ms) // 2] and not need_rb: need_rb = "mid-month refresh"
        if cfg.get("glide") and d == ms[-1]:
            V, H, m = st["after_fee_usd"], st["af_hwm_usd"], st.get("glide_m", 0.0); newm = m
            if V <= 0.70 * H: newm = 1.0
            elif V <= 0.80 * H: newm = max(m, 0.5)
            if V >= 0.95 * H: newm = 0.0
            if newm > 0: st["glide_months_on"] = st.get("glide_months_on", 0) + 1
            if newm != m:
                if m == 0 and newm > 0: st["glide_episodes"] = st.get("glide_episodes", 0) + 1
                if m > 0 and newm == 0: st["glide_episodes_completed"] = st.get("glide_episodes_completed", 0) + 1
                st["glide_m"] = newm; events.append("GLIDE m %.1f -> %.1f (after-fee %.0f vs HWM %.0f)" % (m, newm, V, H))
                if st.get("trend_w"): need_rb = need_rb or "glide move"
        if need_rb and st.get("trend_w"): cost += book_rebalance(st, cfg, book_value(st), events, need_rb)
        if d == ms[-1]:
            s = r2_signals(d, cfg["sigma"], signal_fn)
            st["pending_w"] = s["w"]; st["pending_signal_asof"] = s["asof"]; st["pending_trade_date"] = next_session(d, "US").isoformat()
            st["gate_open"] = s["gate_open"]; events.append("R2 signal %s staged for the %s close" % (s["asof"], st["pending_trade_date"]))
        if cost:
            st["after_fee_usd"] *= book_value(st) / v
        st["costs_usd"] += cost; st["last_date"] = d.isoformat(); st["sessions"] += 1
        rows.append(book_row(st, d, cost, events, cfg))
    return st, rows

# ------------------------------------------------------------------------------------------------ overlay engine
def new_overlay(tid, cfg, start):
    return {"id": tid, "kind": "overlay", "status": STATUS, "label": cfg["name"], "paper_shadow_only": True, "in_firm_equity": False,
            "orders": "NONE", "start_date": start.isoformat(), "started_utc": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "held": {}, "pos_usd": {}, "notional_usd": 0.0, "cum_pnl_usd": 0.0, "cum_pnl_after_fee_usd": 0.0, "gross_usd": 0.0,
            "costs_usd": 0.0, "fx_pnl_usd": 0.0, "events": 0, "event_log": [], "last_us": None, "last_asx": None,
            "last_fee_date": start.isoformat(), "mf_displaced_usd": 0.0, "idle_usd": 0.0, "idle_basis": None,
            "kill": {"active": False, "reason": ""}}

def idle_for(d, live_idle):
    """Idle cash for overlay sizing: trend18's live MF basket (read-only) if funded, else R2 model idle from r2ref."""
    if live_idle and live_idle > 0: return live_idle, "trend18 MF basket (live, read-only)"
    best = None
    for r in history("r2ref"):
        if r["date"] <= d.isoformat(): best = r
    if best: return float(best["model_idle_usd"]), "R2 model idle (r2ref shadow; trend18 MF basket not funded)"
    return SHADOW_NOTIONAL_USD * 0.40, "R2 model idle (fallback 40% of $1.79m)"

def _fee(st, d):
    cd = (d - dt.date.fromisoformat(st["last_fee_date"])).days
    if cd > 0: st["cum_pnl_after_fee_usd"] *= fee_factor(dt.date.fromisoformat(st["last_fee_date"]), d); st["last_fee_date"] = d.isoformat()

def _overlay_rebalance(st, cfg, tgt, d, px, idle, basis, events, extra=""):
    fx = 1.0
    if cfg["venue"] == "ASX":
        fx = _last_le(px["adj"].get("AUDUSD=X", {}), d) or 0.65
    caps = []; impacts = {}
    for k, w in tgt.items():
        adv_l, sd = liq(px, k, d); adv = adv_l * fx; impacts[k] = (adv, sd)
        caps.append(ADV_FRAC * adv / w if w > 0 else float("inf"))
    for k in st["held"]:
        if k not in impacts:
            adv_l, sd = liq(px, k, d); impacts[k] = (adv_l * fx, sd)
    N = min([idle] + caps) if tgt else 0.0
    cost = 0.0; old_tot = sum(st["pos_usd"].values())
    new = {k: w * N for k, w in tgt.items()}
    for k in set(new) | set(st["pos_usd"]):
        tr = abs(new.get(k, 0.0) - st["pos_usd"].get(k, 0.0))
        if tr > 0:
            adv, sd = impacts[k]; cost += tr * (COST_SIDE["etf"] + sd * math.sqrt(tr / max(adv, 1.0)))
    swap = abs(sum(new.values()) - old_tot) * SWAP_SIDE; cost += swap
    if tgt and not st["held"]:
        st["events"] += 1
        st["event_log"] = (st["event_log"] + [{"entered": d.isoformat(), "insts": sorted(tgt), "notional_usd": round(N, 2), "note": extra}])[-24:]
        events.append("ENTER %s notional %.0f (idle %.0f, liquidity cap %.0f)%s" % ("+".join(sorted(tgt)), N, idle, min(caps) if caps else 0, extra))
    elif not tgt and st["held"]:
        events.append("EXIT %s -> MF basket" % "+".join(sorted(st["held"])))
        if st["event_log"]: st["event_log"][-1]["exited"] = d.isoformat()
    st["held"] = dict(tgt); st["pos_usd"] = new; st["notional_usd"] = round(N, 2)
    st["idle_usd"] = round(idle, 2); st["idle_basis"] = basis
    return cost

def _overlay_row(st, d, leg, day_ret, mfr, cost, pnl, idle, events, fxp=0.0):
    return {"date": d.isoformat(), "leg": leg, "notional_usd": st["notional_usd"], "held": "+".join(sorted(st["held"])) or "MF",
            "inst_ret": round(day_ret, 6), "mf_ret": round(mfr, 6), "cost_usd": round(cost, 2), "pnl_usd": round(pnl, 2),
            "cum_pnl_usd": round(st["cum_pnl_usd"], 2), "cum_pnl_after_fee_usd": round(st["cum_pnl_after_fee_usd"], 2),
            "fx_pnl_usd": round(fxp, 2), "idle_usd": round(idle, 2), "active_ret": (pnl / idle) if idle else 0.0, "events": "; ".join(events)}

def advance_overlay_us(tid, cfg, st, px, upto, live_idle):
    rows = []
    start = dt.date.fromisoformat(st["start_date"])
    first = start if st["last_us"] is None else dt.date.fromisoformat(st["last_us"]) + dt.timedelta(days=1)
    for d in sessions_between(first, upto, "US"):
        if d not in px["adj"].get("SPY", {}):
            if any(k > d for k in px["adj"].get("SPY", {})): continue
            break
        events = []; pnl = 0.0; day_ret = 0.0; mfr = 0.0
        idle, basis = idle_for(d, live_idle)
        if st["last_us"] is not None and st["held"]:
            prev = dt.date.fromisoformat(st["last_us"]); mfr = mf_ret(px, prev, d); tot = sum(st["pos_usd"].values())
            for k in list(st["pos_usd"]):
                r = _ret(px, k, prev, d) or 0.0; pnl += st["pos_usd"][k] * (r - mfr); st["pos_usd"][k] *= 1 + r
            day_ret = sum(st["pos_usd"].values()) / tot - 1 if tot else 0.0
            st["gross_usd"] += pnl
        extra = ""
        if tid == "ft3_pension":
            tgt, D = sig_ft3(px, d)
            if D is not None and not st["held"]: extra = " (D = %+.2f%%)" % (100 * D)
            if D is not None and not tgt and not st["held"] and d == month_sessions(d, "US")[-4]:
                events.append("FT3 no trade: D = %+.2f%% inside +/-3%%" % (100 * D))
        else:
            tgt = sig_ft1(px, d)
        cost = 0.0
        if tgt != st["held"]: cost = _overlay_rebalance(st, cfg, tgt, d, px, idle, basis, events, extra)
        else: st["idle_usd"] = round(idle, 2); st["idle_basis"] = basis
        pnl -= cost; st["costs_usd"] += cost
        _fee(st, d); st["cum_pnl_usd"] += pnl; st["cum_pnl_after_fee_usd"] += pnl
        st["last_us"] = d.isoformat()
        rows.append(_overlay_row(st, d, "US", day_ret, mfr, cost, pnl, idle, events))
    return st, rows

def advance_overlay_asx(tid, cfg, st, px, upto_us, upto_asx, live_idle):
    """FT5: ASX closes mark the instruments and decide; each later US close charges the displaced MF basket."""
    rows = []
    start = dt.date.fromisoformat(st["start_date"])
    a0 = start if st["last_asx"] is None else dt.date.fromisoformat(st["last_asx"]) + dt.timedelta(days=1)
    u0 = start if st["last_us"] is None else dt.date.fromisoformat(st["last_us"]) + dt.timedelta(days=1)
    evs = [(d, 0) for d in sessions_between(a0, upto_asx, "ASX")] + [(d, 1) for d in sessions_between(u0, upto_us, "US")]
    for d, kind in sorted(evs):
        ref = "IOZ.AX" if kind == 0 else "SPY"
        if d not in px["adj"].get(ref, {}):
            if any(k > d for k in px["adj"].get(ref, {})): continue   # unscheduled closure: no bar, later bars exist
            break                                                       # bar not final yet: stop, retry next run
        events = []; pnl = 0.0; day_ret = 0.0; mfr = 0.0; fxp = 0.0; cost = 0.0
        idle, basis = idle_for(d, live_idle)
        if kind == 0:
            if st["last_asx"] is not None and st["held"]:
                prev = dt.date.fromisoformat(st["last_asx"]); tot = sum(st["pos_usd"].values())
                fr = _ret(px, "AUDUSD=X", _nearest(px, "AUDUSD=X", prev), _nearest(px, "AUDUSD=X", d)) or 0.0
                for k in list(st["pos_usd"]):
                    r = _ret(px, k, prev, d) or 0.0; pnl += st["pos_usd"][k] * r; st["pos_usd"][k] *= 1 + r
                fxp = tot * fr; st["fx_pnl_usd"] += fxp
                day_ret = sum(st["pos_usd"].values()) / tot - 1 if tot else 0.0
                st["gross_usd"] += pnl
            tgt = sig_ft5(px, d); extra = ""
            if tgt and not st["held"]:
                ms = month_sessions(next_session(d, "ASX"), "ASX")[:15]; k_in = ms.index(next_session(d, "ASX"))
                if k_in > 0: extra = " (partial window: entered after session %d of 15, holds %d sessions)" % (k_in, 15 - k_in)
            if tgt != st["held"]: cost = _overlay_rebalance(st, cfg, tgt, d, px, idle, basis, events, extra)
            else: st["idle_usd"] = round(idle, 2); st["idle_basis"] = basis
            st["last_asx"] = d.isoformat()
        else:
            disp = st.get("mf_displaced_usd", 0.0)
            if st["last_us"] is not None and disp:
                prev = dt.date.fromisoformat(st["last_us"]); mfr = mf_ret(px, prev, d); pnl -= disp * mfr; st["gross_usd"] -= disp * mfr
            st["mf_displaced_usd"] = round(sum(st["pos_usd"].values()), 2)
            st["last_us"] = d.isoformat()
        pnl -= cost; st["costs_usd"] += cost
        _fee(st, d); st["cum_pnl_usd"] += pnl; st["cum_pnl_after_fee_usd"] += pnl
        rows.append(_overlay_row(st, d, "ASX" if kind == 0 else "US-MF", day_ret, mfr, cost, pnl, idle, events, fxp))
    return st, rows

def _nearest(px, s, d):
    ks = [k for k in px["adj"].get(s, {}) if k <= d]
    return max(ks) if ks else d

# ------------------------------------------------------------------------------------------------ progress, verdicts, kills
def _tstat(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) <= 20: return None
    m = sum(xs) / len(xs); sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
    return round(m / sd * math.sqrt(len(xs)), 3) if sd > 0 else None

def _months(st, last):
    return round((dt.date.fromisoformat(last) - dt.date.fromisoformat(st["start_date"])).days / 30.44, 2) if last else 0.0

def overlay_progress(tid, st, H=None):
    cfg = OVERLAYS[tid]; H = history(tid) if H is None else H
    last = max([x for x in (st.get("last_us"), st.get("last_asx")) if x] or [st["start_date"]])
    months = _months(st, last); t = _tstat([float(r["active_ret"]) for r in H])
    pct = min(1.0, months / cfg["min_months"], st["events"] / max(cfg["min_events"], 1))
    gross = st.get("gross_usd", 0.0); cost_share = st["costs_usd"] / max(abs(gross), 1.0) if st["events"] else 0.0
    verdict = "RUNNING"
    eval_ok = months >= cfg["eval_months"] and st["events"] >= cfg.get("eval_events", 0)
    if eval_ok:
        verdict = "PASS (keep running on paper)" if (t or 0) >= 1.0 and st["cum_pnl_after_fee_usd"] > 0 else "FAIL (retire)"
    return {"months": months, "events": st["events"], "min_months": cfg["min_months"], "min_events": cfg["min_events"],
            "eval_months": cfg["eval_months"], "pct_to_min": round(pct, 3), "min_met": pct >= 1.0, "t": t,
            "cost_share_of_gross": round(cost_share, 3), "verdict": verdict, "exp_active_sr": cfg["exp_sr"],
            "years_to_t2_at_exp_sr": round((2 / cfg["exp_sr"]) ** 2)}

def overlay_kill(tid, st, pr):
    cfg = OVERLAYS[tid]
    if st["kill"]["active"]: return
    if tid == "ft5_asx":
        if pr["events"] >= 4 and pr["t"] is not None and pr["t"] <= cfg["kill_t"]:
            st["kill"] = {"active": True, "reason": "forward t %.2f <= -1.5 after 4 events" % pr["t"]}
        if pr["events"] >= 4 and pr["cost_share_of_gross"] > 0.5:   # read as 'after 4 events' too, so one noisy window cannot kill it
            st["kill"] = {"active": True, "reason": "costs %.0f%% of gross edge after %d events" % (100 * pr["cost_share_of_gross"], pr["events"])}
        return
    if pr["months"] >= 6 and pr["t"] is not None and pr["t"] <= cfg["kill_t"]:
        st["kill"] = {"active": True, "reason": "forward t %.2f <= -1.5 after 6 months" % pr["t"]}
    if pr["events"] >= 6 and pr["cost_share_of_gross"] > 0.5:
        st["kill"] = {"active": True, "reason": "implementation shortfall: costs %.0f%% of gross edge after %d events" % (100 * pr["cost_share_of_gross"], pr["events"])}

def _cagr(v1, v0, days):
    return (v1 / v0) ** (365.25 / days) - 1 if days >= 365 and v0 > 0 else None

def book_progress(tid, st, ref, H=None, HR=None):
    cfg = BOOKS[tid]; H = history(tid) if H is None else H; HR = history("r2ref") if HR is None else HR
    months = _months(st, st["last_date"])
    rv = {r["date"]: float(r["value_usd"]) for r in HR}
    act = []; prev = None; dd_gap_worst = 0.0
    for r in H:
        if prev and r["date"] in rv and prev["date"] in rv:
            a = float(r["value_usd"]) / float(prev["value_usd"]) - 1; b = rv[r["date"]] / rv[prev["date"]] - 1; act.append(a - b)
        prev = r
    rdd = {r["date"]: float(r["dd_pct"]) for r in HR}
    for r in H:
        if r["date"] in rdd: dd_gap_worst = min(dd_gap_worst, float(r["dd_pct"]) - rdd[r["date"]])
    t = _tstat(act)
    days = (dt.date.fromisoformat(st["last_date"]) - dt.date.fromisoformat(st["start_date"])).days
    af_ret = st["after_fee_usd"] / st["notional_usd"] - 1; ref_af = (ref["after_fee_usd"] / ref["notional_usd"] - 1) if ref else None
    pct = min(1.0, months / cfg["min_months"])
    min_met = months >= cfg["min_months"] and (not cfg.get("glide") or st.get("glide_episodes_completed", 0) >= 1)
    verdict = "RUNNING"
    if min_met and ref:
        c1, c0 = _cagr(st["after_fee_usd"], st["notional_usd"], days), _cagr(ref["after_fee_usd"], ref["notional_usd"], days)
        ok = c1 is not None and c0 is not None and c1 >= c0 + 0.005 and abs(st["max_dd_pct"]) <= 1.25 * abs(ref["max_dd_pct"])
        verdict = "PASS (keep running on paper)" if ok else "FAIL (retire)"
    return {"months": months, "min_months": cfg["min_months"], "eval_months": cfg["eval_months"], "pct_to_min": round(pct, 3),
            "min_met": min_met, "t": t, "after_fee_ret_pct": round(100 * af_ret, 3),
            "ref_after_fee_ret_pct": round(100 * ref_af, 3) if ref_af is not None else None,
            "excess_after_fee_pp": round(100 * (af_ret - ref_af), 3) if ref_af is not None else None,
            "max_dd_pct": st["max_dd_pct"], "ref_max_dd_pct": ref["max_dd_pct"] if ref else None,
            "worst_dd_gap_pp": round(dd_gap_worst, 3), "verdict": verdict, "exp_active_sr": cfg["exp_sr"],
            "years_to_t2_at_exp_sr": round((2 / cfg["exp_sr"]) ** 2),
            "glide_episodes_completed": st.get("glide_episodes_completed", 0) if cfg.get("glide") else None}

def book_kill(tid, st, pr, px, d):
    cfg = BOOKS[tid]
    if st["kill"]["active"]: return
    if pr["worst_dd_gap_pp"] < -cfg["dd_kill_pp"]:
        st["kill"] = {"active": True, "reason": "shadow DD %.1fpp deeper than the R2 reference" % -pr["worst_dd_gap_pp"]}
    if tid == "ft2_glide" and pr["months"] >= 12 and pr["t"] is not None and pr["t"] <= -1.5:
        st["kill"] = {"active": True, "reason": "forward active t %.2f <= -1.5 after 12 months" % pr["t"]}
    if tid == "ft4_sig24" and px is not None:
        adv, _ = liq(px, "BTAL", d)
        st["btal_adv_usd"] = round(adv, 0)
        if 0 < adv < 3e6: st["kill"] = {"active": True, "reason": "BTAL 60d $ADV %.1fm < $3m" % (adv / 1e6)}

# ------------------------------------------------------------------------------------------------ run
def run(now=None, prices_file=None, fetch=fetch_prices, signal_fn=None):
    import fcntl
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, ".deep18_fwd_shadow.lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        up_us, up_asx = final_session("US", now), final_session("ASX", now)
        px = load_snapshot(prices_file) if prices_file else fetch()
        if not prices_file: save_snapshot(px, os.path.join(STATE_DIR, "prices_snapshot_shadow.csv"))
        last_bar = {"US": max(px["adj"].get("SPY", {}) or [None], default=None), "ASX": max(px["adj"].get("IOZ.AX", {}) or [None], default=None)}
        if last_bar["US"] is None: raise RuntimeError("no SPY bars: price fetch failed")
        up_us = min(up_us, last_bar["US"]); up_asx = min(up_asx, last_bar["ASX"] or up_asx)
        s_us, s_asx = max(up_us, START_NOT_BEFORE), max(up_asx, START_NOT_BEFORE)
        live_idle, t18_nav, t18_upd = trend18_idle_usd()
        done = {}
        # 1) R2 reference + whole-book shadows
        for tid, cfg in [("r2ref", REF)] + [(k, BOOKS[k]) for k in ("ft2_glide", "ft4_sig24")]:
            st = load(tid); rows = []
            try:
                if st is None: st, rows = new_book(tid, cfg, s_us, px, signal_fn)
                st, more = advance_book(tid, cfg, st, px, up_us, signal_fn); rows += more
            except Exception as e:
                log("%s stopped: %s" % (tid, str(e)[:200])); print("%s stopped: %s" % (tid, e), file=sys.stderr)
                if st is None: continue
            st["updated_utc"] = dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            save(tid, st, rows); done[tid] = [r["date"] for r in rows]
        ref = load("r2ref")
        for tid in ("ft2_glide", "ft4_sig24"):
            st = load(tid)
            if not st: continue
            pr = book_progress(tid, st, ref); book_kill(tid, st, pr, px, dt.date.fromisoformat(st["last_date"])); save(tid, st)
        # 2) overlays on trend18 idle cash
        for tid, cfg in OVERLAYS.items():
            st = load(tid); rows = []
            if st is None: st = new_overlay(tid, cfg, s_us if cfg["venue"] == "US" else s_asx)
            if st["kill"]["active"]:
                done[tid] = []; continue
            try:
                if cfg["venue"] == "US": st, rows = advance_overlay_us(tid, cfg, st, px, up_us, live_idle)
                else: st, rows = advance_overlay_asx(tid, cfg, st, px, up_us, up_asx, live_idle)
            except Exception as e:
                log("%s stopped: %s" % (tid, str(e)[:200])); print("%s stopped: %s" % (tid, e), file=sys.stderr)
            st["trend18_link"] = {"mf_basket_usd": live_idle, "nav_per_unit": t18_nav, "ledger_updated": t18_upd, "mode": "read-only"}
            st["updated_utc"] = dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            save(tid, st, rows)
            pr = overlay_progress(tid, st); overlay_kill(tid, st, pr); save(tid, st)
            done[tid] = [r["date"] + ("/" + r["leg"] if r.get("leg") else "") for r in rows]
        log("run ok: final US %s ASX %s; processed %s" % (up_us, up_asx, json.dumps(done)))
        return done

# ------------------------------------------------------------------------------------------------ dashboard (read-only)
def _curve(H, cols, n):
    return [{c: (r.get(c) if c == "date" else (float(r[c]) if r.get(c) not in (None, "") else None)) for c in cols} for r in H[-n:]] if H else []

def summary(tid, curve_points=400):
    """Read-only payload for /api/deep18_fwd/<id>. Never fetches, never writes."""
    if tid not in OVERLAYS and tid not in BOOKS and tid != "r2ref":
        return {"status": "UNKNOWN_TEST", "id": tid, "known": ORDER + ["r2ref"]}
    st = load(tid)
    cfg = OVERLAYS.get(tid) or BOOKS.get(tid) or REF
    base = {"id": tid, "fid": cfg.get("fid"), "name": cfg["name"], "rank": cfg.get("rank"), "rule": cfg.get("rule"),
            "kill_rule": cfg.get("kill_rule"), "paper_shadow_only": True, "in_firm_equity": False, "orders": "NONE"}
    if tid in OVERLAYS:
        base["mode"] = "PAPER overlay on trend18 idle cash (read-only link; not firm equity)"
    elif tid in BOOKS:
        base["mode"] = "Whole-book SHADOW of R2 (comparison only; FT2 and FT4 are mutually exclusive: only one could ever be adopted)"
    else:
        base["mode"] = "R2 reference shadow (benchmark for FT2/FT4)"
    if not st:
        return dict(base, status="PENDING", note="Starts at the first run after a final close (launchd com.tfo.deep18-fwd).")
    H = history(tid)
    if tid in OVERLAYS:
        pr = overlay_progress(tid, st, H)
        return dict(base, status="OK", start_date=st["start_date"], last_us=st.get("last_us"), last_asx=st.get("last_asx"),
                    updated_utc=st.get("updated_utc"), held=st["held"], notional_usd=st["notional_usd"], idle_usd=st.get("idle_usd"),
                    idle_basis=st.get("idle_basis"), pnl_net_costs_usd=round(st["cum_pnl_usd"], 2),
                    pnl_net_costs_and_fee_usd=round(st["cum_pnl_after_fee_usd"], 2), costs_usd=round(st["costs_usd"], 2),
                    gross_usd=round(st.get("gross_usd", 0.0), 2), fx_pnl_usd=round(st.get("fx_pnl_usd", 0.0), 2) if tid == "ft5_asx" else None,
                    event_log=st.get("event_log", [])[-6:], progress=pr, kill=st["kill"], trend18_link=st.get("trend18_link"),
                    equity_curve=_curve(H, ["date", "cum_pnl_usd", "cum_pnl_after_fee_usd"], curve_points),
                    last_events=[r["date"] + ": " + r["events"] for r in H if r.get("events")][-5:])
    ref = load("r2ref") if tid != "r2ref" else None
    v = book_value(st)
    out = dict(base, status="OK", start_date=st["start_date"], last_date=st["last_date"], updated_utc=st.get("updated_utc"),
               notional_usd=st["notional_usd"], sigma=st["sigma"], shadow_value_usd=round(v, 2), hypothetical_notional_usd=round(v, 2),
               after_fee_value_usd=round(st["after_fee_usd"], 2), pnl_net_costs_usd=round(v - st["notional_usd"], 2),
               pnl_net_costs_and_fee_usd=round(st["after_fee_usd"] - st["notional_usd"], 2), costs_usd=round(st["costs_usd"], 2),
               max_dd_pct=st["max_dd_pct"], dd_pct=round(100 * (v / st["peak_usd"] - 1), 3),
               weights_pct={k: round(100 * x / v, 2) for k, x in sorted(st["pos_usd"].items())} if v else {},
               bills_pct=round(100 * st["bills_usd"] / v, 2) if v else None, signal_asof=st.get("signal_asof"),
               pending={"signal_asof": st.get("pending_signal_asof"), "trade_date": st.get("pending_trade_date"),
                        "trend_w": st.get("pending_w")} if st.get("pending_trade_date") else None,
               r2_kill=st["r2_kill"], kill=st["kill"], gate_open=st.get("gate_open"))
    if tid in BOOKS:
        pr = book_progress(tid, st, ref, H)
        out["progress"] = pr
        HR = {r["date"]: r for r in history("r2ref")}
        cur = []
        for r in H[-curve_points:]:
            rr = HR.get(r["date"])
            cur.append({"date": r["date"], "value_usd": float(r["value_usd"]), "after_fee_usd": float(r["after_fee_usd"]),
                        "ref_after_fee_usd": float(rr["after_fee_usd"]) if rr else None,
                        "cum_pnl_after_fee_usd": float(r["after_fee_usd"]) - st["notional_usd"]})
        out["equity_curve"] = cur
        if BOOKS[tid].get("glide"):
            out.update(glide_m=st.get("glide_m", 0.0), af_hwm_usd=round(st["af_hwm_usd"], 2),
                       glide_trigger_usd=round(0.80 * st["af_hwm_usd"], 2), glide_months_on=st.get("glide_months_on", 0))
        if BOOKS[tid].get("btal"):
            out.update(btal_pct=round(100 * st["pos_usd"].get("BTAL", 0.0) / v, 2) if v else 0.0, btal_adv_usd=st.get("btal_adv_usd"))
        _, t18_nav, _ = trend18_idle_usd(); out["trend18_nav_per_unit"] = t18_nav
    else:
        out["equity_curve"] = _curve(H, ["date", "value_usd", "after_fee_usd"], curve_points)
    out["last_events"] = [r["date"] + ": " + r["events"] for r in H if r.get("events")][-5:]
    return out

def summary_all(curve_points=120):
    tests = []
    for t in ORDER:
        try: tests.append(summary(t, curve_points))
        except Exception as e: tests.append({"id": t, "status": "ERROR", "error": str(e)[:200]})
    try: ref = summary("r2ref", curve_points)
    except Exception as e: ref = {"status": "ERROR", "error": str(e)[:200]}
    return {"status": "OK", "label": "DEEP18 Round-3 forward tests (PAPER SHADOW: no orders, not firm equity)",
            "fee_daily_pct": 100 * FEE_DAY, "fee_history": [{"from": e.isoformat(), "rate_pct_day": round(100 * r, 6)} for e, r in FEE_SCHEDULE], "exclusive_note": "FT2 and FT4 are both leverage changes to R2: tracked side by side as comparisons, never stacked; only one could ever be adopted.",
            "overlay_note": "FT1/FT3/FT5 are differential P&L vs the MF basket on trend18's idle cash (read-only); R2 model idle is used while trend18's MF basket is unfunded.",
            "schedule": "launchd com.tfo.deep18-fwd: 07:40/08:40/10:40/12:40 Sydney after the US close and 16:45/17:45 after the ASX close (local time, AEDT-aware), idempotent",
            "tests": tests, "r2ref": ref, "generated_utc": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}

def main():
    ap = argparse.ArgumentParser(description="DEEP18 round-3 PAPER forward tests (shadow ledgers; no orders)")
    sp = ap.add_subparsers(dest="c")
    r = sp.add_parser("run"); r.add_argument("--prices-file")
    sp.add_parser("status")
    a = ap.parse_args()
    if a.c == "status":
        print(json.dumps(summary_all(5), indent=1, default=str)); return
    if a.c == "run":
        done = run(prices_file=a.prices_file)
        print(json.dumps({"processed": done}, indent=1, default=str)); return
    ap.print_help()

if __name__ == "__main__":
    main()
