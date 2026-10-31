"""Story quality on top of the catalyst stream. Paper, advisory, fail open.

Clusters the same story across wires, scores how unexpected it is, and records
which source sat behind a paper entry. Nothing here arms, sizes, stops, or
vetoes. A missing file, a dead vendor, or a failed translation leaves the
stream and the book on their existing path. Keys, when a translator is
configured, are read at call time from ~/.tfo_secrets and are not logged.
This module does not write a ledger.
"""
import datetime as dt, hashlib, json, os, re, urllib.request
UTC = dt.timezone.utc
N_HASH = 64
SHINGLE_K = 5
NEWS_WINDOW_S = 6 * 3600
FILING_WINDOW_S = 18 * 3600
NOVELTY_DAYS = 30
ATTR_LEAD_S = 36 * 3600
ATTR_LAG_S = 15 * 60
MIN_N = 8
MIN_DAYS = 30
WEAK_HIT = 0.40
R_CAP = 15
FILING_KINDS = {"8k", "form4", "asx", "rns", "hkex", "tdnet"}
GENERIC = {"news", "press_release"}
SCHEDULE_SOURCES = {"fmp_earnings", "eodhd_earnings", "calendar_latest", "eia_wpsr", "eia_rule", "fmp_economic", "ff_calendar"}
J_TITLE = 0.80
J_EVENT = 0.30
J_EVENT_LOOSE = 0.58
J_TICK = 0.78
STOPWORDS = {"the", "a", "an", "of", "and", "for", "inc", "corp", "ltd", "co", "plc", "limited", "holdings", "group", "sa", "nv"}
PRIORS = {"trading_halt": 94, "fda": 90, "guidance_cut": 86, "ma": 82, "index_add": 78, "capital_raise": 76,
          "guidance_raise": 64, "earnings": 48, "dividend": 28, "buyback": 36, "8k": 62, "form4": 24, "flow": 62,
          "cb_decision": 42, "cb_speaker": 22, "eia": 30, "pdufa": 40, "asx": 34, "press_release": 30, "news": 18,
          "hkex": 36, "tdnet": 36, "rns": 36, "economic": 28}
# Routing table for Asia disclosures. Not a trade universe and not a signal.
ASIA_SECTOR = {"6758.T": "tech_ai_infra", "9984.T": "tech_ai_infra", "6501.T": "tech_ai_infra", "6861.T": "tech_ai_infra",
               "8035.T": "tech_ai_infra", "6920.T": "tech_ai_infra", "8306.T": "banks", "8316.T": "banks", "8411.T": "banks",
               "9432.T": "tech_ai_infra", "9433.T": "tech_ai_infra", "9983.T": "essentials_utilities", "4502.T": "healthcare",
               "4519.T": "healthcare", "4568.T": "healthcare", "700.HK": "tech_ai_infra", "9988.HK": "tech_ai_infra",
               "3690.HK": "tech_ai_infra", "9618.HK": "tech_ai_infra", "1810.HK": "tech_ai_infra", "9888.HK": "tech_ai_infra",
               "941.HK": "tech_ai_infra", "5.HK": "banks", "1299.HK": "banks", "939.HK": "banks", "1398.HK": "banks",
               "3988.HK": "banks", "883.HK": "energy", "857.HK": "energy", "386.HK": "energy", "1211.HK": "tech_ai_infra",
               "1177.HK": "healthcare", "2269.HK": "healthcare", "1093.HK": "healthcare"}
SECTOR_SEATS = {"energy": "energy", "financials": "banks", "financial services": "banks", "health care": "healthcare",
                "healthcare": "healthcare", "information technology": "tech_ai_infra", "technology": "tech_ai_infra",
                "communication services": "tech_ai_infra", "consumer staples": "essentials_utilities", "utilities": "essentials_utilities"}
