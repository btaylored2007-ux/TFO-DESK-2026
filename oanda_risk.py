"""oanda_risk.py - pure risk/sizing/cost/shadow maths for the OANDA practice sleeve (12 Oct 2026, GPT blueprint; RULE_LOG.md).
No network, no state: every function takes plain numbers/lists so it is unit-testable. Formula sheet: research/oanda_formulas.md.
LIVE use (oanda_sleeve.py): wilder_atr (B), inst_meta/round_units (B), size_units (C), r0_aud/net_r (A), cost_gate (E).
SHADOW only: gpt_caps_check + ewma_cov (D, GPT's tighter caps), tsmom_carry_score (G), Part C2 a_star batch multiplier + C4 rounding fallback.
LIVE (Part C, 13 Oct 2026): round_stop_outward (C4, Decimal), nav_return (C8 external-flow-adjusted NAV return)."""
import math
# ---------------- B: Wilder ATR + instrument metadata ----------------
def true_ranges(cs):
    return [max(cs[i]["h"] - cs[i]["l"], abs(cs[i]["h"] - cs[i - 1]["c"]), abs(cs[i]["l"] - cs[i - 1]["c"])) for i in range(1, len(cs))]
def wilder_atr(cs, n=20):
    """Wilder ATR: seed = SMA of the first n true ranges, then ATR_t = (ATR_{t-1}*(n-1) + TR_t)/n. None if < n true ranges."""
    tr = true_ranges(cs or [])
    if len(tr) < n: return None
    a = sum(tr[:n]) / n
    for x in tr[n:]: a = (a * (n - 1) + x) / n
    return a
def inst_meta(i):
    """Fields used from OANDA /instruments (logged with every entry)."""
    return dict(pipLocation=int(i.get("pipLocation", -4)), tradeUnitsPrecision=int(i.get("tradeUnitsPrecision", 0)),
                minimumTradeSize=float(i.get("minimumTradeSize", 1) or 0), displayPrecision=int(i.get("displayPrecision", 5)),
                marginRate=float(i.get("marginRate", 0.05)), financing=i.get("financing") or {})
def pip(meta): return 10.0 ** meta["pipLocation"]
def round_units(u, meta):
    """Floor to tradeUnitsPrecision; 0 if below minimumTradeSize."""
    p = meta["tradeUnitsPrecision"]; u = math.floor(max(0.0, u) * 10 ** p + 1e-9) / 10 ** p
    return u if u >= meta["minimumTradeSize"] and u > 0 else 0.0
def c_quote(q2h):
    """quote->AUD conversion; conservative = the larger of OANDA's positive/negative factors."""
    v = [float(q2h[k]) for k in ("positiveUnits", "negativeUnits") if q2h and q2h.get(k) not in (None, "")]
    v = [x for x in v if math.isfinite(x) and x > 0]
    return max(v) if v else None          # 13 Oct 2026 audit #5: missing conversion is UNKNOWN (never 1.0)
def c_quote_loss(px):
    """13 Oct 2026 audit #4: AUD per quote unit for a LOSS = OANDA homeConversions accountLoss of the quote currency (what OANDA books
    a losing trade at, incl. its conversion markup); fallback = the larger quoteHomeConversionFactors. None if unknown (entry must skip)."""
    if not px: return None
    v = px.get("loss_cq")
    try:
        if v is not None and math.isfinite(float(v)) and float(v) > 0: return float(v)
    except Exception: pass
    return c_quote(px.get("q2h"))
# ---------------- C: sizing ----------------
VOL_DAYS = 252          # 13 Oct 2026 (audit #10): ONE annualisation constant (blueprint: 252 trading days)
LAMBDA_VOL = math.exp(-math.log(2) / 60)   # 0.988514020353, 60-day half-life
def ewma_sigma_d(cs, arith=True, lam=LAMBDA_VOL, seed_n=20):
    """Blueprint section 1: zero-mean EWMA daily vol. arith=True: x_t = P_t/P_(t-1)-1 (cash-notional sizing); False: y_t = ln(P_t/P_(t-1))
    (stops / trend normalisation). Seeded with the mean square of the first seed_n returns, then v_t = lam*v_(t-1) + (1-lam)*r_t^2.
    None if fewer than seed_n+1 valid returns."""
    c = [x["c"] for x in (cs or [])]
    r = [((c[i] / c[i - 1] - 1) if arith else math.log(c[i] / c[i - 1])) for i in range(1, len(c)) if c[i - 1] > 0 and c[i] > 0]
    if len(r) < seed_n + 1: return None
    v = sum(x * x for x in r[:seed_n]) / seed_n
    for x in r[seed_n:]: v = lam * v + (1 - lam) * x * x
    return math.sqrt(v)
