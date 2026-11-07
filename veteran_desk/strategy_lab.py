"""strategylab_20261102 (Brett 20:42 WITA, Super Power build fm-20261102124017-d40c). STRATEGY LAB. PAPER ONLY, advisory/research only.
Design new strategies and bring the desk up to speed on new ideas:
  1 DRAFT     every available AI (ai_router.FAN) drafts independently: thesis, rules, entry/stop/target, universe, risk, failure modes,
              plus a machine-testable spec in the LAB DSL below.
  2 CRITIQUE  each drafting AI attacks the OTHER drafts (lookahead, overfitting, costs, capacity, survivorship, regime, crowding).
  3 SYNTH     strongest AI that is free merges the best into ONE spec (same DSL). No synth AI free -> least-attacked draft (labelled).
  4 GATES     honest PIT backtest + walk-forward, net of costs and survivorship (see HONEST below). Verdict PASS / FAIL / INCONCLUSIVE.
  5 OUTPUT    state/strategy_lab/sessions/<sid>.json, latest.json (dash Strategy Lab card), brief to every seat mailbox (seat_mail.deliver).
  A PASS only registers a PAPER SHADOW FORWARD TEST (state/strategy_lab/shadow/<sid>.json). It NEVER sizes, arms or writes any book,
  ledger or gate; promotion out of shadow is Brett's decision only.
Skip any AI on ai_cooldown / signed out / busy (never wait, never block). Zero AIs free -> session saved as no_ai, nothing else happens.
Who can open a session: Brett (msgdesk "lab: <idea>" or dash POST /api/strategy_lab), any seat ("lab_idea" in its JSON answer), CLI:
  python -m veteran_desk.strategy_lab open --seat <seat> "idea"     (queues + spawns a detached drain; returns immediately)
  python -m veteran_desk.strategy_lab drain | run --seat s "idea" | status | shadow-tick | backtest spec.json
HONEST (pre-registered, do not loosen without Brett):
 - bars from data/pit/pit.sqlite (read-only), signal on bar t close, fill at bar t+1 OPEN (no lookahead); gap through stop fills at open;
   stop and target both inside one bar -> stop first.
 - costs: cost_tables.est_rt_rate per name (spread tier/ADV bucket + commission + tax + FX conv) every round trip; shorts pay borrow
   (CM_BORROW_DEFAULT_PA); crypto SPOT shorts are not tradable -> skipped.
 - survivorship: universe = deterministic hash sample of EVERY symbol the PIT universe table knows on those venues, listed AND delisted
   (never "today's winners"). Venues flagged UNIVERSE_BIASED in pit.VENUE_BIAS get a per-trade haircut on long trades before the gate.
 - walk-forward: rules are fixed (no fitting) but the AIs designed them knowing history, so history is treated as in-sample. Gate needs:
   enough trades, positive net mean R overall AND in the last 40% (hold-out) AND in >= 4 of 5 time folds, t-stat, profit factor, and
   robustness to +-25% stop/target perturbation. The real out-of-sample test is the shadow forward test."""
import concurrent.futures as cf, datetime as dt, fcntl, hashlib, json, math, os, re, sqlite3, subprocess, sys, time
import numpy as np
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = os.path.join(BASE, "state", "strategy_lab"); SESS = os.path.join(D, "sessions"); SHADOW = os.path.join(D, "shadow")
QUEUE = os.path.join(D, "queue.jsonl"); LATEST = os.path.join(D, "latest.json"); LOCK = os.path.join(D, ".drain.lock")
DB = os.path.join(BASE, "data", "pit", "pit.sqlite")
WITA = dt.timezone(dt.timedelta(hours=8)); UTC = dt.timezone.utc
VERSION = "strategy_lab_20261102"
# pre-registered gate (honest results only)
G = dict(n_min=100, mean_r_min=0.05, holdout_frac=0.40, holdout_mean_r_min=0.0, folds=5, folds_pos_min=4, t_min=2.0, pf_min=1.10,
         robust_min=3, surv_haircut_r=0.03, liq_min_local=1_000_000.0, min_px=1.0, max_names=150, max_names_cap=400, min_bars=260, slip_bps=5.0)
LIMITS = dict(per_seat_day=6, total_day=20, dedupe_h=24, idea_chars=2000)
FEATS = ("ret", "close_vs_sma", "sma_ratio", "rsi", "zscore", "breakout", "breakdown", "atr_pct", "vol_ratio", "gap")
OPS = {">": np.greater, "<": np.less, ">=": np.greater_equal, "<=": np.less_equal}
VENUES = ("US", "ASX", "LSE", "Euronext", "XETRA", "TSE", "HKEX", "SGX", "KRX", "TWSE", "NSE", "NZX", "SIX", "BME", "MILAN", "NORDIC",
          "TSX", "CRYPTO", "FX", "FUT", "IDX")
DSL = ('"spec": {"side": "long|short", "universe": {"venues": ["US"|"ASX"|"LSE"|"CRYPTO"|"FX"|"FUT"|...], "syms": [optional explicit list]}, '
       '"entry": [{"f": one of %s, "n": int lookback, "m": int (sma_ratio slow only), "op": ">"|"<"|">="|"<=", "v": number}, ... ALL must hold at bar close], '
       '"stop_atr": number (stop = entry -/+ stop_atr*ATR14, 0.5-6), "target_r": number (target in R multiples of the stop distance), '
       '"max_hold_days": int (1-120)}. Feature meanings: ret=c/c[n]-1; close_vs_sma=c/SMA(n)-1; sma_ratio=SMA(n)/SMA(m)-1; rsi=RSI(n) 0-100; '
       'zscore=(c-mean_n)/std_n; breakout=c/max(high prior n)-1; breakdown=c/min(low prior n)-1; atr_pct=ATR(n)/c; vol_ratio=v/mean(v prior n); gap=o/c[1]-1.'
       % "|".join(FEATS))
