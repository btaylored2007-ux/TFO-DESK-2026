"""Per-ticker feature store. Paid feeds first, free sources as fallback. Every field is fresh, stale or missing.

Missing values are null. They are never filled with zero. A failed vendor keeps the last value and marks it stale.
"""
import datetime as dt
from plan48.scanner import data_quality, gap_scan, regime
from plan48.scanner.vendors import Client
UTC = dt.timezone.utc
SECTOR_ETF = {"Technology": "XLK", "Financial Services": "XLF", "Financials": "XLF", "Energy": "XLE", "Healthcare": "XLV",
              "Consumer Cyclical": "XLY", "Consumer Defensive": "XLP", "Industrials": "XLI", "Basic Materials": "XLB",
              "Real Estate": "XLRE", "Utilities": "XLU", "Communication Services": "XLC"}
ENERGY = {"CL=F", "BZ=F", "NG=F", "RB=F", "HO=F", "XLE", "USO", "UNG"}
CRYPTO = {"BTC": "BTCUSDT_PERP.A", "XBT": "BTCUSDT_PERP.A", "ETH": "ETHUSDT_PERP.A", "SOL": "SOLUSDT_PERP.A",
          "KRAKEN:XBTUSD": "BTCUSDT_PERP.A", "KRAKEN:ETHUSD": "ETHUSDT_PERP.A", "KRAKEN:SOLUSD": "SOLUSDT_PERP.A"}

def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError): return None
    if f != f or abs(f) == float("inf"): return None
    return f

def _asof(v):
    if isinstance(v, dt.datetime): return v if v.tzinfo else v.replace(tzinfo=UTC)
    if isinstance(v, dt.date): return dt.datetime(v.year, v.month, v.day, tzinfo=UTC)
    try:
        a = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return a if a.tzinfo else a.replace(tzinfo=UTC)
    except Exception: return None

def env(value, asof, source, ttl_h, now, note=None, vendor_error=None, force_stale=False):
    base = dict(source=source, advisory=True, blocks_trade=False)
    if vendor_error: base["vendor_error"] = vendor_error
    if value is None:
        return dict(base, value=None, status="missing", asof=None, missing=True, stale=False, note=note or "no data; not filled")
    a = _asof(asof)
    if a is None or force_stale:
        return dict(base, value=value, status="stale", asof=None if a is None else a.isoformat(), missing=False, stale=True,
                    note=note or "vendor failed; cached or fallback value kept")
    age_h = (now - a).total_seconds() / 3600.0
    fresh = age_h <= ttl_h
    return dict(base, value=value, status="fresh" if fresh else "stale", asof=a.isoformat(), missing=False, stale=not fresh,
                age_h=round(age_h, 2), note=note)

def _rows(body):
    if isinstance(body, list): return body
    if isinstance(body, dict):
        for k in ("data", "results", "earnings", "historical"):
            v = body.get(k)
            if isinstance(v, list): return v
        resp = body.get("response")
        if isinstance(resp, dict):
            for k in ("data", "results"):
                if isinstance(resp.get(k), list): return resp[k]
    return []

def _mark(fetched, field):
    if fetched.get("error"): field = dict(field, vendor_error=fetched["error"])
    if fetched.get("from_cache") and fetched.get("error") and field.get("status") != "missing":
        field = dict(field, status="stale", stale=True, note="vendor failed; last value kept")
    return field

def parse_surprises(rows, source, now):
    out = []
    for r in rows or []:
        if not isinstance(r, dict): continue
        actual = _num(r.get("eps") if r.get("eps") is not None else r.get("actual"))
        est = _num(r.get("epsEstimated") if r.get("epsEstimated") is not None else r.get("estimate"))
        when = r.get("date") or r.get("report_date") or r.get("period")
        if actual is None or est in (None, 0) or not when: continue
        out.append(dict(date=str(when)[:10], actual=actual, estimate=est, surprise_pct=round((actual - est) / abs(est), 4)))
    out.sort(key=lambda x: x["date"])
    if not out: return env(None, None, source, 24 * 120, now)
    return env(out, out[-1]["date"] + "T21:00:00+00:00", source, 24 * 120, now)

