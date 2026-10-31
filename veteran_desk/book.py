"""Veteran Desk PAPER books - one A$500,000 ledger PER TRADING SEAT (11 seats, A$5,502,250 total: 9 x A$500k seed Brett 2026-10-28 16:57 WITA + tech_ai_infra/trend A$500k each + A$2,250 leaderboard prizes; replaces single book of 16:49). PAPER only, no broker, no live orders.
Trades ONLY veteran-pipeline setups that passed gate_v2 + Marcus; since Brett 2026-10-28 19:05 WITA they auto-arm on PAPER with no funder tick (entries arrive decision=approve; Brett may still --reject before the next tick). Every trade has a code-enforced stop.
Risk per trade 2-5% of book equity (2% default, 4% for A+ - Brett 2026-10-29 12:59 WITA, hard cap 5%). Firm rails: no new entries when
reports/firm_risk.json new_entries_halted or level=HALT; book heat cap 15% open risk; max 8 open. Stops/targets run in code (no AI).
  python3 -m veteran_desk.book tick | status"""
import datetime as dt, json, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from veteran_desk import pit as P
BASE = P.BASE; UTC = dt.timezone.utc; _REAL_BOOK = os.path.join(BASE, "reports", "veteran_desk_book.json")   # old VD book (never touched by THF)
THF = os.environ.get("THF_DESK") == "1"; THF_DIR = os.path.join(BASE, "state", "thf"); THF_BOOK = os.path.join(THF_DIR, "thf_book.json")   # tfo_thffresh_20261030: Taylor Hedge Fund fresh paper book
BOOK = THF_BOOK if THF else _REAL_BOOK
def is_live_book(): return os.path.abspath(BOOK) in (os.path.abspath(_REAL_BOOK), os.path.abspath(THF_BOOK) if THF else None)
TRADERS = ["energy", "essentials_utilities", "banks", "healthcare", "macro", "news_filings", "price_action", "crypto", "fx_commodities", "tech_ai_infra", "trend"]   # 11 seats x A$500k = A$5.5m (Nico Hart added 17:06; Dex Romano trend seat added Brett 2026-10-28 20:13 WITA)
SEAT_AUTH = {"trend": "Brett 2026-10-28 20:13 WITA (\"Yes, give Dex a new A$500k paper seat.\")"}
SEAT_SEED = 500000.0; SEED_AUD = SEAT_SEED * len(TRADERS); SPEC_SLEEVE_PCT = .10   # A$50k speculative sleeve per seat (Brett 17:04)
EXEC_TRADER = "Sam Okafor"; MAX_SLIP = .0015; SLIP_COLLAR_BPS = 15.0; OPEN_AVOID_MIN = 15
OPEN_UTC = {"US": (13, 30), "ASX": (0, 0), "FX": None, "FUT": (13, 30), "CRYPTO": None, "IDX": None}
KILL_HALVE_DD, KILL_FLAT_DD = -.08, -.12
HEDGE_PCT = {"calm": .0025, "choppy": .005, "risk-off": .01, "crisis": .02, "unknown": .01}   # tail-hedge premium budget, % of desk equity
def posture():
    """Risk-off FUND default: posture is the stricter of the news regime and 'risk-off'."""
    try: f = json.load(open(os.path.join(BASE, "state", "veteran_desk", "news.json")))["regime"]["flag"]
    except Exception: f = "unknown"
    if f == "unknown": return f, "unknown"   # FAIL-SAFE (TFO HT 22:55): regime inputs missing -> no new arms, 0.5x size cap
    return f, ("crisis" if f == "crisis" else "risk-off")
def in_open_window(venue, t):
    if venue == "ASX":   # 2026-10-29 fixer: ASX opens 10:00 Australia/Sydney (23:00 UTC in AEDT); fixed (0,0) UTC was an hour late in daylight time
        from zoneinfo import ZoneInfo
        sy = t.astimezone(ZoneInfo("Australia/Sydney")); m = sy.hour * 60 + sy.minute - 600; return 0 <= m < OPEN_AVOID_MIN
    o = OPEN_UTC.get(venue)
    if not o: return False
    m = t.hour * 60 + t.minute - (o[0] * 60 + o[1]); return 0 <= m < OPEN_AVOID_MIN
US_HOLIDAYS = {"2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"}   # NYSE full closes (fix 2/7, TFO HT approved 2026-10-28)
ASX_HOLIDAYS = {"2026-12-25", "2026-12-28", "2027-01-01", "2027-01-26", "2027-03-26", "2027-03-29", "2027-04-26", "2027-06-14", "2027-12-27", "2027-12-28"}   # ASX full closes (fixer 2026-10-29)
INTL_SESSIONS = {"TSE": ("Asia/Tokyo", [(540, 690), (750, 930)]), "LSE": ("Europe/London", [(480, 990)]), "Euronext": ("Europe/Paris", [(540, 1050)]),
                 "XETRA": ("Europe/Berlin", [(540, 1050)]), "NORDIC": ("Europe/Stockholm", [(540, 1050)]), "SIX": ("Europe/Zurich", [(540, 1050)]),
                 "MILAN": ("Europe/Rome", [(540, 1050)]), "BME": ("Europe/Madrid", [(540, 1050)]), "HKEX": ("Asia/Hong_Kong", [(570, 720), (780, 960)]),
                 "SGX": ("Asia/Singapore", [(540, 720), (780, 1020)]), "TSX": ("America/Toronto", [(570, 960)]), "NZX": ("Pacific/Auckland", [(600, 1005)]),
                 "KRX": ("Asia/Seoul", [(540, 930)]), "TWSE": ("Asia/Taipei", [(540, 810)]), "NSE": ("Asia/Kolkata", [(555, 930)])}   # local mins, exchange holidays not modelled (stale-price guard covers them)
def session_open(venue, t):
    """Fix 2/7: True while the venue's regular session is open. US = NYSE 09:30-16:00 America/New_York Mon-Fri ex holidays
    (21:30-04:00 WITA in EDT, 22:30-05:00 WITA after 1 Nov). Other venues: not gated here (returns True)."""
    if venue == "ASX":   # 2026-10-29 fixer: ASX 10:00-16:12 Australia/Sydney Mon-Fri (incl. closing auction); no off-hours fills on a stale last bar, no false stale-mark ALERTs after the close
        from zoneinfo import ZoneInfo
        sy = t.astimezone(ZoneInfo("Australia/Sydney"))
        if sy.weekday() >= 5 or sy.date().isoformat() in ASX_HOLIDAYS: return False
        m = sy.hour * 60 + sy.minute; return 600 <= m < 972
    if venue in INTL_SESSIONS:   # stalefill 2026-10-29 (TFO 19:21 WITA): no off-hours fills on non-US/ASX equity venues (6098.T filled with TSE closed)
        from zoneinfo import ZoneInfo
        tz, wins = INTL_SESSIONS[venue]; lt = t.astimezone(ZoneInfo(tz))
        if lt.weekday() >= 5: return False
        m = lt.hour * 60 + lt.minute; return any(a <= m < b for a, b in wins)
    if venue != "US": return True
    from zoneinfo import ZoneInfo
    ny = t.astimezone(ZoneInfo("America/New_York"))
    if ny.weekday() >= 5 or ny.date().isoformat() in US_HOLIDAYS: return False
    m = ny.hour * 60 + ny.minute; return 570 <= m < 960
def first_print_after_open(sym, t):
    """Fix 2/7: first 1-minute print at/after today's US open (Open of first bar), for rebooking off-hours fills. None if no bar yet.
    2026-10-29 infra: yfinance empty/crash under py3.9 -> Yahoo chart v8 1m (still Yahoo; IBKR stays backup elsewhere)."""
    from zoneinfo import ZoneInfo
    ny = t.astimezone(ZoneInfo("America/New_York")); op = ny.replace(hour=9, minute=30, second=0, microsecond=0)
    h = None
    try:
        import yfinance as yf
        h = yf.Ticker(sym).history(period="1d", interval="1m")
    except Exception: h = None
    if h is not None and len(h):
        h = h[h.index >= op]
        if len(h): return dict(px=float(h.Open.iloc[0]), asof=str(h.index[0]))
    try:
        q = yahoo_v8_quote(sym, "1m", "1d")
        if not q: return None
        return dict(px=float(q["last"]), asof=q["asof"], src="yahoo_chart_v8")
    except Exception: return None
RISK_MIN, RISK_A_PLUS, RISK_MAX, RISK_SPEC_OF_SLEEVE, HEAT_CAP, MAX_OPEN, ARM_HOURS = .01, .04, .05, .05, .15, 8, 48   # risk-off fund: base 2% (floor of Brett range), A+ 4% (Brett 2026-10-29 12:59 WITA; was 2.5%); spec trades risk 5% of A$50k sleeve
RISK_CRYPTO_MIN = .005   # Brett 2026-10-29 13:08 WITA: crypto seat only, floor 0.5% (0.5-1% band) of seat equity; all other seats keep 2%
RISK_CRYPTO_DEFAULT, RISK_CRYPTO_MAX = .01, .02   # kairails Brett 2026-10-29 14:28 WITA: crypto seat default 1%, band 0.5-2% (A+ = 2%); hard cap RISK_MAX 5% still applies
RISK_A_CAPBIND_MIN = .015   # tfo_a1pct_20261031 (Brett 00:07 WITA): when the 40% single-stock seat cap binds, grade-A may go to 1.5%
RISK_NON_A_MIN = .02   # non-A still needs 2%
RISK_A_DEFAULT, RISK_A_MAX = .01, .02   # tfo_a1pct_20261031 (Marcus 00:19): grade A sizes at 1% by default (seat may lift to 2% once proven) (1% allowed until proven; default/lift-to 2%); NEVER force a 1% seat choice up; A+ 4%; cap 5%
CRYPTO_CLIP_AUD, CRYPTO_CLIP_APLUS_AUD = 50000.0, 100000.0   # kairails: crypto notional per clip A$50k standard, A$100k only for A+
from zoneinfo import ZoneInfo as _ZI
SOFT_TRIM_UNTIL = dt.datetime(2026, 10, 30, 15, 11, tzinfo=_ZI("Asia/Makassar"))   # softtrim Brett 2026-10-29 16:12 WITA (date fixed HT 16:24: Fri = 30 Oct): NEW crypto-seat arms A+ only until Fri 30 Oct 15:11 WITA; open/armed untouched
def crypto_clip_cap(grade=None): return CRYPTO_CLIP_APLUS_AUD if grade == "A+" else CRYPTO_CLIP_AUD
def crypto_clip(risk_aud, per_unit_aud, unit_aud, equity_aud, grade=None):
    """kairails: cut a crypto clip to its notional cap (A$50k / A$100k A+); returns (risk_aud, units, reject). Reject 'below_crypto_floor' if the capped clip risks < 0.5% of seat equity."""
    q = (risk_aud / per_unit_aud) if per_unit_aud > 0 else 0.0; cap = crypto_clip_cap(grade)
    if unit_aud > 0 and q * unit_aud > cap: q = cap / unit_aud
    r = q * per_unit_aud
    return r, q, ("below_crypto_floor" if r < float(equity_aud) * RISK_CRYPTO_MIN - 0.01 else None)
RISK_A_PLUS_AGREE = .04   # Brett 2026-10-29 12:59 WITA "Lift A+ to 4%" (was .03)
THEMES = {"oil": {"XOM", "CVX", "COP", "EOG", "OXY", "XLE", "XOP", "VDE", "OIH", "USO", "BNO", "CL=F", "BZ=F", "MCL=F", "WDS.AX", "STO.AX", "BPT.AX", "SHEL", "SHEL.L", "BP", "BP.L", "TTE", "EQNR", "SU", "CNQ", "CNQ.TO", "SU.TO", "MPC", "VLO", "PSX", "HES", "DVN", "FANG", "APA", "SLB", "HAL", "BKR", "CTRA", "PXD", "MRO", "E", "ENI.MI", "REP.MC", "IMO", "CVE", "0883.HK", "0857.HK", "0386.HK", "5020.T", "1605.T", "RELIANCE.NS", "ONGC.NS"}}   # tfo_theme_20261031
def theme_of(ticker):
    s = str(ticker or "").upper().replace("KRAKEN:", "")
    return next((k for k, v in THEMES.items() if s in v), None)
def _theme_clash(D, e):
    """tfo_theme_20261031: firm one-trade-per-theme - a new arm is blocked if any seat already holds an open/armed position on the same theme and side. Returns (theme, ticker) or None."""
    th = theme_of(e.get("ticker"))
    if not th: return None
    for _s, _B in (D.get("seats") or {}).items():
        for _p in (_B.get("positions") or {}).values():
            if _p.get("id") != e.get("id") and _p.get("status") in ("open", "armed") and _p.get("side") == e.get("side") and theme_of(_p.get("ticker")) == th: return (th, "%s held by %s" % (_p.get("ticker"), _s))
    return None
def risk_plan(equity_aud, grade=None, chal_agree=False, mult=1.0, floor=None, seat_pct=None):
    """ONE sizing source. tfo_a1pct_20261031 (Brett 00:07 WITA): A may pass at 1% (lift to 2% once proven - NEVER force a 1% seat choice up); A+ 4%; non-A floor 2%; hard cap 5%.
    Cuts move risk toward the floor, never below it, never above 5%. Returns dict(pct, risk_aud, wanted_pct, warning)."""
    base = RISK_A_PLUS_AGREE if (grade == "A+" and chal_agree) else RISK_A_PLUS if grade == "A+" else (RISK_NON_A_MIN if grade != "A" else (RISK_A_DEFAULT if seat_pct in (None, "") else min(max(float(seat_pct), RISK_MIN), RISK_A_MAX)))   # tfo_a1pct_20261031: A 1% default (seat may choose up to 2%); non-A 2%
    fl = RISK_MIN if floor is None else float(floor)
    crypto = abs(fl - RISK_CRYPTO_MIN) < 1e-12   # kairails 14:28 WITA: crypto seat band 0.5-2%, default 1%, A+ 2%
    if crypto: base = RISK_CRYPTO_MAX if grade == "A+" else RISK_CRYPTO_DEFAULT   # floor=RISK_CRYPTO_MIN for the crypto seat (Brett 2026-10-29 13:08 WITA)
    want = base * float(mult if mult is not None else 1.0)
    if not crypto and grade in ("A", "A+") and want < RISK_A_DEFAULT: want = RISK_A_DEFAULT   # tfo_review_fix_20261031: a cut A sizes to max(cut, 1%) (thf_review rule) - never rejected below_1pct_floor
    if not crypto and grade not in ("A", "A+"): fl = max(fl, RISK_NON_A_MIN)   # tfo_review_fix_20261031: non-A floor 2% holds after cuts -> reject (never lift, railfix rule)
    if want < fl - 1e-12:   # railfix 2026-10-29 (Rules Auditor 14:09 WITA): REJECT, never lift a sub-floor idea (crypto seat floor stays 0.5%)
        why = ("below_2pct_nonA_floor" if fl >= RISK_NON_A_MIN - 1e-12 else "below_1pct_floor") if fl >= RISK_MIN - 1e-12 else "below_crypto_floor"
        return dict(pct=0.0, risk_aud=0.0, wanted_pct=round(want, 5), base_pct=base, reject=why,
                    warning="%s: sizing wanted %.2f%% of seat equity (cuts x%.2f) < %.1f%% floor - rejected, not lifted (Brett rule)" % (why, want * 100, float(mult), fl * 100))
    pct = min(want, RISK_CRYPTO_MAX if crypto else RISK_MAX, RISK_MAX)   # per-trade hard cap 5% (A+ 4%); crypto band top 2%
    return dict(pct=round(pct, 5), risk_aud=round(float(equity_aud) * pct, 2), wanted_pct=round(want, 5), base_pct=base, warning=None, reject=None)
def seat_equity(seat):
    """Seat equity for sizing the tick sheet: last book mark, else fresh A$500k seed."""
    try: return float(json.load(open(BOOK))["seats"][seat].get("equity_aud") or SEAT_SEED)
    except Exception: return SEAT_SEED
COST_RT_LEGACY_20261029 = {"US": .0010, "ASX": .0020, "FX": .0003, "FUT": .0006, "IDX": .0010, "CRYPTO": .0065,
           "LSE": .0020, "Euronext": .0020, "XETRA": .0020, "TSE": .0020, "HKEX": .0020, "SGX": .0020,
           "KRX": .0020, "TWSE": .0020, "NSE": .0020, "NZX": .0020, "SIX": .0020, "BME": .0020, "MILAN": .0020,
           "NORDIC": .0020, "TSX": .0010, "SSE": .0020, "SZSE": .0020}   # intl equities ~20bp RT default; crypto: Kraken 0.25% maker in + 0.40% taker out
from veteran_desk import cost_tables as CT   # ONE SOURCE for cost tables (book/desk/gate_v2) - tfo_costmodel_20261030
COST_RT = CT.venue_rt_table()   # venue RT fractions DERIVED from CT tables (legacy shape); frozen pre-cutover table above is used ONLY to re-baseline peaks
# ---- PER-VENUE COST MODEL (tfo_costmodel_20261030; PAPER). COST_RT above kept ONLY as legacy fallback / pre-trade estimate. ----
# Leg cost (A$) = commission + half-spread (ADV bucket) + taxes (buy/sell legs) + FX conversion (non-AUD) ; crypto Kraken maker-in/taker-out; futures per whole contract.
from veteran_desk.cost_tables import CM_SUFFIX, CM_COMM, CM_TAX, CM_SPREAD_BPS, CM_SPREAD_TIER_BPS, CM_FX_CONV, CM_KRAKEN, CM_FUT, CM_BORROW_DEFAULT_PA   # noqa
def cm_market(p): return CT.market(p["ticker"], p.get("venue") or P.venue_of(p["ticker"]))
def _major_to_aud(p, au):
    """A$ per 1 unit of the listing's MAJOR currency (GBP not pence)."""
    try:
        c = listing_ccy(p["ticker"]); div = _SUBUNIT.get(c, (c, 1.0))[1]
        return aud_per_unit(p["ticker"], au) * div
    except Exception: return 1.0 / au
