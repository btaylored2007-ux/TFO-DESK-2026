#!/usr/bin/env python3
"""Timestamped public equity valuations, with no orders or accounting flows.

Run with --write to refresh existing PAPER ledgers. The default only writes the
quote/audit report. Original fills, units, cash, capital, realised P&L, costs and
closed records are preserved. Extended-hours marks are valuation-only.
"""
import argparse
import concurrent.futures
import contextlib
import copy
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

BASE = Path(__file__).resolve().parent
REPORTS = BASE / "reports"
UTC = dt.timezone.utc
FRESH_SECONDS = 300
FX_FRESH_SECONDS = 120
# 2026-09-28 funder mandate (Head Trader): Yahoo delayed quotes (~15-30 min) on non-US venues (.AX .L .DE .HK
# .T .HE .OL .ST .TO ...) are valid DELAYED PAPER prints when they come from the CURRENT regular session.
# US (no suffix) keeps the 20-minute paper-bar window; US LIVE (<60s) and crypto are unaffected.
US_PAPER_BAR_MAX_AGE = 1200
DELAYED_VENUE_PAPER_BAR_MAX_AGE = 2700


def paper_bar_max_age(symbol):
    """Max age (s) of a current-regular-session 1m bar usable as a DELAYED PAPER print."""
    sym = str(symbol or "")
    if "=" in sym or sym.startswith("^") or "." not in sym:
        return US_PAPER_BAR_MAX_AGE
    return DELAYED_VENUE_PAPER_BAR_MAX_AGE
BOOKS = ("fundamental_book", "fundamental_spec_book", "convexity_sleeve",
         "soxl_weekend_probe", "value_book", "gap_close_levered_book")


def utc_now():
    return dt.datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _iso(epoch):
    return dt.datetime.fromtimestamp(epoch, UTC).isoformat().replace("+00:00", "Z")


def _positive(value):
    if isinstance(value, bool):
        raise ValueError("boolean price")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("price must be positive and finite")
    return number


def parse_chart(symbol, payload, fetched_at=None):
    """Pure parser: pair every price with its actual provider timestamp."""
    fetched_at = fetched_at or utc_now()
    now = dt.datetime.fromisoformat(fetched_at.replace("Z", "+00:00")).timestamp()
    result = payload["chart"]["result"][0]
    meta = result["meta"]
    ccy = meta.get("currency")
    if not isinstance(ccy, str) or not ccy.strip():
        raise ValueError("quote currency missing")
    regular_epoch = _positive(meta.get("regularMarketTime"))
    if regular_epoch > now + 60:
        raise ValueError("regular quote timestamp is in the future")
    regular = {"px": _positive(meta.get("regularMarketPrice")),
               "quote_utc": _iso(regular_epoch), "epoch": regular_epoch,
               "price_field": "regularMarketPrice", "session": "regular"}
    periods = meta.get("currentTradingPeriod") or {}
    session = next((name for name in ("pre", "regular", "post")
                    if float((periods.get(name) or {}).get("start", 0)) <= now
                    < float((periods.get(name) or {}).get("end", 0))), "closed")
    extended = None
    paper_bar = None
    max_paper_age = paper_bar_max_age(symbol)
    closes = ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    for epoch, price in zip(result.get("timestamp") or [], closes):
        try:
            epoch, price = _positive(epoch), _positive(price)
        except (TypeError, ValueError):
            continue
        if epoch > now + 60:
            continue
        regular_period = periods.get('regular') or {}
        if (float(regular_period.get('start', 0)) <= epoch < float(regular_period.get('end', 0))
                and 0 <= now - epoch <= max_paper_age
                and (paper_bar is None or epoch > paper_bar['epoch'])):
            paper_bar = {'px': price, 'quote_utc': _iso(epoch), 'epoch': epoch,
                         'price_field': '1m chart close', 'session': 'regular'}
        period = next((name for name in ("pre", "post")
                       if float((periods.get(name) or {}).get("start", 0)) <= epoch
                       < float((periods.get(name) or {}).get("end", 0))), None)
        if period and epoch > regular_epoch and (extended is None or epoch > extended["epoch"]):
            extended = {"px": price, "quote_utc": _iso(epoch), "epoch": epoch,
                        "price_field": "1m chart close", "session": period}
    selected = extended or regular
    # Keep live eligibility distinct; delayed PAPER uses an observed bar, not metadata alone.
    if session == 'regular' and paper_bar is not None:
        selected = paper_bar
    age = max(0.0, now - selected["epoch"])
    current = age <= FRESH_SECONDS
    executable = session == "regular" and selected["session"] == "regular" and 0 <= now-selected['epoch'] <= 60
    paper_executable = session == 'regular' and paper_bar is not None and selected is paper_bar
    status = ("closed_last" if session == "closed" else
              session + ("_current" if current else "_delayed"))
    return {"symbol": symbol, "px": selected["px"], "ccy": ccy,
            "quote_utc": selected["quote_utc"], "fetched_at": fetched_at,
            "session": session, "price_session": selected["session"],
            "status": status, "quote_current": current, "executable": executable,
            "valuation_only": not executable, "age_sec": round(age, 3),
            "paper_executable": paper_executable,
            "paper_price_class": ('DELAYED PAPER' if age > 60 else 'CURRENT PAPER') if paper_executable else None,
            "paper_price_model": 'Observed regular-session 1m close; native registered costs apply' if paper_executable else None,
            "regular": regular, "extended": extended,
            "exchange": meta.get("exchangeName"),
            "exchange_timezone": meta.get("exchangeTimezoneName"),
            "currentTradingPeriod": periods}