def parse_revisions(grades, estimates, now):
    events = []
    rank = {"strong buy": 5, "buy": 4, "outperform": 4, "overweight": 4, "hold": 3, "neutral": 3, "underperform": 2,
            "underweight": 2, "sell": 1, "strong sell": 0}
    for r in grades or []:
        if not isinstance(r, dict): continue
        prev, new = r.get("previousGrade") or r.get("previous"), r.get("newGrade") or r.get("new")
        when = r.get("publishedDate") or r.get("date") or r.get("gradingDate")
        if not prev or not new or not when: continue
        a, b = rank.get(str(prev).lower()), rank.get(str(new).lower())
        if a is None or b is None or a == b: continue
        events.append(dict(date=str(when)[:19], direction="up" if b > a else "down", previous=prev, new=new))
    events.sort(key=lambda x: x["date"])
    if not events and estimates:
        same = {}
        for r in estimates:
            if not isinstance(r, dict): continue
            period = r.get("period") or r.get("date")
            cur = _num(r.get("epsAvg") or r.get("estimatedEpsAvg"))
            prev = _num(r.get("previousEpsAvg") or r.get("epsAvgPrior"))
            if period and cur is not None and prev is not None and cur != prev:
                same[str(period)] = (prev, cur, r.get("date") or period)
        for period, (prev, cur, when) in same.items():
            events.append(dict(date=str(when)[:19], direction="up" if cur > prev else "down", previous=prev, new=cur, period=period))
    if not events: return env(None, None, "fmp:grades", 48, now, "no paired revision; not filled as flat")
    cut = now - dt.timedelta(hours=48)
    within = []
    for e in events:
        a = _asof(e["date"])
        if a and a >= cut: within.append(e)
    latest = events[-1]
    return env(dict(direction=latest["direction"], latest=latest, history=events[-12:], within_48h=within),
               latest["date"], "fmp:grades", 48, now)

def parse_target(row, now):
    if not isinstance(row, dict): row = (row or [None])[0] if isinstance(row, list) else {}
    px = _num((row or {}).get("targetConsensus") or (row or {}).get("targetMedian") or (row or {}).get("priceTarget"))
    when = (row or {}).get("publishedDate") or (row or {}).get("date") or now
    if px is None: return env(None, None, "fmp:price-target-consensus", 24 * 7, now)
    return env(dict(target=px), when, "fmp:price-target-consensus", 24 * 7, now)

def parse_guidance(row, now):
    if not isinstance(row, dict): return env(None, None, "guidance", 48, now)
    change = row.get("change") or row.get("guidance")
    if change not in ("up", "down", "maintain"): return env(None, None, "guidance", 48, now)
    return env(dict(change=change), row.get("date") or now, row.get("source") or "guidance", 48, now)

def parse_finra(body, now):
    rows = _rows(body)
    parsed = []
    for r in rows:
        if not isinstance(r, dict): continue
        qty = _num(r.get("currentShortPositionQuantity") or r.get("currentShortShareNumber"))
        when = r.get("settlementDate") or r.get("settlement_date")
        if qty is None or not when: continue
        item = dict(settlement=str(when)[:10], shares=qty, previous=_num(r.get("previousShortPositionQuantity")),
                    days_to_cover=_num(r.get("daysToCoverQuantity") or r.get("daysToCover")))
        parsed.append(item)
    parsed.sort(key=lambda x: x["settlement"])
    if not parsed: return env(None, None, "finra:consolidatedShortInterest", 24 * 21, now)
    return env(parsed[-1], parsed[-1]["settlement"] + "T21:00:00+00:00", "finra:consolidatedShortInterest", 24 * 21, now)

def parse_insiders(rows, source, now, window_days=14):
    buys = []
    for r in rows or []:
        if not isinstance(r, dict): continue
        code = str(r.get("transactionCode") or r.get("transaction_code") or r.get("acquistionOrDisposition") or r.get("acquisitionOrDisposition") or "")
        kind = str(r.get("transactionType") or r.get("txn_type") or r.get("type") or "")
        buy = code.upper() == "P" or "purchase" in kind.lower() or kind.lower() in ("buy", "p - purchase")
        sale = "sale" in kind.lower() or code.upper() == "S"
        if not buy or sale: continue
        when = r.get("transactionDate") or r.get("transaction_date") or r.get("filed_at") or r.get("date")
        name = r.get("reportingName") or r.get("name") or r.get("owner")
        if not when: continue
        a = _asof(when)
        if a is None or a < now - dt.timedelta(days=window_days): continue
        buys.append(dict(date=str(when)[:10], name=name, shares=_num(r.get("securitiesTransacted") or r.get("shares"))))
    if not buys: return env(None, None, source, 24 * 21, now)
    names = {b["name"] for b in buys if b.get("name")}
    distinct = len(names) if names else None
    return env(dict(buy_count=len(buys), distinct_buyers=distinct, buys=buys[-20:]), buys[-1]["date"] + "T21:00:00+00:00", source, 24 * 21, now,
               note=None if distinct is not None else "buyer names missing; count only")