MACRO_RE = re.compile(r"\b(boj|hkma|nikkei|hang seng|topix|monetary policy|policy rate|index (add|addition|inclusion|rebalanc))\b|日銀|金融政策|恒生指数|日経", re.I)
SURPRISE_WORD = re.compile(r"\b(surprise|unexpected|emergency|unscheduled)\b", re.I)
INLINE_WORD = re.compile(r"\b(in line|as expected|reaffirms|reaffirm|unchanged guidance)\b", re.I)
HALT_RE = re.compile(r"\b(trading halt|halted|bankruptcy|chapter 11|chapter 7|default)\b|売買停止|取引停止|停牌", re.I)
FDA_RE = re.compile(r"\bfda\b.{0,40}\b(approv|reject|refus)|pdufa", re.I)
GUIDE_CUT_RE = re.compile(r"\b(guidance cut|cuts guidance|cut its guidance|lowers guidance|profit warning|downward revision)\b|下方修正|盈利警告|盈警", re.I)
GUIDE_UP_RE = re.compile(r"\b(raises guidance|guidance raise|upward revision|lifts guidance)\b|上方修正", re.I)
MA_RE = re.compile(r"\b(acquir|merger|takeover|buyout|tender offer)\b|公開買付|合併|株式交換|關連交易|关连交易", re.I)
INDEX_RE = re.compile(r"\b(index (add|addition|inclusion)|added to the (s&p|nasdaq|nikkei|hang seng)|reconstitution)\b", re.I)
RAISE_RE = re.compile(r"\b(capital raise|share offering|stock offering|secondary offering|dilution|placing|rights issue)\b|第三者割当|資金調達|新株発行|配售|供股", re.I)
EARN_RE = re.compile(r"\b(earnings|results|eps|quarterly (profit|loss))\b|決算", re.I)
DIV_RE = re.compile(r"\b(dividend)\b|配当|股息", re.I)
BUY_RE = re.compile(r"\b(buyback|repurchase)\b|自己株式の取得|自社株買い|股份購回|回购", re.I)
CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
# Common filing phrases. A title that is still mostly CJK after this is left as-is.
GLOSSARY = (("業績予想の下方修正", "guidance cut"), ("配当予想の下方修正", "dividend guidance cut"),
            ("業績予想の修正", "guidance revision"), ("上方修正", "guidance raise"), ("下方修正", "guidance cut"),
            ("決算短信", "earnings flash"), ("決算", "earnings"), ("中間配当", "interim dividend"), ("期末配当", "year-end dividend"),
            ("配当", "dividend"), ("自己株式の取得", "share buyback"), ("自社株買い", "share buyback"),
            ("公開買付け", "tender offer"), ("公開買付", "tender offer"), ("株式交換", "share exchange"), ("合併", "merger"),
            ("第三者割当", "third-party allotment"), ("新株発行", "share issue"), ("資金調達", "capital raise"),
            ("株式分割", "stock split"), ("売買停止", "trading halt"), ("取引停止", "trading halt"),
            ("お知らせ", "notice"), ("に関する", "regarding"), ("株式会社", ""),
            ("盈利警告", "profit warning"), ("盈警", "profit warning"), ("停牌", "trading halt"),
            ("須予披露交易", "discloseable transaction"), ("须予披露交易", "discloseable transaction"),
            ("關連交易", "connected transaction"), ("关连交易", "connected transaction"),
            ("股份購回", "share buyback"), ("股份回购", "share buyback"), ("回购", "buyback"),
            ("內幕消息", "inside information"), ("内幕消息", "inside information"), ("股息", "dividend"),
            ("配售", "placing"), ("供股", "rights issue"), ("全年業績", "annual results"), ("中期業績", "interim results"))
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _root():
    try:
        from veteran_desk import catalyst_stream as CS
        return CS.base()
    except Exception:
        return os.path.join(BASE, "state", "catalyst_stream")
def _parse(s):
    try:
        from veteran_desk import catalyst_stream as CS
        return CS.parse_time(s)
    except Exception:
        return None
def _now():
    return dt.datetime.now(UTC)
def norm_ticker(t):
    u = str(t or "").upper().strip()
    try:
        from veteran_desk import catalyst_stream as CS
        u = CS.ALIAS.get(u, u)
    except Exception:
        pass
    m = re.fullmatch(r"0*(\d{1,5})\.HK", u)
    if m: return str(int(m.group(1))) + ".HK"
    return u
def _norm_text(title):
    t = re.sub(r"[^a-z0-9]+", " ", (title or "").lower())
    return re.sub(r"\s+", " ", t).strip()
def shingles(title, k=SHINGLE_K):
    """Character k-grams of the normalised headline. The MinHash signature is taken over this set."""
    t = _norm_text(title).replace(" ", "")
    if not t: return frozenset()
    if len(t) <= k: return frozenset([t])
    return frozenset(t[i:i + k] for i in range(0, len(t) - k + 1))
def signature(title, n=N_HASH):
    """Deterministic MinHash of the character shingles. Equal inputs always yield the same signature."""
    sh = shingles(title)
    if not sh: return (0,) * n
    sig = []
    for i in range(n):
        seed = (0x9E3779B1 ^ (i * 0x85EBCA6B)) & 0xFFFFFFFF
        best = None
        for s in sh:
            h = seed
            for ch in s:
                h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
            if best is None or h < best: best = h
        sig.append(best or 0)
    return tuple(sig)
def minhash_jaccard(a, b):
    if not a or not b or len(a) != len(b): return 0.0
    if all(x == 0 for x in a) or all(x == 0 for x in b): return 0.0
    return sum(x == y for x, y in zip(a, b)) / float(len(a))
def word_jaccard(a, b):
    def toks(s):
        return {w for w in _norm_text(s).split() if w not in STOPWORDS and (len(w) > 1 or w.isdigit())}
    ta, tb = toks(a), toks(b)
    if not ta or not tb: return 0.0
    return len(ta & tb) / float(len(ta | tb))
