#!/usr/bin/env python3
"""DEEP19 near-miss PAPER SHADOW forward tests (NS7, NS1) + TFO Equity S01 spin-off UNPROVEN shadow. SHADOW LEDGERS ONLY. $0 desk capital.

Head Trader decision 2026-10-03 21:32 AEST (round-4 near-misses): wire C-factors-7 and C-factors-1/Q01 as paper shadows.
BOTH FAILED DEEP19 ROUND 4 (deflated-Sharpe hurdle, ruin-level drawdowns). They are tracked to learn, not to trade.
  * NOT firm capital. No orders are placed, previewed or queued. Not a book, not in firm equity, firm_risk, desk caps or
    funder_fee.py. trend18 / deep18_fwd state is READ-ONLY here (deep18's r2ref history is read as the R2 benchmark).
  * Only files written: state/deep19_fwd/*_shadow.json, *_shadow_history.csv, prices_snapshot_shadow.csv, run_log_shadow.txt.
Frozen rules: research/deep19/fwd/DEEP19_FWD_RULES_20261003.md (SHA-256 in research/deep19/fwd/RULES_SHA256.txt).
  NS7 ns_cf7  C-factors-7: R2 (trend18 engine, sigma 20%) with the UPRO slot moved into the 3x ETF of the top-1 of
              {SPY + 12 sector underlyings} by 12-2 momentum (audit V3, 3 Oct 21:51: all sectors eligible, no ADV filter).
              Same capital weight as UPRO x (3 / ETF leverage). Monthly signal at the last US close, traded next close.
  NS1 ns_cf1  DROPPED 3 Oct 2026 21:51 AEST (Head Trader; audit: real-ETF CAGR 8.7%, MaxDD -87%, ~$0.25m capacity). Code kept, not run.
  S01 ns_s01  TFO Equity spin-off rule (UNPROVEN SHADOW: failed in-sample). Addendum research/deep19/fwd/DEEP19_FWD_S01_ADDENDUM_20261003.md.
Costs: 12.5bp/side + sqrt impact (sigma_d x sqrt(trade/$ADV)) per ETF, BTC 25bp/side. Fee: deep18_fwd.FEE_SCHEDULE (dated).
Commands: deep19_fwd.py run [--prices-file F] | status      (DEEP19_FWD_STATE_DIR overrides the state dir)
"""
import argparse, csv, datetime as dt, json, math, os, sys
# TFO DoH hook 2026-10-28 (desk_doh: process-local DNS-over-HTTPS for venue/data hosts; kill switch TFO_DESK_DOH=0)
try:
    import sys as _s; _s.path.insert(0, "/Users/test/Applications/MultiVenueDesk") if "/Users/test/Applications/MultiVenueDesk" not in _s.path else None
    import desk_doh as _doh; _doh.install()
except Exception as _e:
    print("desk_doh install skipped:", _e, file=__import__("sys").stderr)

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import deep18_fwd as D18          # read-only reuse: calendars, dated fee schedule, price helpers, R2 signal wrapper
STATE_DIR = os.environ.get("DEEP19_FWD_STATE_DIR") or os.path.join(BASE, "state", "deep19_fwd")
UTC = dt.timezone.utc
STATUS = "NEAR_MISS_SHADOW_DID_NOT_PASS_ZERO_DESK_CAPITAL"
LABEL = "NEAR-MISS SHADOW: did NOT pass, $0 desk capital"
START_NOT_BEFORE = dt.date(2026, 10, 2)
NOTIONAL = 1_787_506.89                       # same paper notional as deep18 shadows (comparison only; $0 desk capital)
RULES_FILE = os.path.join(BASE, "research", "deep19", "fwd", "DEEP19_FWD_RULES_20261003.md")
COST_ETF, COST_BTC = 0.00125, 0.0025
ADV_MIN = 5e6
RES_FRAC = 0.028
MIN_TRADE = 250.0
KILL_DD, KILL_SIGMA, KILL_HIGH_N = -0.30, 0.10, 126
MF = ["DBMF", "KMLM", "CTA"]
SECTORS = [  # (key, 1x underlying for the signal, tradable leveraged ETF, leverage)
    ("tech", "XLK", "TECL", 3), ("semis", "SOXX", "SOXL", 3), ("fin", "XLF", "FAS", 3), ("health", "XLV", "CURE", 3),
    ("biotech", "XBI", "LABU", 3), ("energy", "XLE", "ERX", 2), ("reit", "IYR", "DRN", 3), ("util", "XLU", "UTSL", 3),
    ("indus", "XLI", "DUSL", 3), ("discr", "XLY", "WANT", 3), ("homeb", "ITB", "NAIL", 3), ("retail", "XRT", "RETL", 3)]
