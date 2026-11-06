"""seatdata_20261103 (PAPER, prompt data only): structure-aware trimming of the compact seat prompt DATA (desk.compact_pk).

WHY (roll-call rollcall-20261103-1547, seats' own words 3 Nov 15:58-17:59 WITA): compact_pk cut every DATA key whose JSON was over
1,500 chars to its first 700 characters + '...(trimmed)'. candidates_ranked (10 rows ~1.8 KB) lost ranks 6-10 mid-row
(essentials 17:08 'candidates_ranked trimmed after rank 5'), engine_wake lost the woken candidates (fx 16:54 'engine wake is trimmed
after USDMXN'), and kraken_24h / seat_scan / news_tags / calendar_72h / intraday / macro_bars were cut mid-object (crypto 17:38,
fx 16:54, essentials 17:08). The cut also hid the paid derivatives/dominance rows the crypto seat needs.

NOW: whole rows only, a per-key character budget, the seat's kept symbols first, and an explicit '+N more' marker so a seat knows
the list continues. Budgets scale with env VET_FIT_SCALE (default 1.0) if prompts must shrink. Never raises: on any error the caller
keeps its own value. No network, no state writes.

pinlive_20261103 (crypto seat 19:53 WITA 3 Nov, fm-20261103115348-cfe4; Sam Reyes p1 work order): the 19:42 crypto prompt kept its
crypto_live rows in insertion order (woken name, then liquidity) until the 5.2 KB budget and cut the rest ('_more': 6) - KRAKEN:XMRUSD,
the engine's #1 B (83.2), was row 11 and was never in the prefer list (kept instruments + woken names only; the engine top10 and the
seat's own open/armed names were not in it). NOW, for the {sym: row} live blocks (LIVE: crypto_live rows, live_quotes): rows of PINNED
symbols (engine wake list + engine top10 + the seat's own open/armed names, from desk.compact_pk) are never trimmed; the other live rows
are kept best engine rank first (unranked after them) and trimmed lowest rank first; if the pinned rows alone overflow the key budget the
overflow is taken from LOW_PRI sections, and if that is not enough the pinned rows stay anyway and a warning is logged. Every build
appends what it trimmed to logs/pack_fit.log (env VET_FIT_LOG overrides; tests write nowhere unless they set it).

fitfill_20261103 (crypto seat 21:19 + 21:25 WITA 3 Nov, Sam Reyes p1 ch-1793712670-8c3f): fit() still cut INSIDE objects - it recursed two
levels deep, so a row (crypto_live KRAKEN:NEARUSD) kept some keys and lost 9 ('_more'), and it reordered keys (preferred first). NOW, for
every seat: fit() only ever drops whole keys / rows / list items (it recurses once, into a list or a {name: row} map, never into a row) and
keeps the original key order; fit_live emits the kept rows in their original order. CRYPTO only (TIERED): one pooled budget = the sum of what
each key's own budget allowed (the prompt never grows), filled by priority tier - tier 1 engine top10 entries + crypto_live rows (pinned =
held/armed + top10 + wake names, then the rest in current order), tier 2 catalyst items tagged KRAKEN:/CRYPTO (or a vendor crypto ticker
X:BTCUSD / BTC-USD / AION-USD.CC), tier 3 generic catalyst items, news_tags, everything else (engine woken candidates last). Lowest tier and
the END of each list go first; pinned rows and top10 entries are never dropped (warning if they alone overflow). A compact '_trimmed'
manifest (dropped count per key + dropped crypto_live symbols) is counted inside the budget."""
import copy as _copy, datetime as _dt, json, os, re, sys

TAG = "seatdata_20261103"
BUDGET = {"candidates_ranked": 1900, "engine_wake": 2200, "seat_scan": 2400, "crypto_live": 5200, "crypto_market": 900,
          "intraday": 1400, "calendar_72h": 1000, "catalyst_stream": 800, "kraken_24h": 900, "news_tags": 1000,
          "macro_bars": 2200, "screens": 1000, "live_quotes": 2200}