def title_similarity(a, b, sig_a=None, sig_b=None):
    """MinHash estimate of the shingle Jaccard, or the word overlap when that is higher."""
    sa = sig_a if sig_a is not None else signature(a)
    sb = sig_b if sig_b is not None else signature(b)
    return max(minhash_jaccard(sa, sb), word_jaccard(a, b))
def distinct_series(a, b):
    """'XOM item 0' and 'XOM item 1' are different headlines, not the same story."""
    wa, wb = _norm_text(a).split(), _norm_text(b).split()
    return bool(wa) and len(wa) == len(wb) and wa[:-1] == wb[:-1] and wa[-1] != wb[-1] and wa[-1].isdigit() and wb[-1].isdigit()
def cjk_ratio(s):
    chars = [c for c in (s or "") if not c.isspace()]
    if not chars: return 0.0
    return sum(1 for c in chars if CJK_RE.match(c)) / float(len(chars))
def glossary(title):
    t = title or ""
    for src, dst in GLOSSARY:
        if src in t: t = t.replace(src, (" " + dst + " ") if dst else " ")
    return re.sub(r"\s+", " ", t).strip()
def english_title(title, title_en=None, translate=None):
    """English when the wire has it, a glossary or translator otherwise. Fail open: keep the original."""
    raw = str(title or "").strip()
    en = str(title_en or "").strip()
    if en and cjk_ratio(en) < 0.25: return en[:220], "en"
    if raw and cjk_ratio(raw) < 0.15: return raw[:220], "en"
    gloss = glossary(raw)
    if gloss and cjk_ratio(gloss) < 0.25 and re.search(r"[A-Za-z]", gloss): return gloss[:220], "glossary"
    tr = None
    if translate is not None:
        try: tr = translate(raw)
        except Exception: tr = None
    if tr and cjk_ratio(str(tr)) < 0.25 and re.search(r"[A-Za-z]", str(tr)): return str(tr).strip()[:220], "translated"
    if gloss and gloss != raw and re.search(r"[A-Za-z]", gloss): return gloss[:220], "glossary_partial"
    return raw[:220], "untranslated"
def remote_translate(text, key_fn=None):
    """Optional LibreTranslate-compatible POST. No URL configured, or any error: return None. The key is not logged."""
    url = os.environ.get("TFO_TRANSLATE_URL")
    if not url or not text: return None
    key = None
    try:
        if key_fn: key = key_fn("translate.env", "TRANSLATE_API_KEY")
    except Exception: key = None
    body = json.dumps({"q": str(text)[:500], "source": "auto", "target": "en", "format": "text"}).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "TFO paper research"}
    if key: headers["Authorization"] = "Bearer " + key
    try:
        req = urllib.request.Request(url, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=3) as r:
            js = json.loads(r.read().decode("utf-8", "replace"))
        out = js.get("translatedText") if isinstance(js, dict) else None
        return str(out).strip()[:220] if out else None
    except Exception:
        return None
def event_key(item):
    blob = " ".join(str(item.get(k) or "") for k in ("title", "title_raw"))
    items = set(item.get("items") or [])
    if HALT_RE.search(blob) or "1.03" in items: return "trading_halt"
    if FDA_RE.search(blob): return "fda"
    if GUIDE_CUT_RE.search(blob): return "guidance_cut"
    if GUIDE_UP_RE.search(blob): return "guidance_raise"
    if "2.01" in items or "1.01" in items or MA_RE.search(blob): return "ma"
    if INDEX_RE.search(blob): return "index_add"
    if RAISE_RE.search(blob): return "capital_raise"
    if "2.02" in items or "4.02" in items or EARN_RE.search(blob): return "earnings"
    if DIV_RE.search(blob): return "dividend"
    if BUY_RE.search(blob): return "buyback"
    kind = item.get("kind") or "news"
    if kind in GENERIC: return "news"
    return kind
def is_schedule(item):
    if any(item.get(k) is not None for k in ("eps", "revenue", "actual")): return False
    blob = item.get("title") or ""
    if SURPRISE_WORD.search(blob): return False
    src = str(item.get("source") or "")
    if src in SCHEDULE_SOURCES or src.startswith("cb_"): return True
    if item.get("kind") in ("pdufa", "eia"): return True
    return False
def _when(item):
    return _parse(item.get("source_utc") or item.get("first_source_utc") or item.get("fetched_utc"))
def within_window(a, b):
    ta, tb = _when(a), _when(b)
    if ta is None or tb is None: return False
    filing = (a.get("kind") in FILING_KINDS) or (b.get("kind") in FILING_KINDS)
    return abs((ta - tb).total_seconds()) <= (FILING_WINDOW_S if filing else NEWS_WINDOW_S)
def _ticks(item):
    return {norm_ticker(t) for t in (item.get("tickers") or []) if norm_ticker(t)}
