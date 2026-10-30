"""Veteran desk risk gate v2 (Ada Stroud). Code only, PAPER, NEVER arms; every pass still needs Brett's tick before book.py may open it.
2026-10-28 Brett: gate must not be too strict and must use all the point-in-time history.
History: data/pit/pit.sqlite (read-only) with fallback to data/pit_staging/* (available_at_utc <= asof; no lookahead). Longest PIT series wins.
HARD (block): banned asset, B grade, bad side, stop missing/wrong side/inside noise (<0.5 ATR) or absurdly wide (>6 ATR), R:R<min,
  correlated-heat cap, ADV>5% (cannot get out), crypto short, borrow explicitly refused, kill switch FLAT, firm HALT,
  history base rate clearly negative on enough samples, challenger-opposite AND advocate factual kill together.
WARN (size-down, never block): missing data of any kind, stale data, one LLM objection, stop narrower than normal 10d noise, gap risk,
  moderate correlation, ADV 1-5%, mildly negative base rate, chop, chase."""
import datetime as dt, glob, json, math, os, sqlite3
import numpy as np
from veteran_desk import pit as P
BASE = P.BASE; UTC = dt.timezone.utc; ST = os.path.join(BASE, "data", "pit_staging")
VERSION = "gate_v2_20261030_review"
LIVE_CHASE_MAX = 0.01   # same 1% as desk.LIVE_CHASE_MAX (stop-limit entries only)
from veteran_desk import cost_tables as CT   # ONE SOURCE (tfo_costmodel_20261030)
COST_RT = CT.venue_rt_table()   # derived venue RT (legacy shape)
TH = dict(stop_min_atr=0.5, stop_max_atr_hard=6.0, stop_max_atr_soft=6.0, stop_tight_soft=1.0, rr_min=1.5, entry_far_pct=0.03, entry_far_atr=1.5,
          choppy_er_soft=0.25, chop_mult=1.0, chase_mult=0.75, chase_ret5=0.12, chase_ret20=0.25, chase_sma_atr=3.0, stale_days=4.0,
          adv_soft=0.01, adv_hard=0.05, min_value_aud=500000.0, min_bars=60, corr_hi=0.7, heat_cap=0.15, seat_equity_aud=500000.0,
          base_n_min=40, base_block_exp=-0.30, base_half_exp=-0.15, base_soft_exp=0.0, horizon=10, min_mult=0.25,
          chal_agree_needed_for_3pct=True)
HARD_RULES = ["stop on every trade (correct side, >=0.5 ATR, <=6 ATR)", "risk per trade 1-5% of seat equity in book.py (A 1-2%: 1% allowed until proven, never force up; 1.5% when 40% single-stock cap binds, A+ up to 4%, hard cap 5%; crypto 0.5-2%)",
              "one sizing source: book.risk_plan (A 1-2% never force up, A+ 4%; overrides half size; sub-1% rejected - tfo_review_20261030)", "fee filter: expected move to T1 >= 3x round-trip costs", "every entry limit/stop-limit, 15 bps slip collar", "firm rails HALT", "correlated heat cap 15%", "kill switch FLAT", "Brett tick before arming", "gate never arms"]
# ---------------- history (PIT) ----------------
_IDX = {}
def _staging_index():
    if _IDX: return _IDX
    for g in ("us_ca", "europe_apac"):
        for f in glob.glob(os.path.join(ST, g, "**", "*.parquet"), recursive=True):
            if "/_work/" in f or "/filings/" in f: continue
            _IDX.setdefault(os.path.basename(f)[:-8], f)
    for f in glob.glob(os.path.join(ST, "macro_fx_cmdty_crypto", "series", "*.csv")):
        try:
            with open(f) as fh: fh.readline(); s = fh.readline().split(",")[0].strip()
            if s: _IDX.setdefault(s, f)
        except Exception: pass
    return _IDX
def _iso(asof): return asof.astimezone(UTC).isoformat() if isinstance(asof, dt.datetime) else str(asof)
def _sqlite_rows(sym, asof):
    try:
        c = sqlite3.connect("file:%s?mode=ro" % P.DB, uri=True, timeout=15)
        r = c.execute("SELECT date,o,h,l,c,v,knowable_utc FROM bars WHERE sym=? AND knowable_utc<=? ORDER BY date", (sym, _iso(asof))).fetchall(); c.close(); return r
    except Exception: return []
def _staging_rows(sym, asof):
    f = _staging_index().get(sym)
    if not f: return []
    try:
        import pandas as pd
        d = pd.read_parquet(f) if f.endswith(".parquet") else pd.read_csv(f)
        a = pd.to_datetime(d["available_at_utc"], utc=True, errors="coerce"); d = d[a.notna() & (a <= pd.Timestamp(_iso(asof)))].copy()
        d = d[(d["low"] > 0) & (d["high"] >= d["low"])].drop_duplicates("date").sort_values("date")
        return [(str(r.date)[:10], float(r.open), float(r.high), float(r.low), float(r.close), float(r.volume or 0), str(r.available_at_utc)) for r in d.itertuples()]
    except Exception: return []