DEFAULT = 1500
SKIP = ("instruments", "live_feeds", "live_quotes", "kraken_futures")   # compact_pk handles these itself (live_quotes: compact_pk filters, fit_live below budgets it)
LIVE = {"crypto_live": "rows", "live_quotes": None}   # pinlive_20261103: key -> sub-key holding the {sym: row} map (None = the value itself)
LOW_PRI = ("screens", "news_tags", "calendar_72h", "catalyst_stream", "macro_bars", "seat_scan")   # pinlive_20261103: shrink order when pinned live rows overflow
FLOOR = 160   # a low-priority section is never shrunk below this many chars
LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "pack_fit.log")
LAST = {}   # the last build's trim report (tests / proofs)
EW_KEEP = ("sym", "side", "grade", "score", "close", "stop", "k_atr", "atr_pct", "rvol", "ret20", "last_bar", "market", "news",
           "signals", "floors_failed", "market_open", "wake_ok", "why_no_wake", "earnings_block", "pillars_z", "history", "tier", "wake_block")
EW_TOP_KEEP = ("sym", "side", "grade", "score", "close", "stop", "k_atr", "rvol", "why_no_wake")
HIST_KEEP = ("n", "exp_R", "natural_pct", "size_cuts", "wake_ok")


def _js(v):
    return json.dumps(v, separators=(",", ":"), default=str)


def scale():
    try: return max(0.3, min(3.0, float(os.environ.get("VET_FIT_SCALE", "1") or 1)))
    except Exception: return 1.0


def budget(key):
    return int(BUDGET.get(key, DEFAULT) * scale())


def lean(r):
    """Drop empty / null / False fields from one row (a False flag carries no information in a ranked row)."""
    if not isinstance(r, dict): return r
    return {k: v for k, v in r.items() if v not in (None, False, "", [], {})}


def _hit(k, prefer):
    k = str(k)
    return any(p and (p == k or p in k or k in p) for p in prefer)


def _splittable(x):
    """fitfill_20261103: a value fit() may shorten = a list (drop trailing whole items) or a {name: row|list} map (drop whole entries).
    A row/record (any scalar value) is never split - it is kept or dropped whole."""
    return isinstance(x, list) or (isinstance(x, dict) and bool(x) and all(isinstance(y, (dict, list)) for y in x.values()))


def _fit_record(rec, b):
    """fitfill_20261103: a section's record that does not fit keeps EVERY key; only its list fields lose trailing whole items (largest list
    first, '+N more' marker). None when it still cannot fit (the caller then drops the record whole). Never cuts a key or a scalar."""
    lists = sorted((k for k, x in rec.items() if isinstance(x, list) and x), key=lambda k: -len(_js(rec[k])))
    if not lists: return None
    out = dict(rec)
    for k in lists:
        if len(_js(out)) <= b: break
        out[k] = fit(rec[k], max(2, b - (len(_js(out)) - len(_js(out[k])))))
    return out if len(_js(out)) <= b else None


def fit(v, b, prefer=None, _depth=0):
    """Whole items only. list -> leading items that fit + '+N more (trimmed to fit)'; dict -> preferred keys chosen first, then the rest,
    emitted in the ORIGINAL key order, '_more': N for dropped keys; one level of recursion only, and only into a list or a {name: row} map
    (fitfill_20261103: was two levels into any dict, which kept part of a row); str -> cut + '...(trimmed)' (top-level strings only)."""
    if len(_js(v)) <= b: return v
    prefer = [str(p) for p in (prefer or []) if p]
    if isinstance(v, list):
        out, n = [], 2
        for x in v:
            s = len(_js(x)) + 1
            if n + s > b - 34: break
            out.append(x); n += s
        if len(out) < len(v): out.append("+%d more (trimmed to fit)" % (len(v) - len(out)))
        return out
    if isinstance(v, dict):
        keys = list(v.keys())
        order = ([k for k in keys if _hit(k, prefer)] + [k for k in keys if not _hit(k, prefer)]) if prefer else keys
        got, n, more = {}, 2, 0
        for k in order:
            s = len(_js({k: v[k]}))
            if n + s <= b - 24: got[k] = v[k]; n += s; continue
            room = b - 24 - n - len(_js(k)) - 2
            if _depth < 1 and room > 160:
                if _splittable(v[k]): got[k] = fit(v[k], room, prefer, _depth + 1); n += len(_js({k: got[k]})); continue
                r = _fit_record(v[k], room) if isinstance(v[k], dict) else None
                if r is not None: got[k] = r; n += len(_js({k: r})); continue
            more += 1
        out = {k: got[k] for k in keys if k in got}   # fitfill_20261103: original key order
        if more: out["_more"] = "%d more key(s) trimmed to fit" % more
        return out
    if isinstance(v, str): return v[:max(0, b - 14)] + "...(trimmed)"
    return v


