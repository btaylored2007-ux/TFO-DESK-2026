"""Benzinga headlines via the existing Massive/Polygon key. PAPER ONLY.

Sentiment and recency feed THF's N pillar and Quinn's (news_filings) pack.
A missing key, a breaker, or any error contributes 0 (neutral) and returns a
warning. It never blocks a trade and never prints the key.
"""
import datetime as dt, math
UTC = dt.timezone.utc
POS = ("beat", "beats", "upgrade", "surge", "record", "approval", "wins", "raised guidance", "buy rating")
NEG = ("miss", "misses", "downgrade", "fraud", "probe", "halt", "bankrupt", "plunge", "cuts guidance", "sell rating", "investigation")


def sentiment_of(title, raw=None):
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return max(-1.0, min(1.0, float(raw)))
    if isinstance(raw, str):
        m = {"positive": 1.0, "bullish": 1.0, "negative": -1.0, "bearish": -1.0, "neutral": 0.0}
        if raw.strip().lower() in m:
            return m[raw.strip().lower()]
    t = (title or "").lower()
    p = sum(1 for w in POS if w in t)
    n = sum(1 for w in NEG if w in t)
    if p == n:
        return 0.0
    return 1.0 if p > n else -1.0


def recency_weight(published, now=None, half_life_h=24.0, max_h=72.0):
    now = now or dt.datetime.now(UTC)
    try:
        pub = published if isinstance(published, dt.datetime) else dt.datetime.fromisoformat(str(published).replace("Z", "+00:00"))
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=UTC)
    except Exception:
        return 0.0
    age_h = (now - pub).total_seconds() / 3600.0
    if age_h < 0 or age_h > max_h:
        return 0.0
    return math.exp(-age_h / half_life_h)


def score_rows(rows, now=None):
    """{TICKER: sum(sentiment * recency)} from annotated news rows."""
    now = now or dt.datetime.now(UTC)
    out = {}
    for r in rows or []:
        w = recency_weight(r.get("published_utc"), now)
        if not w:
            continue
        s = sentiment_of(r.get("title"), r.get("sentiment_raw") if "sentiment_raw" in r else r.get("sentiment"))
        for t in r.get("tickers") or []:
            k = str(t).upper()
            if k:
                out[k] = out.get(k, 0.0) + s * w
    return out


def annotate_pack(rows, now=None):
    now = now or dt.datetime.now(UTC)
    out = []
    for r in rows or []:
        w = recency_weight(r.get("published_utc"), now)
        sent = sentiment_of(r.get("title"), r.get("sentiment_raw") if "sentiment_raw" in r else r.get("sentiment"))
        out.append(dict(title=(r.get("title") or "")[:220], tickers=list(r.get("tickers") or []),
                        sentiment=sent, recency_w=round(w, 4), published_utc=r.get("published_utc"),
                        source=r.get("source") or "massive_benzinga"))
    return out


def load(get=None, now=None):
    """{ok, scores, rows, warning}. ok False -> caller adds 0 to N and logs the warning."""
    try:
        from veteran_desk import massive as M
        if get is None and not M._key():
            return dict(ok=False, scores={}, rows=[], warning="benzinga/massive key missing; N contribution neutral", blocks_trade=False)
        rows = M.news(limit=50, get=get)
    except Exception as e:
        return dict(ok=False, scores={}, rows=[], warning="benzinga %s; N contribution neutral" % type(e).__name__, blocks_trade=False)
    if rows is None:
        return dict(ok=False, scores={}, rows=[], warning="benzinga unavailable; N contribution neutral", blocks_trade=False)
    now = now or dt.datetime.now(UTC)
    return dict(ok=True, scores=score_rows(rows, now), rows=annotate_pack(rows, now), warning=None, blocks_trade=False)


def for_quinn(get=None, now=None):
    """Quinn pack block. Always returns a dict; never raises."""
    try:
        d = load(get=get, now=now)
    except Exception as e:
        d = dict(ok=False, scores={}, rows=[], warning="benzinga %s; N contribution neutral" % type(e).__name__, blocks_trade=False)
    return dict(ok=d.get("ok"), rows=d.get("rows") or [], warning=d.get("warning"),
                blocks_trade=False, note="Benzinga via Massive. sentiment -1..1, recency_w decays over 24h and is 0 after 72h. Missing feed is neutral, not a block.")
