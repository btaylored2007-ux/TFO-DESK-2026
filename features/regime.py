"""Advisory regime: VIX term structure, breadth, HY OAS, the dollar and rates.

The composite flag from veteran_desk.news_desk.regime is an input. This module
does not change that function and does not change sizing. risk_on and risk_off
are labels for a reader. A missing input leaves the tag missing rather than
defaulting to risk_on.

risk_off when any of these hold, and every required input is present:
  - news desk flag is crisis or risk-off (its existing labels)
  - VIXCLS above VXVCLS (backwardation)
  - HY OAS (BAMLH0A0HYM2) above 5, the news desk's risk-off cutoff
  - breadth under 45 percent of names above their 50-day average
  - 10-year yield below the 2-year
  - broad dollar (DTWEXBGS) up more than 2 percent across the last 20 prints,
    when those 20 prints exist
Otherwise risk_on. EIA inventories are reported beside the tag and do not flip it.
"""
import datetime as dt
from features.envelope import field
from features.scrub import scrub
from features.vendors import eia, fred
from features.vendors.common import iso, num

UTC = dt.timezone.utc
HY_OAS_RISK_OFF = 5.0
BREADTH_RISK_OFF = 0.45
DOLLAR_CHANGE_RISK_OFF = 0.02
NEWS_RISK_OFF = {"crisis", "risk-off"}
REQUIRED = ("news_regime", "vix_term", "breadth", "hy_oas", "dollar", "rates")


def build_regime(now, clients):
    as_of = iso(now)
    fields = {}
    fields["news_regime"] = _guard("news_desk", as_of, lambda: _news(now, clients))
    fields["vix_term"] = _guard("fred", as_of, lambda: _vix_term(now, clients))
    fields["breadth"] = _guard("breadth", as_of, lambda: _breadth(now, clients))
    fields["hy_oas"] = _guard("fred", as_of, lambda: _hy(now, clients))
    fields["dollar"] = _guard("fred", as_of, lambda: _dollar(now, clients))
    fields["rates"] = _guard("fred", as_of, lambda: _rates(now, clients))
    fields["eia_inventories"] = _guard("eia", as_of, lambda: eia.fetch(now, clients.eia, clients.gates["eia"]))
    fields["tag"] = _tag(fields, as_of)
    return {
        "advisory": True,
        "gates": False,
        "rails": "unchanged",
        "paper_only": True,
        "built_utc": as_of,
        "fields": fields,
    }


def _guard(source, as_of, fn):
    try:
        return fn()
    except Exception as e:
        return field(None, as_of, source=source, missing=True, reason=scrub(e))


def _news(now, clients):
    flag, gauges = clients.news_regime()
    if not flag or flag == "unknown":
        return field(None, iso(now), source="news_desk", missing=True, reason="regime inputs missing")
    return field({"flag": flag, "gauges": gauges or {}}, iso(now), source="news_desk")


def _last(doc):
    if not doc or doc.get("missing") or not doc.get("value"):
        return None
    row = doc["value"][-1]
    return str(row[0])[:10], num(row[1])


def _vix_term(now, clients):
    gate = clients.gates["fred"]
    spot = fred.series(fred.VIX, 1, clients.fred_rows, gate, now)
    three = fred.series(fred.VIX3M, 1, clients.fred_rows, gate, now)
    a, b = _last(spot), _last(three)
    if not a or not b or a[1] is None or b[1] in (None, 0):
        return field(None, iso(now), source="fred", missing=True, reason="vix term structure incomplete")
    fresh = spot.get("freshness") == "fresh" and three.get("freshness") == "fresh"
    structure = "backwardation" if a[1] > b[1] else "contango"
    return field(
        {
            "vix": a[1],
            "vix_date": a[0],
            "vix3m": b[1],
            "vix3m_date": b[0],
            "ratio": a[1] / b[1],
            "structure": structure,
        },
        min(a[0], b[0]),
        source="fred",
        fresh=fresh,
        reason=None if fresh else "stale vix leg",
    )


def _hy(now, clients):
    doc = fred.series(fred.HY_OAS, 1, clients.fred_rows, clients.gates["fred"], now)
    last = _last(doc)
    if not last or last[1] is None:
        return field(None, iso(now), source="fred", missing=True, reason="hy oas missing")
    return field(
        {"date": last[0], "hy_oas_pct": last[1]},
        last[0],
        source="fred",
        fresh=doc.get("freshness") == "fresh",
        reason=doc.get("reason"),
    )