def fetch_quote(symbol):
    """Native quote only; six-second request timeout, no ledger writes or FX."""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(symbol, safe="")
           + "?interval=1m&range=1d&includePrePost=true")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=6) as response:
        payload = json.load(response)
    quote = parse_chart(symbol, payload)
    quote.update(source="Yahoo public chart", url=url)
    return quote


def fetch_fx_rates(currencies):
    """Existing book FX conversion, bounded in a child; source time unknown."""
    currencies = sorted(set(currencies) - {"USD"})
    if not currencies:
        return {"USD": 1.0}, {}
    code = ("import json,sys;sys.path.insert(0,sys.argv[1]);"
            "import fundamental_book as fb;"
            "print(json.dumps({c:fb.fx_to_usd(c,throttle=0) for c in json.loads(sys.argv[2])}))")
    try:
        process = subprocess.run([sys.executable, "-c", code, str(BASE), json.dumps(currencies)],
                                 text=True, capture_output=True, timeout=45, check=True)
        raw = json.loads(process.stdout.strip().splitlines()[-1])
    except Exception as exc:
        return {"USD": 1.0}, {c: "FX unavailable: " + str(exc)[:180] for c in currencies}
    rates, failures = {"USD": 1.0}, {}
    for ccy in currencies:
        try:
            rates[ccy] = _positive(raw.get(ccy))
        except (TypeError, ValueError):
            failures[ccy] = "existing FX helper returned no positive rate"
    return rates, failures


def parse_fx_chart(currency, payload, fetched_at=None):
    """USD per native quote unit from an actual timestamped FX bar."""
    fetched_at = fetched_at or utc_now()
    now = dt.datetime.fromisoformat(fetched_at.replace("Z", "+00:00")).timestamp()
    result = payload["chart"]["result"][0]
    if result["meta"].get("currency") != "USD":
        raise ValueError("FX pair must be quoted in USD")
    closes = result["indicators"]["quote"][0].get("close") or []
    bars = []
    for epoch, price in zip(result.get("timestamp") or [], closes):
        try:
            epoch, price = _positive(epoch), _positive(price)
        except (ValueError, TypeError):
            continue
        if epoch <= now:
            bars.append((epoch, price))
    if not bars:
        raise ValueError("no timestamped FX bar")
    epoch, rate = max(bars, key=lambda bar: bar[0])
    if currency in ("GBp", "GBX", "gbp"):
        rate /= 100.0
    return {"rate": rate, "quote_utc": _iso(epoch), "fetched_at": fetched_at,
            "paper_usable": 0 <= now-epoch <= 1200,
            "status": "current" if now - epoch <= FX_FRESH_SECONDS else "stale",
            "source": "Yahoo public 1m FX bar", "age_sec": now - epoch}


# --- FX resilience (Head Trader decision 2026-09-26 #3) -------------------------------------------------
# 2 retries with short backoff, then a per-currency last-good rate. FX moves little intraday relative to a 5% stop,
# so a cached rate is far better than a blocked exit: never block solely for FX when a usable cached rate exists.
FX_RETRY_BACKOFF = (0.5, 1.5)
FX_CACHE_PATH = REPORTS / "fx_last_good.json"
FX_CACHE_MAX_AGE_OPEN = 6 * 3600
FX_CACHE_MAX_AGE_CLOSED = 72 * 3600
_fx_sleep = __import__("time").sleep