def parse_13f(body, now):
    rows = _rows(body)
    points = []
    for r in rows:
        if not isinstance(r, dict): continue
        shares = _num(r.get("shares") or r.get("totalShares") or r.get("numberOfShares"))
        when = r.get("date") or r.get("filingDate") or r.get("period")
        if shares is None or not when: continue
        points.append((str(when)[:10], shares, r.get("investorName") or r.get("holder")))
    points.sort()
    if not points: return env(None, None, "fmp:13f", 24 * 120, now), env(None, None, "fmp:13f_change", 24 * 120, now)
    snap = env(dict(date=points[-1][0], shares=points[-1][1], holder=points[-1][2]), points[-1][0] + "T21:00:00+00:00", "fmp:13f", 24 * 120, now)
    if len(points) < 2: return snap, env(None, None, "fmp:13f_change", 24 * 120, now, "one period only; change not filled")
    delta = points[-1][1] - points[-2][1]
    chg = env(dict(date=points[-1][0], prior_date=points[-2][0], share_change=delta), points[-1][0] + "T21:00:00+00:00", "fmp:13f_change", 24 * 120, now)
    return snap, chg

def parse_index(rows, sym, now):
    sym = sym.upper()
    found = []
    for r in rows or []:
        if not isinstance(r, dict): continue
        added = str(r.get("addedSecurity") or r.get("symbol") or r.get("added") or "").upper()
        removed = str(r.get("removedTicker") or r.get("removed") or "").upper()
        when = r.get("date") or r.get("dateAdded") or r.get("dateRemoved")
        if not when: continue
        if added == sym: found.append(dict(date=str(when)[:10], action="add", index=r.get("index") or "SP500"))
        if removed == sym: found.append(dict(date=str(when)[:10], action="delete", index=r.get("index") or "SP500"))
    found.sort(key=lambda x: x["date"])
    if not found: return env(None, None, "fmp:historical-sp500-constituent", 24 * 30, now)
    return env(found[-5:], found[-1]["date"] + "T21:00:00+00:00", "fmp:historical-sp500-constituent", 24 * 30, now)

def _bars(seq):
    out = []
    for r in seq or []:
        if isinstance(r, dict):
            c, o, v = _num(r.get("c") if r.get("c") is not None else r.get("close")), _num(r.get("o") if "o" in r else r.get("open")), _num(r.get("v") if "v" in r else r.get("volume"))
            d = r.get("d") or r.get("date") or r.get("t")
        elif isinstance(r, (list, tuple)) and len(r) >= 5:
            d, o, c, v = r[0], _num(r[1]), _num(r[4]), _num(r[5]) if len(r) > 5 else None
        else: continue
        out.append(dict(d=str(d)[:10] if d is not None else None, o=o, c=c, v=v))
    return [b for b in out if b["c"] is not None]

def parse_volume(bars, source, now, force_stale=False, vendor_error=None):
    xs = _bars(bars)
    vols = [b["v"] for b in xs if b["v"] is not None]
    if len(vols) < 21: return env(None, None, source, 24 * 5, now, "fewer than 21 volume bars; ratio not filled", vendor_error)
    last, base = vols[-1], vols[-21:-1]
    if len(base) < 20 or any(v is None for v in base): return env(None, None, source, 24 * 5, now, vendor_error=vendor_error)
    avg = sum(base) / 20.0
    if avg <= 0: return env(None, None, source, 24 * 5, now, vendor_error=vendor_error)
    asof = xs[-1]["d"]
    return env(dict(last_volume=last, avg_20d=round(avg, 2), ratio_vs_20d=round(last / avg, 4)),
               (asof + "T21:00:00+00:00") if asof and len(asof) == 10 else asof, source, 24 * 5, now, force_stale=force_stale, vendor_error=vendor_error)