def sigma_ann(cs, n=None, days=VOL_DAYS, arith=True):
    """13 Oct 2026 (audit #10): annualised EWMA vol (blueprint) = sigma_d * sqrt(252). Arithmetic for sizing by default. None if too short."""
    s = ewma_sigma_d(cs, arith=arith)
    return None if s is None else s * math.sqrt(days)
def size_units(E, D, P, cq, sig, risk_pct=0.005, vol_cap=0.03, cost_per_unit_aud=0.0):
    """units = min(E*risk/(D*cq + cost), E*vol_cap/(P*cq*sigma_ann)). risk_pct is a CEILING. Returns (units, binding, u_risk, u_vol)."""
    u_risk = E * risk_pct / (D * cq + max(0.0, cost_per_unit_aud))
    u_vol = E * vol_cap / (P * cq * sig) if sig and sig > 0 else float("inf")
    return (u_vol, "vol_cap", u_risk, u_vol) if u_vol < u_risk else (u_risk, "risk", u_risk, u_vol)
# ---------------- A: frozen R0 and net R ----------------
def r0_aud(units, D, cq): return abs(units) * D * cq
def net_r(realized_pl_aud, financing_aud, r0):
    """net R = (OANDA realizedPL [spread+conversion embedded] + financing) / frozen entry R0."""
    return None if not r0 else (float(realized_pl_aud or 0) + float(financing_aud or 0)) / float(r0)
# ---------------- E: cost gate ----------------
def financing_debit_aud(units, P, cq, rate_annual, hold_days):
    """Expected financing DEBIT only (credits never count, so they cannot rescue an entry)."""
    return abs(units) * P * cq * max(0.0, -float(rate_annual or 0)) * hold_days / 365.0
def cost_gate(units, spread_px, slip_px, P, cq, rate_annual, hold_days, r0, atr, max_cost_r=0.10, max_spread_atr=0.05):
    """None if OK else reason. cost/R0 = (spread + slippage)*units*cq + financing debit over hold, divided by R0."""
    if atr and spread_px > max_spread_atr * atr:
        return "skip:cost_gate spread %.3g > %.2f x ATR20 %.3g" % (spread_px, max_spread_atr, atr)
    cost = abs(units) * (spread_px + slip_px) * cq + financing_debit_aud(units, P, cq, rate_annual, hold_days)
    if r0 and cost / r0 > max_cost_r:
        return "skip:cost_gate cost/R0 %.3f > %.2f" % (cost / r0, max_cost_r)
    return None
def cost_ratio(units, spread_px, slip_px, P, cq, rate_annual, hold_days, r0):
    return (abs(units) * (spread_px + slip_px) * cq + financing_debit_aud(units, P, cq, rate_annual, hold_days)) / r0 if r0 else None
# ---------------- D (SHADOW): GPT currency-leg caps + EWMA forecast vol ----------------
LAMBDA = 0.988514; GAMMA = 0.30
GPT_CAPS = dict(net=0.01, gross=0.015, total=0.02, ccy_notional=1.0, gross_notional=3.0, vol=0.08)
def legs(ins): b, q = ins.split("_"); return b, q
def ccy_risk(pos):
    """pos: dicts(ins, dir, risk_frac[, notional_frac]). -> (net b_c, gross g_c) of initial stop risk per currency (XAU excluded)."""
    net, gross = {}, {}
    for p in pos:
        b, q = legs(p["ins"])
        for k, s in ((b, 1), (q, -1)):
            if k == "XAU": continue
            net[k] = net.get(k, 0.0) + s * p["dir"] * p["risk_frac"]; gross[k] = gross.get(k, 0.0) + p["risk_frac"]
    return net, gross
def ccy_notional(pos):
    """e_c: signed AUD notional per currency / E (base +, quote -), including XAU and USD."""
    e = {}
    for p in pos:
        b, q = legs(p["ins"]); n = p.get("notional_frac", 0.0) * p["dir"]
        e[b] = e.get(b, 0.0) + n; e[q] = e.get(q, 0.0) - n
    return e
