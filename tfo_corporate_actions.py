#!/usr/bin/env python3
"""tfo_corporate_actions.py -- V1 corporate-actions pre-check (splits + cash dividends). PAPER ONLY.

Origin: 2026-09-29. Tokio Marine (8766.T) went ex a 15-for-1 split and a JPY 122.5 interim dividend; the ledger knew
about neither, so the post-split print read as -93% and a fictitious STOP filled. This module makes corporate actions
a first-class input for the V1 books (fundamental_book, fundamental_spec_book, convexity_sleeve; never tfo_v2*):

  WHEN   * start of every Head Trader rail cycle (tfo_ticket.py --execute calls precheck() BEFORE scan(), so a split is
           applied before any rail evaluates that session), and
         * every hourly desk tick (desk_tick.sh runs this script before fundamental_book.py marks the book).
         A symbol is re-checked once per regular session, at/after its exchange's open (Yahoo currentTradingPeriod), or
         immediately when a marker flags a split-like bad tick (reports/corporate_actions_requests.json).
  WHAT   Yahoo chart events=div,split since the position's fill (idempotent: every applied event is recorded on the
         position, so "since fill" and "since last check" give the same result).
         SPLIT (ex-date after fill): units x ratio; entry_px, entry_px_usd, rail_orig_entry_usd, rail_rebase_usd,
           rail_trail *_usd levels and exit_state.orig_units adjusted; a pre-ex-date mark is re-based; notional/premium
           (the money) unchanged. Pre-split fields kept in position.corporate_actions[]. Rail RULES unchanged.
         DIVIDEND (ex-date after fill, held into the ex-date incl. units sold on/after it): cash credited (long) or
           debited (short) and the same amount booked to realised_usd so the equity identity
           (seed + realised - costs + unrealised + interest) stays reconciled; FX at the ex-date; one
           position.distributions_received[] record per event, the sourced registry (reports/distributions.json) marked
           applied so fundamental_distributions.apply_pending can never book it twice.
           Amount basis: Yahoo quotes dividends in the split basis of the ex-date (8766.T: JPY 122.5 per PRE-split share
           on a same-day 15:1 split). If amount / split-adjusted prior close > 15%, it is divided by the ratio of splits
           on/after the ex-date; still implausible -> REFUSED and logged for a human (no guessed special dividends).
         Recently closed positions (closed on/after an ex-date >= CLOSED_DIV_FROM) get dividends they were entitled to.
  LOG    reports/corporate_actions_log.jsonl (every check/action/refusal), a DIV/SPLIT line in the HT LOG and a CA-* fact.
"""
import argparse, datetime as dt, json, math, os, sys, tempfile, time, urllib.request
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.abspath(__file__))
R = os.path.join(BASE, "reports")
BOOKS = {"fundamental_book": "fundamental", "fundamental_spec_book": "fundamental_spec", "convexity_sleeve": "convexity_sleeve"}
UTC, SYD = dt.timezone.utc, ZoneInfo("Australia/Sydney")
MAX_DIV_YIELD = 0.15            # a single cash dividend above 15% of the prior close is treated as a basis error
CLOSED_DIV_FROM = "2026-09-29"  # closed records: ex-dates from go-live only (history is reported, not restated)
CLOSED_LOOKBACK_DAYS = 10
UA = {"User-Agent": "Mozilla/5.0"}   # same header as the desk's quote fetchers (a full browser UA draws 429s)
MINOR = {"GBp": ("GBP", 0.01), "GBX": ("GBP", 0.01), "ZAc": ("ZAR", 0.01), "ILA": ("ILS", 0.01)}


def paths(reports=None):
    r = reports or R
    return {"state": os.path.join(r, "corporate_actions_state.json"), "log": os.path.join(r, "corporate_actions_log.jsonl"),
            "requests": os.path.join(r, "corporate_actions_requests.json"), "ht_log": os.path.join(r, "tfo_head_trader_log.txt"),
            "facts": os.path.join(r, "tfo_head_trader_facts.jsonl"), "registry": os.path.join(r, "distributions.json"),
            "reports": r}