def parse_gap(bars, now):
    xs = _bars(bars)
    if len(xs) < 2 or xs[-2]["c"] in (None, 0) or xs[-1]["o"] is None:
        return env(None, None, "bars", 24 * 5, now, "open or prior close missing; gap not filled")
    gap = round(xs[-1]["o"] / xs[-2]["c"] - 1, 6)
    hold = (xs[-1]["c"] >= xs[-1]["o"]) if gap > 0 else (xs[-1]["c"] <= xs[-1]["o"]) if gap < 0 else None
    return env(dict(gap=gap, gap_hold=hold, open=xs[-1]["o"], prev_close=xs[-2]["c"]), (xs[-1]["d"] or "") + "T21:00:00+00:00", "bars", 24 * 5, now)

def parse_options(body, source, now):
    row = body if isinstance(body, dict) else {}
    if isinstance(body, list) and body: row = body[0] if isinstance(body[0], dict) else {}
    data = row.get("data") if isinstance(row.get("data"), dict) else row
    call = _num(data.get("call_volume") or data.get("callVolume") or data.get("calls"))
    put = _num(data.get("put_volume") or data.get("putVolume") or data.get("puts"))
    if call is None and put is None: return env(None, None, source, 6, now)
    pc = _num(data.get("put_call") or data.get("putCall"))
    if pc is None and call not in (None, 0) and put is not None: pc = round(put / call, 4)
    if pc is None: return env(None, None, source, 6, now, "put/call not filled; a zero call volume is not a ratio")
    when = data.get("date") or data.get("asof") or now
    return env(dict(call_volume=call, put_volume=put, put_call=pc), when, source, 6, now)

def parse_eia(body, source, now):
    rows = []
    for r in _rows(body):
        if not isinstance(r, dict): continue
        v, p = _num(r.get("value")), r.get("period")
        if v is None or not p: continue
        rows.append((str(p)[:10], v))
    rows.sort()
    if not rows: return env(None, None, source, 24 * 10, now)
    return env(dict(period=rows[-1][0], value=rows[-1][1], prior=rows[-2][1] if len(rows) > 1 else None), rows[-1][0] + "T16:00:00+00:00", source, 24 * 10, now)

def rs_pair(name, bench):
    if name is None or bench is None: return None
    return round(name - bench, 6)

def _ret(closes, n=20):
    if not closes or len(closes) <= n: return None
    a, b = closes[-1 - n], closes[-1]
    if a in (None, 0) or b is None: return None
    return b / a - 1

def relative_strength(sym, bars, sector, now):
    series = {k: [b["c"] for b in _bars(v)] for k, v in (bars or {}).items()}
    name, spy = _ret(series.get(sym)), _ret(series.get("SPY"))
    etf = SECTOR_ETF.get(sector)
    sec = _ret(series.get(etf)) if etf else None
    ri = rs_pair(name, spy)
    rs = rs_pair(name, sec)
    idx = env(dict(rs=ri, bench="SPY", name_ret20=_ret(series.get(sym)), bench_ret20=spy) if ri is not None else None,
              now, "rs_index", 24 * 5, now, None if ri is not None else "name or SPY return missing")
    secf = env(dict(rs=rs, bench=etf, sector=sector) if rs is not None else None, now, "rs_sector", 24 * 5, now,
               None if rs is not None else "sector map or sector return missing")
    return idx, secf

def sector_rotation(bars, now):
    series = {k: [b["c"] for b in _bars(v)] for k, v in (bars or {}).items()}
    spy = _ret(series.get("SPY"))
    if spy is None: return env(None, None, "sector_rotation", 24 * 5, now, "SPY return missing")
    rows = []
    for name, etf in sorted(set(SECTOR_ETF.items())):
        r = _ret(series.get(etf))
        if r is None: continue
        rows.append(dict(sector=name, etf=etf, excess_20d=round(r - spy, 6)))
    if not rows: return env(None, None, "sector_rotation", 24 * 5, now, "no sector ETF returns")
    rows.sort(key=lambda x: -x["excess_20d"])
    return env(rows, now, "sector_rotation", 24 * 5, now)

