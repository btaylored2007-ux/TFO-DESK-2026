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
def _pins(seat, EW=None, book=None):   # pinlive_20261103 (crypto 19:53 WITA: XMRUSD, engine #1 B, lost its crypto_live row to the trimmer): names whose LIVE rows a prompt build never trims
    """{'pinned': engine wake list + engine top10 + the seat's own open/armed names, 'rank': {sym: engine rank (top10 order, then the grades file order)}}.
    top10 = this wake's snapshot, else state/vd_engine/top10/<seat>.json. Own names from the desk's own book file (read-only; skipped under pytest unless book= is given). Never raises."""
    out = dict(seat=seat, wake=ENGINE_WAKE, pinned=[], rank={}, wake_syms=[], top10=[], own=[])
    try:
        wk = [str(x.get("sym")) for x in ((EW or {}).get("candidates") or []) if isinstance(x, dict) and x.get("sym")]
        T = (EW or {}).get("top10")
        if not isinstance(T, list): T = (_rj(os.path.join(BASE, "state", "vd_engine", "top10", str(seat) + ".json"), {}) or {}).get("top10") or []
        top = [str(x.get("sym")) for x in T if isinstance(x, dict) and x.get("sym")]
        own = []
        if book is None and not os.environ.get("PYTEST_CURRENT_TEST"):
            from veteran_desk import book as _BKP; book = _rj(_BKP.BOOK, {})
        for p in ((((book or {}).get("seats") or {}).get(seat) or {}).get("positions") or {}).values():
            if isinstance(p, dict) and p.get("ticker") and str(p.get("status") or "").startswith(("open", "armed")): own.append(str(p["ticker"]))
        G = (_rj(os.path.join(BASE, "state", "vd_engine", "grades", str(seat) + ".json"), {}) or {}).get("grades") or {}
        rank = {}
        for s_ in top + [str(k) for k in G]:
            if len(rank) >= 60: break   # live blocks hold <= ~25 rows; names past 60 trim like unranked ones
            rank.setdefault(s_, len(rank) + 1)
        majors=["KRAKEN:XBTUSD","KRAKEN:ETHUSD","KRAKEN:SOLUSD","KRAKEN:XRPUSD","KRAKEN:NEARUSD","KRAKEN:SUIUSD","KRAKEN:XMRUSD","KRAKEN:MNTUSD"] if seat=="crypto" else []
        out.update(pinned=list(dict.fromkeys(wk+top+own+majors)),rank=rank,wake_syms=wk,top10=top,own=list(dict.fromkeys(own)),majors=majors)
    except Exception as x: out["error"] = type(x).__name__
    return out
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
AU_SRC = {}   # fxmark_20261103 (F19): v / src / age_min of the AUDUSD this process sizes with (desk run, seat_cli propose, forward marks)
def audusd(c, asof):
    """AUDUSD for seat sizing. fxmark_20261103 (F19, was desk.py:88: the pit daily close, else a SILENT 0.65). Now: 1 the live rate when
    asof is now (+-15 min; book.audusd = OANDA practice mid first, Yahoo/FRED fallback, weekend freeze; skipped under tests), 2 the pit
    daily close knowable at asof (<= 96h old), 3 the book's last good live rate (<= 24h old, asof now only). Source + age kept in AU_SRC.
    Nothing usable -> book.FxUnavailable with a plain reason: sizing is refused (seat_cli propose returns the reason), never 0.65."""
    from veteran_desk import book as BK
    AU_SRC.clear(); tries = []; t = now()
    try: a_ = asof if isinstance(asof, dt.datetime) else dt.datetime.fromisoformat(str(asof).replace("Z", "+00:00"))
    except Exception: a_ = t
    a_ = a_ if a_.tzinfo else a_.replace(tzinfo=UTC); is_now = abs((t - a_).total_seconds()) <= 900
    if is_now and not os.environ.get("PYTEST_CURRENT_TEST"):
        try:
            v = float(BK.audusd()); a = BK.AU_META.get("asof")
            AU_SRC.update(v=v, src="live " + str(BK.AU_META.get("src") or "feed"), age_min=round((t - dt.datetime.fromisoformat(str(a))).total_seconds() / 60, 1) if a else None)
            return v
        except Exception as x: tries.append("live: %s" % (str(x) or type(x).__name__)[:100])
    b = P.bars_asof(c, "AUDUSD=X", asof, 1)
    if b:
        try: ag = (a_ - dt.datetime.fromisoformat(str(b[-1][6]).replace("Z", "+00:00"))).total_seconds() / 3600
        except Exception: ag = None
        if ag is not None and ag <= 96 and b[-1][4] and 0.4 < float(b[-1][4]) < 1.2:
            AU_SRC.update(v=float(b[-1][4]), src="pit daily close %s" % b[-1][0], age_min=round(ag * 60, 1)); return float(b[-1][4])
        tries.append("pit: newest AUDUSD=X close %s is %s old" % (b[-1][0], "?" if ag is None else "%.0fh" % ag))
    else: tries.append("pit: no AUDUSD=X bar knowable at %s" % a_.isoformat()[:16])
    try:
        cp = BK._au_cache() if is_now else None; L = json.load(open(cp)) if cp else {}
        la = (t - dt.datetime.fromisoformat(str(L["utc"]))).total_seconds() / 3600
        if la <= 24 and 0.4 < float(L["v"]) < 1.2:
            AU_SRC.update(v=float(L["v"]), src="last good %s" % (L.get("src") or "live rate"), age_min=round(la * 60, 1)); return float(L["v"])
        tries.append("last good rate %.1fh old" % la)
    except Exception: tries.append("last good rate: none")
    raise BK.FxUnavailable("AUDUSD for sizing unavailable (%s) - sizing refused, no 0.65 fallback" % "; ".join(tries))
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
def _small_coin_top():
    """tfo_smallcoin_20261101: small-coin tier names the engine graded A+/A/B in Kai's top 10 (not the whole tier - keeps the prompt small)."""
    try:
        from veteran_desk import small_coin as SC
        T = json.load(open(os.path.join(P.BASE, "state", "vd_engine", "top10", "crypto.json"))).get("top10") or []
        return [x["sym"] for x in T if x.get("grade") in ("A+", "A", "B") and SC.is_small(x["sym"])]
    except Exception: return []
def fit_instrument_rows(rows,pinned=(),ranked=(),limit=199):
    """Bound feature records, preserving every owned/woken/top10 name and highest ranked scan rows."""
    if len(rows)<=limit:return rows
    priority=list(dict.fromkeys(list(pinned)+[r.get("sym") or r.get("ticker") for r in ranked if isinstance(r,dict)]))
    order={sym:i for i,sym in enumerate(priority) if sym}
    pins=set(pinned);required=sum(r.get("sym") in pins for r in rows)
    return sorted(rows,key=lambda r:order.get(r.get("sym"),len(order)))[:max(limit,required)]