def same_story(a, b):
    """Same story: time window, and ticker+event or a close MinHash/shingle match. Schedules do not swallow a print."""
    if distinct_series(a.get("title") or "", b.get("title") or ""): return False
    if bool(a.get("schedule")) != bool(b.get("schedule")): return False
    if not within_window(a, b): return False
    j = title_similarity(a.get("title") or "", b.get("title") or "", a.get("sig"), b.get("sig"))
    if j >= J_TITLE: return True
    ticks = _ticks(a) & _ticks(b)
    ea, eb = a.get("event") or "news", b.get("event") or "news"
    if ea not in GENERIC and eb not in GENERIC and ea != eb: return False
    if ea == eb and ea not in GENERIC:
        if ticks and j >= J_EVENT: return True
        if j >= J_EVENT_LOOSE: return True
    if ticks and j >= J_TICK: return True
    return False
def _source_row(item):
    return {"source": item.get("source"), "source_utc": item.get("source_utc"), "title": (item.get("title_raw") or item.get("title") or "")[:220],
            "link": (item.get("link") or "")[:300], "tier": item.get("tier")}
def _snap(item):
    return {k: v for k, v in item.items() if k != "sig"}
def new_cluster(item):
    t = item.get("source_utc") or item.get("fetched_utc")
    cid = hashlib.sha1(("%s|%s|%s" % (item.get("source"), item.get("title_raw") or item.get("title"), t)).encode()).hexdigest()[:20]
    return {"id": cid, "event": item.get("event") or "news", "kind": item.get("kind"), "title": item.get("title"),
            "tickers": sorted(_ticks(item)), "first_source": item.get("source"), "first_source_utc": t,
            "sources": [_source_row(item)], "seats": list(item.get("seats") or []), "surprise": item.get("surprise"),
            "urgency": item.get("urgency") or 0, "schedule": bool(item.get("schedule")), "woke": False, "emitted": False,
            "sig": item.get("sig"), "item": _snap(item), "updated": t}
def _earlier(a, b):
    ta, tb = _parse(a), _parse(b)
    if ta and tb: return a if ta <= tb else b
    return a or b
def merge_cluster(cluster, item):
    row = _source_row(item)
    have = {(s.get("source"), s.get("title")) for s in cluster.get("sources") or []}
    if (row.get("source"), row.get("title")) not in have:
        cluster.setdefault("sources", []).append(row)
    if _earlier(item.get("source_utc"), cluster.get("first_source_utc")) == item.get("source_utc") and item.get("source_utc"):
        cluster["first_source_utc"] = item.get("source_utc")
        cluster["first_source"] = item.get("source")
    ticks = set(cluster.get("tickers") or []) | _ticks(item)
    cluster["tickers"] = sorted(ticks)
    seats = set(cluster.get("seats") or []) | set(item.get("seats") or [])
    cluster["seats"] = sorted(seats)
    try:
        if item.get("surprise") is not None and (cluster.get("surprise") is None or float(item["surprise"]) > float(cluster.get("surprise") or 0)):
            cluster["surprise"] = item.get("surprise")
            if item.get("title"): cluster["title"] = item.get("title")
    except Exception: pass
    try:
        if int(item.get("urgency") or 0) > int(cluster.get("urgency") or 0): cluster["urgency"] = int(item.get("urgency") or 0)
    except Exception: pass
    cluster["updated"] = item.get("fetched_utc") or cluster.get("updated")
    cluster["_dup_this_cycle"] = True
    return cluster
def _as_item(cluster):
    base = dict(cluster.get("item") or {})
    base["title"] = cluster.get("title") or base.get("title")
    base["event"] = cluster.get("event")
    base["tickers"] = cluster.get("tickers") or base.get("tickers") or []
    base["source_utc"] = cluster.get("first_source_utc")
    base["first_source_utc"] = cluster.get("first_source_utc")
    base["kind"] = cluster.get("kind") or base.get("kind")
    base["schedule"] = cluster.get("schedule")
    base["sig"] = cluster.get("sig")
    base["seats"] = cluster.get("seats") or []
    return base
def find_cluster(item, clusters):
    for c in clusters.values():
        if same_story(item, _as_item(c)): return c
    return None
def cluster_batch(items, clusters):
    """Admit stamped items. Earliest source time wins the canonical clock. Returns (fresh, dup_count)."""
    fresh, dup = [], 0
    ordered = sorted(items, key=lambda it: str(it.get("source_utc") or it.get("fetched_utc") or ""))
    for item in ordered:
        if not item.get("sig"): item["sig"] = signature(item.get("title") or "")
        hit = find_cluster(item, clusters)
        if hit:
            merge_cluster(hit, item)
            dup += 1
        else:
            c = new_cluster(item)
            clusters[c["id"]] = c
            fresh.append(c)
    return fresh, dup