SPX = ("spx", "SPY", "UPRO", 3)
R2I = ["UPRO", "TQQQ", "TMF", "UGL", "BTC-USD"]
SYMS = sorted(set([s[1] for s in SECTORS] + [s[2] for s in SECTORS] + MF + R2I + ["SPY", "^IRX"]))
LEDGER_KEYS = D18.LEDGER_KEYS
TESTS = {
 "ns_cf7": dict(fid="NS7", name="NS7 C-factors-7: R2 with UPRO slot -> 3x sector momentum (audit V3 real-ETF version)", kind="r2swap", exp_sr=0.15,
   label="UNPROVEN: holdout +5.3pp vs R2, but only +0.4pp over 1999–2026 and worse DD (−47% vs −34%)",
   interim_months=12, review_months=24, dd_kill_pp=10.0,
   rule="R2 (trend18 engine, sigma 20%). The UPRO capital weight moves to the 3x ETF of the top-1 of {SPY + 12 sectors} by "
        "12-2 momentum of the 1x sector ETFs (TFO Equity near-miss audit V3 PRIMARY: all 12 sectors eligible, no ADV filter, held in the "
        "real 3x ETF; ERX is ~2x since 2020 so it is treated as 2x and sized x1.5, capped by uninvested room; SPY -> stays UPRO). Signal at "
        "the last US close of the month, traded at the next close; R2 mid-month refresh as trend18. Costs 12.5bp/side + sqrt impact.",
   pass_rule="at 24 months: after-fee CAGR >= R2 reference + 1pp AND max DD no more than 2pp deeper than the R2 reference",
   kill_rule="shadow DD more than 10pp deeper than the R2 reference at any point (tracking stops; reported)",
   gap={"audit_v3_tradable_1999_2026": {"cagr_pct": 21.4, "worst_day_pct": -14.1, "worst_month_pct": -24.3, "max_dd_pct": -46.9, "r2_max_dd_pct": -34.4},
        "audit_v3_holdout_2021_2026": {"cagr_pct": 25.7, "worst_day_pct": -8.1, "worst_month_pct": -15.0, "max_dd_pct": -31.8, "r2_max_dd_pct": -28.2},
        "r4_ff_backtest_1986_2020": {"worst_day_pct": -26.1, "worst_month_pct": -43.5, "max_dd_pct": -54.2, "r2_max_dd_pct": -48.5},
        "vs_r2": "+0.4pp/yr 1999-2026, -0.9pp 1999-2020, +5.3pp holdout (contaminated); beats R2 in 2 of 4 subperiods; at a $2m book -1.1 to -2.1pp over 1999-2026",
        "capacity": "5% of ADV20 hit at about a $5.5m R2 book (WANT binds at $0.27m, RETL $2.1m, NAIL $3.7m)",
        "scenario": "sector -20% overnight gap -> the 3x leg loses 60% -> book loses 0.6 x the UPRO-slot weight (live figure below)"}),
 "ns_cf1": dict(fid="NS1", name="NS1 C-factors-1/Q01: 3x sector momentum standalone", kind="sector", exp_sr=0.35,
   interim_months=12, review_months=24, dd_kill_pct=-60.0,
   rule="Top-3 of 12 sectors by 12-2 momentum among 3x ETFs with 60d $ADV >= $5m; each 1/3 held in the 3x ETF (ERX x1.5) "
        "only while the underlying is above its 10-month SMA, else that third sits in DBMF/KMLM/CTA. Monthly: signal at "
        "the last US close of the month, traded at the next close.",
   pass_rule="at 24 months: pre-fee CAGR >= 19% AND max DD better than -50% AND after-fee P&L > 0",
   kill_rule="shadow drawdown worse than -60% from peak (tracking stops; reported)",
   gap={"backtest_ff49_1927_2020": {"worst_day_pct": -59.1, "worst_month_pct": -73.9, "max_dd_pct": -99.2},
        "holdout_2021_2026": {"worst_day_pct": -12.7, "max_dd_pct": -54.0},
        "tradable_etf_check_2011_2026": {"cagr_pct": 20.0, "worst_day_pct": -32.8, "worst_day": "2020-03-12",
                                          "worst_month_pct": -60.6, "max_dd_pct": -82.6, "vol_pct": 56.4, "upro_cagr_pct": 29.1},
        "scenario": "sector -20% overnight gap with all three legs on -> book loses ~60%"}),
}
S01_LABEL = "UNPROVEN SHADOW: failed in-sample, $0 desk capital"
S01_SPEC_SHA = "f9f8d6b8bcb08941bafef436d1511ee52f791ff9f458b29e55e9022e57695fe5"   # TFO Equity research/deep19/equity_events/S01_spinoff spec (hash-locked)
SEC_UA = {"User-Agent": __import__("sec_contact").user_agent()}
S01_SEED = os.path.join(BASE, "research", "deep19", "fwd", "s01_seed_spincos.csv")
S01 = dict(k=20, H=120, wmax=0.10, min_px=2.0, min_adv=2e6, delist_ret=-0.10)
S01_PANEL_END = dt.date(2026, 10, 1)   # last date of TFO Equity's Alpaca panel; seed first bars up to here are Equity's own
TESTS["ns_s01"] = dict(fid="S01", name="S01 spin-offs: buy each US spinco at its day-20 close, hold 120 sessions (TFO Equity rule)", kind="events",
   label=S01_LABEL, exp_sr=0.3, interim_months=12, review_months=24,
   rule="TFO Equity S01 exactly as hash-locked (spec sha256 f9f8d6b8...): spinco = registrant of an initial Form 10-12B on EDGAR; day 1 = its first "
        "regular trading bar after the filing (no bars before the filing, within 730 days); buy at the close of day 20, hold 120 sessions; "
        "weight min(1/open positions, 10%) per name, the rest in SPY; skip if price < $2 or 60d $ADV < $2m at entry; per-side cost 10/20/40bp "
        "by $ADV >= 50m / 10-50m / 2-10m; a series that stops -> exit at its last close -10%.",
   impl_note="Forward data mapping only (no retuning): prices Alpaca SIP -> Yahoo split-adjusted close (price-only, as Equity); first bar = Equity's "
             "own Alpaca first bar for names already in its list, else first Yahoo bar (Yahoo can include up to ~3 thin when-issued sessions that "
             "Alpaca lacks, so day 20 can come early); Form-4 ticker map -> SEC company_tickers.json; 10-12B discovery from EDGAR daily indices. "
             "Entries only after the 2 Oct 2026 start (never backdated).",
   pass_rule="at 24 months (Head Trader decides): forward active return vs SPY > 0 with t >= 1.3 on >= 20 completed trades AND the top-5 names "
             "under 50% of summed trade returns",
   kill_rule="none (monitor); concentration and drawdown reported",
   concentration="62% of the holdout's summed trade returns came from 5 names (Q, SOLS, RHLD, GEV, CEG); ex-top-5 mean trade +5.8% per 120 days; "
                 "50 holdout events; about 60% of the book sits in SPY on average; IS (2016-20) FAILED.",
   gap={"is_2016_2020": {"worst_day_pct": -11.67, "worst_month_pct": -16.55, "note": "worst day 2020-03-16, worst month 2020-03"},
        "holdout_2021_2026": {"cagr_pct": 22.9, "max_dd_pct": -18.0, "worst_day_pct": -7.19, "worst_month_pct": -10.67},
        "as_r2_idle_sleeve_holdout": {"max_dd_pct": -37.1, "r2_max_dd_pct": -24.1},
        "scenario": "a single spinco -50% overnight at the 10% cap costs 5% of the book"})
DROPPED = [{"id": "ns_cf1", "fid": "NS1", "name": "NS1 C-factors-1/Q01: 3x sector momentum standalone", "dropped": "2026-10-03 21:51 AEST",
             "by": "Head Trader", "reason": "TFO Equity near-miss audit: on real ETFs 1999-2026 CAGR 8.7%, MaxDD -87%, capacity ~$0.25m. "
             "Removed before its first trade (no sessions processed); state archived under state/deep19_fwd/archive_dropped/."}]
ORDER = ["ns_cf7", "ns_s01"]