def _spread_bps(p, mk): return CT.spread_bps(p["ticker"], mk, p.get("adv_aud"))   # ADV bucket, else liquidity tier (ruling 5)
def leg_cost_aud(p, px, q, au, marks=None, leg="exit", detail=False):
    """A$ cost of one leg (entry|exit) of q units of p at px. Table driven. detail=True -> (total, parts)."""
    mk = cm_market(p); q = abs(float(q)); px = float(px); L = p["side"] == "LONG"; buy = (leg == "entry") == L
    N = (px * q / quote_to_aud(p["ticker"], au, marks)) if is_fx(p) else aud(p["ticker"], px, au) * q
    parts = dict(spread=N * _spread_bps(p, mk) / 1e4)
    if mk == "CRYPTO":
        parts["kraken"] = N * CM_KRAKEN["entry" if leg == "entry" else "exit"]
    elif mk == "FUT":
        F = CM_FUT.get(p["ticker"])
        if F:
            if q % F["mult"] == 0 or not F.get("alt"): n_c, fee = q / F["mult"], F["comm"] + F["exch"]
            else: n_c, fee = -(-q // F["alt_mult"]), F["alt_comm"] + F["alt_exch"]
            parts["contract_fees"] = n_c * fee / au
        else: parts["comm"] = N * .0003
    else:
        C = CM_COMM.get(mk, CM_COMM["DEFAULT"]); m2a = _major_to_aud(p, au)
        if "per_share" in C: parts["comm"] = max(C["per_share"] * q, C.get("min_ccy", 0)) * m2a + C.get("clearing_per_share", 0) * q * m2a
        else: parts["comm"] = max(C["pct"] * N, C.get("min_aud", 0.0))
        T = CM_TAX.get(mk) or {}
        tax = N * (T.get("buy", 0) if buy else T.get("sell", 0))
        if buy and T.get("ptm_ccy") and N / m2a > T.get("ptm_over_ccy", 0): tax += T["ptm_ccy"] * m2a
        if tax: parts["tax"] = tax
    try: nonaud = (listing_ccy(p["ticker"]) != "AUD") if mk not in ("FX", "CRYPTO", "FUT", "IDX") else (mk != "FX" and not p["ticker"].upper().endswith("AUD"))
    except Exception: nonaud = mk != "AU"
    if nonaud: parts["fx_conv"] = N * CM_FX_CONV
    tot = sum(parts.values())
    return (tot, {k: round(v, 2) for k, v in parts.items()}) if detail else tot
def _adv_default(p, au):
    """20-day average daily traded value in A$ (yfinance); None on failure / crypto / FX / futures."""
    if cm_market(p) in ("CRYPTO", "FX", "FUT", "IDX"): return None
    if not is_live_book(): return None   # temp/sim books (tests): no network; tier spread applies (THF live book uses network)
    try:
        import yfinance as yf
        h = yf.Ticker(p["ticker"]).history(period="2mo", interval="1d").tail(20)
        if len(h) < 5: return None
        return round(float((h["Close"] * h["Volume"]).mean()) * aud_per_unit(p["ticker"], au), 0)
    except Exception: return None
ADV_FN = _adv_default
def rt_rate(p, au=None):
    """Round-trip cost as a fraction of fill notional (for BE floors). Stored per position by the tick; legacy fallback COST_RT."""
    r = p.get("cost_rt_rate")
    if r is None and au:
        try:
            f = float(p["fill"]); q = _qty(p); N = (f * q / quote_to_aud(p["ticker"], au)) if is_fx(p) else aud(p["ticker"], f, au) * q
            r = (leg_cost_aud(p, f, q, au, None, "entry") + leg_cost_aud(p, f, q, au, None, "exit")) / N if N else None
        except Exception: r = None
    return float(r) if r is not None else CT.est_rt_rate(p.get("ticker"), p.get("venue"), p.get("fill") or p.get("entry"), p.get("adv_aud"))
def borrow_accrue(B, p, t):
    """Daily borrow on open shorts (US included): fee_pa from borrow data, else CM_BORROW_DEFAULT_PA. Charges seat cash; tracks borrow_accrued_aud."""
    if p.get("side") != "SHORT" or p.get("status") != "open": return 0.0
    dk = t.astimezone(dt.timezone(dt.timedelta(hours=8))).date()
    try: last = dt.date.fromisoformat(p.get("borrow_accrued_day") or str(p.get("filled_utc"))[:10])
    except Exception: last = dk
    if p.get("borrow_accrued_day") is None and p.get("borrow_min_charge_days"): last = last + dt.timedelta(days=int(p["borrow_min_charge_days"]))   # min charge already booked at fill
    days = (dk - last).days
    if days <= 0: return 0.0
    bw = ((p.get("pre_trade") or {}).get("borrow") or {})
    fee = p.get("borrow_fee_pa_est") or bw.get("fee_pa") or CM_BORROW_DEFAULT_PA.get(cm_market(p), CM_BORROW_DEFAULT_PA["DEFAULT"])
    amt = float(p.get("mark_notional_aud") or 0) * float(fee) * days / 360.0
    B["cash_aud"] -= amt; p["borrow_accrued_aud"] = round(float(p.get("borrow_accrued_aud") or 0) + amt, 2); p["borrow_accrued_day"] = dk.isoformat(); return amt
def adv_refresh(p, t, au):
    """Daily ADV (A$) on its own key adv_day (WITA); a failed fetch retries at most hourly. Same ADV_FN as the cost preview."""
    if p.get("status") != "open": return
    dk = t.astimezone(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
    if p.get("adv_day") == dk and (p.get("adv_aud") or str(p.get("adv_try_utc") or "")[:13] == t.isoformat()[:13]): return
    try: v = ADV_FN(p, au)
    except Exception: v = None
    if v: p["adv_aud"] = v
    p.update(adv_day=dk, adv_try_utc=t.isoformat())
def trigger_hold(p, t):
    """Reason str when profit-side triggers must hold: market closed or mark stale (px_age_min > venue stale limit). None = act."""
    v = p.get("venue")
    if _is_crypto(p) or is_fx(p): return None if (p.get("px_age_min") is None or p["px_age_min"] <= stale_limit_min(v)) else "stale_mark"
    try:
        if not session_open(v, t): return "market_closed"
    except Exception: pass
    if p.get("px_age_min") is not None and p["px_age_min"] > stale_limit_min(v): return "stale_mark"
    return None
def _legacy_exit_aud(p, px, au, marks=None):
    if _is_crypto(p): return KRAKEN_TAKER * _notional_aud(p, px, au, marks)
    return COST_RT_LEGACY_20261029.get(p.get("venue") or P.venue_of(p["ticker"]), .002) / 2 * _notional_aud(p, px, au, marks)
def cm_rebaseline(p, px, au, marks, t):
    """ONE-TIME at cost cutover: shift stored peak net R by (old - new) exit cost at the peak price / 1R, so a pure cost change cannot read as giveback."""
    if p.get("status") != "open" or p.get("cm_rebaseline"): return
    try:
        hp = float(p.get("rail_hwp") or px); q = _qty(p); r = risk0_aud(p, au, marks)
        if not r or r <= 0: return
        d = (_legacy_exit_aud(p, hp, au, marks) - leg_cost_aud(p, hp, q, au, marks, "exit")) / r
        rec = dict(utc=t.isoformat(), at_px=hp, delta_R=round(d, 4))
        for k in ("gb_peak_net_R", "ts_peak_net_R", "revguard_peak_net_R"):
            if p.get(k) is not None: rec[k + "_old"] = p[k]; p[k] = round(float(p[k]) + d, 4)
        p["cm_rebaseline"] = rec
    except Exception: pass
def now(): return dt.datetime.now(UTC)
def _wj(p, o):
    """Atomic save like payouts.py: unique temp file in the same dir, flush + fsync, then os.replace; temp removed on any error."""
    import tempfile
    d = os.path.dirname(os.path.abspath(p)); os.makedirs(d, exist_ok=True)
    fd, t = tempfile.mkstemp(prefix="." + os.path.basename(p) + ".", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(o, fh, indent=1, default=str); fh.flush(); os.fsync(fh.fileno())
        os.replace(t, p)
    except BaseException:
        try: os.unlink(t)
        except OSError: pass
        raise
def yahoo_v8_quote(sym, interval="5m", rng="2d"):
    """TFO HT 2026-10-29 11:1x: Yahoo's own chart v8 endpoint (still Yahoo) - used when yfinance returns None/empty."""
    import urllib.request, urllib.parse
    u = "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=%s&includePrePost=false" % (urllib.parse.quote(sym), rng, interval)
    J = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=15).read())
    R = ((J.get("chart") or {}).get("result") or [None])[0]
    if not R: return None
    ts = R.get("timestamp") or []; q = ((R.get("indicators") or {}).get("quote") or [{}])[0]; off = int((R.get("meta") or {}).get("gmtoffset") or 0)
    rows = [(t, q["high"][i], q["low"][i], q["close"][i]) for i, t in enumerate(ts) if q.get("close") and q["close"][i] is not None and q["high"][i] is not None and q["low"][i] is not None]
    if not rows: return None
    tz = dt.timezone(dt.timedelta(seconds=off)); last = rows[-6:]
    return dict(last=float(rows[-1][3]), hi=float(max(r[1] for r in last)), lo=float(min(r[2] for r in last)), asof=str(dt.datetime.fromtimestamp(rows[-1][0], tz)), window_start=dt.datetime.fromtimestamp(last[0][0], tz).isoformat(), src="yahoo_chart_v8")
CRYPTO_XCHECK = .03   # Yahoo vs Kraken >3% apart -> Yahoo symbol is a different coin; use Kraken
YAHOO_CRYPTO = {"XBT": "BTC", "XDG": "DOGE"}   # Kraken altname base -> Yahoo base (rest identical)
def kraken_pair(sym):
    """2026-10-29 krakenfx: KRAKEN:<alt> -> (alt, base, quote). Kraken REST key may differ (XMRUSD -> XXMRZUSD); Ticker accepts the altname and returns the key."""
    alt = str(sym).upper().replace("KRAKEN:", "")
    for q in ("USDT", "USDC", "USD", "EUR", "GBP"):
        if alt.endswith(q) and len(alt) > len(q): return alt, alt[: -len(q)], q
    return alt, alt, None
def kraken_live_px(sym, get=None):
    """Kraken public Ticker via DoH (system DNS for api.kraken.com is ISP-sinkholed; same path as kraken_sweep/feeds.kraken_spot)."""
    alt = kraken_pair(sym)[0]
    if get is None:
        import kraken_dns, urllib.request; kraken_dns.install()
        get = lambda u: json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "TFO paper research"}), timeout=15).read())
    J = get("https://api.kraken.com/0/public/Ticker?pair=" + alt); R = J.get("result") or {}
    if J.get("error") or len(R) != 1: return None
    k, v = next(iter(R.items())); last = float(v["c"][0])
    return dict(last=last, hi=float(v["h"][0]), lo=float(v["l"][0]), asof=dt.datetime.now(UTC).isoformat(), window_start=dt.datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(), src="kraken_rest_doh:" + k)   # Kraken h/l = today since 00:00 UTC
def crypto_live_px(sym, yahoo_fn=None, kraken_fn=None):
    """KRAKEN:<pair> live price. Yahoo primary (<BASE>-<QUOTE>, XBT->BTC), Kraken REST via DoH backup. Never asks yfinance for 'KRAKEN:...' (404 -> 'no live price')."""
    alt, base, q = kraken_pair(sym); y = k = None
    if q:
        try:
            y = (yahoo_fn or yahoo_v8_quote)("%s-%s" % (YAHOO_CRYPTO.get(base, base), q))
            if not (y and y.get("last")): y = None
        except Exception: y = None
    try: k = (kraken_fn or kraken_live_px)(sym)
    except Exception: k = None
    if k and not k.get("last"): k = None
    if y and k and abs(float(y["last"]) / float(k["last"]) - 1) > CRYPTO_XCHECK:   # Yahoo ticker collision (ARB/TON/JUP/AERO-USD are other coins on Yahoo) -> venue price
        return dict(k, src=k.get("src", "kraken") + ":yahoo_mismatch(%g)" % float(y["last"]))
    if y: return dict(y, src=(y.get("src") or "yahoo") + ":crypto")
    return k
def live_px(sym):
    if str(sym).upper().startswith("KRAKEN:"): return crypto_live_px(sym)
    try:   # tfo_massive_20261030: Massive PRIMARY for US equities (fresh 5m bars only); Yahoo below stays the fallback
        from veteran_desk import feeds as _FDM, massive as _MS
        if _FDM.is_us(sym):
            _q = _MS.quote(sym)
            if _q: return _q
    except Exception: pass
    try:   # tfo_paidrt_20261031: EODHD Cboe Europe websocket when healthy; Yahoo below stays the fallback
        from veteran_desk import eodhd_eu_ws as _EU
        _eq = _EU.live_px(sym)
        if _eq: return _eq
    except Exception: pass
    try:
        import yfinance as yf
        h = yf.Ticker(sym).history(period="2d", interval="5m")
    except Exception: h = None
    if h is None or not len(h):
        try: return yahoo_v8_quote(sym)   # 2026-10-29: yfinance None -> Yahoo chart v8 (same source); IBKR stays backup via feeds.px_with_fallback
        except Exception: return None
    return dict(last=float(h.Close.iloc[-1]), hi=float(h.High.iloc[-6:].max()), lo=float(h.Low.iloc[-6:].min()), open=float(h.Open.iloc[-6:].iloc[0]), asof=str(h.index[-1]), window_start=h.index[-6:][0].isoformat())
AUDUSD_MAX_AGE_MIN = 60; AUDUSD_SANE = (0.4, 1.2); _AU_LAST = {}   # tfo_review_20261030 (GPT HIGH): no invented 0.65 - last good rate if <= 60 min old, else block
AUDUSD_CACHE = None   # None -> <state dir>/audusd_last_good.json (temp books write beside the temp book)
def _au_cache(): return AUDUSD_CACHE or (None if os.environ.get("PYTEST_CURRENT_TEST") else os.path.join(_state_dir(), "audusd_last_good.json"))   # pytest: memory only unless a cache path is set
def audusd(px_fn=None, t=None):
    from veteran_desk import feeds as FD
    t = t or dt.datetime.now(UTC)
    try: p = FD.px_with_fallback("AUDUSD=X", px_fn or live_px) if px_fn is None else px_fn("AUDUSD=X")
    except Exception: p = None
    v = float(p["last"]) if p and p.get("last") else None
    if v is not None and math.isfinite(v) and AUDUSD_SANE[0] < v < AUDUSD_SANE[1]:
        _AU_LAST.update(v=v, utc=t.isoformat())
        try:
            if _au_cache(): _wj(_au_cache(), dict(_AU_LAST, src=(p or {}).get("src")))
        except Exception: pass
        return v
    L = dict(_AU_LAST) or ((_rj_safe(_au_cache()) if _au_cache() else None) or {})
    try: age = (t - dt.datetime.fromisoformat(L["utc"])).total_seconds() / 60
    except Exception: age = None
    if age is not None and age <= AUDUSD_MAX_AGE_MIN and L.get("v"): return float(L["v"])
    raise FxUnavailable("AUDUSD unavailable: no live rate and last good rate %s - blocked (no 0.65 fallback)" % ("missing" if age is None else "%.0f min old > %d" % (age, AUDUSD_MAX_AGE_MIN)))
def _rj_safe(p):
    try: return json.load(open(p))
    except Exception: return None
# ---- TFO HT 2026-10-29 fxsize: listing-currency aware AUD conversion ----
# Bug: aud() read every non-ASX price as USD, so 4519.T (JPY) / 2800.HK (HKD) were sized as if quoted in USD (2800.HK: "A$9,999" risk, ~A$1.3k real).
# Now: currency from the Yahoo suffix; live rate Yahoo first (AUDxxx=X, then via USD: AUDUSD x USDxxx), IBKR only as fallback; no rate -> FxUnavailable (fail closed).
# FX pairs (=X: quote_to_aud), futures (=F), indices (^) and Kraken crypto keep their existing USD handling.
class FxUnavailable(RuntimeError): pass
_EUR = ("DE", "F", "BE", "DU", "MU", "SG", "HM", "PA", "AS", "BR", "LS", "MC", "MI", "IR", "VI", "HE", "AT")
SUFFIX_CCY = dict({"AX": "AUD", "T": "JPY", "HK": "HKD", "KS": "KRW", "KQ": "KRW", "TW": "TWD", "TWO": "TWD", "SI": "SGD", "L": "GBp", "IL": "GBp",
                   "SW": "CHF", "ST": "SEK", "CO": "DKK", "OL": "NOK", "NZ": "NZD", "TO": "CAD", "V": "CAD", "NE": "CAD", "NS": "INR", "BO": "INR",
                   "SS": "CNY", "SZ": "CNY", "KL": "MYR", "BK": "THB", "JO": "ZAc", "SA": "BRL", "MX": "MXN", "TA": "ILA"}, **{k: "EUR" for k in _EUR})
_SUBUNIT = {"GBp": ("GBP", 100.0), "ZAc": ("ZAR", 100.0), "ILA": ("ILS", 100.0)}   # quoted in pence/cents/agorot
FX_SANE = {"JPY": (40, 250), "HKD": (3, 9), "KRW": (500, 1600), "TWD": (12, 35), "SGD": (.6, 1.3), "GBP": (.3, .8), "EUR": (.4, 1.0), "CHF": (.4, 1.0),
           "CAD": (.6, 1.3), "NZD": (.9, 1.3), "CNY": (3, 7), "INR": (35, 80), "SEK": (4, 10), "DKK": (3, 7), "NOK": (4, 10)}   # units per 1 AUD; outside -> rate rejected
FX_TTL_S = 900; _FX = {}   # ccy -> (units_per_aud, epoch, src)
def listing_ccy(sym):
    """Quote currency of a listing. 'USD' for no-suffix US names, =F futures, ^ indices, KRAKEN: crypto (existing handling). None for an unknown suffix."""
    s = str(sym or "").upper()
    if s.startswith("KRAKEN:") or s.endswith("=F") or s.startswith("^") or s.endswith("=X"): return "USD"
    if "." not in s: return "USD"
    suf = s.rsplit(".", 1)[-1]
    if s.endswith(".AX"): return "AUD"
    return {k.upper(): v for k, v in SUFFIX_CCY.items()}.get(suf)
def _ibkr_fx(ccy):
    """IBKR fallback only (read-only historical MIDPOINT on paper gateway). Returns units of ccy per 1 AUD or None."""
    try:
        import subprocess
        py = os.path.expanduser("~/ibkr/venv/bin/python")
        if not os.path.exists(py): return None
        out = subprocess.run([py, os.path.join(BASE, "veteran_desk", "ibkr_hist.py"), "--fx", "AUD" + ccy], capture_output=True, text=True, timeout=60).stdout
        v = json.loads(out.strip().splitlines()[-1]).get("AUD" + ccy)
        return float(v) if isinstance(v, (int, float)) and v > 0 else None
    except Exception: return None
def ccy_per_aud(ccy, au=None, px_fn=None, ibkr_fn=None):
    """Units of `ccy` per 1 AUD from LIVE rates: Yahoo AUDccy=X, else Yahoo AUDUSD x USDccy, else IBKR. Raises FxUnavailable (fail closed)."""
    if ccy == "AUD": return 1.0
    if ccy == "USD" and au: return float(au)
    hit = _FX.get(ccy)
    if hit and px_fn is None and dt.datetime.now(UTC).timestamp() - hit[1] < FX_TTL_S: return hit[0]
    pf = px_fn or live_px; lo, hi = FX_SANE.get(ccy, (0, float("inf"))); tries = []
    def _last(t):
        try: m = pf(t) or {}
        except Exception: m = {}
        v = m.get("last") if isinstance(m, dict) else None
        return float(v) if v and v == v and v > 0 else None
    v = _last("AUD%s=X" % ccy); tries.append(("yahoo:AUD%s=X" % ccy, v))
    if not (v and lo <= v <= hi):
        a = _last("AUDUSD=X"); u = _last("%s=X" % ccy) or _last("USD%s=X" % ccy); v = a * u if a and u else None; tries.append(("yahoo:AUDUSDxUSD%s" % ccy, v))
    if not (v and lo <= v <= hi):
        v = (ibkr_fn or _ibkr_fx)(ccy); tries.append(("ibkr:AUD%s" % ccy, v))
    if not (v and lo <= v <= hi): raise FxUnavailable("no live AUD/%s rate (%s) - fail closed, never sized as USD" % (ccy, ", ".join("%s=%s" % t for t in tries)))
    if px_fn is None: _FX[ccy] = (v, dt.datetime.now(UTC).timestamp(), tries[-1][0])
    return v
def aud_per_unit(sym, au, px_fn=None, ibkr_fn=None):
    """AUD value of 1.0 of the listing's quoted price. Raises FxUnavailable for unknown suffix or missing rate.
    2026-10-29 infra: after venue_of maps .T/.HK/... to TSE/HKEX/..., listing-currency path must still run (do NOT treat them as USD via venue!=US)."""
    v = P.venue_of(sym)
    if v == "ASX": return 1.0
    if v == "FX":   # 2026-10-29 krakenfx/fxsize2: FX pairs are quoted in their QUOTE ccy (USDKRW -> KRW), not USD; Theo USDKRW risk showed A$10,064 vs ~A$7 real
        try: return 1.0 / quote_to_aud(sym, au, px_fn=px_fn)
        except Exception as x: raise FxUnavailable("no live quote-ccy rate for %s (%s) - fail closed" % (sym, x))
    if v in ("FUT", "IDX", "CRYPTO"): return 1.0 / au
    c = listing_ccy(sym)
    if c is None: raise FxUnavailable("unknown listing currency for %s - fail closed" % sym)
    if c == "AUD": return 1.0
    if c == "USD": return 1.0 / au
    base, div = _SUBUNIT.get(c, (c, 1.0))
    return 1.0 / (div * ccy_per_aud(base, au, px_fn, ibkr_fn))
def aud(sym, x, au): return x * aud_per_unit(sym, au)
def is_fx(p): return (p.get("venue") or P.venue_of(p["ticker"])) == "FX"
def quote_to_aud(sym, au, marks=None, px_fn=None):
    """TFO HT 29 Oct FX settlement fix: units of the pair's QUOTE currency per 1 AUD (divide a quote-ccy amount by this to get AUD).
    EURUSD=X -> AUDUSD; xxxAUD -> 1; USDJPY=X -> AUDUSD * USDJPY (own mark); EURJPY=X -> AUDUSD * USDJPY mark."""
    pr = sym.upper().replace("=X", ""); base, q = pr[:3], pr[3:6]
    if q == "AUD": return 1.0
    if q == "USD": return au
    def _px(t):
        m = (marks or {}).get(t) or (marks or {}).get(t.replace("USD", "", 1) if t.startswith("USD") else t) or {}
        v = m.get("last") if isinstance(m, dict) else None
        if not v: m = (px_fn or live_px)(t) or {}; v = m.get("last")
        if not v: raise ValueError("no USD%s mark for %s quote conversion" % (q, sym))
        return float(v)
    return au * _px("USD%s=X" % q)
def fx_upnl_aud(p, px, au, marks=None):   # FX: only the P&L is ours; the notional never sat in A$ cash
    return (1 if p["side"] == "LONG" else -1) * p["shares"] * (px - p["fill"]) / quote_to_aud(p["ticker"], au, marks)
def pos_value_aud(p, px, au, marks=None):   # contribution of one open position to seat equity
    return fx_upnl_aud(p, px, au, marks) if is_fx(p) else (1 if p["side"] == "LONG" else -1) * p["shares"] * aud(p["ticker"], px, au)
def _seat_ledger(): return dict(seed_aud=SEAT_SEED, cash_aud=SEAT_SEED, positions={}, closed=[], commission_expense_aud=0.0, commission_forfeit_credit_aud=0.0)
LABEL = "Taylor Hedge Fund (THF) - fresh per-seat paper books (auto-arm, PAPER)" if THF else "TFO Veteran Desk - per-seat paper books (auto-arm, PAPER)"
def load(au=None):
    if os.path.exists(BOOK):
        B = json.load(open(BOOK))
        if "seats" in B:
            B["label"] = LABEL   # 2026-10-28: auto-arm since Brett 19:07 WITA, no longer funder-ticked
            for s_ in TRADERS:   # a newly confirmed trading seat gets its own fresh A$500k (logged as its own injection)
                if s_ not in B["seats"]:
                    au = au or audusd(); B["seats"][s_] = _seat_ledger(); B["seed_aud"] = B.get("seed_aud", 0) + SEAT_SEED; B["start"] = round(B.get("start", 0) + SEAT_SEED * au, 2)
                    if B.get("day_start_equity_aud") is not None: B["day_start_equity_aud"] += SEAT_SEED   # injection is not P&L
                    B["capital_events"].append(dict(type="injection", amount_aud=SEAT_SEED, amount_usd=round(SEAT_SEED * au, 2), utc=now().isoformat(), source=f"fresh funder seed for seat {s_}", authorised=SEAT_AUTH.get(s_, "Brett 2026-10-28 17:06 WITA")))
            return B
    au = au or audusd(); t = now().isoformat()
    return dict(book="thf" if THF else "veteran_desk", label=LABEL, paper_only=True, ccy_native="AUD", seed_aud=SEED_AUD,
                start=round(SEED_AUD * au, 2), audusd_at_seed=au, seats={s: _seat_ledger() for s in TRADERS},
                capital_events=[dict(type="injection", amount_aud=SEED_AUD, amount_usd=round(SEED_AUD * au, 2), utc=t, source=("THF fresh paper book: %d x A$500k seat books, same seats/seed as VD, nothing carried from VD" % len(TRADERS)) if THF else "fresh funder seed capital, 9 x A$500k seat books (not from any other book)",
                                     authorised="Brett 2026-10-30 19:16 WITA (THF separate fresh book)" if THF else "Brett 2026-10-28 16:57 WITA")], created_utc=t, holdings={}, rules=__doc__)