def snapshot(cluster):
    base = dict(cluster.get("item") or {})
    base["id"] = cluster["id"]
    base["cluster_id"] = cluster["id"]
    base["title"] = cluster.get("title") or base.get("title")
    base["event"] = cluster.get("event")
    base["tickers"] = list(cluster.get("tickers") or base.get("tickers") or [])
    base["seats"] = list(cluster.get("seats") or base.get("seats") or [])
    base["sources"] = list(cluster.get("sources") or [])
    base["n_sources"] = len(base["sources"])
    base["first_source"] = cluster.get("first_source")
    base["first_source_utc"] = cluster.get("first_source_utc")
    base["source_utc"] = cluster.get("first_source_utc") or base.get("source_utc")
    base["surprise"] = cluster.get("surprise")
    base["urgency"] = cluster.get("urgency")
    base["schedule"] = bool(cluster.get("schedule"))
    base["advisory_only"] = True
    base["blocks_trade"] = False
    base["blocks_feed"] = False
    base.pop("sig", None)
    return base
def prune_clusters(clusters, now):
    cut = now - dt.timedelta(hours=72)
    kept = {}
    for i, c in (clusters or {}).items():
        t = _parse(c.get("updated") or c.get("first_source_utc"))
        if t is None or t >= cut: kept[i] = c
    if len(kept) > 800:
        ranked = sorted(kept.items(), key=lambda kv: str(kv[1].get("updated") or ""), reverse=True)[:800]
        kept = dict(ranked)
    for c in kept.values():
        c.pop("_dup_this_cycle", None)
        c.pop("sig", None)
    return kept
def load_clusters(root=None):
    path = os.path.join(root or _root(), "clusters.json")
    try: raw = json.load(open(path))
    except Exception: raw = {}
    if not isinstance(raw, dict): return {}
    out = {}
    for i, c in raw.items():
        if isinstance(c, dict) and c.get("id"):
            c["sig"] = signature(c.get("title") or "")
            out[c["id"]] = c
    return out
def event_prior(item):
    ev = item.get("event") or item.get("kind") or "news"
    p = PRIORS.get(ev, PRIORS.get(item.get("kind") or "", 20))
    if item.get("schedule") and ev in ("earnings", "cb_decision", "cb_speaker", "eia", "pdufa", "economic"): p = min(p, 24)
    if item.get("price_sensitive"): p = min(100, p + 18)
    try:
        if ev == "flow" and float(item.get("premium") or 0) >= 250000: p = max(p, 74)
    except Exception: pass
    blob = item.get("title") or ""
    if SURPRISE_WORD.search(blob): p = min(96, p + 18)
    if INLINE_WORD.search(blob): p = min(p, 22)
    if set(item.get("items") or []) & {"1.01", "1.03", "2.01", "2.02", "4.02", "5.02"}: p = max(p, 70)
    return int(max(0, min(100, p)))
def _pct_score(mag):
    mag = abs(float(mag))
    if mag < 0.01: return 12
    if mag < 0.03: return 35
    if mag < 0.06: return 58
    if mag < 0.12: return 78
    return min(100, int(80 + mag * 80))
def earnings_component(item):
    """None when the wire did not bring a consensus and an actual. In-line prints score low."""
    pairs = []
    for a, e in (("eps", "eps_est"), ("revenue", "revenue_est"), ("actual", "estimate"), ("guidance", "guidance_prior")):
        if item.get(a) is None or item.get(e) is None: continue
        try: av, ev = float(item.get(a)), float(item.get(e))
        except Exception: continue
        if ev == 0: pairs.append(1.0 if av != 0 else 0.0)
        else: pairs.append((av - ev) / abs(ev))
    if not pairs: return None
    return _pct_score(max(abs(x) for x in pairs))
def novelty_score(item, history, now, cache):
    ticks = list(item.get("tickers") or []) or ["_macro"]
    cut = (now or _now()) - dt.timedelta(days=NOVELTY_DAYS)
    sig = item.get("sig") or signature(item.get("title") or "")
    sim, seen = 0.0, False
    for t in ticks:
        for row in (history or {}).get(norm_ticker(t) if t != "_macro" else "_macro", []) or []:
            ts = _parse(row.get("t"))
            if ts is not None and (ts < cut or (now and ts > now + dt.timedelta(minutes=1))): continue
            title = row.get("title") or ""
            if not title: continue
            seen = True
            if title not in cache: cache[title] = signature(title)
            sim = max(sim, title_similarity(item.get("title") or "", title, sig, cache[title]))
    if not seen: return 55
    return int(round(100 * (1.0 - sim)))
def reaction_score(bars):
    """Abnormal first-minutes move or volume. None when the tape is too short to judge."""
    rows = [b for b in (bars or []) if isinstance(b, dict) and b.get("c") is not None and b.get("o") is not None]
    if len(rows) < 3: return None
    try:
        first, last = float(rows[0]["o"]), float(rows[-1]["c"])
        ret = (last - first) / first if first else 0.0
    except Exception: return None
    vols = []
    for b in rows:
        try: vols.append(float(b.get("v") or 0))
        except Exception: vols.append(0.0)
    ratio = None
    if len(vols) >= 6 and sum(vols[:-3]) > 0:
        ratio = (sum(vols[-3:]) / 3.0) / (sum(vols[:-3]) / float(len(vols) - 3))
    mag = abs(ret)
    if mag >= 0.03 or (ratio is not None and ratio >= 5): return 92
    if mag >= 0.015 or (ratio is not None and ratio >= 3): return 74
    if mag >= 0.007 or (ratio is not None and ratio >= 2): return 55
    return 18
