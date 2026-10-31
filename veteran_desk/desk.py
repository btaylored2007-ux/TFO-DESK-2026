"""TFO Veteran Desk phase 1 pipeline. PAPER only. Info-only proposals; nothing arms, nothing is placed.
proposer seats (blind to each other) -> blind challenger (other CLI, never sees proposal) -> devil's advocate -> code-only risk gate
-> funder tick sheet entry in state/funder_queue.json (book 'veteran', info_only). No engine consumes 'veteran' entries in phase 1.
Stops are computed and enforced by code; nothing here needs an AI to be up to protect a position.
  python3 -m veteran_desk.desk run [--no-llm] [--no-queue]   |  python3 -m veteran_desk.desk score"""
import concurrent.futures as cf, datetime as dt, hashlib, json, os, re, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from veteran_desk import pit as P
BASE = P.BASE; UTC = dt.timezone.utc; HERE = os.path.dirname(os.path.abspath(__file__))
SD = os.path.join(BASE, "state", "veteran_desk"); SCORE = os.path.join(BASE, "state", "veteran_desk_scorecard.json")
FWD_DIR = os.path.join(BASE, "state", "thf") if os.environ.get("THF_DESK") == "1" else SD   # tfo_thffresh_20261030 (GPT review HIGH): THF forward journal + scorecard isolated
_FWD0 = FWD_DIR; _SD0 = SD
def _fwd_dir():   # tfo_review_20261030: resolved per call so tests that patch SD (or FWD_DIR) never write the live forward_trades.json(.tmp)
    if FWD_DIR != _FWD0: return FWD_DIR
    return os.path.join(BASE, "state", "thf") if os.environ.get("THF_DESK") == "1" and SD == _SD0 else SD
if os.environ.get("THF_DESK") == "1": SCORE = os.path.join(BASE, "state", "thf", "thf_scorecard.json")
REPLAY = os.path.join(BASE, "state", "veteran_desk_replay_TRAINING_ONLY.json"); HEALTH = os.path.join(BASE, "static", "veteran_desk_health.json")
CARD = os.path.join(BASE, "static", "veteran_desk.json"); REPORTS = os.path.join(BASE, "reports", "veteran_desk")
CLAUDE = None; CODEX = None  # removed 2026-10-30 22:29 (Brett): Grok only; CODEX_MODEL = "gpt-6-astra"
LLM_TIMEOUT = int(os.environ.get("VET_LLM_TIMEOUT", "300"))
from veteran_desk import cost_tables as CT   # ONE SOURCE (tfo_costmodel_20261030)
COST_RT = CT.venue_rt_table()   # derived venue RT (legacy shape)  # intl ~20bp RT; Kraken 0.25% maker + 0.40% taker
SUB_AGENTS_PER_TRADE, SUB_AGENTS_PER_RUN, SUB_TIMEOUT, MAX_PARALLEL = 5, 20, 180, 4   # Mac-safe caps   # round-trip, of notional
RISK_CAP_PER_TRADE, RISK_CAP_RUN = 1000, 4000   # v1 audit only; live sizing = book.risk_plan (2-5% of seat equity), no A$ run cap (Brett 19:00 WITA)
def now(): return dt.datetime.now(UTC)
def seats(): return {j["seat"]: j for j in (json.load(open(os.path.join(HERE, "seats", f))) for f in sorted(os.listdir(os.path.join(HERE, "seats"))) if f.endswith(".json"))}
def _wj(p, o):
    os.makedirs(os.path.dirname(p), exist_ok=True); t = p + ".tmp"; json.dump(o, open(t, "w"), indent=1, default=str); os.replace(t, p)
def _rj(p, d=None):
    try: return json.load(open(p))
    except Exception: return d
# ---------------- vd-engine (Taylor Hedge Fund) hooks, 2026-10-30. Active only when VD_ENGINE_WAKE is set by the engine wake runner ----------------
ENGINE_WAKE = os.environ.get("VD_ENGINE_WAKE") or None
GR = {"A+": 3, "A": 2, "B": 1, "reject": 0}
def _engine_wake(seat):
    if not ENGINE_WAKE: return None
    w = _rj(os.path.join(BASE, "state", "vd_engine", "wakes", ENGINE_WAKE + ".json"), {}) or {}
    return w.get("seats", {}).get(seat)
def _engine_cap(seat, j):
    """Cap (never lift) the seat grade at the engine grade for a name the engine scored this cycle. Returns cap record or None."""
    if not ENGINE_WAKE: return None
    try:
        G = (_rj(os.path.join(BASE, "state", "vd_engine", "grades", seat + ".json"), {}) or {}).get("grades") or {}
        eg = G.get(str(j.get("ticker")).upper()) or G.get(str(j.get("ticker")))
        sg = j.get("grade")
        if eg and sg in GR and GR.get(eg, 0) < GR[sg]:
            j["grade_seat"] = sg; j["grade"] = eg if eg in ("A+", "A", "B") else "B"; return dict(ticker=j.get("ticker"), seat_grade=sg, engine_grade=eg, capped_to=j["grade"])
    except Exception: pass
    return None
# ---------------- features (code, PIT only) ----------------
def feats(c, sym, asof):
    b = P.bars_asof(c, sym, asof, 260)
    if len(b) < 60: return None
    C = [r[4] for r in b]; H = [r[2] for r in b]; L = [r[3] for r in b]
    tr = [max(H[i] - L[i], abs(H[i] - C[i - 1]), abs(L[i] - C[i - 1])) for i in range(1, len(b))]; atr = sum(tr[-14:]) / 14
    er = abs(C[-1] - C[-21]) / (sum(abs(C[i] - C[i - 1]) for i in range(-20, 0)) or 1)
    sma = lambda n: sum(C[-n:]) / n
    V = [r[5] or 0 for r in b[-20:]]; adv20 = sum(V) / len(V) if V else 0   # 2026-10-29: avg volume (PIT bars) for the seats; gate_v2 still does the ADV check
    return dict(adv20=round(adv20) if adv20 else None, adv20_value=round(adv20 * C[-1] / _subunit_div(sym)) if adv20 else None, adv20_value_ccy=_major_ccy(sym), sym=sym, venue=P.venue_of(sym), last_date=b[-1][0], knowable_utc=b[-1][6], close=round(C[-1], 4), atr14=round(atr, 4),
                sma20=round(sma(20), 4), sma50=round(sma(50), 4), sma200=round(sma(200), 4) if len(C) >= 200 else None,
                ret5=round(C[-1] / C[-6] - 1, 4), ret20=round(C[-1] / C[-21] - 1, 4), ret60=round(C[-1] / C[-61] - 1, 4),
                hi20=round(max(H[-20:]), 4), lo20=round(min(L[-20:]), 4), hi252=round(max(H), 4), lo252=round(min(L), 4), efficiency_ratio20=round(er, 3),
                choppy=er < 0.25, last10_closes=[round(x, 4) for x in C[-10:]],
                last10_highs=[round(x, 4) for x in H[-10:]], last10_lows=[round(x, 4) for x in L[-10:]])   # 2026-10-29: challenger/DA pack needs PIT highs/lows (Victor/Marcus)
def audusd(c, asof):
    b = P.bars_asof(c, "AUDUSD=X", asof, 1); return b[-1][4] if b else 0.65
def to_aud(sym, x, au):   # fxsize 2026-10-29: one conversion with book.aud (listing currency, live Yahoo FX, fail closed)
    from veteran_desk import book as BK
    return BK.aud(sym, x, au)
KRAKEN_24H = os.path.join(SD, "kraken_24h_all.json")
def kraken_sweep_liq(path=None, include_thin=False):
    """2026-10-29 kailiq: {'KRAKEN:<alt>': 24h row} for every sweep-liquid pair in kraken_24h_all.json (kraken_sweep.py, DoH). Fields absent in the sweep stay None."""
    try: K = json.load(open(path or KRAKEN_24H))
    except Exception: return {}
    out = {}
    for x in K.get("pairs") or []:
        if not x.get("alt") or not (x.get("liquid") or include_thin): continue
        out["KRAKEN:" + x["alt"]] = dict(vol24h_aud=x.get("vol24h_aud"), vol24h_usd=x.get("vol24h_usd"), last=x.get("last"),
            chg_pct_since_utc_open=x.get("chg_pct_since_open"), spread_bp=x.get("spread_bp"), liquid=bool(x.get("liquid")),
            liquidity_src="kraken_sweep (" + str(K.get("src") or "kraken REST") + ")", sweep_utc=K.get("utc"))
    return out
def sweep_row_for(ticker, liq=None):
    liq = kraken_sweep_liq(include_thin=True) if liq is None else liq
    t = (ticker or "").upper(); r = liq.get(t) or liq.get("KRAKEN:" + t.split(":")[-1])
    if r: return r
    b = t.split(":")[-1].replace("XBT", "BTC")
    for k, v in liq.items():
        kk = k.split(":")[-1].replace("XBT", "BTC")
        if kk == b or kk == b + "USD" or (b.endswith("USD") and kk == b): return v
    return None