def firm_ok():
    try: f = json.load(open(os.path.join(BASE, "reports", "firm_risk.json")))
    except Exception: return False, "firm_risk.json unreadable - fail closed"
    if f.get("new_entries_halted") or str(f.get("level")).upper() == "HALT": return False, f"firm rail {f.get('level')} - new entries halted"
    return True, f.get("level")
def equity_aud(B, marks, au):
    v = B["cash_aud"]   # B = one seat ledger
    for p in B["positions"].values():
        if p["status"] == "open":
            px = (marks.get(p["ticker"]) or {}).get("last") or p.get("mark") or p["fill"]
            v += pos_value_aud(p, px, au, marks)   # FX: unrealised pip P&L only (settlement fix 29 Oct); equities unchanged
    return v
STALE_PX_MIN = 15
FILL_MAX_AGE_MIN = {"CRYPTO": 15, "FX": 15, "FUT": 15}   # stalefill: max mark age at fill (min); every other venue STALE_PX_MIN
# venuestale 2026-10-29 (TFO 19:31 WITA): per-venue staleness = known feed delay + 5 min. Delays measured from the live 5m feed 19:33 WITA
# (bar-start age while open: LSE/Euronext/XETRA/BME/SIX/MILAN ~17-20 min, NORDIC ~2 min). Real-time / unclear venues stay at STALE_PX_MIN.
VENUE_DELAY_MIN = {"LSE": 20, "Euronext": 20, "XETRA": 20, "BME": 20, "SIX": 20, "MILAN": 20}
def venue_delay_min(venue): return VENUE_DELAY_MIN.get(venue, 0)
def stale_limit_min(venue): return FILL_MAX_AGE_MIN.get(venue) or ((VENUE_DELAY_MIN[venue] + 5) if venue in VENUE_DELAY_MIN else STALE_PX_MIN)
FILL_MIN_ROOM_R = 1.0   # stalefill: no fill when room to target from the actual fill < 1.0R (TFO 19:21 WITA)
def fallback_quote(sym):
    """Fix 4/7: fallback quote when the live 5m feed gives nothing: last daily close (yfinance 5d/1d). MARK ONLY - never used for fills/stops."""
    try:
        import yfinance as yf
        h = yf.Ticker(sym).history(period="5d", interval="1d")
    except Exception: h = None
    if h is None or not len(h):
        try:   # 2026-10-29: yfinance None -> Yahoo chart v8 daily (MARK ONLY, same rule)
            q = yahoo_v8_quote(sym, "1d", "5d"); return dict(last=q["last"], asof=q["asof"], src="yahoo_chart_v8_daily_fallback") if q else None
        except Exception: return None
    return dict(last=float(h.Close.iloc[-1]), asof=str(h.index[-1]), src="yf_daily_close_fallback")
def px_age_min(m, t):
    try:
        a = dt.datetime.fromisoformat(str(m.get("asof")).replace(" ", "T"))
        if a.tzinfo is None: a = a.replace(tzinfo=UTC)
        return (t - a).total_seconds() / 60
    except Exception: return None
def _price_alert(rec):
    try:
        d = _state_dir(); os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "price_alerts.jsonl"), "a") as fh: fh.write(json.dumps(rec, default=str) + "\n")
    except Exception: pass
def seat_view(B):
    """Fix 3/7: THE one seat equity source (net of costs) for seat_cli, leaderboard and dashboard: the seat fields written by the last tick.
    cash_aud is already net of every entry/exit cost; equity = cash + open positions at last mark."""
    return dict(equity_aud=B.get("equity_aud") if B.get("equity_aud") is not None else B.get("seed_aud"), cash_aud=B.get("cash_aud"), today_pnl_aud=B.get("today_pnl_aud"),
                pnl_since_inception_aud=B.get("pnl_since_inception_aud"), costs_aud=round(sum(p.get("cost_aud", 0) or 0 for p in B.get("positions", {}).values() if p.get("status") == "open") + sum((c.get("gross_aud", 0) or 0) - (c.get("net_aud", 0) or 0) for c in B.get("closed", [])), 2), net_of_costs=True)
def seat_of(e): return str((e.get("tags") or {}).get("strategy", "veteran:?")).split(":", 1)[1].split(" ")[0]
# ---- staged auto-hedge (TFO HT 03:15 Brett hedge staged) ----
HEDGE_BANDS = ((.70, 3), (.50, 2), (.30, 1))   # gross open notional / book equity -> stage
HEDGE_OFF = .15                                # below 15%: fully off; 15-30%: hold current stage
HEDGE_STAGE_PREM, HEDGE_MAX_PREM = .0025, .0075   # premium 0.25% of book equity per stage, max 0.75% total
HEDGE_ENABLED = False   # Brett 2026-10-29 06:41/06:44 WITA: VIX hedge never approved - staged hedge auto-deploy OFF, never re-arms
HEDGE_ARCHIVE = None   # None -> <state dir>/archive/hedge_20261029.json (TFO HT 03:28: overridable, never live under tests)
KILL_ALERT_PATH = None   # None -> <state dir>/KILL_ALERT.json
def _temp_payouts_path():
    """TFO HT 03:35: temp book -> payouts beside the temp book, unless the caller already pointed payouts at a non-live path."""
    from veteran_desk import payouts as PY
    live = os.path.join(BASE, "state", "veteran_desk_payouts.json")
    return PY.DEFAULT_OUT_PATH if os.path.abspath(PY.DEFAULT_OUT_PATH) != os.path.abspath(live) else os.path.join(_state_dir(), "veteran_desk_payouts.json")
def _state_dir():
    """TFO HT 03:28: live state dir only when ticking the real book; otherwise the temp book's own dir (tests never write live paths)."""
    return os.path.join(BASE, "state", "veteran_desk") if os.path.abspath(BOOK) == os.path.abspath(_REAL_BOOK) else os.path.dirname(os.path.abspath(BOOK))
def hedge_stage(gp, cur):
    if gp < HEDGE_OFF: return 0
    for lvl, st in HEDGE_BANDS:
        if gp >= lvl: return st
    return cur   # 15% <= gp < 30%: hold
def gross_open_aud(D, marks, au):
    return sum(abs(p["shares"] * aud(p["ticker"], (marks.get(p["ticker"]) or {}).get("last") or p.get("mark") or p["fill"], au)) for S_ in D["seats"].values() for p in S_["positions"].values() if p.get("status") == "open")
def vix_option_cost():
    """Real exit/entry cost: half bid-ask spread of the ~30d near-ATM VIX call (yfinance ^VIX chain), as a fraction of mid. None if no live two-sided quote."""
    import yfinance as yf
    tk = yf.Ticker("^VIX"); spot = (tk.history(period="1d", interval="5m").Close.iloc[-1])
    ex = [e for e in tk.options if (dt.date.fromisoformat(e) - now().date()).days >= 20][0]
    c = tk.option_chain(ex).calls; r = c.iloc[(c.strike - spot).abs().argsort()[:1]].iloc[0]
    if not (r.bid > 0 and r.ask > r.bid): return None
    mid = (r.bid + r.ask) / 2; return dict(pct=float((r.ask - r.bid) / 2 / mid), contract=str(r.contractSymbol), bid=float(r.bid), ask=float(r.ask), expiry=ex)
def _archive(rec):
    ha = HEDGE_ARCHIVE or os.path.join(_state_dir(), "archive", "hedge_20261029.json"); os.makedirs(os.path.dirname(ha), exist_ok=True)
    A = json.load(open(ha)) if os.path.exists(ha) else dict(label="AUDIT ONLY - not shown on dashboard (TFO HT 03:15 Brett)", trades=[])
    A["trades"].append(rec); tmp = ha + ".tmp"; json.dump(A, open(tmp, "w"), indent=1, default=str); os.replace(tmp, ha)
def _tranche_value(x, vix, t):
    return round(x["premium_aud"] * max(0.0, (vix / x["vix_ref"]) ** 3) * max(0.2, 1 - (t - dt.datetime.fromisoformat(x["opened_utc"])).days / 30), 2)
def hedge_step(D, H, marks, au, tot_eq, t, vix, vm, cost_fn=None):
    cost_fn = cost_fn or vix_option_cost
    gross = gross_open_aud(D, marks, au); gp = gross / tot_eq if tot_eq > 0 else 0.0
    liquid = bool(vix) and session_open("US", t) and not in_open_window("US", t)
    if H.get("premium_aud") and "stage" not in H:   # legacy single overlay -> close at first liquid mark, archive, realise
        if vix: H["value_aud"] = _tranche_value(H, vix, t); H["pnl_aud"] = round(H["value_aud"] - H["premium_aud"], 2)
        co = None
        if liquid:
            try: co = cost_fn()
            except Exception: co = None
        if not co: H["close_pending"] = "legacy hedge close queued - waits for first liquid US-hours VIX mark with a live two-sided option quote (no off-hours fill)"; return H
        cost = round(H["value_aud"] * co["pct"], 2); real = round(H["value_aud"] - cost - H["premium_aud"], 2)
        _archive(dict(H, kind_closed="legacy VIX-call/index-put overlay", closed_utc=t.isoformat(), exit_vix=vix, exit_value_aud=H["value_aud"], exit_cost_aud=cost, exit_cost_basis=co, realised_pnl_aud=real, reason="Brett 03:15 WITA via TFO: close old hedge"))
        D["hedge_realised_aud"] = round((D.get("hedge_realised_aud") or 0.0) + H["value_aud"] - cost - H["premium_aud"], 2)
        H = dict(stage=0, tranches=[], **{k: H[k] for k in ("value_src", "vix_last", "vix_asof", "vix_src", "vix_stale", "vix_stale_why") if k in H})
    H.setdefault("stage", 0); H.setdefault("tranches", [])
    for x in H["tranches"]:
        if vix: x["value_aud"] = _tranche_value(x, vix, t)
    want = hedge_stage(gp, H["stage"]); H.update(gross_open_aud=round(gross, 2), gross_pct=round(gp * 100, 2), target_stage=want)
    if want != H["stage"]:
        if not liquid: H["exec_note"] = "stage %d -> %d queued for first liquid US-hours mark (no off-hours fill)" % (H["stage"], want)
        else:
            try: co = cost_fn()
            except Exception: co = None
            if not co: H["exec_note"] = "stage change queued - no live two-sided VIX option quote"
            else:
                while H["stage"] > want and H["tranches"]:   # step down: sell newest tranche
                    x = H["tranches"].pop(); cost = round(x["value_aud"] * co["pct"], 2)
                    D["hedge_realised_aud"] = round((D.get("hedge_realised_aud") or 0.0) + x["value_aud"] - cost - x["premium_aud"], 2)
                    _archive(dict(x, closed_utc=t.isoformat(), exit_vix=vix, exit_cost_aud=cost, exit_cost_basis=co, realised_pnl_aud=round(x["value_aud"] - cost - x["premium_aud"], 2))); H["stage"] -= 1
                while H["stage"] < want and sum(x["premium_aud"] for x in H["tranches"]) + HEDGE_STAGE_PREM * tot_eq <= HEDGE_MAX_PREM * tot_eq + 0.01:
                    p = round(HEDGE_STAGE_PREM * tot_eq, 2); cost = round(p * co["pct"], 2)
                    x = dict(stage=H["stage"] + 1, premium_aud=p, value_aud=p, entry_cost_aud=cost, entry_cost_basis=co, vix_ref=vix, opened_utc=t.isoformat(), gross_pct_at_open=round(gp * 100, 2))
                    D["hedge_realised_aud"] = round((D.get("hedge_realised_aud") or 0.0) - cost, 2); H["tranches"].append(x); H["stage"] += 1
                    _archive(dict(x, event="bought"))
                H.pop("exec_note", None)
    H["premium_aud"] = round(sum(x["premium_aud"] for x in H["tranches"]), 2); H["value_aud"] = round(sum(x["value_aud"] for x in H["tranches"]), 2)
    H["pnl_aud"] = round(H["value_aud"] - H["premium_aud"], 2)
    H["kind"] = "staged VIX-call hedge (paper model, value ~ premium*(VIX/VIXref)^3, 30-day decay; cost = live half-spread)"
    return H

def hedge_close_all(D, H, t, reason):
    """Brett 06:41 WITA 29 Oct: close every hedge tranche at its current paper mark (last live value_aud) less exit cost = the tranche's
    recorded live half-spread (entry_cost_basis pct); realised P&L booked to hedge_realised_aud, archived audit-only."""
    real_tot = 0.0
    for x in list(H.get("tranches") or []):
        pct = float(((x.get("entry_cost_basis") or {}).get("pct")) or 0.0); cost = round(x["value_aud"] * pct, 2); real = round(x["value_aud"] - cost - x["premium_aud"], 2)
        D["hedge_realised_aud"] = round((D.get("hedge_realised_aud") or 0.0) + x["value_aud"] - cost - x["premium_aud"], 2); real_tot += real
        _archive(dict(x, event="closed", closed_utc=t.isoformat(), exit_value_aud=x["value_aud"], exit_vix=H.get("vix_last"), exit_vix_asof=H.get("vix_asof"), exit_cost_aud=cost,
                      exit_cost_basis=dict(pct=pct, src="recorded live half-spread at entry (no live two-sided quote off-hours)"), realised_pnl_aud=real, reason=reason))
    _archive(dict(event="hedge_disabled", utc=t.isoformat(), reason=reason, realised_this_close_aud=round(real_tot, 2), hedge_realised_total_aud=D.get("hedge_realised_aud"), final_hedge_state=H))
    return dict(disabled=True, stage=0, tranches=[], premium_aud=0.0, value_aud=0.0, pnl_aud=0.0, disabled_utc=t.isoformat(), disabled_reason=reason, closed_realised_aud=round(real_tot, 2))
# ---- VET exit rail v1 (Brett 2026-10-29 12:44 WITA "hardwire profit oversight", TFO approved; PAPER) ----
# Every open position with a fill, every tick, in code: R0 = |fill - initial_stop|; peak_fav_R = high-water favourable excursion (bar hi/lo).
# +1.0R -> stop to breakeven (+ round-trip cost buffer); +1.5R -> trail fill + (peak-1)R0, at least max(0.5R0, ATR14) behind the high-water price,
# floored at BE; half-bank at T1 or +2.0R, whichever first; once peak >= 2R, open R <= 60% of peak -> flatten residual (giveback).
# Hard stop on the bar wins; stops only ever tighten; kill switch unchanged.
RAIL_BE_R, RAIL_TRAIL_R, RAIL_HALF_R, RAIL_GB_PEAK_R, RAIL_GB_KEEP = 1.0, 1.5, 2.0, 2.0, 0.60
# FULL FLAT / HARD BACKSTOP (TFO Veteran Desk 2026-10-30 15:31 WITA; PAPER). vs deploy_basis = entry notional A$; P&L = net A$ after entry + exit costs, at last.
FULL_FLAT_PCT = None      # exitC 2026-10-30: disabled (was 0.05; code path kept) - net >= +5% of deploy_basis -> close the whole remaining position (full_flat_5pct)
FULL_FLAT_R = 3.0         # exitC 2026-10-30: full flat at net 3R - optional: when set, full flat instead at net R (original risk) >= FULL_FLAT_R (full_flat_<R>R)
RAIL_T2_R = 2.0           # exitC: after half-bank, bank another 1/4 of ORIGINAL size at net R >= 2 (t2_done); final 1/4 trails
SHADOW_UNTIL_UTC = "2026-11-29T07:50:00+00:00"   # exitC shadow log: 30 days
HARD_BACKSTOP_PCT = 0.05  # net <= -5% of deploy_basis -> close all (hard_backstop_5pct); protective only, never widens the stop
# bank1pct SUPERSEDED by FINAL 17:20 WITA rail below (1R original risk net; +1% leg removed).
# (A$, after entry cost) >= the trade's A$ risk at entry (1R dollars) OR >= +1% of deploy_basis (entry notional A$). Residual stop -> BE, then existing trail.
# Grade B/C keep prior rails (T1 / +2R only). Capped sleeves opt out with bank_capped=True. Full flat +5% stays (prompt law; R giveback unchanged).
RAIL_BANK_GRADES = ("A", "A+")
def pos_grade(p):
    """A/A+/B/C from position.grade, else parse tags.strategy '... grade A ...', else funder_queue tags (legacy opens)."""
    g = p.get("grade")
    if g in ("A+", "A", "B", "C"): return g
    tags = p.get("tags") or {}; s = str(tags.get("strategy") or "")
    for cand in ("A+", "A", "B", "C"):
        if ("grade " + cand) in s or ("grade_" + cand) in s: return cand
    pid = p.get("id")
    if not pid: return None
    try:
        import funder_gate as _FG; q = json.load(open(_FG.QUEUE))
        e = (q.get("entries") or {}).get(pid) or {}
        s = str((e.get("tags") or {}).get("strategy") or "")
        g2 = e.get("grade")
        if g2 in ("A+", "A", "B", "C"): return g2
        for cand in ("A+", "A", "B", "C"):
            if ("grade " + cand) in s or ("grade_" + cand) in s: return cand
    except Exception: pass
    return None
def open_net_aud(p, px, au, marks=None):
    """(net_aud, basis_aud) at px: gross unrealised minus recorded entry cost; basis = entry notional in A$."""
    sg = 1 if p["side"] == "LONG" else -1; f = float(p["fill"]); q = _qty(p)
    if is_fx(p): qa = quote_to_aud(p["ticker"], au, marks); gross = sg * (px - f) * q / qa; basis = f * q / qa
    else: gross = sg * (aud(p["ticker"], px, au) - aud(p["ticker"], f, au)) * q; basis = aud(p["ticker"], f, au) * q
    return gross - float(p.get("cost_aud") or 0), basis
# FINAL (Brett HARD via TFO 2026-10-29 17:20 WITA; supersedes 17:01-17:13 bank1pct): +1% basis leg DROPPED.
# Half-bank when open NET A$ (after entry + exit costs) >= 1R of the ORIGINAL entry risk (|fill - initial_stop| x qty in A$).
# Grade read ONLY from the persisted position field `grade` (A/A+); missing grade -> not eligible. Dex (seat 'trend') banks at 1.5R.
RAIL_BANK_R = 1.0; RAIL_BANK_R_SEAT = {"trend": 1.5}
KRAKEN_MAKER, KRAKEN_TAKER = 0.0025, 0.0040   # Kai crypto (TFO Crypto 17:19): per leg; entry maker, exit taker
def _is_crypto(p): return (p.get("venue") or P.venue_of(p["ticker"])) == "CRYPTO"
CRYPTO_DP = 8   # tfo_review_20261030 (HT ruling): Kraken spot sizes crypto in fractional units (8 dp, floored); replaces whole-unit rounding + 0.98 floortol
def crypto_units(x): return math.floor(float(x) * 10 ** CRYPTO_DP + 1e-6) / 10 ** CRYPTO_DP if x and x > 0 else 0.0
def _qty(p): v = p.get("shares") or 0; return crypto_units(float(v)) if _is_crypto(p) else int(v)
def _half(p, q=None): q = _qty(p) if q is None else q; return crypto_units(q / 2.0) if _is_crypto(p) else int(q) // 2
def _can_half(p): q = _qty(p); return (q > 0 and _half(p) > 0 and q - _half(p) > 0) if _is_crypto(p) else q >= 2
def _notional_aud(p, px, au, marks=None):
    return (px * _qty(p) / quote_to_aud(p["ticker"], au, marks)) if is_fx(p) else aud(p["ticker"], px, au) * _qty(p)
def net_open_aud(p, px, au, marks=None):
    """Open net A$ at px after entry AND exit costs. Crypto: Kraken 0.25% maker entry + 0.40% taker exit on notional."""
    sg = 1 if p["side"] == "LONG" else -1; f = float(p["fill"]); q = _qty(p)
    if is_fx(p): gross = sg * (px - f) * q / quote_to_aud(p["ticker"], au, marks)
    else: gross = sg * (aud(p["ticker"], px, au) - aud(p["ticker"], f, au)) * q
    ent = leg_cost_aud(p, f, q, au, marks, "entry") if _is_crypto(p) else float(p.get("cost_aud") or 0)   # crypto: Kraken maker entry modelled (legacy crypto cost_aud ignored, as before)
    return gross - ent - leg_cost_aud(p, px, q, au, marks, "exit") - float(p.get("borrow_accrued_aud") or 0)   # tfo_costmodel_20261030
def risk0_aud(p, au, marks=None):
    """1R in A$ from the ORIGINAL initial stop (not the trailed stop)."""
    f = float(p["fill"]); s0 = _initial_stop(p); q = _qty(p)
    if is_fx(p): return abs(f - s0) * q / quote_to_aud(p["ticker"], au, marks)
    return abs(aud(p["ticker"], f, au) - aud(p["ticker"], s0, au)) * q
def net_R(p, px, au, marks=None):
    try: r = risk0_aud(p, au, marks); return net_open_aud(p, px, au, marks) / r if r > 0 else 0.0
    except Exception: return 0.0