def read_borrow(sym, payload, now):
    """Read an IBKR borrow file already on disk. This does not call the gateway and does not apply the short cap."""
    if not payload: return env(None, None, "ibkr_borrow_file", 24, now, "borrow file missing")
    names = {}
    if isinstance(payload, dict) and "names" in (payload.get("us") or {}):
        names = (payload.get("us") or {}).get("names") or {}
        asof = payload.get("updated_utc")
    elif isinstance(payload, dict):
        names = payload.get("names") or {}
        asof = payload.get("updated_utc")
    else: return env(None, None, "ibkr_borrow_file", 24, now)
    row = names.get(sym.split(".")[0]) or names.get(sym)
    if not row: return env(None, None, "ibkr_borrow_file", 24, now, "no borrow row for this name")
    value = {}
    if "available" in row: value["available"] = row.get("available")
    if "fee_pa" in row: value["fee_pa"] = row.get("fee_pa")
    if "ok" in row: value["ok"] = row.get("ok")
    if not value: return env(None, None, "ibkr_borrow_file", 24, now, "borrow row had no readable fields")
    return env(value, asof or now, "ibkr_borrow_file", 24, now)

def _eodhd_code(sym):
    s = sym.upper()
    if s.endswith(".AX"): return s[:-3] + ".AU"
    if s.endswith(".L"): return s[:-2] + ".LSE"
    if s.endswith(".T"): return s[:-2] + ".TSE"
    if "." in s: return s
    return s + ".US"

def _get(client, provider, url, method="GET", body=None):
    return client.call(provider, url, method=method, json_body=body)