def _utc(t=None):
    return (t or dt.datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(UTC)
    except (TypeError, ValueError):
        return None


def _local_date(utc_s, tz):
    t = _parse_utc(utc_s)
    return t.astimezone(ZoneInfo(tz)).date().isoformat() if t else None


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x


def _jload(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _jsave(path, obj):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix="." + os.path.basename(path), suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=1, default=str)
    os.replace(tmp, path)


# ----------------------------------------------------------------------------------------------- Yahoo
def chart_url(sym, p1, p2):
    return ("https://query1.finance.yahoo.com/v8/finance/chart/%s?period1=%d&period2=%d&interval=1d&events=div,split"
            % (sym, int(p1), int(p2)))


def parse_events(sym, payload, url=""):
    res = ((payload or {}).get("chart") or {}).get("result") or []
    if not res:
        raise ValueError("%s: no chart result" % sym)
    r0 = res[0]
    meta = r0.get("meta") or {}
    tz = meta.get("exchangeTimezoneName") or "UTC"
    Z = ZoneInfo(tz)
    ev = r0.get("events") or {}
    day = lambda ts: dt.datetime.fromtimestamp(int(ts), Z).date().isoformat()
    splits = []
    for s in (ev.get("splits") or {}).values():
        num, den = float(s.get("numerator") or 0), float(s.get("denominator") or 0)
        if num > 0 and den > 0 and abs(num / den - 1) > 1e-9:
            splits.append({"date": day(s["date"]), "ts": int(s["date"]), "numerator": num, "denominator": den,
                           "ratio": num / den, "split_ratio": s.get("splitRatio") or "%g:%g" % (num, den)})
    divs = [{"date": day(d["date"]), "ts": int(d["date"]), "amount": float(d["amount"])}
            for d in (ev.get("dividends") or {}).values() if _num(d.get("amount")) and d["amount"] > 0]
    closes = {}
    q = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
    for ts, c in zip(r0.get("timestamp") or [], q.get("close") or []):
        if _num(c) and c > 0:
            closes[day(ts)] = float(c)
    reg = ((meta.get("currentTradingPeriod") or {}).get("regular") or {})
    return {"sym": sym, "tz": tz, "ccy": meta.get("currency"), "url": url, "splits": sorted(splits, key=lambda x: x["ts"]),
            "divs": sorted(divs, key=lambda x: x["ts"]), "closes": closes, "last_px": meta.get("regularMarketPrice"),
            "regular_start": reg.get("start"), "regular_end": reg.get("end")}


def _get_json(url, timeout=12, retries=2):
    last = None
    for i in range(retries + 1):
        u = url if i % 2 == 0 else url.replace("query1.", "query2.")
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=timeout) as h:
                return json.load(h)
        except Exception as e:                                          # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def yahoo_fetch(sym, p1, p2, timeout=12):
    url = chart_url(sym, p1, p2)
    return parse_events(sym, _get_json(url, timeout), url)