# ------------------------------------------------------------------ prices (own symbol list, deep18 snapshot format)
def fetch_prices(period="2y"):
    import yfinance as yf
    df = yf.download(SYMS, period=period, interval="1d", auto_adjust=False, actions=False, progress=False, group_by="column", threads=True)
    out = {"adj": {}, "close": {}, "vol": {}}
    if df is None or df.empty: return out
    for s in SYMS:
        for key, col in (("adj", "Adj Close"), ("close", "Close"), ("vol", "Volume")):
            try: ser = df[(col, s)]
            except KeyError: continue
            rows = {ts.date(): float(v) for ts, v in ser.items() if v == v and v is not None and (key == "vol" or v > 0)}
            if rows: out[key][s] = rows
    return out

def month_end_closes(px, sym, d):
    """Adjusted closes at each US month-end session (last NYSE session of its month) <= d."""
    p = px["adj"].get(sym, {})
    return [(k, p[k]) for k in sorted(p) if k <= d and k == D18.month_sessions(k, "US")[-1]]

def sector_scores(px, d):
    """12-2 momentum and 10m-SMA test per candidate, as of signal date d (uses month-end closes <= d)."""
    res = {}
    for key, und, etf, lev in SECTORS + [SPX]:
        me = month_end_closes(px, und, d)
        if len(me) < 13: continue
        mom = me[-2][1] / me[-13][1] - 1
        sma = sum(v for _, v in me[-10:]) / 10.0; last = D18._last_le(px["adj"].get(und, {}), d)
        adv, sd = D18.liq(px, etf, d)
        res[key] = {"und": und, "etf": etf, "lev": lev, "mom": round(mom, 5), "above_sma": bool(last and last > sma),
                    "adv_usd": round(adv, 0), "eligible": key == "spx" or adv >= ADV_MIN}
    return res

def sel_cf7(px, d):
    sc = sector_scores(px, d); el = dict(sc)            # audit V3: all 12 sectors + SPY eligible (no ADV filter)
    if not el: return "spx", sc
    return max(el, key=lambda k: el[k]["mom"]), sc

def sel_cf1(px, d):
    sc = sector_scores(px, d)
    el = sorted([k for k, v in sc.items() if k != "spx" and v["eligible"]], key=lambda k: -sc[k]["mom"])[:3]
    w = {}
    for k in el:
        if sc[k]["above_sma"]: w[sc[k]["etf"]] = w.get(sc[k]["etf"], 0.0) + (1 / 3) * (3.0 / sc[k]["lev"])
    return w, el, sc

# ------------------------------------------------------------------ state io
def _paths(tid): return os.path.join(STATE_DIR, "%s_shadow.json" % tid), os.path.join(STATE_DIR, "%s_shadow_history.csv" % tid)
def load(tid):
    try:
        with open(_paths(tid)[0]) as f: return json.load(f)
    except Exception: return None
def save(tid, st, rows=()):
    bad = LEDGER_KEYS & set(st)
    if bad: raise ValueError("refusing ledger-shaped keys %s" % sorted(bad))
    os.makedirs(STATE_DIR, exist_ok=True); p, h = _paths(tid); tmp = p + ".tmp"
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
    h = _paths(tid)[1]
    if not os.path.exists(h): return []
    with open(h) as f: return list(csv.DictReader(f))
def log(line):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, "run_log_shadow.txt"), "a") as f:
        f.write("%s %s\n" % (dt.datetime.now(D18.SYD).strftime("%Y-%m-%d %H:%M %Z"), line))

# ------------------------------------------------------------------ targets
def targets(tid, st, px, d):
    """Target weights (fraction of value) per instrument, plus bills fraction; from the staged signal in st."""
    sig = st.get("sig") or {}
    if tid == "ns_cf1":
        w = dict(sig.get("w") or {}); tot = sum(w.values())
        if tot > 1.0: w = {k: x / tot for k, x in w.items()}          # ERX x1.5 never borrows: scale legs to <= 100%
        idle = max(0.0, 1.0 - sum(w.values()))
        for m in MF: w[m] = w.get(m, 0.0) + idle / 3
        return w, 0.0
    w = dict(sig.get("r2_w") or {})
    if st["r2_kill"]["active"]: w = {k: x * KILL_SIGMA / 0.20 for k, x in w.items()}
    up = w.pop("UPRO", 0.0); etf, lev = sig.get("etf", "UPRO"), sig.get("lev", 3)
    if up > 0:
        room = max(0.0, 1.0 - RES_FRAC - sum(w.values()))
        w[etf] = w.get(etf, 0.0) + min(up * 3.0 / lev, room)             # ERX x1.5 only within the uninvested room (no borrowing)
    idle = max(0.0, 1.0 - sum(w.values()) - RES_FRAC)
    for m in MF: w[m] = w.get(m, 0.0) + idle / 3
    return w, RES_FRAC

def value(st): return st["bills_usd"] + sum(st["pos_usd"].values())

def rebalance(tid, st, px, d, events, why):
    v = value(st); tgt, _ = targets(tid, st, px, d); cost = traded = 0.0
    for k in set(tgt) | set(st["pos_usd"]):
        cur = st["pos_usd"].get(k, 0.0); t = tgt.get(k, 0.0) * v; dl = t - cur
        if abs(dl) < MIN_TRADE and t > 0: continue
        if k == "BTC-USD": c = abs(dl) * COST_BTC
        else:
            adv, sd = D18.liq(px, k, d); c = abs(dl) * (COST_ETF + sd * math.sqrt(abs(dl) / max(adv, 1.0)))
        st["pos_usd"][k] = t; st["bills_usd"] -= dl + c; cost += c; traded += abs(dl)
    st["pos_usd"] = {k: x for k, x in st["pos_usd"].items() if abs(x) > 0.005}
    events.append("%s rebalance: traded %.0f, cost %.0f" % (why, traded, cost))
    return cost

def stage_signal(tid, st, px, d, events):
    if tid == "ns_cf1":
        w, top, sc = sel_cf1(px, d)
        st["pending"] = {"asof": d.isoformat(), "w": w, "top3": top, "scores": sc, "trade": D18.next_session(d, "US").isoformat()}
        events.append("signal %s: top3 %s -> %s" % (d, ",".join(top), ", ".join("%s %.0f%%" % (k, 100 * x) for k, x in w.items()) or "all MF"))
    else:
        s = D18.r2_signals(d, 0.20)
        k, sc = sel_cf7(px, d); etf, lev = (SPX[2], 3) if k == "spx" else (sc[k]["etf"], sc[k]["lev"])
        st["pending"] = {"asof": d.isoformat(), "r2_w": s["w"], "gate_open": s["gate_open"], "pick": k, "etf": etf, "lev": lev,
                         "scores": sc, "trade": D18.next_session(d, "US").isoformat()}
        events.append("signal %s: UPRO slot -> %s (%s, 12-2 %+.1f%%); R2 UPRO w %.1f%%" % (d, etf, k, 100 * sc.get(k, {}).get("mom", 0), 100 * s["w"].get("UPRO", 0)))