def annotate_grades(out,snapshot,universe=()):
    """Prompt facts only: distinguish an engine grade from a name with no saved evaluation."""
    grades=dict(snapshot.get("grades") or {})
    ew=out.get("engine_wake") or {}
    for row in list(ew.get("top10") or [])+list(ew.get("candidates") or []):
        if isinstance(row,dict) and row.get("sym") and row.get("grade"):grades.setdefault(row["sym"],row["grade"])
    def grade(sym):
        value=grades.get(sym)
        if isinstance(value,dict):value=value.get("grade") or ("reject:"+str(value["reason"]) if value.get("reason") else None)
        return str(value) if value else "ungraded:no_saved_engine_evaluation"
    rows=list(out.get("instruments") or [])+list(out.get("candidates_ranked") or [])+list((out.get("seat_scan") or {}).get("top") or [])
    rows+=list(ew.get("top10") or [])+list(ew.get("candidates") or [])
    for key in ("crypto_live","live_quotes"):
        block=out.get(key) or {}
        mapping=block.get("rows",{}) if key=="crypto_live" else block
        if isinstance(mapping,dict):
            for sym,row in mapping.items():
                if isinstance(row,dict) and not row.get("grade"):row["grade"]=grade(sym)
    for row in rows:
        if isinstance(row,dict):
            sym=row.get("sym") or row.get("ticker")
            if sym and not row.get("grade"):row["grade"]=grade(sym)
    symbols=list(dict.fromkeys(universe or [r.get("sym") for r in rows if isinstance(r,dict) and r.get("sym")]))
    counts={"graded":0,"rejected":0,"ungraded":0};missing=[]
    for sym in symbols:
        value=grade(sym)
        status="ungraded" if value.startswith("ungraded:") else "rejected" if value.lower().startswith("reject") else "graded"
        counts[status]+=1
        if status=="ungraded":missing.append(sym)
    out["grade_coverage"]=dict(universe=len(symbols),**counts,asof_utc=snapshot.get("utc"),ungraded_symbols=missing[:30],note="Saved engine evaluations only; an ungraded name is never promoted to a trading grade.")
    return out

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
            U = sorted(set(P.CRYPTO_CORE) | set(L) | set(_small_coin_top())); scan = "scanned %d of %d (%d cleared A$500k)" % (len(U), Y, Z)   # tfo_seatpipe_2_20261030 unified scan line
        elif L:
            try:   # tfo_seatpipe_6d_20261030: names with an 8-K in the last 2 days count as a news hit in the coded score (live runs only)
                from veteran_desk import feeds as _FD
                X8 = {r["sym"] for r in _FD.edgar_8k_recent(refresh=False) if r.get("fresh_filing")} if abs((now() - asof).total_seconds()) <= 900 else set()
            except Exception: X8 = set()
            top, rk, X = SU.rank_candidates(c, L, asof, int(os.environ.get("VET_SCAN_TOP", "120")), extra_news=X8, mode=seat.get("seat") if seat.get("seat") in ("trend", "price_action") else "default")   # tfo_seatpipe_dexsplit_20261030   # tfo_seatpipe_3_20261030: 40 -> ~120, coded score (was Dex trend-stack prescreen top 40)
            ranked = [dict(r, rank=i + 1) for i, r in enumerate(rk)]
            U = sorted(set(U) | set(top)); scan = "scanned %d of %d (%d cleared A$500k)" % (len(L), Y, Z)   # tfo_seatpipe_2_20261030: seat universe of seat total (cleared); prescreen hits=%d kept below
            scan_hits = X
        elif key not in ("crypto", "fx_commodities", "macro"):
            scan = "scanned 0 of %d (%d cleared A$500k) - seat sector map empty" % (Y, Z)
    except Exception as x: scan = "scan_universe unavailable (%s) - hand list only" % type(x).__name__
    EW = _engine_wake(seat.get("seat"))   # vd-engine (Taylor Hedge Fund) 2026-10-30: engine-driven wake -> its candidates join the pack (additive)
    if EW: U = sorted(set(U) | {x["sym"] for x in list(EW.get("candidates") or [])+list(EW.get("top10") or []) if isinstance(x,dict) and x.get("sym")})
    F = [f for f in (feats(c, s, asof) for s in U) if f]
    out = dict(asof_utc=asof.isoformat(), instruments=F)
    PIN = _pins(seat.get("seat"), EW)
    if abs((now()-asof).total_seconds())<=900:
        present={f["sym"] for f in F}
        F.extend(f for sym in PIN.get("pinned",[]) if sym not in present for f in [feats(c,sym,asof)] if f)
    before_features=len(F)
    F[:]=fit_instrument_rows(F,PIN.get("pinned",[]),ranked or [],160 if seat.get("seat")=="banks" else 199)
    if len(F)<before_features:out["feature_coverage"]=dict(available=before_features,retained=len(F),note="Pinned names and ranked scan features first; full scan/rank coverage remains in its own sections.")
    try:
        from veteran_desk import thf_exdiv as EX
        out["cash_distributions"]=EX.context([f["sym"] for f in F],asof,c=c)
        for feature in F:feature.update(out["cash_distributions"]["events"].get(feature["sym"]) or {})
    except Exception:out["cash_distributions"]=dict(status="ex-div data not available")
    out["_fit"] = PIN   # pinlive_20261103: compact_pk pops _fit (never rendered) and never trims these names' live rows
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
    try:   # thf_scan_20261102 + seatmail_20261102: the seat's own deterministic full-slice scan (coverage + top 25) and colleague inbox
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            from veteran_desk import thf_scan as _TS, seat_mail as _SM
            _b = _TS.seat_brief(seat.get("seat"), 50 if seat.get("seat") in ("fx_commodities", "macro") else 25)   # seatagents_20261103 17:17 (FX helper handoff): FX/macro see their top 50
            if _b: out["seat_scan"] = _b
            _ib = _SM.inbox(seat.get("seat"), "desk", 2, mark=False)
            if _ib: out["seat_inbox"] = _ib; out["seat_inbox_note"] = "messages from desk colleagues (answer them in the msgdesk thread with to_seats / @seat)"
    except Exception: pass
    if seat.get("seat") == "crypto" and not os.environ.get("PYTEST_CURRENT_TEST") and abs((now() - asof).total_seconds()) <= 900:   # seatdata_20261103 (D24 Kai): live bid/ask/spread/intraday volume + PF_ perp/funding/OI/basis per kept name (one public Kraken call + engine caches)
        try:
            from veteran_desk import crypto_live as _CL
            _ck = list(PIN.get("pinned") or []) + [x.get("sym") for x in (EW or {}).get("candidates", []) if isinstance(x, dict)] + [f["sym"] for f in sorted(F, key=lambda x: -(x.get("adv20_value") or x.get("vol24h_usd") or 0))[:COMPACT_N]] + [r.get("sym") for r in ((out.get("seat_scan") or {}).get("top") or [])[:6] if isinstance(r, dict)]   # pinlive_20261103: wake + engine top10 + own open/armed first, then engine-woken coins, liquidity, own scan
            _adv = {f["sym"]: f.get("adv20") for f in F}
            for _s in (PIN.get("pinned") or []):   # pinlive_20261103: a pinned coin outside the pack universe still gets its 20d ADV (pit read, no network)
                if _s not in _adv and str(_s).upper().startswith("KRAKEN:"):
                    try: _adv[_s] = (feats(c, _s, asof) or {}).get("adv20")
                    except Exception: pass
            out["crypto_live"] = _CL.snapshot(_ck, adv20=_adv, pinned=PIN.get("pinned") or ())
        except Exception as _x: out["crypto_live_err"] = type(_x).__name__
    if scan_hits is not None: out["scan_prescreen_hits"] = scan_hits
    if ranked: out["candidates_ranked"] = ranked; out["candidates_note"] = "coded score = trend + 0.5*(relvol-1) + news hit + 0.5*factor rank; grade the top 10 by rank, log a reject reason for each one you pass on"
    try:   # tfo_livequote_20261030 (Brett 23:16 WITA): seats priced off prior close -> inject paid live quotes (Massive US / EODHD-FMP intl via paid_feeds.fetch_quote)
        if not os.environ.get("PYTEST_CURRENT_TEST") and abs((now() - asof).total_seconds()) <= 900:
            import sys as _sys; _sys.path.insert(0, BASE) if BASE not in _sys.path else None
            import paid_feeds as _PFQ
            from concurrent.futures import ThreadPoolExecutor as _TPE
            LQ = list(PIN.get("pinned") or []) + [x["sym"] for x in (EW or {}).get("candidates", [])] + [r.get("sym") or r.get("ticker") for r in (ranked or [])[:12]] + [f["sym"] for f in F]   # pinlive_20261103: pinned names fetched first
            _npin = len([x for x in (PIN.get("pinned") or []) if not str(x).upper().startswith("KRAKEN:")])
            LQ = [x for x in dict.fromkeys(LQ) if x and not str(x).upper().startswith("KRAKEN:")][:max(25, _npin)]
            def _q(x):
                try: q = _PFQ.fetch_quote(x); return x, (dict(px=q.get("px"), ccy=q.get("ccy"), quote_utc=q.get("quote_utc"), status=q.get("status"), src=q.get("source") or q.get("provider")) if q and q.get("px") else None)
                except Exception: return x, None
            with _TPE(8) as _ex: LQd = dict(_ex.map(_q, LQ))
            out["live_quotes"] = {k: v for k, v in LQd.items() if v}
            out["live_quotes_note"] = "PAID live quotes fetched this wake (Massive real-time US; EODHD Cboe Europe websocket for LSE/EU when healthy, else EODHD/FMP). Yahoo is fallback only. Set entry vs live px, NOT the prior daily close in instruments[]. Missing symbol = no paid quote; say so. OANDA practice real-time mid for OANDA-book FX (bid/ask included); futures/indices Yahoo ~10 min delayed."   # seatagents_20261103 17:17 (FX helper handoff)
    except Exception as _x: out["live_quotes_err"] = type(_x).__name__
    try:   # tfo_paidrt_20261031: 1m/5m/15m from data/intraday/bars.sqlite (not pit). Past asof drops bars that open later.
        from veteran_desk import intraday_store as _IB
        _asof_ms = None if abs((now() - asof).total_seconds()) <= 900 else int(asof.timestamp() * 1000)
        _syms = []
        for _r in (ranked or [])[:25]:
            _s = _r.get("sym") or _r.get("ticker")
            if _s and _s not in _syms: _syms.append(_s)
        for _f in F:
            if _f["sym"] not in _syms: _syms.append(_f["sym"])
            if len(_syms) >= 40: break
        _iv = _IB.for_symbols(_syms, asof_ms=_asof_ms)
        if _iv:
            out["intraday"] = _iv
            out["intraday_note"] = "1m/5m/15m from data/intraday/bars.sqlite (Massive US, EODHD where pulled, Cboe Europe candles). Pit stays daily. Missing symbol = no intraday row yet."
    except Exception: pass
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
    if seat.get("seat") == "crypto" and isinstance(out.get("live_feeds"), dict):   # seatdata_20261103 (D24 Kai): BTC dominance + Coinalyze majors as their own key (compact_pk used to drop/trim them)
        try:
            from veteran_desk import crypto_live as _CL; _cm = _CL.market(out["live_feeds"])
            if _cm: out["crypto_market"] = _cm
        except Exception: pass
    if any(i.startswith("edgar") for i in seat["data_inputs"]):
        out["filings"] = {f["sym"]: P.filings_asof(c, f["sym"], asof, 3) for f in F}
        from veteran_desk.filing_freshness import annotate
        out["filings_freshness"]={sym:[annotate(sym,row,asof) for row in rows] for sym,rows in out["filings"].items()}
        out["filings_note"]="Historical filings stay visible. Freshness uses public acceptance time, never fetch/ingestion time. Amendments are not fresh standalone events; missing event dates are unverified."
    if "edgar:8k_fulltext" in seat["data_inputs"] and abs((now() - asof).total_seconds()) <= 900:   # tfo_seatpipe_6c_20261030: Quinn gets 8-K text (live runs only - lookahead guard)
        try:
            from veteran_desk import feeds as FD
            R = FD.edgar_8k_recent(refresh=False); mine = {f["sym"] for f in F} | set((ranked and [r["sym"] for r in ranked]) or [])
            out["filings_8k"] = [FD.edgar_8k_text(dict(r)) for r in R if r["sym"] in mine][:10] + [dict(r, text=r.get("text")) for r in R if r["sym"] not in mine and r.get("text")][:6]
            out["filings_8k_note"] = "Dated SEC filing cache; amendments and old events are explicitly labelled and are not fresh standalone catalysts. Missing text is not fetched during a trader decision. items: 2.02 results, 1.01 material agreement, 5.02 officer change, 8.01 other, 7.01 Reg FD. text = first 1,500 chars of the filing. Only names in your universe/ranked list are tradable here."
        except Exception as x: out["filings_8k_note"] = "edgar filing cache unavailable: %s" % type(x).__name__
    grade_snapshot=_rj(os.path.join(BASE,"state","vd_engine","grades",seat.get("seat","")+".json"),{}) if not os.environ.get("PYTEST_CURRENT_TEST") and abs((now()-asof).total_seconds())<=900 else {}
    annotate_grades(out,grade_snapshot or {},U)
    return out