# 2026-10-29 crypto mapping fix: bare BTC/ETH/XRP resolved to the US-listed ETF rows in pit.sqlite (BTC=Grayscale mini ~$35, ETH ~$22, XRP ~$15)
# -> ATR ~$0.8 and a meaningless base rate. Crypto tickers always grade on the Kraken spot series.
CRYPTO_ALIAS = {"BTC": "XBTUSD", "XBT": "XBTUSD", "ETH": "ETHUSD", "XRP": "XRPUSD", "SOL": "SOLUSD", "ADA": "ADAUSD", "LINK": "LINKUSD", "DOGE": "DOGEUSD", "LTC": "LTCUSD", "DOT": "DOTUSD", "AVAX": "AVAXUSD"}
def canon(sym, crypto_hint=False):
    s = str(sym or "").upper().strip()
    if s.startswith("KRAKEN:"): return s
    for sep in ("-USD", "/USD", "USD", "-AUD"):
        if s.endswith(sep) and s[: -len(sep)] in CRYPTO_ALIAS: return "KRAKEN:" + CRYPTO_ALIAS[s[: -len(sep)]]
    if s in ("XBTUSD", "ETHUSD", "XRPUSD", "SOLUSD"): return "KRAKEN:" + s
    if s in ("BTC", "XBT", "ETH", "XRP") or (crypto_hint and s in CRYPTO_ALIAS): return "KRAKEN:" + CRYPTO_ALIAS[s]   # bare BTC/ETH/XRP = the coins on this desk (US rows BTC/ETH/XRP are an ETF/stock)
    return sym
# 2026-10-29 stale-bar fix: grade on fresh bars. If the newest stored bar is behind the last finished session, top it up from Yahoo
# (chart endpoint fallback, IBKR gaps) before grading. Only for live grading (asof within 2h of now); never in tests unless forced.
FRESHEN = not os.environ.get("PYTEST_CURRENT_TEST")
def freshen(sym, asof):
    try:
        if not FRESHEN or sym.startswith("KRAKEN:") or abs((dt.datetime.now(UTC) - asof).total_seconds()) > 7200: return None
        c = P.con()
        try:
            if not P.is_stale(c, sym): return None
            ok, fail = P.refresh_symbols([sym], "1mo", c); return dict(refreshed=sym, ok=ok, fail=fail)
        finally: c.close()
    except Exception as e: return dict(refreshed=sym, error=str(e)[:120])
NON_AUD_USD_SUFFIX = ("HK", "T", "KS", "KQ", "TW", "TWO", "SI", "L", "PA", "DE", "AS", "BR", "LS", "SW", "ST", "CO", "HE", "OL", "MC", "MI", "IR", "VI", "NS", "BO", "NZ", "TO", "V", "SS", "SZ")
def ccy_issue(sym, au=None, px_fn=None, ibkr_fn=None):
    """fxsize 2026-10-29: replaces the 10:17 HKD/JPY blanket reject. book.aud() now converts the listing currency with live FX
    (Yahoo first, IBKR fallback). Backstop only: returns a reason (hard reject) when the suffix is unknown or no live FX rate exists."""
    from veteran_desk import book as BK
    s = str(sym or "")
    if s.startswith("KRAKEN:") or s.endswith("=X") or s.endswith("=F") or s.startswith("^") or "." not in s: return None
    c = BK.listing_ccy(s)
    if c is None: return "unknown listing currency for .%s - cannot size in AUD (fail closed)" % s.rsplit(".", 1)[-1].upper()
    if c in ("AUD", "USD"): return None
    try: BK.aud_per_unit(s, au or BK.audusd(), px_fn, ibkr_fn); return None
    except BK.FxUnavailable as x: return str(x)
def history(sym, asof):
    """All bars knowable at asof. Returns (rows, source)."""
    sym = canon(sym); freshen(sym, asof)
    a = _sqlite_rows(sym, asof); b = _staging_rows(sym, asof) if len(a) < 2000 else []
    return (b, "pit_staging") if len(b) > len(a) else (a, "pit.sqlite")
def _shift(x, k): return np.r_[np.full(k, np.nan), x[:-k]] if k < len(x) else np.full(len(x), np.nan)
def _roll(x, n):
    cs = np.cumsum(np.r_[0.0, x]); out = np.full(len(x), np.nan)
    if len(x) >= n: out[n - 1:] = (cs[n:] - cs[:-n]) / n
    return out
def arrays(rows):
    o, h, l, c, v = (np.array([float(r[k] or 0) for r in rows]) for k in range(1, 6))
    pc = np.r_[c[0], c[:-1]]; tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atr = _roll(tr, 14); s20 = _roll(c, 20); s50 = _roll(c, 50)
    path = _roll(np.abs(np.r_[0.0, np.diff(c)]), 20) * 20; net = np.abs(c - _shift(c, 20))
    with np.errstate(invalid="ignore", divide="ignore"):
        er = net / np.where(path > 0, path, np.nan); ret5 = c / _shift(c, 5) - 1; ret20 = c / _shift(c, 20) - 1
    st = np.where((c > s20) & (s20 > s50), 1, np.where((c < s20) & (s20 < s50), -1, 0))
    return dict(dates=[r[0] for r in rows], o=o, h=h, l=l, c=c, v=v, atr=atr, s20=s20, s50=s50, er=er, ret5=ret5, ret20=ret20, trend=st)