def new_state(tid, start, px):
    st = {"id": tid, "status": STATUS, "label": LABEL, "name": TESTS[tid]["name"], "paper_shadow_only": True, "in_firm_equity": False,
          "desk_capital_usd": 0, "orders": "NONE", "start_date": start.isoformat(), "started_utc": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
          "notional_usd": NOTIONAL, "bills_usd": NOTIONAL, "pos_usd": {}, "sig": None, "pending": None,
          "after_fee_usd": NOTIONAL, "peak_usd": NOTIONAL, "max_dd_pct": 0.0, "worst_day_pct": 0.0,
          "r2_kill": {"active": False, "ref_usd": NOTIONAL, "since": None}, "recent_values": [NOTIONAL],
          "costs_usd": 0.0, "last_date": start.isoformat(), "sessions": 1, "kill": {"active": False, "reason": ""}}
    ev = []; stage_signal(tid, st, px, start, ev)
    return st, [row(tid, st, start, 0.0, ["START %.0f paper notional in bills at the %s close ($0 desk capital)" % (NOTIONAL, start)] + ev)]

def row(tid, st, d, cost, events):
    v = value(st)
    return {"date": d.isoformat(), "value_usd": round(v, 2), "after_fee_usd": round(st["after_fee_usd"], 2),
            "ret_since_start_pct": round(100 * (v / st["notional_usd"] - 1), 4), "dd_pct": round(100 * (v / st["peak_usd"] - 1), 4),
            "lev_etf_usd": round(sum(x for k, x in st["pos_usd"].items() if k not in MF and k not in ("TQQQ", "TMF", "UGL", "BTC-USD")), 2),
            "mf_usd": round(sum(st["pos_usd"].get(m, 0) for m in MF), 2), "cost_usd": round(cost, 2), "events": "; ".join(events)}