DRAFT = ("STRATEGY LAB - independent DRAFT (Taylor Hedge Fund, PAPER only, research). Funder/seat idea:\n\"{idea}\"\n(opened by {who})\n"
         "Design ONE concrete, testable daily-bar strategy for this idea. Be honest about why it may NOT work. Reply with ONE JSON object:\n"
         '{{"name": "...", "thesis": "why the edge should exist and who is on the other side", "rules": "plain-English rules", '
         '"entry": "...", "stop": "...", "target": "...", "universe": "...", "risk": "sizing/heat/correlation notes (paper)", '
         '"failure_modes": ["..."], {dsl}}}\nOnly the DSL features listed can be backtested; approximate the idea with them and say what is lost.')
CRIT = ("STRATEGY LAB - CROSS-CRITIQUE. Idea: \"{idea}\". You drafted:\n{mine}\n\nOther AIs drafted (attack these hard: lookahead, data-snooping/overfit, "
        "costs and turnover, capacity/liquidity, survivorship, regime dependence, crowding, untestable rules, stop/target logic):\n{others}\n\n"
        'Reply with ONE JSON object: {{"attacks": [{{"target": "<ai name>", "issue": "...", "severity": "high|med|low"}}], '
        '"best_ideas_to_keep": ["..."], "would_change_in_mine": "..."}}')
SYN = ("STRATEGY LAB - SYNTH. Merge the drafts and critiques below into ONE best strategy for the idea \"{idea}\". Keep only ideas that survived "
       "the critiques; fix the high-severity attacks; never invent backtest numbers (the desk runs the backtest itself after you). PAPER only; "
       "a PASS goes to shadow forward test only, never to live sizing.\n\n=== DRAFTS ===\n{drafts}\n\n=== CRITIQUES ===\n{crits}\n\n"
       'Reply with ONE JSON object: {{"name": "...", "thesis": "...", "rules": "...", "entry": "...", "stop": "...", "target": "...", '
       '"universe": "...", "risk": "...", "failure_modes": ["..."], "disagreements": "who disagreed on what", "kept_from": {{"<ai>": "what was kept"}}, {dsl}}}')

def now_wita(): return dt.datetime.now(WITA).strftime("%Y-%m-%d %H:%M:%S WITA")
def _j(p, fb=None):
    try:
        with open(p) as f: return json.load(f)
    except Exception: return fb
def _atomic(p, o):
    os.makedirs(os.path.dirname(p), exist_ok=True); t = "%s.tmp.%d" % (p, os.getpid())
    with open(t, "w") as f: json.dump(o, f, indent=1, default=str)
    os.replace(t, p)
def parse_json(s):
    s = str(s or ""); m = re.search(r"\{.*\}", s, re.S)
    if not m: return None
    try: return json.loads(m.group(0))
    except Exception:
        try: return json.loads(re.sub(r",\s*([}\]])", r"\1", m.group(0)))
        except Exception: return None

# ------------------------------------------------------------------ spec validation (DSL)
def _num(x, lo, hi, d=None):
    try: v = float(x)
    except Exception: return d
    return v if (v == v and lo <= v <= hi) else d
def clean_spec(spec):
    """Sanitise an AI spec into the DSL. Returns (spec or None, problems[])."""
    P = []
    if not isinstance(spec, dict): return None, ["no spec object"]
    side = str(spec.get("side") or "long").lower()
    if side not in ("long", "short"): P.append("side %r -> long" % side); side = "long"
    u = spec.get("universe") if isinstance(spec.get("universe"), dict) else {}
    ven = [v for v in (u.get("venues") or []) if v in VENUES] or (["US"] if not u.get("syms") else [])
    syms = [str(s).strip() for s in (u.get("syms") or []) if str(s).strip()][:G["max_names_cap"]]
    syms = [s for s in syms if not (s.upper().endswith(".JK") or "IDR" in s.upper())]   # Brett: no Indonesian assets
    ent = []
    for r in (spec.get("entry") or [])[:8]:
        if not isinstance(r, dict) or r.get("f") not in FEATS or r.get("op") not in OPS: P.append("dropped rule %s" % json.dumps(r)[:80]); continue
        n = int(_num(r.get("n"), 1, 504, 14 if r.get("f") in ("rsi", "atr_pct") else 20)); v = _num(r.get("v"), -1e6, 1e6)
        if v is None: P.append("rule without numeric v: %s" % json.dumps(r)[:80]); continue
        rr = dict(f=r["f"], n=n, op=r["op"], v=v)
        if r["f"] == "sma_ratio": rr["m"] = int(_num(r.get("m"), 2, 504, max(n * 4, 50)))
        ent.append(rr)
    if not ent: return None, P + ["no testable entry rules"]
    out = dict(side=side, universe=dict(venues=ven, syms=syms), entry=ent, stop_atr=_num(spec.get("stop_atr"), 0.5, 6.0, 2.0),
               target_r=_num(spec.get("target_r"), 0.3, 20.0, 2.0), max_hold_days=int(_num(spec.get("max_hold_days"), 1, 120, 20)))
    if side == "short" and ven == ["CRYPTO"] and not syms: P.append("crypto SPOT shorts are not tradable (PF_ perps only)")
    return out, P

# ------------------------------------------------------------------ features (causal: value at t uses bars <= t only)
def _sma(x, n):
    out = np.full(len(x), np.nan)
    if len(x) >= n: c = np.cumsum(np.insert(x, 0, 0.0)); out[n - 1:] = (c[n:] - c[:-n]) / n
    return out