def pack(c, seat, asof):
    if seat.get("universe") == "pit:all":   # 2026-10-29 breadth: pit:all = full liquid pit.sqlite equities (Dex path) + crypto core; never dump 1,300+ Kraken pairs into the prompt
        U = sorted(set(P.CRYPTO_CORE))
    else: U = sorted({s for g in seat["universe_groups"] for s in (P.CRYPTO_CORE if (g == "crypto" and seat.get("seat") != "crypto") else P.UNIVERSE[g])})  # non-crypto seats: crypto core only; Kai's crypto seat keeps the full Kraken list
    scan = None; scan_hits = None; ranked = None   # Brett 2026-10-29 13:09 WITA: coded pre-screen over full liquid DB; seat sees hand list + top 40 + flat scan line
    try:
        from veteran_desk import scan_universe as SU
        key = "pit:all" if seat.get("universe") == "pit:all" else seat["seat"]
        L, Y, Z = SU.seat_list(key)
        if key == "crypto" and L:   # 2026-10-29 Kai: universe = kraken_sweep 24h (>=A$500k, ex-stables) + core, not all 1,344 pairs
            U = sorted(set(P.CRYPTO_CORE) | set(L)); scan = "scanned %d of %d (%d cleared A$500k)" % (len(U), Y, Z)   # tfo_seatpipe_2_20261030 unified scan line
        elif L:
            try:   # tfo_seatpipe_6d_20261030: names with an 8-K in the last 2 days count as a news hit in the coded score (live runs only)
                from veteran_desk import feeds as _FD
                X8 = {r["sym"] for r in _FD.edgar_8k_recent()} if abs((now() - asof).total_seconds()) <= 900 else set()
            except Exception: X8 = set()
            top, rk, X = SU.rank_candidates(c, L, asof, int(os.environ.get("VET_SCAN_TOP", "120")), extra_news=X8, mode=seat.get("seat") if seat.get("seat") in ("trend", "price_action") else "default")   # tfo_seatpipe_dexsplit_20261030   # tfo_seatpipe_3_20261030: 40 -> ~120, coded score (was Dex trend-stack prescreen top 40)
            ranked = [dict(r, rank=i + 1) for i, r in enumerate(rk)]
            U = sorted(set(U) | set(top)); scan = "scanned %d of %d (%d cleared A$500k)" % (len(L), Y, Z)   # tfo_seatpipe_2_20261030: seat universe of seat total (cleared); prescreen hits=%d kept below
            scan_hits = X
        elif key not in ("crypto", "fx_commodities", "macro"):
            scan = "scanned 0 of %d (%d cleared A$500k) - seat sector map empty" % (Y, Z)
    except Exception as x: scan = "scan_universe unavailable (%s) - hand list only" % type(x).__name__
    EW = _engine_wake(seat.get("seat"))   # vd-engine (Taylor Hedge Fund) 2026-10-30: engine-driven wake -> its candidates join the pack (additive)
    if EW: U = sorted(set(U) | {x["sym"] for x in EW.get("candidates", [])})
    F = [f for f in (feats(c, s, asof) for s in U) if f]
    out = dict(asof_utc=asof.isoformat(), instruments=F)
    if EW: out["engine_wake"] = dict(wake_id=EW.get("wake_id"), candidates=EW.get("candidates"), top10=EW.get("top10"), note="Taylor Hedge Fund engine (code scorer) woke you on these A/A+ candidates. Advisory: your grade is capped at the engine grade for any name it scored; rails/gate unchanged.")
    if seat.get("seat") == "crypto":   # 2026-10-29 kailiq: verified 24h liquidity for EVERY sweep-liquid pair (not just feeds' 10)
        KL = kraken_sweep_liq()
        if KL:
            out["kraken_24h"] = {s: KL[s] for s in U if s in KL}
            out["kraken_24h_note"] = "source kraken_24h_all.json; chg_pct_since_utc_open = last vs Kraken Ticker 'o' (today's open, UTC 00:00), not rolling 24h; null = not in sweep"
            for f in F:
                r = KL.get(f["sym"])
                if r: f.update(vol24h_aud=r["vol24h_aud"], vol24h_usd=r["vol24h_usd"], liquidity_src=r["liquidity_src"], sweep_utc=r["sweep_utc"])
    if not scan: scan = "scanned %d of %d (%d cleared A$500k)" % (len(U), len(U), len(U))   # tfo_seatpipe_2_20261030: hand-list seats (fx/macro) - no A$500k sweep
    out["scan"] = scan
    if scan_hits is not None: out["scan_prescreen_hits"] = scan_hits
    if ranked: out["candidates_ranked"] = ranked; out["candidates_note"] = "coded score = trend + 0.5*(relvol-1) + news hit + 0.5*factor rank; grade the top 10 by rank, log a reject reason for each one you pass on"
    try:   # tfo_livequote_20261030 (Brett 23:16 WITA): seats priced off prior close -> inject paid live quotes (Massive US / EODHD-FMP intl via paid_feeds.fetch_quote)
        if not os.environ.get("PYTEST_CURRENT_TEST") and abs((now() - asof).total_seconds()) <= 900:
            import sys as _sys; _sys.path.insert(0, BASE) if BASE not in _sys.path else None
            import paid_feeds as _PFQ
            from concurrent.futures import ThreadPoolExecutor as _TPE
            LQ = [x["sym"] for x in (EW or {}).get("candidates", [])] + [r.get("sym") or r.get("ticker") for r in (ranked or [])[:12]] + [f["sym"] for f in F]
            LQ = [x for x in dict.fromkeys(LQ) if x and not str(x).upper().startswith("KRAKEN:")][:25]
            def _q(x):
                try: q = _PFQ.fetch_quote(x); return x, (dict(px=q.get("px"), ccy=q.get("ccy"), quote_utc=q.get("quote_utc"), status=q.get("status"), src=q.get("source") or q.get("provider")) if q and q.get("px") else None)
                except Exception: return x, None
            with _TPE(8) as _ex: LQd = dict(_ex.map(_q, LQ))
            out["live_quotes"] = {k: v for k, v in LQd.items() if v}
            out["live_quotes_note"] = "PAID live quotes fetched this wake (Massive real-time US; EODHD/FMP intl, may be exchange-delayed - see status). Set entry vs live px, NOT the prior daily close in instruments[]. Missing symbol = no paid quote; say so."
    except Exception as _x: out["live_quotes_err"] = type(_x).__name__
    try:   # tfo_seatpipe_5c_20261030: screens -> every seat's pack (daily value rank, essentials screen, unusual volume, intraday rescan). Advisory only; gate untouched.
        from veteran_desk import scan_universe as _SU
        syms = {f["sym"] for f in F}; FR = _SU._factor_rank(); ES = _SU.essentials_tags(); sc = {}
        vr = {k: round(v, 2) for k, v in FR.items() if k in syms}
        if vr: sc["value_rank_pct"] = vr
        es = {k: v for k, v in ES.items() if k in syms}
        if es: sc["essentials"] = es
        uv = [r["sym"] for r in (ranked or []) if r.get("unusual_vol")][:20]
        if uv: sc["unusual_volume"] = uv
        try:
            IR = json.load(open(os.path.join(BASE, "state", "intraday_rescan", "state.json")))
            arm = sorted({x if isinstance(x, str) else (x.get("sym") or x.get("ticker") or str(x)) for v in IR.values() for x in (v.get("newly_armed") or [])})
            sc["intraday_rescan"] = dict(newly_armed=arm[:20], last=max((v.get("finished_wita") or "") for v in IR.values()))
        except Exception: pass
        if sc: out["screens"] = sc; out["screens_note"] = "value_rank_pct = daily fundamental rank (1.0 best, reports/value_rank.json); essentials = passes essentials screen (core/spec); unusual_volume = relvol>=2 in your ranked list; intraday_rescan = names other desks armed today. Context only - rails/gate unchanged."
    except Exception: pass
    try:   # tfo_seatpipe_6b_20261030: earnings + macro calendar (state/calendar/calendar_latest.json, refreshed every 30 min by com.tfo.event-calendar)
        CAL = json.load(open(os.path.join(BASE, "state", "calendar", "calendar_latest.json"))); syms = {f["sym"] for f in F}
        er = [dict(sym=e["sym"], when_wita=e.get("when_wita"), title=(e.get("title") or "")[:90]) for e in CAL.get("next72", []) if e.get("type") == "earnings" and e.get("sym") in syms]
        mc = [dict(when_wita=e.get("when_wita"), title=(e.get("title") or "")[:90], cb=e.get("central_bank")) for e in CAL.get("next72", []) if e.get("sym") == "MACRO"][:6]
        out["calendar_72h"] = dict(asof_wita=CAL.get("generated_wita"), earnings=er, macro=mc, note="earnings inside 72h on your instruments: gap risk through the print - grade/size accordingly; rails unchanged")
    except Exception: pass
    try:
        N = json.load(open(os.path.join(SD, "news.json"))); out["news_regime"] = N["regime"]["flag"]
        out["news_tags"] = {k: v[:3] for k, v in (N.get("asset_tags") or {}).items() if k in {f["sym"] for f in F}}
    except Exception: out["news_regime"] = "unknown"
    try:   # catalyst stream: local advisory queue only. A missing file or a dead daemon never blocks this pack.
        from veteran_desk import catalyst_stream as _CS
        _CS.attach(out, seat.get("seat") or "")
    except Exception: pass
    m = {}
    for inp in seat["data_inputs"]:
        if inp.startswith("fred:"):
            for s in inp[5:].split(): r = P.macro_asof(c, s, asof, 5); m[s] = r[-1][:2] if r else None
    if m: out["macro"] = m
    # 2026-10-29 fixer (Rex stale WTI): 'pit:macro [SYMS]' was never wired - seats only saw lagged FRED (DCOILWTICO, ~1-8d behind).
    # Now: last 10 knowable daily closes per symbol from pit.sqlite bars (Yahoo primary, IBKR backfill), filtered at asof (no lookahead).
    mb = {}
    for inp in seat["data_inputs"]:
        if inp.startswith("pit:macro"):
            syms = inp.split()[1:] or ["CL=F", "BZ=F", "NG=F", "GC=F", "^TNX", "AUDUSD=X", "^GSPC"]
            for s in syms:
                try:
                    r = P.bars_asof(c, s, asof, 10)
                    mb[s] = dict(last_date=r[-1][0], last=r[-1][4], closes=[[x[0], x[4]] for x in r]) if r else None
                except Exception as e: mb[s] = dict(error="%s: %s" % (type(e).__name__, str(e)[:80]))
    if mb:
        out["macro_bars"] = mb
        out["macro_bars_note"] = "daily closes from pit bars (Yahoo primary, IBKR backup), knowable at asof; prefer these over FRED macro for live price levels (FRED oil lags 1+ days)"
    fd = [i[5:] for i in seat["data_inputs"] if i.startswith("feed:")]
    if "paid_pack" not in fd: fd.append("paid_pack")   # tfo_paidpush_20261030: every seat gets the paid-feed pack first (Yahoo/free = fallback)
    if fd:
        from veteran_desk import feeds as FD
        if abs((now() - asof).total_seconds()) <= 900:   # 2026-10-29: live feeds only in a LIVE run (never in a past-asof replay = no lookahead)
            out["live_feeds"] = {k: v for k, v in FD.fetch(fd).items() if "data" in v}   # each tagged source + available_at_utc
            ts_ = [v.get("available_at_utc") for v in out["live_feeds"].values() if v.get("available_at_utc")]
            if ts_ and max(ts_) > out["asof_utc"]:   # feeds are fetched seconds after run start; the seat decides after the fetch, so the pack's as-of is the fetch time (bars stay filtered at the run asof)
                out["bars_asof_utc"] = out["asof_utc"]; out["asof_utc"] = max(ts_)
        else: out["live_feeds_note"] = "past-asof run: live feeds withheld (lookahead guard)"
    if any(i.startswith("edgar") for i in seat["data_inputs"]):
        out["filings"] = {f["sym"]: P.filings_asof(c, f["sym"], asof, 3) for f in F if f["sym"] in P.CIKS}
    if "edgar:8k_fulltext" in seat["data_inputs"] and abs((now() - asof).total_seconds()) <= 900:   # tfo_seatpipe_6c_20261030: Quinn gets 8-K text (live runs only - lookahead guard)
        try:
            from veteran_desk import feeds as FD
            R = FD.edgar_8k_recent(); mine = {f["sym"] for f in F} | set((ranked and [r["sym"] for r in ranked]) or [])
            out["filings_8k"] = [FD.edgar_8k_text(dict(r)) for r in R if r["sym"] in mine][:10] + [dict(r, text=r.get("text")) for r in R if r["sym"] not in mine and r.get("text")][:6]
            out["filings_8k_note"] = "8-Ks filed last 2 days (EDGAR). items: 2.02 results, 1.01 material agreement, 5.02 officer change, 8.01 other, 7.01 Reg FD. text = first 1,500 chars of the filing. Only names in your universe/ranked list are tradable here."
        except Exception as x: out["filings_8k_note"] = "edgar 8-K fetch failed: %s" % type(x).__name__
    return out
