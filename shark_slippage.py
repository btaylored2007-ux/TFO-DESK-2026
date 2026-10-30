#!/usr/bin/env python3
"""Shark slippage logger (2026-10-02, edge_tests_cost_jp_spin_20261002.md step 2). LOGGING ONLY. PAPER.

Every Head Trader BUY (tfo_buy.execute) and rail exit (tfo_ticket.execute: HALF/STOP/FLAT/...) appends one row to
logs/shark_slippage.csv with the live top-of-book read at order time next to the paper fill, so the next ~30 shark trades
give a measured spread/slippage instead of the 10bp model assumption.

Hard rules:
  * called AFTER the ledger has been saved; it never changes a price, size, gate, fee, ledger or ticket;
  * never raises (every failure becomes a row with quote_source=unavailable and an error note);
  * bounded: one quote read per fill (Nasdaq timeout 4s; Alpaca 4s).
Quote sources, in order:
  1. Alpaca IEX latest quote (feed=iex) ONLY if APCA_API_KEY_ID/APCA_API_SECRET_KEY are already in the environment
     (keys are never read from files, printed or logged);
  2. quote_check.nasdaq_quote: the desk's existing primary US bid/ask source (Nasdaq Basic real-time top of book);
  3. non-US names: Yahoo .info bid/ask (the desk's non-US source), validated by quote_check.check.
Columns: slip_vs_mid_bp is the signed cost vs the quote mid (+ = worse than mid: paid above mid on a buy, received below
mid on a sell; shorts mirror). Paper fills are idealised, so the spread columns are the real evidence.
"""
import csv, datetime as dt, json, os, time, urllib.request
# TFO DoH hook 2026-10-28 (Brett item B4: desk_doh process-local DoH for IBKR + Binance/Coinbase/OKX/Alpaca/Polymarket; no system DNS; kill switch TFO_DESK_DOH=0)
try:
    import desk_doh as _doh; _doh.install(extra=True)
except Exception as _e:
    print("desk_doh install skipped:", _e, file=__import__("sys").stderr)

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE, "logs", "shark_slippage.csv")
DEFAULT_LEDGER_LOG = os.path.join(BASE, "reports", "tfo_head_trader_log.txt")
ENABLED = True
ALPACA_URL = "https://data.alpaca.markets/v2/stocks/%s/quotes/latest?feed=iex"
FIELDS = ["utc", "event", "book", "sym", "side", "qty", "fill_px_native", "fill_px_usd", "ccy", "tape", "ticket_id", "exit_variant",
          "exit_rule", "ht_clip", "quote_source", "quote_ts", "bid", "ask", "mid", "spread_bp", "half_spread_bp", "quote_consistent",
          "slip_vs_mid_bp", "quote_read_ms", "note"]


def _alpaca_iex(sym):
    kid, sec = os.environ.get("APCA_API_KEY_ID"), os.environ.get("APCA_API_SECRET_KEY")
    if not (kid and sec) or not sym.replace(".", "").isalpha() or "." in sym: return None
    req = urllib.request.Request(ALPACA_URL % sym, headers={"APCA-API-KEY-ID": kid, "APCA-API-SECRET-KEY": sec})
    with urllib.request.urlopen(req, timeout=4) as r:
        q = json.load(r).get("quote") or {}
    if not (q.get("bp") and q.get("ap")): return None
    return {"bid": float(q["bp"]), "ask": float(q["ap"]), "ts": q.get("t"), "source": "alpaca_iex"}


def _nasdaq(sym):
    import quote_check
    q = quote_check.nasdaq_quote(sym)
    return {"bid": q["bid"], "ask": q["ask"], "ts": q.get("ts"), "source": "nasdaq", "last": q.get("last")} if q else None


def _yahoo(sym):
    import yfinance as yf
    inf = yf.Ticker(sym).info
    b, a = float(inf.get("bid") or 0), float(inf.get("ask") or 0)
    return {"bid": b, "ask": a, "ts": None, "source": "yahoo_info"} if (b > 0 and a > 0) else None


def read_quote(sym):
    """First available live quote -> dict(bid, ask, ts, source) or None; errors collected, never raised."""
    errs = []
    for f in (_alpaca_iex, _nasdaq, _yahoo):
        try:
            q = f(sym)
            if q: return q, errs
        except Exception as e:
            errs.append("%s: %s" % (f.__name__.strip("_"), str(e)[:80]))
    return None, errs


def _path_for(ledger_log):
    """Live runs write logs/shark_slippage.csv. When a caller's ledger LOG points elsewhere (offline tests patch it into a
    temp dir) the row goes next to that LOG instead, so tests never touch the live log."""
    try:
        if ledger_log and os.path.realpath(ledger_log) != os.path.realpath(DEFAULT_LEDGER_LOG):
            return os.path.join(os.path.dirname(ledger_log), "shark_slippage.csv"), False
    except Exception:
        pass
    return LOG_PATH, True


def record(event, book, sym, side, qty, q, ticket=None, ledger_log=None, quote_reader=None, now=None):
    """Append one slippage row. Never raises. Returns the row dict (or None if disabled)."""
    if not ENABLED: return None
    try:
        path, live = _path_for(ledger_log)
        reader = quote_reader or (read_quote if live else None)    # offline callers get no network unless they inject a reader
        tk = ticket or {}; q = q or {}
        fill_native = q.get("px") if q.get("px") is not None else q.get("px_usd")
        row = {"utc": (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "event": event, "book": book, "sym": sym,
               "side": side, "qty": qty, "fill_px_native": fill_native, "fill_px_usd": q.get("px_usd"), "ccy": q.get("ccy"), "tape": q.get("tape"),
               "ticket_id": tk.get("ticket_id"), "exit_variant": tk.get("exit_variant"), "exit_rule": tk.get("exit_rule"), "ht_clip": tk.get("ht_clip", True),
               "quote_source": "unavailable", "note": ""}
        if reader is None:
            row["note"] = "offline (non-live ledger log): quote not read"
        else:
            t0 = time.monotonic(); qq, errs = reader(sym); row["quote_read_ms"] = int(1000 * (time.monotonic() - t0))
            if errs: row["note"] = "; ".join(errs)[:300]
            if qq:
                b, a = float(qq["bid"]), float(qq["ask"]); mid = (b + a) / 2.0
                row.update(quote_source=qq.get("source"), quote_ts=qq.get("ts"), bid=b, ask=a, mid=round(mid, 6))
                if mid > 0:
                    row["spread_bp"] = round((a - b) / mid * 1e4, 2); row["half_spread_bp"] = round((a - b) / mid * 5e3, 2)
                    try:
                        import quote_check
                        row["quote_consistent"] = quote_check.check(b, a, float(fill_native))[0]
                    except Exception:
                        row["quote_consistent"] = None
                    if fill_native:
                        buyish = (event == "BUY") == (side != "short")         # buy-to-open long / buy-to-cover short
                        s = (float(fill_native) / mid - 1) * 1e4
                        row["slip_vs_mid_bp"] = round(s if buyish else -s, 2)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new = not os.path.exists(path)
        with open(path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            if new: w.writeheader()
            w.writerow(row)
        return row
    except Exception as e:
        try: print("SHARK-SLIPPAGE log unavailable: %s" % str(e)[:160])
        except Exception: pass
        return None