def full_flat_trigger(p, px, au, marks=None):
    """'full_flat_5pct' | 'full_flat_<R>R' | 'hard_backstop_5pct' | None at px (net A$ after costs vs deploy_basis). Hold (None) on failure."""
    try:
        if px is None or _qty(p) <= 0: return None
        basis = _notional_aud(p, float(p["fill"]), au, marks); n = net_open_aud(p, float(px), au, marks)
        if not basis or basis <= 0: return None
        if HARD_BACKSTOP_PCT is not None and n <= -HARD_BACKSTOP_PCT * basis: return "hard_backstop_5pct"
        if FULL_FLAT_R is not None:
            r = risk0_aud(p, au, marks)
            if r > 0 and n / r >= FULL_FLAT_R: return f"full_flat_{FULL_FLAT_R:g}R"
        elif FULL_FLAT_PCT is not None and n >= FULL_FLAT_PCT * basis: return "full_flat_5pct"
    except Exception: return None
    return None
def orig_shares(p):
    """Original size: stored at the half-bank; legacy fallback = 2x residual after t1, else current shares."""
    v = p.get("shares_orig") or (_qty(p) * 2 if p.get("t1_done") else _qty(p)); return crypto_units(float(v)) if _is_crypto(p) else int(v)
def t2_trigger(p, px, au, marks=None):
    """'rail_2R_quarter_out' | None: after t1_done, before t2_done, net R >= RAIL_T2_R, leaves >= 1 share."""
    if not p.get("t1_done") or p.get("t2_done") or p.get("bank_capped") or px is None: return None
    q = crypto_units(orig_shares(p) / 4.0) if _is_crypto(p) else orig_shares(p) // 4
    if q <= 0 or _qty(p) - q <= 0 or (not _is_crypto(p) and (q < 1 or _qty(p) - q < 1)): return None
    return "rail_2R_quarter_out" if net_R(p, float(px), au, marks) >= RAIL_T2_R else None
def shadow_old5(p, px, au, marks=None):
    """Record (once) when the old +5%-of-deploy_basis full flat would have fired."""
    try:
        if p.get("shadow_old5_px") is None and net_open_aud(p, float(px), au, marks) >= 0.05 * _notional_aud(p, float(p["fill"]), au, marks): return True
    except Exception: pass
    return False
def shadow_write(seat, p, t):
    """Append one row per closed trade (30 days): C exit vs old +5% flat. Never raises."""
    try:
        if t.isoformat() > SHADOW_UNTIL_UTC: return
        sg = 1 if p["side"] == "LONG" else -1; o5 = p.get("shadow_old5_px")
        row = dict(id="tfo_exitC_20261030", utc=t.isoformat(), seat=seat, pos=p.get("id"), ticker=p["ticker"], side=p["side"], fill=p["fill"], c_exit=p.get("exit"), c_reason=p.get("exit_reason"),
                   c_net_aud_residual=p.get("net_aud"), t1_done=bool(p.get("t1_done")), t2_done=bool(p.get("t2_done")), old5_px=o5, old5_utc=p.get("shadow_old5_utc"),
                   old5_vs_c_px=(None if o5 is None or p.get("exit") is None else round(sg * (float(o5) - float(p["exit"])), 6)))
        with open(os.path.join(_state_dir(), "exitC_shadow.jsonl"), "a") as fh: fh.write(json.dumps(row) + "\n")
    except Exception: pass
# REVERSAL GUARD G3 - ALERT ONLY (tfo_revguard_20261030; spec /workspace/vd/REVERSAL_GUARD_SPEC.md on the box). PAPER.
# Arm at peak NET open R >= 2.0 (net of round-trip costs, original risk; peak from BEFORE this tick). Fire once when current net R <=
# peak - min(0.20*peak, 0.60). Places NO orders, changes NO stops/sizes; Option C exits untouched. Logs + shadow P&L of G3 action only.
REVG_ARM_R, REVG_GB_FRAC, REVG_OFF_R, REVG_BANK_FRAC, REVG_LOCK_R = 2.0, 0.20, 0.60, 0.50, 1.25
def revguard_check(p, px, au, marks=None):
    """Returns alert dict (once) or None. Only mutates p's revguard_* bookkeeping fields. Never raises."""
    try:
        if px is None or p.get("revguard_fired") or int(p.get("shares") or 0) < 1: return None
        cur = net_R(p, float(px), au, marks); pk = float(p.get("revguard_peak_net_R") or 0.0)
        out = None
        if pk >= REVG_ARM_R:
            frac_lvl, off_lvl = pk * (1 - REVG_GB_FRAC), pk - REVG_OFF_R
            if cur <= max(frac_lvl, off_lvl):
                why = "gave_back_20pct_of_peak" if frac_lvl >= off_lvl else "fell_0.60R_off_peak"
                q = _qty(p); h = int(q * REVG_BANK_FRAC); rest = q - h; r_sh = risk0_aud(p, au, marks) / q
                bank = net_open_aud(p, float(px), au, marks) * h / q; lock = REVG_LOCK_R * r_sh * rest
                out = dict(peak_R=round(pk, 4), current_R=round(cur, 4), trigger=why, px=float(px), shares=q,
                           shadow_bank_shares=h, shadow_bank_net_aud=round(bank, 2), shadow_lock_shares=rest, shadow_lock_floor_net_aud=round(lock, 2), shadow_total_net_aud=round(bank + lock, 2))
                p["revguard_fired"] = True
        if cur > pk: p["revguard_peak_net_R"] = round(cur, 4)
        return out
    except Exception: return None
def revguard_emit(seat, k, p, t, a):
    """Append jsonl row + refresh unacknowledged alerts file (atomic). Never raises."""
    try:
        w = t.astimezone(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S WITA")
        row = dict(id="tfo_revguard_20261030", time_wita=w, utc=t.isoformat(), trade_id=k, seat=seat, ticker=p.get("ticker"), side=p.get("side"), **a)
        sd = _state_dir(); open(os.path.join(sd, "reversal_guard.jsonl"), "a").write(json.dumps(row) + "\n")
        ap = os.path.join(sd, "reversal_guard_alerts.json")
        try: A = json.load(open(ap))
        except Exception: A = {}
        al = [x for x in (A.get("alerts") or []) if not x.get("acked")] + [dict(row, acked=False)]
        tmp = ap + ".tmp"; json.dump(dict(updated_wita=w, note="unacknowledged reversal-guard fires; heartbeat sets acked=true to clear", alerts=al), open(tmp, "w"), indent=1); os.replace(tmp, ap)
        p["revguard_fired_utc"] = t.isoformat(); _rlog(p, t, "revguard_alert", **{x: a[x] for x in ("peak_R", "current_R", "trigger")})
    except Exception: pass
# REVGUARD WEBHOOK (tfo_revguard_webhook_20261030): POST once per NEW G3 fire. Secrets read at fire time, never logged/printed.
REVG_SECRETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "secrets")
def _revg_secret(name):
    try:
        with open(os.path.join(REVG_SECRETS_DIR, name)) as fh: return fh.read().strip()
    except Exception: return ""
def revguard_webhook(seat, k, p, t, a, payload=None):
    """Synchronous, timeout=3s, never raises. Returns HTTP status (int) on success, False on skip/fail. payload overrides body (wiring test). Logs only fixed strings + exception class name."""
    try:
        if os.environ.get("VD_REVGUARD_WEBHOOK_DISABLE") == "1": print("revguard webhook skipped: disabled"); return False
        url, key = _revg_secret("vd_revguard_url"), _revg_secret("vd_revguard_key")
        if not url or not key: print("revguard webhook skipped: not configured"); return False
        import urllib.request
        body = (json.dumps(payload) if payload is not None else json.dumps(dict(trade_id=k, ticker=p.get("ticker"), side=p.get("side"), seat=seat, peak_r=a.get("peak_R"),
                               current_r=a.get("current_R"), trigger=a.get("trigger"), ts=t.isoformat()))).encode()
        req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
        r = urllib.request.urlopen(req, timeout=3)
        try: st = int(getattr(r, "status", None) or r.getcode())
        finally: r.close()
        return st
    except Exception as e:
        try: print("revguard webhook post failed: " + type(e).__name__)
        except Exception: pass
        return False
# US BETA CAP (tfo_usbeta_cap_20261030; PAPER): refuse a NEW fill that takes VD net US equity beta beyond US_BETA_CAP_USD.
# US-listed equity (venue US, not FX/crypto/futures) notional USD at mark: long +, short -, levered/inverse ETFs x multiple. Exits never blocked.
# tfo_betacap_dyn_20261030 (Brett 19:22 WITA 30 Oct 2026): cap = 40% of THIS book's equity (A$) x live Yahoo AUDUSD; replaces fixed US$750k. THF uses its own book (THF_DESK=1 -> thf_book.json).
US_BETA_CAP_PCT = 0.60   # tfo_cap60_20261030 (Brett 20:06 WITA): 60% (40% -> 50% 19:58 -> 60% 20:06)
def book_equity_aud(D):
    v = (D or {}).get("equity_aud")
    if not v: v = sum(float(B.get("equity_aud") or B.get("cash_aud") or 0) for B in ((D or {}).get("seats") or {}).values())
    return float(v or SEED_AUD)
THF_US_BETA_CAP_USD = 750000.0   # tfo_thffresh_20261030 (GPT review HIGH): THF rail = fixed US$750k (Brett), stricter of fixed and 40% rule
def us_beta_cap_usd(D, au=None):
    c = US_BETA_CAP_PCT * book_equity_aud(D) * float(au or audusd())
    return c   # tfo_review_20261030 (Brett): THF also 40% of own equity - US$750k min removed (THF_US_BETA_CAP_USD kept for reference only)
LEVERED_ETF_MULT = {"TQQQ": 3, "SQQQ": -3, "UPRO": 3, "SPXU": -3, "SPXL": 3, "SPXS": -3, "SSO": 2, "SDS": -2, "QLD": 2, "QID": -2, "SOXL": 3, "SOXS": -3,
                    "TNA": 3, "TZA": -3, "UDOW": 3, "SDOW": -3, "TECL": 3, "TECS": -3, "FAS": 3, "FAZ": -3, "LABU": 3, "LABD": -3, "SH": -1, "PSQ": -1, "DOG": -1}
def _us_beta_leg(p, px):
    if (p.get("venue") or P.venue_of(p["ticker"])) != "US" or is_fx(p) or _is_crypto(p): return 0.0
    return (1 if p["side"] == "LONG" else -1) * float(px) * int(p.get("shares") or 0) * LEVERED_ETF_MULT.get(p["ticker"], 1)
def us_beta_usd(D, marks=None, exclude=()):
    """Net US equity beta (USD) over all VD seats' OPEN positions at mark (fallback fill)."""
    tot = 0.0
    for B in D["seats"].values():
        for k, q in B["positions"].items():
            if q.get("status") != "open" or k in exclude or q.get("ticker") in exclude: continue
            px = ((marks or {}).get(q["ticker"]) or {}).get("last") or q.get("mark") or q.get("fill")
            if px: tot += _us_beta_leg(q, px)
    return tot
def us_beta_block(D, p, f, marks=None, au=None):
    """None if the new fill is allowed, else a reason string. Only blocks moves that increase |beta| beyond the cap."""
    try:
        cur = us_beta_usd(D, marks); new = cur + _us_beta_leg(p, f); cap = us_beta_cap_usd(D, au)
        if abs(new) > cap and abs(new) > abs(cur): return "us_beta_cap: net US beta US$%.0f -> US$%.0f > cap US$%.0f (%.0f%% of book equity)" % (cur, new, cap, US_BETA_CAP_PCT * 100)
    except Exception: return None
    return None
def bank_trigger(p, px, au, marks=None, seat=None):
    """'rail_1R_aud_scale_out' (or 'rail_1_5R_aud_scale_out' for trend) | None. Persisted grade A/A+ only."""
    if p.get("bank_capped") or (p.get("grade") or pos_grade(p)) not in RAIL_BANK_GRADES: return None   # tfo_review_20261030: legacy opens without the grade field banked only at T1/2R - now read tags/queue grade
    k = RAIL_BANK_R_SEAT.get(seat or p.get("seat"), RAIL_BANK_R)
    if net_R(p, px, au, marks) >= k: return "rail_1R_aud_scale_out" if k == 1.0 else "rail_1_5R_aud_scale_out"
    return None
# GIVEBACK PROTECT (Brett HARD via TFO 2026-10-29 17:17/17:20 WITA; DNA for every seat, all grades; bank_capped opts out).
# Only while open net > 0 and before the half-bank. Fires half-bank at last, residual stop -> fee floor, when ANY of:
#  a) peak open net R >= RAIL_GB_ARM_R and current net R <= (1 - RAIL_GB_FRAC) x peak            -> rail_giveback_protect
#  b) last CLOSED bar closes through the prior bar's low (long) / high (short), or through p['pre_event_level'],
#     with volume >= RAIL_GB_VOL_X x avg of the prior RAIL_GB_VOL_N bars                              -> rail_giveback_volturn
#     generic: daily bars, N=20; crypto (Kai): Kraken 1h bars, N=24 (24h avg)
#  c) news logged for the position after fill and after deploy                                         -> rail_giveback_news
#     generic: state/veteran_desk/news_alerts.jsonl ticker match, or p['news_logged_utc'] set by a seat;
#     crypto: news.json top5 item whose assets/markets list KRAKEN:<pair> or CRYPTO.
RAIL_GB_ARM_R, RAIL_GB_FRAC = 0.5, 0.5
RAIL_GB_VOL_X = 2.0; RAIL_GB_VOL_N = 20; RAIL_GB_VOL_N_CRYPTO = 24
RAIL_GB_NEWS_SINCE = "2026-10-29T09:20:00+00:00"   # deploy time: older headlines never trigger
def _bars_default(sym, crypto):
    """Closed bars oldest->newest as dicts(o,h,l,c,v). Crypto: Kraken public OHLC 1h. Else: yfinance daily. None on failure."""
    try:
        if crypto:
            import urllib.request
            pair = sym.split(":", 1)[1]
            J = json.loads(urllib.request.urlopen(f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=60", timeout=8).read())
            rows = [v for k, v in (J.get("result") or {}).items() if k != "last"][0][:-1]   # drop the forming bar
            return [dict(o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4]), v=float(r[6])) for r in rows[-40:]]
        import yfinance as yf
        h = yf.Ticker(sym).history(period="3mo", interval="1d")
        if h is None or len(h) < 22: return None
        h = h.iloc[:-1]
        return [dict(o=float(r.Open), h=float(r.High), l=float(r.Low), c=float(r.Close), v=float(r.Volume)) for r in h.tail(40).itertuples()]
    except Exception: return None
BARS_FN = _bars_default
def _news_default(p):
    """Latest qualifying news utc (str) for p, else None."""
    since = max(str(p.get("filled_utc") or ""), RAIL_GB_NEWS_SINCE); hit = None
    if p.get("news_logged_utc") and str(p["news_logged_utc"]) >= since: hit = str(p["news_logged_utc"])
    try:
        if _is_crypto(p):
            N = json.load(open(os.path.join(BASE, "state", "veteran_desk", "news.json")))
            for h in N.get("top5") or []:
                tags = set(h.get("assets") or []) | set(h.get("markets") or [])
                u = str(h.get("published_utc") or "")
                if (p["ticker"] in tags or "CRYPTO" in tags) and u >= since: hit = max(hit or "", u)
        else:
            for ln in open(os.path.join(BASE, "state", "veteran_desk", "news_alerts.jsonl")).readlines()[-400:]:
                try: a = json.loads(ln)
                except Exception: continue
                if a.get("ticker") == p["ticker"] and str(a.get("utc") or "") >= since: hit = max(hit or "", str(a["utc"]))
    except Exception: pass
    return hit
NEWS_FN = _news_default
def volturn(p, bars):
    """True when the last closed bar closes through prior bar low/high (or pre_event_level) against p on >= 2x avg volume."""
    crypto = _is_crypto(p); n = RAIL_GB_VOL_N_CRYPTO if crypto else RAIL_GB_VOL_N
    if not bars or len(bars) < n + 2: return False
    last, prev = bars[-1], bars[-2]; avg = sum(b["v"] for b in bars[-n - 1:-1]) / n
    if avg <= 0 or last["v"] < RAIL_GB_VOL_X * avg: return False
    L = p["side"] == "LONG"; lvl = p.get("pre_event_level")
    thru = last["c"] < prev["l"] if L else last["c"] > prev["h"]
    if lvl is not None: thru = thru or (last["c"] < float(lvl) if L else last["c"] > float(lvl))
    return bool(thru)
def giveback_trigger(p, px, au, marks=None):
    """Reason code or None. Only for open-profit trades not yet half-banked."""
    if p.get("bank_capped") or p.get("t1_done") or not p.get("r0") or not _can_half(p): return None
    cur = net_R(p, px, au, marks)
    pk = max(float(p.get("gb_peak_net_R") or 0.0), cur); p["gb_peak_net_R"] = round(pk, 4)
    if cur <= 0: return None   # flat / losing -> stop logic owns it
    if pk >= RAIL_GB_ARM_R and cur <= (1 - RAIL_GB_FRAC) * pk: return "rail_giveback_protect"
    try:
        if volturn(p, BARS_FN(p["ticker"], _is_crypto(p))): return "rail_giveback_volturn"
    except Exception: pass
    try:
        u = NEWS_FN(p)
        if u and u != p.get("gb_news_used"): p["gb_news_used"] = u; return "rail_giveback_news"
    except Exception: pass
    return None
# TIME STOP (Brett via TFO - HEAD TRADER, 2026-10-29 17:44 WITA: 'Yes go ahead'). PAPER only.
# Exit an open trade that has NOT reached +0.5R by bar N from entry. R = net A$ after costs / ORIGINAL entry risk (initial stop).
# 'Reached' = peak since entry (ts_peak_net_R: max net R at last mark and at the rail high-water price), so a trade that once touched +0.5R is spared.
# N fixed per seat at entry, stored on the position (time_stop_*), never extended. Missing mark / failed bar fetch -> hold.
# exit_reason rail_time_stop + counterfactual row in early_bank_audit.jsonl vs the plain stop. Halfway stop at +0.5R REJECTED (not implemented).
RULE_LOG = [dict(utc="2026-10-29T09:44:00+00:00", wita="17:44 WITA 29 Oct 2026", rule="rail_time_stop", approved_by="Brett",
                 words="Yes go ahead", relayed_by="TFO - HEAD TRADER",
                 text="TIME STOP: exit if not reached +0.5R net A$ (peak since entry, original risk) by bar N from entry; N per seat fixed at entry, never extended; halfway stop at +0.5R REJECTED.")]
RAIL_TS_R = 0.5
TIME_STOP_N = {"crypto": (24, "1h"), "news_filings": (3, "1d"), "healthcare": (7, "1d"), "tech_ai_infra": (7, "1d"), "banks": (7, "1d"),
               "essentials_utilities": (7, "1d"), "energy": (7, "1d"), "macro": (10, "1d"), "fx_commodities": (10, "1d"), "trend": (15, "1d")}
TIME_STOP_DEFAULT = (7, "1d"); TIME_STOP_TREND_ABOVE_20DMA = 20
def _dma20_above_default(sym, entry_utc, px):
    """True/False: px above the 20DMA of the daily closes BEFORE entry; None when bars are unavailable."""
    try:
        import yfinance as yf
        h = yf.Ticker(sym).history(period="3mo", interval="1d")
        cl = [float(r.Close) for r in h.itertuples() if r.Index.date() < entry_utc.date()]
        if len(cl) < 20: return None
        return float(px) > sum(cl[-20:]) / 20
    except Exception: return None
DMA20_FN = _dma20_above_default
def _add_weekdays(d, n):
    while n > 0:
        d += dt.timedelta(days=1)
        if d.weekday() < 5: n -= 1
    return d
def _parse_dt(s):
    try:
        x = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return x if x.tzinfo else x.replace(tzinfo=UTC)
    except Exception:
        try: return dt.datetime.combine(dt.date.fromisoformat(str(s)[:10]), dt.time(23, 59, 59), UTC)
        except Exception: return None
RULE_LOG.append(dict(id="tfo_fullflat_20261030_1531", utc="2026-10-30T07:31:00+00:00", wita="15:31 WITA 30 Oct 2026", by="TFO Veteran Desk (Marcus Hale)", rule="full_flat_5pct / hard_backstop_5pct",
                     text="Every open position, every tick, after the hard-stop check: net A$ (after entry+exit costs) >= +5% of deploy_basis -> close all (full_flat_5pct; FULL_FLAT_R overrides when set); <= -5% -> close all (hard_backstop_5pct). Backstop is protective only: whichever of it or the initial stop is hit first; stops never widened."))
RULE_LOG.append(dict(id="tfo_exitC_20261030", utc="2026-10-30T07:31:00+00:00", wita="15:31 WITA 30 Oct 2026", by="TFO (Brett delegated pick)", rule="exit Option C", report="research/VD_EXIT_OPTIONS_20261030.md",
                     text="Half-bank at net 1R (1.5R trend) -> residual BE+fees; then 1/4 of ORIGINAL size at net 2R (t2_done); final 1/4 trails ATR/structure; full flat at net 3R (FULL_FLAT_R=3.0, FULL_FLAT_PCT disabled). -5% backstop, giveback and time stops unchanged. R net of round-trip costs on original entry risk. 30-day shadow log state/veteran_desk/exitC_shadow.jsonl vs old +5% flat."))
RULE_LOG.append(dict(id="tfo_revguard_20261030", utc="2026-10-30T08:19:00+00:00", wita="16:19 WITA 30 Oct 2026", by="TFO Veteran Desk (Marcus Hale)", rule="reversal guard G3 alert-only", spec="/workspace/vd/REVERSAL_GUARD_SPEC.md (box)",
                     text="Arm at peak net open R >= 2.0; fire once (revguard_fired) when net R <= peak - min(20% of peak, 0.60R). ALERT ONLY: no orders, no stop/size change, Option C untouched. Row to state/veteran_desk/reversal_guard.jsonl with shadow P&L (bank 50% of remaining + lock +1.25R on rest, net A$); unacked list in state/veteran_desk/reversal_guard_alerts.json."))