def outcomes(A, side, k, rr, H=10, cost=0.001):
    """Net R of entering at close j with stop k ATR and target rr*k ATR, H-day time stop; stop checked first, gaps through stop filled at open."""
    c, h, l, o, atr = A["c"], A["h"], A["l"], A["o"], A["atr"]; n = len(c); m = n - H; out = np.full(n, np.nan)
    if m <= 0: return out
    idx = np.arange(m)[:, None] + np.arange(1, H + 1)[None, :]; a = atr[:m, None]; b = c[:m, None]; sg = 1 if side == "LONG" else -1
    with np.errstate(invalid="ignore", divide="ignore"):
        adv = (b - l[idx]) / a if sg == 1 else (h[idx] - b) / a; fav = (h[idx] - b) / a if sg == 1 else (b - l[idx]) / a
        gap = ((b - o[idx]) / a if sg == 1 else (o[idx] - b) / a)
        hs = adv >= k; ht = fav >= rr * k; BIG = H + 5
        ds = np.where(hs.any(1), hs.argmax(1), BIG); dtg = np.where(ht.any(1), ht.argmax(1), BIG)
        stop_loss = -np.maximum(1.0, gap[np.arange(m), np.minimum(ds, H - 1)] / k)
        fin = sg * (c[idx[:, -1]] - c[:m]) / atr[:m] / k
        R = np.where(ds <= dtg, np.where(ds < BIG, stop_loss, fin), rr)
        R = R - cost * c[:m] / (k * atr[:m])
    out[:m] = R; return out
# ---------------- book / environment ----------------
def _rj(p, d=None):
    try: return json.load(open(p))
    except Exception: return d
def book_positions():
    B = _rj((os.path.join(BASE, "state", "thf", "thf_book.json") if os.environ.get("THF_DESK") == "1" else os.path.join(BASE, "reports", "veteran_desk_book.json")), {}) or {}; out = []
    for seat, L in (B.get("seats") or {}).items():
        for pid, p in (L.get("positions") or {}).items():
            if p.get("status") == "open": out.append(dict(ticker=p.get("ticker") or p.get("sym"), side=p.get("side"), risk_aud=float(p.get("risk_aud") or 0), seat=seat))
    return out, (B.get("kill_switch") or {}).get("state", "UNKNOWN")
def firm_state():
    f = _rj(os.path.join(BASE, "reports", "firm_risk.json"))
    if f is None: return None
    return "HALT" if (f.get("new_entries_halted") or str(f.get("level")).upper() == "HALT") else str(f.get("level"))
def _corr(a, b, n=250):
    ra = dict(zip(a["dates"][1:], np.diff(np.log(a["c"])))); rb = dict(zip(b["dates"][1:], np.diff(np.log(b["c"]))))
    ds = sorted(set(ra) & set(rb))[-n:]
    if len(ds) < 60: return None
    x = np.array([ra[d] for d in ds]); y = np.array([rb[d] for d in ds])
    if x.std() == 0 or y.std() == 0: return None
    return float(np.corrcoef(x, y)[0, 1])
# ---------------- the gate ----------------
DEX_SEATS = ("trend", "price_action")
G10 = ("USD", "EUR", "JPY", "GBP", "CHF", "CAD", "AUD", "NZD", "NOK", "SEK")
def deep_otc(t, venue):
    """tfo_seatpipe_g10fx_20261030: G10 spot crosses (=X, both legs G10) and listed liquid futures roots (fut_roll.ROLL) trade far above A$500k/day;
    Yahoo 'volume' for these is not traded value (FX tick counts / contract counts without multiplier). Exempt them from the ADV floor ONLY."""
    u = (t or "").upper()
    if venue == "FX" and u.endswith("=X"):
        b = u[:-2]; b = ("USD" + b) if len(b) == 3 else b
        return len(b) == 6 and b[:3] in G10 and b[3:] in G10
    if venue == "FUT" and u.endswith("=F"):
        try:
            from veteran_desk import fut_roll as _FR; return u[:-2] in _FR.ROLL
        except Exception: return False
    return False