def _one(sym, client, now, bars, borrow, filings, guidance):
    fields = {}
    fmp_earn = _get(client, "fmp", "https://financialmodelingprep.com/stable/earnings?symbol=%s" % sym)
    sur = parse_surprises(_rows(fmp_earn.get("body")), "fmp:earnings", now)
    sur = _mark(fmp_earn, sur)
    if sur["status"] == "missing":
        code = _eodhd_code(sym)
        start, end = (now.date() - dt.timedelta(days=400)).isoformat(), (now.date() + dt.timedelta(days=30)).isoformat()
        ed = _get(client, "eodhd", "https://eodhd.com/api/calendar/earnings?symbols=%s&from=%s&to=%s" % (code, start, end))
        alt = parse_surprises(_rows(ed.get("body")), "eodhd:calendar/earnings", now)
        alt = _mark(ed, alt)
        if alt["status"] != "missing": sur = alt
        elif fmp_earn.get("error"): sur = dict(sur, vendor_error=fmp_earn["error"])
    fields["earnings_surprises"] = sur
    grades = _get(client, "fmp", "https://financialmodelingprep.com/stable/grades?symbol=%s" % sym)
    est = _get(client, "fmp", "https://financialmodelingprep.com/stable/analyst-estimates?symbol=%s&period=quarter" % sym)
    rev = parse_revisions(_rows(grades.get("body")), _rows(est.get("body")), now)
    if grades.get("error") and not grades.get("ok"): rev = _mark(grades, rev)
    elif grades.get("from_cache"): rev = _mark(grades, rev)
    fields["estimate_revisions"] = rev
    pt = _get(client, "fmp", "https://financialmodelingprep.com/stable/price-target-consensus?symbol=%s" % sym)
    fields["price_target"] = _mark(pt, parse_target(pt.get("body"), now))
    fields["guidance"] = parse_guidance((guidance or {}).get(sym), now)
    fin = _get(client, "finra", "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest", "POST",
               {"compareFilters": [{"compareType": "equal", "fieldName": "symbolCode", "fieldValue": sym.split(".")[0]}], "limit": 4})
    fields["short_interest"] = _mark(fin, parse_finra(fin.get("body"), now))
    fields["ibkr_borrow"] = read_borrow(sym, borrow, now)
    uw = _get(client, "unusual_whales", "https://api.unusualwhales.com/api/insider/%s" % sym)
    ins = _mark(uw, parse_insiders(_rows(uw.get("body")), "unusual_whales:insider", now))
    if ins["status"] == "missing":
        fi = _get(client, "fmp", "https://financialmodelingprep.com/stable/insider-trading/search?symbol=%s" % sym)
        alt = _mark(fi, parse_insiders(_rows(fi.get("body")), "fmp:insider-trading", now))
        if alt["status"] != "missing": ins = alt
        elif filings and filings.get(sym):
            alt = parse_insiders(filings.get(sym), "sec:form4", now)
            if alt["status"] != "missing": ins = alt
            elif uw.get("error"): ins = dict(ins, vendor_error=uw["error"])
    fields["insider_buys"] = ins
    f13 = _get(client, "fmp", "https://financialmodelingprep.com/stable/institutional-ownership/symbol-positions-summary?symbol=%s" % sym)
    snap, chg = parse_13f(f13.get("body"), now)
    fields["form_13f"] = _mark(f13, snap); fields["form_13f_change"] = _mark(f13, chg)
    ix = _get(client, "fmp", "https://financialmodelingprep.com/stable/historical-sp500-constituent")
    fields["index_changes"] = _mark(ix, parse_index(_rows(ix.get("body")), sym, now))
    start = (now.date() - dt.timedelta(days=40)).isoformat()
    ms = _get(client, "massive", "https://api.massive.com/v2/aggs/ticker/%s/range/1/day/%s/%s?adjusted=true&sort=asc&limit=40" % (sym, start, now.date().isoformat()))
    maggs = ((ms.get("body") or {}) if isinstance(ms.get("body"), dict) else {}).get("results")
    if ms.get("ok") and maggs:
        vol = parse_volume([dict(d=dt.datetime.fromtimestamp(r["t"] / 1000, UTC).date().isoformat(), c=r.get("c"), o=r.get("o"), v=r.get("v")) for r in maggs if r.get("t")], "massive_aggs_1d", now)
        if ms.get("from_cache") and ms.get("error"): vol = dict(vol, status="stale", stale=True, vendor_error=ms["error"])
        fields["unusual_volume"] = vol
    elif (bars or {}).get(sym):
        fields["unusual_volume"] = parse_volume(bars.get(sym), "bars_fallback", now, force_stale=bool(ms.get("error")), vendor_error=ms.get("error"))
    else:
        fields["unusual_volume"] = env(None, None, "massive_aggs_1d", 24 * 5, now, vendor_error=ms.get("error"))
    if (bars or {}).get(sym): fields["gap"] = parse_gap(bars.get(sym), now)
    else: fields["gap"] = env(None, None, "bars", 24 * 5, now, "no bar open; gap not filled")
    opt = _get(client, "unusual_whales", "https://api.unusualwhales.com/api/stock/%s/options-volume" % sym)
    fields["options"] = _mark(opt, parse_options(opt.get("body"), "unusual_whales:options-volume", now))
    flow = _get(client, "unusual_whales", "https://api.unusualwhales.com/api/stock/%s/flow-recent" % sym)
    fields["options_flow"] = _mark(flow, env(_rows(flow.get("body")) or None, now, "unusual_whales:flow-recent", 6, now) if _rows(flow.get("body")) else env(None, None, "unusual_whales:flow-recent", 6, now))
    if fields["options_flow"].get("status") != "missing" and flow.get("from_cache"): fields["options_flow"] = _mark(flow, fields["options_flow"])
    dark = _get(client, "unusual_whales", "https://api.unusualwhales.com/api/darkpool/%s" % sym)
    fields["dark_pool"] = env(_rows(dark.get("body")) or None, now, "unusual_whales:darkpool", 6, now) if _rows(dark.get("body")) else env(None, None, "unusual_whales:darkpool", 6, now, vendor_error=dark.get("error"))
    cong = _get(client, "unusual_whales", "https://api.unusualwhales.com/api/congress/recent-trades?ticker=%s" % sym)
    fields["congress"] = env(_rows(cong.get("body")) or None, now, "unusual_whales:congress", 24 * 7, now) if _rows(cong.get("body")) else env(None, None, "unusual_whales:congress", 24 * 7, now, vendor_error=cong.get("error"))
    day = now.astimezone(dt.timezone(dt.timedelta(hours=-4))).date().isoformat()
    pre = _get(client, "massive", "https://api.massive.com/v2/aggs/ticker/%s/range/1/minute/%s/%s?adjusted=true&sort=asc&limit=50000" % (sym, day, day))
    mins = ((pre.get("body") or {}) if isinstance(pre.get("body"), dict) else {}).get("results")
    vol = fields["unusual_volume"]["value"] or {}
    prev = (fields["gap"]["value"] or {}).get("prev_close") if fields["gap"].get("value") else None
    if pre.get("ok") and mins:
        got = gap_scan.from_minutes(mins, prev, vol.get("avg_20d"), day, now)
        if pre.get("from_cache") and pre.get("error"): got = dict(got, status="stale", stale=True, vendor_error=pre["error"])
        fields["premarket"] = got
    else:
        fields["premarket"] = env(None, None, "massive_aggs_1m", 26, now, vendor_error=pre.get("error"))
    code = CRYPTO.get(sym.upper()) or CRYPTO.get(sym.upper().split(":")[-1].replace("USD", "") if sym.upper().startswith("KRAKEN:") else "")
    if code:
        oi = _get(client, "coinalyze", "https://api.coinalyze.net/v1/open-interest?symbols=%s&convert_to_usd=true" % code)
        fr = _get(client, "coinalyze", "https://api.coinalyze.net/v1/funding-rate?symbols=%s" % code)
        lq = _get(client, "coinalyze", "https://api.coinalyze.net/v1/liquidation-history?symbols=%s&interval=1hour&convert_to_usd=true" % code)
        ls = _get(client, "coinalyze", "https://api.coinalyze.net/v1/long-short-ratio-history?symbols=%s&interval=1hour" % code)
        fields["crypto_derivs"] = _crypto(oi, fr, lq, ls, code, now)
    if sym.upper() in ENERGY or sym.upper().split(".")[0] in ENERGY:
        fields["eia_inventories"] = _eia(client, now)
    sec = None
    prof = _get(client, "fmp", "https://financialmodelingprep.com/stable/profile?symbol=%s" % sym)
    body = prof.get("body")
    row = body[0] if isinstance(body, list) and body else body if isinstance(body, dict) else {}
    if isinstance(row, dict): sec = row.get("sector")
    idx, secf = relative_strength(sym, bars, sec, now)
    fields["rs_index"] = idx; fields["rs_sector"] = secf
    nxt = _next_earn(fields["earnings_surprises"], sym, client, now)
    fields["next_earnings"] = nxt
    return fields