RULE_LOG.append(dict(id="tfo_usbeta_cap_20261030", utc="2026-10-30T08:35:00+00:00", wita="16:35 WITA 30 Oct 2026", by="TFO Veteran Desk (Marcus Hale)", rule="US beta cap",
                     text="Refuse any NEW fill that takes VD net US equity beta beyond the US beta cap (SUPERSEDED 30 Oct 19:22 WITA by tfo_betacap_dyn_20261030: 40% of book equity x live AUDUSD; was fixed US$750k) (US-listed equity notional USD at mark; long +, short -, levered/inverse ETFs at multiple). Arm stays armed, reason us_beta_cap logged. Exits and rebooks of existing fills never blocked."))
RULE_LOG.append(dict(id="tfo_betacap_dyn_20261030", utc="2026-10-30T11:22:00+00:00", wita="19:22 WITA 30 Oct 2026", by="Brett (approved via TFO)", rule="US beta cap dynamic",
                     text="US beta cap = 40% of current book equity (A$, each book its own: VD veteran_desk_book.json, THF thf_book.json) x live Yahoo AUDUSD. Replaces fixed US$750k. Enforced at arm time in gate_v2 (open + armed + staged queue + this run) and at fill time in book.py."))
RULE_LOG.append(dict(id="tfo_costmodel_20261030", utc="2026-10-30T08:45:00+00:00", wita="16:45 WITA 30 Oct 2026", by="TFO Veteran Desk (Marcus Hale)", rule="per-venue cost model",
                     text="leg_cost_aud replaces flat COST_RT on new fills and on FUTURE exit legs of open positions (config tables CM_*): IBKR-style commissions (US $0.0035/sh min $0.35 + clearing; UK/EU/CH 0.05%; ASX 0.08% min A$5; JP/HK 0.05%), ADV half-spread buckets, taxes (UK stamp 0.5% buys + PTM GBP1.50 >GBP10k; FR 0.4%/ES 0.2%/IT 0.1% buys; HK 0.1% both; KR/TW sells; US SEC $20.60/$1M sells), FX conversion 3bp non-AUD, Kraken 0.25% maker in / 0.40% taker out + spread, futures per whole contract (GC $0.85+exch, MGC $0.25+exch), daily borrow on all shorts. Booked entry costs of open positions and closed trades NOT changed.",
                     closed_restatement=dict(trades=7, old_cost_aud_minus_new="see delta", delta_new_minus_old_aud=557.29, note="restatement only, ledgers unchanged"), cost_rt_legacy="SUPERSEDED by v2: pre-trade estimates in book/desk/gate_v2 now CT.est_rt_rate (one source veteran_desk/cost_tables.py)"))
RULE_LOG.append(dict(id="tfo_costmodel_20261030_v2", utc="2026-10-30T09:00:00+00:00", wita="17:00 WITA 30 Oct 2026", by="TFO rulings via Marcus Hale", rule="cost model v2 (redeploy after 16:44 rollback)",
                     text="(2) Market closed or mark stale (px_age_min > venue stale limit) -> giveback, full-flat (3R) and reversal-guard triggers HELD (p.trigger_held); hard stop and -5% hard backstop still act. One-time cutover re-baseline: gb/ts/revguard peak net R shifted by (legacy - new) exit cost at rail_hwp / 1R (p.cm_rebaseline). (3) ADV refreshed on its own daily key (adv_day) - it sat inside the ATR block, which was skipped because ATR was already cached today, so the tick priced TCL.AX at the no-ADV default 5bp (A$339.2) while the preview fetched ADV (2bp, A$260.9). (4) One cost source: veteran_desk/cost_tables.py used by book.py, desk.py, gate_v2.py; COST_RT now derived. (5) No-ADV spread = liquidity tier (US ETF 1.5bp, US 3bp, AU/UK/EU/CH/JP/CA 4bp, HK 5bp, SG/KR/TW/IN 6bp, NZ/CN 8bp, default 8bp)."))
RULE_LOG.append(dict(id="tfo_costmodel_tcl_note_20261030", utc="2026-10-30T08:44:04+00:00", wita="16:44 WITA 30 Oct 2026", by="TFO ruling 1", rule="TCL.AX half-bank stands",
                     text="VET-TCLAX-20261029-015705-essentials_utilities half-bank (9,433 sh @ 13.83, net A$1,682.85, reason rail_giveback_protect, stop -> BE 13.6554) was triggered by the cost-model cutover on a stale mark (ASX closed; filled at the last real close 13.83). TFO ruling: the bank STANDS; fill not edited."))
def time_stop_setup(p, seat, t):
    """Fix N at entry (from filled_utc) once; stored on p. Returns the deadline (datetime) or None if no fill time."""
    fu = _parse_dt(p.get("filled_utc"))
    if fu is None: return None
    if p.get("time_stop_entry_utc") == p.get("filled_utc") and p.get("time_stop_deadline_utc"): return _parse_dt(p["time_stop_deadline_utc"])
    seat = seat or p.get("seat"); n, unit = TIME_STOP_N.get(seat, TIME_STOP_DEFAULT); note = None
    if seat not in TIME_STOP_N: note = "default 7 daily bars (seat not in Brett's list)"
    if unit == "1h": dl = fu + dt.timedelta(hours=n)
    else: dl = dt.datetime.combine(_add_weekdays(fu.date(), n), dt.time(23, 59, 59), UTC)
    if seat == "trend":
        try: ab = DMA20_FN(p["ticker"], fu, p["fill"])
        except Exception: ab = None
        if ab: n = TIME_STOP_TREND_ABOVE_20DMA; dl = dt.datetime.combine(_add_weekdays(fu.date(), n), dt.time(23, 59, 59), UTC); note = "price above 20DMA at entry -> 20 bars"
        else: note = "20DMA check failed -> 15 bars" if ab is None else "price not above 20DMA at entry -> 15 bars"
    elif seat == "news_filings":
        ew = _parse_dt(p.get("event_window_end_utc") or p.get("event_window_end")) if (p.get("event_window_end_utc") or p.get("event_window_end")) else None
        if ew and ew < dl: dl = ew; note = "event window end"
    elif seat == "healthcare":
        rd = p.get("readout_date") or p.get("catalyst_date") or (p.get("tags") or {}).get("catalyst_date")
        rdt = _parse_dt(rd) if rd else None
        if rdt: dl = dt.datetime.combine(rdt.date() + dt.timedelta(days=2), dt.time(23, 59, 59), UTC); n = None; unit = "readout+2d"; note = f"readout {rdt.date()} + 2 days"
        else: note = "FALLBACK: no catalyst/readout date stored -> 7 daily bars"
    p.update(time_stop_n=n, time_stop_unit=unit, time_stop_deadline_utc=dl.isoformat(), time_stop_entry_utc=p.get("filled_utc"), time_stop_note=note, time_stop_set_utc=t.isoformat())
    _rlog(p, t, "time_stop_set", n=n, unit=unit, deadline=dl.isoformat(), note=note); return dl
def time_stop_trigger(p, m, t, au, marks=None, seat=None):
    """'rail_time_stop' | None. Hold (None) on any missing mark or failure."""
    try:
        if not m or m.get("last") is None: return None
        dl = time_stop_setup(p, seat, t)
        if dl is None or not (p.get("r0") or 0): return None
        cand = [net_R(p, float(m["last"]), au, marks)]
        if p.get("rail_hwp") is not None: cand.append(net_R(p, float(p["rail_hwp"]), au, marks))
        pk = max([float(p.get("ts_peak_net_R") or -1e9)] + cand); p["ts_peak_net_R"] = round(pk, 4)
        if t >= dl and pk < RAIL_TS_R: return "rail_time_stop"
    except Exception: return None
    return None
# T1 SYNC (TFO ruling 19:11 WITA 29 Oct 2026: manual half-bank must set t1_done). Same post-half state as the rail's own half-bank.
RULE_LOG.append(dict(utc="2026-10-29T11:11:00+00:00", wita="19:11 WITA 29 Oct 2026", rule="t1sync", approved_by="TFO ruling",
                     text="Manual half-bank / seat_cli partial close must set t1_done (and the rail's half-bank state: original R kept, residual stop -> fee floor/BE tighten-only, target -> T2, rail_state t1_done) so the rail never half-banks the residual again."))
def mark_half_banked(p, t, x, h, why):
    """Apply the rail's post-half-bank state to p (residual). Idempotent; stops only tighten; initial_stop/r0 preserved."""
    L = p["side"] == "LONG"; f = float(p["fill"]); s0 = _initial_stop(p)
    p.setdefault("initial_stop", s0); p["r0"] = p.get("r0") or abs(f - s0)
    if p.get("t1_done"):
        if not p.get("t2_done"): p.update(t2_done=True, t2_utc=t.isoformat(), rail_state="t2_done"); _rlog(p, t, "quarter_bank", px=x, shares=h, reason=why)   # exitC: 2nd partial
        return p
    p.setdefault("shares_orig", int(p["shares"]) + int(h))
    be = f + (1 if L else -1) * f * rt_rate(p)
    if (L and p["stop"] < be) or (not L and p["stop"] > be): _rlog(p, t, "stop_tighten", old=p["stop"], new=round(be, 6), why="manual half banked -> BE"); p["stop"] = round(be, 6)
    tg = p.get("target")
    if tg is not None: p.update(t1=tg, target=round(f + 2 * (float(tg) - f), 6))
    p.update(t1_done=True, t1_utc=t.isoformat(), rail_state="t1_done"); _rlog(p, t, "half_bank", px=x, shares=h, reason=why)
    return p
RULE_LOG.append(dict(id="brett_override_20261029_1917", utc="2026-10-29T11:17:00+00:00", wita="19:17 WITA 29 Oct 2026", approved_by="Brett", relayed_by="TFO",
                     words="Arm the grade A's", scope="trim-only: lifts ONLY the soft trim (A+-only new arms) for grade-A setups; every other rail still applies (A$500k liquidity, 5% ADV, 5% cap / A at 2%, stop on every trade, desk + correlated heat, US net beta <= ~US$750k, no levered ETFs, no chase >1% / price not through stop)"))
RULE_LOG.append(dict(id="tfo_stalefill_20261029_1921", utc="2026-10-29T11:21:00+00:00", wita="19:21 WITA 29 Oct 2026", approved_by="TFO ruling",
                     text="Never fill on a stale price (> STALE_PX_MIN=15 min) or with the venue session closed (INTL_SESSIONS added for TSE/LSE/Euronext/XETRA/NORDIC/HKEX/TSX/...); no fill when room to target from the actual fill < 1.0R. 6098.T fill 19:20:07 WITA voided (void_stale_fill)."))
RULE_LOG.append(dict(id="tfo_venuestale_20261029_1931", utc="2026-10-29T11:31:00+00:00", by="TFO 19:31 WITA", rule="per-venue staleness", text="Fill staleness limit per venue = known feed delay + 5 min (LSE/Euronext/XETRA/BME/SIX/MILAN delay 20 -> 25 min); real-time and unclear venues stay 15 min. venue_delay_min and fill_px_age_min logged on every fill."))
RULE_LOG.append(dict(id="tfo_borrow_intl_20261029_1931", utc="2026-10-29T11:31:00+00:00", by="TFO 19:31 WITA", rule="non-US short borrow", text="Non-US/ASX shorts need an IBKR public short-stock row (home-market file, home currency, <=24h, enough shares); fee = max(IBKR, 3%/yr), 3-day minimum charge taken at fill; rows and fills flagged estimated. Unverifiable -> blocked."))
RULE_LOG.append(dict(id="tfo_sizingcfg_20261029_1943", utc="2026-10-29T11:43:00+00:00", by="TFO 19:43 WITA", rule="seat sizing config cleanup", text="Config cleanup: stale A$1,000/A$500 fixed-risk fields removed from 13 seat JSONs (crypto untouched) and replaced by pct block A 2% / A+ 4% / floor 2% / max 5%. Code (book.risk_plan) is authoritative; Brett 2-5% rule."))
RULE_LOG.append(dict(id="tfo_borrowcron_20261029_1943", utc="2026-10-29T11:43:00+00:00", by="TFO 19:43 WITA", rule="daily intl borrow refresh", text="LaunchAgent com.tfo.veteran-intl-borrow-refresh runs veteran_desk/run_borrow_refresh.sh (python3 -m veteran_desk.intl_borrow) daily 06:03 WITA, off tick minutes; writes state/veteran_desk/borrow_refresh_status.json ok:true/false; 24h stale block stays the fallback."))
RULE_LOG.append(dict(id="tfo_debate_advisory_20261029_1918", utc="2026-10-29T11:18:00+00:00", wita="19:18 WITA 29 Oct 2026", approved_by="TFO ruling",
                     text="gate_v2 challenger / devil's advocate are ADVISORY only and never block or resize an arm: debate_unavailable, STAND_ASIDE, opposite call and factual kill are logged warnings at x1.0."))
def _audit_path():
    return os.path.join(BASE, "state", "veteran_desk", "early_bank_audit.jsonl") if BOOK == _REAL_BOOK else os.path.join(os.path.dirname(BOOK), "early_bank_audit.jsonl")
def audit_early_bank(seat, k, p, t, reason, px, shares, cf_stop, cf_half):
    """Risk audit (30d review): every rail half-bank with reason + counterfactual rail exits."""
    try:
        path = _audit_path(); os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(dict(utc=t.isoformat(), seat=seat, id=k, ticker=p["ticker"], side=p["side"], grade=p.get("grade"), fill=p["fill"],
                                    initial_stop=p.get("initial_stop"), reason=reason, bank_px=px, shares=shares, peak_fav_R=p.get("peak_fav_R"),
                                    gb_peak_net_R=p.get("gb_peak_net_R"), cf_rail_stop_px=cf_stop, cf_rail_half_px=cf_half,
                                    note="cf_rail_stop_px = where the pre-existing rail stop would have exited the position; cf_rail_half_px = normal half-bank level (T1 or +2R)")) + "\n")
    except Exception: pass
def _atr14_yf(sym):
    """ATR14 from daily bars (yfinance, Yahoo chart v8 fallback). None if unavailable (rail then uses 0.5R0 only)."""
    try:
        import yfinance as yf
        h = yf.Ticker(sym).history(period="2mo", interval="1d")
        if h is None or len(h) < 15: return None
        hi, lo, cl = list(h.High), list(h.Low), list(h.Close)
    except Exception: return None
    tr = [max(hi[i] - lo[i], abs(hi[i] - cl[i - 1]), abs(lo[i] - cl[i - 1])) for i in range(1, len(cl))][-14:]
    return float(sum(tr) / len(tr)) if tr else None
ATR_FN = _atr14_yf
def _initial_stop(p):
    if p.get("initial_stop") is not None: return float(p["initial_stop"])
    am = p.get("amends") or []
    return float(((am[0].get("old") or {}).get("stop")) if am and (am[0].get("old") or {}).get("stop") is not None else p["stop"])
def realise(B, p, x, h, au, marks=None):
    """Book closing h units of p at x into seat cash (FX: P&L only). Returns (gross_aud, exit_cost_aud)."""
    sg = 1 if p["side"] == "LONG" else -1; cc = leg_cost_aud(p, x, h, au, marks, "exit")   # tfo_costmodel_20261030
    if is_fx(p): qa = quote_to_aud(p["ticker"], au, marks); n = x * h / qa; gross = sg * (x - p["fill"]) * h / qa; B["cash_aud"] += gross - cc
    else: n = aud(p["ticker"], x, au) * h; B["cash_aud"] += sg * n - cc; gross = sg * (aud(p["ticker"], x, au) - aud(p["ticker"], p["fill"], au)) * h
    return gross, cc
def partial_close(B, p, k, x, h, au, t, reason, suffix, marks=None):
    """Close h of p's shares at x; residual stays open. Appends the closed slice to B['closed']. Returns the slice."""
    gross, cc = realise(B, p, x, h, au, marks); ca = round((p.get("cost_aud") or 0) * h / p["shares"], 2)
    part = dict(p, id=k + suffix, shares=h, status="closed", exit=x, exit_reason=reason, closed_utc=t.isoformat(), gross_aud=round(gross, 2), net_aud=round(gross - ca - cc, 2), cost_aud=ca)
    part.pop("rail_log", None); B["closed"].append(part)
    p.update(shares=(crypto_units(_qty(p) - h) if _is_crypto(p) else int(p["shares"]) - h), cost_aud=round((p.get("cost_aud") or 0) - ca, 2)); return part
def _rlog(p, t, ev, **k):
    p.setdefault("rail_log", []).append(dict(utc=t.isoformat(), ev=ev, **k)); p["rail_log"] = p["rail_log"][-40:]
def rail_update(p, m, t, atr=None):
    """Update peak_fav_R / stop (tighten only) / rail_state. Returns 'stop_through_mark' if the tightened stop sits through the last price."""
    L = p["side"] == "LONG"; sg = 1 if L else -1; f = float(p["fill"]); s0 = _initial_stop(p); r0 = abs(f - s0)
    p.setdefault("initial_stop", s0); p["r0"] = r0; p.setdefault("scale_t1", True)
    if p.get("scale_t1") is None: p["scale_t1"] = True
    if r0 <= 0: p.setdefault("rail_state", "initial"); p.setdefault("peak_fav_R", 0.0); return None
    ext = m["hi"] if L else m["lo"]; hwp = p.get("rail_hwp"); hwp = ext if hwp is None else (max(hwp, ext) if L else min(hwp, ext)); p["rail_hwp"] = hwp
    peak = max(float(p.get("peak_fav_R") or 0.0), sg * (hwp - f) / r0, 0.0); p["peak_fav_R"] = round(peak, 4)
    be = f + sg * f * rt_rate(p); cand = None
    if peak >= RAIL_BE_R: cand = be
    if peak >= RAIL_TRAIL_R:
        d = max(0.5 * r0, float(atr or 0.0)); tr = f + sg * (peak - 1.0) * r0
        tr = min(tr, hwp - d) if L else max(tr, hwp + d); cand = max(tr, be) if L else min(tr, be)
    old = p["stop"]
    if cand is not None and ((L and cand > old) or (not L and cand < old)):
        p["stop"] = round(cand, 6); _rlog(p, t, "stop_tighten", old=old, new=p["stop"], peak_fav_R=p["peak_fav_R"], atr14=atr)
    st = "t1_done" if p.get("t1_done") else "trailing" if peak >= RAIL_TRAIL_R else "be" if peak >= RAIL_BE_R else "initial"
    if st != p.get("rail_state"): _rlog(p, t, "state", old=p.get("rail_state"), new=st); p["rail_state"] = st
    if p["stop"] != old and ((L and p["stop"] >= m["last"]) or (not L and p["stop"] <= m["last"])): return "stop_through_mark"
    return None
def open_R(p, px):
    r0 = p.get("r0") or abs(float(p["fill"]) - _initial_stop(p)); return ((1 if p["side"] == "LONG" else -1) * (px - float(p["fill"])) / r0) if r0 else 0.0
# ---- tfo_review_20261030 (Brett 19:34 WITA 30 Oct 2026 "let the traders learn"): shared VD + THF helpers ----
DEX_SEATS = ("trend", "price_action"); DEX_NOTIONAL_CAP = 0.35; DEX_HALFCUT_SESSIONS = 2; DEX_HALFCUT_R = 0.5
MARGIN_HEADROOM = 1.30; FX_MARGIN_RATE = 1 / 30.0; FEE_FILTER_X = 3.0
SEAT_NOTIONAL_CAP = 0.40   # seatcap 2026-10-30 21:23 WITA (Brett FULL AUTH): no single non-FX position above 40% of seat equity (Lena GC was 91%, gapped -A$9,676)
SEAT_ETF_CAP = 0.75; SEAT_MARGIN_CAP = 0.40   # seatcap2 (TFO ruling 21:54 WITA): broad index/sector/macro ETFs 75% of seat notional; FX + futures by MARGIN used <= 40% of seat (30% headroom kept); single stocks, commodity ETFs, crypto 40%
BROAD_ETFS = frozenset("""SPY VOO IVV VTI ITOT QQQ QQQM DIA IWM RSP MDY IJH IJR VT ACWI VEA VWO EFA EEM IEFA IEMG DGRO SCHD VIG VYM NOBL SPLV USMV QUAL MTUM VLUE
XLK XLF XLE XLV XLI XLP XLY XLU XLB XLRE XLC SMH SOXX VGT VDE VHT VFH VIS VCR VDC VPU VAW VNQ KRE KBE IBB XBI XOP XME XHB XRT ITB IYR IGV HACK ARKK
TLT IEF SHY IEI AGG BND LQD HYG JNK TIP MUB EMB BIL SGOV EWJ EWZ FXI MCHI INDA EWA EWC EWU EWG EWY EWT EWW EZU VGK
STW.AX IOZ.AX VAS.AX A200.AX NDQ.AX IVV.AX VGS.AX XIU.TO XIC.TO ZEB.TO XEG.TO ZCN.TO VFV.TO ISF.L VUSA.L CSPX.L""".split())
def seat_cap_pct(e):
    """(cap fraction, basis) for one proposal: basis 'margin' for FX/futures, else 'notional'."""
    tk = str(e.get("ticker") or "").upper()
    if tk.endswith("=F") or is_fx(e): return SEAT_MARGIN_CAP, "margin"
    return (SEAT_ETF_CAP if tk in BROAD_ETFS else SEAT_NOTIONAL_CAP), "notional"
