#!/usr/bin/env python3
"""All-feeds data-latency check. PAPER ONLY. Times each feed and writes reports/feed_speed.json.
Keys are read only through the existing loaders (never printed). Verdicts know market hours:
EODHD's 15-minute delay is normal; FMP going stale after the US close is normal.
  python3 tools/feed_speed.py
"""
import datetime as dt, json, os, sys, time, urllib.request
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, BASE)
UTC = dt.timezone.utc
OUT = os.path.join(BASE, "reports", "feed_speed.json")
# response slower than this is amber even when the payload is fresh
SLOW_S = 8.0
def _now(now=None): return now or dt.datetime.now(UTC)
def us_open(now=None):
    """NYSE regular session, weekdays, holidays not modelled (a holiday reads as amber, not a false red)."""
    from zoneinfo import ZoneInfo
    ny = _now(now).astimezone(ZoneInfo("America/New_York"))
    if ny.weekday() >= 5: return False
    m = ny.hour * 60 + ny.minute
    return 570 <= m < 960
def judge(name, ok, elapsed_s=None, age_min=None, market_open=None, expected_delay_min=0, after_close_ok=False, key_present=True, err=None):
    """green / amber / red for one feed. Does not change any trading rule."""
    market_open = us_open() if market_open is None else market_open
    row = dict(feed=name, ok=bool(ok), elapsed_s=None if elapsed_s is None else round(float(elapsed_s), 3),
               age_min=None if age_min is None else round(float(age_min), 1), market_open=bool(market_open),
               expected_delay_min=expected_delay_min, key_present=bool(key_present), err=(str(err)[:160] if err else None))
    if not key_present:
        row.update(verdict="amber", why="no key in the existing env/config loader — not probed")
        return row
    if not ok:
        row.update(verdict="red", why=row["err"] or "no response")
        return row
    slow = elapsed_s is not None and elapsed_s > SLOW_S
    fresh_limit = expected_delay_min + 10
    stale = age_min is not None and age_min > fresh_limit
    if stale and not market_open and after_close_ok:
        row.update(verdict="green", why="stale after the close is normal")
        return row
    if stale and not market_open:
        row.update(verdict="amber", why="stale while the US session is closed")
        return row
    if stale and market_open:
        row.update(verdict="red", why="stale by %.0f min during the session (expected delay %s min)" % (age_min, expected_delay_min))
        return row
    if slow:
        row.update(verdict="amber", why="fresh but slow (%.1fs)" % elapsed_s)
        return row
    row.update(verdict="green", why="fresh" + (" (delay up to %s min is normal)" % expected_delay_min if expected_delay_min else ""))
    return row
def _time(fn):
    t0 = time.time()
    try:
        v = fn()
        return True, time.time() - t0, v, None
    except Exception as e:
        return False, time.time() - t0, None, type(e).__name__ + ": " + str(e)[:120]
def _age_min(ts, now=None):
    if not ts: return None
    try:
        t = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if t.tzinfo is None: t = t.replace(tzinfo=UTC)
        return (_now(now) - t.astimezone(UTC)).total_seconds() / 60
    except Exception: return None