# ---------------- model calls (foreground subprocess, never background) ----------------
CLAUDE_RETIRED = os.path.exists(os.path.join(BASE, "state", "config", "claude_retired.json")) or os.environ.get("TFO_CLAUDE_OFF") == "1"
def llm(engine, prompt, timeout=None):
    """Dispatch optional AI research; provider failures lead to the paper code engine."""
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
    _mdl = getattr(_LB.LAST, "model", None) if _st in ("ok", "reused") else None   # fablefirst_20261103: record the AI that really answered (claude fable max / chatgpt web / grok relay ...)
    rec = dict(utc=now().isoformat(), engine="seat_brain" if _mdl else "code_fallback" if _st=="no_ai_available" else "grok_queue", model=_mdl or ("no AI / code fallback" if _st=="no_ai_available" else "grok_bot/file_queue"), secs=round(time.time() - _t0, 1), tries=1, ok=_st in ("ok", "reused"),
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
        FX_ = q.pop("_fit", None); FX_ = FX_ if isinstance(FX_, dict) else None   # pinlive_20261103: pack's pin context, never rendered
        _ewd = pk.get("engine_wake") if isinstance(pk.get("engine_wake"), dict) else {}
        _top = [str(x.get("sym")) for x in (_ewd.get("top10") or []) if isinstance(x, dict) and x.get("sym")]
        pins = [str(s_) for s_ in ((FX_ or {}).get("pinned") or ([x.get("sym") for x in (_ewd.get("candidates") or []) if isinstance(x, dict)] + _top)) if s_]   # wake + engine top10 + own open/armed (pack); wake + top10 (bare pk)
        prank = (FX_ or {}).get("rank") or {s_: i + 1 for i, s_ in enumerate(dict.fromkeys(_top))}
        rk = [str(x.get("sym") or x.get("ticker") or x) if isinstance(x, dict) else str(x) for x in (pk.get("candidates_ranked") or [])]
        if rk: ins = sorted(ins, key=lambda x: rk.index(x.get("sym")) if x.get("sym") in rk else 999)
        else: ins = sorted(ins, key=lambda x: -(x.get("adv20_value") or x.get("vol24h_usd") or 0))
        keep = ins[:COMPACT_N]; syms = {x.get("sym") for x in keep}
        ew = [str(x.get("sym")) for x in ((pk.get("engine_wake") or {}).get("candidates") or []) if isinstance(x, dict) and x.get("sym")]   # seatdata_20261103 (D26): names the engine WOKE the seat for keep their bars + live quote (ULVR.L, USDMXN, ROR.L had neither: not in the ranked top 10)
        keep=keep+[x for x in ins[COMPACT_N:] if x.get("sym") in set(ew)|set(pins)]
        syms={x.get("sym") for x in keep}|set(ew)
        drop = ("knowable_utc", "sweep_utc", "liquidity_src", "adv20_value_ccy", "venue")
        q["instruments"] = [{k: v for k, v in x.items() if k not in drop} for x in keep]
        try:   # seatdata_20261103 (D26 KVUE): no new daily bar while the same venue printed newer ones = halted/delisted/feed gap, flagged instead of looking current
            nv = {}
            for x in ins: nv[x.get("venue")] = max(nv.get(x.get("venue")) or "", str(x.get("last_date") or ""))
            for x, y in zip(keep, q["instruments"]):
                if x.get("venue") not in ("CRYPTO", "FX", "FUT", "IDX") and x.get("last_date") and str(x["last_date"]) < nv.get(x.get("venue"), ""): y["stale_bar"] = "no bar since %s while %s printed %s - halted/delisted? no live trade" % (x["last_date"], x.get("venue"), nv[x.get("venue")])
        except Exception: pass
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
            q["live_feeds"] = {k: _f(v) for k, v in lf.items() if k != "kraken_futures"}
            kf = (lf.get("kraken_futures") or {}).get("data")   # tfo_krfut_20261101: PF_ funding/OI/basis kept whole (seat syms are KRAKEN:XBTUSD, never matched PF_ keys -> all null)
            if isinstance(kf, dict) and kf:
                q["kraken_futures"] = dict(source="kraken_futures", available_at_utc=lf["kraken_futures"].get("available_at_utc"), note="futures.kraken.com v3 tickers, PF_ perps. funding_rate/funding_pred = USD per contract per hour (relative/h = funding_rate/mark; positive = longs pay shorts); open_interest in contracts; basis_pct = mark/index-1",
                                           rows={k: {f: r.get(f) for f in ("funding_rate", "funding_pred", "open_interest", "mark", "basis_pct")} for k, r in kf.items() if isinstance(r, dict)})
        if isinstance(pk.get("live_quotes"), dict): q["live_quotes"] = {k: v for k, v in pk["live_quotes"].items() if k in syms or k in pins or k in [str(r)[:20] for r in rk[:COMPACT_N]]} or dict(list(pk["live_quotes"].items())[:COMPACT_N])   # pinlive_20261103: + pinned names
        try:   # seatdata_20261103 (D24/D26): whole rows + per-key budgets, kept names first (veteran_desk/pack_fit) - was 700 chars of JSON per key ('candidates_ranked trimmed after rank 5'; engine_wake / kraken_24h / seat_scan / news_tags cut mid-row)
            from veteran_desk import pack_fit as _PFIT; _PFIT.apply(q, [s_ for s_ in syms if s_] + rk[:COMPACT_N], pinned=pins, rank=prank, seat=(FX_ or {}).get("seat"), wake=(FX_ or {}).get("wake") or ((pk.get("engine_wake") or {}).get("wake_id") if isinstance(pk.get("engine_wake"), dict) else None))   # pinlive_20261103
        except Exception:
            q["context_warning"]="Context fitter unavailable; complete source records retained. No trade permission is inferred from this warning."
        lj = json.dumps(q.get("live_feeds"), default=str)
        if len(lj) > 2000:   # fitfill_20261103: whole feeds / whole rows only (was lj[:2000] + '...(trimmed)' = a cut inside a feed object)
            try:
                from veteran_desk import pack_fit as _PF2; q["live_feeds"] = _PF2.fit(q["live_feeds"], 2000)
            except Exception:q["live_feeds_note"]="Whole live-feed records retained; secondary fitter unavailable."
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
def _pnl_open(seat):   # tfo_pnllight_20261101 (Brett): wake prompt opens with the seat's own profit light + P&L (own ledger, read-only); red adds a hunt-hard-within-rules push
    try:
        from veteran_desk import pnl_light as PL; return PL.prompt_line(seat["seat"])
    except Exception: return ""
def _my_positions(seat, pk=None):   # seatexec_20261103 (D23): the seat's OWN open/armed rows, one line each: book tick mark + age, unreal A$ + R, a no-API paid quote (the wake's live_quotes first). THF only.
    if os.environ.get("THF_DESK") != "1": return ""
    try:
        from veteran_desk import my_positions as _MP
        b = _MP.block(seat["seat"], desk=False, quotes=(pk or {}).get("live_quotes") if isinstance(pk, dict) else None, hint=_MP.ROUND_HINT, show_id=False)
        return (b + "\n") if b else ""
    except Exception: return ""
def _seat_ctx(seat, cap=6000):   # seatbrain_20261102 (Brett 08:58): code-only context backfill from droplet data, refreshed every round, no LLM
    if isinstance(seat, dict): seat = seat.get("seat")   # seatdata_20261103 (D26): prop_prompt passes the seat DICT -> .get(dict) / dict + str raised TypeError, so every seat read 'SEAT CONTEXT unavailable (TypeError)'
    try:
        from veteran_desk import book as BK; L = []; B = (BK.load().get("seats") or {}).get(seat) or {}
        C = B.get("closed") or []
        def _pnl(x):   # seatbrain_20261102: computed in code from entry/exit/qty/costs (local ccy x fx if given); None = never filled
            ex = x.get("exit") or x.get("exit_px") or x.get("close_px") or x.get("exit_price")
            if not ex or not x.get("entry") or not x.get("shares") or str(x.get("status") or "").lower() in ("expired", "cancelled", "canceled", "unfilled"): return None
            sg = -1 if str(x.get("side")).upper() == "SHORT" else 1
            return round(sg * (float(ex) - float(x["entry"])) * float(x["shares"]) * float(x.get("fx_aud") or x.get("aud_per_ccy") or 1) - float(x.get("cost_aud") or 0) - float(x.get("exit_cost_aud") or 0), 2)
        for x in C: x["pnl_aud"] = x.get("net_aud") if x.get("net_aud") is not None else _pnl(x)   # in-memory copy only, book not written
        pn = [float(x["pnl_aud"]) for x in C if x.get("pnl_aud") is not None]
        L.append("TRADE HISTORY: %d closed (%d filled), %d wins, net A$%.0f, equity A$%.0f" % (len(C), len(pn), sum(1 for x in pn if x > 0), sum(pn), float(B.get("equity_aud") or 0)))
        for x in C[-8:]: L.append(" closed %s %s %s grade=%s pnl=%s why=%s" % (x.get("ticker"), x.get("side"), str(x.get("closed_utc") or "")[:10], x.get("grade"), x.get("pnl_aud") or x.get("realised_aud"), str(x.get("exit_reason") or x.get("reason") or "")[:60]))
        for k, x in (B.get("positions") or {}).items(): L.append(" open %s %s %s entry=%s stop=%s mark=%s status=%s" % (x.get("ticker"), x.get("side"), x.get("grade"), x.get("entry"), x.get("stop"), x.get("mark"), x.get("status")))
        rp = os.path.join(BASE, "state", "thf", "seat_memory", seat + "_rounds.jsonl")
        if os.path.exists(rp): L += [" round " + l.strip()[:200] for l in open(rp).read().splitlines()[-6:]]
        G = (_rj(os.path.join(BASE, "state", "vd_engine", "grades", seat + ".json"), {}) or {}).get("grades") or {}
        if G: L.append("ENGINE GRADES: " + ", ".join("%s=%s" % kv for kv in list(G.items())[:25]))
        fs = _rj(os.path.join(BASE, "state", "thf", "feed_status.json"), None)
        if fs: L.append("FEED STATUS: " + json.dumps(fs, default=str)[:600])
        t = "\nSEAT CONTEXT (code summary of your own history; advisory, never loosens rails):\n" + "\n".join(L); return t[:cap]
    except Exception as e: return "\nSEAT CONTEXT unavailable (%s)" % type(e).__name__
def _paid_context(seat=None):
    if os.environ.get("PYTEST_CURRENT_TEST"):return ""
    try:
        from veteran_desk import thf_competition as COMP
        return COMP.context(seat=seat) if seat else COMP.context()
    except Exception:
        try:
            from veteran_desk import thf_paid_pack as PAID
            return PAID.brief(seat=seat) if seat else PAID.brief()
        except Exception: return ""

RESEARCH_MANDATE_PROMPT = (
    "\nRESEARCH MANDATE (research_mandate_20261106, PAPER, separate from arming): "
    "On a hunt, include research_candidate even when action is stand_aside. "
    "A bare stand_aside with only a short reason does NOT complete research. "
    "research_candidate object fields: ticker, side LONG|SHORT, asof ISO timestamp, source, unit (price currency, never %), "
    "entry, stop, target, catalyst, fee_bps, slippage_bps, net_r, grade A+|A|B, disconfirming, duplicate_exposure, "
    "test_evidence, blocked_by, next_action. "
    "Do not invent prices, freshness, fees or an A grade. If there is no quote, leave the numbers null, set blocked_by to no_quote "
    "and next_action to the data you need. That is incomplete research, not a finished hunt and not an order. "
    "Grade A or A+ only with fresh source-backed prices and fees-and-slippage net R. "
    "Standing aside stays allowed when the candidate is not executable. Never set confirm true for a candidate you cannot defend.\n")
def prop_prompt(seat, pk, task_id=None):
    return (_pnl_open(seat) + _my_positions(seat, pk) + _paid_context(seat.get("seat")) + f"You are the '{seat['seat']}' seat on a veteran trading desk. Role: {seat['description']}\nPlaybook (habits of veteran traders):\n- " + "\n- ".join(seat["playbook"]) +
            f"\nFUND POSTURE: risk-off fund tilted slightly speculative. Favour defensive sectors; tech/neocloud allowed LONG or SHORT on the data. Shorts need borrow. 'spec' sleeve = small asymmetric winner with a TIGHT stop (<=1.5 ATR) and >=3:1 payoff, never a chase.\nNEWS REGIME: {pk.get('news_regime')}\nSCAN: {pk.get('scan') or 'hand list only'}\n{RULES}\nPick at most ONE setup from the instruments below, or stand aside. Grade honestly: A+ only if trend, clean chart, catalyst/reason and room to target all line up. "
            "Schema: {\"action\":\"propose\"|\"stand_aside\",\"ticker\":str,\"side\":\"LONG\"|\"SHORT\",\"entry\":num,\"stop\":num,\"target\":num,\"grade\":\"A+\"|\"A\"|\"B\","
            "\"sleeve\":\"core\"|\"spec\",\"horizon_days\":int,\"thesis\":str,\"catalyst\":str|null (fresh news/catalyst headline; REQUIRED for grade B unless last-bar volume >=1.5x avg),\"invalidation\":str,\"why_not_choppy\":str,\"graded\":[{\"ticker\":str,\"grade\":\"A+\"|\"A\"|\"B\"|\"reject\",\"reason\":str}],"
            "\"risk_pct\":num (A only: A risk 1.0-2.0 % of seat equity (1% allowed until proven; lift to 2% once proven; do NOT invent a higher size); A+ is 4%; non-A needs 2%),\"lessons_respected\":str,\"to_seats\":{\"<seat id>|all|marcus|head_trader\":\"<=400 chars\"}|null (seatagents_20261103: message colleagues now),\"report_to_marcus\":str|null (formal report up the chain: fills/closes/misses/lessons/faults),\"confirm\":true (set ONLY if you want this proposal armed; omitted -> never arms),\"ranked\":[{\"ticker\":str,\"side\":\"LONG\"|\"SHORT\",\"grade\":\"A+\"|\"A\"|\"B\",\"why\":str}],\"best_long\":str|null,\"best_short\":str|null}\n"   # tfo_seatpipe_3_20261030 + tfo_seatpipe_7a_20261030
            "Always fill 'ranked' with your best 3-5 setups (A+/A/B, best first) even if you stand aside, and name best_long and best_short (null if none worth a B). The proposal, if any, must be ranked[0].\n"
            "If DATA has candidates_ranked: grade its top 10 in rank order in 'graded' (each reject needs a one-line reason).\n" + "".join(x + "\n" for x in seat.get("prompt_overrides", [])) + "DATA:\n" + json.dumps(_data_for_prompt(pk), separators=(",", ":"), default=str) + _dash_block(seat) + _lessons_slim(seat) + _ll_block(seat) + _thf_line(seat) + _seat_mem(seat) + _seat_ctx(seat) + _mail_block(seat, pk) + _brain_block(seat, task_id) + RESEARCH_MANDATE_PROMPT + PAID_PUSH_RULE + _crypto_prepump_line(seat))
def _crypto_prepump_line(seat):
    if (seat or {}).get("seat") != "crypto":
        return ""
    try:
        from veteran_desk import crypto_prepump as CP
        return CP.prompt_line()
    except Exception:
        return ""
def _data_for_prompt(pk):   # seatagents_20261103: the colleague inbox is rendered as its own prompt section, not buried (and trimmed) inside DATA
    q = _slim(compact_pk(pk))
    if isinstance(q, dict) and ("seat_inbox" in q or "seat_inbox_note" in q or "_fit" in q):   # pinlive_20261103: the pin context never reaches the prompt (non-compact path too)
        q = dict(q); q.pop("seat_inbox", None); q.pop("seat_inbox_note", None); q.pop("_fit", None)
    return q
def _mail_block(seat, pk):   # seatagents_20261103 (Brett P0 21:06 WITA 2 Nov): inbox EVERY round + send_to / report_to_marcus in the schema
    try:
        ib = (pk or {}).get("seat_inbox") or []
        try:
            from veteran_desk import seat_registry as _SR; ids = ",".join(_SR.mail_ids())
        except Exception: ids = "head_of_desk,head_trader,<trader seat ids>"
        s = ("\nYOUR INBOX (%d unread from desk colleagues; answer each with to_seats):\n" % len(ib) + "".join("- %s from %s [%s]: %s\n" % (str(r.get("ts_wita") or "")[5:16], r.get("frm"), r.get("kind"), str(r.get("text"))[:600]) for r in ib[:2])) if ib else "\nYOUR INBOX: empty.\n"
        limits = "max 2 per round, 6/h (Marcus/HT 20), 1 per colleague per 15 min, no repeats, 2 hops"
        try:
            from veteran_desk import seat_mail as SM
            limits = SM.limits_text()
        except Exception: pass
        s += ("SEND_TO (seatagents_20261103): \"to_seats\": {\"<seat id>|all|marcus|head_trader\": \"<=400 chars\"} messages colleagues NOW (delivered this round; they answer in chat or their next round; ids: %s). Limits: %s; a message to a colleague who wrote to you = your REPLY (goes to their inbox, no wake). "
              "REPORTING CHAIN (Brett 21:45 WITA 2 Nov): formal reports (fills, closes, misses, lessons, faults, end-of-day, problems) go to MARCUS via \"report_to_marcus\": \"<=300 chars\"; Marcus rolls up to the Head Trader, who answers Brett. You may still DM any seat.\n" % (ids, limits))
        return s
    except Exception: return ""
def _dash_block(seat):   # seatagents_20261103 + master fixer A21/B12: every seat round reads the whole dashboard (veteran_desk/dash_context, cached <=4 min, never raises)
    try:
        from veteran_desk import dash_context as _DC; b = _DC.block(seat["seat"], n=1800)
        try:   # seatexec_20261103 (D23 budget): own rows now in MY POSITIONS -> the snapshot keeps other seats' names only; AI plumbing/desk scan lines are not trading data
            from veteran_desk import my_positions as _MP; b = _MP.compact_dash(b, seat["seat"], desk=False, drop=("BRAINS:", "SCAN (", "Full detail"))
        except Exception: pass
        return ("\n" + b + "\n") if b else ""
    except Exception: return ""
def _brain_block(seat, task_id=None):   # seatagents_20261103: MY FACTS / JOURNAL / LESSONS from the seat's persistent brain (state/thf/seat_brain/<seat>/)
    try:
        from veteran_desk import seat_brain as _SB; return _SB.prompt_block(seat["seat"], 1100, task_id=task_id)
    except Exception: return ""
def _after_round(seat, j):   # seatagents_20261103: deliver round-time to_seats / report_to_marcus (seat_mail: mailbox + chat post + wake) and journal the round. THF only, never in tests.
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("THF_DESK") != "1": return None
    out = None
    try:
        from veteran_desk import seat_mail as _SM; out = _SM.deliver_round(seat, j)
        if out:
            with open(os.path.join(BASE, "logs", "seat_mail_rounds.log"), "a") as _f: _f.write("%s %s %s %s\n" % (now().isoformat(), ENGINE_WAKE, seat, json.dumps(out)))
    except Exception: pass
    try:
        from veteran_desk import seat_brain as _SB; _SB.after_round(seat, j, ENGINE_WAKE)
    except Exception: pass
    return out
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
        if full and desk == "thf":
            # Old THF Markdown/desk_record can contain Veteran Desk closes. Never
            # trust them, even when a newer JSON file exists beside stale Markdown.
            with open(os.path.join(d, seat["seat"] + ".json")) as fh: m = json.load(fh)
            history_keys = ("universe_n", "symbols_with_history", "bars", "date_range", "bar_sources",
                            "method", "setups", "seasonality_month_avg_pct", "seasonality_dow_avg_pct",
                            "earnings_reaction", "vol_regime")
            history = {k: m[k] for k in history_keys if k in m}
            return ("\nCORE MEMORY (mechanical history base rates; advisory, never loosens rails):\n"
                    "Own THF outcomes are not supplied by this historical memory; legacy desk records excluded.\n"
                    + json.dumps(history, ensure_ascii=False, indent=1) + "\n")
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
    ok(p.get("grade") in ("A+", "A", "B"), f"grade {p.get('grade')} - C or worse is a pass")   # histsize 2026-10-31 (Brett 31 Oct): grade B trades at half size; ok(side in ("LONG", "SHORT"), "bad side")
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
        if v == "CRYPTO":   # tfo_pfshort_20261101: crypto shorts only as Kraken Futures PF_ perps, stop above entry
            from veteran_desk import gate_v2 as _G2
            _cb = _G2.crypto_short_block(_G2.canon(t), v)
            if _cb: why.append(_cb)
            else: ok(s > e > 0, "PF_ perp short needs a stop ABOVE entry")
        elif v in ("US", "ASX"):
            try: bn = json.load(open(os.path.join(BASE, "state", "ibkr_borrow.json")))[v.lower()]["names"].get(t.replace(".AX", ""))
            except Exception: bn = None
            ok(bool(bn and bn.get("ok") and (bn.get("available") or 0) > 0), "short needs borrow available (IBKR borrow file)")
    risk = RISK_CAP_PER_TRADE if p.get("grade") == "A+" and isinstance(chal, dict) and chal.get("call") == side else 500
    if p.get("grade") == "B": risk = risk * 0.5   # histsize 2026-10-31: grade B half size
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
            if pct < fl - 1e-9: g.setdefault("warnings", []).append("thf_half_risk: %.2f%% below %.1f%% floor - sized up to floor (Brett 31 Oct size-and-let-in)" % (pct * 100, fl * 100)); pct = fl   # histsize 2026-10-31: never reject sub-floor
            if True: k_ = pct / pct0 if pct0 else TR.size_mult(seat); g.update(risk_pct=round(pct, 5), risk_aud=round(g["risk_aud"] * k_), size_mult=round(float(g.get("size_mult") or 1) * k_, 3))
    except Exception as x: g.setdefault("warnings", []).append("thf half-risk check error %s" % type(x).__name__)
    try:   # tfo_btight_20261101 (Brett HARD 15:15 WITA): grade B = really tight + confirmation (all seats)
        from veteran_desk import b_tight as _BT; _BT.apply_gate(p, g, f, asof)
    except Exception as x: g["passed"] = False; g.setdefault("reasons", []).append("b_tight check error %s - fail closed" % type(x).__name__)
    try:   # tfo_smallcoin_20261101: small-coin tier sizing/rules after the half-risk lift (never lifted back to the 0.5% floor)
        from veteran_desk import small_coin as _SC; _SC.apply_gate(p, g, au, rr_min=G2.TH["rr_min"])
    except Exception as x: g["passed"] = False; g.setdefault("reasons", []).append("small_coin check error %s - fail closed" % type(x).__name__)
    try:
        import audit
        audit.log("risk_decision", "thf" if os.environ.get("THF_DESK") == "1" else "veteran", {
            "ticker": (p or {}).get("ticker"), "side": (p or {}).get("side"), "grade": (p or {}).get("grade"),
            "seat": seat, "passed": bool(g.get("passed")), "reasons": (g.get("reasons") or [])[:8],
            "risk_aud": g.get("risk_aud"), "paper_only": True})
    except Exception:
        pass
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
import threading as _th; _HB_LOCK = _th.Lock()
def _code_fallback(seat, exclude=()):   # seatbrain_20261102 (Brett HARD 08:48 WITA): brain down -> code grade only, A+/A, half of max risk (A 1.0%, A+ 2.0%), gate_v2 + caps still decide
    try:
        top = (_rj(os.path.join(BASE, "state", "vd_engine", "top10", seat + ".json"), {}) or {}).get("top10") or []
        # The warm global top10 includes closed markets. A scheduled THF wake
        # has already selected the eligible session/pre-open candidates; never
        # substitute an unrelated warm idea when its own candidates are B/flat.
        scoped_wake = os.environ.get("THF_DESK") == "1" and bool(ENGINE_WAKE)
        if scoped_wake:
            wake = _engine_wake(seat)
            if not isinstance(wake, dict) or not isinstance(wake.get("candidates"), list):
                return _with_prepump(seat, dict(action="stand_aside", reason="brain_down: selected wake candidates unavailable", _brain_down=True), [])
            top = wake["candidates"]
        held = set()
        if os.environ.get("THF_DESK") == "1":
            book = _rj(os.path.join(BASE, "state", "thf", "thf_book.json"), {}) or {}
            for owner in (book.get("seats") or {}).values():
                for position in (owner.get("positions") or {}).values():
                    if position.get("status") in ("open", "armed"):
                        held.add((position.get("ticker") or position.get("sym"), position.get("side")))
        for x in top:
            if (x.get("sym"), x.get("side")) in held or x.get("sym") in exclude: continue  # THF only; final gate and caps still decide
            if os.environ.get("THF_DESK") == "1" and not scoped_wake and x.get("market_open") is False: continue
            if x.get("grade") in ("A+", "A") and x.get("wake_ok") and x.get("close") and x.get("stop"):
                c, st = float(x["close"]), float(x["stop"])
                return _with_prepump(seat, dict(action="propose", ticker=x["sym"], side=x["side"], entry=c, stop=st, target=round(c + 2 * (c - st), 6), grade=x["grade"], sleeve="core", horizon_days=5,
                            risk_pct=2.0 if x["grade"] == "A+" else 1.0, confirm=True, thesis="code-only fallback (brain down): engine grade %s score %s" % (x["grade"], x.get("score")),
                            catalyst=None, invalidation="stop", why_not_choppy="engine score", lessons_respected="No AI lesson review: deterministic gate_v2, caps and rails unchanged", graded=[], _brain_down=True), top)
    except Exception: pass
    return _with_prepump(seat, dict(action="stand_aside", reason="brain_down: code fallback found no open A/A+", _brain_down=True), locals().get("top") or [])
def _with_prepump(seat, reply, rows):
    """Crypto wakes carry a pre-pump report. The report does not invent a watch or an order."""
    if seat != "crypto" or not isinstance(reply, dict):
        return reply
    try:
        from veteran_desk import crypto_prepump as CP
        return CP.apply_reply(reply, rows or [])
    except Exception:
        return reply
def _seat_heartbeat(seat, j):
    if os.environ.get("PYTEST_CURRENT_TEST"): return   # fixer 2026-11-02: never write live state/thf/seat_heartbeat.json from tests (test guard)
    try:
        p = os.path.join(BASE, "state", "thf", "seat_heartbeat.json"); ok = bool(j) and not j.get("_brain_down"); t = now()
        _mdl = getattr(__import__("veteran_desk.llm_backend", fromlist=["LAST"]).LAST, "model", None) if ok else None   # seatbrain_20261102: model the AI reported (fablefirst_20261103: claude fable max first)
        _rw = t.astimezone(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M WITA")
        from veteran_desk import brain_hb as _HB   # fablefirst_20261103: one locked writer shared with msgdesk/head_review (admin seats)
        with _HB_LOCK:
            h = _HB.update(seat, last_round_utc=t.isoformat(), last_round=_rw, mode="brain" if ok else "brain_down", wake=ENGINE_WAKE, model=_mdl, via="round", **({"last_brain_ok": _rw} if ok else {})) or dict(mode="brain" if ok else "brain_down")
        with open(os.path.join(BASE, "state", "thf", "seat_memory", seat + "_rounds.jsonl"), "a") as f:
            f.write(json.dumps(dict(utc=t.isoformat(), wake=ENGINE_WAKE, mode=h["mode"], action=(j or {}).get("action"), ticker=(j or {}).get("ticker"), grade=(j or {}).get("grade"), why=str((j or {}).get("thesis") or (j or {}).get("reason") or "")[:200])) + "\n")
    except Exception: pass
SEAT_PARALLEL = int(os.environ.get("THF_SEAT_PARALLEL", "6"))   # rounddl_20261103: seat brains per round in parallel (Claude CLIs stay bounded by claude_brain's global cap of 8)
ROUND_BUDGET_S = int(os.environ.get("THF_ROUND_BUDGET_S", "1500"))   # rounddl_20261103: vd_engine kills a desk run at 1800s and then keeps NOTHING
def run(use_llm=True, queue=True, asof=None, queue_path=None, only=None):
    if os.environ.get("THF_DESK") == "1":
        from veteran_desk import thf_round
        return thf_round.run(sys.modules[__name__],use_llm=use_llm,queue=queue,asof=asof,queue_path=queue_path,only=only)
    t0 = time.time(); asof = asof or now(); c = P.con(); S = seats(); au = audusd(c, asof); runid = asof.strftime("%Y%m%d-%H%M")
    # rounddl_20261103 (master fixer 17:10 WITA): the 16:37 all-11 proof round was killed at 30 min and lost ALL 11 seats' work
    # because one seat (tech, 27KB prompt) was still on Opus max at 599s. Every AI call in this process now ends by this deadline
    # (claude_brain / llm_backend read THF_ROUND_DEADLINE); a seat that runs out of time gets the code fallback, the rest keep theirs.
    os.environ["THF_ROUND_DEADLINE"] = str(t0 + ROUND_BUDGET_S)
    props = [s for s in S.values() if s["role"] == "proposer" and (not only or s["seat"] in only)]; log = dict(run_id=runid, asof_utc=asof.isoformat(), paper_only=True, seats={}, proposals=[])
    def _pack_one(s):
        c2 = P.con()
        try: return s["seat"], pack(c2, s, asof)
        finally:
            try: c2.close()
            except Exception: pass
    from veteran_desk import feeds as _FDROUND
    with P.bar_cache(), _FDROUND.round_cache():
        if len(props) > 1:
            with cf.ThreadPoolExecutor(min(4, len(props))) as ex: packs = dict(ex.map(_pack_one, props))
        else: packs = dict([_pack_one(s) for s in props])
    def go(s):
        if not use_llm: return s["seat"], None, "llm disabled"
        task = runid + "-" + s["seat"]
        try: txt = llm(s["engine"], prop_prompt(s, packs[s["seat"]], task)); j = parse_json(txt)   # seatbrain_20261102: per-seat brain, code-only fallback on fail/timeout
        except Exception as e: txt, j = "llm_err %s" % type(e).__name__, None
        if txt and not str(txt).startswith("llm_err"):
            try:
                from veteran_desk import seat_brain as _SB
                _SB.after_chat(s["seat"], "round", txt, task_id=task)
            except Exception: pass
        if j is None and os.environ.get("THF_DESK") == "1": j = _code_fallback(s["seat"]); txt = (txt or "") + " [brain_down -> code fallback]"
        _seat_heartbeat(s["seat"], j)
        _after_round(s["seat"], j)   # seatagents_20261103
        if os.environ.get("THF_DESK") == "1" and j and not j.get("_brain_down") and not os.environ.get("PYTEST_CURRENT_TEST"):
            try:
                from veteran_desk import seat_mail as _SMack
                _SMack.ack(s["seat"], [row.get("id") for row in packs[s["seat"]].get("seat_inbox", [])[:2]], "desk")
            except Exception: pass
        return s["seat"], j, (txt or "")[:400]
    props = sorted(props, key=lambda s: -len(json.dumps(packs.get(s["seat"]) or {}, default=str)))   # rounddl_20261103: biggest prompts start first
    with cf.ThreadPoolExecutor(min(SEAT_PARALLEL, max(1, len(props)))) as ex: res = list(ex.map(go, props))   # blind: each seat sees only its own pack; rounddl_20261103: 6 parallel (was MAX_PARALLEL 4)
    rescanned = {}
    if use_llm and _open_run(asof):   # tfo_seatpipe_7b_20261030 (TFO ruling 18:28 WITA): flat-run re-scan ONLY on the 5 weekday market-open runs, ONLY for seats that stood aside; max 11 extra calls/run (~55/weekday)
        flat = [s for s in props if not any(r[0] == s["seat"] and r[1] and r[1].get("action") == "propose" for r in res)]
        def go2(s):
            pk = dict(packs[s["seat"]]); rk = pk.get("candidates_ranked") or []
            if len(rk) > 10: pk["candidates_ranked"] = rk[10:40]
            pk["rescan_note"] = "SECOND LOOK (flat first pass at a market open): your first pass stood aside. " + ("candidates_ranked now holds ranks 11-40 (unseen); grade its top 10 in 'graded'. " if len(rk) > 10 else "Re-check the same instruments for any A/A+ you missed. ") + "Same rules and gate; standing aside again is fine."
            task = runid + "-" + s["seat"]
            txt = llm(s["engine"], prop_prompt(s, pk, task)); j2 = parse_json(txt); _after_round(s["seat"], j2)
            try:
                from veteran_desk import seat_brain as _SB
                _SB.after_chat(s["seat"], "round", txt, task_id=task)
            except Exception: pass
            return s["seat"], j2, (txt or "")[:400]   # seatagents_20261103
        with cf.ThreadPoolExecutor(min(SEAT_PARALLEL, max(1, len(flat)))) as ex: res2 = list(ex.map(go2, flat))   # rounddl_20261103
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
        if isinstance(j, dict):
            try:   # research_mandate_20261106: score the reply in memory. Disk coverage is written outside tests. Risk gate below is unchanged.
                from veteran_desk import research_mandate as _RM
                _view = _RM.public_view(_RM.assess(j, now=asof, seat=seat))
                log["seats"][seat]["research"] = _view
                if not os.environ.get("PYTEST_CURRENT_TEST"):
                    _RM.observe(seat=seat, reply=j, asof=asof.isoformat(), source="desk.run")
            except Exception:
                pass
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
            try:
                from veteran_desk import small_coin as _SC0; _sct = _SC0.is_small(p["ticker"])
            except Exception: _sct = False
            if v and thin and not _sct: g["passed"] = False; g["reasons"].append("thin coin (Kraken 24h < A$500k) - pass")   # tfo_smallcoin_20261101: tier coins use the 20d A$100k-500k band
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
        if p.get("test"): p = dict(p, test=None)   # TEST tag is operator-only (seat_cli); never honoured from an engine round
        try:   # tfo_smallcoin_20261101: final small-coin pass after every resize (1/4 risk, 2% ADV cap, spread, limit-only, PF_ shorts, cost_R)
            from veteran_desk import small_coin as _SC1, gate_v2 as _G2s; _SC1.apply_gate(p, g, au, rr_min=_G2s.TH["rr_min"])
        except Exception as _x: g["passed"] = False; g.setdefault("reasons", []).append("small_coin check error %s - fail closed" % type(_x).__name__)
        seen.add(p["ticker"]); sz = size(p, f, g.get("risk_aud", 500), au) if g["passed"] else None
        if sz and not sz["passes_doubled_cost"]: g["passed"] = False; g["reasons"].append("fails doubled-cost hurdle")
        if g["passed"]: used += sz["risk_aud"]
        if g.get("quote_asof") and not p.get("quote_asof"):
            p = dict(p, quote_asof=g.get("quote_asof"), quote_last=g.get("quote_last"), quote_src=g.get("quote_src"))
        rec = dict(id=f"VET-{re.sub(r'[^A-Z0-9]', '', p['ticker'].upper())}-{runid}-{seat}", seat=seat, trader=name(S, seat), sub_agents=subs, engine=ran_engine(S[seat]["engine"]), proposal=p,
                   challenger=dict(engine=other, trader=name(S, "blind_challenger"), view=chal), devils_advocate=da, gate=g, sizing=sz, features=f or dict(sym=p["ticker"], venue=P.venue_of(p["ticker"]), pit_missing=True),
                   universe_bias=P.VENUE_BIAS.get(P.venue_of(p["ticker"]), ("?",))[0], created_utc=now().isoformat(), info_only=True, seat_reply_ok=True)
        rec["hash"] = hashlib.sha256(json.dumps([p, sz], sort_keys=True, default=str).encode()).hexdigest()[:16]; out.append(rec)
    log["proposals"] = out
    apply_marcus_limits(S, out)   # code caps only; the Marcus note LLM must not sit in front of the queue (a timeout there used to drop passed proposals)
    arm = [r for r in out if r["gate"]["passed"]]
    try:   # research candidates that fail the predeclared quality bar stay shadow. Legacy proposes with no candidate still follow the gate.
        from veteran_desk import research_mandate as _RM
        kept = []
        for r in arm:
            why = _RM.arm_withheld(r.get("proposal") or {})
            if why:
                r["research_shadow"] = why
            else:
                kept.append(r)
        log["research_shadow"] = [r.get("id") for r in arm if r.get("research_shadow")]
        arm = kept
    except Exception:
        pass
    if queue: log["queued"] = enqueue(arm, au, queue_path, ack_required=not os.environ.get("PYTEST_CURRENT_TEST"))   # tfo_deskrun_ack_20261031: desk-run proposals (ghost-arm ABBV-1521/ISRG) arm only after seat_cli confirm
    log["head_of_desk"] = head_of_desk(S, out, use_llm)
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
def apply_marcus_limits(S, out, committed=()):
    """Code-only Marcus caps (max 2 per venue+side, top 5). Idempotent. Runs before the queue write so a slow note cannot drop a passed proposal."""
    passed=[r for r in out if r["gate"]["passed"]]
    committed=set(committed)
    fixed=[r for r in passed if r.get("id") in committed]
    # Already submitted records cannot be displaced by a later model reply.
    P_ = fixed + sorted([r for r in passed if r.get("id") not in committed], key=lambda r: (r["proposal"].get("grade") != "A+", -(r["gate"].get("rr") or 0)))
    keep, groups = [], {}
    for r in P_:   # correlated-bet kill: max 2 per venue+side, 1 per seat
        k = (r["features"]["venue"], r["proposal"]["side"])
        if groups.get(k, 0) >= 2:
            if "head of desk: correlated with 2 other picks same market+side" not in r["gate"]["reasons"]:
                r["gate"]["passed"] = False; r["gate"]["reasons"].append("head of desk: correlated with 2 other picks same market+side")
            continue
        groups[k] = groups.get(k, 0) + 1; keep.append(r)
    keep = keep[:5]
    for r in P_:
        if r not in keep and r["gate"]["passed"]: r["gate"]["passed"] = False; r["gate"]["reasons"].append("head of desk: outside today's top 5")
    return keep
def head_of_desk(S, out, use_llm, committed=()):
    """Marcus Hale: ranks passed setups, kills dupes/correlated bets (code), enforces caps (code), writes a plain-English note. Cannot arm."""
    keep = apply_marcus_limits(S, out, committed=committed)
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
            Q["entries"][r["id"]] = dict(id=r["id"], book="veteran", ledger=None, seat=r["seat"], owner_seat=r["seat"], proposing_seat=r["seat"], market=r["features"]["venue"], ticker=p["ticker"], name=p["ticker"], side=p["side"],
                entry=float(p["entry"]), trigger=float(p["entry"]), max_fill=float(p["entry"]), stop=float(p["stop"]), target=float(p["target"]), t1=float(p["target"]),
                shares=z["shares"], risk_aud=z["risk_aud"], risk_pct=r["gate"].get("risk_pct"), chal_agree=r["gate"].get("chal_agree"), win_aud=z["win_aud"], score=r["gate"]["rr"],
                tags=dict(news_risk=[], quality="VETERAN_GATE_PASS", catalyst=None, strategy=f"veteran:{r['seat']} grade {p.get('grade')} sleeve {p.get('sleeve') or 'core'}", seat=r['seat'], desk_name=r.get('trader') or r['seat'], desk=('THF' if os.environ.get('THF_DESK') == '1' else 'VD')), sleeve=p.get("sleeve") or "core", grade=p.get("grade"),
                confirm_rvol=p.get("confirm_rvol"), catalyst=p.get("catalyst"), test=("TEST" if p.get("test") else None),   # kai2 1Nov: B fill-time confirm + TEST tag carried to the book
                eli10=f"{r['trader']} (veteran desk, {r['seat']}) likes {p['side'].lower()} {p['ticker']}: {p.get('thesis','')[:220]} Blind challenger said {((r['challenger']['view'] or {}).get('call'))}; devil's advocate: {((r['devils_advocate'] or {}).get('kill_case',''))[:160]} If target hit ~A${z['win_aud']:,}; if stop hit ~A${z['risk_aud']:,}.",
                state=FG.WAIT, decision="approve", decided_by=AUTO_ARM_BY, decided_utc=now().isoformat(), queued_utc=now().isoformat(), expires_utc=__import__("veteran_desk.session_expiry", fromlist=["queue_expiry"]).queue_expiry(r["features"]["venue"], now()), paper_only=True, info_only=True,   # tfo_sessexpiry_20261101: 20h of OPEN market for session venues, wall clock for crypto
               
                ack_required=bool(ack_required), acked=(not ack_required) or (p.get("confirm") is True and r.get("seat_reply_ok") is True), ack_missing=(None if not ack_required else "no_confirm" if p.get("confirm") is not True else "seat_reply_not_ok" if r.get("seat_reply_ok") is not True else None),   # ackwhy_20261103: the book's expiry text names the real reason   # tfo_propose_ack_20261031 + tfo_review_fix: desk-run auto-confirm only on the seat's own explicit "confirm":true in a reply the desk received
                note="VETERAN DESK PAPER auto-arm (Brett 2026-10-28 19:05 WITA): gate_v2 + Marcus passed, book.py arms on its next tick without a funder tick. Brett can still veto before then: python3 funder_approve.py --reject ID", hash=r["hash"], gate_version=r["gate"].get("version", "v1"), gate_warnings=r["gate"].get("warnings", [])[:8], gate_size_mult=r["gate"].get("size_mult", 1.0), seat_risk_pct=r["gate"].get("seat_risk_pct"), override=bool(r["gate"].get("override")), lessons_respected=(p.get("lessons_respected") or "")[:400])
            ids.append(r["id"])
    FG.update(f, path)
    try:
        import audit
        _desk = "thf" if os.environ.get("THF_DESK") == "1" else "veteran"
        for i in ids:
            audit.log("paper_order", _desk, {"id": i, "stage": "queued", "paper_only": True})
    except Exception:
        pass
    return ids
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

# --- tfo_guardian_armguard_20261102 begin ---
try:
    import thf_guardian.arm_guard as _AG
    _AG.install(sys.modules[__name__])
except Exception as _age:
    pass
# --- tfo_guardian_armguard_20261102 end ---