def _crypto(oi, fr, lq, ls, code, now):
    def pick(resp):
        rows = resp.get("body") if isinstance(resp.get("body"), list) else _rows(resp.get("body"))
        for r in rows or []:
            if isinstance(r, dict) and (r.get("symbol") in (None, code) or code in str(r.get("symbol") or "")): return r
        return rows[0] if rows else None
    o, f, q, s = pick(oi), pick(fr), pick(lq), pick(ls)
    if not any((o, f, q, s)):
        err = next((r.get("error") for r in (oi, fr, lq, ls) if r.get("error")), None)
        return env(None, None, "coinalyze", 2, now, vendor_error=err)
    hist = (q or {}).get("history") or []
    last_ls = ((s or {}).get("history") or [None])[-1] or {}
    longs = [_num(z.get("l")) for z in hist if isinstance(z, dict)]
    shorts = [_num(z.get("s")) for z in hist if isinstance(z, dict)]
    def _sum(xs):
        if not xs or any(x is None for x in xs): return None
        return round(sum(xs), 2)
    value = dict(oi_usd=_num((o or {}).get("value")), funding_rate=_num((f or {}).get("value")),
                 liq_long_usd=_sum(longs), liq_short_usd=_sum(shorts),
                 long_short_ratio=_num(last_ls.get("r") if isinstance(last_ls, dict) else None))
    if not any(v is not None for v in value.values()): return env(None, None, "coinalyze", 2, now)
    got = env(value, now, "coinalyze", 2, now)
    if any(r.get("from_cache") and r.get("error") for r in (oi, fr, lq, ls)): got = dict(got, status="stale", stale=True)
    return got

def _eia(client, now):
    crude = _get(client, "eia", "https://api.eia.gov/v2/petroleum/stoc/wstk/data/?frequency=weekly&data[0]=value&facets[series][]=WCESTUS1&length=6")
    gas = _get(client, "eia", "https://api.eia.gov/v2/natural-gas/stor/wkly/data/?frequency=weekly&data[0]=value&facets[series][]=NW2_EPG0_SWO_R48_BCF&length=6")
    c, g = parse_eia(crude.get("body"), "eia:crude_stocks", now), parse_eia(gas.get("body"), "eia:natgas_storage", now)
    if c["status"] == "missing" and g["status"] == "missing":
        return env(None, None, "eia", 24 * 10, now, vendor_error=crude.get("error") or gas.get("error"))
    value = dict(crude=c["value"], natgas=g["value"])
    if c["value"] is None: value["crude_missing"] = True
    if g["value"] is None: value["natgas_missing"] = True
    asof = c.get("asof") or g.get("asof") or now.isoformat()
    got = env(value, asof, "eia", 24 * 10, now)
    if crude.get("from_cache") or gas.get("from_cache"): got = dict(got, status="stale", stale=True)
    return got

