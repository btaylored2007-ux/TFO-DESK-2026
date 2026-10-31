"""Gap and pre-market volume from Massive minute aggregates. Missing volume stays null."""
import datetime as dt
from zoneinfo import ZoneInfo
UTC = dt.timezone.utc
ET = ZoneInfo("America/New_York")

def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError): return None
    if f != f or abs(f) == float("inf"): return None
    return f

def from_minutes(results, prev_close, avg20, session, now):
    """results: Massive agg rows {t,o,h,l,c,v}. session is a date. Pre-market is before 09:30 ET."""
    if isinstance(session, str): session = dt.date.fromisoformat(session[:10])
    rows = []
    for r in results or []:
        if r.get("t") is None: continue
        ts = dt.datetime.fromtimestamp(r["t"] / 1000.0, UTC).astimezone(ET)
        if ts.date() != session or (ts.hour, ts.minute) >= (9, 30): continue
        vol = _num(r.get("v"))
        op = _num(r.get("o"))
        rows.append((ts, op, vol))
    rows.sort(key=lambda x: x[0])
    if not rows:
        return dict(value=None, status="missing", asof=None, source="massive_aggs_1m", missing=True, stale=False,
                    advisory=True, blocks_trade=False, note="no pre-market prints")
    opens = [o for _, o, _ in rows if o is not None]
    vols = [v for _, _, v in rows if v is not None]
    gap = None
    if opens and prev_close:
        gap = round(opens[0] / prev_close - 1, 6)
    pre_vol = round(sum(vols), 2) if vols else None
    rvol = round(pre_vol / avg20, 4) if pre_vol is not None and avg20 else None
    value = dict(gap=gap, open=opens[0] if opens else None, prev_close=prev_close, premarket_volume=pre_vol,
                 avg20_volume=avg20, rvol_vs_20d=rvol, session=session.isoformat())
    if gap is None: value["gap_missing"] = True
    if pre_vol is None: value["premarket_volume_missing"] = True
    if rvol is None: value["rvol_missing"] = True
    asof = rows[-1][0].astimezone(UTC)
    age_h = (now - asof).total_seconds() / 3600.0
    fresh = age_h <= 26
    return dict(value=value, status="fresh" if fresh else "stale", asof=asof.isoformat(), source="massive_aggs_1m",
                missing=False, stale=not fresh, advisory=True, blocks_trade=False, age_h=round(age_h, 2))

def hits(tickers):
    """Scanner rows. A name is listed when the gap is known. Volume is null when Massive did not send it."""
    out = []
    for sym, fields in (tickers or {}).items():
        p = fields.get("premarket") or {}
        if p.get("status") == "missing" or not p.get("value"): continue
        v = p["value"]
        if v.get("gap") is None: continue
        if abs(v["gap"]) < 0.02 and (v.get("rvol_vs_20d") or 0) < 1.5: continue
        out.append(dict(symbol=sym, gap=v.get("gap"), premarket_volume=v.get("premarket_volume"),
                        rvol_vs_20d=v.get("rvol_vs_20d"), volume_missing=bool(v.get("premarket_volume_missing")),
                        asof=p.get("asof"), stale=bool(p.get("stale")), advisory=True, blocks_trade=False))
    return out