def fx_market_closed(when=None):
    """Spot FX weekend (NY Fri 17:00 -> Sun 17:00) or the Dec 25 / Jan 1 holidays."""
    from zoneinfo import ZoneInfo
    ny = (when or dt.datetime.now(UTC)).astimezone(ZoneInfo("America/New_York"))
    if (ny.month, ny.day) in ((12, 25), (1, 1)):
        return True
    wd = ny.weekday()
    return wd == 5 or (wd == 4 and ny.hour >= 17) or (wd == 6 and ny.hour < 17)


def fx_cache_max_age(when=None):
    return FX_CACHE_MAX_AGE_CLOSED if fx_market_closed(when) else FX_CACHE_MAX_AGE_OPEN


def _fx_age(ev, when=None):
    q = dt.datetime.fromisoformat(str(ev["quote_utc"]).replace("Z", "+00:00"))
    return ((when or dt.datetime.now(UTC)) - q).total_seconds()


def _fx_cache_load():
    try:
        with open(FX_CACHE_PATH) as f:
            d = json.load(f)
        return d.get("rates", {}) if isinstance(d, dict) else {}
    except Exception:
        return {}


def _fx_cache_store(updates):
    if not updates:
        return
    try:
        rates = _fx_cache_load()
        rates.update(updates)
        tmp = str(FX_CACHE_PATH) + ".tmp%d" % os.getpid()
        with open(tmp, "w") as f:
            json.dump({"note": "last-good FX per currency (tfo_equity_refresh.fetch_fx_evidence); not a ledger",
                       "saved_utc": utc_now(), "rates": rates}, f, indent=1)
        os.replace(tmp, FX_CACHE_PATH)
    except Exception as exc:
        print("fx cache write failed: %s" % exc, file=sys.stderr)


def _fx_get(url):
    last = None
    for attempt in range(1 + len(FX_RETRY_BACKOFF)):
        if attempt:
            _fx_sleep(FX_RETRY_BACKOFF[attempt - 1])
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=6) as response:
                return json.load(response)
        except Exception as exc:
            last = exc
    raise last


def fetch_fx_evidence(currencies):
    rates = {"USD": {"rate": 1.0, "status": "identity", "source": "USD identity"}}
    failures, majors, good = {}, {}, {}
    cache = None
    for currency in sorted(set(currencies) - {"USD"}):
        major = "GBP" if currency in ("GBp", "GBX", "gbp") else currency.upper()
        if len(major) != 3 or not major.isascii() or not major.isalpha():
            failures[currency] = "unsupported FX currency code"
            continue
        try:
            if major not in majors:
                symbol = major + "USD=X"
                url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
                       + urllib.parse.quote(symbol, safe="") + "?interval=1m&range=1d")
                majors[major] = (_fx_get(url), utc_now(), url)
            payload, fetched_at, url = majors[major]
            ev = dict(parse_fx_chart(currency, payload, fetched_at), url=url)
            if not ev.get("paper_usable") and 0 <= ev["age_sec"] <= fx_cache_max_age():
                ev["paper_usable"] = True           # last bar old but inside the FX cache window (e.g. weekend)
                ev["note"] = "last FX bar %.1fh old; inside FX cache window" % (ev["age_sec"] / 3600)
            rates[currency] = ev
            good[currency] = {k: ev[k] for k in ("rate", "quote_utc", "fetched_at", "source")}
        except Exception as exc:
            if cache is None:
                cache = _fx_cache_load()
            hit = cache.get(currency)
            try:
                age = _fx_age(hit) if hit else None
            except Exception:
                age = None
            limit = fx_cache_max_age()
            if hit and age is not None and 0 <= age <= limit and float(hit.get("rate") or 0) > 0:
                rates[currency] = {"rate": float(hit["rate"]), "quote_utc": hit["quote_utc"],
                                   "fetched_at": hit.get("fetched_at"), "paper_usable": True, "status": "cached",
                                   "source": "last-good cache (%s)" % hit.get("source", "Yahoo"), "age_sec": age,
                                   "note": "live FX failed (%s); cached rate %.1fh old" % (str(exc)[:80], age / 3600)}
                print("%s FX CACHE USED %s rate=%s age=%.1fh (limit %.0fh): %s" % (utc_now(), currency, hit["rate"],
                      age / 3600, limit / 3600, str(exc)[:120]), file=sys.stderr)
            else:
                failures[currency] = "FX unavailable: " + str(exc)[:160] + (
                    "; cache too old (%.1fh)" % (age / 3600) if age is not None else "; no cached rate")
    _fx_cache_store(good)
    return rates, failures