def gate(p, f, chal, da, asof, run_risk_used=0, others=None, au=0.65, th=None, hist=None, env=None, quote=None):
    """Returns dict(passed, reasons=HARD blocks, warnings, size_mult, risk_aud, rr, history, version). Never arms."""
    T = dict(TH, **(th or {})); hard = []; warn = []; mult = [1.0]; atr_cut = [1.0]
    def H_(cond, msg):
        if not cond: hard.append(msg)
    def W(msg, m=1.0): warn.append(msg); mult[0] *= m
    t = p.get("ticker") or ""; side = p.get("side")
    try: e, s, tg = (float(p.get(k) or 0) for k in ("entry", "stop", "target"))
    except Exception: e = s = tg = 0.0
    t = canon(t, crypto_hint=str(p.get("market") or p.get("venue") or "").upper() == "CRYPTO" or "crypto" in str(p.get("seat") or "")); venue = P.venue_of(t); spec = p.get("sleeve") == "spec"
    cq = ccy_issue(t, au); H_(cq is None, "listing-currency sizing: %s" % cq)
    H_(not P.banned(t), "Indonesian asset banned")
    _seat = p.get("_seat") or T.get("seat")   # tfo_seatpipe_dexrail_20261030 (TFO 18:20 WITA): across Dex's two seats, max ONE open-or-armed position per name
    if _seat in DEX_SEATS:
        _B = _rj((os.path.join(BASE, "state", "thf", "thf_book.json") if os.environ.get("THF_DESK") == "1" else os.path.join(BASE, "reports", "veteran_desk_book.json")), {}) or {}
        _held = [(sn, q.get("status")) for sn in DEX_SEATS for q in ((_B.get("seats") or {}).get(sn, {}).get("positions") or {}).values()
                 if q.get("status") in ("open", "armed") and canon(q.get("ticker") or q.get("sym") or "") == t]
        _held += [(q.get("_seat"), "same-run") for q in (others or []) if q.get("_seat") in DEX_SEATS and canon(q.get("ticker") or "") == t]
        H_(not _held, "Dex one-name rail: %s already held across Dex seats (%s)" % (t, ", ".join("%s %s" % x for x in _held[:3])))
    if venue == "CRYPTO":   # tfo_seatpipe_kaifloor_20261030 (TFO 18:20 WITA): every Kai proposal must clear A$500k on the LIVE Kraken 24h sweep (all ~623 USD pairs swept)
        try:
            from veteran_desk import desk as _D; _r = _D.sweep_row_for(t)
        except Exception: _r = None
        _v = float((_r or {}).get("vol24h_aud") or 0)
        H_(_v >= T["min_value_aud"], "Kraken 24h traded value A$%.0f < A$%.0f floor (live sweep%s)" % (_v, T["min_value_aud"], "" if _r else ": pair not in sweep"))
    H_(p.get("grade") in ("A+", "A"), "grade %s - B or worse is a pass (playbook)" % p.get("grade"))
    H_(side in ("LONG", "SHORT"), "bad side")
    H_(s > 0 and ((side == "LONG" and s < e < tg) or (side == "SHORT" and tg < e < s)), "stop missing or stop/target not on correct sides of entry")
    dist = abs(e - s); rr = abs(tg - e) / dist if dist else 0.0
    H_(rr >= T["rr_min"], "reward:risk %.2f < %.1f" % (rr, T["rr_min"]))
    # --- 2026-10-29 exthours: freshest US print (regular / pre / post, newest within 30 min) for through-stop, no-chase and room ---
    XQ = quote
    if XQ is None and not os.environ.get("PYTEST_CURRENT_TEST"):
        try:
            from veteran_desk import feeds as FD
            XQ = FD.ext_quote(t)
        except Exception: XQ = None
    lx = float(XQ["last"]) if isinstance(XQ, dict) and XQ.get("last") else None; lses = (XQ or {}).get("session") if lx else None
    live = None
    if lx and s > 0 and e > 0 and side in ("LONG", "SHORT"):
        L_ = side == "LONG"; tag = "%s %s @ %s" % (XQ.get("src"), lses, XQ.get("asof"))
        H_(not ((L_ and lx <= s) or (not L_ and lx >= s)), "live %g already through stop %g (%s print, %s)" % (lx, s, lses, tag))
        ch_l = (lx / e - 1) if L_ else (1 - lx / e)
        H_(not (str(p.get("order_type") or "").lower() == "stop_limit" and ch_l > LIVE_CHASE_MAX), "live %g is %.1f%% through stop-entry %g (%s print) - re-propose from the live price" % (lx, ch_l * 100, e, lses))
        rd = abs(lx - s); room = (abs(tg - lx) / rd) if rd else None
        if ((L_ and lx >= tg) or (not L_ and lx <= tg)): W("live %g (%s print) already past target %g - no room left" % (lx, lses, tg))
        elif room is not None and room < T["rr_min"]: W("room from live %g (%s print): %.2fR to target < %.1fR" % (lx, lses, room, T["rr_min"]))
        live = dict(last=lx, session=lses, asof=XQ.get("asof"), src=XQ.get("src"), market_state=XQ.get("market_state"), fresh=XQ.get("fresh"), fallback=XQ.get("fallback"),
                    chase_pct=round(ch_l, 4), room_R=round(room, 2) if room is not None else None, candidates=XQ.get("candidates"))
    # --- history ---
    rows, src = hist if hist is not None else history(t, asof)
    hs = dict(source=src, bars=len(rows), first=rows[0][0] if rows else None, last=rows[-1][0] if rows else None)
    A = arrays(rows) if len(rows) >= 60 else None
    H_(len(rows) > 0, "no point-in-time price history for %s - cannot verify stop, gap or base rate (2026-10-29 rule)" % t)
    H_(len(rows) == 0 or len(rows) >= T["min_bars"], "insufficient PIT history for %s: %d bars < %d min (all venues; Marcus 2026-10-29 13:08 WITA, was soft x0.25)" % (t, len(rows), T["min_bars"]))
    if A is None:
        W("NO/short PIT history (%d bars) - stop sanity, gap, base rate unverified: size x0.25" % len(rows), 0.25)
        a = (f or {}).get("atr14") or 0
    else:
        a = float(A["atr"][-1]); hs.update(atr14=round(a, 6), close=float(A["c"][-1]))
    if a and a > 0 and dist > 0:
        k = dist / a; hs["stop_atr"] = round(k, 2)
        if venue == "CRYPTO" and not spec and not (T["stop_min_atr"] <= k <= T["stop_max_atr_hard"]):   # kairails 14:28 WITA: no ATR-band skip for crypto; size toward the 0.5% floor
            W("crypto stop %.2f ATR outside %.1f-%.0f ATR band: sized down toward 0.5%% floor (x0.5), not skipped" % (k, T["stop_min_atr"], T["stop_max_atr_hard"]), .5); atr_cut[0] *= .5
        if venue != "CRYPTO" or spec:
            H_(k >= T["stop_min_atr"], "stop %.2f ATR inside one day's noise (<%.1f ATR)" % (k, T["stop_min_atr"]))
            H_(k <= T["stop_max_atr_hard"], "stop %.1f ATR - wider than %.0f ATR is not a stop" % (k, T["stop_max_atr_hard"]))
        if T["stop_max_atr_soft"] < k <= T["stop_max_atr_hard"]: W("wide stop %.1f ATR: size x0.75" % k, .75)
        if T["stop_min_atr"] <= k < T["stop_tight_soft"]: W("tight stop %.2f ATR (history: <1 ATR stops lose ~0.15R more): size x0.5" % k, .5)
        if spec: H_(k <= 1.5 and rr >= 3, "spec sleeve needs tight stop <=1.5 ATR and >=3:1 payoff")
    else: k = None
    if A is not None and k:
        c = A["c"]; n = len(c); sg = 1 if side == "LONG" else -1
        lr = np.diff(np.log(c)); vol20 = float(np.std(lr[-20:]) * math.sqrt(252)) if n > 21 else None
        peak = np.maximum.accumulate(c); maxdd = float((c / peak - 1).min()); trough = np.minimum.accumulate(c); maxru = float((c / trough - 1).max())
        idx = np.arange(max(0, n - 2520), n - T["horizon"])
        if len(idx) > 50:
            w = np.array([(c[i] - A["l"][i + 1:i + 1 + T["horizon"]].min()) / A["atr"][i] if sg == 1 else (A["h"][i + 1:i + 1 + T["horizon"]].max() - c[i]) / A["atr"][i] for i in idx])
            w = w[np.isfinite(w)]; mae50, mae90 = float(np.percentile(w, 50)), float(np.percentile(w, 90))
        else: mae50 = mae90 = None
        with np.errstate(invalid="ignore", divide="ignore"):
            g = sg * (np.r_[np.nan, A["o"][1:] - c[:-1]]) * -1 / np.r_[np.nan, A["atr"][:-1]]   # adverse overnight gap in ATR
        g = g[np.isfinite(g)][-2520:]; gp99 = float(np.percentile(g, 99)) if len(g) > 50 else None; gthru = float((g >= k).mean()) if len(g) > 50 else None
        hs.update(realised_vol20_ann=round(vol20, 3) if vol20 else None, max_drawdown=round(maxdd, 3), max_runup=round(maxru, 3),
                  mae10_p50_atr=round(mae50, 2) if mae50 else None, mae10_p90_atr=round(mae90, 2) if mae90 else None,
                  gap_p99_adverse_atr=round(gp99, 2) if gp99 is not None else None, gap_through_stop_freq=round(gthru, 4) if gthru is not None else None)
        if mae50 and k < 0.6 * mae50: W("stop %.2f ATR is under 60%% of a normal 10-day adverse move (%.2f ATR) - likely noise-stopped: size x0.75" % (k, mae50), .75)
        if gthru is not None and gthru > 0.02: W("overnight gaps went through a stop this wide on %.1f%% of days - worst p99 gap %.1f ATR: size x0.75" % (gthru * 100, gp99), .75)
        # base rate of similar setups on this instrument (same side, same trend state), full history, completed outcomes only
        R = outcomes(A, side, k, max(rr, 0.1), T["horizon"], CT.est_rt_rate(t, venue, float(A["c"][-1]))); st = A["trend"]; cur = st[-1]
        sel = np.isfinite(R) & (st == cur); sel[-T["horizon"]:] = False
        nb = int(sel.sum()); exp = float(np.mean(R[sel])) if nb else None; wr = float(np.mean(R[sel] > 0)) if nb else None
        hs["base_rate"] = dict(n=nb, win_rate=round(wr, 3) if wr is not None else None, exp_R_net=round(exp, 3) if exp is not None else None, trend_state=int(cur), horizon_days=T["horizon"])
        if nb >= T["base_n_min"] and exp is not None:
            H_(exp > T["base_block_exp"], "history: %d similar setups lost %.2fR on average (win %.0f%%) - real negative edge" % (nb, exp, wr * 100))
            H_(exp >= 0, "history: similar setups negative (%.2fR, n=%d) - rejected, not lifted to the 2%% floor (Brett 29 Oct)" % (exp, nb))
            if T["base_block_exp"] < exp <= T["base_half_exp"]: W("history: similar setups negative (%.2fR, n=%d): size x0.5" % (exp, nb), .5)
            elif T["base_half_exp"] < exp < T["base_soft_exp"]: W("history: similar setups slightly negative (%.2fR, n=%d): size x0.75" % (exp, nb), .75)
        elif nb < T["base_n_min"]: W("history: only %d similar setups - base rate unproven: size x0.75" % nb, .75)
        er = A["er"][-1]
        if np.isfinite(er) and er < T["choppy_er_soft"]: W("choppy tape (efficiency %.2f) - info only, history shows no edge loss from chop" % er, T["chop_mult"])
        r5, r20 = A["ret5"][-1], A["ret20"][-1]; s20 = A["s20"][-1]; ch_ = sg * r5 > T["chase_ret5"] or sg * r20 > T["chase_ret20"] or sg * (c[-1] - s20) > T["chase_sma_atr"] * a
        if ch_: W("move already ran (5d %.1f%%, 20d %.1f%%): size x0.75" % (r5 * 100, r20 * 100), T["chase_mult"])
        ref = lx if lx else c[-1]; ref_n = ("%s print" % lses) if lx else "last close"   # exthours: judge distance vs freshest US print when we have one
        if abs(e / ref - 1) > max(T["entry_far_pct"], T["entry_far_atr"] * a / ref): W("entry %.4g far from %s %.4g - may never trigger" % (e, ref_n, ref))
        age = (asof - dt.datetime.fromisoformat(rows[-1][6].replace("Z", "+00:00"))).total_seconds() / 86400 if rows[-1][6] else 99
        if age > T["stale_days"]: W("PIT data %.1f days old - size x0.5 until refreshed" % age, .5)
    # --- debate (LLM) - advisory unless both object ---
    opp = "SHORT" if side == "LONG" else "LONG"; c_opp = isinstance(chal, dict) and chal.get("call") == opp; d_kill = isinstance(da, dict) and bool(da.get("factual_kill"))
    def _dead(x): return (not isinstance(x, dict)) or str(x.get("status") or x.get("call") or "").upper() in ("RETIRED", "UNAVAILABLE", "ERROR", "TIMEOUT") or bool(x.get("error")) or not ("call" in x or "factual_kill" in x)   # debatefix 2026-10-29 (Brett 08:33): outage/retired model never gates
    if _dead(chal) or _dead(da): W("debate_unavailable: challenger %s, advocate %s (%s) - advisory only, x1.0, no block" % ("answered" if not _dead(chal) else ("not run" if chal is None else "no verdict"), "answered" if not _dead(da) else ("not run" if da is None else "no verdict"), "not requested / --no-debate or direct gate call" if (chal is None or da is None) else "model reply had no verdict"), 1.0)
    if isinstance(chal, dict) and not _dead(chal) and str(chal.get("call")).upper() == "STAND_ASIDE": W("blind challenger verdict STAND_ASIDE (advisory only, x1.0 - TFO 19:18 WITA)", 1.0)
    if _dead(chal): chal = None
    if _dead(da): da = None
    c_opp = isinstance(chal, dict) and chal.get("call") == opp; d_kill = isinstance(da, dict) and bool(da.get("factual_kill"))   # debatefix: dead model verdicts carry no weight
    # debate advisory-only (TFO ruling 19:18 WITA 29 Oct 2026): challenger / devil's advocate NEVER block or resize an arm - warnings x1.0, logged
    if c_opp and d_kill: W("ADVISORY: blind challenger called the opposite AND devil's advocate factual kill: %s (x1.0, no block)" % str(da.get("fact"))[:200], 1.0)
    elif c_opp: W("ADVISORY: blind challenger called the opposite (x1.0, no block)", 1.0)
    elif d_kill: W("ADVISORY: devil's advocate factual objection: %s (x1.0, no block)" % str(da.get("fact"))[:240], 1.0)
    # --- venue rules ---
    if side == "SHORT":
        if venue == "CRYPTO": hard.append("no crypto shorts on Kraken spot")
        elif venue in ("US", "ASX"):
            bf = _rj(os.path.join(BASE, "state", "ibkr_borrow.json"))
            bn = ((bf or {}).get(venue.lower(), {}).get("names") or {}).get(t.replace(".AX", "")) if bf else None
            if bn is None: W("borrow status unknown (no IBKR borrow row) - confirm borrow before tick: size x0.75", .75)
            else: H_(bool(bn.get("ok") and (bn.get("available") or 0) > 0), "borrow refused/none available (IBKR borrow file)")
    # --- book, kill switch, firm rails ---
    E = env if env is not None else dict(book=book_positions(), firm=firm_state())
    pos, ks = E["book"]; firm = E["firm"]
    if firm == "HALT": hard.append("firm rail HALT - new entries halted")
    elif firm is None: W("firm_risk.json unreadable - book.py will still refuse to arm if firm is halted")
    if ks == "FLAT": hard.append("desk kill switch FLAT - Brett must reset")
    elif ks == "HALVED": W("kill switch HALVED (-8%% from peak): size x0.5", .5)
    from veteran_desk import book as BK
    agree = isinstance(chal, dict) and chal.get("call") == side
    FLOOR = BK.RISK_CRYPTO_MIN if venue == "CRYPTO" else BK.RISK_MIN   # crypto seat floor 0.5% (Brett 2026-10-29 13:08 WITA)
    _SP = [p.get("risk_pct")]   # tfo_a1pct_20261031: A seat choice 1-2% kept as-is (never force a 1% proposal up to 2%); <1% rejected
    try: _SP[0] = float(_SP[0]) / 100.0 if _SP[0] is not None and float(_SP[0]) > 0.05 else (float(_SP[0]) if _SP[0] is not None else None)   # accepts 1.5 (%) or .015
    except Exception: _SP[0] = None
    _rank = {"A+": 3, "A": 2, "B": 1, "C": 0}; _gs = p.get("grade_seat")
    _ovr = bool(p.get("override")) or (_gs in _rank and _rank[_gs] > _rank.get(p.get("grade"), 0))
    if _ovr: W("override (gate grade %s below seat ask %s): default half size x0.5 (tfo_review_20261030)" % (p.get("grade"), _gs or "-"), .5)
    if p.get("_ll_required") and not str(p.get("lessons_respected") or "").strip(): W("lessons_respected missing - proposal did not say how it respects past lessons: size x0.5", .5)
    try:
        _ft = BK.fee_filter(t, e, tg)
        if _ft: hard.append(_ft)
    except Exception as _x: hard.append("fee filter error %s - fail closed" % type(_x).__name__)
    if _seat in DEX_SEATS:   # Dex seat rules (tighten only, tfo_review_20261030)
        if c_opp and d_kill: hard.append("Dex: both debate models dissent - pass")
        elif c_opp or d_kill: W("Dex: one debate model dissents - half size x0.5", .5)
        try: _a = float(A["atr"][-1])
        except Exception: _a = 0.0
        if _a > 0: H_(dist >= 1.5 * _a - 1e-9, "Dex: stop %.4g from entry < 1.5 ATR (%.4g)" % (dist, 1.5 * _a))
        _SB = ((_B.get("seats") or {}).get(_seat) or {})
        if BK.seat_red(_SB):
            # tfo_a1pct_20261031: do NOT force Dex-red A up to RISK_A_DEFAULT (Brett 00:07: 1% A allowed)
            _n = sum(1 for q in (_SB.get("positions") or {}).values() if q.get("status") in ("open", "armed"))
            H_(_n < 2, "Dex red seat: %d open/armed - max 2 while red" % _n)
        if t.upper() == "SMT.L":
            _cl = [c.get("closed_utc") for sn in DEX_SEATS for c in ((_B.get("seats") or {}).get(sn, {}).get("closed") or []) if canon(c.get("ticker") or "") == "SMT.L" and c.get("closed_utc")]
            _ok = asof.date() >= dt.date(2026, 11, 7) and (not _cl or BK.sessions_since(max(_cl), asof) >= 5)
            H_(_ok, "Dex: no SMT.L re-entry for 5 sessions after the last Dex SMT.L close (not before 2026-11-07)")
    def _risk(mm):
        rp = BK.risk_plan(T["seat_equity_aud"], p.get("grade"), agree, mm, floor=FLOOR, seat_pct=_SP[0])
        if venue == "CRYPTO" and atr_cut[0] < 1 and rp.get("reject") and rp["base_pct"] * mm / atr_cut[0] >= FLOOR - 1e-12:   # kairails: ATR cut stops AT the 0.5% floor, never skips
            rp = BK.risk_plan(T["seat_equity_aud"], p.get("grade"), agree, FLOOR / rp["base_pct"], floor=FLOOR)
        return round(rp["risk_aud"]), rp
    m = max(mult[0], T["min_mult"]); risk, RP = _risk(m)
    corr_info = []; heat = risk
    if A is not None:
        for q in list(pos) + list(others or []):
            qt = q.get("ticker")
            if not qt or qt == t: continue
            try: B_ = arrays(history(qt, asof)[0])
            except Exception: continue
            cr = _corr(A, B_)
            if cr is None: continue
            signed = cr * (1 if q.get("side") == side else -1); corr_info.append(dict(ticker=qt, corr=round(cr, 2), same_way=round(signed, 2)))
            if signed >= T["corr_hi"]: heat += float(q.get("risk_aud") or 0)
        hi = [x for x in corr_info if x["same_way"] >= T["corr_hi"]]
        if hi: W("correlated (>=%.1f) with %s: size x0.5" % (T["corr_hi"], ",".join(x["ticker"] for x in hi)), .5); m = max(mult[0], T["min_mult"]); risk, RP = _risk(m)
        H_(heat <= T["heat_cap"] * T["seat_equity_aud"], "correlated heat A$%.0f > cap %.0f%% of A$%.0f" % (heat, T["heat_cap"] * 100, T["seat_equity_aud"]))
        hs["correlation"] = corr_info
        v = A["v"][-20:] * A["c"][-20:]; adv_usd = float(np.mean(v)) if len(v) else 0
        if deep_otc(t, venue): hs["adv20_aud"] = None; warn.append("G10 spot FX / liquid futures root: Yahoo volume is not traded value - ADV floor exempt (g10fx 30 Oct)")
        elif adv_usd > 0 and dist > 0:
            try: k_ = BK.aud_per_unit(t, au)   # fxsize 29 Oct: local-currency listings converted at live FX (was: every non-ASX read as USD)
            except BK.FxUnavailable: k_ = None
            if k_ is None: k_ = 1.0 / au   # only reached if ccy_issue already hard-rejected
            per = dist * k_; sh = risk / per; notion = sh * e * k_; adv_aud = adv_usd * k_
            fr = notion / adv_aud; hs["adv20_aud"] = round(adv_aud); hs["pos_pct_adv"] = round(fr, 5)
            H_(fr <= T["adv_hard"], "position %.1f%% of 20d ADV - could not get out in a hurry" % (fr * 100))
            H_(adv_aud >= T["min_value_aud"], "avg daily traded value A$%.0f < A$%.0f floor (Brett 2026-10-29 13:02 WITA: all caps, min A$500k)" % (adv_aud, T["min_value_aud"]))
            if T["adv_soft"] < fr <= T["adv_hard"]: W("position %.1f%% of ADV: size x0.5" % (fr * 100), .5); m = max(mult[0], T["min_mult"]); risk, RP = _risk(m)
        else: hs["adv20_aud"] = None; warn.append("no volume in history (OTC FX/index) - ADV check skipped, venue liquidity assumed deep" if venue in ("FX", "IDX", "FUT") else "no volume data - ADV unverified")
    if venue == "CRYPTO" and not spec and dist > 0 and e > 0 and not RP.get("reject"):   # kairails 14:28 WITA: A$50k clip (A$100k A+), reject if the capped clip < 0.5% floor
        try: k2 = BK.aud_per_unit(t, au)
        except BK.FxUnavailable: k2 = 1.0 / au
        r2, _q2, rj = BK.crypto_clip(risk, dist * k2, e * k2, T["seat_equity_aud"], p.get("grade"))
        if r2 < risk - 0.5: warn.append("crypto clip capped at A$%.0fk notional: risk A$%.0f -> A$%.0f" % (BK.crypto_clip_cap(p.get("grade")) / 1000, risk, r2)); risk = round(r2)
        if rj: hard.append("below_crypto_floor: A$%.0fk-notional clip risks A$%.0f < 0.5%% of seat equity" % (BK.crypto_clip_cap(p.get("grade")) / 1000, r2))
    if RP.get("reject") == "below_crypto_floor": hard.append("below_crypto_floor: natural size %.2f%% of seat equity < 0.5%%" % (RP["wanted_pct"] * 100))
    elif RP.get("wanted_pct") is not None and not (venue == "CRYPTO" and atr_cut[0] < 1): H_(RP["wanted_pct"] >= FLOOR - 1e-9, "below risk floor: natural size %.2f%% of seat equity < %.1f%% - rejected, not lifted (Brett rule; Marcus 2026-10-29 13:08 WITA, was: lift unless n>=40 and <1%%)" % (RP["wanted_pct"] * 100, FLOOR * 100))
    if RP["warning"]: warn.append(RP["warning"])
    # 2026-10-29 infra: attach live quote as-of so seats/stale checks can judge freshness (never blocks)
    q_asof = q_last = q_src = None
    try:
        if live: q_asof, q_last, q_src = live["asof"], live["last"], live["src"]
        elif not os.environ.get("PYTEST_CURRENT_TEST"):
            mq = BK.live_px(t) or {}
            q_asof, q_last, q_src = mq.get("asof"), mq.get("last"), mq.get("src") or ("yahoo" if mq.get("last") is not None else None)
    except Exception: pass
    # tfo_seatpipe_usbeta_20261030 (TFO Risk cap): at ARM time, desk net US equity beta = open + armed (live book) + this run's already-passed
    # US proposals (others[].us_beta_usd) + this trade's notional must stay within book.us_beta_cap_usd (US_BETA_CAP_PCT, now 60%, of this book's equity x live AUDUSD, tfo_betacap_dyn_20261030); levered ETFs at their multiple. Staged (WAIT) queue entries also count.
    usb_new = 0.0
    try:
        if venue == "US" and not hard and dist > 0 and e > 0:
            try: k3 = BK.aud_per_unit(t, au)
            except BK.FxUnavailable: k3 = 1.0 / au
            sh = int(risk / (dist * k3)) if risk else 0
            usb_new = (1 if side == "LONG" else -1) * e * sh * BK.LEVERED_ETF_MULT.get(t, 1)
            _thf = os.environ.get("THF_DESK") == "1"; D_ = T.get("us_beta_book")
            if D_ is None and not os.environ.get("PYTEST_CURRENT_TEST"): D_ = _rj(os.path.join(BASE, "state", "thf", "thf_book.json") if _thf else os.path.join(BASE, "reports", "veteran_desk_book.json"), {}) or {}
            D_ = D_ or {}
            if T.get("us_beta_cur_usd") is not None: cur = float(T["us_beta_cur_usd"])
            elif os.environ.get("PYTEST_CURRENT_TEST") and T.get("us_beta_book") is None: cur = 0.0
            else:
                cur = 0.0; _ids = set()
                for B_ in (D_.get("seats") or {}).values():
                    for q_ in (B_.get("positions") or {}).values():
                        if q_.get("status") in ("open", "armed"):
                            px_ = q_.get("mark") or q_.get("fill") or q_.get("entry")
                            if px_: cur += BK._us_beta_leg(q_, px_)
                        _ids.add(q_.get("id"))
                _stg = T.get("us_beta_staged")
                if _stg is None and not os.environ.get("PYTEST_CURRENT_TEST"):
                    try:
                        import funder_gate as _FG; _stg = [e_ for e_ in (_FG.load().get("entries") or {}).values() if e_.get("book") == "veteran" and e_.get("state") == _FG.WAIT]
                    except Exception: _stg = []
                for e_ in (_stg or []):   # staged = in queue, not yet armed in the book
                    if e_.get("id") in _ids or e_.get("ticker") == t and e_.get("side") == side: continue
                    if e_.get("entry") and e_.get("shares"): cur += BK._us_beta_leg(dict(e_, venue=e_.get("venue") or P.venue_of(e_["ticker"])), e_["entry"])
            cur += sum(float(o.get("us_beta_usd") or 0) for o in (others or []))
            cap = float(T["us_beta_cap_usd"]) if T.get("us_beta_cap_usd") is not None else BK.us_beta_cap_usd(D_, au if os.environ.get("PYTEST_CURRENT_TEST") else None)
            tot = cur + usb_new; hs["us_beta"] = dict(cur_usd=round(cur), new_usd=round(usb_new), total_usd=round(tot), cap_usd=round(cap), cap_rule="%.0f%% book equity x AUDUSD" % (100 * BK.US_BETA_CAP_PCT))
            H_(not (abs(tot) > cap and abs(tot) > abs(cur)), "us_beta_cap: desk net US beta (open+armed+staged) US$%.0f + this US$%.0f = US$%.0f > cap US$%.0f (%.0f%% of book equity, TFO Risk)" % (cur, usb_new, tot, cap, 100 * BK.US_BETA_CAP_PCT))
    except Exception as x: hard.append("us_beta check error %s - fail closed (tfo_review_20261030: an unverifiable beta cap rejects)" % type(x).__name__)
    return dict(passed=not hard, reasons=hard, warnings=warn, size_mult=round(m, 3), risk_aud=risk, risk_pct=RP["pct"], chal_agree=agree, seat_equity_aud=T["seat_equity_aud"], rr=round(rr, 2), history=hs, version=VERSION,
                quote_asof=q_asof, quote_last=q_last, quote_src=q_src, quote_session=(live or {}).get("session") or ("REGULAR" if q_last is not None else None), live_quote=live,
                armed=False, can_arm=False, needs_brett_tick=True, hard_rules_kept=HARD_RULES, us_beta_usd=round(usb_new), seat_risk_pct=_SP[0], override=_ovr)