def _dollar(now, clients):
    doc = fred.series(fred.DOLLAR, 20, clients.fred_rows, clients.gates["fred"], now)
    rows = doc.get("value") or []
    if doc.get("missing") or not rows:
        return field(None, iso(now), source="fred", missing=True, reason=doc.get("reason") or "dollar missing")
    last_d, last_v = str(rows[-1][0])[:10], num(rows[-1][1])
    first_v = num(rows[0][1])
    change_missing = len(rows) < 20 or last_v is None or not first_v
    change = None if change_missing else last_v / first_v - 1.0
    return field(
        {"date": last_d, "value": last_v, "change_20d": change, "change_missing": change_missing, "prints": len(rows)},
        last_d,
        source="fred",
        fresh=doc.get("freshness") == "fresh",
        reason=doc.get("reason"),
    )


def _rates(now, clients):
    gate = clients.gates["fred"]
    ten = fred.series(fred.DGS10, 1, clients.fred_rows, gate, now)
    two = fred.series(fred.DGS2, 1, clients.fred_rows, gate, now)
    a, b = _last(ten), _last(two)
    if not a or not b or a[1] is None or b[1] is None:
        return field(None, iso(now), source="fred", missing=True, reason="rates incomplete")
    fresh = ten.get("freshness") == "fresh" and two.get("freshness") == "fresh"
    return field(
        {"dgs10": a[1], "dgs10_date": a[0], "dgs2": b[1], "dgs2_date": b[0], "curve_10y2y": a[1] - b[1]},
        min(a[0], b[0]),
        source="fred",
        fresh=fresh,
        reason=None if fresh else "stale rates leg",
    )


def breadth_from_bars(bars_by_sym):
    """Share of names whose last close is above their own 50-close average.

    A name with fewer than 50 closes is skipped. It is not treated as below the average.
    """
    above = used = 0
    dates = []
    for rows in (bars_by_sym or {}).values():
        closes, last_date = [], None
        for r in rows or []:
            if isinstance(r, dict):
                c, last_date = num(r.get("c")), r.get("date") or last_date
            else:
                c = num(r[4]) if len(r) > 4 else None
                last_date = r[0] if r else last_date
            if c is not None:
                closes.append(c)
        if len(closes) < 50:
            continue
        used += 1
        if closes[-1] > (sum(closes[-50:]) / 50.0):
            above += 1
        if last_date:
            dates.append(str(last_date)[:10])
    if not used:
        return None
    return {"above_50d": above, "n": used, "pct": above / float(used), "as_of_bar": max(dates) if dates else None}


def _breadth(now, clients):
    stats = breadth_from_bars(clients.breadth_bars())
    if not stats:
        return field(None, iso(now), source="breadth", missing=True, reason="no name with 50 closes")
    return field(stats, stats.get("as_of_bar") or iso(now), source="breadth")


def _tag(fields, as_of):
    missing = [k for k in REQUIRED if fields.get(k, {}).get("missing") or fields.get(k, {}).get("value") is None]
    if missing:
        return field(None, as_of, source="regime", missing=True, reason="missing " + ",".join(missing))
    fresh = all(fields[k].get("freshness") == "fresh" for k in REQUIRED)
    reasons = []
    if fields["news_regime"]["value"].get("flag") in NEWS_RISK_OFF:
        reasons.append("news_" + fields["news_regime"]["value"]["flag"])
    if fields["vix_term"]["value"].get("structure") == "backwardation":
        reasons.append("vix_backwardation")
    hy = num(fields["hy_oas"]["value"].get("hy_oas_pct"))
    if hy is not None and hy > HY_OAS_RISK_OFF:
        reasons.append("hy_oas")
    pct = num(fields["breadth"]["value"].get("pct"))
    if pct is not None and pct < BREADTH_RISK_OFF:
        reasons.append("breadth")
    curve = num(fields["rates"]["value"].get("curve_10y2y"))
    if curve is not None and curve < 0:
        reasons.append("curve_inverted")
    chg = fields["dollar"]["value"].get("change_20d")
    if chg is not None and chg > DOLLAR_CHANGE_RISK_OFF:
        reasons.append("dollar")
    tag = "risk_off" if reasons else "risk_on"
    return field(tag, as_of, source="regime", fresh=fresh, reason=",".join(reasons) or None)