def _std(x, n):
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        w = np.lib.stride_tricks.sliding_window_view(x, n); out[n - 1:] = w.std(axis=1)
    return out
def _roll(x, n, fn):   # fn over the PRIOR n bars (excludes t)
    out = np.full(len(x), np.nan)
    if len(x) > n:
        w = np.lib.stride_tricks.sliding_window_view(x[:-1], n); out[n:] = fn(w, axis=1)
    return out
def atr(h, l, c, n=14):
    pc = np.insert(c[:-1], 0, c[0]); tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    return _sma(tr, n)
def feature(f, n, m, o, h, l, c, v):
    with np.errstate(all="ignore"):
        if f == "ret": out = np.full(len(c), np.nan); out[n:] = c[n:] / c[:-n] - 1; return out
        if f == "close_vs_sma": return c / _sma(c, n) - 1
        if f == "sma_ratio": return _sma(c, n) / _sma(c, m) - 1
        if f == "zscore": return (c - _sma(c, n)) / _std(c, n)
        if f == "breakout": return c / _roll(h, n, np.max) - 1
        if f == "breakdown": return c / _roll(l, n, np.min) - 1
        if f == "atr_pct": return atr(h, l, c, n) / c
        if f == "vol_ratio": return v / _roll(v, n, np.mean)
        if f == "gap": out = np.full(len(c), np.nan); out[1:] = o[1:] / c[:-1] - 1; return out
        if f == "rsi":
            d = np.diff(c, prepend=c[0]); up = _sma(np.clip(d, 0, None), n); dn = _sma(np.clip(-d, 0, None), n)
            return 100 - 100 / (1 + up / np.where(dn == 0, np.nan, dn))
    raise ValueError(f)
def signals(spec, o, h, l, c, v):
    ok = np.ones(len(c), bool)
    for r in spec["entry"]:
        x = feature(r["f"], r["n"], r.get("m"), o, h, l, c, v)
        with np.errstate(invalid="ignore"): ok &= np.nan_to_num(OPS[r["op"]](x, r["v"]), nan=False).astype(bool) & ~np.isnan(x)
    return ok

# ------------------------------------------------------------------ data
def _con():
    return sqlite3.connect("file:%s?mode=ro" % DB, uri=True, timeout=30)
CRYPTO_PFX = ("KRAKEN:", "BINANCE:", "COINBASE:", "PF_")
def venue_of(sym):   # pit.venue_of + BINANCE:/COINBASE: pairs (pit maps unknown prefixes to US)
    if str(sym).upper().startswith(CRYPTO_PFX): return "CRYPTO"
    from veteran_desk import pit as P
    return P.venue_of(sym)
def pick_universe(spec, con=None, cap=None):
    """Survivorship-honest universe: explicit syms, else a deterministic hash sample of ALL PIT universe symbols (listed + delisted)
    on the requested venues. Never filters on today's price/performance."""
    cap = int(cap or G["max_names"]); u = spec["universe"]
    if u.get("syms"): return list(dict.fromkeys(u["syms"]))[:G["max_names_cap"]], dict(method="explicit", note="hand-picked names = selection bias risk")
    con = con or _con(); want = set(u.get("venues") or ["US"])
    rows = con.execute("SELECT sym, status FROM universe").fetchall()
    pool = [(s, st) for s, st in rows if s and venue_of(s) in want and not (s.upper().endswith(".JK") or "IDR" in s.upper())]
    if "FX" in want or "FUT" in want or "IDX" in want:   # macro series are not all in the universe table
        extra = [r[0] for r in con.execute("SELECT DISTINCT sym FROM bars WHERE sym LIKE '%=X' OR sym LIKE '%=F' OR sym LIKE '^%'").fetchall()]
        pool += [(s, "listed") for s in extra if venue_of(s) in want and s not in {p[0] for p in pool}]
    pool.sort(key=lambda r: hashlib.sha256(r[0].encode()).hexdigest())
    pick = pool[:cap]
    return [p[0] for p in pick], dict(method="hash_sample_listed_and_delisted", pool=len(pool), n=len(pick),
                                      delisted_in_pool=sum(1 for p in pool if p[1] == "delisted"), delisted_picked=sum(1 for p in pick if p[1] == "delisted"))
def load_bars(sym, con):
    r = con.execute("SELECT date,o,h,l,c,v FROM bars WHERE sym=? ORDER BY date", (sym,)).fetchall()
    r = [x for x in r if None not in x[1:5] and x[4] > 0 and x[2] >= x[3] > 0]
    if not r: return None
    a = np.array([x[1:] for x in r], float); a[:, 4] = np.nan_to_num(a[:, 4])
    return dict(date=[x[0] for x in r], o=a[:, 0], h=a[:, 1], l=a[:, 2], c=a[:, 3], v=a[:, 4])

# ------------------------------------------------------------------ simulator
def cost_rt(sym, px, adv_local):
    from veteran_desk import cost_tables as CT
    try: return CT.est_rt_rate(sym, venue_of(sym), px=px, adv_aud=adv_local) + 2 * G["slip_bps"] / 1e4
    except Exception: return 0.004 + 2 * G["slip_bps"] / 1e4
def borrow_pa(sym):
    from veteran_desk import cost_tables as CT
    B = CT.CM_BORROW_DEFAULT_PA; return B["US"] if venue_of(sym) == "US" else B["DEFAULT"]