def prefill_seat_cap(p, f, n, eq, au=None, marks=None):
    """capfill 30 Oct: return None if within cap, 'reject' if the capped size rounds to zero, else a note after cutting p['shares'] (and risk_aud) down to the cap. Cuts only."""
    try:
        cap, basis = seat_cap_pct(p)
        if eq <= 0 or not p.get("shares") or not n: return None
        mr = 1.0
        if basis == "margin":
            try:
                from veteran_desk import execution_rules as _XR
                fsm = _XR.FUTURES.get(str(p["ticker"]).upper()); mr = float(fsm["margin"]) if fsm else FX_MARGIN_RATE
            except Exception: mr = FX_MARGIN_RATE
        used = n * mr
        if used <= eq * cap * 1.001: return None
        cx = str(p.get("ticker", "")).upper().endswith("-USD")
        q0 = p["shares"]; q = q0 * (eq * cap) / used
        q = (crypto_units(q) if cx else int(q))
        if q <= 0: return "reject"
        if p.get("risk_aud"): p["risk_aud"] = round(float(p["risk_aud"]) * q / q0, 2)
        note = "seat_cap_%s_%dpct at fill: %s -> %s (was %.0f%% of seat)" % (basis, cap * 100, q0, q, used / eq * 100)
        p.update(shares=q, cap_resized=note); return note
    except Exception: return None
GAP_PCT_FUTURES, GAP_PCT_THIN, THIN_ADV_AUD = 0.03, 0.04, 5_000_000   # gapguard: assumed gap beyond the stop for futures / thin names (ADV < A$5m)
def gap_risk_pct(e):
    """Extra adverse move (fraction of entry) assumed beyond the stop for gap-prone instruments; 0 for liquid cash names/FX/crypto majors."""
    tk = str(e.get("ticker") or "").upper()
    if tk.endswith("=F"): return GAP_PCT_FUTURES
    try: adv = float(e.get("adv_aud") or 0)
    except Exception: adv = 0.0
    return GAP_PCT_THIN if 0 < adv < THIN_ADV_AUD else 0.0
def stabilised(e, bars):
    """Dip-buy guard: the last COMPLETED bar must show stabilisation (long: green bar whose low held the prior low; short: mirror). None/short data -> False (fail closed)."""
    if not bars or len(bars) < 2: return False
    b, pb = bars[-1], bars[-2]
    if e.get("side") == "LONG": return b["c"] > b["o"] and b["l"] >= pb["l"]
    return b["c"] < b["o"] and b["h"] <= pb["h"]
def seat_red(B): return float((B or {}).get("pnl_since_inception_aud") or 0) < -0.5 or float((B or {}).get("today_pnl_aud") or 0) < -0.5
def sessions_since(u, t):
    """Weekday sessions completed since u (UTC iso); 0 if unknown."""
    try: d0 = dt.datetime.fromisoformat(str(u)).date()
    except Exception: return 0
    n, d = 0, d0
    while d < t.date():
        d += dt.timedelta(days=1)
        if d.weekday() < 5: n += 1
    return n
def post_extremes(m, since):
    """(hi, lo, open) of the mark usable for orders placed at `since`. Live marks carry window_start (first bar of the hi/lo window);
    a window that starts before `since` is not trusted -> last price only (no fills/exits from pre-order bars). Marks without the field (legacy/test fakes) keep hi/lo."""
    last = m.get("last"); ws = m.get("window_start")
    if ws and since:
        try:
            if dt.datetime.fromisoformat(str(ws).replace("Z", "+00:00")) < dt.datetime.fromisoformat(str(since).replace("Z", "+00:00")): return last, last, None
        except Exception: return last, last, None
    return m.get("hi", last), m.get("lo", last), m.get("open")
def gap_stop_px(p, o_, last=None):
    """Stop fill: the stop, or the gap price when the bar opened through it (long: open < stop; short: open > stop)."""
    s_ = float(p["stop"]); L = p["side"] == "LONG"
    if o_ is not None and ((L and float(o_) < s_) or (not L and float(o_) > s_)): return float(o_)
    return s_
def fee_filter(ticker, entry, target, adv_aud=None):
    """None or reason: expected move to T1 must be >= 3x round-trip costs."""
    try:
        rt = CT.est_rt_rate(ticker, P.venue_of(ticker), float(entry)); mv = abs(float(target) - float(entry)) / float(entry)
        return None if mv >= FEE_FILTER_X * rt - 1e-12 else "fee_filter: move to T1 %.3f%% < %.0fx round-trip cost %.3f%%" % (mv * 100, FEE_FILTER_X, rt * 100)
    except Exception as x: return "fee_filter check failed - fail closed (%s)" % type(x).__name__
def prearm_check(e, q, px, t):
    """Pre-arm booking check. Units/multiplier vs instrument spec, fee filter. Closed market -> order queued for the open (fills are session + stale gated)."""
    from veteran_desk import execution_rules as XR
    tk = str(e["ticker"]).upper(); fs = XR.FUTURES.get(tk)
    if fs and e.get("multiplier") is not None and float(e["multiplier"]) != float(fs["mult"]): return "futures_units: multiplier %s != spec %s for %s" % (e["multiplier"], fs["mult"], tk)
    if tk.endswith("=F") and not fs: return "futures_units: no contract spec for %s - fail closed" % tk
    if e.get("units") is not None and fs and float(e["units"]) % float(fs["mult"]): return "futures_units: %s units not a whole number of %s-unit contracts" % (e["units"], fs["mult"])
    return fee_filter(e["ticker"], e["entry"], e["target"])
def _slip_log(seat, k, p, f, slip, ot, t):
    try: open(os.path.join(_state_dir(), "slip_rejects.jsonl"), "a").write(json.dumps(dict(utc=t.isoformat(), seat=seat, id=k, ticker=p.get("ticker"), side=p.get("side"), plan=p.get("entry"), fill=f, slip_bps=slip, order_type=ot, collar_bps=SLIP_COLLAR_BPS)) + "\n")
    except Exception: pass
