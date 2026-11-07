"""tfo_smallcoin_20261101 (Brett/funder approved 2026-11-01 07:26 WITA, PAPER ONLY): SMALL-COIN TIER for Kai's THF crypto scan.
Universe: Kraken USD pairs (non-pegged) whose 20-day average daily traded value is A$100k..A$500k (below the A$500k main floor).
The main >=A$500k crypto universe is unchanged.
Risk: 1/4 of Kai's normal per-trade risk (normal ~A$2,500 -> ~A$625), then capped so notional <= 2% of the coin's 20d ADV.
Execution: LIMIT orders only (no market, no stop-entry chase). Reject at propose/arm if the live Kraken spread > 0.5%.
cost_R includes Kraken fees (0.25% maker entry, 0.40% taker exit) + slippage (half spread + sqrt-impact per leg).
Shorts: only if the coin has a Kraken Futures PF_ perpetual. Tag: tier=small_coin. Grading, stops and firm heat caps unchanged."""
import datetime as dt, json, os, time
TIER = "small_coin"
BAND_LO, BAND_HI = 100_000.0, 500_000.0
RISK_MULT = 0.25
ADV_NOTIONAL_CAP = 0.02
MAX_SPREAD = 0.005
MAKER, TAKER = 0.0025, 0.0040
SLIP_FLOOR, IMPACT_K = 0.0010, 0.02
COVERAGE_KEY = "CRYPTO small-coin tier (A$100k-500k)"
UTC = dt.timezone.utc
def _base():
    try:
        from veteran_desk import pit as P; return P.BASE
    except Exception: return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def tier_path(): return os.path.join(_base(), "state", "veteran_desk", "small_coin_tier.json")
def pf_path(): return os.path.join(_base(), "state", "veteran_desk", "pf_perps.json")
def _rj(p, d=None):
    try: return json.load(open(p))
    except Exception: return d
def _wj(p, o):
    os.makedirs(os.path.dirname(p), exist_ok=True); t = p + ".tmp.%d" % os.getpid(); json.dump(o, open(t, "w"), default=str); os.replace(t, p)
def alt(sym):
    """KRAKEN:ACXUSD / ACXUSD / KRAKEN:PF_ACXUSD / PF_ACXUSD -> ACXUSD"""
    s = str(sym or "").upper().strip().split(":")[-1]
    return s[3:] if s.startswith("PF_") else s
def in_band(adv_aud):
    try: return adv_aud is not None and BAND_LO <= float(adv_aud) < BAND_HI
    except Exception: return False
def is_candidate(p):
    """Kraken sweep row (USD pair) NOT in the main >=A$500k universe -> small-coin candidate (20d ADV decides membership)."""
    return str(p.get("alt", "")).endswith("USD") and not (p.get("liquid") and (p.get("vol24h_aud") or 0) >= BAND_HI)
_MEM = {"t": 0, "d": None}
def members(path=None):
    """{ALT: row} from the tier file the engine writes after each feature-store build."""
    if path is None and _MEM["d"] is not None and time.time() - _MEM["t"] < 60: return _MEM["d"]
    d = ((_rj(path or tier_path(), {}) or {}).get("members") or {})
    if path is None: _MEM.update(t=time.time(), d=d)
    return d
def is_small(sym, M=None):
    return alt(sym) in (members() if M is None else M)
def adv_of(sym, M=None):
    r = (members() if M is None else M).get(alt(sym)) or {}
    return r.get("adv20_aud")
def write_members(rows, n_candidates, path=None):
    o = dict(utc=dt.datetime.now(UTC).isoformat(), tier=TIER, band_aud=[BAND_LO, BAND_HI], n_candidates=n_candidates, n_members=len(rows), members=rows,
             rules=dict(risk_mult=RISK_MULT, adv_notional_cap=ADV_NOTIONAL_CAP, max_spread=MAX_SPREAD, maker=MAKER, taker=TAKER, order="limit only", shorts="PF_ perp only"))
    _wj(path or tier_path(), o); _MEM.update(t=0, d=None); return o
# ---------- PF_ perps (Kraken Futures public tickers, cached 6h) ----------
def pf_set(fetch=None, max_age_h=6.0):
    J = _rj(pf_path(), {}) or {}
    try: age = (dt.datetime.now(UTC) - dt.datetime.fromisoformat(J["utc"])).total_seconds() / 3600
    except Exception: age = 1e9
    if age > max_age_h and (fetch is not None or not os.environ.get("PYTEST_CURRENT_TEST")):
        try:
            if fetch is None:
                import urllib.request
                try:
                    import kraken_dns; kraken_dns.install()
                except Exception: pass
                fetch = lambda: json.loads(urllib.request.urlopen("https://futures.kraken.com/derivatives/api/v3/tickers", timeout=20).read())["tickers"]
            S = sorted({str(t["symbol"]).upper() for t in fetch() if str(t.get("symbol", "")).upper().startswith("PF_")})
            if S: J = dict(utc=dt.datetime.now(UTC).isoformat(), symbols=S); _wj(pf_path(), J)
        except Exception: pass
    return set(J.get("symbols") or [])