def simulate(spec, sym, B, start_date=None, stop_atr=None, target_r=None, sig=None):
    """Trades for one symbol. Signal at close t, entry at open t+1. One position at a time. R net of costs."""
    o, h, l, c, v, dates = B["o"], B["h"], B["l"], B["c"], B["v"], B["date"]; n = len(c)
    if n < G["min_bars"]: return []
    side = 1 if spec["side"] == "long" else -1
    if side < 0 and venue_of(sym) == "CRYPTO" and not str(sym).upper().startswith("PF_"): return []   # spot crypto shorts not tradable
    k_stop = stop_atr or spec["stop_atr"]; k_tgt = target_r or spec["target_r"]; H = spec["max_hold_days"]
    sig = signals(spec, o, h, l, c, v) if sig is None else sig; A = atr(h, l, c, 14)
    dv = _sma(c * v, 20) if v.any() else np.full(n, np.nan); fxlike = venue_of(sym) in ("FX", "IDX", "FUT")
    out = []; t = 0
    while t < n - 2:
        if not sig[t] or np.isnan(A[t]) or (start_date and dates[t] < start_date): t += 1; continue
        if not fxlike and not (dv[t] >= G["liq_min_local"]): t += 1; continue
        if not fxlike and venue_of(sym) != "CRYPTO" and c[t] < G["min_px"]: t += 1; continue   # sub-min-price equities: per-share costs/ticks not modelable
        e = o[t + 1]; risk = k_stop * A[t]
        if not (e > 0 and risk > 0): t += 1; continue
        st = e - side * risk; tg = e + side * k_tgt * risk; x = None; why = "time"; j = t + 1
        while j < n and j <= t + H:
            if j > t + 1 and ((side > 0 and o[j] <= st) or (side < 0 and o[j] >= st)): x, why = o[j], "stop_gap"; break
            if (side > 0 and l[j] <= st) or (side < 0 and h[j] >= st): x, why = st, "stop"; break
            if (side > 0 and h[j] >= tg) or (side < 0 and l[j] <= tg): x, why = (max(tg, o[j]) if side > 0 else min(tg, o[j])) if j > t + 1 else tg, "target"; break
            j += 1
        if x is None:
            if j >= n: break   # still open at end of data: not counted (no mark-to-model)
            j = min(j, n - 1); x = c[j]
        gross = side * (x - e) / risk
        cr = cost_rt(sym, e, None if fxlike or np.isnan(dv[t]) else dv[t]) * e / risk
        if side < 0: cr += borrow_pa(sym) * (j - t) / 365.0 * e / risk
        out.append(dict(sym=sym, d=dates[t + 1], x_d=dates[j], gross_r=round(float(gross), 4), cost_r=round(float(cr), 4),
                        r=round(float(gross - cr), 4), why=why, days=int(j - t)))
        t = j + 1
    return out

def _stats(R):
    R = np.asarray(R, float); n = len(R)
    if not n: return dict(n=0)
    m = float(R.mean()); sd = float(R.std(ddof=1)) if n > 1 else 0.0; eq = np.cumsum(R)
    pos, neg = R[R > 0].sum(), -R[R < 0].sum()
    return dict(n=n, mean_r=round(m, 4), median_r=round(float(np.median(R)), 4), t=round(m / (sd / math.sqrt(n)), 2) if sd > 0 else None,
                win=round(float((R > 0).mean()), 3), pf=round(float(pos / neg), 3) if neg > 0 else None, sum_r=round(float(R.sum()), 2),
                max_dd_r=round(float((np.maximum.accumulate(eq) - eq).max()), 2))

def backtest(spec, con=None, bars_fn=None, universe=None, cap=None):
    """Run the honest gate on a clean spec. bars_fn(sym) -> bars dict (tests). Returns result dict with verdict."""
    t0 = time.time(); con = con if (con is not None or bars_fn) else _con()
    if universe is None: syms, umeta = pick_universe(spec, con, cap)
    else: syms, umeta = list(universe), dict(method="given")
    B = {}; trades = []; sim = {}
    for s in syms:
        b = bars_fn(s) if bars_fn else load_bars(s, con)
        if not b or len(b["c"]) < G["min_bars"]: continue
        B[s] = b; sim[s] = signals(spec, b["o"], b["h"], b["l"], b["c"], b["v"]); trades += simulate(spec, s, b, sig=sim[s])
    trades.sort(key=lambda r: r["d"])
    biased = sorted({venue_of(s) for s in B if _bias(venue_of(s))})
    hc = G["surv_haircut_r"] if (biased and spec["side"] == "long") else 0.0
    R = [t["r"] - (hc if _bias(venue_of(t["sym"])) else 0.0) for t in trades]
    res = dict(version=VERSION, gate=G, universe=umeta, names_with_data=len(B), venues_biased=biased, surv_haircut_r=hc,
               all=_stats(R), gross=_stats([t["gross_r"] for t in trades]), avg_cost_r=round(float(np.mean([t["cost_r"] for t in trades])), 4) if trades else None,
               first=trades[0]["d"] if trades else None, last=trades[-1]["d"] if trades else None)
    if len(R) < G["n_min"]:
        res.update(verdict="INCONCLUSIVE", reasons=["only %d trades (< %d) - not enough evidence either way" % (len(R), G["n_min"])], secs=round(time.time() - t0, 1),
                   sample=trades[-10:]); return res
    k = int(len(R) * (1 - G["holdout_frac"])); res["insample"] = _stats(R[:k]); res["holdout"] = _stats(R[k:])
    fs = np.array_split(np.arange(len(R)), G["folds"]); res["folds"] = [dict(_stats([R[i] for i in f]), frm=trades[f[0]]["d"], to=trades[f[-1]]["d"]) for f in fs if len(f)]
    rob = []
    for ks, kt in ((0.75, 1.0), (1.25, 1.0), (1.0, 0.75), (1.0, 1.25)):
        rr = []
        for s, b in B.items():
            rr += [t["r"] - (hc if _bias(venue_of(s)) else 0.0) for t in simulate(spec, s, b, stop_atr=spec["stop_atr"] * ks, target_r=spec["target_r"] * kt, sig=sim[s])]
        rob.append(dict(stop_x=ks, target_x=kt, **_stats(rr)))
    res["robustness"] = rob
    a = res["all"]; why = []
    if a["mean_r"] < G["mean_r_min"]: why.append("net mean %.3fR < %.2fR" % (a["mean_r"], G["mean_r_min"]))
    if (a.get("t") or 0) < G["t_min"]: why.append("t-stat %s < %.1f" % (a.get("t"), G["t_min"]))
    if (a.get("pf") or 0) < G["pf_min"]: why.append("profit factor %s < %.2f" % (a.get("pf"), G["pf_min"]))
    if res["holdout"]["mean_r"] <= G["holdout_mean_r_min"]: why.append("hold-out (last %d%%) mean %.3fR not positive" % (G["holdout_frac"] * 100, res["holdout"]["mean_r"]))
    fp = sum(1 for f in res["folds"] if f.get("mean_r", 0) > 0)
    if fp < G["folds_pos_min"]: why.append("only %d/%d time folds positive" % (fp, len(res["folds"])))
    rp = sum(1 for r in rob if r.get("n") and r["mean_r"] > 0)
    if rp < G["robust_min"]: why.append("fragile: %d/4 stop/target perturbations positive" % rp)
    res.update(verdict="FAIL" if why else "PASS", reasons=why or ["all pre-registered gates met net of costs%s" % (" and survivorship haircut" if hc else "")],
               folds_pos=fp, robust_pos=rp, secs=round(time.time() - t0, 1), sample=trades[-10:])
    return res