# ---------------- model calls (foreground subprocess, never background) ----------------
CLAUDE_RETIRED = os.path.exists(os.path.join(BASE, "state", "config", "claude_retired.json")) or os.environ.get("TFO_CLAUDE_OFF") == "1"
def llm(engine, prompt, timeout=None):
    """Grok Bot queue only (Brett 22:29 WITA 30 Oct). Engine labels are legacy."""
    """GPT/Codex only (Brett 2026-10-29: Claude retired). Empty Codex answers return __ERR__ so _llm's 20s/60s 429-backoff can retry."""
    if engine == "claude": engine = "codex"   # Claude seats remapped; no Claude subprocess, no Claude fallback
    return _llm(engine, prompt, timeout)
import threading
_SEM = threading.BoundedSemaphore(int(os.environ.get("VET_LLM_CONCURRENCY", "2")))   # 2026-10-29 Marcus: 4 -> 2   # Mac guard: max concurrent model CLIs
USAGE = []; _UL = threading.Lock()
SECRET_RX = re.compile(r"(sk-[A-Za-z0-9]{12,}|api[_-]?key\s*[:=]|secret\s*[:=]|password\s*[:=]|BEGIN [A-Z ]*PRIVATE KEY)", re.I)
def _codex_stdout(txt):
    """Codex prints the final answer after a line 'codex' and before 'tokens used'; used when the -o file is missing/empty."""
    t = txt or ""
    i = t.rfind("\ncodex\n")
    if i >= 0: t = t[i + 7:]
    j = t.rfind("\ntokens used")
    return (t[:j] if j >= 0 else t).strip()
def _llm(engine, prompt, timeout=None, effort="high", fallback_for=None):
    """Strongest models: Claude opus; Codex gpt-6-astra at reasoning effort 'high' (use effort='low' only for bulk news triage).
    Back-off on rate limits (20s, 60s), concurrency-capped, prompts screened for secrets, every call logged."""
    LT = timeout or LLM_TIMEOUT
    if SECRET_RX.search(prompt or ""): return "__ERR__ prompt blocked: looks like it contains a secret"
    # tfo_grokqueue_20261030: Brett 22:29 WITA - Codex removed entirely; every model call goes to the Grok Bot file queue (no prompt cap)
    from veteran_desk import llm_backend as _LB
    _t0 = time.time(); out, _rid, _st = _LB.grok_call(engine, prompt, timeout, effort)
    rec = dict(utc=now().isoformat(), engine="grok_queue", model="grok_bot/file_queue", secs=round(time.time() - _t0, 1), tries=1, ok=_st in ("ok", "reused"),
               status=_st, queue_id=_rid, timeout=False, rate_limited=False, fallback_for=fallback_for, chars=len(out or ""), prompt_chars=len(prompt or ""), err=None if _st in ("ok", "reused") else "awaiting_grok")
    with _UL:
        USAGE.append(rec)
        try: os.makedirs(SD, exist_ok=True); open(os.path.join(SD, "model_usage.jsonl"), "a").write(json.dumps(rec) + "\n")
        except Exception: pass
    return out
DROUGHT_N = 6
def drought(sd, n=DROUGHT_N):
    """2026-10-29 (Brett): flag any proposer seat with NO proposal in its last n runs -> <state>/veteran_desk/seat_drought.json (read by the gate overlord)."""
    import glob
    R = []
    for p_ in sorted(glob.glob(os.path.join(sd, "runs", "*.json"))):
        try: R.append(json.load(open(p_)))
        except Exception: pass
    out = {}
    for sk in sorted({k for r in R for k in (r.get("seats") or {})}):
        mine = [r for r in R if sk in (r.get("seats") or {})][-n:]
        props = sum(1 for r in mine if (r["seats"][sk] or {}).get("action") == "propose")
        out[sk] = dict(runs_checked=len(mine), proposals=props, last_run=mine[-1].get("run_id") if mine else None, flag=len(mine) >= n and props == 0,
                       last_reasons=[str((r["seats"][sk] or {}).get("reason"))[:160] for r in mine[-2:]])
    _wj(os.path.join(sd, "seat_drought.json"), dict(utc=now().isoformat(), window_runs=n, flagged=[k for k, v in out.items() if v["flag"]], seats=out)); return out
def parse_json(txt):
    m = re.search(r"\{.*\}", txt or "", re.S)
    try: return json.loads(m.group(0)) if m else None
    except Exception:
        try: return json.loads(re.search(r"\{.*?\}\s*$", txt, re.S).group(0))
        except Exception: return None
RULES = ("PAPER research only. You may not arm or place anything. No Indonesian assets. No pump-chasing. Use ONLY the data given (it is point-in-time); "
         "do not use knowledge of prices after asof_utc. Reply with ONE JSON object and nothing else.")
def _subunit_div(sym):   # 2026-10-29 pence: .L (GBp) / .JO (ZAc) / .TA (ILA) prices are sub-units; traded value reported in major ccy (GBP etc)
    try:
        from veteran_desk import book as _B; return _B._SUBUNIT.get(_B.listing_ccy(sym) or "", ("", 1.0))[1]
    except Exception: return 100.0 if str(sym).upper().endswith(".L") else 1.0
def _major_ccy(sym):
    try:
        from veteran_desk import book as _B; c_ = _B.listing_ccy(sym) or "?"; return _B._SUBUNIT.get(c_, (c_, 1.0))[0]
    except Exception: return "?"
COMPACT_N = int(os.environ.get("VET_PROMPT_TOP_N", "10"))
def compact_pk(pk):   # tfo_fastllm_20261030 (Brett 23:31): seat prompt <=~15KB - top-10 candidates w/ live quotes + bars, no full-universe dumps
    if os.environ.get("VET_PROMPT_COMPACT", "1") != "1" or not isinstance(pk, dict): return pk
    try:
        q = dict(pk); ins = list(pk.get("instruments") or [])
        rk = [str(x.get("sym") or x.get("ticker") or x) if isinstance(x, dict) else str(x) for x in (pk.get("candidates_ranked") or [])]
        if rk: ins = sorted(ins, key=lambda x: rk.index(x.get("sym")) if x.get("sym") in rk else 999)
        else: ins = sorted(ins, key=lambda x: -(x.get("adv20_value") or x.get("vol24h_usd") or 0))
        keep = ins[:COMPACT_N]; syms = {x.get("sym") for x in keep}
        drop = ("knowable_utc", "sweep_utc", "liquidity_src", "adv20_value_ccy", "venue")
        q["instruments"] = [{k: v for k, v in x.items() if k not in drop} for x in keep]
        q["instruments_note"] = f"top {len(keep)} of {len(ins)} by rank/liquidity (compact prompt)"
        if isinstance(pk.get("kraken_24h"), dict): q["kraken_24h"] = {k: v for k, v in pk["kraken_24h"].items() if k in syms}
        if isinstance(pk.get("candidates_ranked"), list): q["candidates_ranked"] = pk["candidates_ranked"][:COMPACT_N]
        lf = pk.get("live_feeds")
        if isinstance(lf, dict):
            def _f(v, depth=0):
                if isinstance(v, dict):
                    hit = {k: x for k, x in v.items() if any(s_ and (s_ in str(k) or str(k) in s_) for s_ in syms)}
                    if hit: return hit
                    return {k: _f(x, depth + 1) for k, x in list(v.items())[:6]} if depth < 2 else None
                if isinstance(v, list): return v[:5]
                return v
            q["live_feeds"] = {k: _f(v) for k, v in lf.items()}
        if isinstance(pk.get("live_quotes"), dict): q["live_quotes"] = {k: v for k, v in pk["live_quotes"].items() if k in syms or k in [str(r)[:20] for r in rk[:COMPACT_N]]} or dict(list(pk["live_quotes"].items())[:COMPACT_N])
        for k, v in list(q.items()):
            if k not in ("instruments", "live_feeds", "live_quotes") and len(json.dumps(v, default=str)) > 1500: q[k] = json.dumps(v, default=str)[:700] + "...(trimmed)"
        lj = json.dumps(q.get("live_feeds"), default=str)
        if len(lj) > 2000: q["live_feeds"] = lj[:2000] + "...(trimmed)"
        return q
    except Exception: return pk
_SLIM_DROP = ("last_date", "adv20", "vol24h_aud", "liquidity_src", "sweep_utc", "knowable_utc")   # tfo_fastllm5: redundant with adv20_value / vol24h_usd / asof_utc
def _r4(v):
    if isinstance(v, float): return float(f"{v:.5g}")
    if isinstance(v, list): return [_r4(x) for x in v]
    if isinstance(v, dict): return {k: _r4(x) for k, x in v.items()}
    return v
def _slim(q):   # tfo_fastllm5 (head trader 03:09 WITA): dedupe + 5-sig-fig numbers; all feed fields the seat uses kept
    if os.environ.get("VET_PROMPT_SLIM", "1") != "1" or not isinstance(q, dict): return q
    try:
        q = dict(q)
        if isinstance(q.get("instruments"), list): q["instruments"] = [{k: v for k, v in x.items() if k not in _SLIM_DROP} if isinstance(x, dict) else x for x in q["instruments"]]
        return _r4(q)
    except Exception: return q
def _lessons_slim(seat):   # advisory sounding-board critiques trimmed to 240 chars per line; lessons/rails text untouched
    t = _lessons(seat)
    if os.environ.get("VET_PROMPT_SLIM", "1") != "1" or "Sounding boards" not in t: return t
    try:
        head, _, rest = t.partition("### Sounding boards")
        return head + "### Sounding boards" + "\n".join((l[:240] + "...") if len(l) > 240 else l for l in rest.split("\n"))
    except Exception: return t
def prop_prompt(seat, pk):
    return (f"You are the '{seat['seat']}' seat on a veteran trading desk. Role: {seat['description']}\nPlaybook (habits of veteran traders):\n- " + "\n- ".join(seat["playbook"]) +
            f"\nFUND POSTURE: risk-off fund tilted slightly speculative. Favour defensive sectors; tech/neocloud allowed LONG or SHORT on the data. Shorts need borrow. 'spec' sleeve = small asymmetric winner with a TIGHT stop (<=1.5 ATR) and >=3:1 payoff, never a chase.\nNEWS REGIME: {pk.get('news_regime')}\nSCAN: {pk.get('scan') or 'hand list only'}\n{RULES}\nPick at most ONE setup from the instruments below, or stand aside. Grade honestly: A+ only if trend, clean chart, catalyst/reason and room to target all line up. "
            "Schema: {\"action\":\"propose\"|\"stand_aside\",\"ticker\":str,\"side\":\"LONG\"|\"SHORT\",\"entry\":num,\"stop\":num,\"target\":num,\"grade\":\"A+\"|\"A\"|\"B\","
            "\"sleeve\":\"core\"|\"spec\",\"horizon_days\":int,\"thesis\":str,\"invalidation\":str,\"why_not_choppy\":str,\"graded\":[{\"ticker\":str,\"grade\":\"A+\"|\"A\"|\"B\"|\"reject\",\"reason\":str}],"
            "\"risk_pct\":num (A only: A risk 1.0-2.0 % of seat equity (1% allowed until proven; lift to 2% once proven; do NOT invent a higher size); A+ is 4%; non-A needs 2%),\"lessons_respected\":str,\"confirm\":true (set ONLY if you want this proposal armed; omitted -> never arms),\"ranked\":[{\"ticker\":str,\"side\":\"LONG\"|\"SHORT\",\"grade\":\"A+\"|\"A\"|\"B\",\"why\":str}],\"best_long\":str|null,\"best_short\":str|null}\n"   # tfo_seatpipe_3_20261030 + tfo_seatpipe_7a_20261030
            "Always fill 'ranked' with your best 3-5 setups (A+/A/B, best first) even if you stand aside, and name best_long and best_short (null if none worth a B). The proposal, if any, must be ranked[0].\n"
            "If DATA has candidates_ranked: grade its top 10 in rank order in 'graded' (each reject needs a one-line reason).\n" + "".join(x + "\n" for x in seat.get("prompt_overrides", [])) + "DATA:\n" + json.dumps(_slim(compact_pk(pk)), separators=(",", ":"), default=str) + _lessons_slim(seat) + _ll_block(seat) + _thf_line(seat) + _seat_mem(seat) + PAID_PUSH_RULE)