def yahoo_fx(ccy, ex_date, now=None, timeout=12):
    """USD per 1 unit of `ccy` on the ex-date (daily close; live price when the ex-date is today)."""
    if not ccy or ccy == "USD":
        return 1.0, "USD"
    scale = 1.0
    if ccy in MINOR:
        ccy, scale = MINOR[ccy]
        if ccy == "USD":
            return scale, "minor USD"
    now = now or dt.datetime.now(UTC)
    d = dt.date.fromisoformat(ex_date)
    p1 = int(dt.datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp()) - 7 * 86400
    p2 = int(now.timestamp()) + 86400
    sym = "%s=X" % ccy          # USD->CCY (e.g. JPY=X ~156.x); inverted. CCYUSD=X is rounded to 4 dp (0.0064: 0.6% error on JPY)
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?period1=%d&period2=%d&interval=1d" % (sym, p1, p2)
    r0 = _get_json(url, timeout)["chart"]["result"][0]
    meta = r0.get("meta") or {}
    if ex_date >= now.date().isoformat() and _num(meta.get("regularMarketPrice")):
        return scale / float(meta["regularMarketPrice"]), "1/%s live %.6g %s" % (sym, meta["regularMarketPrice"], _utc(now))
    best = None
    closes = ((r0.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    for ts, c in zip(r0.get("timestamp") or [], closes):
        day = dt.datetime.fromtimestamp(ts, ZoneInfo(meta.get("exchangeTimezoneName") or "Europe/London")).date().isoformat()
        if _num(c) and c > 0 and day <= ex_date:
            best = (float(c), day)
    if not best:
        raise ValueError("no %s bar on/before %s" % (sym, ex_date))
    return scale / best[0], "1/%s daily close %.6g %s" % (sym, best[0], best[1])


# ----------------------------------------------------------------------------------------------- position helpers
def entry_utc(p):
    return (p.get("entry_utc") or (p.get("entry_ticket") or {}).get("utc") or p.get("opened_utc")
            or p.get("entry_time") or p.get("filled_utc"))


def applied_splits(p):
    out = []
    ca = p.get("corporate_action")
    if isinstance(ca, dict) and ca.get("type") == "stock_split" and ca.get("ex_date"):
        try:
            a, b = str(ca.get("ratio", "")).lower().replace("-for-", ":").split(":")
            out.append((ca["ex_date"], float(a) / float(b)))
        except ValueError:
            out.append((ca["ex_date"], None))
    for c in p.get("corporate_actions") or []:
        if c.get("type") == "split":
            out.append((c["ex_date"], c.get("ratio")))
    return out


def booked_div_dates(p):
    return {d.get("ex_date") for d in (p.get("distributions_received") or [])}


def _fill_qty(f):
    for k in ("qty", "units"):
        if _num(f.get(k)):
            return abs(float(f[k]))
    return 0.0


def units_at_ex(p, ex_date, tz):
    """Units (current split basis) held at the start of the ex-date: remaining units + units sold on/after it."""
    u = abs(float(p.get("units") or 0.0))
    splits = [(d, r) for d, r in applied_splits(p) if r]
    for f in p.get("exit_fills") or []:
        fd = _local_date(f.get("utc"), tz)
        if fd and fd >= ex_date:
            q = _fill_qty(f)
            for d, r in splits:        # a fill before a split is in the older, smaller share basis
                if fd < d:
                    q *= r
            u += q
    return u


def _scale_split(p, ratio, ex_date, tz, convexity):
    pre = {k: p.get(k) for k in ("units", "entry_px", "entry_px_usd", "rail_orig_entry_usd", "rail_rebase_usd", "mark", "mark_usd")}
    pre["rail_trail"] = dict(p["rail_trail"]) if isinstance(p.get("rail_trail"), dict) else None
    p["units"] = float(p["units"]) * ratio
    for k in ("entry_px", "entry_px_usd", "rail_orig_entry_usd", "rail_rebase_usd"):
        if _num(p.get(k)):
            p[k] = float(p[k]) / ratio
    t = p.get("rail_trail")
    if isinstance(t, dict):
        for k, v in list(t.items()):
            if k.endswith("_usd") and _num(v):
                t[k] = float(v) / ratio
    es = p.get("exit_state")
    if isinstance(es, dict) and _num(es.get("orig_units")):
        es["orig_units"] = float(es["orig_units"]) * ratio
    mq = _local_date(p.get("mark_quote_utc") or p.get("mark_utc"), tz)
    rebased_mark = bool(mq is None or mq < ex_date)
    if rebased_mark:
        if _num(p.get("mark")):
            p["mark"] = float(p["mark"]) / ratio
        if _num(p.get("mark_usd")) and not convexity:     # convexity mark_usd is the position total: unchanged
            p["mark_usd"] = float(p["mark_usd"]) / ratio
    post = {k: p.get(k) for k in pre if k != "rail_trail"}
    post["rail_trail"] = dict(t) if isinstance(t, dict) else None
    return pre, post, rebased_mark


def normalise_div(div, ev):
    """-> (amount per CURRENT-basis share, note) or (None, reason)."""
    amt, d = div["amount"], div["date"]
    prev = [c for day, c in ev["closes"].items() if day < d]
    prior = prev[-1] if prev else None
    if prior is None:
        days = sorted(k for k in ev["closes"] if k < d)
        prior = ev["closes"][days[-1]] if days else None
    same_or_after = 1.0
    for s in ev["splits"]:
        if s["date"] >= d:
            same_or_after *= s["ratio"]
    if not prior:
        return amt, "no prior close to sanity-check yield; amount as published"
    y = amt / prior
    if y <= MAX_DIV_YIELD:
        return amt, "yield %.2f%% of prior close %.6g" % (100 * y, prior)
    if same_or_after != 1.0 and amt / same_or_after / prior <= MAX_DIV_YIELD:
        return amt / same_or_after, ("published %.6g per pre-split share (yield %.1f%% vs split-adjusted prior close %.6g); "
                                     "divided by split ratio %g -> %.6g per current share" % (amt, 100 * y, prior, same_or_after, amt / same_or_after))
    return None, "REFUSED: dividend %.6g is %.1f%% of prior close %.6g (> %d%%) -- basis unclear; book by hand" % (amt, 100 * y, prior, round(100 * MAX_DIV_YIELD))


def _registry_has(registry, sym, ex_date):
    return any(e.get("symbol") == sym and e.get("ex_date") == ex_date for e in (registry or {}).get("events") or [])


# ----------------------------------------------------------------------------------------------- core (pure)
def process_book(book, led, now, fetch, fx, only=None, splits_only=False, registry=None, closed=True, events_cache=None):
    """Apply due corporate actions to one V1 ledger dict IN PLACE. No locks, no file writes (the caller persists).
    fetch(sym, p1, p2) -> parse_events dict; fx(ccy, ex_date) -> (usd_per_unit, source). Returns a list of actions."""
    if book.startswith("tfo_v2"):
        raise ValueError("V2 books are out of scope")
    convexity = book == "convexity_sleeve"
    cache = events_cache if events_cache is not None else {}
    actions = []
    registry = registry if registry is not None else {"events": []}
    nowu = _utc(now)

    def events(sym, since):
        key = (sym, since)
        if key not in cache:
            p1 = int(since.timestamp()) - 14 * 86400
            cache[key] = fetch(sym, p1, int(now.timestamp()) + 86400)
        return cache[key]

    def book_div(p, sym, ev, div, units, holder, closed_key=None):
        amt, note = normalise_div(div, ev)
        tag = "CA-DIV-%s-%s-%s" % (book, sym, div["date"])
        if amt is None:
            actions.append({"type": "dividend_refused", "book": book, "sym": sym, "ex_date": div["date"], "published": div["amount"],
                            "ccy": ev["ccy"], "reason": note, "url": ev["url"], "utc": nowu, "tag": tag})
            return
        rate, fx_src = fx(ev["ccy"], div["date"])
        short = str(p.get("side", "long")).lower() == "short"
        gross_local = units * amt
        usd = round(gross_local * rate * (-1 if short else 1), 2)
        rec = {"ex_date": div["date"], "amount_per_share": round(amt, 8), "published_amount": div["amount"], "ccy": ev["ccy"],
               "units": units, "gross_local": round(gross_local, 4), "fx_usd": rate, "fx_source": fx_src, "usd": usd,
               "kind": "dividend", "basis_note": note, "source": ev["url"], "booked_by": "tfo_corporate_actions",
               "booked_utc": nowu, "tag": tag, "in_realised": True, "paper_only": True}
        p.setdefault("distributions_received", []).append(rec)
        led["cash"] = round(float(led.get("cash") or 0.0) + usd, 2)
        led["realised_usd"] = round(float(led.get("realised_usd") or 0.0) + usd, 2)
        led["distributions_total_usd"] = round(float(led.get("distributions_total_usd") or 0.0) + usd, 2)
        led["distributions_in_realised_usd"] = round(float(led.get("distributions_in_realised_usd") or 0.0) + usd, 2)
        if led.get("equity") is not None:
            led["equity"] = round(float(led["equity"]) + usd, 2)
        registry.setdefault("events", []).append({
            "symbol": sym, "ex_date": div["date"], "amount_per_share": round(amt, 8), "currency": ev["ccy"], "kind": "dividend",
            "source_url": ev["url"], "note": "tfo_corporate_actions %s; %s" % (book, note), "recorded_utc": nowu,
            "applied_utc": nowu, "book": book, "booked_by": "tfo_corporate_actions", "usd": usd, "units": units})
        actions.append({"type": "dividend", "book": book, "sym": sym, "ex_date": div["date"], "amount_per_share": amt,
                        "published": div["amount"], "ccy": ev["ccy"], "units": units, "fx_usd": rate, "fx_source": fx_src,
                        "usd": usd, "side": "short" if short else "long", "closed_key": closed_key, "note": note,
                        "url": ev["url"], "utc": nowu, "tag": tag, "per_share_usd": amt * rate})

    for sym, p in list((led.get("positions") or {}).items()):
        if only and sym not in only:
            continue
        ticker = p.get("mark_ticker") or sym
        eu = _parse_utc(entry_utc(p))
        if eu is None:
            actions.append({"type": "skipped", "book": book, "sym": sym, "reason": "no entry time on position", "utc": nowu})
            continue
        try:
            ev = events(ticker, eu)
        except Exception as e:                                          # noqa: BLE001
            actions.append({"type": "fetch_failed", "book": book, "sym": sym, "reason": str(e)[:200], "utc": nowu})
            continue
        tz = ev["tz"]
        ed = eu.astimezone(ZoneInfo(tz)).date().isoformat()
        today = now.astimezone(ZoneInfo(tz)).date().isoformat()
        done = {d for d, _ in applied_splits(p)}
        for s in ev["splits"]:
            if s["date"] <= ed or s["date"] > today or s["date"] in done:
                continue
            pre, post, rebased = _scale_split(p, s["ratio"], s["date"], tz, convexity)
            tag = "CA-SPLIT-%s-%s-%s" % (book, sym, s["date"])
            p.setdefault("corporate_actions", []).append({
                "type": "split", "ex_date": s["date"], "ratio": s["ratio"], "split_ratio": s["split_ratio"], "applied_utc": nowu,
                "source": s.get("url") or ev["url"], "pre_split": pre, "post_split": post, "mark_rebased": rebased,
                "tag": tag, "by": "tfo_corporate_actions", "paper_only": True,
                "note": "money unchanged (notional/premium); rail rules unchanged; levels re-based by the ratio"})
            actions.append({"type": "split", "book": book, "sym": sym, "ex_date": s["date"], "ratio": s["ratio"],
                            "split_ratio": s["split_ratio"], "units": p["units"], "entry_px_usd": p.get("entry_px_usd") or p.get("rail_orig_entry_usd"),
                            "pre_units": pre["units"], "mark_rebased": rebased, "url": ev["url"], "utc": nowu, "tag": tag})
        if splits_only:
            continue
        booked = booked_div_dates(p)
        for dv in ev["divs"]:
            if dv["date"] <= ed or dv["date"] > today or dv["date"] in booked:
                continue
            if _registry_has(registry, sym, dv["date"]):
                actions.append({"type": "dividend_skipped", "book": book, "sym": sym, "ex_date": dv["date"],
                                "reason": "already in distributions registry (booked by fundamental_distributions)", "utc": nowu})
                continue
            book_div(p, sym, ev, dv, units_at_ex(p, dv["date"], tz), p)

    if closed and not splits_only and isinstance(led.get("closed"), dict):
        for key, c in list(led["closed"].items()):
            if not isinstance(c, dict):
                continue
            sym = key.split("#")[0]
            if only and sym not in only:
                continue
            fills = [f for f in c.get("exit_fills") or [] if _parse_utc(f.get("utc"))]
            if not fills:
                continue
            last = max(_parse_utc(f["utc"]) for f in fills)
            if last.date().isoformat() < CLOSED_DIV_FROM or (now - last).days > CLOSED_LOOKBACK_DAYS:
                continue
            eu = _parse_utc(entry_utc(c))
            if eu is None:
                continue
            try:
                ev = events(c.get("mark_ticker") or sym, eu)
            except Exception as e:                                      # noqa: BLE001
                actions.append({"type": "fetch_failed", "book": book, "sym": key, "reason": str(e)[:200], "utc": nowu})
                continue
            tz = ev["tz"]
            ed = eu.astimezone(ZoneInfo(tz)).date().isoformat()
            ld = last.astimezone(ZoneInfo(tz)).date().isoformat()
            booked = booked_div_dates(c)
            for dv in ev["divs"]:
                if dv["date"] < CLOSED_DIV_FROM or dv["date"] <= ed or dv["date"] > ld or dv["date"] in booked:
                    continue
                if _registry_has(registry, sym, dv["date"]):
                    continue
                u = units_at_ex(c, dv["date"], tz)
                if u > 0:
                    book_div(c, sym, ev, dv, u, c, closed_key=key)
    return actions


# ----------------------------------------------------------------------------------------------- persistence / logging
def record_actions(actions, reports=None, now=None):
    """Append every action to the CA log; DIV/SPLIT also to the HT LOG + facts. Never raises."""
    P = paths(reports)
    now = now or dt.datetime.now(UTC)
    syd = now.astimezone(SYD).strftime("%Y-%m-%d %H:%M")
    try:
        with open(P["log"], "a") as f:
            for a in actions:
                f.write(json.dumps(a, default=str) + "\n")
        with open(P["ht_log"], "a") as lg, open(P["facts"], "a") as fc:
            for a in actions:
                if a["type"] == "dividend":
                    lg.write("LOG %s DIV %s %.6g %.6g CORP %s 0.00 %.2f %.2f 0.00\n"
                             % (syd, a["sym"], a["units"], a["per_share_usd"], a["book"], a["usd"], a["usd"]))
                    fc.write(json.dumps({"syd": syd, "fact": "DIV %s %s ex %s: %s %.6g %s/sh x %.6g sh @ %.6g USD = $%.2f (%s%s); cash + realised"
                                         % (a["sym"], a["book"], a["ex_date"], "short OWES" if a["side"] == "short" else "long receives",
                                            a["amount_per_share"], a["ccy"], a["units"], a["fx_usd"], a["usd"], a["fx_source"],
                                            "; closed record " + a["closed_key"] if a.get("closed_key") else ""),
                                         "ticket": a["tag"], "fees_usd": 0.0, "source": a["url"]}) + "\n")
                elif a["type"] == "split":
                    lg.write("LOG %s SPLIT %s %.6g %.6g CORP %s 0.00 0.00 0.00 0.00\n"
                             % (syd, a["sym"], a["units"], a["entry_px_usd"] or 0, a["book"]))
                    fc.write(json.dumps({"syd": syd, "fact": "SPLIT %s %s %s ex %s applied: units %.6g -> %.6g, basis/rail levels / %g%s"
                                         % (a["sym"], a["book"], a["split_ratio"], a["ex_date"], a["pre_units"], a["units"], a["ratio"],
                                            "; pre-ex mark re-based" if a["mark_rebased"] else ""),
                                         "ticket": a["tag"], "fees_usd": 0.0, "source": a["url"]}) + "\n")
        return True
    except Exception as e:                                              # noqa: BLE001
        print("CORP-ACTIONS log write failed: %s" % e)
        return False


def _next_check(ev, now):
    s = ev.get("regular_start")
    if not _num(s):
        return now + dt.timedelta(hours=6)
    st = dt.datetime.fromtimestamp(int(s), UTC)
    return st if now < st else st + dt.timedelta(days=1)


def due_symbols(ledgers, state, requests, now, force=False):
    due = {}
    for book, led in ledgers.items():
        for sym, p in (led.get("positions") or {}).items():
            k = "%s|%s" % (book, sym)
            nx = _parse_utc((state.get("symbols") or {}).get(k, {}).get("next_check_utc"))
            if force or sym in requests or nx is None or now >= nx:
                due.setdefault(book, set()).add(sym)
        for key, c in (led.get("closed") or {}).items() if isinstance(led.get("closed"), dict) else []:
            k = "%s|closed|%s" % (book, key)
            if (state.get("closed_done") or {}).get(k):
                continue
            fills = [_parse_utc(f.get("utc")) for f in (c.get("exit_fills") or []) if isinstance(c, dict)]
            fills = [f for f in fills if f]
            if fills and max(fills).date().isoformat() >= CLOSED_DIV_FROM and (now - max(fills)).days <= CLOSED_LOOKBACK_DAYS:
                due.setdefault(book, set()).add(key.split("#")[0])
    return due


def run(now=None, fetch=None, fx=None, force=False, only=None, dry=False, reports=None, lock=True, lock_timeout=30, verbose=True):
    """Check due V1 positions and apply actions under the book locks. Returns a summary dict."""
    P = paths(reports)
    now = now or dt.datetime.now(UTC)
    fetch = fetch or yahoo_fetch
    fx = fx or (lambda ccy, d: yahoo_fx(ccy, d, now))
    state = _jload(P["state"], {"symbols": {}, "closed_done": {}})
    requests = _jload(P["requests"], {})
    ledgers = {b: _jload(os.path.join(P["reports"], b + ".json"), {}) for b in BOOKS}
    due = due_symbols(ledgers, state, requests, now, force)
    if only:
        due = {b: s & set(only) for b, s in due.items() if s & set(only)}
    summary = {"utc": _utc(now), "due": {b: sorted(s) for b, s in due.items()}, "actions": [], "dry": dry}
    if not due:
        if verbose:
            print("CORP-ACTIONS %s nothing due" % summary["utc"])
        return summary
    # fetch OUTSIDE the locks (network), apply INSIDE them from the cache
    cache, fetched = {}, {}

    def cfetch(sym, p1, p2):
        if sym not in fetched:
            try:
                fetched[sym] = fetch(sym, p1, p2)
            except Exception as e:                                      # noqa: BLE001
                fetched[sym] = e
        if isinstance(fetched[sym], Exception):
            raise fetched[sym]
        return fetched[sym]
    for b, syms in due.items():
        process_book(b, json.loads(json.dumps(ledgers[b])), now, cfetch, lambda c, d: (1.0, "prefetch"), only=syms,
                     registry={"events": []}, events_cache={})
    import contextlib
    with contextlib.ExitStack() as stack:
        if lock and not dry:
            import desk_lock
            for b in BOOKS:
                if b in due:
                    stack.enter_context(desk_lock.ledger_lock(BOOKS[b], timeout=lock_timeout))
        registry = _jload(P["registry"], {"events": [], "note": "sourced distribution events only - no URL, no event"})
        n_reg = len(registry.get("events") or [])
        for b in BOOKS:
            if b not in due:
                continue
            path = os.path.join(P["reports"], b + ".json")
            led = _jload(path, None)
            if not led:
                continue
            acts = process_book(b, led, now, cfetch, fx, only=due[b], registry=registry, events_cache=cache)
            changed = [a for a in acts if a["type"] in ("split", "dividend")]
            if changed and not dry:
                led.setdefault("corporate_actions_log", []).extend(
                    {k: a.get(k) for k in ("type", "sym", "ex_date", "ratio", "usd", "units", "tag", "utc")} for a in changed)
                _jsave(path, led)
            summary["actions"].extend(acts)
            for sym in due[b]:
                ticker = ((led.get("positions") or {}).get(sym) or {}).get("mark_ticker") or sym
                ev = fetched.get(ticker)
                if isinstance(ev, dict) and not dry:
                    state.setdefault("symbols", {})["%s|%s" % (b, sym)] = {
                        "last_check_utc": _utc(now), "next_check_utc": _utc(_next_check(ev, now)), "tz": ev["tz"],
                        "splits_seen": [s["date"] for s in ev["splits"]][-3:], "divs_seen": [d["date"] for d in ev["divs"]][-3:]}
                    for key in (led.get("closed") or {}) if isinstance(led.get("closed"), dict) else []:
                        if key.split("#")[0] == sym and sym not in (led.get("positions") or {}):
                            state.setdefault("closed_done", {})["%s|closed|%s" % (b, key)] = _utc(now)
        if not dry and len(registry.get("events") or []) != n_reg:
            _jsave(P["registry"], registry)
    if not dry:
        live = {k: v for k, v in (state.get("symbols") or {}).items()
                if k.split("|")[1] in (ledgers.get(k.split("|")[0], {}).get("positions") or {})}
        state["symbols"] = live
        state["updated_utc"] = _utc(now)
        _jsave(P["state"], state)
        done_req = {s for b in due.values() for s in b}
        if requests and done_req & set(requests):
            _jsave(P["requests"], {k: v for k, v in requests.items() if k not in done_req})
        record_actions(summary["actions"], reports, now)
    if verbose:
        applied = [a for a in summary["actions"] if a["type"] in ("split", "dividend")]
        print("CORP-ACTIONS %s checked %d symbol(s); applied %s%s" % (
            summary["utc"], sum(len(s) for s in due.values()),
            "; ".join("%s %s %s %s" % (a["type"].upper(), a["sym"], a["ex_date"], ("$%.2f" % a["usd"]) if a["type"] == "dividend" else a["split_ratio"]) for a in applied) or "none",
            "".join("; %s %s: %s" % (a["type"], a["sym"], a.get("reason")) for a in summary["actions"] if a["type"] in ("dividend_refused", "fetch_failed"))))
    return summary


def precheck(**kw):
    """Rail-cycle hook: never raises."""
    try:
        return run(**kw)
    except Exception as e:                                              # noqa: BLE001
        print("CORP-ACTIONS precheck failed (%s: %s) -- rails keep the bad-tick guard" % (type(e).__name__, str(e)[:200]))
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="check every open V1 position now")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--dry", action="store_true", help="report what would be applied; write nothing")
    a = ap.parse_args()
    s = run(force=a.force, only=set(a.only) if a.only else None, dry=a.dry)
    if a.dry:
        print(json.dumps([x for x in s["actions"] if x["type"] != "skipped"], indent=1, default=str))


if __name__ == "__main__":
    main()
