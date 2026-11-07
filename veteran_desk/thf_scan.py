"""thf_scan_20261102 (PAPER, code only, no AI): deterministic full-universe scanner, one slice per seat.
Brett 2 Nov 20:00 WITA: every seat must scan its WHOLE sector slice, not just the shared engine top-10.
- Universe: data/pit/scan_universe.json (liquid A$500k / >=200 bars, ~9.6k names) via scan_universe.seat_list(),
  Kraken liquid USD pairs for crypto, hand lists + engine FX/futures/index lists for fx_commodities/macro.
- Bars: data/pit/pit.sqlite read-only, point-in-time (knowable_utc <= now). No network in the scan itself.
- Rank: scan_universe.rank_candidates (same coded score desk.py uses; trend/price_action use rank_dex).
- Output: state/thf/scan/<seat>.json = top 50 + counts scanned/ok/stale/failed + newest bar per market;
  state/thf/scan/_summary.json. Seats read their own file (desk.py pack, thf_msgdesk prompt).
- Feed self-repair: a market whose newest bar is >2 business days old (or a seat with >25% stale names) -> feed_heal.jsonl
  row + deterministic repair (pit_eod_sgp.py --exchanges <suffix>, EODHD bulk + Yahoo chart fallback), max once per 2h
  per market; still stale after 2 repairs -> one code_hands job (Claude hands) per market per 12h.
Never writes ledgers, books, gates or the engine. Run: python -m veteran_desk.thf_scan [--no-repair]"""
import datetime as dt, json, os, sqlite3, subprocess, sys, time
from veteran_desk import pit as P
from veteran_desk import scan_universe as SU
L = P.BASE; OUT = os.path.join(L, "state", "thf", "scan"); HEAL = os.path.join(L, "state", "thf", "feed_heal.jsonl")
RST = os.path.join(OUT, "_repair_state.json"); CHQ = os.path.join(L, "state", "thf", "code_hands_queue.jsonl")
UTC = dt.timezone.utc; WITA = dt.timezone(dt.timedelta(hours=8))
SEATS = ["banks", "crypto", "energy", "essentials_utilities", "fx_commodities", "healthcare", "macro", "news_filings",
         "price_action", "tech_ai_infra", "trend"]
TOP_N = 50; STALE_BDAYS = 2; REPAIR_EVERY = 7200; CH_EVERY = 43200
# fxfeed_20261103: source conventions seats must not read as artifacts (written into the fx_commodities / macro scan files + seat_brief)
DATA_NOTE = ("FX =X bars for the OANDA-book pairs are OANDA practice daily mid candles (London day, true close; v = OANDA tick volume, "
             "compare only within FX). The few pairs OANDA does not list or whose dates could not be proven (USDINR, USDKRW, USDBRL, PLN/CZK/"
             "HUF crosses, USDTRY, USDHKD) stay Yahoo spot: volume 0 by nature and Yahoo's FX daily close is that day's 00:00-London quote "
             "(close ~ open). Futures =F volume on the latest completed session may repeat Yahoo's previous value until Yahoo updates it. "
             "Source conventions, not artifacts (fxfeed_20261103).")
FUT_EXTRA = ["CL=F", "BZ=F", "NG=F", "GC=F", "SI=F", "HG=F", "PL=F", "PA=F", "ZW=F", "ZC=F", "ZS=F", "KC=F", "CC=F", "SB=F", "CT=F", "LE=F", "HE=F", "RB=F", "HO=F"]
MACRO_EXTRA = ["^GSPC", "^NDX", "^DJI", "^RUT", "^FTSE", "^GDAXI", "^FCHI", "^N225", "^HSI", "^AXJO", "^STOXX50E", "^TNX", "^FVX", "^TYX", "^IRX", "^VIX",
               "ES=F", "NQ=F", "YM=F", "RTY=F", "ZN=F", "ZB=F", "ZF=F", "ZT=F", "DX-Y.NYB"]
FX_MAJ = ["AUD", "EUR", "GBP", "NZD", "USD", "CAD", "CHF", "JPY"]
def now(): return dt.datetime.now(UTC)
def wita(): return now().astimezone(WITA).strftime("%Y-%m-%d %H:%M:%S WITA")
def jl(p, d):
    try: return json.load(open(p))
    except Exception: return d
def jw(p, o):
    t = p + ".tmp"; json.dump(o, open(t, "w"), default=str); os.replace(t, p)
def heal(row):
    with open(HEAL, "a") as f: f.write(json.dumps(dict(ts_wita=wita(), src="thf_scan", **row)) + "\n")