PAID_PUSH_RULE = ("\nSTANDING ORDER (Brett 30 Oct 2026 21:17 WITA, all desks incl. Taylor Hedge Fund; every scheduled wake): "   # tfo_paidpush_20261030
    "(1) PAID FEEDS FIRST - price/judge from the paid sources in DATA (massive_* real-time US + Benzinga news, eodhd fundamentals/earnings, fmp, unusual_whales flow/dark pool/congress, coinalyze crypto derivs (funding/OI/liquidations/long-short), eia energy); Kraken for crypto prices; Yahoo is FALLBACK only - say which source you used. "
    "(2) BIG PUSH at the US open: hunt hard and get QUALITY A/A+ trades filled; never force a C (or weak B) setup - standing aside beats a bad trade. "
    "(3) Every rail stays: stop on every trade, heat caps, 60% US beta cap, A 1-2% (1% ok until proven) / A+ 4% / cap 5%; non-A needs 2%; A may go to 1.5% only when the 40% single-stock cap binds, PAPER ONLY.\n")
SEAT_MEM_SUMMARY_MAX = 900
def _seat_mem(seat, desk=None, full=None):   # tfo_seatmem_20261030 (Brett 22:19): per-seat CORE MEMORY from paid-feed history; Grok gets the full memory (Codex removed 22:29)
    try:
        desk = desk or ("thf" if os.environ.get("THF_DESK") == "1" else "veteran_desk")
        d = os.path.join(BASE, "state", desk, "seat_memory")
        if full is None: full = os.environ.get("SEAT_MEM_MODE", "summary") == "full"   # tfo_fastllm_20261030: summary default (prompt <=15KB)
        if full:
            t = open(os.path.join(d, seat["seat"] + ".md")).read(); return "\nCORE MEMORY (history base rates; advisory, never loosens rails):\n" + t
        m = json.load(open(os.path.join(d, seat["seat"] + ".json")))
        su = "; ".join(f"{k} n{v['n']} win{v.get('win_rate')} R{v.get('avg_r')}" for k, v in m["setups"].items() if v.get("n"))
        hv = ", ".join(f"{x['sym']} p{x['pctile_5y']}" for x in m.get("vol_regime", [])[:4])
        return (f"\nCORE MEMORY (summary; {m.get('date_range')}): setups {su}; high-vol {hv}\n")[:SEAT_MEM_SUMMARY_MAX]
    except Exception: return ""
def _ll_block(seat):   # tfo_review_20261030 learning loop: last 5 own auto-lessons + other seats' biggest wins/losses; proposal must say how it respects them
    try:
        from veteran_desk import lesson_loop as LL, book as BK; return LL.prompt_block(seat["seat"], BK._state_dir())
    except Exception: return ""
def _thf_line(seat):   # tfo_thfreview_20261030 (Brett 19:32 WITA): THF stern review / praise per round; no-op outside THF
    try:
        from veteran_desk import thf_review as TR; return TR.prompt_line(seat["seat"], ENGINE_WAKE or now().strftime("%Y%m%d-%H%M")) if TR.enabled() else ""   # tfo_review_20261030: VD too
    except Exception: return ""
def _lessons(seat):   # learning loop (Brett 2026-10-28 20:26 WITA): seat reads its own lessons.md before it hunts; advisory, never loosens rails
    try:
        from veteran_desk import learning as LRN; return LRN.lessons_for_prompt(seat["seat"])
    except Exception: return ""
def _live_block(f):   # tfo_chal_live_20261030
    try:
        from veteran_desk import live_ctx as _LC; x = _LC.ctx((f or {}).get("sym")); return ("\nLIVE_TODAY:\n" + json.dumps(x, default=str)) if x else ""
    except Exception: return ""
def chal_prompt(f):
    return ("You are a blind challenger seat. You have NOT seen anyone else's view. Give your own independent call on this one instrument. " + RULES +
            " Schema: {\"call\":\"LONG\"|\"SHORT\"|\"STAND_ASIDE\",\"conviction\":0-5,\"reason\":str}\nDATA:\n" + json.dumps(f) + _live_block(f))
def da_prompt(p, f, pending):
    return ("You are the devil's advocate. Make the strongest case this proposal LOSES money. Check: crowding, liquidity, catalyst already priced, correlation to pending book, "
            "stale data, stop too tight/too wide, chasing. Set factual_kill=true ONLY if you can cite a concrete fact from the DATA that breaks the trade. " + RULES +
            " Schema: {\"kill_case\":str,\"factual_kill\":bool,\"fact\":str,\"checks\":{\"crowding\":str,\"liquidity\":str,\"priced_in\":str,\"correlation\":str,\"stale\":str}}\n"
            "PROPOSAL:\n" + json.dumps(p) + "\nDATA:\n" + json.dumps(f) + _live_block(f) + "\nPENDING BOOK:\n" + json.dumps(pending))
# ---------------- code-only risk gate ----------------
def risk_gate_v1(p, f, chal, da, asof, run_risk_used):
    why = []; ok = lambda cond, msg: (None if cond else why.append(msg))
    t = p.get("ticker") or ""; side = p.get("side"); e, s, tg = (float(p.get(k) or 0) for k in ("entry", "stop", "target"))
    ok(not P.banned(t), "Indonesian asset banned"); ok(f is not None, "no PIT data")
    if f is None: return dict(passed=False, reasons=why)
    ok(p.get("grade") in ("A+", "A"), f"grade {p.get('grade')} - B or worse is a pass"); ok(side in ("LONG", "SHORT"), "bad side")
    ok(s > 0 and ((side == "LONG" and s < e < tg) or (side == "SHORT" and tg < e < s)), "stop/target not on correct sides of entry")
    a = f["atr14"]; dist = abs(e - s)
    ok(0.5 * a <= dist <= 4 * a, f"stop distance {dist:.4g} outside 0.5-4 ATR ({a:.4g})")
    ok(abs(e / f["close"] - 1) <= 0.03, "entry more than 3% from last close")
    rr = abs(tg - e) / dist if dist else 0; ok(rr >= 1.5, f"reward:risk {rr:.2f} < 1.5")
    ok(not f["choppy"], f"choppy tape (efficiency {f['efficiency_ratio20']}) - stand aside")
    chase = (side == "LONG" and (f["ret5"] > .12 or f["ret20"] > .25 or f["close"] > f["sma20"] + 3 * a)) or (side == "SHORT" and (f["ret5"] < -.12 or f["ret20"] < -.25 or f["close"] < f["sma20"] - 3 * a))
    ok(not chase, "pump/dump chase - move already happened")
    age = (asof - dt.datetime.fromisoformat(f["knowable_utc"])).total_seconds() / 86400; ok(age <= 4, f"data stale {age:.1f} days")
    ok(isinstance(chal, dict) and chal.get("call") != ("SHORT" if side == "LONG" else "LONG"), f"blind challenger called the opposite ({(chal or {}).get('call')})")
    ok(not (isinstance(da, dict) and da.get("factual_kill")), f"devil's advocate factual kill: {(da or {}).get('fact')}")
    ok(isinstance(chal, dict) and isinstance(da, dict), "challenger or advocate missing - no tick without the full debate")
    spec = p.get("sleeve") == "spec"
    if spec: ok(dist <= 1.5 * a and rr >= 3, "spec sleeve needs tight stop <=1.5 ATR and >=3:1 payoff")
    if side == "SHORT":
        v = f["venue"]
        if v == "CRYPTO": why.append("no crypto shorts on Kraken spot")
        elif v in ("US", "ASX"):
            try: bn = json.load(open(os.path.join(BASE, "state", "ibkr_borrow.json")))[v.lower()]["names"].get(t.replace(".AX", ""))
            except Exception: bn = None
            ok(bool(bn and bn.get("ok") and (bn.get("available") or 0) > 0), "short needs borrow available (IBKR borrow file)")
    risk = RISK_CAP_PER_TRADE if p.get("grade") == "A+" and isinstance(chal, dict) and chal.get("call") == side else 500
    ok(run_risk_used + risk <= RISK_CAP_RUN, "run risk cap A$4,000 reached")
    return dict(passed=not why, reasons=why, risk_aud=risk, rr=round(rr, 2))
def risk_gate(p, f, chal, da, asof, run_risk_used, others=None, au=0.65):
    """v2 (2026-10-28): full PIT history, hard real-risk rules only, missing data -> size-down. v1 kept above for audit."""
    from veteran_desk import gate_v2 as G2
    seat = (p or {}).get("_seat")
    from veteran_desk import book as BK
    g = G2.gate(p, f, chal, da, asof, run_risk_used, others=others, au=au, th=dict(seat_equity_aud=BK.seat_equity(seat), seat=seat) if seat else None)   # tfo_seatpipe_dexrail_20261030
    try:   # tfo_thffresh_20261030 (GPT review HIGH): THF fails CLOSED when equity ADV/liquidity cannot be verified (FX/IDX/FUT/G10 exemptions unchanged)
        from veteran_desk import thf_review as TR0
        if TR0.active() and any("no volume data - ADV unverified" in str(w) for w in (g.get("warnings") or [])):
            g["passed"] = False; g.setdefault("reasons", []).append("thf_liquidity_unverified: no volume data - A$500k floor / 5% ADV cannot be proven (fail closed)")
    except Exception: pass
    try:   # tfo_thfreview_20261030: THF seat red 3 straight rounds -> half risk until a green round; never lifted over the floor
        from veteran_desk import thf_review as TR
        if seat and TR.enabled() and TR.size_mult(seat) < 1 and g.get("risk_aud"):   # tfo_review_20261030: both engines; half of an A = 1% floor (allowed)
            fl = BK.RISK_CRYPTO_MIN if (p.get("venue") or P.venue_of(p["ticker"])) == "CRYPTO" else BK.RISK_MIN; pct0 = float(g.get("risk_pct") or 0); pct = pct0 * TR.size_mult(seat)
            if p.get("grade") in ("A", "A+") and pct < fl - 1e-9 <= pct0: pct = fl
            g.setdefault("warnings", []).append("THF half risk (red 3 straight rounds) x%.2f" % TR.size_mult(seat))
            if pct < fl - 1e-9: g["passed"] = False; g.setdefault("reasons", []).append("thf_half_risk: %.2f%% below %.1f%% floor - rejected (A+ only while half risk)" % (pct * 100, fl * 100))
            else: k_ = pct / pct0 if pct0 else TR.size_mult(seat); g.update(risk_pct=round(pct, 5), risk_aud=round(g["risk_aud"] * k_), size_mult=round(float(g.get("size_mult") or 1) * k_, 3))
    except Exception as x: g.setdefault("warnings", []).append("thf half-risk check error %s" % type(x).__name__)
    return g