def collect_quotes(symbols, fetcher=None, fx_provider=None):
    if fetcher is None:   # tfo_paidquote_20261030 (Brett 23:13): paid feeds PRIMARY (Massive US / FMP .AX .L .T .HK); Yahoo only as paid_feeds' counted fallback
        try:
            import paid_feeds as _PFQ; fetcher = _PFQ.fetch_quote
        except Exception: fetcher = fetch_quote
    quotes, failures = {}, {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        pending = {pool.submit(fetcher, symbol): symbol for symbol in sorted(set(symbols))}
        for future in concurrent.futures.as_completed(pending):
            symbol = pending[future]
            try:
                quotes[symbol] = future.result()
            except Exception as exc:
                failures[symbol] = type(exc).__name__ + ": " + str(exc)[:200]
    rates, fx_failures = (fx_provider or fetch_fx_evidence)(q["ccy"] for q in quotes.values())
    for symbol, q in quotes.items():
        evidence = rates.get(q["ccy"])
        if isinstance(evidence, dict):
            rate = evidence.get("rate")
            status = evidence.get("status", "source_time_unknown")
        else:
            rate = evidence
            evidence = {}
            status = "identity" if q["ccy"] == "USD" else "source_time_unknown"
        q["fx_usd"] = rate
        q["px_usd"] = q["px"] * rate if rate is not None else None
        q["fx_quote_utc"] = evidence.get("quote_utc")
        q["fx_status"] = status
        q["fx_source"] = evidence.get("source", "unverified FX provider")
        q["fx_timestamp_verified"] = status in ("identity", "current")
        q["executable"] = q.get("executable") is True and q["fx_timestamp_verified"]
        q['paper_executable'] = q.get('paper_executable') is True and (
            q['fx_timestamp_verified'] or evidence.get('paper_usable') is True)
        if q['paper_executable'] and not q['fx_timestamp_verified']:
            q['paper_price_class'] = 'DELAYED PAPER'
        q["valuation_only"] = not q["executable"]
        if rate is None:
            q["fx_status"] = "unavailable"
            failures[symbol] = fx_failures.get(q["ccy"], "FX unavailable")
    return quotes, failures


def _metadata(p, q, checked_at):
    # mark_utc now refers to source time; observation time has its own field.
    from paper_execution_price import eligible
    p.update(mark_utc=q["quote_utc"], mark_quote_utc=q["quote_utc"],
             mark_checked_utc=checked_at, mark_source=q.get("source"),
             mark_source_url=q.get("url"), mark_session=q["session"],
             mark_price_session=q.get("price_session", q["session"]),
             mark_quote_current=q["quote_current"], mark_executable=q["executable"],
             mark_paper_executable=q.get('paper_executable') is True,
             mark_price_class=q.get('paper_price_class'),
             mark_fx_quote_utc=q.get("fx_quote_utc"), mark_fx_status=q.get("fx_status"),
             mark_stale=not eligible(q))
    p["mark_note"] = (q["status"] + "; source " + q["quote_utc"]
                      + ("; valuation only" if not eligible(q) else '; '+str(q.get('paper_price_class') or 'CURRENT PAPER'))
                      + ("; FX source time unknown" if q.get("fx_status") == "source_time_unknown" else ""))
    p.pop("mark_error", None)
    p.pop("mark_failure", None)
    p.pop("mark_rejected", None)


def _flow_mode(risk):
    return "peak_net_flow_usd" in risk or str(risk.get("drawdown_basis") or "").startswith("flow")


def _risk_fields(book, equity, checked_at):
    for key in ("book_risk", "sleeve_risk"):
        risk = book.get(key)
        if not isinstance(risk, dict):
            continue
        if key == "book_risk" and _flow_mode(risk):
            # 2026-09-29 fix (fundamental book false freeze, -57.92% at 28 Sep 15:29 AEST): this refresh used to raise
            # peak_equity to RAW equity without moving peak_net_flow_usd, so book_kill.evaluate (flow-adjusted) paired
            # a peak inflated by Head Trader buy inflows with a stale flow baseline and read the inflows as losses.
            # Same arithmetic as book_kill.evaluate: drawdown = (equity - flows since peak) / peak - 1, and a new
            # peak resets the flow baseline with it.
            import book_kill
            flow = float(book_kill.net_capital_flow(book))
            peak = float(risk.get("peak_equity") or equity)
            pf = risk.get("peak_net_flow_usd")
            pf = float(pf) if isinstance(pf, (int, float)) else flow
            adj = equity - (flow - pf)
            if adj > peak:
                peak, pf, adj = float(equity), flow, float(equity)
                risk["peak_at_utc"] = checked_at
            dd = (adj / peak - 1) * 100 if peak else 0.0
            risk.update(equity=round(equity, 2), peak_equity=round(peak, 2), peak_net_flow_usd=round(pf, 2),
                        net_flow_usd=round(flow, 2), drawdown_pct=round(dd, 4), checked_utc=checked_at)
        else:
            peak = max(float(risk.get("peak_equity") or equity), equity)
            dd = (equity / peak - 1) * 100 if peak else 0.0
            risk.update(equity=round(equity, 2), peak_equity=peak,
                        drawdown_pct=round(dd, 4), checked_utc=checked_at)
        threshold = risk.get("kill_drawdown_pct")
        if threshold is not None and dd <= float(threshold):
            risk["new_entries_frozen" if key == "sleeve_risk" else "frozen"] = True
    if "peak_equity" in book and "peak_net_flow_usd" in book:
        # 2026-09-29 23:59 AEST HT fix (GCL false sleeve kill): the top-level pair (peak_equity, peak_net_flow_usd) is
        # flow-adjusted (gap_close_levered_book._mark). Raising peak_equity to RAW equity after powder inflows (London sells
        # 17:38) without moving the flow baseline made _mark read -42.9% and set frozen. Same arithmetic as the book_risk
        # path above; a new peak resets the flow baseline with it. The book's own flow definition is used.
        flow = float(_top_level_flow(book))
        peak = float(book.get("peak_equity") or equity)
        pf = book.get("peak_net_flow_usd")
        pf = float(pf) if isinstance(pf, (int, float)) else flow
        adj = equity - (flow - pf)
        if adj > peak:
            peak, pf, adj = float(equity), flow, float(equity)
        book.update(peak_equity=round(peak, 2), peak_net_flow_usd=round(pf, 2), net_flow_usd=round(flow, 2),
                    drawdown_pct=round((adj / peak - 1) * 100, 4) if peak else 0.0)
        return
    for key in ("peak_equity", "peak"):
        if key in book:
            book[key] = max(float(book[key] or equity), equity)
            book["drawdown_pct"] = round((equity / book[key] - 1) * 100, 4) if book[key] else 0.0


def _top_level_flow(book):
    """Signed net capital flow using the book's OWN definition (GCL counts BANK_IN/withdrawal/absorb notes too)."""
    if book.get("book_key") == "gap_close_levered_book" or book.get("name") == "gap_close_levered_book":
        import gap_close_levered_book as gcl
        return gcl.net_flow(book)
    import book_kill
    return book_kill.net_capital_flow(book)


GUARDED_BOOKS = ("fundamental_book", "fundamental_spec_book", "convexity_sleeve")   # V1 position books


def _guard(name, p, q):
    """-> (reason|None, split_like_ratio). Per-share comparison (convexity mark_usd is the position total)."""
    if name not in GUARDED_BOOKS:
        return None, 0
    try:
        import bad_tick_guard as btg
        units = float(p.get("units") or 0)
        if name == "convexity_sleeve":
            last = float(p["mark_usd"]) / units if units and _positive(p.get("mark_usd")) else None
            e0 = p.get("rail_orig_entry_usd") or (float(p["premium_usd"]) / units if units and p.get("premium_usd") else None)
        else:
            last, e0 = p.get("mark_usd"), p.get("rail_orig_entry_usd") or p.get("entry_px_usd")
        why, _ref, ratio = btg.mark_check(name, q["px_usd"], p.get("side", "long"), last, e0)
        return why, ratio
    except Exception:                                   # noqa: BLE001 -- guard must never break valuation
        return None, 0


def value_book(name, original, quotes, failures, checked_at):
    """Pure mark transformation; returns a new book and an evidence receipt."""
    book = copy.deepcopy(original)
    before = book.get("equity")
    refreshed, unavailable, rejected = [], [], []
    failures = dict(failures)
    positions = book.get("positions") or {}
    for symbol, p in positions.items():
        ticker = p.get("mark_ticker") or symbol
        q = quotes.get(ticker)
        if not q or q.get("px_usd") is None:
            p.update(mark_stale=True, mark_executable=False, mark_checked_utc=checked_at,
                     mark_quote_current=False, mark_error=failures.get(ticker, "quote unavailable"),
                     mark_note="quote unavailable; carrying last sourced mark")
            unavailable.append(symbol)
            continue
        why, ratio = _guard(name, p, q)
        if why:
            # 2026-09-29 bad-tick guard (8766.T): never write a >30% one-print move into a V1 position's mark; carry
            # the last good mark, flag it, count the position unavailable. Split-like ratios -> corporate-actions check.
            import bad_tick_guard as btg
            btg.flag_rejected(p, q.get("px"), q["px_usd"], why, ratio, checked_at)
            p.update(mark_executable=False, mark_checked_utc=checked_at, mark_quote_current=False,
                     mark_note="bad tick rejected; carrying last good mark")
            failures[symbol] = why
            unavailable.append(symbol)
            rejected.append({"book": name, "sym": symbol, "reason": why, "split_like_ratio": ratio})
            continue
        p["mark"] = q["px"]
        p["mark_fx_usd"] = q["fx_usd"]
        p["mark_usd"] = (round(q["px_usd"] * float(p["units"]), 2)
                         if name == "convexity_sleeve" else q["px_usd"])
        _metadata(p, q, checked_at)
        refreshed.append(symbol)
    if name in ("fundamental_book", "fundamental_spec_book"):
        long_mv = short_mv = short_basis = unreal = 0.0
        for symbol, p in positions.items():
            mv = float(p["units"]) * _positive(p.get("mark_usd"))
            # Spec exits retain original notional after selling a tranche;
            # only the remaining units' cost belongs in unrealised P&L.
            # Main-book exits reduce notional, including short collateral.
            if name == "fundamental_spec_book":
                basis = float(p["entry_px_usd"]) * float(p["units"])
            else:
                basis = float(p["notional_usd"] if "notional_usd" in p else
                              float(p["entry_px_usd"]) * float(p["units"]))
            if p.get("side", "long") == "short":
                short_mv += mv
                short_basis += basis
                pnl = basis - mv
            else:
                long_mv += mv
                pnl = mv - basis
            p["unrealised_usd"] = round(pnl, 2)
            unreal += pnl
        equity = float(book["cash"]) + long_mv + short_basis + short_basis - short_mv
        book.update(long_mv_usd=round(long_mv, 2), unrealised_usd=round(unreal, 2),
                    n_unmarked=len(unavailable), unmarked_usd=round(sum(
                        float(positions[s].get("notional_usd", 0)) for s in unavailable), 2),
                    price_failures={s: failures.get(s, "unavailable") for s in unavailable})
        if name == "fundamental_book":
            book.update(short_mv_usd=round(short_mv, 2), short_notional_usd=round(short_basis, 2),
                        n_long=sum(p.get("side") == "long" for p in positions.values()),
                        n_short=sum(p.get("side") == "short" for p in positions.values()))
            # Same identity as the canonical writer (fundamental_book.mark, CM-054): realised P&L on forward
            # short receipts already includes borrow charged to cash (borrow_released_cash_correct_usd), so it is
            # added back. 2026-09-28: omitting it left a spurious ~$6 identity gap after every mark refresh.
            borrow_in_realised = sum(float(p.get("borrow_released_cash_correct_usd") or 0)
                                     for group in ("positions", "closed")
                                     for p in (book.get(group) or {}).values() if isinstance(p, dict))
            identity = (float(book["seed_usd"]) + float(book.get("realised_usd", 0)) + borrow_in_realised
                        - float(book.get("costs_paid_usd", 0)) + unreal
                        + float(book.get("cash_interest_accrued", 0)))
            book.update(equity_identity_usd=round(identity, 2),
                        equity_identity_gap_usd=round(equity - identity, 2))
        else:
            book["n_positions"] = len(positions)
    elif name == "convexity_sleeve":
        equity = float(book["cash"]) + sum(float(p["mark_usd"]) for p in positions.values())
        for p in positions.values():
            p["unrealised_usd"] = round(float(p["mark_usd"]) - float(p["premium_usd"]), 2)
        book["unrealised_usd"] = round(sum(p["unrealised_usd"] for p in positions.values()), 2)
    else:
        holdings = book.get("holdings") or {}
        marks = dict(book.get("mark_px_usd") or {})
        for symbol in holdings:
            q = quotes.get(symbol)
            if q and q.get("px_usd") is not None:
                marks[symbol] = q["px_usd"]
                refreshed.append(symbol)
            else:
                unavailable.append(symbol)
        if any(symbol not in marks for symbol in holdings):
            raise ValueError(name + ": cannot fully value holdings; refusing partial total")
        equity = float(book["cash"]) + sum(float(units) * _positive(marks[s]) for s, units in holdings.items())
        if holdings:
            book["mark_px_usd"] = marks
            book["last_mark_px"] = dict(marks)
            book["last_mark_usd"] = round(equity, 2)
            if isinstance(original.get("last_mark"), (int, float)) and not isinstance(original.get("last_mark"), bool):
                book["last_mark"] = equity
            book["last_mark_checked_utc"] = checked_at
            own_quotes = {s: quotes[s] for s in holdings if s in quotes}
            book["mark_quotes"] = own_quotes
            if len(own_quotes) == len(holdings):
                oldest = min(q["quote_utc"] for q in own_quotes.values())
                book["last_mark_utc"] = oldest
                book["last_mark_asof"] = oldest
            book["mark_stale"] = bool(unavailable) or any(not q["executable"] for q in own_quotes.values())
            basis = float(book.get("deploy_basis_usd") or 0)
            if basis:
                book["net_since_deploy_usd"] = round(equity - basis, 2)
                book["pct_net_since_deploy"] = (equity / basis - 1) * 100
                book["last_mark_pct_net_since_deploy"] = book["pct_net_since_deploy"]
            book["last_mark_routine"] = "tfo_equity_refresh: valuation only"
    book["equity"] = round(equity, 2)
    book["updated"] = checked_at
    book["mark_checked_utc"] = checked_at
    if name == "convexity_sleeve":
        book["updated_utc"] = checked_at
    if name == "value_book" and not book.get("holdings"):
        book.update(last_valued_equity=round(equity, 2), last_valued_at=checked_at,
                    equity_status="ok: flat book equals cash")
    _risk_fields(book, equity, checked_at)
    assert_preserved(original, book)
    receipt = {"equity_before": before, "equity_after": book["equity"],
                  "refreshed": refreshed, "unavailable": unavailable,
                  "position_count": len(positions or book.get("holdings") or {}),
                  "cash_preserved": book.get("cash") == original.get("cash"),
                  "flat_cash_equity_repair": name == "value_book" and not book.get("holdings") and before != book["equity"],
                  "mark_rejected": rejected}
    if receipt["flat_cash_equity_repair"]:
        receipt["accounting_repair"] = {"classification": "stale derived equity corrected; no trading gain or cash flow",
                                        "evidence": "No holdings; equity must equal unchanged recorded cash.",
                                        "recorded_cash": book["cash"], "prior_equity": before,
                                        "corrected_equity": book["equity"]}
    return book, receipt


def assert_preserved(before, after):
    """Fail before writing if economic history/size changed unexpectedly."""
    protected = ("cash", "seed_usd", "start", "contributed_capital_usd", "realised_usd",
                 "costs_paid_usd", "costs_paid", "closed", "capital_events", "events",
                 "deployments", "holdings", "entry_px", "entry_px_usd", "deploy_basis_usd",
                 "cash_interest_accrued", "trade_fills_this_tick", "trade_decisions")
    for key in protected:
        if before.get(key) != after.get(key):
            raise AssertionError("mark-only invariant changed: " + key)
    if set(before.get("positions", {})) != set(after.get("positions", {})):
        raise AssertionError("positions changed")
    for symbol, p in (before.get("positions") or {}).items():
        for key, value in p.items():
            if key.startswith("entry") or key in ("units", "side", "notional_usd", "premium_usd",
                                                   "exit_fills", "released_fraction", "borrow_accrued_usd"):
                if after["positions"][symbol].get(key) != value:
                    raise AssertionError("position invariant changed: " + symbol + "/" + key)


def atomic_json(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextlib.contextmanager
def writer_locks(reports=REPORTS, extra_locks=()):
    """Coordinate hourly desk, fundamental refresh/entries and bank scanner.
    extra_locks (2026-09-28): further desk_lock names taken AFTER the fundamental locks, in order
    (book locks before the powder lock, matching tfo_ticket/tfo_buy/tfo_crypto)."""
    import desk_lock
    tick = Path(reports) / ".tick.lock"
    try:
        tick.mkdir()
    except FileExistsError:
        raise RuntimeError("desk tick is running; retry after it completes") from None
    try:
        with contextlib.ExitStack() as stack:
            stack.enter_context(desk_lock.ledger_lock("fundamental", timeout=60))
            stack.enter_context(desk_lock.ledger_lock("fundamental_spec", timeout=60))
            for name in extra_locks:
                stack.enter_context(desk_lock.ledger_lock(name, timeout=60))
            scanner = stack.enter_context(open(Path(reports) / ".tfo_bank_rail_scanner.lock", "a"))
            try:
                fcntl.flock(scanner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("bank scanner is running; retry after it completes") from None
            yield
    finally:
        tick.rmdir()


def refresh(write=False, reports=REPORTS, fetcher=None, fx_provider=None, extra_locks=(), backup_prefix="equity-refresh-"):
    reports = Path(reports)
    lock = writer_locks(reports, extra_locks) if write else contextlib.nullcontext()
    with lock:
        originals, source_bytes = {}, {}
        for name in BOOKS:
            path = reports / (name + ".json")
            if path.exists():
                source_bytes[name] = path.read_bytes()
                originals[name] = json.loads(source_bytes[name])
        symbols = []
        for book in originals.values():
            symbols.extend(p.get("mark_ticker") or s for s, p in (book.get("positions") or {}).items())
            symbols.extend(book.get("holdings") or {})
        quotes, failures = collect_quotes(symbols, fetcher, fx_provider)
        checked_at = utc_now()
        updated, receipts = {}, {}
        for name, original in originals.items():
            updated[name], receipts[name] = value_book(name, original, quotes, failures, checked_at)
        report = {"checked_at": checked_at, "paper_only": True, "valuation_only": True,
                  "writes_applied": False, "quotes": quotes, "books": receipts, "failures": failures,
                  "policy": "Latest timestamped regular or extended-hours mark; no fills, flows or entry edits."}
        if write:
            # Detect an uncooperative writer before any write; locks remain held.
            for name, raw in source_bytes.items():
                if (reports / (name + ".json")).read_bytes() != raw:
                    raise RuntimeError("ledger changed despite locks: " + name)
            tag = dt.datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            backup = reports / "revisions" / "rollback" / (backup_prefix + tag)
            backup.mkdir(parents=True)
            for name in originals:
                shutil.copy2(reports / (name + ".json"), backup / (name + ".json"))
            report["backup"] = str(backup)
            report["before_sha256"] = {name: hashlib.sha256(raw).hexdigest() for name, raw in source_bytes.items()}
            atomic_json(backup / "receipt.json", report)
            for name, book in updated.items():
                atomic_json(reports / (name + ".json"), book)
            report["writes_applied"] = True
            try:                                     # split-like rejected prints -> corporate-actions check next run
                import bad_tick_guard as btg
                for rec in receipts.values():
                    for rj in rec.get("mark_rejected") or []:
                        if rj.get("split_like_ratio"):
                            btg.request_ca_check(rj["book"], rj["sym"], rj["reason"], rj["split_like_ratio"],
                                                 path=str(reports / "corporate_actions_requests.json"))
            except Exception:                        # noqa: BLE001
                pass
        atomic_json(reports / "price_refresh_equities_latest.json", report)
        return report


def prune_backups(prefix, keep, reports=REPORTS):
    """Bound the rolling AUTO mark-refresh backups (2026-09-28). Only dirs named <prefix>* are touched;
    manual 'equity-refresh-<tag>' backups are never pruned. Returns the number removed."""
    if not prefix or prefix == "equity-refresh-" or keep < 1:
        return 0
    root = Path(reports) / "revisions" / "rollback"
    dirs = sorted(d for d in root.glob(prefix + "*") if d.is_dir())
    removed = 0
    for d in dirs[:-keep]:
        shutil.rmtree(d, ignore_errors=True)
        removed += 1
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="apply marks to existing PAPER ledgers")
    args = parser.parse_args()
    result = refresh(write=args.write)
    print(json.dumps({"checked_at": result["checked_at"], "writes_applied": result["writes_applied"],
                      "quotes": len(result["quotes"]), "failures": result["failures"],
                      "books": result["books"], "backup": result.get("backup")}, indent=2))


if __name__ == "__main__":
    main()