def probe_all(now=None, get=None):
    """Probe every feed. `get` replaces HTTP for tests (callable url -> body). Live calls use the real loaders."""
    now = _now(now); open_ = us_open(now); rows = []
    # Massive / Polygon + Benzinga (same key, veteran_desk.massive)
    try:
        from veteran_desk import massive as MS
        key = bool(MS._key())
    except Exception as e:
        MS = None; key = False
    if MS and key and get is None:
        ok, el, q, err = _time(lambda: MS.quote("AAPL", now_utc=now))
        rows.append(judge("massive_polygon", ok and bool(q), el, _age_min((q or {}).get("asof"), now) if q else None, open_, err=err, key_present=True))
        ok, el, n, err = _time(lambda: MS.news(["AAPL"], limit=3))
        rows.append(judge("benzinga", ok and bool(n), el, _age_min((n or [{}])[0].get("published_utc"), now) if n else None, open_, err=err if not n else None, key_present=True))
    else:
        rows.append(judge("massive_polygon", False, key_present=key, err=None if key else "key missing"))
        rows.append(judge("benzinga", False, key_present=key, err=None if key else "key missing"))
    # EODHD — 15 min delay is the contract
    try:
        from veteran_desk import data_layer as DL
        ekey = bool(DL._key())
    except Exception:
        DL = None; ekey = False
    if DL and ekey and get is None:
        ok, el, q, err = _time(lambda: DL.eodhd_quote("AAPL", now_utc=now, max_age_min=10 ** 6))
        rows.append(judge("eodhd", ok and bool(q), el, (q or {}).get("age_min"), open_, expected_delay_min=15, err=err, key_present=True))
    else:
        rows.append(judge("eodhd", False, key_present=ekey, expected_delay_min=15, err=None if ekey else "key missing"))
    # FMP — stale after the close is normal
    try:
        from veteran_desk import thf_paid_pack as TP
        fkey = bool(TP._key("fmp", "FMP_API_KEY"))
    except Exception:
        TP = None; fkey = False
    if TP and fkey and get is None:
        ok, el, q, err = _time(TP.fmp_movers)
        rows.append(judge("fmp", ok and bool(q), el, None, open_, after_close_ok=True, err=err, key_present=True))
    else:
        rows.append(judge("fmp", False, key_present=fkey, after_close_ok=True, market_open=open_, err=None if fkey else "key missing"))
    # Unusual Whales
    try:
        from veteran_desk import thf_paid_pack as TP2
        ukey = bool(TP2._key("unusual_whales", "UNUSUAL_WHALES_API_KEY"))
    except Exception:
        TP2 = None; ukey = False
    if TP2 and ukey and get is None:
        ok, el, q, err = _time(TP2.uw_flow)
        rows.append(judge("unusual_whales", ok and bool(q), el, None, open_, err=err, key_present=True))
    else:
        rows.append(judge("unusual_whales", False, key_present=ukey, err=None if ukey else "key missing"))
    # Coinalyze
    try:
        from veteran_desk import thf_paid_pack as TP3
        ckey = bool(TP3.coinalyze_key())
    except Exception:
        TP3 = None; ckey = False
    if TP3 and ckey and get is None:
        ok, el, q, err = _time(TP3.coinalyze_derivs)
        rows.append(judge("coinalyze", ok and bool(q), el, None, open_, err=err, key_present=True))
    else:
        rows.append(judge("coinalyze", False, key_present=ckey, err=None if ckey else "key missing"))
    # Yahoo (no key) — chart v8 via pit
    if get is None:
        from veteran_desk import pit as P
        ok, el, rows_y, err = _time(lambda: P.yahoo_chart("AAPL", "5d"))
        rows.append(judge("yahoo", ok and bool(rows_y), el, None, open_, err=err))
    else:
        rows.append(judge("yahoo", True, 0.1, None, open_))
    # OANDA practice ONLY
    try:
        import oanda_sleeve as OA
        okey = bool(OA.token())
    except Exception:
        OA = None; okey = False
    if OA and okey and get is None:
        def _oa():
            assert OA.HOST.startswith("https://api-fxpractice."), "practice host only"
            req = urllib.request.Request(OA.HOST + OA.acct("/summary"), headers={"Authorization": "Bearer " + OA.token(), "User-Agent": "TFO paper"})
            with urllib.request.urlopen(req, timeout=12) as f: return json.loads(f.read())
        ok, el, q, err = _time(_oa)
        rows.append(judge("oanda_practice", ok and isinstance(q, dict), el, None, open_, err=err, key_present=True))
    else:
        rows.append(judge("oanda_practice", False, key_present=okey, err=None if okey else "key missing"))
    # CoinGecko
    if get is None:
        from veteran_desk import feeds as FD
        ok, el, q, err = _time(lambda: FD.coingecko_global())
        rows.append(judge("coingecko", ok and bool(q), el, None, open_, err=err))
    else:
        rows.append(judge("coingecko", True, 0.2, None, open_))
    # Kraken REST order book + a short public websocket handshake
    if get is None:
        def _book():
            req = urllib.request.Request("https://api.kraken.com/0/public/Depth?pair=XBTUSD&count=5", headers={"User-Agent": "TFO paper"})
            with urllib.request.urlopen(req, timeout=12) as f: return json.loads(f.read())
        ok, el, q, err = _time(_book)
        rows.append(judge("kraken_order_book", ok and bool((q or {}).get("result")), el, None, True, err=err))
        try:
            import websocket
            def _ws():
                ws = websocket.create_connection("wss://ws.kraken.com/", timeout=8)
                ws.send(json.dumps(dict(event="subscribe", pair=["XBT/USD"], subscription=dict(name="book", depth=10))))
                raw = ws.recv(); ws.close(); return raw
            ok, el, raw, err = _time(_ws)
            rows.append(judge("kraken_websocket", ok and bool(raw), el, None, True, err=err))
        except ImportError:
            rows.append(dict(feed="kraken_websocket", ok=False, verdict="amber", why="websocket client not installed; REST order book is the timed check", elapsed_s=None, age_min=None, market_open=True, expected_delay_min=0, key_present=True, err=None))
    else:
        rows.append(judge("kraken_order_book", True, 0.2, None, True))
        rows.append(judge("kraken_websocket", True, 0.3, None, True))
    # Fed RSS
    if get is None:
        from veteran_desk import feeds as FD2
        ok, el, items, err = _time(lambda: FD2.rss("fed_press"))
        rows.append(judge("fed_rss", ok and bool(items), el, None, open_, err=err))
    else:
        rows.append(judge("fed_rss", True, 0.2, None, open_))
    red = sum(1 for r in rows if r["verdict"] == "red"); amber = sum(1 for r in rows if r["verdict"] == "amber")
    return dict(utc=now.isoformat(), paper_only=True, us_session_open=open_, feeds=rows,
                summary=dict(n=len(rows), green=sum(1 for r in rows if r["verdict"] == "green"), amber=amber, red=red),
                note="EODHD 15 min delay is expected. FMP stale after the US close is normal. Keys are never written here.")
def main():
    rep = probe_all(); os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"; json.dump(rep, open(tmp, "w"), indent=1); os.replace(tmp, OUT)
    print(json.dumps(dict(rep["summary"], path="reports/feed_speed.json", us_session_open=rep["us_session_open"]), indent=1))
    return rep
if __name__ == "__main__": main()