def slim_engine_wake(ew):
    """Woken candidates in full (the A/A+/B names the engine woke this seat for), engine top10 as one-line rows."""
    if not isinstance(ew, dict): return ew
    o = dict(ew)
    cs = []
    for x in (ew.get("candidates") or []):
        if not isinstance(x, dict): cs.append(x); continue
        y = {k: x.get(k) for k in EW_KEEP if k in x}
        if isinstance(y.get("history"), dict): y["history"] = {k: y["history"].get(k) for k in HIST_KEEP if k in y["history"]}
        cs.append(lean(y))
    if "candidates" in ew: o["candidates"] = cs
    if isinstance(ew.get("top10"), list): o["top10"] = [lean({k: x.get(k) for k in EW_TOP_KEEP}) if isinstance(x, dict) else x for x in ew["top10"]]
    return o


def _rank_key(rank, order):
    def k(s):
        r = (rank or {}).get(s)
        return (0, float(r), order.get(s, 0)) if isinstance(r, (int, float)) else (1, 0.0, order.get(s, 0))
    return k


def fit_live(v, sub, b, pinned=(), rank=None):
    """pinlive_20261103: fit one {sym: row} live block to b chars. Pinned rows are always kept; the other rows are kept best engine rank
    first (unranked after them, original order) and cut lowest rank first; the cut rows are named in 'rows_trimmed' (sub-keyed block)
    or '_trimmed' (plain map). Returns (value, report); report None when v holds no row map. A block that already fits is unchanged."""
    rows = (v.get(sub) if sub else v) if isinstance(v, dict) else None
    if not isinstance(rows, dict): return v, None
    mk = "rows_trimmed" if sub else "_trimmed"
    rows = {s: x for s, x in rows.items() if s not in ("_more", mk)}
    pins = [s for s in dict.fromkeys(str(p) for p in (pinned or []) if p) if s in rows]
    order = {s: i for i, s in enumerate(rows)}
    rest = sorted((s for s in rows if s not in pins), key=_rank_key(rank, order))

    def build(keep, cut):
        note = ("%d row(s) trimmed to fit, lowest engine rank first: %s" % (len(cut), ", ".join(cut[:8]) + (" +%d more" % (len(cut) - 8) if len(cut) > 8 else ""))) if cut else None
        ks = set(keep); R = {s: rows[s] for s in rows if s in ks}   # fitfill_20261103: kept rows in their original order (rank only decides which go)
        if sub:
            o = {k: x for k, x in v.items() if k not in (sub, mk)}; o[sub] = R   # rows last (crypto_live keeps asof/src/note first)
        else: o = R
        if note: o[mk] = note
        return o
    if len(_js(v)) <= b:
        return v, dict(budget=b, chars=len(_js(v)), pinned=pins, kept=len(rows), trimmed=[], overflow=0)
    n = 0
    while n < len(rest) and len(_js(build(pins + rest[:n + 1], rest[n + 1:]))) <= b: n += 1
    out = build(pins + rest[:n], rest[n:]); ch = len(_js(out))
    return out, dict(budget=b, chars=ch, pinned=pins, kept=len(pins) + n, trimmed=rest[n:], overflow=max(0, ch - b))


TIERED = ("crypto",)   # fitfill_20261103: seats whose DATA shares one pooled budget, filled by priority tier (key priority changes for these only)
POOL_SKIP = ("instruments", "live_feeds", "kraken_futures")   # compact_pk sizes these itself; never in the pool
_CRX = re.compile(r"^(kraken:|crypto)", re.I)
_CRY_FMT = re.compile(r"^(X:[A-Z0-9]{2,15}|[A-Z0-9]{2,12}-USDT?|[A-Z0-9]{2,12}-[A-Z]{3,4}\.CC)$", re.I)   # Massive X:BTCUSD, Yahoo BTC-USD, EODHD AION-USD.CC


def crypto_tagged(it):
    """Tier 2 test for one catalyst item: a tag/ticker starting 'KRAKEN:' or 'CRYPTO' (case-insensitive), or a crypto ticker in the format
    the live vendors actually send (the crypto queue carries X:BTCUSD / BTC-USD / AION-USD.CC tickers, never 'KRAKEN:')."""
    if not isinstance(it, dict): return False
    tags = []
    for f in ("tag", "tags", "tickers", "symbols", "sym"):
        x = it.get(f)
        tags += [str(t) for t in x] if isinstance(x, list) else ([str(x)] if x else [])
    return any(_CRX.match(t.strip()) or _CRY_FMT.match(t.strip()) for t in tags if t)