def _bias(venue):
    from veteran_desk import pit as P
    return (P.VENUE_BIAS.get(venue) or ("",))[0] == "UNIVERSE_BIASED"

# ------------------------------------------------------------------ AI rounds (never block)
def _AR():
    from veteran_desk import ai_router as AR
    return AR
def fan(prompts, seat):
    """prompts: {ai: prompt}. Runs every free AI in parallel with its own hard timeout. Returns (answers{ai:text}, skipped{ai:why})."""
    AR = _AR(); P = AR.providers(max_effort=True); got, skipped, held, futs = {}, {}, [], {}
    fx = cf.ThreadPoolExecutor(max_workers=max(1, len(prompts)))
    for name, p in prompts.items():
        why = AR.skip_reason(name)
        if why: skipped[name] = why; continue
        if not AR._SEM[name].acquire(blocking=False): skipped[name] = "busy"; continue
        held.append(name); futs[fx.submit(AR._call, name, P[name], seat, p, None)] = name
    try:
        for f in cf.as_completed(futs):
            name = futs[f]
            try:
                out = f.result()
                if not str(out or "").strip(): raise RuntimeError("empty answer")
                got[name] = str(out); AR._md()._CD.mark_ok(name)
            except Exception as x:
                skipped[name] = "timeout" if "timeout" in str(x) else "error"; AR._fail(name, x)
    finally:
        for n in held: AR._SEM[n].release()
        fx.shutdown(wait=False)
    return got, skipped
def _brief_draft(d): return json.dumps({k: d.get(k) for k in ("name", "thesis", "rules", "entry", "stop", "target", "universe", "risk", "failure_modes", "spec")}, default=str)[:3500]
def run_session(idea, who="brett", sid=None, ais=None, bars_fn=None, deliver=True):
    """Full lab session. Saves state; returns the session dict. Never raises on AI trouble."""
    idea = str(idea or "").strip()[:LIMITS["idea_chars"]]; sid = sid or "lab-%s-%s" % (dt.datetime.now(WITA).strftime("%Y%m%d%H%M%S"), hashlib.sha1((idea + who).encode()).hexdigest()[:6])
    AR = _AR(); fanset = list(ais or AR.FAN); S = dict(id=sid, idea=idea, opened_by=who, opened_wita=now_wita(), status="drafting", version=VERSION, paper_only=True,
                                                       rule="PASS -> paper shadow forward test only; never live/book sizing; promotion is Brett's call")
    _save(S); dsl = DSL
    drafts_raw, S["skipped_draft"] = fan({a: DRAFT.format(idea=idea, who=who, dsl=dsl) for a in fanset}, "strategy_lab")
    S["drafts"] = {a: (parse_json(t) or dict(raw=t[:3000])) for a, t in drafts_raw.items()}
    if not S["drafts"]:
        S.update(status="no_ai", verdict="NO_AI", summary="no AI free (all cooling/signed-out/busy) - session saved, nothing tested"); _finish(S, deliver); return S
    S["status"] = "critique"; _save(S); crits = {}
    if len(S["drafts"]) >= 2:
        cp = {a: CRIT.format(idea=idea, mine=_brief_draft(d), others="\n\n".join("--- %s ---\n%s" % (b.upper(), _brief_draft(e)) for b, e in S["drafts"].items() if b != a))
              for a, d in S["drafts"].items()}
        cr, S["skipped_critique"] = fan(cp, "strategy_lab"); crits = {a: (parse_json(t) or dict(raw=t[:2000])) for a, t in cr.items()}
    S["critiques"] = crits; S["status"] = "synth"; _save(S)
    final, by = None, None
    if len(S["drafts"]) >= 2:
        sp = SYN.format(idea=idea, dsl=dsl, drafts="\n\n".join("--- %s ---\n%s" % (a.upper(), _brief_draft(d)) for a, d in S["drafts"].items()),
                        crits="\n\n".join("--- %s ---\n%s" % (a.upper(), json.dumps(c, default=str)[:2500]) for a, c in crits.items()) or "(no critiques returned)")
        for name in [n for n in AR.STRONG if n in S["drafts"]] + [n for n in AR.STRONG if n not in S["drafts"] and n != "seat"]:
            got, _ = fan({name: sp}, "strategy_lab")
            if got.get(name) and isinstance(parse_json(got[name]), dict) and clean_spec(parse_json(got[name]).get("spec"))[0]:
                final, by = parse_json(got[name]), name; break
    if final is None:   # code fallback: the testable draft with the fewest high-severity attacks (labelled)
        hits = {a: sum(1 for c in crits.values() for x in (c.get("attacks") or []) if isinstance(x, dict) and str(x.get("target", "")).lower() == a and x.get("severity") == "high") for a in S["drafts"]}
        for a in sorted(S["drafts"], key=lambda a: (hits[a], AR.STRONG.index(a) if a in AR.STRONG else 9)):
            if clean_spec(S["drafts"][a].get("spec"))[0]: final, by = S["drafts"][a], "code:least_attacked<-" + a; break
    S["synth"] = final; S["synth_by"] = by; S["contributors"] = sorted(S["drafts"])
    spec, probs = clean_spec((final or {}).get("spec")); S["spec"] = spec; S["spec_problems"] = probs
    if not spec:
        S.update(status="done", verdict="UNTESTABLE", summary="no AI produced a testable spec in the lab DSL (%s)" % "; ".join(probs)[:200]); _finish(S, deliver); return S
    S["status"] = "backtest"; _save(S)
    try: S["backtest"] = backtest(spec, bars_fn=bars_fn)
    except Exception as x: S["backtest"] = dict(verdict="INCONCLUSIVE", reasons=["backtest error: %s" % str(x)[:200]])
    S["verdict"] = S["backtest"].get("verdict"); S["status"] = "done"
    if S["verdict"] == "PASS": S["shadow"] = register_shadow(S)
    S["summary"] = summary_line(S); _finish(S, deliver); return S