USD_LEGS = {"EUR": ("EUR_USD", 1), "GBP": ("GBP_USD", 1), "AUD": ("AUD_USD", 1), "NZD": ("NZD_USD", 1), "XAU": ("XAU_USD", 1),
            "JPY": ("USD_JPY", -1), "CHF": ("USD_CHF", -1), "CAD": ("USD_CAD", -1)}
def factor_returns(data):
    """Currency factor = daily log return of the currency vs USD (USD is the numeraire, factor 0). -> (ccys, rows aligned by candle time)."""
    series = {}
    for c, (ins, s) in USD_LEGS.items():
        cs = data.get(ins)
        if cs and len(cs) > 30: series[c] = {cs[i]["t"]: s * math.log(cs[i]["c"] / cs[i - 1]["c"]) for i in range(1, len(cs))}
    ccys = sorted(series)
    if not ccys: return [], []
    ts = sorted(set.intersection(*[set(v) for v in series.values()]))
    return ccys, [[series[c][t] for c in ccys] for t in ts]
def ewma_cov(rows, lam=LAMBDA, gamma=GAMMA, raw=False):
    """EWMA covariance (zero mean) seeded with the first 252 synchronised rows' second moments (blueprint s.3; 13 Oct 2026 audit #10),
    then diagonal shrinkage S=(1-g)S+g*diag(S). None if fewer than 252 rows."""
    if not rows or len(rows) < VOL_DAYS: return None
    k = len(rows[0]); n0 = VOL_DAYS
    S = [[sum(r[i] * r[j] for r in rows[:n0]) / n0 for j in range(k)] for i in range(k)]
    for r in rows[n0:]:
        for i in range(k):
            for j in range(k): S[i][j] = lam * S[i][j] + (1 - lam) * r[i] * r[j]
    if raw: return S                                                    # Part C2: raw EWMA (unshrunk) for the second vol root
    return [[S[i][j] if i == j else (1 - gamma) * S[i][j] for j in range(k)] for i in range(k)]
def forecast_vol(e, ccys, S, days=VOL_DAYS):
    """None if any exposed currency (|e_c| > 0, USD numeraire excluded) has no factor in ccys (GPT N12: missing data is not zero risk)."""
    if any(abs(v) > 1e-12 and k != "USD" and k not in ccys for k, v in e.items()): return None
    w = [e.get(c, 0.0) for c in ccys]
    return math.sqrt(max(0.0, sum(w[i] * S[i][j] * w[j] for i in range(len(w)) for j in range(len(w)))) * days)
def gpt_caps_check(pos, ccys=None, S=None, caps=GPT_CAPS, require_vol=False):
    """List of GPT cap breaches (empty = within all GPT caps)."""
    eps = 1e-9; out = []; net, gross = ccy_risk(pos)
    out += ["net %s %+.2f%%" % (k, v * 100) for k, v in sorted(net.items()) if abs(v) > caps["net"] + eps]
    out += ["gross %s %.2f%%" % (k, v * 100) for k, v in sorted(gross.items()) if v > caps["gross"] + eps]
    tot = sum(p["risk_frac"] for p in pos)
    if tot > caps["total"] + eps: out.append("total %.2f%%" % (tot * 100))
    e = ccy_notional(pos)
    out += ["notional %s %.2fE" % (k, v) for k, v in sorted(e.items()) if abs(v) > caps["ccy_notional"] + eps]
    gn = sum(p.get("notional_frac", 0.0) for p in pos)
    if gn > caps["gross_notional"] + eps: out.append("gross_notional %.2fE" % gn)
    if S is not None and ccys:
        v = forecast_vol(e, ccys, S)
        if v is None: out.append("vol_unavailable (exposed currency without factor history)")
        elif v > caps["vol"] + eps: out.append("vol %.1f%%" % (v * 100))
    elif require_vol and any(abs(v) > 1e-12 and k != "USD" for k, v in e.items()): out.append("vol_unavailable (no covariance)")
    return out
# ---------------- F: EMA trend helpers ----------------
def ema(xs, n):
    """EMA seeded with the SMA of the first n values; None if too short."""
    if len(xs) < n: return None
    k = 2.0 / (n + 1); e = sum(xs[:n]) / n
    for x in xs[n:]: e = x * k + e * (1 - k)
    return e