def size(p, f, risk_aud, au):
    from veteran_desk import book as BK
    e, s, tg = float(p["entry"]), float(p["stop"]), float(p["target"])
    try: per = to_aud(p["ticker"], abs(e - s), au)
    except BK.FxUnavailable as x: return dict(shares=0, risk_aud=0, win_aud=0, cost_aud=0, net_win_2x_cost_aud=0, passes_doubled_cost=False, fx_error=str(x))
    q = max(int(risk_aud / per), 0) if per > 0 else 0; win = to_aud(p["ticker"], abs(tg - e), au) * q
    cost = to_aud(p["ticker"], e, au) * q * CT.est_rt_rate(p["ticker"], P.venue_of(p["ticker"]), e)
    return dict(shares=q, risk_aud=round(per * q), win_aud=round(win), cost_aud=round(cost, 2), net_win_2x_cost_aud=round(win - 2 * cost),
                passes_doubled_cost=(win - 2 * cost) > 1.5 * (per * q + 2 * cost) * 0.5)
# ---------------- pipeline ----------------
def ran_engine(e): return "codex" if e in ("claude", None, "") else e  # 2026-10-29: record engine that actually ran (claude remapped)
def name(S, k): return (S.get(k) or {}).get("trader_name") or k
SHADOW_LOG = os.path.join(SD, "shadow_gate.jsonl")
def _shadow_gate(runid, seat, p, g):
    """tfo_seatpipe_8_20261030: shadow log, gate untouched. For every gate FAIL, classify its hard reasons:
    history = PIT-history/base-rate blocks; floor = below the 2% risk floor. would_pass_soft_history: only history blocks remain.
    would_pass_floor_1_5: only floor blocks and natural size >= 1.5%. Never feeds back into arming."""
    try:
        if g.get("passed") or os.environ.get("PYTEST_CURRENT_TEST"): return   # never write the live shadow log from tests (TEST GUARD)
        R = list(g.get("reasons") or []); hist = [r for r in R if "history" in r.lower()]; flo = [r for r in R if r.startswith("below risk floor")]
        other = [r for r in R if r not in hist and r not in flo]; m = re.search(r"natural size ([0-9.]+)%", " ".join(flo)); nat = float(m.group(1)) if m else None
        rec = dict(utc=now().isoformat(), run_id=runid, seat=seat, ticker=p.get("ticker"), side=p.get("side"), grade=p.get("grade"), hard=R,
                   would_pass_soft_history=bool(hist) and not other and not flo, would_pass_floor_1_5=bool(flo) and not other and not hist and nat is not None and nat >= 1.5,
                   would_pass_both=bool(hist or flo) and not other and (not flo or (nat is not None and nat >= 1.5)), natural_size_pct=nat, shadow_only=True)
        with open(SHADOW_LOG, "a") as fh: fh.write(json.dumps(rec) + "\n")
    except Exception: pass
OPEN_RUNS_WITA = ((7, 5), (8, 5), (9, 35), (16, 5), (22, 35))   # tfo_seatpipe_7b_20261030: weekday market-open pipeline runs (com.tfo.veteran-desk plist)
def _open_run(asof, t=None):
    """True only for a LIVE run starting 0-14 min after a weekday market-open slot (WITA = UTC+8). Replays/backtests never re-scan."""
    t = t or now()
    if abs((t - asof).total_seconds()) > 900 or os.environ.get("VET_RESCAN") == "0": return False
    w = t.astimezone(dt.timezone(dt.timedelta(hours=8)))
    return w.weekday() < 5 and any(0 <= (w.hour * 60 + w.minute) - (h * 60 + m) <= 14 for h, m in OPEN_RUNS_WITA)   # 14 not 25: the 07:20 odd-hour run must not re-scan again