def pf_alts(S=None):
    S = pf_set() if S is None else S; out = set()
    for s in S:
        a = str(s).upper(); a = a[3:] if a.startswith("PF_") else a; out.add(a)
        if a.startswith("XBT"): out.add("BTC" + a[3:])
    return out
def has_pf(sym, S=None):
    a = alt(sym); P_ = pf_alts(S)
    return a in P_ or a.replace("BTC", "XBT") in P_
# ---------- live spread ----------
def live_spread(sym, quote=None, max_age_s=900):
    """(spread fraction of mid, source). quote dict(bid, ask) wins; else Kraken public Ticker; else a fresh (<15 min) sweep row; else (None, why)."""
    q = quote
    if q is None and not os.environ.get("PYTEST_CURRENT_TEST"):
        try:
            import urllib.request
            try:
                import kraken_dns; kraken_dns.install()
            except Exception: pass
            r = json.loads(urllib.request.urlopen("https://api.kraken.com/0/public/Ticker?pair=" + alt(sym), timeout=10).read()).get("result") or {}
            v = next(iter(r.values()), None)
            if v: q = dict(bid=float(v["b"][0]), ask=float(v["a"][0]), src="kraken ticker live")
        except Exception: q = None
        if q is None:
            K = _rj(os.path.join(_base(), "state", "veteran_desk", "kraken_24h_all.json"), {}) or {}
            try: fresh = (dt.datetime.now(UTC) - dt.datetime.fromisoformat(K["utc"])).total_seconds() <= max_age_s
            except Exception: fresh = False
            if fresh:
                row = next((p for p in K.get("pairs") or [] if str(p.get("alt", "")).upper() == alt(sym)), None)
                if row and row.get("bid") and row.get("ask"): q = dict(bid=row["bid"], ask=row["ask"], src="kraken sweep")
    try:
        b, a = float(q["bid"]), float(q["ask"])
        if b > 0 and a >= b: return (a - b) / ((a + b) / 2), q.get("src", "quote")
    except Exception: pass
    return None, "no live bid/ask"
def spread_block(sym, quote=None):
    """None if spread <= 0.5%, else HARD reason. Unknown spread fails CLOSED."""
    sp, src = live_spread(sym, quote)
    if sp is None: return "small_coin: live spread unavailable (%s) - fail closed" % src
    if sp > MAX_SPREAD + 1e-12: return "small_coin: live spread %.2f%% > 0.50%% cap (%s)" % (sp * 100, src)
    return None
# ---------- costs + sizing ----------
def slip_per_leg(spread, notional_aud, adv_aud):
    fr = (float(notional_aud) / float(adv_aud)) if adv_aud else ADV_NOTIONAL_CAP
    return max(SLIP_FLOOR, (spread or 0.0) / 2 + IMPACT_K * max(fr, 0.0) ** 0.5)
def cost_R(entry, stop, spread, notional_aud, adv_aud):
    """Round-trip cost in R: maker entry fee + taker exit fee + slippage on both legs, over the stop distance."""
    d = abs(float(entry) - float(stop)) / float(entry) if entry else 0.0
    if d <= 0: return float("inf")
    return (MAKER + TAKER + 2 * slip_per_leg(spread, notional_aud, adv_aud)) / d
def size(normal_risk_aud, entry_aud_unit, per_unit_risk_aud, adv_aud):
    """Quarter of the normal risk, then cap notional at 2% of 20d ADV. Returns dict(risk_aud, units, notional_aud, capped)."""
    r = max(float(normal_risk_aud or 0), 0.0) * RISK_MULT
    q = r / per_unit_risk_aud if per_unit_risk_aud > 0 else 0.0; capped = False
    if adv_aud and entry_aud_unit > 0 and q * entry_aud_unit > ADV_NOTIONAL_CAP * float(adv_aud):
        q = ADV_NOTIONAL_CAP * float(adv_aud) / entry_aud_unit; capped = True
    return dict(risk_aud=round(q * per_unit_risk_aud, 2), units=q, notional_aud=round(q * entry_aud_unit, 2), capped=capped)