def summary_line(S):
    b = S.get("backtest") or {}; a = b.get("all") or {}; h = b.get("holdout") or {}
    return ("%s: %s | n=%s net %sR/trade (gross %sR, cost %sR) t=%s PF=%s hold-out %sR folds+ %s/%s robust %s/4%s | %s" % (
        (S.get("synth") or {}).get("name") or "strategy", S.get("verdict"), a.get("n"), a.get("mean_r"), (b.get("gross") or {}).get("mean_r"), b.get("avg_cost_r"),
        a.get("t"), a.get("pf"), h.get("mean_r"), b.get("folds_pos"), len(b.get("folds") or []), b.get("robust_pos"),
        " | survivorship haircut %sR on %s" % (b.get("surv_haircut_r"), ",".join(b.get("venues_biased") or [])) if b.get("surv_haircut_r") else "",
        "; ".join(b.get("reasons") or [])))[:900]
def register_shadow(S):
    """PASS -> paper shadow forward test record ONLY. Never touches book.py / ledgers / gates."""
    start = dt.datetime.now(UTC).strftime("%Y-%m-%d")
    rec = dict(id=S["id"], name=(S.get("synth") or {}).get("name"), spec=S["spec"], start_date=start, registered_wita=now_wita(), status="SHADOW_FORWARD",
               spec_sha256=hashlib.sha256(json.dumps(S["spec"], sort_keys=True).encode()).hexdigest(), universe=(S.get("backtest") or {}).get("universe"),
               rule="paper shadow forward test only: no book sizing, no arming, no ledger writes; promotion requires Brett", forward=None)
    _atomic(os.path.join(SHADOW, S["id"] + ".json"), rec); return dict(path="state/strategy_lab/shadow/%s.json" % S["id"], start_date=start, status="SHADOW_FORWARD")
def shadow_tick(bars_fn=None):
    """Forward-only stats for every shadow test (entries on bars dated >= start_date). Report only."""
    out = []
    for p in sorted(os.listdir(SHADOW)) if os.path.isdir(SHADOW) else []:
        if not p.endswith(".json"): continue
        r = _j(os.path.join(SHADOW, p)) or {}
        if r.get("status") != "SHADOW_FORWARD": continue
        con = None if bars_fn else _con(); tr = []
        syms = (r.get("universe") or {}).get("syms") or pick_universe(r["spec"], con)[0]
        for s in syms:
            b = bars_fn(s) if bars_fn else load_bars(s, con)
            if b and len(b["c"]) >= G["min_bars"]: tr += simulate(r["spec"], s, b, start_date=r["start_date"])
        if r.get("universe") is not None and not (r["universe"] or {}).get("syms"): r["universe"]["syms"] = syms   # freeze the forward universe
        r["forward"] = dict(_stats([t["r"] for t in tr]), as_of=now_wita(), last=sorted(tr, key=lambda t: t["d"])[-5:])
        _atomic(os.path.join(SHADOW, p), r); out.append(dict(id=r["id"], **{k: r["forward"].get(k) for k in ("n", "mean_r", "sum_r")}))
    return out

# ------------------------------------------------------------------ state, brief, queue
def _save(S): _atomic(os.path.join(SESS, S["id"] + ".json"), S)
def _finish(S, deliver=True):
    S["finished_wita"] = now_wita(); _save(S)
    idx = _j(LATEST, {}) or {}; rows = [r for r in (idx.get("sessions") or []) if r.get("id") != S["id"]]
    rows.insert(0, dict(id=S["id"], idea=S["idea"][:200], opened_by=S["opened_by"], verdict=S.get("verdict"), summary=S.get("summary"),
                        contributors=S.get("contributors"), synth_by=S.get("synth_by"), finished_wita=S["finished_wita"], shadow=S.get("shadow"),
                        skipped=dict(S.get("skipped_draft") or {})))
    _atomic(LATEST, dict(updated_wita=now_wita(), sessions=rows[:30]))
    if deliver: S["briefed"] = brief(S); _save(S)