def ema_series(xs, n):
    if len(xs) < n: return []
    k = 2.0 / (n + 1); e = sum(xs[:n]) / n; out = [None] * (n - 1) + [e]
    for x in xs[n:]: e = x * k + e * (1 - k); out.append(e)
    return out
# ---------------- G (SHADOW): TSMOM + carry/vol score ----------------
def tsmom_carry_score(cs, carry_ann=None, horizons=(20, 60, 120), clip=2.0, w_carry=0.2):
    """mean_h clip(ln(P_t/P_{t-h}) / (sigma_d*sqrt(h)), +-2) + 0.2*carry_ann/sigma_ann. carry_ann from OANDA financing (longRate-shortRate)/2."""
    c = [x["c"] for x in (cs or [])]
    if len(c) < max(horizons) + 1: return None
    s_ann = sigma_ann(cs, arith=False)     # log vol for trend normalisation (blueprint)
    if not s_ann: return None
    s_d = s_ann / math.sqrt(VOL_DAYS); parts = []
    for h in horizons:
        z = math.log(c[-1] / c[-1 - h]) / (s_d * math.sqrt(h)); parts.append(max(-clip, min(clip, z)))
    sc = sum(parts) / len(parts)
    if carry_ann is not None: sc += w_carry * carry_ann / s_ann
    return sc

# ---------------- Part C (13 Oct 2026, GPT blueprint Part C; RULE_LOG.md) ----------------
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
def a_vol(w0, v, S, vol=0.08, days=VOL_DAYS, tol=1e-15):
    """C2: largest a in [0,1] with days*(w0+a v)'S(w0+a v) <= vol^2. None = existing book already breaches (C>tol); raises on invalid (A<0) matrix."""
    k = len(w0); q = lambda x, y: sum(x[i] * S[i][j] * y[j] for i in range(k) for j in range(k))
    A = q(v, v); B = 2 * q(w0, v); C = q(w0, w0) - vol ** 2 / days
    if A < -tol: raise ValueError("covariance not PSD (v'Sv < 0) - repair, do not invent a root")
    if C > tol: return None
    C = min(C, 0.0)
    if A <= tol:
        return 1.0 if B <= 0 else min(1.0, -C / B)
    disc = B * B - 4 * A * C
    if disc < 0:
        if disc < -1e-18: raise ValueError("negative discriminant beyond tolerance")
        disc = 0.0
    r = (-2 * C / (B + disc ** 0.5)) if B >= 0 and (B + disc ** 0.5) > 0 else (-B + disc ** 0.5) / (2 * A)
    return max(0.0, min(1.0, r))
def a_signed(x0, x1, L, eps=1e-12):
    """C2: largest a >= 0 with |x0 + a x1| <= L (inf if nonbinding). None if |x0| > L already."""
    if abs(x0) > L + eps: return None
    if abs(x1) <= eps: return float("inf")
    return (L - (1 if x1 > 0 else -1) * x0) / abs(x1)
def a_gross(z0, z1, L, eps=1e-12):
    """C2: largest a >= 0 with z0 + a z1 <= L for nonnegative gross quantities. None if z0 > L already."""
    if z0 > L + eps: return None
    return (L - z0) / z1 if z1 > eps else float("inf")