def _drop(cont, ref):
    kind, x = ref
    if kind == "k":
        if isinstance(cont, dict) and x in cont: del cont[x]; return True
        return False
    for i in range(len(cont) - 1, -1, -1):
        if cont[i] is x: del cont[i]; return True
    return False


def _section_steps(k, v):
    """'everything else' (tier 3): a section's lists shrink from their END (lists in key order), then its {name: row} entries go whole
    (last first); scalars stay."""
    S = []
    if isinstance(v, list): return [(k, k, v, ("o", x), None) for x in reversed(v)]
    if not isinstance(v, dict): return S
    for sk, sv in v.items():
        if isinstance(sv, list): S += [(k, "%s.%s" % (k, sk), sv, ("o", x), None) for x in reversed(sv)]
    S += [(k, k, v, ("k", sk), None) for sk in reversed(list(v)) if isinstance(v[sk], dict)]
    return S


def _drop_plan(q, pinned, rank=None):
    """Drop order, first = dropped first. Tier 3: generic catalyst items, news_tags entries, everything else (reverse DATA order; the
    catalyst wake list; engine woken candidates last). Tier 2: KRAKEN:/CRYPTO catalyst items. Tier 1: crypto_live rows that are not pinned
    (pinrank_20261103: lowest engine rank first - unranked rows from the end, then ranked worst first; no rank = end first as before).
    Pinned crypto_live rows and engine top10 entries are never in the plan."""
    S = []
    cs = q.get("catalyst_stream"); items = cs.get("items") if isinstance(cs, dict) and isinstance(cs.get("items"), list) else []
    cry = [x for x in items if crypto_tagged(x)]; gen = [x for x in items if not crypto_tagged(x)]
    S += [("catalyst_stream", "catalyst_stream.items", items, ("o", x), None) for x in reversed(gen)]
    nt = q.get("news_tags")
    if isinstance(nt, dict): S += [("news_tags", "news_tags", nt, ("k", k), None) for k in reversed(list(nt))]
    elif isinstance(nt, list): S += [("news_tags", "news_tags", nt, ("o", x), None) for x in reversed(nt)]
    for k in reversed(list(q)):
        if k in POOL_SKIP or k in ("catalyst_stream", "news_tags", "engine_wake", "crypto_live", "live_quotes", "candidates_ranked", "seat_scan", "_trimmed"): continue
        S += _section_steps(k, q[k])
    if isinstance(cs, dict) and isinstance(cs.get("wake"), list):
        S += [("catalyst_stream", "catalyst_stream.wake", cs["wake"], ("o", x), None) for x in reversed(cs["wake"])]
    ew = q.get("engine_wake"); cands = ew.get("candidates") if isinstance(ew, dict) and isinstance(ew.get("candidates"), list) else []
    # Wake candidates are primary work, never discarded to make room for advisory history.
    scan=q.get("seat_scan")
    if isinstance(scan,dict):
        for key,value in scan.items():
            if key!="top":S += _section_steps("seat_scan", {key:value})
        top=scan.get("top") or []
        if isinstance(top,list):S += [("seat_scan","seat_scan.top",top,("o",x),None) for x in reversed(top[15:])]
    S += [("catalyst_stream", "catalyst_stream.items", items, ("o", x), None) for x in reversed(cry)]
    pins = {str(p) for p in (pinned or ())}
    for key,sub in LIVE.items():
        value=q.get(key)
        rows=value.get(sub) if sub and isinstance(value,dict) else value
        if not isinstance(rows,dict):continue
        order={sym:i for i,sym in enumerate(rows)}
        rest=sorted((sym for sym in rows if sym not in pins),key=_rank_key(rank,order))
        S += [(key,key+(".rows" if sub else ""),rows,("k",sym),sym if key=="crypto_live" else None) for sym in reversed(rest)]
    for key,value in reversed(list(q.items())):
        if key not in POOL_SKIP and key not in ("scan","asof_utc","news_regime") and isinstance(value,str) and len(value)>300:
            S.append((key,key,q,("k",key),None))
    return S