def brief(S):
    """Brief to every seat mailbox (desk rounds + msgdesk prompts read it). Not rate-counted, cannot loop."""
    try:
        from veteran_desk import seat_mail as SM
    except Exception: return []
    sy = S.get("synth") or {}
    txt = ("STRATEGY LAB %s [%s] idea by %s: %s | thesis: %s | %s | PAPER: a PASS is shadow forward test only, not tradable size."
           % (S["id"], S.get("verdict"), S.get("opened_by"), S.get("idea", "")[:120], str(sy.get("thesis") or "")[:160], S.get("summary") or ""))
    ok = []
    for s in SM.IDS:
        try:
            if SM.deliver("strategy_lab", s, txt, hop=SM.MAX_HOP, kind="lab_brief"): ok.append(s)
        except Exception: pass
    return ok
def _today_requests():
    day = dt.datetime.now(WITA).strftime("%Y-%m-%d"); rows = []
    try: rows = [json.loads(x) for x in open(QUEUE) if x.strip()]
    except FileNotFoundError: pass
    return rows, [r for r in rows if str(r.get("ts_wita", "")).startswith(day)]
def request(who, idea, root=None, spawn=True):
    """Queue a lab session (Brett or any seat). Returns (ok, msg). Spawns a detached drain so the caller never waits."""
    idea = re.sub(r"\s+", " ", str(idea or "")).strip()[:LIMITS["idea_chars"]]
    if len(idea) < 8: return False, "strategy lab: idea too short"
    os.makedirs(D, exist_ok=True); rows, today = _today_requests()
    if who != "brett" and sum(1 for r in today if r.get("who") == who) >= LIMITS["per_seat_day"]: return False, "strategy lab: %s hit %d sessions today" % (who, LIMITS["per_seat_day"])
    if sum(1 for r in today if r.get("who") != "brett") >= LIMITS["total_day"] and who != "brett": return False, "strategy lab: desk hit %d seat sessions today" % LIMITS["total_day"]
    h = hashlib.sha1(idea.lower().encode()).hexdigest()[:12]
    if any(r.get("h") == h and time.time() - r.get("t", 0) < LIMITS["dedupe_h"] * 3600 for r in rows): return False, "strategy lab: same idea already queued/run in the last %dh" % LIMITS["dedupe_h"]
    sid = "lab-%s-%s" % (dt.datetime.now(WITA).strftime("%Y%m%d%H%M%S"), h[:6])
    with open(QUEUE, "a") as f: f.write(json.dumps(dict(id=sid, who=who, idea=idea, root=root, h=h, t=time.time(), ts_wita=now_wita())) + "\n")
    if spawn: spawn_drain()
    return True, "strategy lab session %s queued for %s (drafts -> critique -> synth -> honest backtest; result on the dash Strategy Lab card + seat brief)" % (sid, who)
def spawn_drain():
    """labresume_20261103: the drain used to be a child of the msgdesk service, so every msgdesk restart killed the running lab session mid-way
    (3 Nov: three sessions died at drafting/critique/synth and were never retried). Now it runs as its own systemd unit (tfo-thf-lab-drain.service,
    also ticked by tfo-thf-lab-drain.timer every 5 min); the old detached Popen is the fallback when systemd is not available."""
    try:
        r = subprocess.run(["sudo", "-n", "systemctl", "start", "--no-block", "tfo-thf-lab-drain.service"], capture_output=True, text=True, timeout=20)
        if r.returncode == 0: return True
    except Exception: pass
    try:
        subprocess.Popen([sys.executable, "-m", "veteran_desk.strategy_lab", "drain"], cwd=BASE, stdin=subprocess.DEVNULL,
                         stdout=open(os.path.join(BASE, "logs", "strategy_lab.log"), "a"), stderr=subprocess.STDOUT, start_new_session=True)
        return True
    except Exception: return False
ABANDONED_S = int(os.environ.get("THF_LAB_ABANDONED_S", "900"))   # labresume_20261103: an unfinished session file untouched this long = its runner died
def abandoned_sessions(rows, now=None):
    """labresume_20261103: queued requests whose session exists but is neither finished nor being written to (runner died) -> re-run in order."""
    now = now or time.time(); out = []
    for r in rows:
        f = os.path.join(SESS, r["id"] + ".json")
        if not os.path.exists(f): continue
        S = _j(f, {}) or {}
        if S.get("status") in ("done", "no_ai", "error"): continue
        if now - os.path.getmtime(f) >= ABANDONED_S: out.append(dict(r, _resume_from=S.get("status")))
    return out
def drain():
    """Run every queued session not yet run. Single runner via flock (a second drain exits at once)."""
    os.makedirs(D, exist_ok=True); lk = open(LOCK, "a")
    try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError: return []
    done = []
    try:
        while True:
            rows, _ = _today_requests()
            todo = [r for r in rows if not os.path.exists(os.path.join(SESS, r["id"] + ".json"))] or abandoned_sessions(rows)   # labresume_20261103
            if not todo: break
            r = todo[0]; print(now_wita(), "lab run", r["id"], r["who"], ("RESUME from %s (runner died)" % r["_resume_from"]) if r.get("_resume_from") else "", flush=True)
            try: S = run_session(r["idea"], r["who"], sid=r["id"]); done.append(S["id"]); _post_thread(r, S)
            except Exception as x:
                _save(dict(id=r["id"], idea=r["idea"], opened_by=r["who"], status="error", verdict="ERROR", summary=str(x)[:300])); print("lab err", x, flush=True)
        try: shadow_tick()
        except Exception as x: print("shadow_tick err", x, flush=True)
    finally: lk.close()
    return done