def _next_earn(surprises, sym, client, now):
    code = _eodhd_code(sym)
    start, end = now.date().isoformat(), (now.date() + dt.timedelta(days=21)).isoformat()
    ed = _get(client, "eodhd", "https://eodhd.com/api/calendar/earnings?symbols=%s&from=%s&to=%s" % (code, start, end))
    dates = []
    for r in _rows(ed.get("body")):
        if isinstance(r, dict) and r.get("report_date"): dates.append(str(r["report_date"])[:10])
    dates = sorted(d for d in dates if d >= now.date().isoformat())
    if dates: return _mark(ed, env(dates[0], dates[0] + "T12:00:00+00:00", "eodhd:calendar/earnings", 24 * 3, now))
    return env(None, None, "eodhd:calendar/earnings", 24 * 3, now, vendor_error=ed.get("error"), note="next earnings date unknown")

def _fred(client, series):
    got = _get(client, "fred", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=%s" % series)
    text = got.get("body") if isinstance(got.get("body"), str) else ""
    return got, regime.parse_fred_csv(text)

def build(symbols, client=None, now=None, bars=None, borrow=None, filings=None, panel=None, guidance=None, write=False, state_path=None, static_path=None, snap_path=None):
    now = now or dt.datetime.now(UTC)
    if now.tzinfo is None: now = now.replace(tzinfo=UTC)
    client = client or Client(min_interval=0)
    tickers = {}
    for sym in symbols or []:
        try: tickers[sym.upper()] = _one(sym.upper(), client, now, bars, borrow, filings, guidance)
        except Exception as e:
            tickers[sym.upper()] = dict(error=env(None, None, "feature_store", 1, now, note="%s; name skipped, trade not blocked" % type(e).__name__))
    vix_f, vix = _fred(client, "VIXCLS"); v3_f, v3 = _fred(client, "VXVCLS")
    hy_f, hy = _fred(client, "BAMLH0A0HYM2"); usd_f, usd = _fred(client, "DTWEXBGS")
    d10_f, d10 = _fred(client, "DGS10"); d2_f, d2 = _fred(client, "DGS2")
    reg = regime.assemble(vix, v3, panel, hy, usd, d10, d2, now)
    for field, fetched in ((reg["vix_term"], vix_f), (reg["credit_hy_oas"], hy_f), (reg["dollar"], usd_f), (reg["rates"], d10_f)):
        if fetched.get("error") and field.get("status") != "missing":
            reg_key = field
            reg_key.update(status="stale", stale=True, vendor_error=fetched["error"])
        elif fetched.get("error") and field.get("status") == "missing":
            field["vendor_error"] = fetched["error"]
    if v3_f.get("error") and reg["vix_term"].get("status") == "missing": reg["vix_term"]["vendor_error"] = v3_f["error"]
    eia = _eia(client, now)
    doc = dict(paper_only=True, advisory=True, blocks_trade=False, built_utc=now.isoformat(), tickers=tickers, regime=reg,
               sector_rotation=sector_rotation(bars, now), energy_inventories=eia,
               premarket_scanner=gap_scan.hits(tickers),
               earnings_calendar={s: f.get("next_earnings", {}).get("value") for s, f in tickers.items() if (f.get("next_earnings") or {}).get("value")},
               note="Advisory. Missing fields are null. Stale fields keep their as-of. Nothing here blocks a trade or changes a rail.")
    doc["data_quality"] = data_quality.summarize(doc, now)
    if write:
        data_quality.write(doc["data_quality"], state_path, static_path)
        if snap_path:
            import json, os
            os.makedirs(os.path.dirname(snap_path), exist_ok=True)
            tmp = snap_path + ".tmp"; json.dump(doc, open(tmp, "w"), default=str); os.replace(tmp, snap_path)
    return doc
