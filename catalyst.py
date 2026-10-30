"""2026-10-28 Brett 10:29 WITA: news CATALYST bonus for hunt scores. PAPER only.
Recent (<=72h), real headlines in the trade's direction add a CAPPED bonus (+0.5) to the hunt score:
  long : earnings beat, guidance raise, contract win, broker upgrade, buyback
  short: earnings miss, guidance cut/profit warning, downgrade, contract loss
Bonus only lifts a BORDERLINE setup (base score >= MIN_SCORE - CAP); it never creates one (base < that -> 0) and every
other gate still applies. Levels, stops and sizing are untouched. Mixed/conflicting news -> no bonus.
Feed: state/catalyst/<mkt>.json written by `catalyst.py scan` (yfinance news + desk ASX price-sensitive announcements) for top-ranked names.
"""
import datetime as dt, json, os, re, sys, time
BASE = os.path.dirname(os.path.abspath(__file__)); UTC = dt.timezone.utc
OUT = os.path.join(BASE, "state", "catalyst"); CAP = 0.5; FRESH_H = 72; FILE_MAX_AGE_H = 24; TOP_N = 40
POS = r"\bbeats?\b|tops (estimates|forecasts)|above (estimates|expectations)|raises? (its )?(guidance|outlook|forecast)|upgrades? (to|guidance)|\bupgraded\b|record (profit|earnings)|wins? .*(contract|order|deal)|awarded .*contract|secures? .*(contract|order)|buy-?back|share repurchase"
NEG = r"\bmiss(es|ed)?\b|below (estimates|expectations)|cuts? (its )?(guidance|outlook|forecast)|lowers? (guidance|outlook|forecast)|profit warning|\bdowngraded?\b|loses? .*contract|contract (terminated|cancelled)"

def classify(title):
    t = title or ""; p, n = bool(re.search(POS, t, re.I)), bool(re.search(NEG, t, re.I))
    return "pos" if p and not n else ("neg" if n and not p else None)

def summarise(items, now):
    """items [{title, when}] -> {pos:[..], neg:[..]} within FRESH_H."""
    out = {"pos": [], "neg": []}
    for it in items or []:
        try: w = dt.datetime.fromisoformat(str(it.get("when")).replace("Z", "+00:00"))
        except Exception: continue
        if w.tzinfo is None: w = w.replace(tzinfo=UTC)
        if not (0 <= (now - w).total_seconds() <= FRESH_H * 3600): continue
        c = classify(it.get("title"))
        if c: out[c].append(dict(title=str(it["title"])[:120], when=w.isoformat()))
    return out

def _load(mkt):
    try: j = json.load(open(os.path.join(OUT, f"{mkt}.json")))
    except Exception: return {}
    try:
        if (dt.datetime.now(UTC) - dt.datetime.fromisoformat(j["generated_utc"])).total_seconds() > FILE_MAX_AGE_H * 3600: return {}
    except Exception: return {}
    return j.get("names") or {}

def bonus(mkt, sym, direction, base_score, min_score, now=None, feed=None):
    """-> (bonus, info|None). Capped; borderline-only; aligned-only; conflicting news -> 0."""
    now = now or dt.datetime.now(UTC); f = (feed if feed is not None else _load(mkt)).get(sym)
    if not f: return 0.0, None
    s = summarise(f.get("items"), now); want, other = ("pos", "neg") if direction == "long" else ("neg", "pos")
    if not s[want] or s[other]: return 0.0, None
    info = dict(label=("positive" if want == "pos" else "negative") + " news aligned with " + direction, headlines=s[want][:3], cap=CAP)
    if base_score < min_score - CAP: return 0.0, dict(info, bonus=0.0, note="base score too low: catalyst cannot create a setup")
    return CAP, dict(info, bonus=CAP)

def _yf_news(sym):
    try:
        import yfinance as yf; out = []
        raw = yf.Ticker(sym).news or []
        if not raw:  # 2026-10-30 feed audit: Ticker.news returns [] (Yahoo endpoint change); fall back to Yahoo search news filtered to this ticker
            try: raw = [n for n in (yf.Search(sym, news_count=20, max_results=1).news or []) if sym in (n.get("relatedTickers") or [])]
            except Exception: raw = []
        for n in raw[:20]:
            c = n.get("content") or n; ts = c.get("pubDate") or c.get("providerPublishTime")
            out.append(dict(title=c.get("title") or "", when=ts if isinstance(ts, str) else (dt.datetime.fromtimestamp(ts, UTC).isoformat() if ts else None)))
        return out
    except Exception: return []

def scan(mkt):
    """top-N ranked names (+ ledger ARMED/BENCH) -> state/catalyst/<mkt>.json."""
    import csv
    p = os.path.join(BASE, "state", "calendar", "inputs", "ranked.csv" if mkt == "us" else f"ranked_{mkt}.csv")
    try: R = [r for r in csv.DictReader(open(p)) if (r.get("venue") == "ibkr" if mkt == "us" else r.get("cls", "").endswith("_equity"))]
    except Exception: R = []
    fl = lambda v: float(v) if v not in (None, "") else 0.0
    syms = [r["sym"] for r in sorted(R, key=lambda r: -max(fl(r.get("score")), fl(r.get("rank_score"))))[:TOP_N]]
    for d in (["hunt48_short", "hunt48_short_claude", "hunt48_short_gpt"] if mkt == "us" else [f"hunt48_{mkt}"]):
        try: syms += [t.get("sym") or t.get("ticker") for t in json.load(open(os.path.join(BASE, "state", d, "ledger.json")))["trades"] if t["status"] in ("ARMED", "BENCH", "awaiting_funder")]
        except Exception: pass
    ann = {}
    if mkt == "asx":
        try:
            for a in json.load(open(os.path.join(BASE, "state", "calendar", "inputs", "asx_events.json"))).get("announcements", []):
                if a.get("price_sensitive"): ann.setdefault(a["code"] + ".AX", []).append(dict(title=a.get("headline"), when=a.get("when_utc")))
        except Exception: pass
    names = {}
    for s in dict.fromkeys(syms):
        items = _yf_news(s) + ann.get(s, [])
        if items: names[s] = dict(items=items)
    os.makedirs(OUT, exist_ok=True); tmp = os.path.join(OUT, f"{mkt}.json.tmp")
    json.dump(dict(generated_utc=dt.datetime.now(UTC).isoformat(), market=mkt, names=names), open(tmp, "w"), indent=1); os.replace(tmp, os.path.join(OUT, f"{mkt}.json"))
    return len(names)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "scan":
        for m in (sys.argv[2:] or ["us", "asx", "jp", "hk", "sg", "kr", "tw", "lse"]):
            try: print(m, scan(m))
            except Exception as e: print(m, "error", str(e)[:120])