def _mix(parts, weights):
    present = {k: weights[k] for k in weights if parts.get(k) is not None}
    s = sum(present.values()) or 1.0
    return int(round(sum(float(parts[k]) * present[k] / s for k in present)))
def surprise(item, now, history, tape_fn=None, cache=None):
    """0-100 unexpectedness. Missing inputs are left out; they do not zero the score and they do not raise."""
    cache = cache if cache is not None else {}
    parts = {"prior": event_prior(item), "novelty": novelty_score(item, history, now, cache)}
    earn = None
    try: earn = earnings_component(item)
    except Exception: earn = None
    if earn is not None: parts["earnings"] = earn
    tape = None
    if tape_fn is not None:
        try: tape = tape_fn(item)
        except Exception: tape = None
    if isinstance(tape, (int, float)): parts["tape"] = max(0, min(100, int(tape)))
    if "earnings" in parts:
        score = _mix(parts, {"earnings": 0.50, "prior": 0.20, "novelty": 0.20, "tape": 0.10})
    else:
        score = _mix(parts, {"prior": 0.45, "novelty": 0.35, "tape": 0.20})
    return max(0, min(100, score)), parts
def remember(history, item, now):
    ticks = list(item.get("tickers") or []) or ["_macro"]
    stamp = item.get("source_utc") or (now.isoformat() if now else None)
    cut = (now or _now()) - dt.timedelta(days=NOVELTY_DAYS)
    for t in ticks:
        key = "_macro" if t == "_macro" else norm_ticker(t)
        rows = [r for r in (history.get(key) or []) if (_parse(r.get("t")) or cut) >= cut]
        rows.append({"t": stamp, "title": (item.get("title") or "")[:180]})
        history[key] = rows[-40:]
    if len(history) > 500:
        for k in list(history)[: len(history) - 500]: history.pop(k, None)
    return history
def load_json(path, default):
    try: return json.load(open(path))
    except Exception: return default
def extra_seats(item):
    """Asia filings: Quinn, the Japan or Hong Kong hunt, the sector seat, and Theo when it is macro."""
    if (item.get("kind") or "") not in ("hkex", "tdnet"): return set()
    seats = set()
    for t in item.get("tickers") or []:
        nt = norm_ticker(t)
        if nt.endswith(".T"): seats.add("asia_hunt_jp")
        elif nt.endswith(".HK"): seats.add("asia_hunt_hk")
        sec = ASIA_SECTOR.get(nt)
        if sec: seats.add(sec)
    named = SECTOR_SEATS.get(str(item.get("sector") or "").strip().lower())
    if named: seats.add(named)
    blob = " ".join(str(item.get(k) or "") for k in ("title", "title_raw"))
    if item.get("event") == "index_add" or MACRO_RE.search(blob): seats.add("macro")
    if "banks" in seats or "energy" in seats: seats.add("macro")
    return seats
def hunt_advisory(mkt):
    """Read-only context for an Asia hunt status file. Does not arm, size, or veto."""
    seat = {"jp": "asia_hunt_jp", "hk": "asia_hunt_hk"}.get(mkt)
    note = "Context only. Does not arm, size, stop, or veto."
    if not seat:
        return dict(advisory_only=True, blocks_trade=False, items=[], note=note, seat=None)
    try:
        from veteran_desk import catalyst_stream as CS
        rows = CS.for_seat(seat, 8) or {}
        items = rows.get("items") or []
    except Exception:
        items = []
    return dict(advisory_only=True, blocks_trade=False, blocks_entry=False, note=note, seat=seat, items=items[:8])
class Tape:
    """At most a few Massive minute reads per cycle, cached. Any failure returns None."""
    def __init__(self, get, key_fn, now, root, limit=3):
        self.get, self.key_fn, self.now, self.root, self.limit = get, key_fn, now, root, limit
        self.used, self.mem = 0, {}
    def _key(self):
        try: return self.key_fn("massive.env", "MASSIVE_API_KEY") if self.key_fn else None
        except Exception: return None
    def score(self, item):
        try:
            if not self._fresh(item): return None
            ticks = [norm_ticker(t) for t in (item.get("tickers") or [])]
            ticks = [t for t in ticks if t and "." not in t and "=" not in t and "^" not in t and ":" not in t]
            if not ticks: return None
            t = ticks[0]
            if t in self.mem: return self.mem[t]
            if self.used >= self.limit: return None
            key = self._key()
            if not key: return None
            self.used += 1
            self.mem[t] = self._fetch(t, key)
            return self.mem[t]
        except Exception:
            return None
    def _fresh(self, item):
        src = _parse(item.get("source_utc"))
        if src is None or self.now is None: return False
        age = (self.now - src).total_seconds()
        return 0 <= age <= 25 * 60
    def _fetch(self, ticker, key):
        day = self.now.date()
        start = (day - dt.timedelta(days=1)).isoformat()
        path = "/v2/aggs/ticker/%s/range/1/minute/%s/%s" % (ticker, start, day.isoformat())
        url = "https://api.massive.com" + path + "?adjusted=true&sort=desc&limit=30"
        try:
            status, body = self.get(url, {"Authorization": "Bearer " + key, "Accept": "application/json"}, 3)
        except Exception:
            return None
        if int(status or 0) != 200: return None
        try: js = json.loads(body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else body)
        except Exception: return None
        rows = list(reversed((js or {}).get("results") or [])) if isinstance(js, dict) else []
        return reaction_score(rows)