def apply_gate(p, g, au, quote=None, M=None, pf=None, rr_min=1.5):
    """Final small-coin pass on a desk/gate_v2 result (in place). No-op for non-tier names. Risk only ever cut (1/4 normal, 2% ADV cap)."""
    t = (p or {}).get("ticker") or ""
    if not is_small(t, M): return g
    if g.get("_sc_risk") is not None and g.get("risk_aud") == g["_sc_risk"]: return g   # idempotent: already sized this pass
    g["tier"] = TIER; g.setdefault("warnings", []); g.setdefault("reasons", [])
    adv = adv_of(t, M); hard = []
    if not in_band(adv): hard.append("small_coin: 20d ADV A$%s outside A$100k-500k band" % (round(adv) if adv else "?"))
    if p.get("side") == "SHORT" and not has_pf(t, pf): hard.append("small_coin short blocked: %s has no Kraken PF_ perp" % alt(t))
    ot = str(p.get("order_type") or "limit").lower()
    if ot != "limit": hard.append("small_coin: limit orders only (got %s)" % ot)
    sb = spread_block(t, quote)
    if sb: hard.append(sb)
    sp = live_spread(t, quote)[0] if not sb else None
    try:
        e, s, tg = float(p["entry"]), float(p["stop"]), float(p["target"])
        from veteran_desk import book as BK
        k = BK.aud_per_unit(t, au); z = size(g.get("risk_aud") or 0, e * k, abs(e - s) * k, adv)
        g["normal_risk_aud"] = g.get("risk_aud"); g["risk_aud"] = round(z["risk_aud"]); g["small_coin_notional_aud"] = z["notional_aud"]
        if g.get("risk_pct") and g["normal_risk_aud"]: g["risk_pct"] = round(float(g["risk_pct"]) * z["risk_aud"] / float(g["normal_risk_aud"]), 6)
        g["warnings"].append("small_coin tier: risk 1/4 normal A$%s -> A$%s%s" % (g["normal_risk_aud"], g["risk_aud"], (" (capped at 2%% of 20d ADV A$%.0f)" % adv) if z["capped"] else ""))
        if sp is not None:
            cr = cost_R(e, s, sp, z["notional_aud"], adv); g["cost_R"] = round(cr, 3)
            rr = abs(tg - e) / abs(e - s) if e != s else 0.0
            if rr - cr < rr_min - 1e-9: hard.append("small_coin: reward %.2fR minus cost %.2fR (fees 0.25%%/0.40%% + slippage) < %.1fR" % (rr, cr, rr_min))
        if z["risk_aud"] <= 0: hard.append("small_coin: size rounds to zero")
        g["_sc_risk"] = g["risk_aud"]
    except Exception as x: hard.append("small_coin sizing error %s - fail closed" % type(x).__name__)
    if hard: g["passed"] = False; g["reasons"].extend(hard)
    return g
def arm_check(e, quote=None, M=None, pf=None):
    """Book arm-time check for a tier entry: HARD reason or None. Limit only (no stop-entry/market), PF_ for shorts, live spread <= 0.5%."""
    t = e.get("ticker") or ""
    if not is_small(t, M): return None
    if e.get("side") == "SHORT" and not has_pf(t, pf): return "small_coin short blocked: no PF_ perp"
    if str(e.get("order_type") or "limit").lower() != "limit": return "small_coin: limit orders only (no stop-entry/market)"
    return spread_block(t, quote)
def arm_units(q, per_aud, unit_aud, sym, M=None):
    """Book arm: q from the NORMAL risk plan -> quarter, then the 2%-of-ADV notional cap. Returns (units, note)."""
    adv = adv_of(sym, M); z = size(q * per_aud, unit_aud, per_aud, adv)
    return z["units"], "small_coin: 1/4 risk A$%.0f%s" % (z["risk_aud"], " capped at 2% ADV" if z["capped"] else "")
def scoreboard(book):
    """Tier results from the THF book (positions + closed tagged tier=small_coin), shown separately on the dashboard."""
    o = dict(tier=TIER, open=0, armed=0, closed=0, wins=0, losses=0, realised_aud=0.0, open_risk_aud=0.0, rows=[])
    for sn, S in ((book or {}).get("seats") or {}).items():
        for p in list((S.get("positions") or {}).values()) + list(S.get("closed") or []):
            if p.get("tier") != TIER and (p.get("tags") or {}).get("tier") != TIER: continue
            st = p.get("status") or "closed"
            if st in ("open", "armed"): o[st] += 1; o["open_risk_aud"] += float(p.get("risk_aud") or 0)
            elif st in ("closed", "stopped", "target", "exited"):
                o["closed"] += 1; pl = float(p.get("pnl_aud") or p.get("realised_aud") or 0); o["realised_aud"] += pl
                o["wins" if pl > 0 else "losses"] += 1
            o["rows"].append(dict(seat=sn, ticker=p.get("ticker"), side=p.get("side"), status=st, risk_aud=p.get("risk_aud"), pnl_aud=p.get("pnl_aud")))
    o["realised_aud"] = round(o["realised_aud"], 2); o["open_risk_aud"] = round(o["open_risk_aud"], 2)
    J = _rj(tier_path(), {}) or {}; o.update(members=J.get("n_members"), candidates=J.get("n_candidates"), members_utc=J.get("utc"))
    return o