def mkt(sym):
    if sym.startswith("KRAKEN:"): return "CRYPTO"
    if sym.endswith("=X"): return "FX"
    if sym.endswith("=F") or sym.startswith("^") or sym.startswith("DX-Y"): return "FUT/IDX"   # seatdata_20261103: was startswith("DX") -> TSX funds DXB.TO, DXN.TO ... counted as futures/index
    try: return SU.market_of(sym)
    except Exception: return "?"
def suffix(sym):
    if ":" in sym or "=" in sym or sym.startswith("^"): return None
    return ("." + sym.rsplit(".", 1)[1]) if "." in sym else ""
def bdays(a, b):
    """weekdays strictly after date a up to and including date b"""
    n, d = 0, a
    while d < b:
        d += dt.timedelta(days=1)
        if d.weekday() < 5: n += 1
    return n
# ---- seatdata_20261103 (roll-call 3 Nov, checklist D24/D25/D26): data-quality flags so junk never ranks as 'ok' ----
JUNK_FLAT6, JUNK_ZEROV20, THIN_MEDV20 = 3, 8, 100
def junk_reason(rows, market="FUT/IDX", sym=""):
    """Futures/index daily series nobody can trade off: >= 3 of the last 6 bars O=H=L=C (no trades), or (=F only) >= 8 of the last 20
    sessions with zero volume (Yahoo's =F sits on an illiquid contract month). Lena 3 Nov 17:23: PA=F 5 of 6 bars flat, 13 lots in 6
    sessions; PL=F 3-77 lots/day - both scanned 'ok'. rows = [(date, o, h, l, c, v, ...)] ascending. None = tradable series."""
    if market not in ("FUT/IDX", "FUT", "IDX") or not rows or len(rows) < 6: return None
    flat = sum(1 for r in rows[-6:] if r[2] is not None and r[3] is not None and abs(r[2] - r[3]) <= 1e-9 * max(1.0, abs(r[4] or 1)))
    if flat >= JUNK_FLAT6: return "junk: %d of the last 6 bars O=H=L=C (no trades)" % flat
    if str(sym).endswith("=F"):
        zv = sum(1 for r in rows[-20:] if not r[5])
        if zv >= JUNK_ZEROV20: return "junk: %d of the last 20 sessions zero volume (illiquid contract month)" % zv
    return None
