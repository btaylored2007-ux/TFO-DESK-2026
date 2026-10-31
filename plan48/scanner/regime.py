"""Market regime from VIX term structure, breadth, HY OAS, the dollar and rates. Advisory tags only."""
import datetime as dt
UTC = dt.timezone.utc
SEAT_LENS = {
    "crypto": ("vix_term", "dollar"),
    "news_filings": ("vix_term", "breadth"),
    "essentials_utilities": ("rates", "breadth"),
    "trend": ("breadth", "vix_term"),
    "price_action": ("breadth", "vix_term"),
    "macro": ("rates", "dollar", "credit_hy_oas", "vix_term"),
    "energy": ("credit_hy_oas", "dollar", "rates"),
    "tech_ai_infra": ("breadth", "vix_term", "rates"),
    "banks": ("credit_hy_oas", "rates", "breadth"),
    "fx_commodities": ("dollar", "rates"),
    "healthcare": ("breadth", "vix_term"),
}

def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError): return None
    if f != f or abs(f) == float("inf"): return None
    return f

def envelope(value, asof, source, ttl_h, now, note=None):
    base = dict(source=source, advisory=True, blocks_trade=False)
    if value is None:
        return dict(base, value=None, status="missing", asof=None, missing=True, stale=False, note=note or "no data; not filled")
    a = _asof(asof)
    if a is None:
        return dict(base, value=value, status="stale", asof=None, missing=False, stale=True, note=note or "value kept; as-of unreadable")
    age_h = (now - a).total_seconds() / 3600.0
    fresh = age_h <= ttl_h
    return dict(base, value=value, status="fresh" if fresh else "stale", asof=a.isoformat(), missing=False, stale=not fresh,
                age_h=round(age_h, 2), note=note)

def _asof(v):
    if isinstance(v, dt.datetime): return v if v.tzinfo else v.replace(tzinfo=UTC)
    if isinstance(v, dt.date): return dt.datetime(v.year, v.month, v.day, tzinfo=UTC)
    try:
        a = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return a if a.tzinfo else a.replace(tzinfo=UTC)
    except Exception: return None

def parse_fred_csv(text):
    rows = []
    for ln in str(text or "").splitlines()[1:]:
        p = ln.split(",")
        if len(p) < 2 or p[1] in ("", "."): continue
        n = _num(p[1])
        if n is None: continue
        rows.append((p[0][:10], n))
    return rows

def series_field(rows, source, now, ttl_h=120, chg_key="chg_20"):
    if not rows: return envelope(None, None, source, ttl_h, now)
    d, v = rows[-1]
    chg = None
    if len(rows) >= 21 and rows[-21][1]: chg = round(v - rows[-21][1], 4) if source.startswith("fred:BAML") or source.startswith("fred:DGS") else round(v / rows[-21][1] - 1, 4)
    if source.startswith("fred:DGS") or source.startswith("fred:BAML"):
        if len(rows) >= 21: chg = round(v - rows[-21][1], 4)
    value = dict(last=v, date=d)
    if chg is None: value[chg_key] = None
    else: value[chg_key] = chg
    if chg is None: value[chg_key + "_missing"] = True
    return envelope(value, d + "T21:00:00+00:00", source, ttl_h, now)

def vix_term(vix_rows, vix3m_rows, now):
    if not vix_rows or not vix3m_rows or not vix3m_rows[-1][1]:
        return envelope(None, None, "fred:VIXCLS/VXVCLS", 120, now, "VIX or VIX3M missing")
    vix, v3 = vix_rows[-1], vix3m_rows[-1]
    ratio = vix[1] / v3[1]
    shape = "backwardation" if ratio > 1 else "contango" if ratio < 1 else "flat"
    asof = min(vix[0], v3[0]) + "T21:00:00+00:00"
    return envelope(dict(vix=vix[1], vix3m=v3[1], vix_date=vix[0], vix3m_date=v3[0], ratio=round(ratio, 4), shape=shape),
                    asof, "fred:VIXCLS/VXVCLS", 120, now)