def advance(tid, st, px, upto):
    rows = []; adj = px["adj"]
    for d in D18.sessions_between(dt.date.fromisoformat(st["last_date"]) + dt.timedelta(days=1), upto, "US"):
        if d not in adj.get("SPY", {}):
            if any(k > d for k in adj.get("SPY", {})): continue
            break
        prev = dt.date.fromisoformat(st["last_date"]); events = []; cost = 0.0; v0 = value(st)
        st["bills_usd"] *= (1 + D18.rf_annual(px, prev) / 365.0) ** (d - prev).days
        for k in list(st["pos_usd"]):
            r = D18._ret(px, k, prev, d)
            if r is None:
                p = adj.get(k, {}); a = D18._last_le(p, prev); b = D18._last_le(p, d); r = (b / a - 1) if a and b else 0.0
                events.append("%s mark carried from last bar" % k)
            st["pos_usd"][k] *= 1 + r
        v = value(st); dr = v / v0 - 1 if v0 > 0 else 0.0
        st["worst_day_pct"] = min(st.get("worst_day_pct", 0.0), round(100 * dr, 4))
        st["after_fee_usd"] *= (1 + dr) * D18.fee_factor(prev, d)
        st["peak_usd"] = max(st["peak_usd"], v); st["max_dd_pct"] = min(st["max_dd_pct"], round(100 * (v / st["peak_usd"] - 1), 4))
        st["recent_values"] = (st.get("recent_values") or [])[-(KILL_HIGH_N - 1):] + [v]
        need = None
        if tid == "ns_cf7":
            k = st["r2_kill"]
            if not k["active"]: k["ref_usd"] = max(k["ref_usd"], v)
            if not k["active"] and v / k["ref_usd"] - 1 <= KILL_DD:
                k.update(active=True, since=d.isoformat()); need = "R2 KILL (10% vol)"; events.append("R2 kill: -30% from reference")
            elif k["active"] and v >= max(st["recent_values"][:-1]):
                k.update(active=False, since=None, ref_usd=v); need = "R2 kill cleared"; events.append("R2 kill cleared")
        pend = st.get("pending")
        if pend and pend.get("trade") == d.isoformat():
            st["sig"] = pend; st["pending"] = None; need = "monthly (signal %s)" % pend["asof"]
        ms = D18.month_sessions(d, "US")
        if tid == "ns_cf7" and st.get("sig") and d == ms[len(ms) // 2] and not need:
            s = D18.r2_signals(d, 0.20); st["sig"]["r2_w"] = s["w"]; st["sig"]["gate_open"] = s["gate_open"]; need = "R2 mid-month refresh"
        if need and st.get("sig"): cost += rebalance(tid, st, px, d, events, need)
        if d == ms[-1]: stage_signal(tid, st, px, d, events)
        if cost: st["after_fee_usd"] *= value(st) / v
        st["costs_usd"] += cost; st["last_date"] = d.isoformat(); st["sessions"] += 1
        rows.append(row(tid, st, d, cost, events))
    return st, rows

# ------------------------------------------------------------------ S01 spin-off event engine (TFO Equity rule, forward mapping)
def _http(url, timeout=30):
    import urllib.request
    req = urllib.request.Request(url, headers=SEC_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r: return r.read()

RUN_ERRORS = []
def _us_holiday(d):
    try:
        from pandas.tseries.holiday import USFederalHolidayCalendar as _C
        return d in {x.date() for x in _C().holidays(start=str(d.year) + "-01-01", end=str(d.year) + "-12-31")}
    except Exception: return False
def s01_discover(st, today):
    """Registrants of initial Form 10-12B: Equity's seed list + EDGAR daily form indices since the last scan (max 40 days per run)."""
    regs = st.setdefault("regs", {})
    if not st.get("seeded"):
        try:
            for r in csv.DictReader(open(S01_SEED)):
                regs.setdefault(str(int(r["cik"])), {"name": r["name"], "fdate": r["fdate"][:10], "sym": r["sym"] or None, "src": "equity_seed",
                                                    "fb_eq": (r.get("first_bar") or "")[:10] or None})
            st["seeded"] = True
        except Exception as e: log("s01 seed missing: %s" % e)
    d = dt.date.fromisoformat(st.get("scan_next", "2026-09-25")); n = 0
    while d <= today and n < 40:
        if d.weekday() < 5:
            q = (d.month - 1) // 3 + 1
            try:
                txt = _http("https://www.sec.gov/Archives/edgar/daily-index/%d/QTR%d/form.%s.idx" % (d.year, q, d.strftime("%Y%m%d"))).decode("latin-1")
                for line in txt.splitlines():
                    if line.startswith("10-12B ") and not line.startswith("10-12B/A"):
                        parts = line.split()
                        cik = str(int(parts[-3])); regs.setdefault(cik, {"name": " ".join(parts[1:-3]), "fdate": d.isoformat(), "sym": None, "src": "edgar_daily"})
            except Exception as e:
                if "403" in str(e) and _us_holiday(d): e = "404 (federal holiday %s: SEC serves 403 for the missing index)" % d   # 2026-10-29: 12 Oct Columbus Day blocked the scan for 17 days
                if "403" in str(e) and ((today - d).days <= 1 or (d.weekday() == 4 and (today - d).days <= 3)): break   # latest index not published yet: retry next run
                if "404" not in str(e) and d < today: log("s01 index %s: %s" % (d, str(e)[:80])); RUN_ERRORS.append("s01 index %s: %s" % (d, str(e)[:80]))
                if "404" not in str(e): break
            import time; time.sleep(0.15)
        d += dt.timedelta(days=1); n += 1
    st["scan_next"] = d.isoformat()
    if any(not v.get("sym") for v in regs.values()):
        try:
            mp = {str(v["cik_str"]): v["ticker"] for v in json.loads(_http("https://www.sec.gov/files/company_tickers.json")).values()}
            for cik, v in regs.items():
                if not v.get("sym") and cik in mp: v["sym"] = mp[cik].replace(".", "-")
        except Exception as e: log("s01 ticker map: %s" % str(e)[:80])
    return regs

def s01_prices(syms):
    import yfinance as yf
    out = {}
    if not syms: return out
    df = yf.download(sorted(syms), period="2y", interval="1d", auto_adjust=False, actions=False, progress=False, group_by="column", threads=True)
    if df is None or df.empty: return out
    for s in syms:
        try: a, c, v = df[("Adj Close", s)], df[("Close", s)], df[("Volume", s)]
        except KeyError: continue
        rows = {ts.date(): (float(a[ts]), float(c[ts]), float(v[ts]) if v[ts] == v[ts] else 0.0) for ts in a.index if a[ts] == a[ts] and a[ts] > 0}
        if rows: out[s] = rows
    return out

def _s01_cost(adv): return 0.0010 if adv >= 50e6 else 0.0020 if adv >= 10e6 else 0.0040

def s01_new(start):
    return {"id": "ns_s01", "status": STATUS, "label": S01_LABEL, "name": TESTS["ns_s01"]["name"], "paper_shadow_only": True, "in_firm_equity": False,
            "desk_capital_usd": 0, "orders": "NONE", "start_date": start.isoformat(), "started_utc": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "notional_usd": NOTIONAL, "nav_usd": NOTIONAL, "after_fee_usd": NOTIONAL, "spy_nav_usd": NOTIONAL, "peak_usd": NOTIONAL,
            "max_dd_pct": 0.0, "worst_day_pct": 0.0, "open": [], "closed": [], "skipped": [], "regs": {}, "seeded": False,
            "scan_next": "2026-09-25", "last_date": start.isoformat(), "sessions": 1, "costs_pct_nav": 0.0, "kill": {"active": False, "reason": ""}}

def s01_advance(st, px, upto, now_date):
    regs = s01_discover(st, now_date)
    lo = dt.date.fromisoformat(st["start_date"]) - dt.timedelta(days=730)
    syms = sorted({v["sym"] for v in regs.values() if v.get("sym") and dt.date.fromisoformat(v["fdate"]) >= lo})
    sp = s01_prices(syms); st["tracked_syms"] = len(syms); rows = []
    # pending entries: day-20 close strictly after the start date
    traded = {p["sym"] for p in st["open"]} | {p["sym"] for p in st["closed"]} | {p["sym"] for p in st["skipped"]}
    cand = {}
    for cik, v in regs.items():
        s = v.get("sym")
        if not s or s in traded or s not in sp: continue
        fd = dt.date.fromisoformat(v["fdate"]); bars = sorted(sp[s])
        if v.get("fb_eq"):                                # Equity's own Alpaca first bar (hash-locked data) wins
            fb = dt.date.fromisoformat(v["fb_eq"]); bars = [b for b in bars if b >= fb]; src = "equity_alpaca"
            if not bars: continue
        else:
            if v["src"] == "equity_seed" and bars and bars[0] <= S01_PANEL_END: continue   # Equity's rule excluded it (pre-filing data / not listed)
            if any(b <= fd for b in bars): continue      # traded before the filing -> not a new spinco listing
            if not bars or (bars[0] - fd).days >= 730: continue
            if bars[0] <= now_date - dt.timedelta(days=700): continue   # older than the 2y data window: cannot see a true first bar
            src = "yahoo"
        if len(bars) >= S01["k"]:
            cand[s] = {"entry_date": bars[S01["k"] - 1], "first_bar": bars[0], "cik": cik, "name": v["name"], "bars_list": bars, "fb_src": src}
        else:
            cand[s] = {"entry_date": None, "first_bar": bars[0], "cik": cik, "name": v["name"], "bars": len(bars), "fb_src": src}
    st["pipeline"] = sorted([{"sym": s, "name": c["name"][:40], "first_bar": c["first_bar"].isoformat(),
                              "day20": c["entry_date"].isoformat() if c["entry_date"] else "in %d sessions" % (S01["k"] - c["bars"]), "first_bar_src": c["fb_src"]}
                             for s, c in cand.items() if (c["entry_date"] is None or c["entry_date"] > dt.date.fromisoformat(st["start_date"]))],
                            key=lambda x: x["first_bar"])[-12:]
    spy = px["close"].get("SPY", {})                    # Equity: SPY Close (price only); spincos: split-adjusted close, no dividends
    for d in D18.sessions_between(dt.date.fromisoformat(st["last_date"]) + dt.timedelta(days=1), upto, "US"):
        if d not in spy:
            if any(k > d for k in spy): continue
            break
        prev = dt.date.fromisoformat(st["last_date"]); events = []
        sr = spy[d] / spy[prev] - 1 if prev in spy else 0.0
        rs = []; still = []
        for p in st["open"]:
            bars = sp.get(p["sym"], {}); b0 = D18._last_le({k: x[1] for k, x in bars.items()}, prev) if bars else None
            if d in bars and b0:
                r = bars[d][1] / b0 - 1
            elif any(k > d for k in bars): r = 0.0
            elif bars and max(bars) < d - dt.timedelta(days=30):
                r = S01["delist_ret"]; p["held"] = S01["H"] - 1; events.append("%s series stopped: exit -10%%" % p["sym"])
            else: r = 0.0
            if p["held"] == 0: r -= p["cost_side"]
            p["held"] += 1
            if p["held"] >= S01["H"]: r -= p["cost_side"]
            p["cum"] = (1 + p["cum"]) * (1 + r) - 1; rs.append(r)
            if p["held"] >= S01["H"]:
                p["exit_date"] = d.isoformat(); st["closed"].append(p); events.append("EXIT %s after %d sessions: %+.1f%%" % (p["sym"], p["held"], 100 * p["cum"]))
            else: still.append(p)
        K = len(rs); w = min(1.0 / K, S01["wmax"]) if K else 0.0
        port = w * sum(rs) + (1 - w * K) * sr
        st["open"] = still
        # new entries at today's close (accrue from the next session)
        for s, c in cand.items():
            if c["entry_date"] == d and s not in {p["sym"] for p in st["open"] + st["closed"] + st["skipped"]}:
                bars = sp[s]; ks = [k for k in c["bars_list"] if k <= d][-60:]          # Equity: 60d median $volume (min 20 bars) from the first bar
                dv = sorted(bars[k][1] * bars[k][2] for k in ks); m = len(dv)
                adv = (dv[m // 2] if m % 2 else 0.5 * (dv[m // 2 - 1] + dv[m // 2])) if m >= 20 else 0.0; pxc = bars[d][1]
                if pxc >= S01["min_px"] and adv >= S01["min_adv"]:
                    st["open"].append({"sym": s, "name": c["name"][:40], "cik": c["cik"], "first_bar": c["first_bar"].isoformat(), "entry_date": d.isoformat(),
                                       "first_bar_src": c["fb_src"], "adv_usd": round(adv), "cost_side": _s01_cost(adv), "held": 0, "cum": 0.0})
                    events.append("ENTER %s (%s) at the day-20 close, $ADV %.1fm" % (s, c["name"][:30], adv / 1e6))
                else:
                    st["skipped"].append({"sym": s, "date": d.isoformat(), "px": pxc, "adv_usd": round(adv)}); events.append("SKIP %s (px %.2f, $ADV %.2fm)" % (s, pxc, adv / 1e6))
        v0 = st["nav_usd"]; st["nav_usd"] *= 1 + port; st["spy_nav_usd"] *= 1 + sr
        st["after_fee_usd"] *= (1 + port) * D18.fee_factor(prev, d)
        st["worst_day_pct"] = min(st["worst_day_pct"], round(100 * port, 4))
        st["peak_usd"] = max(st["peak_usd"], st["nav_usd"]); st["max_dd_pct"] = min(st["max_dd_pct"], round(100 * (st["nav_usd"] / st["peak_usd"] - 1), 4))
        st["last_date"] = d.isoformat(); st["sessions"] += 1
        rows.append({"date": d.isoformat(), "value_usd": round(st["nav_usd"], 2), "after_fee_usd": round(st["after_fee_usd"], 2),
                     "spy_value_usd": round(st["spy_nav_usd"], 2), "port_ret": round(port, 6), "spy_ret": round(sr, 6), "n_open": K,
                     "w_per_name": round(w, 4), "dd_pct": round(100 * (st["nav_usd"] / st["peak_usd"] - 1), 4), "events": "; ".join(events)})
    return st, rows

def s01_concentration(st):
    tr = [p["cum"] for p in st["closed"]] + [p["cum"] for p in st["open"]]
    names = [(p["sym"], p["cum"]) for p in st["closed"] + st["open"]]
    tot = sum(tr); top = sorted(names, key=lambda x: -x[1])[:5]
    share = (sum(x[1] for x in top) / tot) if tot > 0 else None
    return {"trades": len(tr), "closed": len(st["closed"]), "open": len(st["open"]), "sum_trade_ret_pct": round(100 * tot, 2),
            "top5": [{"sym": s, "ret_pct": round(100 * r, 2)} for s, r in top], "top5_share_of_sum": round(share, 3) if share is not None else None}

# ------------------------------------------------------------------ progress / kill (R2 benchmark = deep18 r2ref, read-only)
def _tstat(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) <= 20: return None
    m = sum(xs) / len(xs); sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
    return round(m / sd * math.sqrt(len(xs)), 3) if sd > 0 else None

def progress(tid, st, H=None):
    cfg = TESTS[tid]; H = history(tid) if H is None else H
    months = round((dt.date.fromisoformat(st["last_date"]) - dt.date.fromisoformat(st["start_date"])).days / 30.44, 2)
    days = (dt.date.fromisoformat(st["last_date"]) - dt.date.fromisoformat(st["start_date"])).days
    v = value(st); ret = v / st["notional_usd"] - 1; af = st["after_fee_usd"] / st["notional_usd"] - 1
    cagr = (1 + ret) ** (365.25 / days) - 1 if days >= 365 else None
    out = {"months": months, "interim_months": cfg["interim_months"], "review_months": cfg["review_months"],
           "pct_to_review": round(min(1.0, months / cfg["review_months"]), 3), "ret_pct": round(100 * ret, 3),
           "after_fee_ret_pct": round(100 * af, 3), "cagr_pct": round(100 * cagr, 2) if cagr is not None else None,
           "max_dd_pct": st["max_dd_pct"], "worst_day_pct": st.get("worst_day_pct"), "exp_sr": cfg["exp_sr"]}
    if tid == "ns_cf7":
        HR = D18.history("r2ref"); rv = {r["date"]: r for r in HR}; act = []; prev = None; gap = 0.0
        for r in H:
            if prev and r["date"] in rv and prev["date"] in rv:
                act.append(float(r["value_usd"]) / float(prev["value_usd"]) - float(rv[r["date"]]["value_usd"]) / float(rv[prev["date"]]["value_usd"]))
            if r["date"] in rv: gap = min(gap, float(r["dd_pct"]) - float(rv[r["date"]]["dd_pct"]))
            prev = r
        ref = D18.load("r2ref")
        out.update(t_vs_r2=_tstat(act), worst_dd_gap_pp=round(gap, 3),
                   ref_after_fee_ret_pct=round(100 * (ref["after_fee_usd"] / ref["notional_usd"] - 1), 3) if ref else None,
                   ref_max_dd_pct=ref["max_dd_pct"] if ref else None)
        out["excess_after_fee_pp"] = round(out["after_fee_ret_pct"] - out["ref_after_fee_ret_pct"], 3) if out["ref_after_fee_ret_pct"] is not None else None
    stage = "RUNNING"
    if months >= cfg["review_months"]:
        if tid == "ns_cf1":
            ok = cagr is not None and cagr >= 0.19 and st["max_dd_pct"] > -50 and af > 0
        else:
            ref = D18.load("r2ref"); ok = False
            if ref and days >= 365:
                c0 = (ref["after_fee_usd"] / ref["notional_usd"]) ** (365.25 / days) - 1; c1 = (1 + af) ** (365.25 / days) - 1
                ok = c1 >= c0 + 0.01 and st["max_dd_pct"] >= ref["max_dd_pct"] - 2
        stage = "24M REVIEW: " + ("criteria met (still a near-miss; Head Trader decides)" if ok else "criteria NOT met (retire)")
    elif months >= cfg["interim_months"]: stage = "12M INTERIM LOOK DUE / DONE (no decision before 24 months)"
    out["stage"] = stage
    return out

def kill_check(tid, st, pr):
    if st["kill"]["active"]: return
    if tid == "ns_cf1" and st["max_dd_pct"] <= TESTS[tid]["dd_kill_pct"]:
        st["kill"] = {"active": True, "reason": "drawdown %.1f%% <= -60%%" % st["max_dd_pct"], "date": st["last_date"]}
    if tid == "ns_cf7" and pr.get("worst_dd_gap_pp", 0) < -TESTS[tid]["dd_kill_pp"]:
        st["kill"] = {"active": True, "reason": "DD %.1fpp deeper than R2 reference" % -pr["worst_dd_gap_pp"], "date": st["last_date"]}

# ------------------------------------------------------------------ run
def run(now=None, prices_file=None, fetch=fetch_prices):
    import fcntl
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, ".deep19_fwd_shadow.lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        up = D18.final_session("US", now)
        px = D18.load_snapshot(prices_file) if prices_file else fetch()
        if not prices_file: D18.save_snapshot(px, os.path.join(STATE_DIR, "prices_snapshot_shadow.csv"))
        lb = max(px["adj"].get("SPY", {}) or [None], default=None)
        if lb is None: raise RuntimeError("no SPY bars: price fetch failed")
        up = min(up, lb); start = max(up, START_NOT_BEFORE); done = {}
        for tid in ORDER:
            st = load(tid); rows = []
            if st and st["kill"]["active"]: done[tid] = []; continue
            try:
                if tid == "ns_s01":
                    if st is None: st = s01_new(start)
                    st, rows = s01_advance(st, px, up, dt.datetime.now(D18.SYD).date())
                else:
                    if st is None: st, rows = new_state(tid, start, px)
                    st, more = advance(tid, st, px, up); rows += more
            except Exception as e:
                RUN_ERRORS.append("%s stopped: %s" % (tid, str(e)[:120]))
                log("%s stopped: %s" % (tid, str(e)[:200])); print("%s stopped: %s" % (tid, e), file=sys.stderr)
                if st is None: continue
            st["updated_utc"] = dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            save(tid, st, rows)
            if tid != "ns_s01": pr = progress(tid, st); kill_check(tid, st, pr); save(tid, st)
            done[tid] = [r["date"] for r in rows]
        log(("run FAILED (%d errors: %s)" % (len(RUN_ERRORS), "; ".join(RUN_ERRORS)[:300]) if RUN_ERRORS else "run ok") + ": final US %s; processed %s" % (up, json.dumps(done)))
        return done

# ------------------------------------------------------------------ dashboard (read-only)
def _rules_sha():
    try:
        import hashlib
        with open(RULES_FILE, "rb") as f: return hashlib.sha256(f.read()).hexdigest()
    except Exception: return None

def summary(tid, curve_points=400):
    if tid not in TESTS: return {"status": "UNKNOWN_TEST", "id": tid, "known": ORDER}
    cfg = TESTS[tid]; st = load(tid)
    base = {"id": tid, "fid": cfg["fid"], "name": cfg["name"], "label": LABEL, "rule": cfg["rule"], "pass_rule": cfg["pass_rule"],
            "kill_rule": cfg["kill_rule"], "gap_risk": cfg["gap"], "paper_shadow_only": True, "in_firm_equity": False,
            "desk_capital_usd": 0, "orders": "NONE", "passed_deep19": False, "field_labels": {"shadow_value_usd": "hypothetical notional (not capital; alias hypothetical_notional_usd)"}}  # 2026-10-04 TFO Risk nit: label
    base["label"] = cfg.get("label", LABEL)
    if tid == "ns_s01": base.update(concentration_note=cfg["concentration"], impl_note=cfg["impl_note"], spec_sha256=S01_SPEC_SHA)
    if not st: return dict(base, status="PENDING", note="Starts at the first run after a final US close (launchd com.tfo.deep19-fwd).")
    if tid == "ns_s01":
        H = history(tid); v = st["nav_usd"]
        months = round((dt.date.fromisoformat(st["last_date"]) - dt.date.fromisoformat(st["start_date"])).days / 30.44, 2)
        act = [float(r["port_ret"]) - float(r["spy_ret"]) for r in H if r.get("port_ret") not in (None, "")]
        stage = "24M REVIEW DUE (Head Trader)" if months >= 24 else ("12M INTERIM LOOK DUE / DONE" if months >= 12 else "RUNNING")
        mx = max([min(1.0 / max(len(st["open"]), 1), S01["wmax"])] if st["open"] else [0.0])
        return dict(base, status="OK", start_date=st["start_date"], last_date=st["last_date"], updated_utc=st.get("updated_utc"),
                    notional_usd=st["notional_usd"], shadow_value_usd=round(v, 2), hypothetical_notional_usd=round(v, 2), after_fee_value_usd=round(st["after_fee_usd"], 2),
                    spy_value_usd=round(st["spy_nav_usd"], 2), pnl_net_costs_usd=round(v - st["notional_usd"], 2),
                    pnl_net_costs_and_fee_usd=round(st["after_fee_usd"] - st["notional_usd"], 2),
                    vs_spy_usd=round(v - st["spy_nav_usd"], 2), dd_pct=round(100 * (v / st["peak_usd"] - 1), 3), max_dd_pct=st["max_dd_pct"],
                    worst_day_pct=st["worst_day_pct"], open_positions=st["open"], pipeline=st.get("pipeline", []), skipped=st["skipped"][-5:],
                    tracked_syms=st.get("tracked_syms"), max_name_weight_pct=round(100 * mx, 1),
                    live_gap_loss_pct_if_sector_gaps_20=round(-100 * 0.5 * mx, 2) if st["open"] else 0.0, lev_etf_weight_pct=0.0,
                    concentration=s01_concentration(st), kill=st["kill"],
                    progress={"months": months, "interim_months": 12, "review_months": 24, "t_vs_spy": _tstat(act), "stage": stage,
                              "max_dd_pct": st["max_dd_pct"], "worst_day_pct": st["worst_day_pct"]},
                    equity_curve=[{"date": r["date"], "value_usd": float(r["value_usd"]), "after_fee_usd": float(r["after_fee_usd"]),
                                   "ref_after_fee_usd": float(r["spy_value_usd"])} for r in H[-curve_points:]],
                    last_events=[r["date"] + ": " + r["events"] for r in H if r.get("events")][-5:])
    H = history(tid); v = value(st); pr = progress(tid, st, H)
    sig = st.get("sig") or {}; pend = st.get("pending") or {}
    w = {k: round(100 * x / v, 2) for k, x in sorted(st["pos_usd"].items())} if v else {}
    lev_w = sum(x for k, x in w.items() if k not in MF and k not in ("TQQQ", "TMF", "UGL", "BTC-USD")) / 100.0
    live_gap = round(-100 * 0.6 * lev_w * (1.0 if tid == "ns_cf1" else 1.0), 2)
    HR = {r["date"]: r for r in D18.history("r2ref")} if tid == "ns_cf7" else {}
    cur = [{"date": r["date"], "value_usd": float(r["value_usd"]), "after_fee_usd": float(r["after_fee_usd"]),
            "ref_after_fee_usd": float(HR[r["date"]]["after_fee_usd"]) if r["date"] in HR else None} for r in H[-curve_points:]]
    return dict(base, status="OK", start_date=st["start_date"], last_date=st["last_date"], updated_utc=st.get("updated_utc"),
                notional_usd=st["notional_usd"], shadow_value_usd=round(v, 2), hypothetical_notional_usd=round(v, 2), after_fee_value_usd=round(st["after_fee_usd"], 2),
                pnl_net_costs_usd=round(v - st["notional_usd"], 2), pnl_net_costs_and_fee_usd=round(st["after_fee_usd"] - st["notional_usd"], 2),
                costs_usd=round(st["costs_usd"], 2), dd_pct=round(100 * (v / st["peak_usd"] - 1), 3), max_dd_pct=st["max_dd_pct"],
                worst_day_pct=st.get("worst_day_pct"), weights_pct=w, lev_etf_weight_pct=round(100 * lev_w, 2),
                live_gap_loss_pct_if_sector_gaps_20=live_gap,
                current={"asof": sig.get("asof"), "pick": sig.get("pick"), "etf": sig.get("etf"), "top3": sig.get("top3"), "w": sig.get("w")},
                pending={"asof": pend.get("asof"), "trade": pend.get("trade"), "pick": pend.get("pick"), "etf": pend.get("etf"),
                         "top3": pend.get("top3"), "w": pend.get("w")} if pend else None,
                r2_kill=st["r2_kill"] if tid == "ns_cf7" else None, kill=st["kill"], progress=pr, equity_curve=cur,
                last_events=[r["date"] + ": " + r["events"] for r in H if r.get("events")][-5:])

def summary_all(curve_points=120):
    tests = []
    for t in ORDER:
        try: tests.append(summary(t, curve_points))
        except Exception as e: tests.append({"id": t, "status": "ERROR", "error": str(e)[:200]})
    return {"status": "OK", "label": LABEL, "title": "DEEP19 unproven shadows (NS7 C-factors-7, TFO Equity S01)", "labels": {t: TESTS[t].get("label", LABEL) for t in ORDER}, "paper_shadow_only": True, "desk_capital_usd": 0,
            "passed_deep19": False, "rules_file": "research/deep19/fwd/DEEP19_FWD_RULES_20261003.md", "rules_sha256": _rules_sha(),
            "why_failed": "NS7 (C-factors-7) failed DEEP19 round 4's deflated-Sharpe hurdle; TFO Equity's real-ETF audit: holdout +5.3pp vs R2 but only +0.4pp over 1999-2026 with MaxDD -47% vs R2 -34%. S01 (TFO Equity) FAILED in-sample 2016-20; holdout DSR 0.46; 62% of holdout trade returns from 5 names. NS1/Q01 dropped 3 Oct 21:51 (audit: 8.7% CAGR, -87% MaxDD, ~$0.25m capacity on real ETFs).", "dropped": DROPPED,
            "review": "interim look at 12 months, decision review at 24 months (Head Trader)",
            "fee_daily_pct": 100 * D18.FEE_DAY, "fee_history": [{"from": e.isoformat(), "rate_pct_day": round(100 * r, 6)} for e, r in D18.FEE_SCHEDULE],
            "schedule": "launchd com.tfo.deep19-fwd: 07:50/08:50/10:50/12:50 Sydney after the US close (AEDT-aware), idempotent",
            "liquidity_rule": "NS7 picks across all 12 sectors + SPY as in the audit V3 (no ADV filter; capacity ~$5.5m R2 book); S01 skips names under $2 or $2m 60d median $ADV",
            "tests": tests, "generated_utc": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}

def main():
    ap = argparse.ArgumentParser(description="DEEP19 near-miss PAPER shadows (no orders, $0 desk capital)")
    sp = ap.add_subparsers(dest="c"); r = sp.add_parser("run"); r.add_argument("--prices-file"); sp.add_parser("status")
    a = ap.parse_args()
    if a.c == "status": print(json.dumps(summary_all(5), indent=1, default=str)); return
    if a.c == "run":
        print(json.dumps({"processed": run(prices_file=a.prices_file), "errors": RUN_ERRORS}, indent=1, default=str))
        if RUN_ERRORS: sys.exit(1)   # launchd shows a non-zero exit instead of a silent ok
        return
    ap.print_help()

if __name__ == "__main__":
    main()