def _vec(d, keys): return [d.get(k, 0.0) for k in keys]
def batch_a_star(existing, batch, ccys=None, sigmas=None, caps=GPT_CAPS):
    """C2: single proportional multiplier for the NEW batch (existing fixed). existing/batch rows: ins, dir, risk_frac, notional_frac (E_plan units).
    sigmas: dict name -> daily factor covariance over ccys (e.g. raw + shrunk EWMA). Returns dict(a_star, limiting, bounds{name: a})."""
    bounds = {}
    n0, g0 = ccy_risk(existing); n1, g1 = ccy_risk(batch)
    for k in sorted(set(n0) | set(n1)):
        bounds["net_ccy_" + k] = a_signed(n0.get(k, 0.0), n1.get(k, 0.0), caps["net"])
        bounds["gross_ccy_" + k] = a_gross(g0.get(k, 0.0), g1.get(k, 0.0), caps["gross"])
    e0, e1 = ccy_notional(existing), ccy_notional(batch)
    for k in sorted(set(e0) | set(e1)): bounds["ccy_notional_" + k] = a_signed(e0.get(k, 0.0), e1.get(k, 0.0), caps["ccy_notional"])
    bounds["total_stop"] = a_gross(sum(p["risk_frac"] for p in existing), sum(p["risk_frac"] for p in batch), caps["total"])
    bounds["gross_pair_notional"] = a_gross(sum(p.get("notional_frac", 0.0) for p in existing), sum(p.get("notional_frac", 0.0) for p in batch), caps["gross_notional"])
    if ccys and sigmas and any(S is not None for S in sigmas.values()):
        exposed = {k for d in (e0, e1) for k, v in d.items() if abs(v) > 1e-12 and k != "USD"}
        if exposed - set(ccys):     # GPT N12: an exposed currency without factor history makes the vol bound UNKNOWN -> a_star 0
            return dict(a_star=0.0, limiting="vol_coverage_missing:" + ",".join(sorted(exposed - set(ccys))), bounds=bounds)
        for name, S in sigmas.items():
            if S is not None: bounds["vol_" + name] = a_vol(_vec(e0, ccys), _vec(e1, ccys), S, caps["vol"])
    elif sigmas is not None and any(abs(v) > 1e-12 and k != "USD" for d in (e0, e1) for k, v in d.items()):
        return dict(a_star=0.0, limiting="vol_unavailable (no covariance)", bounds=bounds)   # caller asked for vol bounds but has none
    if any(b is None for b in bounds.values()):
        return dict(a_star=0.0, limiting="existing_book_breach:" + ",".join(k for k, b in bounds.items() if b is None), bounds=bounds)
    lim = min(bounds, key=lambda k: bounds[k]) if bounds else None
    a = max(0.0, min(1.0, bounds[lim] if lim else 1.0))
    return dict(a_star=a, limiting=(lim if lim and bounds[lim] < 1.0 else "none (a=1)"), bounds=bounds)
def max_batch_scale(existing, batch, ccys=None, S=None, caps=GPT_CAPS):
    """Back-compat wrapper (bisection removed 13 Oct 2026): closed-form C2 a_star with one covariance."""
    return batch_a_star(existing, batch, ccys, {"shrunk": S} if S is not None else None, caps)["a_star"]
def round_units_q(u, q=1):
    """C4: u_rounded = sign(u) * q * floor(|u|/q) (Decimal)."""
    q = Decimal(str(q)); d = Decimal(str(abs(u)))
    r = (d / q).to_integral_value(rounding=ROUND_FLOOR) * q
    return float(r) * (1 if u >= 0 else -1)
def round_stop_outward(stop, direction, q_p):
    """C4: protective stop rounded AWAY from entry: long floor, short ceil, to the validated price quantum q_p (Decimal)."""
    q = Decimal(str(q_p)); s = Decimal(repr(float(stop)))
    return float((s / q).to_integral_value(rounding=ROUND_FLOOR if direction > 0 else ROUND_CEILING) * q)
def rounding_fallback(batch, a, units_of, check, max_halvings=40):
    """C4 conservative fallback. batch: candidate dicts with 'u' (continuous candidate units). units_of(cand, a)-> rounded units;
    check(list of (cand, units)) -> None if every cap/cost passes else reason (str) or ('drop', idx, reason) to remove a candidate.
    Returns dict(a, admitted=[(cand, units)], dropped=[(ins, reason)], halvings, limiting)."""
    cands = list(batch); dropped = []; h = 0; lim = None
    while True:
        adm = [(c, units_of(c, a)) for c in cands]; adm = [(c, u) for c, u in adm if u != 0]
        r = check(adm) if adm else None
        if r is None: return dict(a=a, admitted=adm, dropped=dropped, halvings=h, limiting=lim)
        if isinstance(r, tuple) and r[0] == "drop":
            c = adm[r[1]][0]; dropped.append((c.get("ins"), r[2])); cands = [x for x in cands if x is not c]; continue
        lim = r; a /= 2; h += 1
        if h > max_halvings: return dict(a=0.0, admitted=[], dropped=dropped, halvings=h, limiting=lim)
def nav_return(e_prev, e_now, flow=0.0, flow_at_start=False):
    """C8: NAV return net of external flows (deposits/withdrawals/resets are not returns)."""
    if flow_at_start: return e_now / (e_prev + flow) - 1.0
    return (e_now - e_prev - flow) / e_prev