def vol_flag(rows, sym=""):
    """Futures volume that must not feed relvol / the B >= 1.5x gate: the newest bar repeating the previous session's exact volume (Yahoo
    re-uses the prior print until it updates: every =F 2 Nov = 30 Oct, checked 3 Nov), or a thin contract month (median < 100 lots)."""
    if not str(sym).endswith("=F") or not rows or len(rows) < 2: return None
    v1, v0 = rows[-1][5], rows[-2][5]
    if v1 and v0 and v1 == v0: return "vol_repeat: %s volume = %s volume (%d) - Yahoo's repeat, not a print" % (rows[-1][0], rows[-2][0], v1)
    vs = sorted((r[5] or 0) for r in rows[-20:])
    if vs and vs[len(vs) // 2] < THIN_MEDV20: return "thin_contract_month: median %d lots/day" % vs[len(vs) // 2]
    return None
CRYPTO_ALIAS = {"DOGE": "XDG"}   # legacy pit alias -> the Kraken altname the sweep/live feeds use
def crypto_exclusions(c, syms, asof=None):
    """{sym: reason} for Kraken pairs that must not be scanned or ranked: a stablecoin/fiat BASE (exact base match - the old prefix test
    also dropped real tokens AUDIO and T), pegged price behaviour (thf_crypto_feed.is_pegged on 30 daily closes), a stable-QUOTED
    duplicate of a USD pair at the same price (XBTPYUSD, ETHPYUSD, XRPRLUSD, XBTAUSD) or a legacy alias (DOGEUSD = XDGUSD).
    Kai 3 Nov 17:02: 'stable/dupe pairs get ranked: QCADUSD #20, XBTPYUSD #21 (+2.45% vs XBTUSD)'."""
    try:
        from veteran_desk import thf_crypto_feed as TC; SB = set(TC.STABLE_BASES); peg = TC.is_pegged
    except Exception: SB = set(SU.STABLE); peg = None
    qst = sorted((q for q in SB if q.endswith("USD") and len(q) > 3), key=len, reverse=True)
    S = set(syms); out = {}
    try: got = P.bars_asof_many(c, sorted(S), asof or now(), 30)
    except Exception: got = {}
    last = {s: (got.get(s) or [[None] * 5])[-1][4] for s in S}
    for s in sorted(S):
        a = s.split(":", 1)[-1]; base = a[:-3] if a.endswith("USD") else a
        if base.upper() in SB: out[s] = "stablecoin/fiat base %s" % base; continue
        hit = None
        for q in qst:
            if a.endswith(q) and len(a) > len(q):
                twin = "KRAKEN:" + CRYPTO_ALIAS.get(a[:-len(q)], a[:-len(q)]) + "USD"
                if twin in S and twin != s and last.get(s) and last.get(twin) and abs(last[s] / last[twin] - 1) < 0.15:
                    hit = "duplicate of %s quoted in %s (%+.2f%% vs it)" % (twin, q, (last[s] / last[twin] - 1) * 100); break
        if hit: out[s] = hit; continue
        if base in CRYPTO_ALIAS and "KRAKEN:" + CRYPTO_ALIAS[base] + "USD" in S: out[s] = "legacy alias of KRAKEN:%sUSD" % CRYPTO_ALIAS[base]; continue
        cl = [r[4] for r in (got.get(s) or [])]
        if peg and len(cl) >= 30 and peg(a, None, cl): out[s] = "pegged price behaviour (30 daily closes in a fiat/stable band)"
    return out
def _crypto_flags(rows):
    """PF_ perp (Kraken Futures, engine cache kraken_futs.json <= 6 h old) + Kraken 24h liquidity tier (sweep) on crypto scan rows."""
    try:
        K = jl(os.path.join(L, "state", "vd_engine", "kraken_futs.json"), {}) or {}
        kf = (K.get("rows") or {}) if time.time() - float(K.get("utc_ts") or 0) <= 6 * 3600 else None
        W = {"KRAKEN:" + x.get("alt", ""): x for x in ((jl(os.path.join(L, "state", "veteran_desk", "kraken_24h_all.json"), {}) or {}).get("pairs") or [])}
        for r in rows:
            k = (kf or {}).get(r["sym"]) or {}
            r["perp"] = k.get("kf_sym") if kf is not None else "unknown (perp cache stale)"
            r["short_route"] = ("PF_ perp" if k.get("kf_sym") else "none - spot long only") if kf is not None else "unknown"
            v = (W.get(r["sym"]) or {}).get("vol24h_aud")
            r["liq_tier"] = None if v is None else ("A$500k+" if v >= 500000 else ("small A$100k-500k" if v >= 100000 else "thin <A$100k (gate blocks)"))
    except Exception: pass
_ETF = {}
def _etf(sym):
    if not _ETF:
        try: _ETF.update({x["sym"]: x.get("sector") for x in (SU.load() or {}).get("names", [])})
        except Exception: _ETF["_"] = None
    return _ETF.get(sym) == "ETF/Fund"
def _split_suspect(c, rows, asof):
    """Unadjusted split/consolidation in the 260-bar window: a one-day close ratio near 2,3,4,5,8,10.. (bad_tick_guard.split_ratio).
    Trend 3 Nov 17:07: QQD.TO 4.76->25.6, SLVD.TO 2.3->48, HND.TO (TSX 1:5 / 1:8 consolidations 2026-05-25 left unadjusted)."""
    try:
        sys.path.insert(0, L) if L not in sys.path else None
        from bad_tick_guard import split_ratio
        got = P.bars_asof_many(c, [r["sym"] for r in rows if r.get("market") not in ("CRYPTO", "FX", "FUT/IDX")], asof, 260)
        try:
            from veteran_desk import pit_splits as _PSP; ck = _PSP.checks()
        except Exception: ck = {}
        for r in rows:
            b = got.get(r["sym"]) or []; js = []; msg = None
            for i in range(1, len(b)):
                p0, p1 = b[i - 1][4], b[i][4]
                if p0 and p1 and (p1 / p0 >= 1.8 or p1 / p0 <= 0.55):
                    n = split_ratio(p1, p0, 0.08)
                    if n: js.append(b[i][0]); msg = "%s close x%.2f (~%s) - unadjusted split? long-window stats distorted" % (b[i][0], p1 / p0, ("1:%d" % -n) if n < 0 else ("%d:1" % n))
            x = ck.get(r["sym"]) or {}
            if not js or (x.get("verdict") == "real_move" and set(js) <= set(x.get("jumps") or [])): continue   # Yahoo lists no split there: a real move
            r["split_suspect"] = msg
            if r["sym"] not in _SPLIT_TODO: _SPLIT_TODO.append(r["sym"])
    except Exception: pass
_SPLIT_TODO = []
def why(r, mode="default"):
    """One-line reason for a coded scan score (D26: 'top scan scores shown without reasons')."""
    try:
        if mode == "trend":
            st = {1: "close>SMA50>SMA200", -1: "close<SMA50<SMA200"}.get(r.get("trend_state"), "MAs mixed")
            return "12-1 mom %+.0f%%, 6-1 mom %+.0f%%, %s -> %s" % (100 * (r.get("mom12_1") or 0), 100 * (r.get("mom6_1") or 0), st, r.get("bias"))
        if mode == "price_action":
            b = r.get("breakout_hold"); return "rvol %sx (5d %sx)%s, %s ATR from SMA20" % (r.get("rvol"), r.get("rvol5"), (", 20d breakout %s held 3 closes" % b) if b else ", no breakout", r.get("atr_ext"))
        bits = ["trend %s (%s stack/3m)" % (r.get("trend"), r.get("bias"))]
        bits.append("relvol n/a (%s)" % str(r.get("vol_reason") or "no volume").split(":")[0] if r.get("relvol") is None else "relvol %sx" % r.get("relvol"))
        if r.get("rvol_b") is not None: bits[-1] += " (B-gate rvol %sx)" % r["rvol_b"]   # gradefix_20261103: b_tight's own measure (last session / 20d median, >=1.5 or catalyst)
        if r.get("news"):   # gradefix_20261103 (news_filings defect B): say WHAT the hit is (form, time, headline); full record in news_hit
            nh = r.get("news_hit") or {}; f_ = str(nh.get("form") or ""); t_ = str(nh.get("filed_wita") or "")
            u_ = str(nh.get("filed_utc") or ""); s_ = "SEC EDGAR" if nh.get("kind") == "sec_filing" else str(nh.get("source") or "source unknown")[:40]   # newstags_20261103 (F23 / news_filings defect B): + source and UTC time on the same line
            u_ = u_[:16] + " UTC" if u_ and not u_.startswith("unknown") else ("filed " + u_.rsplit("(", 1)[-1].rstrip(")") + ", date only" if "(" in u_ else "UTC time unknown")
            bits.append("news hit: %s%s '%s' (%s, %s)" % ("" if f_.startswith("unknown") else f_ + " ", t_ if not t_.startswith("unknown") else "time unknown", str(nh.get("headline") or "unknown")[:70], s_, u_) if nh else "news hit")
        if r.get("factor"): bits.append("value rank %s" % r.get("factor"))
        if r.get("essentials"): bits.append("essentials %s" % r.get("essentials"))
        return ", ".join(bits)
    except Exception: return None
def _flag_rows(c, seat, rows, asof, mode="default"):
    """seatdata_20261103: per top row - why (score reason), futures vol_repeat/thin month -> relvol None, crypto PF_ perp + liquidity
    tier, ETP flag (ETF/Fund sector), unadjusted-split suspect. Never raises; never re-orders the ranking."""
    try:
        fi = [r["sym"] for r in rows if str(r.get("sym", "")).endswith("=F")]
        gv = P.bars_asof_many(c, fi, asof, 20) if fi else {}
        for r in rows:
            vf = vol_flag(gv.get(r["sym"]) or [], r["sym"]) if r["sym"] in gv else None
            if vf: r["relvol"] = None; r["vol_reason"] = vf; r["unusual_vol"] = False
            if r.get("market") not in ("CRYPTO", "FX", "FUT/IDX") and _etf(r["sym"]): r["etp"] = True
        if seat == "crypto": _crypto_flags(rows)
        if seat in ("trend", "price_action", "energy", "tech_ai_infra", "banks", "healthcare", "essentials_utilities", "news_filings"): _split_suspect(c, rows, asof)
        for r in rows: r["why"] = why(r, mode)
    except Exception: pass
def _crypto_coverage(c, J, syms, asof):
    """Every Kraken sweep pair that is NOT scanned, with its reason, plus the scanned names too short to score (< 64 daily bars)."""
    try:
        E = _EXCL.get("crypto") or {}; X = E.get("excluded") or {}; S = set(syms)
        un = {s: X.get(s) or "no daily bars in pit.sqlite yet (kraken daily loader)" for s in sorted(set(E.get("sweep") or []) - S)}
        cnt = {}
        for i in range(0, len(syms), 400):
            ch = syms[i:i + 400]
            q = "SELECT sym, COUNT(*) FROM bars WHERE sym IN (%s) AND knowable_utc<=? GROUP BY sym" % ",".join("?" * len(ch))
            cnt.update({a: b for a, b in c.execute(q, (*ch, asof.isoformat()))})
        short = sorted(s for s in syms if 0 < cnt.get(s, 0) < 64)
        J["excluded"] = X; J["unscanned_sweep_pairs"] = un; J["too_short_names"] = short
        J["coverage"].update(excluded=len(X), sweep_pairs_not_scanned=len(un))
        J["coverage_note"] = ("universe_total = pairs in the Kraken 24h sweep (all quotes); scanned = USD pairs with pit bars after excluding stablecoin/fiat "
                              "bases, pegs and stable-quoted duplicates; unscanned_sweep_pairs lists every sweep pair not scanned with its reason; "
                              "too_short_names = < 64 daily bars (new listings) so no score yet")
    except Exception: pass
_EXCL = {}
_PS = []
def pit_syms(c):
    if not _PS: _PS.extend(r[0] for r in c.execute("SELECT sym FROM universe"))
    return _PS
def seat_slice(seat, c):
    """(symbols, universe_total, cleared_total, source) for the seat's whole slice."""
    p = jl(os.path.join(L, "veteran_desk", "seats", seat + ".json"), {})
    hand = [s for g in (p.get("universe_groups") or []) for s in (P.CRYPTO_CORE if g == "crypto" else P.UNIVERSE.get(g, []))]
    if seat == "crypto":   # every Kraken USD pair in pit (ex stables/fiat); liquidity floor stays with the gate
        K, n = SU.kraken_liquid(); K = K or []
        allp = [s for s in pit_syms(c) if s.startswith("KRAKEN:") and s.endswith("USD") and not P.banned(s)]   # seatdata_20261103: exact stable/fiat/peg/dupe test below (was a base-PREFIX test: dropped AUDIO and T, kept QCAD and XBTPYUSD)
        U = sorted(set(allp) | set(K) | set(P.CRYPTO_CORE))
        sw = ["KRAKEN:" + x.get("alt", "") for x in ((jl(os.path.join(L, "state", "veteran_desk", "kraken_24h_all.json"), {}) or {}).get("pairs") or []) if x.get("alt")]
        X = crypto_exclusions(c, sorted(set(U) | set(sw))); _EXCL["crypto"] = dict(excluded=X, sweep=sw)
        return [s for s in U if s not in X], n, len(K), "every Kraken USD pair in pit.sqlite ex stablecoin/fiat bases, pegs and stable-quoted duplicates (listed in 'excluded'); %d clear A$500k 24h" % len(K)
    if seat in ("fx_commodities", "macro"):
        have = set(pit_syms(c))
        if seat == "fx_commodities": U = set(P.UNIVERSE.get("fx_cmdty", [])) | {s for s in have if s.endswith("=X") or s.endswith("=F")}
        else: U = set(P.UNIVERSE.get("macro", [])) | {s for s in MACRO_EXTRA + FUT_EXTRA[:6] if s in have} | {s for s in have if s.startswith("^")}
        if seat == "fx_commodities":
            try:   # fxfeed_20261103: canonical FX orientation only (reversed duplicates such as CADAUD=X are neither scanned nor invented)
                from veteran_desk import pit_fx_sgp as PFX; U = set(PFX.canonical_fx(U))
            except Exception: pass
        U = sorted(U); return U, len(U), len(U), "every canonical FX cross + future in pit.sqlite (droplet loader pit_fx_sgp)" if seat == "fx_commodities" else "every index in pit.sqlite + macro futures/FX"
    key = "pit:all" if seat in ("trend", "price_action") else seat
    Lst, Y, Z = SU.seat_list(key)
    if seat == "trend":   # Dex: all liquid equities + crypto core + fx/macro hand lists
        Lst = sorted(set(Lst or []) | set(hand))
    U = sorted(set(Lst or []) | {s for s in hand if not s.startswith("KRAKEN:")})
    return U, Y, Z, "scan_universe.json liquid (A$500k, >=200 bars) sector slice + seat hand list"
def scan(repair=True):
    t0 = time.time(); asof = now(); today = asof.date(); os.makedirs(OUT, exist_ok=True)
    c = sqlite3.connect("file:%s?mode=ro" % P.DB, uri=True, timeout=60); c.execute("PRAGMA busy_timeout=60000")
    slices = {s: seat_slice(s, c) for s in SEATS}
    allsyms = sorted({x for v in slices.values() for x in v[0]})
    last = {}   # newest knowable bar date per symbol (batched, light pauses so the engine keeps DB priority)
    for i in range(0, len(allsyms), 400):
        for s in allsyms[i:i + 400]:
            r = c.execute("SELECT date FROM bars WHERE sym=? AND knowable_utc<=? ORDER BY date DESC LIMIT 1", (s, asof.isoformat())).fetchone()
            last[s] = r[0] if r else None
        time.sleep(0.05)
    frontier = {}
    for s, d in last.items():
        if d: m = mkt(s); frontier[m] = max(frontier.get(m, ""), d)
    mstale = {m: d for m, d in frontier.items() if ((today - dt.date.fromisoformat(d)).days > 2 if m == "CRYPTO" else bdays(dt.date.fromisoformat(d), today) > STALE_BDAYS)}
    summ = dict(utc=asof.isoformat(), wita=wita(), version="thf_scan_20261102", market_newest_bar=frontier, stale_markets=mstale, seats={})
    for seat, (syms, Y, Z, src) in slices.items():
        ok = stale = failed = 0; per = {}; stale_syms = []
        junk = {}   # seatdata_20261103 (D25): zero-range / zero-volume futures series are 'junk', never 'ok', and never ranked
        fi = [s for s in syms if mkt(s) == "FUT/IDX"]
        if fi:
            try:
                gj = P.bars_asof_many(c, fi, asof, 20)
                for s in fi:
                    jr = junk_reason(gj.get(s) or [], "FUT/IDX", s)
                    if jr: junk[s] = jr
            except Exception: pass
        for s in syms:
            d = last.get(s); m = mkt(s); pm = per.setdefault(m, dict(n=0, newest=None, stale=0, failed=0)); pm["n"] += 1
            if not d: failed += 1; pm["failed"] += 1; continue
            pm["newest"] = max(pm["newest"] or "", d)
            cal = m in ("FX", "FUT/IDX", "CRYPTO")   # 24h/odd-calendar markets: own-calendar age, not the cross-section frontier
            if m in mstale or (bdays(dt.date.fromisoformat(d), today) > STALE_BDAYS if cal else d < frontier.get(m, d)): stale += 1; pm["stale"] += 1; stale_syms.append(s) if len(stale_syms) < 40 else None
            elif s in junk: pm["junk"] = pm.get("junk", 0) + 1   # seatdata_20261103: fresh dates but junk bars -> not 'ok'
            else: ok += 1
        mode = seat if seat in ("trend", "price_action") else "default"
        try: _, rows, scored = SU.rank_candidates(c, [s for s in syms if s not in junk], asof, 1000 if seat == "crypto" else TOP_N, mode=mode)
        except Exception as x: rows, scored = [], 0; summ.setdefault("errors", {})[seat] = str(x)[:120]
        if seat == "crypto":   # seatdata_20261103 (Kai D24): same coded score, but gate-tradable rows first (24h liquidity >= A$100k AND long, or short with a PF_ perp)
            _crypto_flags(rows)
            for r in rows:
                t = (r.get("liq_tier") or "").startswith(("A$500k", "small")) and (r.get("bias") == "LONG" or bool(r.get("perp")) and not str(r.get("perp")).startswith("unknown"))
                if not t: r["tradable"] = False; r["untradable_why"] = "thin <A$100k 24h (gate blocks)" if not (r.get("liq_tier") or "").startswith(("A$500k", "small")) else "short with no Kraken PF_ perp"
            rows = ([r for r in rows if r.get("tradable") is not False] + [r for r in rows if r.get("tradable") is False])[:TOP_N]
        for i, r in enumerate(rows):
            r["rank"] = i + 1; r["last_bar"] = last.get(r["sym"]); r["market"] = mkt(r["sym"])
        _levels(c, rows, asof)   # fxfeed_20261103 helper 2: price levels per top row
        _flag_rows(c, seat, rows, asof, mode)   # seatdata_20261103: why / vol_repeat / PF_ perp / liquidity tier / ETP / split flags
        cov = dict(scanned=len(syms), ok=ok, stale=stale, failed=failed, scored=scored, too_short=max(0, len(syms) - len(junk) - failed - scored), universe_total=Y, cleared_total=Z, junk=len(junk))
        J = dict(seat=seat, utc=asof.isoformat(), wita=wita(), version="thf_scan_20261102", source=src, coverage=cov, by_market=per,
                 stale_sample=stale_syms, top=rows, note="deterministic coded scan of the seat's WHOLE slice (no AI). Score: %s. Candidate list only; gate/rails decide." %
                 ("rank_dex %s" % mode if mode != "default" else "trend + 0.5*(relvol-1) + news hit + 0.5*factor rank"),
                 data_note=DATA_NOTE if seat in ("fx_commodities", "macro") else None)
        if junk: J["junk"] = junk; J["junk_note"] = "seatdata_20261103: zero-range/zero-volume series (Yahoo =F on an illiquid contract month) - not ranked until the loader maps the liquid month"
        if seat == "crypto": _crypto_coverage(c, J, syms, asof)   # seatdata_20261103 (Kai: '19/622 unscanned - send the list and the reason')
        jw(os.path.join(OUT, seat + ".json"), J); summ["seats"][seat] = cov
    summ["secs"] = round(time.time() - t0, 1); summ["unique_symbols"] = len(allsyms)
    if repair:
        try: summ["repairs"] = repair_feeds(mstale, summ)
        except Exception as x: summ["repairs"] = "err %s" % str(x)[:120]
    jw(os.path.join(OUT, "_summary.json"), summ); c.close(); return summ
def _levels(c, rows, asof):
    """fxfeed_20261103 helper 2: last close, 20-session high/low and ATR14 (point-in-time pit bars) on every top row, so a seat can set a
    trigger/stop for any scanned name without its own fetch (fx_commodities 3 Nov 15:58 WITA: 'NZDCAD: no cross data on my side')."""
    try:
        got = P.bars_asof_many(c, [r["sym"] for r in rows], asof, 21)
        for r in rows:
            b = got.get(r["sym"]) or []
            if len(b) < 15: continue
            tr = [max(x[2], p[4]) - min(x[3], p[4]) for p, x in zip(b[-15:-1], b[-14:])]
            lv = dict(close=b[-1][4], hi20=max(x[2] for x in b[-20:]), lo20=min(x[3] for x in b[-20:]), atr14=sum(tr) / len(tr))
            r.update({k: float("%.6g" % v) for k, v in lv.items() if v is not None})
    except Exception: pass
def _suffixes_for(market):
    J = SU.load() or {}; return sorted({suffix(x["sym"]) for x in J.get("names", []) if x.get("market") == market and suffix(x["sym"]) is not None})
def repair_feeds(mstale, summ):
    """Deterministic repair first (pit_eod_sgp per exchange), code_hands only if it keeps failing. Rate-limited."""
    st = jl(RST, {}); acts = []; t = time.time()
    seat_bad = [k for k, v in summ["seats"].items() if v["scanned"] and v["stale"] / float(v["scanned"]) > 0.25]
    for m, d in sorted(mstale.items()):
        r = st.setdefault(m, dict(n=0, last=0, ch_last=0)); r["newest"] = d
        if m in ("CRYPTO",):
            if t - r["last"] >= REPAIR_EVERY:
                r["last"] = t; r["n"] += 1; acts.append(dict(market=m, action="kraken_daily", rc=_run(["sudo", "-n", "systemctl", "start", "--no-block", "tfo-thf-kraken-daily.service"])))
        elif m in ("FX", "FUT/IDX"):   # fxfeed_20261103: droplet loader for =X / =F / ^ (tfo-thf-pit-fx.service -> veteran_desk/pit_fx_sgp.py)
            if t - r["last"] >= REPAIR_EVERY:
                r["last"] = t; r["n"] += 1; acts.append(dict(market=m, action="pit_fx_sgp", rc=_run(["sudo", "-n", "systemctl", "start", "--no-block", "tfo-thf-pit-fx.service"])))
        elif m == "?":
            acts.append(dict(market=m, action="report_only", why="no deterministic per-market loader"))
        elif t - r["last"] >= REPAIR_EVERY:
            sx = [s or "" for s in _suffixes_for(m)] or [""]
            r["last"] = t; r["n"] += 1
            rc = _run(["/usr/bin/timeout", "1500", os.path.join(L, "pit_eod_sgp.py"), "--exchanges", ",".join(x if x else "US" for x in sx), "--backfill", "3"])
            acts.append(dict(market=m, action="pit_eod_sgp", exchanges=sx, rc=rc))
        if r["n"] >= 2 and t - r.get("ch_last", 0) >= CH_EVERY:
            r["ch_last"] = t; acts.append(dict(market=m, action="code_hands", id=_code_hands(m, d, r["n"])))
    if _SPLIT_TODO:   # seatdata_20261103 (D26 trend): Yahoo-confirmed split/consolidation fixes for flagged top rows (pit_splits; real moves cached 14 d)
        try:
            from veteran_desk import pit_splits as _PSP; rp = _PSP.run(_SPLIT_TODO[:12], do_apply=True)
            acts.append(dict(market="EQ", action="pit_splits", applied={k: [x["date"] + " " + x["ratio"] for x in v] for k, v in rp["applied"].items()}, real_moves=len(rp["no_event"]), errors=rp["errors"]))
        except Exception as x: acts.append(dict(market="EQ", action="pit_splits", error=str(x)[:100]))
    for k in [k for k in st if k not in mstale]: st[k]["n"] = 0   # recovered
    if mstale or seat_bad: heal(dict(stale_markets=mstale, seats_over_25pct_stale=seat_bad, actions=acts))
    jw(RST, st); return acts
def _run(cmd):
    try: return subprocess.run(cmd, capture_output=True, text=True, timeout=1600, cwd=L).returncode
    except Exception as x: return "err %s" % type(x).__name__
def _code_hands(m, d, n):
    """Append one job to the msgdesk code_hands FIFO (same queue Brett/seats use); msgdesk worker runs it."""
    jid = "ch-%d-scan" % time.time()
    txt = ("[thf_scan auto feed repair] market %s newest daily bar %s is >%d business days old after %d deterministic repair attempts "
           "(pit_eod_sgp.py / kraken daily). Diagnose the loader (EODHD bulk, Yahoo chart fallback, knowable_utc offset), fix it under the "
           "standing rules (backup, DEPLOY_LOG, tests, PAPER, no ledger edits, never invent prices), then rerun the loader." % (m, d, STALE_BDAYS, n))
    from veteran_desk import thf_msgdesk as MD
    answer,_mode=MD.code_hands(txt,via="thf_scan",root="scan-repair-"+str(m)+"-"+str(d),key="scan:"+str(m)+":"+str(d))
    return answer

def seat_brief(seat, n=20):
    """Compact view for prompts: coverage + top n (sym, score, bias, last_bar)."""
    J = jl(os.path.join(OUT, seat + ".json"), None)
    if not J: return None
    keep = ("sym", "rank", "score", "bias", "trend", "relvol", "news", "mom12_1", "rvol", "breakout_hold", "last_bar", "market", "close", "hi20", "lo20", "atr14",   # fxfeed helper 2: + levels
            "why", "vol_reason", "perp", "short_route", "liq_tier", "untradable_why", "etp", "split_suspect", "news_hit", "rvol_b")   # seatdata_20261103: reasons + data-quality flags; gradefix_20261103: + news_hit record, B-gate rvol
    out = dict(file="state/thf/scan/%s.json" % seat, at=J.get("wita"), coverage=J.get("coverage"), data_note=J.get("data_note"),
               newest_bar_by_market={k: v.get("newest") for k, v in (J.get("by_market") or {}).items()})
    if J.get("junk"): out["junk_not_ranked"] = J["junk"]   # seatdata_20261103 (D25)
    if J.get("unscanned_sweep_pairs") is not None:   # seatdata_20261103 (D24): every unscanned Kraken sweep pair grouped by reason (before 'top' so trimming keeps it)
        g = {}
        for s, w in J["unscanned_sweep_pairs"].items():
            k = w.split(" (")[0]; k = "duplicate quote" if k.startswith("duplicate of") else ("stablecoin/fiat base" if k.startswith("stablecoin/fiat base") else k)
            g.setdefault(k, []).append(s.split(":", 1)[-1])
        out["unscanned"] = {k: ", ".join(v) for k, v in g.items()}
        xo = [s for s in (J.get("excluded") or {}) if s not in J["unscanned_sweep_pairs"]]   # pit-only pairs dropped as duplicates/aliases/pegs (XBTPYUSD used to rank #21)
        if xo: out["unscanned"]["excluded pit pairs (duplicate quote / alias / peg)"] = ", ".join(s.split(":", 1)[-1] for s in xo)
        out["too_short_names"] = ", ".join(s.split(":", 1)[-1] for s in (J.get("too_short_names") or []))
    dup = ("trend", "relvol", "news", "mom12_1", "rvol", "breakout_hold", "vol_reason", "short_route", "rvol_b")   # already stated in 'why' (prompt size: msgdesk caps market context at 12k chars); gradefix_20261103: + rvol_b
    out["top"] = []
    for r in (J.get("top") or [])[:n]:
        x = {k: r.get(k) for k in keep if r.get(k) not in (None, False) or (k == "perp" and "perp" in r)}
        out["top"].append({k: v for k, v in x.items() if k not in dup} if x.get("why") else x)
    try:
        from veteran_desk import intraday_store as IB
        for row in out.get("top") or []:
            if isinstance(row,dict) and IB.is_us(row.get("sym")):row["rvol_tod"]=IB.volume_tod(row["sym"])
    except Exception:out["intraday_volume_note"]="same-time intraday volume unavailable; daily volume remains separately labelled"
    return out
if __name__ == "__main__":
    if "-h" in sys.argv or "--help" in sys.argv:   # fxfeed_20261103 (helper 2): --help used to START a full scan with repairs (no argparse here)
        print(__doc__ + "\nFlags: --no-repair (scan only). Scheduled runs hold /tmp/thf_scan.lock (tfo-thf-scan.service, tfo-thf-pit-fx.service ExecStartPost);"
              " manual: flock -w 900 /tmp/thf_scan.lock python -m veteran_desk.thf_scan --no-repair"); sys.exit(0)
    s = scan(repair="--no-repair" not in sys.argv)
    print(json.dumps(dict(secs=s["secs"], unique=s["unique_symbols"], stale_markets=s["stale_markets"], seats=s["seats"], repairs=s.get("repairs")), indent=1))