CORE_ROW=("sym","ticker","rank","side","bias","grade","score","close","last_bar","bar_age_d","bar_age_min","rvol_tod")
WAKE_ROW=CORE_ROW+("stop","k_atr","atr_pct","rvol","ret20","history","wake_ok","market_open","why_no_wake","wake_block","floors_failed","earnings_block")
def _project_rows(rows,fields):
    return [{k:r[k] for k in fields if k in r} if isinstance(r,dict) else r for r in rows]
def _compact_critical(q, budget_chars):
    projected={}
    ranked=q.get("candidates_ranked")
    if isinstance(ranked,list):
        q["candidates_ranked"]=_project_rows(ranked[:10],CORE_ROW)
        projected["candidates_ranked"]=dict(rows_in=len(ranked),rows_out=len(q["candidates_ranked"]),fields=list(CORE_ROW))
    scan=q.get("seat_scan")
    if isinstance(scan,dict) and isinstance(scan.get("top"),list):
        rows=scan["top"];scan["top"]=_project_rows(rows,CORE_ROW)
        projected["seat_scan.top"]=dict(rows_in=len(rows),rows_out=len(rows),fields=list(CORE_ROW))
    wake=q.get("engine_wake")
    if isinstance(wake,dict) and isinstance(wake.get("candidates"),list):
        rows=wake["candidates"];wake["candidates"]=_project_rows(rows,WAKE_ROW)
        # Preserve every candidate; compact decision facts before their block exceeds 20% of the existing pool.
        if len(_js(wake["candidates"]))>budget_chars*.2:
            wake["candidates"]=_project_rows(rows,CORE_ROW+("stop","k_atr","rvol","history"))
        projected["engine_wake.candidates"]=dict(rows_in=len(rows),rows_out=len(rows),fields=list(WAKE_ROW))
    return projected

def fill_tiers(q, pinned=(), rep=None, rank=None):
    """fitfill_20261103: crypto's pooled whole-object tier fill (see module doc). Mutates q (its pool values are deep-copied first, so the
    caller's pack is never changed). Under budget: q untouched, no manifest."""
    rep = rep if isinstance(rep, dict) else {}
    keys = [k for k in q if k not in POOL_SKIP and k != "_trimmed"]
    size = {k: len(_js(q[k])) for k in keys}
    B = sum(min(size[k], budget(k)) for k in keys); n0 = sum(size.values())
    pins = [str(p) for p in (pinned or ()) if p]
    if n0 <= B:
        rep["tiers"] = dict(budget=B, chars=n0, dropped={}); return q
    for k in keys:q[k]=_copy.deepcopy(q[k])
    before_projection={k:len(_js(q[k])) for k in ("engine_wake","candidates_ranked","seat_scan") if k in q}
    projected=_compact_critical(q,B)
    if rep.get("seat") not in TIERED:
        # Preserve the existing whole-bar compaction for large noncritical records before pooling spare room.
        for key in keys:
            if key not in LIVE and key not in ("engine_wake","candidates_ranked","seat_scan") and isinstance(q[key],(dict,list)) and len(_js(q[key]))>budget(key):
                q[key]=fit(q[key],budget(key))
    projection_sizes={k:dict(before=before_projection[k],after=len(_js(q[k]))) for k in before_projection if before_projection[k]!=len(_js(q[k]))}
    size={k:len(_js(q[k])) for k in keys}
    steps = _drop_plan(q, pins, rank); dropped, live_cut = {}, []

    def man():
        if not dropped and not projection_sizes:return None
        m = dict(budget=B, dropped=dict(dropped))
        if projection_sizes:m["compacted"]=projection_sizes
        if live_cut: m["crypto_live"] = list(live_cut)
        return m
    i = 0
    while True:
        m = man(); tot = sum(size.values()) + (len(_js({"_trimmed": m})) if m else 0)
        if tot <= B or i >= len(steps): break
        top, label, cont, ref, sym = steps[i]; i += 1
        if _drop(cont, ref):
            dropped[label] = dropped.get(label, 0) + 1; size[top] = len(_js(q[top])) if top in q else 0
            if sym: live_cut.append(sym)
    m = man()
    if m: q["_trimmed"] = m
    ew = q.get("engine_wake"); cl = q.get("crypto_live")
    t1 = len(_js((ew or {}).get("top10") if isinstance(ew, dict) else None)) + len(_js((cl or {}).get("rows") if isinstance(cl, dict) else None))
    rep["tiers"] = dict(budget=B, chars_in=n0, chars_out=tot, dropped=dropped, crypto_live_dropped=live_cut, tier1_chars=t1, pinned=pins,projected=projected)
    if tot > B: rep.setdefault("warnings", []).append("tier fill: pinned crypto_live rows + engine top10 alone overflow the pooled budget by %d chars; kept whole" % (tot - B))
    return q