def breadth(panel, now):
    """panel: {sym: [closes oldest..newest]}. Names without enough history are left out of that average. Not filled with zero."""
    n50 = a50 = n200 = a200 = adv = dec = 0
    for closes in (panel or {}).values():
        xs = [c for c in closes if c is not None]
        if len(xs) != len(closes):
            xs = list(closes)
        if len(closes) >= 51 and all(c is not None for c in closes[-50:]) and closes[-1] is not None:
            sma = sum(closes[-50:]) / 50.0
            n50 += 1
            if closes[-1] > sma: a50 += 1
        if len(closes) >= 201 and all(c is not None for c in closes[-200:]) and closes[-1] is not None:
            sma = sum(closes[-200:]) / 200.0
            n200 += 1
            if closes[-1] > sma: a200 += 1
        if len(closes) >= 2 and closes[-1] is not None and closes[-2] is not None:
            if closes[-1] > closes[-2]: adv += 1
            elif closes[-1] < closes[-2]: dec += 1
    if n50 < 30:
        return envelope(None, None, "breadth", 120, now, "fewer than 30 names with a 50-day average")
    value = dict(pct_above_50=round(100.0 * a50 / n50, 2), n_50=n50, advances=adv, declines=dec,
                 advance_decline=round(adv / dec, 3) if dec else None)
    if not dec: value["advance_decline_missing"] = True
    if n200 >= 30:
        value["pct_above_200"] = round(100.0 * a200 / n200, 2); value["n_200"] = n200
    else:
        value["pct_above_200"] = None; value["n_200"] = n200; value["pct_above_200_missing"] = True
    return envelope(value, now, "breadth", 120, now)

def credit(rows, now):
    f = series_field(rows, "fred:BAMLH0A0HYM2", now, chg_key="chg_20")
    if f["value"]: f["value"]["unit"] = "oas_pct"
    return f

def dollar(rows, now):
    return series_field(rows, "fred:DTWEXBGS", now, chg_key="chg_20")

def rates(dgs10, dgs2, now):
    if not dgs10: return envelope(None, None, "fred:DGS10/DGS2", 120, now)
    f = series_field(dgs10, "fred:DGS10", now, chg_key="us10y_chg_20")
    v = dict(f["value"] or {})
    if dgs2:
        v["us2y"] = dgs2[-1][1]; v["us2y_date"] = dgs2[-1][0]
        v["curve_2s10s"] = round(dgs10[-1][1] - dgs2[-1][1], 4)
    else:
        v["us2y"] = None; v["curve_2s10s"] = None; v["curve_missing"] = True
    return envelope(v, f.get("asof") or (dgs10[-1][0] + "T21:00:00+00:00"), "fred:DGS10/DGS2", 120, now)

def assemble(vix_rows, vix3m_rows, panel, hy_rows, usd_rows, dgs10, dgs2, now):
    return dict(vix_term=vix_term(vix_rows, vix3m_rows, now), breadth=breadth(panel, now), credit_hy_oas=credit(hy_rows, now),
                dollar=dollar(usd_rows, now), rates=rates(dgs10, dgs2, now))

def tag(reg):
    votes = []
    def val(name):
        f = (reg or {}).get(name) or {}
        if f.get("status") == "missing" or f.get("value") is None: return None
        return f["value"]
    vt = val("vix_term")
    if vt: votes.append("off" if vt.get("shape") == "backwardation" else "on" if vt.get("shape") == "contango" else "mixed")
    br = val("breadth")
    if br and br.get("pct_above_50") is not None:
        p = br["pct_above_50"]
        votes.append("off" if p < 40 else "on" if p > 60 else "mixed")
    cr = val("credit_hy_oas")
    if cr and cr.get("chg_20") is not None:
        votes.append("off" if cr["chg_20"] > 0.25 else "on" if cr["chg_20"] < -0.25 else "mixed")
    dol = val("dollar")
    if dol and dol.get("chg_20") is not None:
        votes.append("off" if dol["chg_20"] > 0.01 else "on" if dol["chg_20"] < -0.01 else "mixed")
    rt = val("rates")
    if rt and rt.get("us10y_chg_20") is not None:
        votes.append("off" if rt["us10y_chg_20"] > 0.15 else "on" if rt["us10y_chg_20"] < -0.15 else "mixed")
    if not votes: label = "unknown"
    else:
        on, off = votes.count("on"), votes.count("off")
        label = "risk-off" if off > on else "risk-on" if on > off else "mixed"
    return dict(tag=label, votes=votes, advisory=True, blocks_trade=False, missing=(label == "unknown"),
                stale_inputs=any((reg or {}).get(k, {}).get("stale") for k in (reg or {})))

def tag_for(seat, reg):
    t = tag(reg)
    lens = SEAT_LENS.get(seat, ("vix_term", "breadth"))
    t["seat"] = seat
    t["lens"] = list(lens)
    t["lens_missing"] = [k for k in lens if ((reg or {}).get(k) or {}).get("status") == "missing" or (reg or {}).get(k) is None]
    return t
