"""tfo_chal_live_20261030 (Brett 23:23 WITA): the debate challenger/devil's advocate saw only pit.sqlite daily bars (stale to 29 Oct),
so day-1 earnings reversals read as trend breaks. ctx(sym) returns TODAY's live picture from paid feeds:
US -> Massive 5-min aggs (today's session OHLC + last 12 bars); intl/FX/futures -> paid_feeds.fetch_quote. Read-only; never fabricates."""
import datetime as dt, os, sys, urllib.parse
UTC = dt.timezone.utc
def ctx(sym):
    if os.environ.get("PYTEST_CURRENT_TEST") or not sym or str(sym).upper().startswith("KRAKEN:"): return None
    out = {}
    try:
        from veteran_desk import feeds as FD, massive as MS
        if FD.is_us(sym):
            t = dt.datetime.now(UTC); s = str(sym).upper().replace("-", ".")
            js = MS._call("aggs", "/v2/aggs/ticker/%s/range/5/minute/%s/%s" % (urllib.parse.quote(s), (t - dt.timedelta(days=4)).date(), t.date()), dict(adjusted="true", sort="asc", limit=5000), None, t.timestamp())
            R = [r for r in ((js or {}).get("results") or []) if all(k in r for k in ("t", "o", "h", "l", "c"))]
            if R:
                d0 = dt.datetime.fromtimestamp(R[-1]["t"] / 1000, UTC).date(); T = [r for r in R if dt.datetime.fromtimestamp(r["t"] / 1000, UTC).date() == d0]
                out = dict(src="massive_aggs_5m", today_bar=dict(date=str(d0), o=T[0]["o"], h=max(r["h"] for r in T), l=min(r["l"] for r in T), c=T[-1]["c"], v=sum(r.get("v", 0) for r in T)),
                           last_5m=[dict(t=dt.datetime.fromtimestamp(r["t"] / 1000, UTC).strftime("%H:%M"), o=r["o"], h=r["h"], l=r["l"], c=r["c"]) for r in R[-12:]])
    except Exception: pass
    try:   # tfo_paidrt_20261031: persisted 1m/5m/15m (companion store, not pit)
        from veteran_desk import intraday_store as _IB
        sm = _IB.summary(sym, n=12)
        if sm: out["intraday"] = dict(tf=sm["tf"], n=sm["n"], src=sm["src"], last=sm["last"], last_utc=sm["last_utc"],
                                      bars=[dict(t=r["utc"][11:16], o=r["o"], h=r["h"], l=r["l"], c=r["c"]) for r in sm["bars"][-12:]])
    except Exception: pass
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if base not in sys.path: sys.path.insert(0, base)
        import paid_feeds as PF
        q = PF.fetch_quote(sym)
        if q and q.get("px"): out["live_quote"] = dict(px=q.get("px"), ccy=q.get("ccy"), quote_utc=q.get("quote_utc"), status=q.get("status"))
    except Exception: pass
    if out: out["note"] = "LIVE paid-feed data for TODAY; daily bars in this DATA end at the last pit.sqlite close. Judge today's move (e.g. day-1 earnings reversal) from this, not as a trend break."
    return out or None