def _log(rep):
    global LAST
    LAST = rep
    p = os.environ.get("VET_FIT_LOG") or ("" if os.environ.get("PYTEST_CURRENT_TEST") else LOG)
    if p:
        try:
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
            with open(p, "a") as f: f.write(_js(rep) + "\n")
        except Exception: pass
    for w in rep.get("warnings") or []:
        try: print("pack_fit WARNING %s %s: %s" % (rep.get("seat"), rep.get("wake"), w), file=sys.stderr)
        except Exception: pass


def apply(q, syms=(), pinned=(), rank=None, seat=None, wake=None):
    """Fit every oversized DATA key of the compact prompt dict q in place (except the keys compact_pk handles itself). Returns q.
    pinlive_20261103: pinned = symbols whose live rows are never trimmed; rank = {sym: engine rank (1 = best)} for the trim order."""
    pinned = [str(s) for s in dict.fromkeys(pinned or []) if s]
    prefer = sorted({str(s) for s in (syms or []) if s} | set(pinned))
    rep = dict(utc=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), tag="pinlive_20261103", seat=seat, wake=wake, pinned=pinned, live={}, fitted={}, shrunk={}, warnings=[])
    if isinstance(q.get("crypto_live"), dict) and (q["crypto_live"].get("rows") or {}) and "kraken_24h" in q:   # live Ticker rows supersede the 4-hourly sweep
        q.pop("kraken_24h", None); q["kraken_24h_note"] = "superseded by crypto_live (Kraken Ticker fetched this wake; the 4-hourly sweep is only its fallback)"
    critical_over=any(k in q and len(_js(q[k]))>budget(k) for k in ("engine_wake","candidates_ranked","seat_scan"))
    if seat in TIERED or critical_over:   # Shared priority path protects primary candidates for every trader.
        try:
            if "engine_wake" in q: q["engine_wake"] = slim_engine_wake(q["engine_wake"])
            if isinstance(q.get("candidates_ranked"), list): q["candidates_ranked"] = [lean(r) for r in q["candidates_ranked"]]
            rep["tag"] = "context_priority_20261105"; fill_tiers(q, pinned, rep, rank)   # pinrank_20261103: + engine rank for the tier-1 trim order
            _log(rep)
            return q
        except Exception as x:
            rep["warnings"].append("tier fill failed (%s); per-key whole-object fit used" % type(x).__name__); q.pop("_trimmed", None)
    for k, v in list(q.items()):
        if k in SKIP or k in LIVE: continue
        try:
            b = budget(k); n0 = len(_js(v))
            if k == "engine_wake": v = slim_engine_wake(v)
            if k == "candidates_ranked" and isinstance(v, list): v = [lean(r) for r in v]
            if len(_js(v)) > b: v = fit(v, b, prefer); rep["fitted"][k] = [n0, len(_js(v))]
            q[k] = v
        except Exception:
            pass
    over = 0
    for k, sub in LIVE.items():
        if k not in q: continue
        try:
            b = budget(k); v, r = fit_live(q[k], sub, b, pinned, rank)
            if r is None:   # no row map (e.g. an error-only crypto_live block): the plain whole-item fit as before
                if k not in SKIP and len(_js(q[k])) > b: n0 = len(_js(q[k])); q[k] = fit(q[k], b, prefer); rep["fitted"][k] = [n0, len(_js(q[k]))]
                continue
            q[k] = v; rep["live"][k] = r; over += r["overflow"]
        except Exception as x:
            rep["warnings"].append("%s fit failed: %s" % (k, type(x).__name__))
    if over > 0:
        for k in LOW_PRI:
            if over <= 0: break
            if k not in q: continue
            try:
                cur = len(_js(q[k]))
                if cur <= FLOOR: continue
                v = fit(q[k], max(FLOOR, cur - over), prefer); new = len(_js(v))
                if new < cur: q[k] = v; rep["shrunk"][k] = [cur, new]; over -= cur - new
            except Exception: pass
        if over > 0:
            rep["warnings"].append("pinned live rows alone overflow the budget by %d chars after shrinking low-priority sections; pinned rows kept" % over)
    _log(rep)
    return q