def _num(v):
    try:
        if v is None or v == "": return None
        return float(v)
    except Exception: return None
def r_multiple(trade):
    risk, net = trade.get("risk_aud"), trade.get("net_aud")
    if isinstance(risk, (int, float)) and risk and isinstance(net, (int, float)):
        r = float(net) / float(risk)
        return None if abs(r) > R_CAP else r
    try:
        entry, stop, exit_ = float(trade["entry"]), float(trade["stop"]), float(trade.get("exit"))
        span = abs(entry - stop)
        if span <= 0: return None
        sg = 1.0 if str(trade.get("side") or "LONG").upper() == "LONG" else -1.0
        r = sg * (exit_ - entry) / span
        return None if abs(r) > R_CAP else r
    except Exception:
        return None
def _median(xs):
    if not xs: return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0
def _bucket(trades):
    rs, leads, wins = [], [], 0
    for r, lead in trades:
        if r is None: continue
        rs.append(r)
        if r > 0: wins += 1
        if lead is not None: leads.append(lead)
    n = len(rs)
    if not n: return dict(n=0, hit_rate=None, avg_r=None, median_lead_s=None)
    return dict(n=n, hit_rate=round(wins / float(n), 3), avg_r=round(sum(rs) / float(n), 3),
                median_lead_s=int(round(_median(leads))) if leads else None)
def attribute(ticker, when, seat=None, root=None):
    """Catalyst items and sources behind one paper entry. Read only. None on any failure, which the book ignores."""
    try:
        clusters = load_clusters(root)
        nt = norm_ticker(ticker)
        if not nt: return None
        stamp = when if isinstance(when, dt.datetime) else _parse(when)
        hits = []
        for c in clusters.values():
            ticks = [norm_ticker(x) for x in (c.get("tickers") or [])]
            if nt not in ticks: continue
            ft = _parse(c.get("first_source_utc"))
            if stamp and ft:
                lead = (stamp - ft).total_seconds()
                if lead < -ATTR_LAG_S or lead > ATTR_LEAD_S: continue
            hits.append(c)
        if not hits: return None
        hits.sort(key=lambda c: -(float(c.get("surprise") or 0)))
        items, sources, seen = [], [], set()
        for c in hits[:3]:
            items.append(dict(id=c.get("id"), event=c.get("event"), title=(c.get("title") or "")[:180],
                              first_source=c.get("first_source"), first_source_utc=c.get("first_source_utc"),
                              surprise=c.get("surprise"), seats=c.get("seats") or []))
            for s in c.get("sources") or []:
                name = s.get("source")
                if name and name not in seen:
                    seen.add(name)
                    sources.append(dict(source=name, source_utc=s.get("source_utc"), tier=s.get("tier")))
        return dict(advisory_only=True, blocks_trade=False, cluster_ids=[c.get("id") for c in hits[:3]], items=items, sources=sources, seat=seat)
    except Exception:
        return None
def _lead(trade, first_utc):
    entry = _parse(trade.get("filled_utc") or trade.get("armed_utc") or trade.get("entry_utc"))
    src = _parse(first_utc)
    if not entry or not src: return None
    return int(round((entry - src).total_seconds()))
def _tagged(trade):
    tags = trade.get("tags") or {}
    cat = tags.get("catalyst") or trade.get("catalyst") or {}
    return cat if isinstance(cat, dict) else {}