def _post_thread(r, S):   # result back into Brett's msgdesk thread when Brett opened it from chat
    if not r.get("root"): return
    try:
        from veteran_desk import thf_msgdesk as MD
        MD.tadd(root=r["root"], **{"from": "strategy_lab", "to": "brett"}, text="🧪 STRATEGY LAB %s\n%s" % (S["id"], S.get("summary")), mode="code")
    except Exception: pass

# ------------------------------------------------------------------ dash
def card():
    idx = _j(LATEST, {}) or {}; sh = []
    for p in sorted(os.listdir(SHADOW)) if os.path.isdir(SHADOW) else []:
        r = _j(os.path.join(SHADOW, p)) if p.endswith(".json") else None
        if r: sh.append(dict(id=r.get("id"), name=r.get("name"), start_date=r.get("start_date"), status=r.get("status"), forward=r.get("forward")))
    q, _ = _today_requests(); pend = [dict(id=r["id"], who=r["who"], idea=r["idea"][:120]) for r in q if not os.path.exists(os.path.join(SESS, r["id"] + ".json"))]
    running = [s for s in (_j(os.path.join(SESS, f)) for f in (os.listdir(SESS) if os.path.isdir(SESS) else []) if f.endswith(".json")) if s and s.get("status") not in ("done", "no_ai", "error")]
    return dict(sessions=(idx.get("sessions") or [])[:10], shadow=sh, pending=pend, running=[dict(id=s["id"], status=s["status"]) for s in running][:5],
                rule="PAPER: PASS -> shadow forward test only, never live sizing", updated_wita=idx.get("updated_wita"))
def panel():
    import html
    E = lambda x: html.escape(str(x if x is not None else ""))
    try: C = card()
    except Exception as x: return "<!-- strategy lab card error %s -->" % type(x).__name__
    col = dict(PASS="#3c3", FAIL="#f55", INCONCLUSIVE="#fc3", UNTESTABLE="#aaa", NO_AI="#aaa", ERROR="#f55")
    rows = "".join('<div class="fmr"><b>%s</b> <span style="color:%s;font-weight:bold">%s</span> · by %s · %s<br><i>%s</i><br>%s</div>' % (
        E(s.get("finished_wita")), col.get(s.get("verdict"), "#ccc"), E(s.get("verdict")), E(s.get("opened_by")),
        E("AIs: " + ", ".join(s.get("contributors") or []) + (" · synth " + str(s.get("synth_by")) if s.get("synth_by") else "") +
          (" · skipped " + ", ".join("%s(%s)" % kv for kv in (s.get("skipped") or {}).items()) if s.get("skipped") else "")),
        E(s.get("idea")), E(s.get("summary"))) for s in C["sessions"]) or "<i>no lab sessions yet</i>"
    sh = "".join('<div class="fmr">🔬 SHADOW %s %s since %s · forward n=%s mean %sR</div>' % (E(r["id"]), E(r.get("name")), E(r.get("start_date")),
        E((r.get("forward") or {}).get("n")), E((r.get("forward") or {}).get("mean_r"))) for r in C["shadow"])
    pend = "".join('<div class="fmr">⏳ %s (%s): %s</div>' % (E(r["id"]), E(r["who"]), E(r["idea"])) for r in C["pending"] + C["running"] if r.get("id"))
    return ('<section id="strategy-lab" style="border:2px solid #3c9;border-radius:8px;padding:8px;margin:8px;font:13px sans-serif;background:#0f1a14;color:#eee">'
            '<b>🧪 STRATEGY LAB</b> <i>(PAPER · all free AIs draft → cross-critique → synth → honest PIT backtest + walk-forward net of costs &amp; survivorship · '
            'PASS = shadow forward test only, never live sizing · AIs on cooldown are skipped)</i>'
            '<div><textarea id="sl-idea" rows="2" style="width:100%%;box-sizing:border-box;background:#222;color:#eee;border:1px solid #555" placeholder="new strategy idea"></textarea>'
            '<input id="sl-pin" type="password" placeholder="pin" style="background:#222;color:#eee;border:1px solid #555"> <button onclick="slOpen()">Open lab session</button> <span id="sl-st"></span></div>'
            '%s%s%s<script>async function slOpen(){const st=document.getElementById("sl-st");st.textContent="queuing...";try{const r=await fetch("/api/strategy_lab",'
            '{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({idea:document.getElementById("sl-idea").value,pin:document.getElementById("sl-pin").value})});'
            'const j=await r.json();st.textContent=j.msg||j.error||r.status}catch(e){st.textContent="error "+e}}</script></section>') % (pend, sh, rows)

def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="strategy_lab"); sp = ap.add_subparsers(dest="cmd", required=True)
    o = sp.add_parser("open"); o.add_argument("--seat", default="brett"); o.add_argument("idea")
    r = sp.add_parser("run"); r.add_argument("--seat", default="brett"); r.add_argument("idea")
    sp.add_parser("drain"); sp.add_parser("status"); sp.add_parser("shadow-tick")
    b = sp.add_parser("backtest"); b.add_argument("spec_json"); b.add_argument("--cap", type=int, default=None)
    a = ap.parse_args(argv)
    if a.cmd == "open": print(json.dumps(dict(zip(("ok", "msg"), request(a.seat, a.idea)))))
    elif a.cmd == "run": print(json.dumps(run_session(a.idea, a.seat), indent=1, default=str)[:20000])
    elif a.cmd == "drain": print(json.dumps(dict(done=drain())))
    elif a.cmd == "status": print(json.dumps(card(), indent=1, default=str))
    elif a.cmd == "shadow-tick": print(json.dumps(shadow_tick(), indent=1))
    else:
        spec, probs = clean_spec(json.load(open(a.spec_json))); print(json.dumps(dict(problems=probs, result=backtest(spec, cap=a.cap) if spec else None), indent=1, default=str))
if __name__ == "__main__": main(sys.argv[1:])