def run(use_llm=True, queue=True, asof=None, queue_path=None, only=None):
    t0 = time.time(); asof = asof or now(); c = P.con(); S = seats(); au = audusd(c, asof); runid = asof.strftime("%Y%m%d-%H%M")
    props = [s for s in S.values() if s["role"] == "proposer" and (not only or s["seat"] in only)]; log = dict(run_id=runid, asof_utc=asof.isoformat(), paper_only=True, seats={}, proposals=[])
    packs = {s["seat"]: pack(c, s, asof) for s in props}
    def go(s):
        if not use_llm: return s["seat"], None, "llm disabled"
        txt = llm(s["engine"], prop_prompt(s, packs[s["seat"]])); return s["seat"], parse_json(txt), (txt or "")[:400]
    with cf.ThreadPoolExecutor(MAX_PARALLEL) as ex: res = list(ex.map(go, props))   # blind: each seat sees only its own pack
    rescanned = {}
    if use_llm and _open_run(asof):   # tfo_seatpipe_7b_20261030 (TFO ruling 18:28 WITA): flat-run re-scan ONLY on the 5 weekday market-open runs, ONLY for seats that stood aside; max 11 extra calls/run (~55/weekday)
        flat = [s for s in props if not any(r[0] == s["seat"] and r[1] and r[1].get("action") == "propose" for r in res)]
        def go2(s):
            pk = dict(packs[s["seat"]]); rk = pk.get("candidates_ranked") or []
            if len(rk) > 10: pk["candidates_ranked"] = rk[10:40]
            pk["rescan_note"] = "SECOND LOOK (flat first pass at a market open): your first pass stood aside. " + ("candidates_ranked now holds ranks 11-40 (unseen); grade its top 10 in 'graded'. " if len(rk) > 10 else "Re-check the same instruments for any A/A+ you missed. ") + "Same rules and gate; standing aside again is fine."
            txt = llm(s["engine"], prop_prompt(s, pk)); return s["seat"], parse_json(txt), (txt or "")[:400]
        with cf.ThreadPoolExecutor(MAX_PARALLEL) as ex: res2 = list(ex.map(go2, flat))
        for seat, j2, raw2 in res2:
            rescanned[seat] = dict(action=(j2 or {}).get("action"), ticker=(j2 or {}).get("ticker"), ranked=(j2 or {}).get("ranked"), graded=(j2 or {}).get("graded"))
            if j2 and j2.get("action") == "propose" and j2.get("ticker"): res = [(x[0], j2, raw2) if x[0] == seat else x for x in res]
    pending = []
    for seat, j, raw in res:
        log["seats"][seat] = dict(trader=name(S, seat), engine=ran_engine(S[seat]["engine"]), reason=(j or {}).get("thesis") or (j or {}).get("reason") or (j or {}).get("why_not_choppy"), raw_ok=bool(j), action=(j or {}).get("action"), raw_head=None if j else raw, scan=packs[seat].get("scan"), graded=(j or {}).get("graded"), ranked=(j or {}).get("ranked"), best_long=(j or {}).get("best_long"), best_short=(j or {}).get("best_short"))   # tfo_seatpipe_3_20261030: top-10 grades + reject reasons logged   # tfo_seatpipe_2_20261030: scan line saved per seat in every run file
        if seat in rescanned: log["seats"][seat]["rescan"] = rescanned[seat]   # tfo_seatpipe_7b_20261030
        try:   # tfo_thfreview_20261030: persist red seats' recovery plans for Marcus (THF only)
            from veteran_desk import thf_review as TR
            if TR.enabled(): TR.record_plan(seat, ENGINE_WAKE or runid, j, name(S, seat))
            if isinstance(j, dict) and not os.environ.get("PYTEST_CURRENT_TEST"): j["_ll_required"] = True   # tfo_review_20261030: live proposals must carry lessons_respected
        except Exception: pass
        if j and j.get("action") == "propose" and j.get("ticker"):
            cap = _engine_cap(seat, j)   # vd-engine: engine grade can only CAP the seat grade, never lift
            if cap: log["seats"][seat]["engine_grade_cap"] = cap
            pending.append((seat, j))
    used = 0; out = []
    FE = {p["ticker"]: (feats(c, p["ticker"], asof) if not P.banned(p["ticker"]) else None) for _, p in pending}   # sqlite in main thread only
    budget = [SUB_AGENTS_PER_RUN]
    def subagents(seat, p, f):
        """Per-trade analysis sub-agents (research, chart read, filings dig), capped per trade and per run; notes logged on the trade."""
        tasks = [("research", "codex", "Research analyst: list the 3 most important facts in DATA for and against this trade."),
                 ("chart_read", "codex", "Chart reader: describe trend, support/resistance and whether this is a chase, using only DATA closes/levels."),
                 ("filings_dig", "codex", "Filings analyst: from DATA filings/feeds, is there any event risk in the horizon? Say 'none in data' if none."),
                 ("second_opinion", "codex", "Second-opinion trader: would you take this exact trade? Yes/no and the one reason."),
                 ("news_check", "codex", "News checker: does any headline in DATA news tags threaten this trade?")][:SUB_AGENTS_PER_TRADE]  # Brett HARD 2026-10-29: Codex only
        n = min(len(tasks), budget[0]); budget[0] -= n
        if not use_llm or n <= 0: return []
        def one(t): return dict(agent=t[0], engine=t[1], note=(llm(t[1], t[2] + " " + RULES.replace("Reply with ONE JSON object and nothing else.", "Reply in under 80 words.") + "\nPROPOSAL:" + json.dumps(p) + "\nDATA:" + json.dumps(f), SUB_TIMEOUT) or "")[:600])
        with cf.ThreadPoolExecutor(min(n, MAX_PARALLEL)) as ex: return list(ex.map(one, tasks[:n]))
    def debate(item):
        seat, p = item; f = FE.get(p["ticker"])
        other = "codex"   # Brett HARD 2026-10-29: debate on GPT/Codex only, never Claude
        chal = parse_json(llm(other, chal_prompt(f))) if (use_llm and f) else None
        da = parse_json(llm("codex", da_prompt(p, f, [x[1] for x in pending if x[1] is not p]))) if (use_llm and f) else None
        return seat, p, f, chal, da, other, (subagents(seat, p, f) if f else [])
    with cf.ThreadPoolExecutor(min(max(len(pending), 1), 2)) as ex: deb = list(ex.map(debate, pending))
    seen = set()
    for seat, p, f, chal, da, other, subs in sorted(deb, key=lambda x: x[1].get("grade") != "A+"):
        g = risk_gate(dict(p, _seat=seat), f, chal, da, asof, used, others=[dict(ticker=r["proposal"]["ticker"], side=r["proposal"].get("side"), risk_aud=r["gate"].get("risk_aud") or 0, us_beta_usd=r["gate"].get("us_beta_usd") or 0) for r in out if r["gate"]["passed"]], au=au)
        _shadow_gate(runid, seat, p, g)   # tfo_seatpipe_8_20261030: SHADOW ONLY - logs what a softer history rule / 1.5% floor would pass; g is never changed
        if p["ticker"] in seen: g["passed"] = False; g["reasons"].append("duplicate ticker this run - one owner per trade")
        if f and f["venue"] == "CRYPTO" and g["passed"]:   # gate v2 law: missing data cuts size, never blocks; data showing a thin coin still blocks
            liq = ((packs.get(seat, {}).get("live_feeds", {}).get("kraken_spot") or {}).get("data") or {})
            v = [x for k, x in liq.items() if p["ticker"].split(":")[-1][:3] in k.replace("XXBT", "XBT").replace("XETH", "ETH")]; thin = False
            if v:   # Brett 2026-10-29 13:02 WITA: floor A$500k 24h traded value (was US$20m), converted via book.aud
                from veteran_desk import book as BK, feeds as FDQ
                try: v24 = BK.aud(p["ticker"], float(v[0].get("vol24h_usd") or 0), au)
                except Exception: v24 = None
                thin = (v24 < FDQ.MIN_24H_AUD) if v24 is not None else not v[0].get("liquid")
            if not v:   # 2026-10-29 kailiq: feeds row missing -> use the kraken_sweep 24h figure (same A$500k floor)
                sr = sweep_row_for(p["ticker"])
                if sr and sr.get("vol24h_aud") is not None:
                    from veteran_desk import feeds as FDQ
                    v = [sr]; thin = float(sr["vol24h_aud"]) < FDQ.MIN_24H_AUD
            if v and thin: g["passed"] = False; g["reasons"].append("thin coin (Kraken 24h < A$500k) - pass")
            elif not v:
                g.setdefault("warnings", []).append("no Kraken liquidity data this run - size x0.5"); g["size_mult"] = round(max((g.get("size_mult") or 1.0) * .5, .25), 3)
                from veteran_desk import book as BK
                rp = BK.risk_plan(g.get("seat_equity_aud") or BK.seat_equity(seat), p.get("grade"), g.get("chal_agree"), g["size_mult"], floor=BK.RISK_CRYPTO_MIN)   # railfix 2026-10-29: crypto seat floor 0.5%; below floor -> reject, never lift
                g["risk_aud"] = round(rp["risk_aud"]); g["risk_pct"] = rp["pct"]
                if rp.get("reject"): g["passed"] = False; g["reasons"].append(rp["reject"])   # kairails: below 0.5% after the x0.5 cut -> below_crypto_floor
                elif f and float(p.get("entry") or 0) > 0:   # kairails: re-apply the A$50k/A$100k A+ clip cap after the resize
                    try:
                        k2 = BK.aud_per_unit(p["ticker"], au); r2, _q, rj = BK.crypto_clip(g["risk_aud"], abs(float(p["entry"]) - float(p["stop"])) * k2, float(p["entry"]) * k2, g.get("seat_equity_aud") or BK.seat_equity(seat), p.get("grade"))
                        g["risk_aud"] = round(r2)
                        if rj: g["passed"] = False; g["reasons"].append(rj)
                    except Exception: pass
                if rp.get("reject"): g["passed"] = False; g["reasons"].append(rp["reject"])
                if rp["warning"]: g["warnings"].append(rp["warning"])
        seen.add(p["ticker"]); sz = size(p, f, g.get("risk_aud", 500), au) if g["passed"] else None
        if sz and not sz["passes_doubled_cost"]: g["passed"] = False; g["reasons"].append("fails doubled-cost hurdle")
        if g["passed"]: used += sz["risk_aud"]
        if g.get("quote_asof") and not p.get("quote_asof"):
            p = dict(p, quote_asof=g.get("quote_asof"), quote_last=g.get("quote_last"), quote_src=g.get("quote_src"))
        rec = dict(id=f"VET-{re.sub(r'[^A-Z0-9]', '', p['ticker'].upper())}-{runid}-{seat}", seat=seat, trader=name(S, seat), sub_agents=subs, engine=ran_engine(S[seat]["engine"]), proposal=p,
                   challenger=dict(engine=other, trader=name(S, "blind_challenger"), view=chal), devils_advocate=da, gate=g, sizing=sz, features=f or dict(sym=p["ticker"], venue=P.venue_of(p["ticker"]), pit_missing=True),
                   universe_bias=P.VENUE_BIAS.get(P.venue_of(p["ticker"]), ("?",))[0], created_utc=now().isoformat(), info_only=True, seat_reply_ok=True)
        rec["hash"] = hashlib.sha256(json.dumps([p, sz], sort_keys=True, default=str).encode()).hexdigest()[:16]; out.append(rec)
    log["proposals"] = out; log["head_of_desk"] = head_of_desk(S, out, use_llm)
    if queue: log["queued"] = enqueue([r for r in out if r["gate"]["passed"]], au, queue_path, ack_required=not os.environ.get("PYTEST_CURRENT_TEST"))   # tfo_deskrun_ack_20261031: desk-run proposals (ghost-arm ABBV-1521/ISRG) arm only after seat_cli confirm
    if use_llm and os.environ.get("VET_SOUNDING_BOARD", "1") == "1":   # 2026-10-28 20:47 WITA Brett "trim the advisory": critique AFTER enqueue, detached, 1 model/trade, 60s cap; never blocks
        try:
            from veteran_desk import learning as LRN; log["sounding_board"] = LRN.critique_async([r for r in out if r["gate"]["passed"]], runid)
        except Exception as x: log["sounding_board_error"] = "%s: %s" % (type(x).__name__, str(x)[:160])
    try:   # fix 5/7: VIX quote + its own timestamp on every run log / latest.json
        from veteran_desk import feeds as FDV; vq = FDV.vix_live() or {}
        log.update(vix_last=vq.get("last"), vix_asof=vq.get("asof"), vix_src=vq.get("src"), vix_stale=bool(vq.get("delayed")) if vq else True, vix_stale_why=vq.get("stale_why"))
    except Exception as x: log.update(vix_last=None, vix_asof=None, vix_error="%s: %s" % (type(x).__name__, str(x)[:120]))
    if ENGINE_WAKE:   # vd-engine: additive wake runs never overwrite the batch pipeline's latest.json / desk card
        log["engine_wake_id"] = ENGINE_WAKE; _wj(os.path.join(SD, "runs", f"{runid}-eng.json"), log); _wj(os.path.join(BASE, "state", "vd_engine", "runs", f"{ENGINE_WAKE}.json"), log)
    else: _wj(os.path.join(SD, "runs", f"{runid}.json"), log); _wj(os.path.join(SD, "latest.json"), log)
    try: drought(SD)
    except Exception: pass
    track(out); sc = score(); sheet(log)
    U = list(USAGE); health(dict(model_usage=dict(calls=len(U), ok=sum(u["ok"] for u in U), timeouts=sum(u["timeout"] for u in U), rate_limited=sum(u["rate_limited"] for u in U),
        by_engine={e: sum(1 for u in U if u["engine"] == e) for e in ("codex",)}, avg_secs=round(sum(u["secs"] for u in U) / len(U), 1) if U else None), ok=True, last_run_utc=now().isoformat(), run_id=runid, secs=round(time.time() - t0), llm=use_llm,
                seats_ok=sum(1 for v in log["seats"].values() if v["raw_ok"]), seats_total=len(props), proposals=len(out), passed=sum(r["gate"]["passed"] for r in out)))
    if not ENGINE_WAKE: card(log, sc)
    return log
HOD = os.path.join(SD, "head_of_desk.json")

WITA = dt.timezone(dt.timedelta(hours=8))
def _wita_day(u):
    try: return dt.datetime.fromisoformat(str(u).replace("Z", "+00:00")).astimezone(WITA).date()
    except Exception: return None
def desk_status_lines(B=None, S=None, today=None):
    """TFO HT 03:20: one line per seat from the FULL paper ledger (open, armed, today's fills and closes), labelled 'Person (seat)'. Code, not LLM."""
    B = B if B is not None else (_rj((os.path.join(BASE, "state", "thf", "thf_book.json") if os.environ.get("THF_DESK") == "1" else os.path.join(BASE, "reports", "veteran_desk_book.json")), {}) or {})
    S = S if S is not None else seats(); today = today or dt.datetime.now(WITA).date(); out = []
    def r(x): return f"{float(x):.6g}" if isinstance(x, (int, float)) else str(x)
    for seat, sb in (B.get("seats") or {}).items():
        who = f"{name(S, seat)} ({seat.replace('_', ' ')})"; bits = []
        for k, p in (sb.get("positions") or {}).items():
            side = "short" if p.get("side") == "SHORT" else "long"
            if p.get("status") == "open":
                fu = p.get("filled_utc") or p.get("fill_utc"); bits.append(f"OPEN {side} {p.get('ticker')} filled {r(p.get('fill'))}" + (" today" if _wita_day(fu) == today else "") + f", stop {r(p.get('stop'))}, target {r(p.get('target'))}, risk A${(p.get('risk_aud') or 0):,.0f}")
            elif p.get("status") == "armed":
                bits.append(f"ARMED {side} {p.get('ticker')} at {r(p.get('entry'))} (not filled), stop {r(p.get('stop'))}")
        for p in sb.get("closed") or []:
            if _wita_day(p.get("closed_utc")) == today:
                bits.append(f"CLOSED today {p.get('ticker')} ({p.get('exit_reason')}) net A${(p.get('net_aud') or 0):,.2f}")
        out.append(f"- {who}: " + ("; ".join(bits) if bits else "flat - nothing open, armed or filled today"))
    return out
def compose_note(summary, body=None):
    d = dt.datetime.now(WITA); hdr = f"**Desk note - {d.strftime('%A')} {d.day} {d.strftime('%B %Y')} ({d.strftime('%H:%M')} WITA)**"
    P = summary.get("picks") or []   # latest run's picks only; book state below comes from the full ledger (TFO HT 03:20)
    body = ("Latest run: " + (f"{len(P)} pick(s) passed every check: " + "; ".join(f"{a} {str(b).lower()} {c} (grade {g})" for a, b, c, g in P) + "." if P else "no new picks passed the checks."))
    if summary.get("blocked"): body += f" {len(summary['blocked'])} idea(s) blocked by the checks."
    live = desk_status_lines()
    tail = ("The desk auto-arms on PAPER: picks that pass the gate arm automatically, no approval from you needed (Brett 28 Oct 19:07 WITA). Stops are always on and you can still veto with funder_approve.py --reject ID.\n\nBook by seat (full ledger):\n" + "\n".join(live))
    return (hdr + "\n\nBrett,\n\n" + body.strip() + "\n\n" + tail + "\n\n- Marcus Hale").replace("**", "")   # TFO HT 03:35: plain text, no markdown bold