def tick(price_fn=None, au=None, queue_path=None):
    import funder_gate as FG
    price_fn = price_fn or live_px; _fxstale = None
    if not au:   # tfo_review_20261030: stale/missing AUDUSD blocks NEW arms only; marks/stops/exits run on the last good rate
        try: au = audusd()
        except FxUnavailable as x:
            _fxstale = str(x); au = float((dict(_AU_LAST) or (_rj_safe(_au_cache()) if _au_cache() else None) or {}).get("v") or 0) or None
            if au is None: raise
    D = load(au); t = now(); ev = []; upd = {}
    Q = FG.load(queue_path); vet = [e for e in Q.get("entries", {}).values() if e.get("book") == "veteran"]
    syms = {p["ticker"] for S in D["seats"].values() for p in S["positions"].values()} | {e["ticker"] for e in vet if e.get("state") == FG.WAIT and e.get("decision") == "approve"}
    marks = {}
    for s_ in syms:
        try: marks[s_] = price_fn(s_)
        except Exception: marks[s_] = None
    ok, why = firm_ok(); news_flag, post = posture(); kill = D.get("kill_switch") or {}
    if _fxstale: ok, why = False, _fxstale
    mult = 0.5 if post == "crisis" else 1.0
    if kill.get("state") == "HALVED": mult *= 0.5
    unk = post == "unknown"   # fail-safe posture: arming paused; armed fills + rebooks HELD (TFO HT 23:10); stops/exits unchanged
    if kill.get("state") == "FLAT": ok, why = False, "desk kill switch FLAT (-12% from peak) - Brett must reset"
    for e in vet:   # 0. 2026-10-29 withdraw-fix: a withdrawn queue entry never stays/gets ARMED
        if e.get("state") == "withdrawn":
            for S_ in D["seats"].values():
                p_ = S_["positions"].get(e["id"])
                if p_ and p_.get("status") == "armed": p_.update(status="cancelled", why="queue withdrawn"); S_.setdefault("closed", []).append(dict(p_, closed_utc=t.isoformat(), exit_reason="withdrawn")); S_["positions"].pop(e["id"], None); ev.append((e["id"], "cancelled", None))
    for e in vet:   # 1. funder ticks -> arm in the PROPOSING seat's own book (only ticked, only gate-passed veteran entries)
        if e.get("state") != FG.WAIT: continue
        if e.get("decision") == "reject": upd[e["id"]] = dict(state="rejected"); continue
        if e.get("decision") != "approve": continue
        if e.get("ack_required") and not e.get("acked"):   # tfo_propose_ack_20261031: caller never confirmed the propose result -> never arms; >10 min unacked -> expired
            try: _age = (t - dt.datetime.fromisoformat(e.get("queued_utc"))).total_seconds()
            except Exception: _age = 1e9
            if _age > 600: upd[e["id"]] = dict(state="expired", why="propose not acked by caller within 10 min (timeout/error) - never armed"); ev.append((e["id"], "blocked", "propose_not_acked"))
            continue
        seat = seat_of(e); B = D["seats"].get(seat)
        if B is None: upd[e["id"]] = dict(state="expired", why=f"seat {seat} has no book"); continue
        gone = [c for c in B.get("closed", []) if c.get("id") == e["id"]]
        if gone or (e["id"] in B["positions"]):   # never re-arm an id the book already armed, cancelled/disarmed or closed (TFO HT 21:31)
            if gone: upd[e["id"]] = dict(state=gone[-1].get("status") or "cancelled", why="book already %s this id - not re-armed" % (gone[-1].get("status") or "closed"))
            continue
        if unk: upd[e["id"]] = dict(note="regime unknown (inputs missing) - new arms paused, entry stays WAIT %sZ" % t.isoformat()[:16]); continue
        if not e.get("order_type") and (e.get("stop_entry") not in (None, "", 0) or str(e.get("entry_type") or "").lower() in ("stop", "stop_entry", "breakout", "stop_limit")):   # stopentry 30 Oct: a stop entry never arms as a plain limit
            try:
                _se = float(e.get("stop_entry") or e["entry"])
                if not (_se > 0): raise ValueError
                e = dict(e, entry=_se, order_type="stop_limit", order_type_auto="stop_entry->stop_limit")
            except Exception:
                upd[e["id"]] = dict(state="expired", why="stop_entry: no limit level derivable - rejected"); ev.append((e["id"], "blocked", "stop_entry_no_limit")); continue
        eq = equity_aud(B, marks, au); L = e["side"] == "LONG"; px = (marks.get(e["ticker"]) or {}).get("last"); stop, entry = float(e["stop"]), float(e["entry"])
        open_risk = sum(p["risk_aud"] for p in B["positions"].values() if p["status"] in ("armed", "open")); n_open = sum(p["status"] in ("armed", "open") for p in B["positions"].values())
        spec = "sleeve spec" in str((e.get("tags") or {}).get("strategy")) or e.get("sleeve") == "spec"
        _grade = e.get("grade") if e.get("grade") in ("A+", "A") else None; _crypto = seat == "crypto"   # kaiseat 15:04 WITA: real grade field; crypto rails by SEAT, not KRAKEN: prefix
        rp = risk_plan(eq, _grade or "A", bool(e.get("chal_agree")), float(e.get("gate_size_mult") or 1.0) * mult, floor=RISK_CRYPTO_MIN if _crypto else None, seat_pct=None if (seat in DEX_SEATS and seat_red(B)) else e.get("seat_risk_pct"))
        pct = rp["pct"]; risk = SEAT_SEED * SPEC_SLEEVE_PCT * RISK_SPEC_OF_SLEEVE * mult if spec else rp["risk_aud"]
        try: per = aud(e["ticker"], abs(entry - stop), au); _u = aud(e["ticker"], entry, au)
        except FxUnavailable as fx_: upd[e["id"]] = dict(state="expired", why=str(fx_)); ev.append((e["id"], "blocked", str(fx_))); continue   # fxsize 29 Oct: fail closed
        _cx = P.venue_of(e["ticker"]) == "CRYPTO"; q = (crypto_units(risk / per) if _cx else int(risk / per)) if per > 0 else 0; bad = None   # tfo_review_20261030 fractional crypto
        if _grade is None: bad = "missing_grade"   # kaiseat: no grade field -> reject the arm
        elif _crypto and _grade != "A+" and now() < SOFT_TRIM_UNTIL: bad = "soft_trim_a_plus_only"   # softtrim 2026-10-29 16:12 WITA: new crypto arms A+ only (new arms only)
        elif rp.get("reject") and not spec: bad = rp["reject"]   # railfix 2026-10-29: sub-floor idea rejected at arm, never lifted
        elif not spec and eq > 0 and per * q > eq * RISK_MAX + 0.01: q = int(eq * RISK_MAX / per)   # per-trade hard cap 5% of seat equity (cuts only)
        _floor_note = None
        if not bad and not spec and _crypto:   # kairails 14:28 WITA: A$50k clip (A$100k A+); reject if capped clip < 0.5% floor (kaiseat: crypto seat only)
            _g = _grade
            if _u > 0 and q * _u > crypto_clip_cap(_g): q = crypto_units(crypto_clip_cap(_g) / _u)
            if per * q < eq * RISK_CRYPTO_MIN - 0.01: bad = "below_crypto_floor"   # tfo_review_20261030: fractional Kraken units - no whole-unit rounding, 0.98 floortol removed
        if not bad and seat in DEX_SEATS and not is_fx(e) and _u > 0 and q * _u > eq * DEX_NOTIONAL_CAP:   # tfo_review_20261030 Dex: notional cap 35% of seat equity (cuts only; < 1% risk after the cut -> reject)
            q = crypto_units(eq * DEX_NOTIONAL_CAP / _u) if _cx else int(eq * DEX_NOTIONAL_CAP / _u)
            if per * q < eq * RISK_MIN - 0.01 and not spec: bad = "dex_notional_cap_35pct: risk after cap %.2f%% < floor" % (per * q / eq * 100)
        if not is_fx(e) and _u > 0 and q * _u > eq: q = (crypto_units(eq / _u) if _cx else int(eq / _u))   # Brett 29 Oct: notional cap 1x seat equity (tight stops pushed 2.4x); cuts risk only
        _gp = gap_risk_pct(e); _per_gap = per + (_u * _gp if _gp else 0.0)
        if not bad and not spec and _gp and _per_gap > 0 and per * q > 0 and _per_gap * q > risk + 0.01:   # gapguard 30 Oct: size so risk INCLUDING an assumed gap through the stop fits the budget (cuts only)
            q = crypto_units(risk / _per_gap) if _cx else int(risk / _per_gap)
        _cap, _basis = seat_cap_pct(e)
        if _basis == "margin":   # seatcap2: FX/futures measured by margin used (futures spec margin rate; FX 3.33%)
            try:
                from veteran_desk import execution_rules as _XR
                _fsm = _XR.FUTURES.get(str(e["ticker"]).upper()); _mr = float(_fsm["margin"]) if _fsm else FX_MARGIN_RATE
            except Exception: _mr = FX_MARGIN_RATE
            _uu = (entry / quote_to_aud(e["ticker"], au, marks)) if is_fx(e) else _u
        else: _mr, _uu = 1.0, _u
        if not bad and _uu > 0 and q * _uu * _mr > eq * _cap:   # seatcap 30 Oct: per-position seat cap (cuts only)
            q = crypto_units(eq * _cap / (_uu * _mr)) if _cx else int(eq * _cap / (_uu * _mr))
            _abind = (not _crypto and _grade == "A" and _basis == "notional" and abs(_cap - SEAT_NOTIONAL_CAP) < 1e-9)
            if _abind and per > 0: q = min(q, int(eq * RISK_A_CAPBIND_MIN / per))   # tfo_review_fix_20261031: A with 40% cap binding SIZES at 1.5% target (capped by notional) - not a floor, never rejects
            _capfl = RISK_CRYPTO_MIN if _crypto else (RISK_A_CAPBIND_MIN if (_grade == "A" and _basis == "notional" and abs(_cap - SEAT_NOTIONAL_CAP) < 1e-9) else (RISK_MIN if _grade in ("A", "A+") else RISK_NON_A_MIN))   # tfo_a1pct_20261031: A 1.5% when 40% cap binds; else A 1%; non-A 2%
            if not spec and not _abind and per * q < eq * _capfl - 0.01: bad = "seat_cap_%s_%dpct: risk after cap %.2f%% < floor" % (_basis, _cap * 100, per * q / eq * 100 if eq > 0 else 0)
        if not bad and _gp and e.get("side") in ("LONG", "SHORT") and px is not None and ((L and entry < px) or (not L and entry > px)) and e.get("order_type") != "stop_limit":   # gapguard: futures/thin limit dip-buy needs a completed stabilisation bar
            try: _bars = BARS_FN(e["ticker"], _cx)
            except Exception: _bars = None
            if not stabilised(e, _bars): bad = "gap_guard: futures/thin limit dip-buy without a completed stabilisation bar"
        dup = [k_ for k_, p_ in B["positions"].items() if p_["status"] in ("armed", "open") and p_["ticker"] == e["ticker"] and p_["side"] == e["side"] and k_ != e["id"]]
        if not dup and os.environ.get("THF_DESK") == "1":   # tfo_thf_xseat_dedupe_20261030: THF - one armed/open order per symbol+side ACROSS seats (FX + macro both proposed EURUSD short)
            for _s2, _B2 in D["seats"].items():
                if _s2 == seat: continue
                _x = [k_ for k_, p_ in _B2.get("positions", {}).items() if p_.get("status") in ("armed", "open") and p_.get("ticker") == e["ticker"] and p_.get("side") == e["side"]]
                if _x: bad = bad or "duplicate_cross_seat: THF seat %s already has %s %s (%s) - one armed/open per symbol+side across THF" % (_s2, e["side"], e["ticker"], _x[0]); break
        if not dup and os.environ.get("THF_DESK") == "1":   # tfo_thf_xseat_dedupe_20261030: THF - one armed/open order per symbol+side ACROSS seats (FX + macro both proposed EURUSD short)
            for _s2, _B2 in D["seats"].items():
                if _s2 == seat: continue
                _x = [k_ for k_, p_ in _B2.get("positions", {}).items() if p_.get("status") in ("armed", "open") and p_.get("ticker") == e["ticker"] and p_.get("side") == e["side"]]
                if _x: bad = bad or "duplicate_cross_seat: THF seat %s already has %s %s (%s) - one armed/open per symbol+side across THF" % (_s2, e["side"], e["ticker"], _x[0]); break
        if not bad and _theme_clash(D, e): bad = "firm_one_trade_per_theme_%s: %s" % _theme_clash(D, e)   # tfo_theme_20261031 (XOM vs held XLE): one open/armed trade per theme+side across ALL seats
        if bad: pass
        elif dup: bad = "duplicate: seat %s already has %s %s %s (%s) - one armed/open order per seat+symbol+side" % (seat, B["positions"][dup[0]]["status"], e["side"], e["ticker"], dup[0])   # dedupe guard (TFO HT 21:08)
        elif not stop or (L and stop >= entry) or (not L and stop <= entry): bad = "no valid stop"
        elif not ok: bad = why
        elif px is None: upd[e["id"]] = dict(note=f"approved; no live price {t.isoformat()[:16]}Z - retry"); continue
        elif (L and px <= stop) or (not L and px >= stop): bad = f"price {px:g} already through stop {stop:g}"
        elif e.get("order_type") == "stop_limit" and ((L and px > entry * 1.01) or (not L and px < entry * .99)):
            bad = f"price {px:g} >1% through stop-entry {entry:g} - not chasing"   # 2026-10-29 Marcus: pullback/rally LIMIT always allowed; chase only stop_limit already through
        elif open_risk + per * q > eq * HEAT_CAP: bad = "seat book heat cap 15% reached"
        elif n_open >= MAX_OPEN: bad = "max 8 open"
        elif q <= 0 or (not _cx and q < 1): bad = "size rounds to zero"
        elif spec and aud(e["ticker"], entry, au) * q > SEAT_SEED * SPEC_SLEEVE_PCT - sum(aud(p["ticker"], p["entry"], au) * p["shares"] for p in B["positions"].values() if p.get("sleeve") == "spec"):
            q = int((SEAT_SEED * SPEC_SLEEVE_PCT - sum(aud(p["ticker"], p["entry"], au) * p["shares"] for p in B["positions"].values() if p.get("sleeve") == "spec")) / aud(e["ticker"], entry, au))
            if q < 1: bad = "speculative sleeve (A$50k) full"
        pt = None
        if not bad:   # execution rules: short borrow, futures multiplier/margin, FX pips, Kraken fees (2026-10-28 19:17 order)
            try:
                from veteran_desk import execution_rules as XR
                bad = prearm_check(e, q, px, t)   # tfo_review_20261030: futures multiplier/units, market open or queued, fee filter
                if not bad:
                    _fs = XR.FUTURES.get(str(e["ticker"]).upper()); _m = int(_fs["mult"]) if _fs else 1
                    if _fs: q = int(q // _m) * _m
                    if _fs and q < _m: bad = "futures_units: size < 1 contract (%s = %d units per contract)" % (e["ticker"], _m)
                if not bad:
                    pt = XR.pre_trade(e["ticker"], e["side"], (q // _m) if _fs else q, px)   # tfo_review_20261030: contracts, not units (margin was 100x on GC=F)
                    if not pt.get("ok"): bad = "execution rules: " + str(pt.get("why"))
                    elif _fs: pt["contracts"] = int(q // _m); pt["units_per_contract"] = _m
            except Exception as x: bad = "execution rules check failed - fail closed (%s)" % type(x).__name__
        if not bad and not is_fx(e):   # 2026-10-29 cashcheck (restored 2026-10-29 hlclip job; lost in the 13:41 cryptoxcheck write): a paper order may never push seat cash below zero; reject, never resize
            _c = CT.est_rt_rate(e["ticker"], P.venue_of(e["ticker"]), e.get("entry")) / 2   # pre-trade estimate from CT (one source)
            _resv = sum((aud(p_["ticker"], p_["entry"], au) * p_["shares"] if p_["side"] == "LONG" else 0) * (1 + _c) for p_ in B["positions"].values() if p_.get("status") == "armed" and not is_fx(p_))
            _need = q * _u * ((1 + _c) if L else _c)
            if B["cash_aud"] - _resv - _need < 0: bad = "insufficient_cash"
            elif B["cash_aud"] - _resv - _need * MARGIN_HEADROOM < 0: bad = "margin_headroom: need A$%.0f x %.1f > available A$%.0f (30%% headroom, tfo_review_20261030)" % (_need, MARGIN_HEADROOM, B["cash_aud"] - _resv)
        if not bad and is_fx(e):   # tfo_review_20261030: FX margin (3.33%% of notional) with 30%% headroom
            _fxm = q * entry / quote_to_aud(e["ticker"], au, marks) * FX_MARGIN_RATE
            if B["cash_aud"] < _fxm * MARGIN_HEADROOM: bad = "margin_headroom: FX margin A$%.0f x %.1f > cash A$%.0f" % (_fxm, MARGIN_HEADROOM, B["cash_aud"])
        if bad: upd[e["id"]] = dict(state="expired", why=bad); ev.append((e["id"], "blocked", bad)); continue
        # TFO HT 03:28: order_type explicit at arm - entry at/through market = true limit; entry beyond market (breakout) = stop_limit
        if not bad and _uu > 0 and q * _uu * _mr > eq * _cap + 0.01: bad = "seat_cap_%s_%dpct: final size %.0f%% of seat (rounding)" % (_basis, _cap * 100, q * _uu * _mr / eq * 100 if eq > 0 else 999)   # seatcap: re-check after futures/spec resizing
        if bad: upd[e["id"]] = dict(state="expired", why=bad); ev.append((e["id"], "blocked", bad)); continue
        B["positions"][e["id"]] = dict(id=e["id"], seat=seat, ticker=e["ticker"], side=e["side"], entry=entry, stop=stop, target=float(e["target"]), shares=q,
            risk_aud=round(per * q, 2), gap_risk_aud=round(_per_gap * q, 2) if _gp else None, risk_pct=pct, sleeve="spec" if spec else "core", size_mult=mult, gate_size_mult=e.get("gate_size_mult"), sizing_warning=rp["warning"], floor_note=_floor_note, posture=post, status="armed", queued_for_open=not session_open(P.venue_of(e["ticker"]), t), seat_risk_pct=e.get("seat_risk_pct"), override=bool(e.get("override")), order_type=e.get("order_type") if e.get("order_type") in ("limit", "stop_limit") else ("limit" if px and ((L and entry <= px) or (not L and entry >= px)) else "stop_limit"), armed_utc=t.isoformat(), venue=P.venue_of(e["ticker"]), hash=e.get("hash"), pre_trade=pt, scale_t1=True, grade=_grade, tags=e.get("tags"))
        upd[e["id"]] = dict(state="armed", armed_utc=t.isoformat(), px_at_approval=px, shares=q, risk_aud=round(per * q), **({"floor_note": _floor_note} if _floor_note else {})); ev.append((e["id"], "armed", q))
    for seat, B in D["seats"].items():   # 2. code-only fills / stops / targets, per seat book
        for k, p in B["positions"].items():
            m = marks.get(p["ticker"])
            if m and m.get("asof"):   # fix 4/7: stale-price check (>15 min old) -> logged; alert while the venue session is open
                age = px_age_min(m, t); p["px_age_min"] = round(age, 1) if age is not None else None
                if age is not None and age > stale_limit_min(p.get("venue")):
                    so = session_open(p.get("venue"), t); rec = dict(utc=t.isoformat(), seat=seat, id=k, ticker=p["ticker"], asof=m.get("asof"), age_min=round(age, 1), session_open=so, level="ALERT" if so else "info-session-closed")
                    D.setdefault("price_alerts", []); D["price_alerts"] = (D["price_alerts"] + [rec])[-50:]; _price_alert(rec)
            if not m:
                if p.get("status") == "open":
                    p["mark_missing"] = True   # MARK_MISSING: no real mark this tick (leaderboard holds awards)
                    try: fb = fallback_quote(p["ticker"])
                    except Exception: fb = None
                    if fb:   # fix 4/7: fallback quote for marking only (no fills/stops on it)
                        p.update(mark=fb["last"], mark_src=fb["src"], mark_asof=fb["asof"], mark_fallback_utc=t.isoformat())
                        _price_alert(dict(utc=t.isoformat(), seat=seat, id=k, ticker=p["ticker"], level="ALERT", why="mark_missing - fallback quote used", fallback=fb))
                    else: _price_alert(dict(utc=t.isoformat(), seat=seat, id=k, ticker=p["ticker"], level="ALERT", why="mark_missing and no fallback quote"))
                continue
            p.pop("mark_missing", None); p.pop("mark_src", None); p["mark_utc"] = t.isoformat()
            L = p["side"] == "LONG"; sg = 1 if L else -1; c = CT.est_rt_rate(p["ticker"], p.get("venue"), p.get("entry"), p.get("adv_aud")) / 2   # pre-trade estimate from CT; fills overwrite with leg_cost_aud
            if p["status"] == "open" and not p.get("off_hours_checked"):   # fix 2/7: flag any fill booked while its session was closed
                p["off_hours_checked"] = True
                try: fu = dt.datetime.fromisoformat(p["filled_utc"])
                except Exception: fu = None
                if fu and p.get("venue") == "US" and not session_open(p["venue"], fu):   # rebook path (first_print_after_open) is NYSE-only; ASX fills are session-gated at fill time
                    p.update(off_hours_fill=True, rebook_pending=True, off_hours_fill_audit=dict(fill=p["fill"], filled_utc=p["filled_utc"], cost_aud=p["cost_aud"], notional_aud=p.get("fill_notional_aud"), shares=p["shares"], risk_aud=p["risk_aud"], slippage_bps=p.get("slippage_bps"), flagged_utc=t.isoformat(), note="OFF-HOURS FILL: booked while venue session closed; to be rebooked at first print after the open (no backdating)"))
                    ev.append((k, "off_hours_fill_flagged", p["fill"]))
            if p["status"] == "open" and p.get("rebook_pending"):
                if not session_open(p["venue"], t): p["exec_note"] = "off-hours fill held; rebook at first print after the open"; continue
                try: fp = first_print_after_open(p["ticker"], t)
                except Exception: fp = None
                if not fp: p["exec_note"] = "rebook pending: no post-open print yet"; continue
                if unk: p["exec_note"] = "rebook HELD: regime unknown - rebook when regime known"; continue   # TFO HT 23:10
                a = p["off_hours_fill_audit"]; _cl = COST_RT_LEGACY_20261029.get(p.get("venue"), .002) / 2; n0 = a.get("notional_aud") or ((a["cost_aud"] / _cl) if _cl else aud(p["ticker"], a["fill"], au) * a["shares"])   # costmodel: stored notional; legacy audits = cost / legacy half-RT   # original AUD notional as debited (cost = notional x half-RT)
                if is_fx(p): B["cash_aud"] += a["cost_aud"]   # FX: only the cost ever left cash
                else: B["cash_aud"] += sg * n0 + a["cost_aud"]   # unwind the off-hours booking (cash + its cost) ...
                f = fp["px"]; ot_ = p.get("order_type") or "limit"
                lim_ = p["entry"] if ot_ == "limit" else p["entry"] * (1 + MAX_SLIP if L else 1 - MAX_SLIP)
                if (L and f > lim_ + 1e-12) or (not L and f < lim_ - 1e-12):   # railfix 2026-10-29 (XLE 68.48 vs limit 68.2): never rebook through the limit
                    p.update(status="armed", rebook_pending=False, rebook_note="first print %g beyond limit %g - off-hours fill unwound, order back to working (no fill through limit)" % (f, lim_), exec_note="rebook: first print beyond limit - order working")
                    for k_ in ("fill", "filled_utc", "cost_aud", "slippage_bps", "order", "exec_by", "mark", "off_hours_checked"): p.pop(k_, None)
                    ev.append((k, "rebook_unfilled_beyond_limit", f)); continue
                per = aud(p["ticker"], abs(f - p["stop"]), au);
                eqx = B["cash_aud"] + sum(pos_value_aud(q_, (marks.get(q_["ticker"]) or {}).get("last") or q_.get("mark") or q_["fill"], au, marks) for kk, q_ in B["positions"].items() if q_["status"] == "open" and kk != k)
                q = p["shares"]; rpct = per * q / eqx if eqx > 0 else 0; rnote = None
                if (L and f <= p["stop"]) or (not L and f >= p["stop"]): rnote = "first print through stop - rebooked then stop applies"
                elif rpct > RISK_MAX: q = int(eqx * RISK_MAX / per); rnote = "resized to 5% risk cap"
                elif rpct < (RISK_CRYPTO_MIN if (p.get("seat") or seat) == "crypto" else RISK_MIN) - 1e-9 and p.get("sleeve") != "spec":   # railfix 2026-10-29: reject, never lift
                    p.update(status="cancelled", why="below_1pct_floor", rebook_pending=False, rebook_note="below_1pct_floor: risk %.2f%% at first print - rejected, not lifted; off-hours booking unwound" % (rpct * 100), cancelled_utc=t.isoformat())
                    B.setdefault("closed", []).append(dict(p, closed_utc=t.isoformat(), exit_reason="below_1pct_floor")); ev.append((k, "rebook_rejected", "below_1pct_floor")); continue
                _u = aud(p["ticker"], f, au)
                if not is_fx(p) and _u > 0 and q * _u > eqx: q = int(eqx / _u); rnote = (rnote or "") + "; capped at 1x seat equity notional"
                n = (f * q / quote_to_aud(p["ticker"], au, marks)) if is_fx(p) else aud(p["ticker"], f, au) * q
                c = (leg_cost_aud(dict(p, shares=q), f, q, au, marks, "entry") / n) if n else c   # tfo_costmodel_20261030
                if not is_fx(p) and B["cash_aud"] - (sg * n + n * c) < 0:   # 2026-10-29 cashcheck: off-hours booking already unwound above; reject rebook, never resize
                    p.update(status="cancelled", why="insufficient_cash", rebook_pending=False, exec_note="rebook rejected: insufficient_cash"); B.setdefault("closed", []).append(dict(p, closed_utc=t.isoformat(), exit_reason="insufficient_cash")); ev.append((k, "blocked", "insufficient_cash")); continue
                B["cash_aud"] -= (n * c) if is_fx(p) else (sg * n + n * c)   # ... and rebook at the first post-open print, costs included
                p.update(fill=f, filled_utc=t.isoformat(), first_print_asof=fp["asof"], shares=q, cost_aud=round(n * c, 2), fill_notional_aud=n, risk_aud=round(per * q, 2), risk_pct_actual=round(per * q / eqx, 5) if eqx > 0 else None,
                         slippage_bps=round((f / p["entry"] - 1) * (1 if L else -1) * 1e4, 1), rebook_pending=False, rebooked_utc=t.isoformat(), rebook_note=rnote, exec_note="rebooked at first print after open")
                ev.append((k, "rebooked_after_open", f))
            if p["status"] == "armed":
                if unk: p["exec_note"] = f"{EXEC_TRADER}: regime unknown - fill HELD, order stays working until regime known"; continue   # TFO HT 23:10: hold, no cap
                if not session_open(p["venue"], t): p["exec_note"] = f"{EXEC_TRADER}: venue session closed - no fill"; continue   # fix 2/7
                if in_open_window(p["venue"], t): p["exec_note"] = f"{EXEC_TRADER}: waiting out the first {OPEN_AVOID_MIN} min after the open"; continue
                _age = px_age_min(m, t)   # stalefill 2026-10-29 (TFO 19:21 WITA): px_age_min was only alerted on, never gated the fill
                _lim = stale_limit_min(p.get("venue"))   # venuestale: feed delay + 5
                if _age is not None and _age > _lim: p["exec_note"] = f"{EXEC_TRADER}: price {_age:.0f} min old (> {_lim} = venue delay {venue_delay_min(p.get('venue'))} + 5) - no fill on a stale price, order still working"; continue
                ot = p.get("order_type") or "limit"
                if ot == "stop_limit":   # breakout/stop entry, explicitly typed: trigger at entry, protective limit MAX_SLIP through
                    _hi, _lo, _op = post_extremes(m, p.get("armed_utc"))   # tfo_review_20261030 (GPT HIGH): no fills from bars before the order was armed
                    lim = p["entry"] * (1 + MAX_SLIP if L else 1 - MAX_SLIP); trig = (L and _hi >= p["entry"]) or (not L and _lo <= p["entry"])
                else:   # TFO HT 03:28 TRUE limit: buy fills only at/below limit, sell only at/above; slippage never crosses the limit
                    _hi, _lo, _op = post_extremes(m, p.get("armed_utc"))
                    lim = p["entry"]; trig = (L and _lo <= p["entry"]) or (not L and _hi >= p["entry"])
                if trig:
                    if ot == "stop_limit" and ((L and m["last"] > lim) or (not L and m["last"] < lim)): p["exec_note"] = f"{EXEC_TRADER}: price {m['last']:g} beyond limit {lim:g} - not chasing, order still working"; continue
                    if ot == "stop_limit": f = max(p["entry"], m["last"]) if L else min(p["entry"], m["last"])
                    else:   # railfix 2026-10-29: touched limit -> fill AT the limit; better only when the bar OPENED through it (gap)
                        o_ = _op; f = p["entry"]
                        if o_ is not None and ((L and o_ < f) or (not L and o_ > f)): f = float(o_)
                    _rk = abs(float(f) - float(p["stop"])); _room = (abs(float(p["target"]) - float(f)) / _rk) if _rk else 0.0
                    if ((L and float(p["target"]) <= f) or (not L and float(p["target"]) >= f) or _room < FILL_MIN_ROOM_R): p["exec_note"] = f"{EXEC_TRADER}: room to target from fill {f:g} = {_room:.2f}R < {FILL_MIN_ROOM_R}R - no fill, order still working"; continue   # stalefill: fill-time room check
                    n = (f * p["shares"] / quote_to_aud(p["ticker"], au, marks)) if is_fx(p) else aud(p["ticker"], f, au) * p["shares"]
                    c = (leg_cost_aud(p, f, p["shares"], au, marks, "entry") / n) if n else c   # tfo_costmodel_20261030
                    slip = round((f / p["entry"] - 1) * (1 if L else -1) * 1e4, 1)
                    if ot not in ("limit", "stop_limit") or slip > SLIP_COLLAR_BPS + 1e-9: p["exec_note"] = f"{EXEC_TRADER}: fill {f:g} is {slip:.1f} bps through plan {p['entry']:g} (> {SLIP_COLLAR_BPS:g} bps collar or bad order type {ot}) - rejected, order still working"; _slip_log(seat, k, p, f, slip, ot, t); ev.append((k, "blocked", "slip_collar")); continue   # tfo_review_20261030
                    if THF and not ok: p["exec_note"] = f"{EXEC_TRADER}: THF fill-time firm/kill check: {why} - no fill, order still working"; ev.append((k, "blocked", "firm_halt")); continue   # tfo_thffresh_20261030 (GPT review HIGH)
                    if THF and ((L and float(f) <= float(p["stop"])) or (not L and float(f) >= float(p["stop"]))): p["exec_note"] = f"{EXEC_TRADER}: THF fill {f:g} at/through stop {p['stop']:g} - no fill"; ev.append((k, "blocked", "fill_through_stop")); continue
                    if THF and (p.get("risk_aud") or 0) and abs(float(f) - float(p["stop"])) * p["shares"] * (1.0 / quote_to_aud(p["ticker"], au, marks) if is_fx(p) else aud(p["ticker"], 1.0, au)) > RISK_MAX * float(equity_aud(B, marks, au)) * 1.001: p["exec_note"] = f"{EXEC_TRADER}: THF fill-time risk > {RISK_MAX:.0%} cap - no fill"; ev.append((k, "blocked", "risk_cap_5pct")); continue
                    _ub = us_beta_block(D, p, f, marks, au)   # tfo_usbeta_cap_20261030: arm stays armed, no fill
                    if _ub: p.update(exec_note=f"{EXEC_TRADER}: {_ub} - no fill, order still working", us_beta_block=_ub, us_beta_block_utc=t.isoformat()); ev.append((k, "blocked", "us_beta_cap")); continue
                    _cf = prefill_seat_cap(p, f, n, float(equity_aud(B, marks, au)), au, marks)   # capfill 30 Oct (Brett HIGH): 40%/75%/margin seat cap re-checked at fill; orders armed before seatcap2 are resized down
                    if _cf == "reject": p["exec_note"] = f"{EXEC_TRADER}: seat cap - resized size rounds to zero, no fill"; ev.append((k, "blocked", "seat_cap")); continue
                    if _cf: n = (f * p["shares"] / quote_to_aud(p["ticker"], au, marks)) if is_fx(p) else aud(p["ticker"], f, au) * p["shares"]; c = (leg_cost_aud(p, f, p["shares"], au, marks, "entry") / n) if n else c; ev.append((k, "resized", _cf))
                    if not is_fx(p) and B["cash_aud"] - (sg * n + n * c) < 0:   # 2026-10-29 cashcheck: fill may never take seat cash below zero
                        p.update(status="cancelled", why="insufficient_cash", exec_note=f"{EXEC_TRADER}: insufficient_cash at fill - rejected, not resized"); B.setdefault("closed", []).append(dict(p, closed_utc=t.isoformat(), exit_reason="insufficient_cash")); ev.append((k, "blocked", "insufficient_cash")); continue
                    _pb = (p.get("pre_trade") or {}); _bw = (_pb.get("borrow") or {}) if _pb.get("borrow_estimated") and not L else {}   # borrow 2026-10-29 (TFO 19:31): non-US short, ESTIMATED
                    if _bw: _bc = n * max(float(_bw.get("fee_pa") or 0), .03) * int(_bw.get("min_charge_days") or 3) / 360.0; B["cash_aud"] -= _bc; p.update(borrow_estimated=True, borrow_fee_pa_est=_bw.get("fee_pa"), borrow_min_charge_days=int(_bw.get("min_charge_days") or 3), borrow_min_charge_aud_est=round(_bc, 2))
                    B["cash_aud"] -= (n * c) if is_fx(p) else (sg * n + n * c); p.update(status="open", fill=f, filled_utc=t.isoformat(), cost_aud=round(n * c, 2), fill_notional_aud=n, slippage_bps=slip, order=ot, order_type=ot, exec_by=EXEC_TRADER, venue_delay_min=venue_delay_min(p.get("venue")), fill_px_age_min=round(_age, 1) if _age is not None else None, stale_limit_min=_lim); ev.append((k, "filled", f))
                    try:   # railfix 2026-10-29 (GC=F): mark + exit-rail state initialised on the fill tick (no stop/target action this bar)
                        s0 = _initial_stop(p); p.update(mark=m["last"], initial_stop=s0, r0=abs(float(f) - s0), peak_fav_R=0.0, rail_state="initial", rail_hwp=float(f), scale_t1=p.get("scale_t1", True)); _rlog(p, t, "state", old=None, new="initial")
                    except Exception: pass
                elif (t - dt.datetime.fromisoformat(p["armed_utc"])).total_seconds() > ARM_HOURS * 3600: p.update(status="expired", why="never triggered"); ev.append((k, "expired", None))
                continue
            p["mark"] = m["last"]
            adv_refresh(p, t, au)   # tfo_costmodel fix: ADV refreshed on its OWN daily key (was inside the ATR block -> skipped when ATR already cached -> tier default)
            try:   # tfo_costmodel_20261030: future exit costs re-priced each tick; daily borrow on shorts
                p["mark_notional_aud"] = round(_notional_aud(p, m["last"], au, marks), 2); p["cost_rt_rate"] = round(rt_rate(dict(p, cost_rt_rate=None), au), 6)
                borrow_accrue(B, p, t)
                cm_rebaseline(p, m["last"], au, marks, t)
            except Exception: pass
            _hold = trigger_hold(p, t)   # stale mark / market closed -> giveback, full-flat (not backstop), revguard held
            _xh, _xl, _xo = post_extremes(m, p.get("filled_utc"))   # tfo_review_20261030 (GPT HIGH): exits only on bars after the fill
            hs = (L and _xl <= p["stop"]) or (not L and _xh >= p["stop"])
            if hs:   # rail rule 5: the hard stop wins if hit first (stop as it stood before this bar)
                x = gap_stop_px(p, _xo, m.get("last"))   # tfo_review_20261030 (GPT HIGH): a gap through the stop fills at the gap price, not the stop
                if x != float(p["stop"]):   # gapguard 30 Oct: flag gap-through fills honestly (exit_reason unchanged for consumers)
                    try: _gx = abs(float(p["stop"]) - x) * aud_per_unit(p["ticker"], au) * float(p["shares"])
                    except Exception: _gx = None
                    p.update(gap_through=True, stop_level=float(p["stop"]), gap_open=x, gap_extra_loss_aud=round(_gx, 2) if _gx is not None else None); ev.append((k, "stop_gap_through", x))
                gross, cc = realise(B, p, x, p["shares"], au, marks)
                p.update(status="closed", exit=x, exit_reason="stop", closed_utc=t.isoformat(), gross_aud=round(gross, 2), net_aud=round(gross - p["cost_aud"] - cc, 2))
                B["closed"].append(dict(p)); ev.append((k, "stop", x)); continue
            if m.get("last") is not None and shadow_old5(p, m["last"], au, marks): p.update(shadow_old5_px=m["last"], shadow_old5_utc=t.isoformat())   # exitC shadow
            ff = full_flat_trigger(p, m.get("last"), au, marks)
            if ff and _hold and ff != "hard_backstop_5pct": p["trigger_held"] = dict(utc=t.isoformat(), trig=ff, why=_hold); ff = None   # FULL FLAT +5% / HARD BACKSTOP -5% (2026-10-30 15:31 WITA)
            _rg = revguard_check(p, m.get("last"), au, marks) if not (ff or _hold) else None   # REVERSAL GUARD alert-only (no order/stop/size change)
            if _rg: revguard_emit(seat, k, p, t, _rg); revguard_webhook(seat, k, p, t, _rg); ev.append((k, "revguard_alert", m.get("last")))
            if ff:
                x = m["last"]; gross, cc = realise(B, p, x, p["shares"], au, marks); _rlog(p, t, ff, px=x)
                p.update(status="closed", exit=x, exit_reason=ff, closed_utc=t.isoformat(), gross_aud=round(gross, 2), net_aud=round(gross - p["cost_aud"] - cc, 2))
                B["closed"].append(dict(p)); ev.append((k, ff, x)); continue
            atr = m.get("atr14")
            if atr is None:   # cached once per WITA day per position
                dk = t.astimezone(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
                if p.get("atr14_day") != dk or (p.get("atr14") is None and str(p.get("atr14_try_utc") or "")[:13] != t.isoformat()[:13]):   # None -> retry at most hourly
                    try: a_ = ATR_FN(p["ticker"])
                    except Exception: a_ = None
                    p.update(atr14=a_, atr14_day=dk, atr14_try_utc=t.isoformat())
                atr = p.get("atr14")
            thru = rail_update(p, m, t, atr); r0 = p.get("r0") or 0
            ht = (L and _xh >= p["target"]) or (not L and _xl <= p["target"])
            half = False
            bt = None if (p.get("t1_done") or ht or (r0 and p["peak_fav_R"] >= RAIL_HALF_R)) else (bank_trigger(p, m["last"], au, marks, seat) or (None if _hold else giveback_trigger(p, m["last"], au, marks)))   # FINAL 17:20 bank + giveback
            if p.get("scale_t1") and not p.get("t1_done") and _can_half(p) and (ht or bt or (r0 and p["peak_fav_R"] >= RAIL_HALF_R)):   # rail rule 3: half at T1 / +2R / 1R A$ net / +1% basis, first wins
                x2 = p["fill"] + (1 if L else -1) * RAIL_HALF_R * r0
                at2 = r0 and p["peak_fav_R"] >= RAIL_HALF_R and (not ht or (L and x2 < p["target"]) or (not L and x2 > p["target"]))
                x = round(x2, 6) if at2 else p["target"]; h = _half(p); rsn = "rail_2R_scale_out" if at2 else "target1_scale_out"
                if bt: x = m["last"]; rsn = bt
                if bt: audit_early_bank(seat, k, p, t, rsn, x, _half(p), p["stop"], round(x2, 6) if not ht else p["target"])
                p.setdefault("shares_orig", _qty(p)); partial_close(B, p, k, x, h, au, t, rsn, "-T1", marks); t2 = p["fill"] + 2 * (p["target"] - p["fill"])
                be = p["fill"] + (1 if L else -1) * p["fill"] * rt_rate(p)
                if (L and p["stop"] < be) or (not L and p["stop"] > be): _rlog(p, t, "stop_tighten", old=p["stop"], new=round(be, 6), why="half banked -> BE"); p["stop"] = round(be, 6)
                p.update(t1=p["target"], target=round(t2, 6), t1_done=True, t1_utc=t.isoformat(), rail_state="t1_done"); _rlog(p, t, "half_bank", px=x, shares=h, reason=rsn)
                ev.append((k, rsn, x)); half = True
            q2 = None if half else t2_trigger(p, m["last"], au, marks)
            if q2:   # exitC: quarter of ORIGINAL size at net 2R; final quarter keeps trailing (stop tighten-only via rail_update)
                h2 = crypto_units(orig_shares(p) / 4.0) if _is_crypto(p) else orig_shares(p) // 4; partial_close(B, p, k, m["last"], h2, au, t, q2, "-T2", marks)
                p.update(t2_done=True, t2_utc=t.isoformat(), rail_state="t2_done"); _rlog(p, t, "quarter_bank", px=m["last"], shares=h2, reason=q2); ev.append((k, q2, m["last"])); half = True
            cur = open_R(p, m["last"])
            gb = r0 and p["peak_fav_R"] >= RAIL_GB_PEAK_R and cur <= RAIL_GB_KEEP * p["peak_fav_R"]
            if gb or thru:   # rail rule 4 giveback flatten / tightened stop already through the last price -> exit residual at the last price
                x = m["last"]; gross, cc = realise(B, p, x, p["shares"], au, marks); rsn = "giveback" if gb else "rail_stop"
                _rlog(p, t, rsn, px=x, open_R=round(cur, 3), peak_fav_R=p["peak_fav_R"])
                p.update(status="closed", exit=x, exit_reason=rsn, closed_utc=t.isoformat(), gross_aud=round(gross, 2), net_aud=round(gross - p["cost_aud"] - cc, 2))
                B["closed"].append(dict(p)); ev.append((k, rsn, x)); continue
            if half: continue
            if seat in DEX_SEATS and not p.get("dex_halfcut_done") and not p.get("t1_done") and m.get("last") is not None and not _hold:   # tfo_review_20261030 Dex: < +0.5R net within 2 sessions -> cut to half (once)
                _nr = net_R(p, float(m["last"]), au, marks); p["dex_peak_net_R"] = round(max(float(p.get("dex_peak_net_R") or -99), _nr), 4)
                if sessions_since(p.get("filled_utc"), t) >= DEX_HALFCUT_SESSIONS and p["dex_peak_net_R"] < DEX_HALFCUT_R and _can_half(p):
                    h3 = _half(p); partial_close(B, p, k, m["last"], h3, au, t, "dex_2session_halfcut", "-DXH", marks); p.setdefault("shares_orig", _qty(p) + h3)
                    p.update(dex_halfcut_done=True, dex_halfcut_utc=t.isoformat()); _rlog(p, t, "dex_halfcut", px=m["last"], shares=h3, peak_net_R=p["dex_peak_net_R"]); ev.append((k, "dex_2session_halfcut", m["last"])); continue
            if time_stop_trigger(p, m, t, au, marks, seat) == "rail_time_stop":   # TIME STOP (Brett 17:44 WITA 29 Oct 'Yes go ahead')
                x = m["last"]; cf = p["stop"]; audit_early_bank(seat, k, p, t, "rail_time_stop", x, int(p["shares"]), cf, p.get("target"))
                gross, cc = realise(B, p, x, p["shares"], au, marks)
                _rlog(p, t, "rail_time_stop", px=x, ts_peak_net_R=p.get("ts_peak_net_R"), deadline=p.get("time_stop_deadline_utc"))
                p.update(status="closed", exit=x, exit_reason="rail_time_stop", closed_utc=t.isoformat(), gross_aud=round(gross, 2), net_aud=round(gross - p["cost_aud"] - cc, 2))
                B["closed"].append(dict(p)); ev.append((k, "rail_time_stop", x)); continue
            if ht and not p.get("t1_done"):   # exitC: after the half-bank the residual is not closed at T2 price target
                x = p["target"]; gross, cc = realise(B, p, x, p["shares"], au, marks)
                p.update(status="closed", exit=x, exit_reason="target", closed_utc=t.isoformat(), gross_aud=round(gross, 2), net_aud=round(gross - p["cost_aud"] - cc, 2))
                B["closed"].append(dict(p)); ev.append((k, "target", x))
        for _p in B["positions"].values():
            if _p.get("status") == "closed" and _p.get("closed_utc") == t.isoformat() and _p.get("fill") is not None:
                shadow_write(seat, _p, t)   # exitC shadow
                try:   # tfo_review_20261030 learning loop: auto-lesson + per-seat stats on every close (never blocks a tick)
                    from veteran_desk import lesson_loop as LL; LL.on_close(seat, _p, B, _state_dir(), t)
                except Exception as _x: print("lesson_loop skipped:", type(_x).__name__)
        B["positions"] = {k: p for k, p in B["positions"].items() if p["status"] in ("armed", "open")}
    tot_eq = sum(equity_aud(B, marks, au) for B in D["seats"].values()); pk = max(D.get("peak_equity_aud") or D.get("seed_aud") or SEED_AUD, tot_eq); dd = tot_eq / pk - 1
    ks = D.get("kill_switch") or dict(state="OK")
    if dd <= KILL_FLAT_DD and ks.get("state") != "FLAT":
        for B in D["seats"].values():   # flatten every open position at mark (code, no AI)
            for k, p in list(B["positions"].items()):
                if p["status"] == "open":
                    m = marks.get(p["ticker"]) or {}; x = m.get("last") or p.get("mark") or p["fill"]; sg = 1 if p["side"] == "LONG" else -1
                    if is_fx(p): qa = quote_to_aud(p["ticker"], au, marks); n = x * p["shares"] / qa; cc = leg_cost_aud(p, x, p["shares"], au, marks, "exit"); gross = sg * (x - p["fill"]) * p["shares"] / qa; B["cash_aud"] += gross - cc
                    else: n = aud(p["ticker"], x, au) * p["shares"]; cc = leg_cost_aud(p, x, p["shares"], au, marks, "exit"); B["cash_aud"] += sg * n - cc; gross = sg * (aud(p["ticker"], x, au) - aud(p["ticker"], p["fill"], au)) * p["shares"]
                    p.update(status="closed", exit=x, exit_reason="kill_switch_flat", closed_utc=t.isoformat(), gross_aud=round(gross, 2), net_aud=round(gross - p["cost_aud"] - cc, 2)); B["closed"].append(dict(p))
                elif p["status"] == "armed": p["status"] = "expired"
            B["positions"] = {k: p for k, p in B["positions"].items() if p["status"] in ("armed", "open")}
        ks = dict(state="FLAT", at_utc=t.isoformat(), dd_pct=round(dd * 100, 2)); ka = KILL_ALERT_PATH or os.path.join(_state_dir(), "KILL_ALERT.json"); os.makedirs(os.path.dirname(ka), exist_ok=True)
        json.dump(dict(ks, msg="VETERAN DESK KILL SWITCH: -12% from peak. All positions flattened, new trades halted. Brett must reset (delete state/veteran_desk/KILL_ALERT.json and set kill_switch.state OK)."), open(ka, "w"), indent=1)
    elif ks.get("state") != "FLAT": ks = dict(state="HALVED" if dd <= KILL_HALVE_DD else "OK", dd_pct=round(dd * 100, 2))
    D.update(peak_equity_aud=round(pk, 2), drawdown_pct=round(dd * 100, 2), kill_switch=dict(ks, rules="-8% from peak halves sizes; -12% goes flat + alerts Brett"), posture=dict(news_regime=news_flag, fund_posture=post, size_multiplier=mult))
    if not HEDGE_ENABLED:   # VIX hedge removed (Brett 06:41 WITA 29 Oct): close any open tranche once, then never fetch VIX / never stage again
        H = D.get("hedge") or {}
        if not H.get("disabled"): H = hedge_close_all(D, H, t, "Brett 2026-10-29 06:41 WITA: getting rid of VIX completely - not approved"); ev.append(("hedge", "closed_disabled", H.get("closed_realised_aud")))
        D["hedge"] = H
    else:
        # tail hedge overlay (paper VIX-call proxy): premium budget by regime, valued by VIX ratio; model only
        try:
            from veteran_desk import feeds as FD
            vm = FD.vix_live() or marks.get("^VIX") or FD.px_with_fallback("^VIX", live_px) or {}   # live VIX first (Yahoo -> CBOE), time-stamped
            vix = vm.get("last")
        except Exception: vm, vix = {}, None
        H = D.get("hedge") or {}
        if vix and vm.get("delayed"):   # 2026-10-28 fix: never revalue/open the hedge on a prior-day FRED close vs a live ref (fabricated +A$7.5k)
            if H.get("value_src") != "live":
                H["value_aud"] = H.get("premium_aud", 0.0); H["value_src"] = "cost"   # no fresh live mark ever -> carry at cost, not a stale-close P&L
            H["mark_note"] = f"VIX live quote unavailable/stale; best {vm.get('src')} asof {vm.get('asof')} ({vm.get('stale_why', 'stale')}) - hedge held at last live mark / cost"
            vix = None
            if H.get("premium_aud"): H["pnl_aud"] = round(H["value_aud"] - H["premium_aud"], 2)
        elif vix: H.pop("mark_note", None); H["value_src"] = "live"
        H.update(vix_last=vm.get("last"), vix_asof=vm.get("asof"), vix_src=vm.get("src"), vix_stale=bool(vm.get("delayed")) if vm else True, vix_stale_why=vm.get("stale_why"))
        H = hedge_step(D, H, marks, au, tot_eq, t, vix, vm)   # TFO HT 03:15 Brett: staged exposure-driven hedge (legacy overlay closed at first liquid mark)
        D["hedge"] = H
    try:
        from veteran_desk import payouts_bridge; payouts_bridge.book_commissions(D, **({} if os.path.abspath(BOOK) == os.path.abspath(_REAL_BOOK) else dict(out_path=_temp_payouts_path(), **(dict(fwd_path=os.path.join(THF_DIR, "forward_trades.json"), score_path=os.path.join(THF_DIR, "thf_scorecard.json")) if THF else {}))))   # tfo_thffresh_20261030: THF commissions from THF journals only   # TFO HT 03:35: temp books never touch live payouts; commission = expense in each seat's own book
    except ImportError: pass
    try:   # 2026-10-30 dashfix: sync funder queue 'armed' entries that the book already filled
        _Q = FG.load(queue_path)
        for _i, _e in (_Q.get("entries") or {}).items():
            if _e.get("state") != "armed" or _i in upd: continue
            for S_ in D["seats"].values():
                _p = (S_.get("positions") or {}).get(_i)
                if _p and _p.get("status") == "open": upd[_i] = dict(state="filled", filled_utc=_p.get("filled_utc") or _p.get("entry_utc"), why="book fill sync"); break
                _c = [c for c in (S_.get("closed") or []) if c.get("id") == _i and c.get("filled_utc") and c.get("status") not in ("void", "cancelled")]
                if _c: upd[_i] = dict(state="filled", filled_utc=_c[-1].get("filled_utc"), why="book fill sync (since closed)"); break
    except Exception as _e: print("queue fill sync skipped:", _e)
    _wj(BOOK, D)   # tfo_review_20261030 (GPT HIGH): ledger committed BEFORE the queue transitions (crash -> queue still WAIT, re-arm is idempotent by id); final save below
    if upd:
        def f(Q):
            for i, v in upd.items():
                if i in Q["entries"] and Q["entries"][i].get("state") != "withdrawn": Q["entries"][i].update(v, applied_utc=t.isoformat())   # withdraw-fix: never clobber a withdraw made mid-tick
                elif i in Q["entries"] and v.get("state") == "armed": withdrawn_mid_tick.append(i)
        withdrawn_mid_tick = []; FG.update(f, queue_path)
        for i in withdrawn_mid_tick:   # withdraw landed between our queue read and write: cancel the arm we just made
            for S_ in D["seats"].values():
                p_ = S_["positions"].get(i)
                if p_ and p_.get("status") == "armed": p_.update(status="cancelled", why="queue withdrawn mid-tick"); S_.setdefault("closed", []).append(dict(p_, closed_utc=t.isoformat(), exit_reason="withdrawn")); S_["positions"].pop(i, None)
    try:   # daily leaderboard prizes (Brett 2026-10-28 20:13 WITA): credited as CAPITAL events, never P&L; idempotent by award id
        from veteran_desk import leaderboard as LB
        if os.path.abspath(BOOK) == os.path.abspath(_REAL_BOOK): LB.apply_prizes(D, au, t)
    except Exception as x: D["leaderboard_error"] = "%s: %s" % (type(x).__name__, str(x)[:160])
    tot = dict(equity_aud=0.0, cash_aud=0.0, invested_aud=0.0, commission_expense_aud=0.0, spec_pnl_aud=0.0)
    day = t.astimezone(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
    for seat, B in D["seats"].items():
        eq = equity_aud(B, marks, au); inv = sum(p["shares"] * aud(p["ticker"], (marks.get(p["ticker"]) or {}).get("last") or p.get("fill") or p["entry"], au) for p in B["positions"].values() if p["status"] == "open")
        if B.get("day") != day: B.update(day=day, day_start_equity_aud=round(eq, 2))
        spec = sum(c.get("net_aud", 0) for c in B["closed"] if c.get("sleeve") == "spec") + sum((1 if p["side"] == "LONG" else -1) * p["shares"] * (aud(p["ticker"], p.get("mark") or p["fill"], au) - aud(p["ticker"], p["fill"], au)) for p in B["positions"].values() if p["status"] == "open" and p.get("sleeve") == "spec")
        sl = [p["slippage_bps"] for p in B["positions"].values() if "slippage_bps" in p] + [c["slippage_bps"] for c in B["closed"] if "slippage_bps" in c]
        B.update(spec_pnl_aud=round(spec, 2), spec_sleeve_aud=SEAT_SEED * SPEC_SLEEVE_PCT, avg_slippage_bps=round(sum(sl) / len(sl), 1) if sl else None)
        B.update(equity_aud=round(eq, 2), invested_aud=round(inv, 2), pnl_since_inception_aud=round(eq - B["seed_aud"], 2), today_pnl_aud=round(eq - B["day_start_equity_aud"], 2))
        B["pnl_hwm_aud"] = round(max(0.0, B.get("pnl_hwm_aud") or 0.0, B["pnl_since_inception_aud"]), 2)   # TFO HT 23:13: floor 0.00 (starting P/L)   # seat P/L high-water mark ex capital events (weekly prize guard, Brett 20:23 WITA)
        for k in tot: tot[k] += B.get(k, 0) or 0
    tot["equity_aud"] += (D.get("hedge") or {}).get("pnl_aud", 0) or 0
    tot["equity_aud"] += D.get("hedge_realised_aud") or 0.0   # realised hedge P&L (incl. real costs) stays in desk equity - never made to disappear
    tot["equity_aud"] += D.get("hedge_error_reversal_aud") or 0.0   # Brett 06:44 WITA 29 Oct: unauthorised VIX hedge reversed to TFO error account (dated adjustment, see archive/error_account.json)
    if D.get("day") != day: D.update(day=day, day_start_equity_aud=round(tot["equity_aud"], 2))
    D.update({k: round(v, 2) for k, v in tot.items()}, pnl_since_inception_aud=round(tot["equity_aud"] - (D.get("seed_aud") or SEED_AUD), 2), today_pnl_aud=round(tot["equity_aud"] - D["day_start_equity_aud"], 2),
             audusd=au, equity=round(tot["equity_aud"] * au, 2), cash=round(tot["cash_aud"] * au, 2), peak=max(D.get("peak") or 0, round(tot["equity_aud"] * au, 2)), equity_usd=round(tot["equity_aud"] * au, 2), cash_usd=round(tot["cash_aud"] * au, 2), peak_usd=max(D.get("peak") or 0, round(tot["equity_aud"] * au, 2)), usd_fields_note="equity/cash/peak are USD (legacy keys kept for firm readers); *_usd are the same values; *_aud are AUD", updated=t.isoformat(),
             last_events=ev, last_events_utc=t.isoformat(), prev_events=(D.get("last_events") or []) if not ev else D.get("prev_events", []))   # this tick's events only (old 'ev or previous' carried stale 21:06 'armed' lines into later ticks)
    _wj(BOOK, D)
    try:   # fix 3/7: refresh leaderboard standings from THIS tick's book every real tick, so leaderboard == seat_cli (no stale snapshot)
        if os.path.abspath(BOOK) == os.path.abspath(_REAL_BOOK):
            from veteran_desk import leaderboard as LB2; LB2._wj(LB2.LIVE, LB2.standings(D, None, t))
    except Exception as x: D["leaderboard_error"] = "%s: %s" % (type(x).__name__, str(x)[:160])
    try:   # learning loop (Brett 2026-10-28 20:26 WITA): journal every fill/close + shadow runner; read-only on the book, never blocks a tick
        if os.path.abspath(BOOK) == os.path.abspath(_REAL_BOOK):
            from veteran_desk import learning as LRN; LRN.sync(D)
    except Exception: pass
    try:   # ancillary staff pool (Brett 20:21 WITA): reporting/accrual only, never touches seat books
        from veteran_desk import staff_pool as SP
        if os.path.abspath(BOOK) == os.path.abspath(_REAL_BOOK): SP.update(D, t)
    except Exception as x: D["staff_pool_error"] = "%s: %s" % (type(x).__name__, str(x)[:160])
    try:   # weekly seat score: forward snapshot (max 1/hour) then score; temp books (tests) write beside the temp book, never to real state
        from veteran_desk import seat_weekly_score as WS
        real = os.path.abspath(BOOK) == os.path.abspath(_REAL_BOOK); sp = None if real else BOOK + ".snapshots.jsonl"; op = None if real else BOOK + ".weekly.json"
        WS.snapshot(now=t, book_path=BOOK, snap_path=sp); WS.score(now=t, book_path=BOOK, snap_path=sp, out_path=op)
    except Exception as x: D["weekly_score_error"] = "%s: %s" % (type(x).__name__, str(x)[:160])
    return D
if __name__ == "__main__":
    b = tick() if (sys.argv[1:] or ["tick"])[0] == "tick" else load()
    print(json.dumps({k: b.get(k) for k in ("equity_aud", "cash_aud", "invested_aud", "pnl_since_inception_aud", "today_pnl_aud", "commission_expense_aud", "last_events")}, default=str))