def score_sources(trades, now=None, days=None):
    """Per source and per event: later R, hit rate, lead time. Proposals only, and only after 30 observed days."""
    now = now or _now()
    by_s, by_e = {}, {}
    used = 0
    for tr in trades or []:
        if not isinstance(tr, dict): continue
        r = r_multiple(tr)
        if r is None: continue
        cat = _tagged(tr)
        items = cat.get("items") or []
        if not items: continue
        used += 1
        srcs, events = set(), set()
        lead = None
        for it in items:
            if not isinstance(it, dict): continue
            if it.get("event"): events.add(it["event"])
            if lead is None: lead = _lead(tr, it.get("first_source_utc"))
            for s in (it.get("sources") or cat.get("sources") or []):
                name = s.get("source") if isinstance(s, dict) else s
                if name: srcs.add(name)
            if it.get("first_source"): srcs.add(it.get("first_source"))
        for name in srcs: by_s.setdefault(name, []).append((r, lead))
        for ev in events: by_e.setdefault(ev, []).append((r, lead))
    sources = {k: _bucket(v) for k, v in sorted(by_s.items())}
    events = {k: _bucket(v) for k, v in sorted(by_e.items())}
    observed = []
    for d in days or []:
        try: observed.append(str(d)[:10])
        except Exception: pass
    observed = sorted(set(observed))
    proposals = []
    if len(observed) >= MIN_DAYS:
        for kind, table in (("source", sources), ("event", events)):
            for name, row in table.items():
                if row["n"] < MIN_N: continue
                weak = (row["hit_rate"] is not None and row["hit_rate"] < WEAK_HIT) or (row["avg_r"] is not None and row["avg_r"] < 0)
                if not weak: continue
                proposals.append(dict(action="down_weight", target=kind, name=name, n=row["n"], hit_rate=row["hit_rate"], avg_r=row["avg_r"],
                                      auto_apply=False, apply=False,
                                      reason="%s %s: hit %s, avg R %s over %d attributed closes. Proposal only." % (kind, name, row["hit_rate"], row["avg_r"], row["n"])))
    return dict(advisory_only=True, blocks_trade=False, blocks_feed=False, auto_apply=False, paper_only=True,
                generated_utc=now.isoformat(), days_observed=len(observed), days=observed[-40:],
                attributed_closes=used, sources=sources, events=events, proposals=proposals,
                note="Scores are advisory. A down-weight is a proposal and is never applied. Stops, size limits and exposure caps are unchanged.")
def warroom_card(scores=None, root=None):
    if scores is None:
        scores = load_json(os.path.join(root or _root(), "source_scores.json"), {})
    if not isinstance(scores, dict): scores = {}
    rows = []
    for name, row in (scores.get("sources") or {}).items():
        if not isinstance(row, dict): continue
        lead = row.get("median_lead_s")
        rows.append(dict(source=name, n=row.get("n") or 0, hit_rate=row.get("hit_rate"), avg_r=row.get("avg_r"),
                         median_lead_s=lead, median_lead_min=(round(lead / 60.0, 1) if isinstance(lead, (int, float)) else None)))
    rows.sort(key=lambda r: (-(r["n"] or 0), r["source"]))
    props = []
    for p in scores.get("proposals") or []:
        if isinstance(p, dict): props.append(dict(p, auto_apply=False, apply=False))
    return dict(title="Catalyst sources", paper_only=True, advisory_only=True, blocks_trade=False, auto_apply=False,
                days_observed=scores.get("days_observed") or 0, rows=rows[:8], proposals=props,
                note="Hit rate, average R and lead time by source. Down-weights are proposals only. They do not change urgency, size, stops, or entries.")
def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(obj, f, indent=1, default=str)
    os.replace(tmp, path)
def read_closed(book_path):
    """Read closed paper positions. Does not write the book."""
    try: D = json.load(open(book_path))
    except Exception: return []
    out = []
    for B in (D.get("seats") or {}).values() if isinstance(D, dict) else []:
        if not isinstance(B, dict): continue
        for c in B.get("closed") or []:
            if isinstance(c, dict): out.append(c)
    return out
def nightly(trades=None, now=None, root=None, book_path=None, days=None):
    """Write source_scores.json and the War Room card. Reads a book only when book_path is set. Never writes one."""
    now = now or _now()
    root = root or _root()
    if trades is None: trades = read_closed(book_path) if book_path else []
    day_path = os.path.join(root, "source_score_days.json")
    if days is None:
        prev = load_json(day_path, [])
        days = list(prev) if isinstance(prev, list) else []
        days.append(now.date().isoformat())
    scores = score_sources(trades, now, days)
    try: _write(os.path.join(root, "source_scores.json"), scores)
    except Exception: pass
    try: _write(day_path, scores.get("days") or [])
    except Exception: pass
    card = warroom_card(scores)
    try: _write(os.path.join(root, "warroom_card.json"), card)
    except Exception: pass
    return scores
def cache_load(path, now_ts, ttl):
    obj = load_json(path, {})
    if not isinstance(obj, dict): return None
    try: age = float(now_ts) - float(obj.get("fetched") or 0)
    except Exception: return None
    if obj.get("items") and 0 <= age < ttl: return obj
    return None
def cache_stale(path, now_ts, max_age):
    obj = load_json(path, {})
    if not isinstance(obj, dict) or not obj.get("items"): return None
    try: age = float(now_ts) - float(obj.get("fetched") or 0)
    except Exception: return None
    if 0 <= age < max_age: return obj
    return None
def cache_save(path, items, now_ts, gap):
    try: _write(path, {"fetched": now_ts, "next_fetch": float(now_ts) + float(gap), "items": items})
    except Exception: pass