def refresh_note():
    H = _rj(HOD, {}) or {}; L = _rj(os.path.join(SD, "latest.json"), {}) or {}
    sm = H.get("summary") or dict(picks=[(r["trader"], r["proposal"]["side"], r["proposal"]["ticker"], r["proposal"].get("grade")) for r in L.get("proposals", []) if r["gate"]["passed"]],
        blocked=[(r.get("trader"), r["proposal"].get("ticker")) for r in L.get("proposals", []) if not r["gate"]["passed"]],
        stood_aside=[v.get("trader") or k for k, v in (L.get("seats") or {}).items() if v.get("action") == "stand_aside"])
    H.update(note=compose_note(sm), summary=sm, updated_utc=now().isoformat(), can_arm=True, auto_arm=True); _wj(HOD, H)
    if L: L["head_of_desk"] = H; _wj(os.path.join(SD, "latest.json"), L)
    C = _rj(CARD, None)
    if isinstance(C, dict) and "head_of_desk" in C: C["head_of_desk"] = H; _wj(CARD, C)
    return H
def head_of_desk(S, out, use_llm):
    """Marcus Hale: ranks passed setups, kills dupes/correlated bets (code), enforces caps (code), writes a plain-English note. Cannot arm."""
    P_ = sorted([r for r in out if r["gate"]["passed"]], key=lambda r: (r["proposal"].get("grade") != "A+", -(r["gate"].get("rr") or 0)))
    keep, groups = [], {}
    for r in P_:   # correlated-bet kill: max 2 per venue+side, 1 per seat
        k = (r["features"]["venue"], r["proposal"]["side"])
        if groups.get(k, 0) >= 2: r["gate"]["passed"] = False; r["gate"]["reasons"].append("head of desk: correlated with 2 other picks same market+side"); continue
        groups[k] = groups.get(k, 0) + 1; keep.append(r)
    keep = keep[:5]
    for r in P_[5:]:
        if r not in keep and r["gate"]["passed"]: r["gate"]["passed"] = False; r["gate"]["reasons"].append("head of desk: outside today's top 5")
    sc = _rj(SCORE, {}).get("seats", {})
    cuts = [k for k, v in sc.items() if str(v.get("verdict", "")).startswith("CUT")]; promos = [k for k, v in sc.items() if str(v.get("verdict", "")).startswith("PROMOTE")]
    summary = dict(picks=[(r["trader"], r["proposal"]["side"], r["proposal"]["ticker"], r["proposal"].get("grade")) for r in keep],
                   blocked=[(r["trader"], r["proposal"].get("ticker"), r["gate"]["reasons"][:2]) for r in out if not r["gate"]["passed"]],
                   stood_aside=[name(S, k) for k in S if S[k]["role"] == "proposer" and k not in {r["seat"] for r in out}])
    note = None
    if use_llm:
        t = llm("codex", f"You are Marcus Hale, head of a PAPER trading desk. Write a desk note for the funder Brett in plain English, under 120 words, no jargon. Cover today's picks, what was blocked and why, who stood aside. No greeting, heading, date or sign-off (code adds them). Do not invent numbers or dates; grade A is the standard grade, only A+ is top grade. Picks that pass the checks arm automatically on PAPER (no tick from Brett needed); stops are always on. DATA: {json.dumps(summary, default=str)}", 180)
        note = (t or "").replace("__FALLBACK_CLAUDE__", "").strip()[:1200] or None
    if not note:
        note = (f"{len(keep)} setup(s) passed and auto-arm on paper: " + "; ".join(f"{a} {b.lower()} {c} ({d})" for a, b, c, d in summary["picks"]) + ". " if keep else "No setups good enough today - the desk is standing aside. ") + \
               (f"{len(summary['blocked'])} idea(s) blocked by the checks." if summary["blocked"] else "")
    note = compose_note(summary, note)
    H = dict(trader=name(S, "head_of_desk"), updated_utc=now().isoformat(), note=note, summary=summary, top_picks=[dict(id=r["id"], trader=r["trader"], ticker=r["proposal"]["ticker"], side=r["proposal"]["side"], why=r["proposal"].get("thesis", "")[:200]) for r in keep],
             cuts=cuts, promotions=promos, can_arm=True, auto_arm=True)
    if ENGINE_WAKE:   # vd-engine: Marcus reviews engine wakes; note goes to the engine log, batch desk note untouched
        try:
            os.makedirs(os.path.join(BASE, "state", "vd_engine"), exist_ok=True); open(os.path.join(BASE, "state", "vd_engine", "marcus_wake_notes.jsonl"), "a").write(json.dumps(dict(H, wake_id=ENGINE_WAKE), default=str) + "\n")
        except Exception: pass
        return H
    _wj(os.path.join(SD, "head_of_desk.json"), H); return H
AUTO_ARM_BY = "auto: veteran desk gate_v2 + Marcus (Brett 19:05 WITA, PAPER, no funder tick)"
LIVE_CHASE_MAX = 0.01   # 2026-10-29: packs price off the last daily close; reject on arrival if live already past stop or >1% through entry
def live_check(p, px_fn=None):
    """Return None if the setup is still valid at the live quote, else a reason. No quote -> None (book.py pre-trade still checks)."""
    try:
        if px_fn is None:
            from veteran_desk import feeds as FD; px_fn = FD.fresh_px   # 2026-10-29 exthours: US -> freshest of regular/pre/post (<=30 min); others -> book.live_px unchanged
        m = px_fn(p["ticker"]) or {}; x = m.get("last")
    except Exception: return None
    if not x: return None
    L = str(p["side"]).upper() == "LONG"; e, st = float(p["entry"]), float(p["stop"])
    if (L and x <= st) or (not L and x >= st): return "live %g already through stop %g (stale bar) - rejected on arrival" % (x, st)
    # 2026-10-29 Marcus: pullback/rally LIMIT (LONG entry < live, SHORT entry > live) is waiting, not chasing.
    # Chase only when a stop-entry is already >1% through the trigger (live past entry in trade direction).
    stop_entry_through = (L and x > e) or (not L and x < e)
    # Pullback/rally limits also have live past entry — allow those. Only block when proposal typed stop_limit, or when entry was on the breakout side of stop geometry with >1% runaway marketable fill.
    # Without order_type on proposals: allow all non-through-stop; book fill enforces stop_limit no-chase.
    ot = str(p.get("order_type") or "").lower()
    ch = (x / e - 1) if L else (1 - x / e)
    if ot == "stop_limit" and stop_entry_through and ch > LIVE_CHASE_MAX:
        return "live %g is %.1f%% through stop-entry %g - rejected, re-propose from the live price" % (x, ch * 100, e)
    return None
def enqueue(recs, au, path=None, px_fn=None, ack_required=False):
    import funder_gate as FG
    ids = []; ok = []
    for r in recs:
        why = live_check(r["proposal"], px_fn) if (px_fn or (path is None and not os.environ.get("PYTEST_CURRENT_TEST"))) else None   # test queues (explicit path) skip the live quote unless a px_fn is given
        if why: r["live_reject"] = why; log("live check %s %s: %s" % (r["id"], r["proposal"].get("ticker"), why)) if "log" in globals() else print(why)
        else: ok.append(r)
    recs = ok
    def f(Q):
        for k, e in Q["entries"].items():   # expire OUR stale pending entries only (book 'veteran'); never touch other books
            if e.get("book") == "veteran" and e.get("state") == FG.WAIT and e.get("expires_utc", "") < now().isoformat(): e.update(state="expired", why="veteran proposal window ended")
        for r in recs:
            p, z = r["proposal"], r["sizing"]
            if r["id"] in Q["entries"]: continue
            Q["entries"][r["id"]] = dict(id=r["id"], book="veteran", ledger=None, market=r["features"]["venue"], ticker=p["ticker"], name=p["ticker"], side=p["side"],
                entry=float(p["entry"]), trigger=float(p["entry"]), max_fill=float(p["entry"]), stop=float(p["stop"]), target=float(p["target"]), t1=float(p["target"]),
                shares=z["shares"], risk_aud=z["risk_aud"], risk_pct=r["gate"].get("risk_pct"), chal_agree=r["gate"].get("chal_agree"), win_aud=z["win_aud"], score=r["gate"]["rr"],
                tags=dict(news_risk=[], quality="VETERAN_GATE_PASS", catalyst=None, strategy=f"veteran:{r['seat']} grade {p.get('grade')} sleeve {p.get('sleeve') or 'core'}"), sleeve=p.get("sleeve") or "core", grade=p.get("grade"),
                eli10=f"{r['trader']} (veteran desk, {r['seat']}) likes {p['side'].lower()} {p['ticker']}: {p.get('thesis','')[:220]} Blind challenger said {((r['challenger']['view'] or {}).get('call'))}; devil's advocate: {((r['devils_advocate'] or {}).get('kill_case',''))[:160]} If target hit ~A${z['win_aud']:,}; if stop hit ~A${z['risk_aud']:,}.",
                state=FG.WAIT, decision="approve", decided_by=AUTO_ARM_BY, decided_utc=now().isoformat(), queued_utc=now().isoformat(), expires_utc=(now() + dt.timedelta(hours=20)).isoformat(), paper_only=True, info_only=True,
                ack_required=bool(ack_required), acked=(not ack_required) or (p.get("confirm") is True and r.get("seat_reply_ok") is True),   # tfo_propose_ack_20261031 + tfo_review_fix: desk-run auto-confirm only on the seat's own explicit "confirm":true in a reply the desk received
                note="VETERAN DESK PAPER auto-arm (Brett 2026-10-28 19:05 WITA): gate_v2 + Marcus passed, book.py arms on its next tick without a funder tick. Brett can still veto before then: python3 funder_approve.py --reject ID", hash=r["hash"], gate_version=r["gate"].get("version", "v1"), gate_warnings=r["gate"].get("warnings", [])[:8], gate_size_mult=r["gate"].get("size_mult", 1.0), seat_risk_pct=r["gate"].get("seat_risk_pct"), override=bool(r["gate"].get("override")), lessons_respected=(p.get("lessons_respected") or "")[:400])
            ids.append(r["id"])
    FG.update(f, path); return ids
# ---------------- forward shadow scoring (code, PIT) ----------------
def track(recs):
    T = _rj(os.path.join(_fwd_dir(), "forward_trades.json"), {"trades": {}})
    for r in recs:  # registered at proposal time = pre-registration stamp
        if r["gate"]["passed"] and r["id"] not in T["trades"]:
            p = r["proposal"]; T["trades"][r["id"]] = dict(seat=r["seat"], ticker=p["ticker"], side=p["side"], entry=float(p["entry"]), stop=float(p["stop"]),
                target=float(p["target"]), shares=r["sizing"]["shares"], cost_aud=r["sizing"]["cost_aud"], registered_utc=r["created_utc"], horizon_days=int(p.get("horizon_days") or 10),
                status="pending_trigger", cluster=f"{r['seat']}:{r['features']['venue']}")
        elif not r["gate"]["passed"] and r["features"] and r["id"] not in T["trades"]:   # shadow-track vetoed setups so vetoes can be judged
            p = r["proposal"]; R = r["gate"]["reasons"]; by = [x for x, k in (("blind_challenger", "challenger called"), ("devils_advocate", "advocate factual kill")) if any(k in y for y in R)]
            try: e, st = float(p["entry"]), float(p["stop"]); q = int(500 / max(abs(e - st), 1e-9))
            except Exception: continue
            T["trades"][r["id"]] = dict(seat=r["seat"], ticker=p["ticker"], side=p.get("side"), entry=e, stop=st, target=float(p.get("target") or e), shares=q,
                cost_aud=round(e * q * CT.est_rt_rate(p["ticker"], r["features"]["venue"], e), 2), registered_utc=r["created_utc"], horizon_days=int(p.get("horizon_days") or 10),
                status="vetoed", shadow_status="pending_trigger", cluster=f"{r['seat']}:{r['features']['venue']}", vetoed=True, vetoed_by=by, veto_reasons=R)   # vetofix 2026-10-29: label vetoed; shadow lifecycle in shadow_status
    _wj(os.path.join(_fwd_dir(), "forward_trades.json"), T)
def mark(asof=None):
    """Code-only stop/target marking on PIT daily bars; stop checked first on any bar touching both (conservative)."""
    asof = asof or now(); c = P.con(); au = audusd(c, asof); T = _rj(os.path.join(_fwd_dir(), "forward_trades.json"), {"trades": {}})
    for k, t in T["trades"].items():
        vet = t["status"] == "vetoed"   # vetofix 2026-10-29: vetoed shadow rows keep status=vetoed; mark their shadow_status instead
        if vet: t["status"] = t.get("shadow_status", "pending_trigger")
        try: _mark_one(t, c, asof, au)
        finally:
            if vet: t["shadow_status"] = t["status"]; t["status"] = "vetoed"
    _wj(os.path.join(_fwd_dir(), "forward_trades.json"), T); return T
def _mark_one(t, c, asof, au):
    if True:
        if t["status"] in ("closed", "cancelled"): return   # cancelled = book/queue cancelled unfilled: never marked or scored (TFO HT 2026-10-28)
        bars = [b for b in P.bars_asof(c, t["ticker"], asof, 400) if b[6] > t["registered_utc"]]; L = t["side"] == "LONG"
        for d, o, h, l, cl, v, kn in bars:
            if t["status"] == "pending_trigger":
                if (L and h >= t["entry"]) or (not L and l <= t["entry"]): t.update(status="open", opened=d)
                elif len([b for b in bars if b[0] <= d]) >= 3: t.update(status="closed", outcome="never_triggered", pnl_aud=0, closed=d); break
                else: continue
            hit_s = (L and l <= t["stop"]) or (not L and h >= t["stop"]); hit_t = (L and h >= t["target"]) or (not L and l <= t["target"])
            age = len([b for b in bars if b[0] >= t.get("opened", d) and b[0] <= d])
            px = t["stop"] if hit_s else t["target"] if hit_t else (cl if age >= t["horizon_days"] else None)
            if px is not None:
                g = (px - t["entry"]) * t["shares"] * (1 if L else -1); g = g if P.venue_of(t["ticker"]) == "ASX" else g / au
                t.update(status="closed", exit=px, closed=d, outcome="stop" if hit_s else "target" if hit_t else "time", gross_aud=round(g, 2), pnl_aud=round(g - t["cost_aud"], 2)); break
PROMOTION = dict(rule="GPT strict (merged plan item 6)", min_forward_trades=100, min_months=6, min_independent_clusters=50, must_survive_doubled_costs=True,
                 max_risk_raise_per_review=0.25, below_30_trades="NO VERDICT (never ranked)", replay_counts=False)
def score():
    T = mark()["trades"]; S = seats(); out = dict(updated_utc=now().isoformat(), currency="AUD", basis="net of modelled costs; forward PAPER shadow only", promotion_rule=PROMOTION, seats={})
    for name, s in S.items():
        if s["role"] != "proposer": out["seats"][name] = dict(role=s["role"], engine=s["engine"], scored="n/a (control seat)"); continue
        C = [t for t in T.values() if t["seat"] == name and not t.get("vetoed") and t["status"] == "closed" and t.get("outcome") != "never_triggered"]
        n = len(C); net = sum(t["pnl_aud"] for t in C); net2 = sum(t["pnl_aud"] - t["cost_aud"] for t in C); wins = sum(t["pnl_aud"] > 0 for t in C)
        first = min((t["registered_utc"] for t in T.values() if t["seat"] == name), default=None)
        months = ((now() - dt.datetime.fromisoformat(first)).days / 30.4) if first else 0
        clusters = len({t["cluster"] + t["closed"][:7] for t in C})
        ok = n >= 100 and months >= 6 and clusters >= 50 and net2 > 0
        out["seats"][name] = dict(role="proposer", engine=s["engine"], forward_trades=n, open=sum(t["status"] == "open" for t in T.values() if t["seat"] == name),
            pending=sum(t["status"] == "pending_trigger" for t in T.values() if t["seat"] == name), net_aud=round(net, 2), net_aud_doubled_costs=round(net2, 2),
            win_rate=round(wins / n, 3) if n else None, months_live=round(months, 2), independent_clusters=clusters, risk_multiplier=1.0,
            verdict="PROMOTE_ELIGIBLE (raise risk max +25%)" if ok else ("NO VERDICT" if n < 30 else "WATCH"))
    _wj(SCORE, out); return out
# ---------------- historical replay: TRAINING ONLY ----------------
def replay(years=5):
    """Deterministic playbook replay (trend + not choppy + no chase; 2 ATR stop, 3 ATR target, 10-day time stop). Builds checklists only - NOT evidence."""
    c = P.con(); res = dict(label="TRAINING_ONLY - historical replay is contaminated by model knowledge and NEVER counts toward promotion", generated_utc=now().isoformat(), seats={})
    for name, s in seats().items():
        if s["role"] != "proposer": continue
        n = net = net2 = w = 0
        for sym in sorted({x for g in s["universe_groups"] for x in P.UNIVERSE[g]}):
            b = c.execute("SELECT date,o,h,l,c FROM bars WHERE sym=? AND knowable_utc<=? ORDER BY date", (sym, now().isoformat())).fetchall()[-252 * years:]   # 2026-10-29: knowable bars only
            i = 60; cost = CT.est_rt_rate(sym, P.venue_of(sym))
            while i < len(b) - 11:
                C = [r[4] for r in b[i - 60:i + 1]]; tr = [b[j][2] - b[j][3] for j in range(i - 13, i + 1)]; a = sum(tr) / 14
                er = abs(C[-1] - C[-21]) / (sum(abs(C[k] - C[k - 1]) for k in range(-20, 0)) or 1); s20 = sum(C[-20:]) / 20; s50 = sum(C[-50:]) / 50
                if er > .35 and C[-1] > s20 > s50 and C[-1] < s20 + 3 * a and C[-1] / C[-6] - 1 < .12:
                    e = C[-1]; stp, tgt = e - 2 * a, e + 3 * a; px = None
                    for j in range(i + 1, i + 11):
                        if b[j][3] <= stp: px = stp; break
                        if b[j][2] >= tgt: px = tgt; break
                    px = px or b[i + 10][4]; r = (px - e) / (2 * a); cr = 2 * cost * e / (2 * a)
                    n += 1; net += (r - cr / 2) * 500; net2 += (r - cr) * 500; w += r > 0; i += 11
                else: i += 1
        res["seats"][name] = dict(trades=n, net_aud_at_A500_risk=round(net), net_aud_doubled_costs=round(net2), win_rate=round(w / n, 3) if n else None, years=years)
    _wj(REPLAY, res); return res
# ---------------- outputs ----------------
def health(h): _wj(HEALTH, dict(h, paper_only=True, can_arm=False, stops_need_ai=False))
def sheet(log):
    d = REPORTS; os.makedirs(d, exist_ok=True)
    L = [f"# Veteran desk pre-open sheet {log['run_id']} (PAPER, info only - nothing arms)", ""]
    for r in log["proposals"]:
        p = r["proposal"]; L.append(f"- **{'PASS' if r['gate']['passed'] else 'BLOCKED'}** {r.get('trader')} ({r['seat']}) {p.get('side')} {p.get('ticker')} grade {p.get('grade')} entry {p.get('entry')} stop {p.get('stop')} target {p.get('target')} | risk A${(r.get('sizing') or {}).get('risk_aud')} ({round((r['gate'].get('risk_pct') or 0) * 100, 2)}% of seat, x{r['gate'].get('size_mult')}) | challenger {(r['challenger']['view'] or {}).get('call')} | gate: {'; '.join(r['gate']['reasons']) or 'ok'}")
    L += ["", "Stand-asides: " + ", ".join(k for k, v in log["seats"].items() if v["action"] == "stand_aside")]
    open(os.path.join(d, f"preopen_{log['run_id']}.md"), "w").write("\n".join(L)); open(os.path.join(d, "latest.md"), "w").write("\n".join(L))
def card(log, sc):
    _wj(CARD, dict(updated_utc=now().isoformat(), run_id=log["run_id"], paper_only=True, info_only=True,
        head_of_desk=log.get("head_of_desk"), seats={k: dict(trader=v.get("trader"), reason=v.get("reason"), engine=v["engine"], action=v["action"] or ("no reply" if not v["raw_ok"] else "?")) for k, v in log["seats"].items()},
        proposals=[dict(id=r["id"], seat=r["seat"], trader=r.get("trader"), da=(r["devils_advocate"] or {}).get("kill_case", "")[:300], side=r["proposal"].get("side"), ticker=r["proposal"].get("ticker"), grade=r["proposal"].get("grade"),
                        passed=r["gate"]["passed"], reasons=r["gate"]["reasons"][:3], challenger=(r["challenger"]["view"] or {}).get("call"),
                        risk_aud=(r["sizing"] or {}).get("risk_aud")) for r in log["proposals"]],
        scorecard={k: dict(trades=v.get("forward_trades"), net_aud=v.get("net_aud"), verdict=v.get("verdict", v.get("scored"))) for k, v in sc["seats"].items()}))
if __name__ == "__main__":
    a = sys.argv[1:]; cmd = a[0] if a else "run"
    try:
        if cmd == "run": l = run(use_llm="--no-llm" not in a, queue="--no-queue" not in a, only=[x.split("=", 1)[1] for x in a if x.startswith("--seats=")][0].split(",") if any(x.startswith("--seats=") for x in a) else None); print(json.dumps(dict(run=l["run_id"], seats={k: v["action"] for k, v in l["seats"].items()}, props=[(r["seat"], r["proposal"].get("ticker"), r["gate"]["passed"], r["gate"]["reasons"]) for r in l["proposals"]], queued=l.get("queued")), default=str))
        elif cmd == "note": print(refresh_note()["note"])
        elif cmd == "score": print(json.dumps(score(), indent=1))
        elif cmd == "replay": print(json.dumps(replay(), indent=1))
    except Exception as e:
        health(dict(ok=False, last_error=f"{type(e).__name__}: {e}"[:300], last_run_utc=now().isoformat())); raise
