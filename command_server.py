#!/usr/bin/env python3
"""Command dashboard server — READ-ONLY aggregator across the whole floor.

Pulls together every desk on this machine into one view:
  * Kraken paper engine (:18799)        live book, P&L, risk state, log
  * Kraken multi-desk floor             mean-reversion / breakout / micro-cap
  * eToro paper replica                 same strategies at eToro's costs
  * Research outputs                    cost wall, holding-period lab
  * Desk health                         from desk_healer

Strictly read-only. It performs GET against the engine and reads files. It never
sends a control action, never writes engine state, never places a trade. If this
process dies, every desk is entirely unaffected.

Binds 127.0.0.1 only.

Usage:  python3 command_server.py [--port 18811]
"""
try:  # 2026-10-27 WITA Brett: ISP hijacks port-53 DNS for Kraken -> scoped DoH for Kraken public hosts only (kill: state/entry_engine/KRAKEN_DOH_OFF)
    import os as _kd_os, sys as _kd_sys
    _kd_sys.path.append(_kd_os.path.normpath(_kd_os.path.join(_kd_os.path.dirname(_kd_os.path.abspath(__file__)), '.')))
    import kraken_dns as _kraken_dns; _kraken_dns.install()
except Exception as _kd_e:  # pragma: no cover
    print("kraken_dns unavailable: %r" % _kd_e, file=__import__("sys").stderr)
import argparse
import csv
import json
import math
import os
import subprocess
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
KRAKEN_DESK = "/Users/test/Applications/KrakenQuantDesk"
PAGE = os.path.join(BASE, "command_dashboard.html")
PRIVATE_WEALTH_PAGE = os.path.join(BASE, "private_wealth_dashboard.html")
CRYPTO20_FWD_DIR = os.path.join(BASE, "research", "crypto20", "fwd")
if CRYPTO20_FWD_DIR not in sys.path:
    sys.path.insert(0, CRYPTO20_FWD_DIR)

ENGINE_PORT = 18799
FLOOR_LEDGER = os.path.join(KRAKEN_DESK, "kraken_bot", "kraken_floor.json")
REPLICA_LEDGER = os.path.join(BASE, "replica_floor.json")
# CoinSpot (AU) paper floor — same Kraken floor algo on Australian retail
# execution; PAPER ONLY, public market data only, AUD-quoted. Lives in the
# Kraken desk's reports dir (created 2026-08-08).
COINSPOT_LEDGER = os.path.join(KRAKEN_DESK, "reports", "coinspot_floor.json")
EQUITY_CSV = os.path.join(KRAKEN_DESK, "kraken_bot", "kraken_equity.csv")
FIRM_EQUITY_CSV = os.path.join(BASE, "reports", "firm_equity.csv")
TRADES_CSV = os.path.join(KRAKEN_DESK, "kraken_bot", "kraken_trades.csv")
HEALTH_JSON = os.path.join(BASE, "reports", "desk_health.json")
BOOT_REPORT = os.path.join(BASE, "reports", "boot_report.json")
COST_WALL = os.path.join(BASE, "reports", "cost_wall.json")
HOLD_LAB = os.path.join(BASE, "reports", "holding_period_lab.json")
GROWTH_BOOK = os.path.join(BASE, "reports", "growth_book.json")
GROWTH_EQUITY = os.path.join(BASE, "reports", "growth_book_equity.csv")
CARRY_BOOK = os.path.join(BASE, "reports", "carry_paper_book.json")
CARRY_MONITOR = os.path.join(BASE, "reports", "carry_monitor.json")
# Carry New-Names Probe — $10k PAPER, delta-neutral basis carry on XMR + HYPE
# (sealed pre-reg 2026-08-10, spec 6196101b61833248). Read-only mirror: its own
# standalone state file, surfaced as a desk card + open-leg rows. USD-quoted,
# funding-only P&L, illiquid marks flagged. Never written here.
CARRY_PROBE_LEDGER = os.path.join(BASE, "reports", "carry_newnames_probe.json")

MAX_CHART_POINTS = 500


def growth_equity_series(limit=MAX_CHART_POINTS):
    try:
        rows = []
        with open(GROWTH_EQUITY) as f:
            for rec in csv.DictReader(f):
                try:
                    rows.append([rec["date"], float(rec["equity"]),
                                 float(rec.get("drawdown_pct", 0.0))])
                except (KeyError, ValueError):
                    continue
    except OSError:
        return {"points": []}
    step = max(1, len(rows) // limit)
    pts = rows[::step]
    if rows and pts[-1] != rows[-1]:
        pts.append(rows[-1])
    return {"points": pts, "n_raw": len(rows)}


NEWS_DIR = os.path.join(BASE, "reports", "news")
MI_DB = os.path.join(KRAKEN_DESK, "kraken_bot", "market_intelligence.sqlite")


def _report_verdict(fname, *keys):
    d = _json_file(os.path.join(BASE, "reports", fname), None)
    if not d:
        return None
    for k in keys:
        if d.get(k):
            return str(d[k])[:160]
    return None


def warroom():
    """Everything the desk knows, watches, and waits for — one payload."""
    # ---- edge map: every research stream and its verdict ----
    streams = [
        {"stream": "Literature survey", "n": 13,
         "verdict": "1 pass: basis carry (decaying). eToro & equity anomalies closed."},
        {"stream": "Microstructure / tape", "n": 238,
         "verdict": "No edge. 4 candidates died on liquidity/decay screens."},
        {"stream": "Cross-sectional crypto", "n": 360,
         "verdict": "No edge — best result was short-beta in a bear (perm. p=0.37/0.96)."},
        {"stream": "Holding-period sweep", "n": 336,
         "verdict": _report_verdict("holding_period_lab.json", "best_verdict")
         or "winner failed effective-n + benchmark"},
        {"stream": "ML / nonlinear", "n": 12,
         "verdict": _report_verdict("ml_lab.json", "verdict") or "rejected"},
        {"stream": "Basis carry", "n": 46,
         "verdict": "MECHANISM REAL (clears costs ~8x); rate ~1%, executor armed at 4%."},
        {"stream": "Equity factors", "n": 30,
         "verdict": _report_verdict("equity_lab.json", "verdict") or "beta, not alpha"},
        {"stream": "Multi-asset allocation", "n": 7,
         "verdict": _report_verdict("multi_asset_lab.json", "verdict") or "index won"},
        {"stream": "36-year regime test", "n": 5,
         "verdict": "Trend/dual-mom: equity-like return at ~half the drawdown. No excess."},
        {"stream": "Sentiment (Fear&Greed 8y)", "n": 56,
         "verdict": _report_verdict("sentiment_lab.json", "verdict")
         or "folk rule inverted; neither direction survives"},
        {"stream": "Mining fundamentals", "n": 10,
         "verdict": _report_verdict("miner_lab.json", "verdict")
         or "hash ribbons negative"},
        {"stream": "Resources & shorts", "n": 6,
         "verdict": _report_verdict("resources_lab.json", "verdict")
         or "shorting loses; cash is the right defense"},
        {"stream": "IPOs (dossier)", "n": None,
         "verdict": "Closed: -20.5% mkt-adj over 3y from first close. Position size zero."},
    ]

    # ---- traps & timers ----
    traps = []
    try:
        import sqlite3
        c = sqlite3.connect("file:%s?mode=ro" % MI_DB, uri=True)
        span = c.execute(
            "SELECT (MAX(ts)-MIN(ts))/86400.0 FROM futures_analytics").fetchone()[0]
        c.close()
        traps.append({"trap": "Futures panel (OI/liq/CVD)", "have_days": round(span or 0, 1),
                      "need_days": 21, "ready": bool((span or 0) >= 21)})
    except Exception:
        traps.append({"trap": "Futures panel", "have_days": None, "need_days": 21,
                      "ready": False})
    try:
        import glob as _g
        epochs = []
        for fn in sorted(_g.glob(os.path.join(NEWS_DIR, "news_*.jsonl"))):
            with open(fn) as f:
                for line in f:
                    try:
                        epochs.append(json.loads(line).get("collected_epoch", 0))
                    except Exception:
                        pass
        span = (max(epochs) - min(epochs)) / 86400.0 if epochs else 0.0
        traps.append({"trap": "News/Reddit corpus (%d rows)" % len(epochs),
                      "have_days": round(span, 1), "need_days": 21,
                      "ready": bool(span >= 21)})
    except Exception:
        pass
    # Kalshi: compute real span from market_intelligence.sqlite (was hardcoded
    # "collecting since 2026-08-06" long after the trap was READY — that made
    # the board look stuck/unrealistic).
    try:
        import sqlite3
        from datetime import datetime, timezone
        c = sqlite3.connect("file:%s?mode=ro" % MI_DB, uri=True)
        row = c.execute(
            "SELECT MIN(utc_ts), MAX(utc_ts), COUNT(*) FROM kalshi_markets"
        ).fetchone()
        c.close()
        t0, t1, n = row or (None, None, 0)
        span = None
        if t0 and t1:
            def _parse_ts(x):
                s = str(x).replace("Z", "+00:00")
                try:
                    return datetime.fromisoformat(s)
                except Exception:
                    return None
            d0, d1 = _parse_ts(t0), _parse_ts(t1)
            if d0 and d1:
                if d0.tzinfo is None:
                    d0 = d0.replace(tzinfo=timezone.utc)
                if d1.tzinfo is None:
                    d1 = d1.replace(tzinfo=timezone.utc)
                span = (d1 - d0).total_seconds() / 86400.0
        traps.append({
            "trap": "Kalshi prediction markets (%s rows)" % (f"{int(n):,}" if n else "0"),
            "have_days": None if span is None else round(span, 1),
            "need_days": 21,
            "ready": bool(span is not None and span >= 21),
            "note": None if (span is not None and span >= 21) else (
                "collector live · sample still thin" if n else "collecting — no rows yet"
            ),
        })
    except Exception as e:
        traps.append({
            "trap": "Kalshi prediction markets",
            "have_days": None,
            "need_days": 21,
            "ready": False,
            "note": "collector status unknown (%s)" % type(e).__name__,
        })

    # ---- live wire: latest relevant headlines ----
    # Prefer professional press; cap retail Reddit noise so the strip reads like
    # a desk wire, not a meme feed.
    wire = []
    try:
        import glob as _g
        rows = []
        for fn in sorted(_g.glob(os.path.join(NEWS_DIR, "news_*.jsonl")))[-2:]:
            with open(fn) as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if r.get("relevance") == "offtopic":
                        continue
                    title = (r.get("title") or "").strip()
                    if len(title) < 28:
                        continue
                    rows.append(r)
        rows.sort(key=lambda r: -r.get("collected_epoch", 0))
        pro = [r for r in rows if r.get("tier") != "retail"]
        retail = [r for r in rows if r.get("tier") == "retail"]
        # Press first (desk wire), then at most 2 retail — do NOT re-sort by
        # time or Reddit chatter jumps the queue.
        picked = pro[:10] + retail[:2]
        wire = [{"title": r["title"][:110], "sentiment": r.get("sentiment", 0),
                 "tier": r.get("tier", "professional"), "source": r.get("source") or "",
                 "symbols": r.get("symbols", [])} for r in picked[:12]]
    except Exception:
        pass

    total = sum(x["n"] for x in streams if x["n"])
    return {"streams": streams, "total_hypotheses": total, "traps": traps,
            "wire": wire}


_PX_CACHE = {"crypto": [0.0, {}], "equity": [0.0, {}]}
_LAST_MARKS_FILE = os.path.join(BASE, "reports", "last_marks.json")
try:
    _LAST_MARKS = json.load(open(_LAST_MARKS_FILE))
except Exception:
    _LAST_MARKS = {}
_LAST_MARKS_SAVED = [0.0]


def _remember(marks, source_epochs=None):
    """Persist every fresh mark as the symbol's last-known-good price. When a
    market later closes or the quote feed stalls, P&L is computed against this
    LOCKED last price — never against entry (which would falsely show $0).

    Since 2026-08-22 (funder directive: last-good marks are DISPLAY ONLY and
    must carry a visible age) every fresh observation also stamps a UTC epoch
    under the reserved "_ts" key: {"_ts": {sym: epoch}}. Additive — every
    existing reader keys by symbol and no symbol is named "_ts". A symbol
    present in the flat map but absent from "_ts" predates the stamping and
    its age is served as UNKNOWN, never guessed."""
    changed = False
    now = __import__("time").time()
    ts_map = _LAST_MARKS.setdefault("_ts", {})
    for k, v in (marks or {}).items():
        if v and v > 0:
            if _LAST_MARKS.get(k) != v:
                _LAST_MARKS[k] = v
                changed = True
            # stamp freshness on every observation (a re-confirmed price is
            # fresh too); throttled so an unchanged book still persists ages
            stamp = (source_epochs or {}).get(k, now)
            if (source_epochs is not None and ts_map.get(k) != stamp) or now - float(ts_map.get(k) or 0) > 30:
                ts_map[k] = round(stamp, 1)
                changed = True
    if changed and now - _LAST_MARKS_SAVED[0] > 30:
        try:
            tmp = _LAST_MARKS_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(_LAST_MARKS, f)
            os.replace(tmp, _LAST_MARKS_FILE)
            _LAST_MARKS_SAVED[0] = now
        except Exception:
            pass
    return marks


def _held_mark(sym, live, entry):
    """live price if we have it; else the locked last-good price; else entry."""
    if live and live > 0:
        return live
    return _LAST_MARKS.get(sym) or entry


def _mark_age(sym):
    """Age in seconds of the stored last-good mark for sym, or None when the
    store has no timestamp for it (pre-stamp rows / entry fallback: the age is
    honestly UNKNOWN — the dashboards render that state, they never guess)."""
    try:
        t0 = (_LAST_MARKS.get("_ts") or {}).get(sym)
        if not t0:
            return None
        return max(0, int(__import__("time").time() - float(t0)))
    except Exception:
        return None


def crypto_marks(symbols):
    """Return only requested, fresh positive Kraken USD marks (15s per symbol)."""
    import time
    now = time.time()
    _, cache = _PX_CACHE["crypto"]
    stamps = _PX_CACHE.setdefault("crypto_stamps", {})
    symbols = list(dict.fromkeys(symbols))
    need = [s for s in symbols if s not in cache or not 0 <= now - stamps.get(s, 0) <= 15]
    aliases = {"DOGE": "XDG", "BTC": "XBT"}
    if need:
        requests = {}
        for symbol in need:
            base, _, quote = symbol.partition("/")
            requests[symbol] = aliases.get(base, base) + quote
        try:
            pairs = ",".join(sorted(set(requests.values())))
            with urllib.request.urlopen("https://api.kraken.com/0/public/Ticker?pair=" + pairs,
                                        timeout=6) as response:
                payload = json.load(response)
            if payload.get("error"):
                raise ValueError("Kraken ticker error")
            result = payload.get("result", {})
            fresh = {}
            for symbol, request in requests.items():
                base, _, quote = symbol.partition("/")
                # Exact pair keys: UNI/USDT must never price UNI/USD.
                keys = (request, base + quote, "X" + aliases.get(base, base) + "Z" + quote)
                row = next((result[k] for k in keys if k in result), None)
                if row is None:
                    continue
                try:
                    value = float(row["c"][0])
                    if math.isfinite(value) and value > 0:
                        fresh[symbol] = value
                except (ValueError, TypeError, KeyError, IndexError):
                    continue
            received = time.time()
            cache.update(fresh)
            stamps.update({symbol: received for symbol in fresh})
            _PX_CACHE["crypto"] = [received, cache]
            _remember(fresh)
        except Exception:
            pass
    checked = time.time()
    return {s: cache[s] for s in symbols if s in cache
            and 0 <= checked - stamps.get(s, 0) <= 15}


def _yf_fetch(tickers):
    """Native quotes with source timestamps; never a daily-bar fetch stamped live."""
    from concurrent.futures import ThreadPoolExecutor
    from tfo_equity_refresh import fetch_quote
    def one(symbol):
        try:
            return symbol, fetch_quote(symbol)
        except Exception:
            return symbol, None
    with ThreadPoolExecutor(max_workers=4) as pool:
        return {symbol: quote for symbol, quote in pool.map(one, dict.fromkeys(tickers)) if quote}


def _epoch_utc(value):
    try:
        from datetime import datetime
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (AttributeError, TypeError, ValueError):
        return None


def equity_marks(tickers):
    """Per-symbol 120s fetch cache; old/closed prices keep real source timestamps."""
    import time
    now = time.time()
    _, cache = _PX_CACHE["equity"]
    metadata = _PX_CACHE.setdefault("equity_quotes", {})
    tickers = list(dict.fromkeys(tickers))
    need = [t for t in tickers if not metadata.get(t) or
            not 0 <= now - (_epoch_utc(metadata[t].get("fetched_at")) or 0) <= 120]
    if need:
        try:
            fresh = _yf_fetch(need)
            valid = {t: q for t, q in fresh.items() if t in need
                     and _epoch_utc(q.get("quote_utc")) is not None
                     and isinstance(q.get("px"), (int, float))
                     and math.isfinite(q["px"]) and q["px"] > 0}
            metadata.update(valid)
            prices = {t: q["px"] for t, q in valid.items()}
            cache.update(prices)
            _PX_CACHE["equity"] = [time.time(), cache]
            _remember(prices, {t: _epoch_utc(q["quote_utc"]) for t, q in valid.items()})
        except Exception:
            pass
    return {t: cache[t] for t in tickers if t in cache and t in metadata}


def _quote_label(session, age, current, price_session=None):
    if price_session == "regular" and session in ("pre", "post"):
        return "last regular-session quote"
    if price_session in ("pre", "post"):
        session = price_session
    if session == "pre":
        return "pre-market quote" if current else "stale pre-market quote"
    if session == "post":
        return "after-hours quote" if current else "stale after-hours quote"
    if session == "closed":
        return "market closed · last quote"
    return "live regular quote" if current else "delayed / stale quote"


def _equity_mark_details(symbol):
    import time
    q = _PX_CACHE.get("equity_quotes", {}).get(symbol, {})
    epoch = _epoch_utc(q.get("quote_utc"))
    age = max(0, int(time.time() - epoch)) if epoch else None
    current = q.get("quote_current") is True and age is not None and age <= 300
    return _quote_label(q.get("session"), age, current, q.get("price_session")), age


def _position_mark_details(position):
    import time
    epoch = _epoch_utc(position.get("mark_quote_utc") or position.get("mark_utc"))
    age = max(0, int(time.time() - epoch)) if epoch else None
    current = position.get("mark_quote_current") is True and age is not None and age <= 300
    if position.get("mark_session"):
        return _quote_label(position["mark_session"], age, current, position.get("mark_price_session")), age
    return "ledger", age


# ------------------------------------------------------------------ perp marks
# PF_* are Kraken PERPETUAL FUTURES symbols (capacity_probe holds PF_PAXGUSD).
# Neither mark helper above can price them: crypto_marks queries the SPOT
# ticker, which has no PF_ pair, and equity_marks queries yfinance, which has no
# such ticker. So _held_mark fell through to ENTRY and every PF_ row published
# upnl $0.00 while the sleeve's own equity carried a real loss -- exactly the
# "$0.00 is not a P&L, it is a missing mark" failure the last-good store exists
# to prevent.
#
# The marks come from the thin order-book collector's own snapshot stream --
# the SAME file the capacity_probe sleeve prices itself from -- so the dashboard
# and the sleeve cannot disagree about what a perp is worth.
#
# Non-negotiable #7 (no stale price presented as live): a snapshot older than
# PERP_FRESH_S is still USED (it beats entry by a mile) but is never labelled
# live -- its src carries STALE and its age, and mark_age_sec is set so the
# dashboard renders the age tag.
PERP_OB_DIR = os.path.join(KRAKEN_DESK, "reports", "microstructure")
PERP_FRESH_S = 900
_PERP_CACHE = [0.0, {}]


def perp_marks(symbols, max_bytes=4 * 1024 * 1024):
    """{sym: {"mid": float, "age_s": int}} for PF_* perp symbols.

    Tail-reads reports/microstructure/ob_perp_thin_*.jsonl newest-file-first.
    30s cache. A symbol with no usable snapshot is ABSENT from the result --
    never substituted, never guessed. Read-only."""
    import glob as _glob
    import time as _t
    want = {s for s in (symbols or []) if s}
    if not want:
        return {}
    now = _t.time()
    if now - _PERP_CACHE[0] < 30 and all(s in _PERP_CACHE[1] for s in want):
        return {s: dict(_PERP_CACHE[1][s], age_s=max(0, int(
            now - _PERP_CACHE[1][s]["t"]))) for s in want}
    found = {}
    try:
        files = sorted(_glob.glob(os.path.join(PERP_OB_DIR, "ob_perp_thin_*.jsonl")))
        if not files:
            print("MARKS: no ob_perp_thin_*.jsonl in %s — PF_ rows fall back to "
                  "the last-good store / entry, flagged" % PERP_OB_DIR)
            return {}
        for path in reversed(files[-3:]):
            with open(path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - max_bytes))
                chunk = f.read()
            lines = chunk.split(b"\n")
            if size > max_bytes:
                lines = lines[1:]          # drop the partial first line
            for raw in lines:
                if not raw.strip():
                    continue
                try:
                    r = json.loads(raw)
                    sym = r.get("sym")
                    if sym not in want:
                        continue
                    mid = float(r.get("mid"))
                    ts = float(r.get("t"))
                except Exception:
                    continue
                if mid <= 0:
                    continue               # zero is missing data, not a price
                if sym not in found or ts > found[sym]["t"]:
                    found[sym] = {"mid": mid, "t": ts}
            if len(found) >= len(want):
                break
    except Exception as e:                 # noqa: BLE001 - loud, degrades visibly
        print("MARKS: perp order-book read failed (%s: %s) — PF_ rows fall back "
              "to the last-good store / entry, flagged"
              % (type(e).__name__, str(e)[:160]))
        return {}
    _PERP_CACHE[0], _PERP_CACHE[1] = now, dict(found)
    out = {s: {"mid": v["mid"], "age_s": max(0, int(now - v["t"]))}
           for s, v in found.items()}
    # fresh readings join the last-good store, so a dead collector degrades to a
    # timestamped last price rather than to entry
    _remember({s: v["mid"] for s, v in out.items() if v["age_s"] <= PERP_FRESH_S})
    return out


_VOLMAP_CACHE = [0.0, {}]


def _liquidity_volmap():
    """{'XBT/USD': vol_musd} from the engine universe; cached 60s, degrades to {}."""
    import time as _t2
    import urllib.request
    now = _t2.time()
    if now - _VOLMAP_CACHE[0] < 60 and _VOLMAP_CACHE[1]:
        return _VOLMAP_CACHE[1]
    m = {}
    try:
        with urllib.request.urlopen("http://127.0.0.1:18799/state", timeout=3) as r:
            uni = json.load(r).get("universe") or []
        for x in uni:
            if isinstance(x, dict) and x.get("symbol"):
                try:
                    m[x["symbol"]] = float(x.get("vol_musd") or 0)
                except Exception:
                    pass
        if m:
            _VOLMAP_CACHE[0], _VOLMAP_CACHE[1] = now, m
    except Exception:
        return _VOLMAP_CACHE[1] or {}
    return m


# ---------------------------------------------------------------------------
# NEVER SILENTLY DEGRADE
#
# Everything below exists to make one rule enforceable: a number this desk
# cannot compute is reported as UNAVAILABLE, loudly, with the reason. It is
# never quietly replaced by a fallback that looks like a real figure.
#
# The bugs this replaced, all the same bug wearing different hats:
#   * a hardcoded 0.70 AUDUSD standing in for a failed FX fetch
#   * a client-side `combined_usd || usd` that silently dropped the whole AUD
#     book and presented the USD subtotal as the firm total
#   * two processes on one port, one unable to import yfinance, each serving
#     frozen marks it believed were current
# ---------------------------------------------------------------------------

# Which process is answering. The split-brain headline was undiagnosable because
# the payload never said who produced it. Now every response is signed.
_SERVED_BY = {"host": None, "port": None}


def served_by():
    out = {"pid": os.getpid(),
           "python": "%d.%d.%d" % sys.version_info[:3],
           "executable": sys.executable,
           "host": _SERVED_BY.get("host"),
           "port": _SERVED_BY.get("port")}
    out["label"] = "pid %s · py %s · %s:%s" % (out["pid"], out["python"],
                                               out["host"], out["port"])
    return out


def _age_human(sec):
    if sec is None:
        return "age unknown"
    sec = int(sec)
    if sec < 90:
        return "%ds old" % sec
    if sec < 5400:
        return "%dm old" % (sec // 60)
    if sec < 172800:
        return "%dh old" % (sec // 3600)
    return "%dd old" % (sec // 86400)


def quote_feed_status():
    """Can THIS process actually fetch equity marks?

    The split-brain: pid 66025 (CommandLineTools python3.9) has no yfinance, so
    equity_marks() throws on every call and silently serves an empty cache —
    every equity row then falls back to a locked last-known price that the row
    reports as current. The process could not know it was blind. Now it can.
    """
    st = {"module": "yfinance", "ok": False, "error": None,
          "last_success_epoch": None, "last_success_age_s": None}
    try:
        import yfinance  # noqa: F401
        st["ok"] = True
    except Exception as e:
        st["error"] = "%s: %s" % (type(e).__name__, str(e)[:120])
    ts = _PX_CACHE["equity"][0] or 0.0
    if ts:
        st["last_success_epoch"] = round(ts, 1)
        st["last_success_age_s"] = int(__import__("time").time() - ts)
    return st


# A quote newer than this is "live"; older than this the in-process cache is
# stale and must be labelled as such rather than passed off as current.
_LIVE_MAX_AGE_S = 300


def fx_audusd():
    """AUDUSD with EXPLICIT provenance. Never invents a rate.

    Returns status one of:
      live       — fetched from the quote feed within _LIVE_MAX_AGE_S
      cached     — the desk's own last-known-good rate, WITH its age
      unavailable— no rate at all; callers must not convert AUD

    Replaces `rate2 = (combined or {}).get("audusd") or 0.70`, a hardcoded
    fallback that fired silently and mispriced the entire AUD book by ~1.2%.
    """
    import time as _t3
    now = _t3.time()
    out = {"pair": "AUDUSD", "rate": None, "status": "unavailable",
           "source": None, "as_of_epoch": None, "age_s": None,
           "age_human": None, "live": False, "note": ""}

    # 1) live quote
    rate = None
    try:
        rate = equity_marks(["AUDUSD=X"]).get("AUDUSD=X")
    except Exception:
        rate = None
    fetched = _epoch_utc(_PX_CACHE.get("equity_quotes", {}).get("AUDUSD=X", {}).get("quote_utc")) or 0.0
    if rate and fetched and (now - fetched) <= _LIVE_MAX_AGE_S:
        age = int(now - fetched)
        out.update(rate=round(float(rate), 6), status="live", live=True,
                   source="quote feed (AUDUSD=X)", as_of_epoch=round(fetched, 1),
                   age_s=age, age_human=_age_human(age),
                   note="live rate, %s" % _age_human(age))
        return out

    # 2) the desk's own cached rate — permitted ONLY when labelled as cached
    #    with its age. A stale rate presented as live is the original bug.
    crate, cts = None, None
    try:
        with open(_LAST_MARKS_FILE) as _f:
            crate = json.load(_f).get("AUDUSD=X")
        cts = os.path.getmtime(_LAST_MARKS_FILE)
    except Exception:
        crate, cts = None, None
    if not crate:
        crate = _LAST_MARKS.get("AUDUSD=X")     # in-memory copy, age unknown
        cts = None
    if crate:
        age = int(now - cts) if cts else None
        # HT decision 2026-09-26 #4b: a cached rate inside the FX cache window (6h; 72h while
        # spot FX is shut) is fresh enough to convert AUD -- no warning. Same window as the rails.
        try:
            if BASE not in sys.path:
                sys.path.insert(0, BASE)
            import tfo_equity_refresh as _ter
            limit = _ter.fx_cache_max_age()
        except Exception:
            limit = 6 * 3600
        fresh = age is not None and 0 <= age <= limit
        out.update(rate=round(float(crate), 6), status="cached", live=False, fresh=fresh,
                   source="reports/last_marks.json", age_s=age,
                   as_of_epoch=round(cts, 1) if cts else None,
                   age_human=_age_human(age),
                   note=("cached rate (%s), inside the %.0fh FX window" % (_age_human(age), limit / 3600)) if fresh
                   else "CACHED rate (%s) — not a live quote" % _age_human(age))
        return out

    out["note"] = ("no live quote and no cached rate — AUD books cannot be "
                   "converted to USD")
    return out


# Yahoo listing suffixes whose quotes are NOT printed in USD. Used ONLY to
# flag rows, never to convert them — the translation itself belongs in fx.py
# (quote-currency detection + minor units: .L is PENCE, .KS is KRW, .PA is EUR).
# Flagging here stops the board presenting a KRW number as dollars while that
# lands; it deliberately performs no arithmetic, so it cannot double-convert.
_NON_USD_SUFFIXES = (
    ".KS", ".KQ", ".L", ".PA", ".AS", ".BR", ".DE", ".F", ".MC", ".MI", ".LS",
    ".VI", ".HE", ".IR", ".AT", ".T", ".TO", ".V", ".AX", ".NZ", ".SS", ".SZ",
    ".HK", ".TW", ".TWO", ".KL", ".SI", ".BK", ".JK", ".NS", ".BO", ".SW",
    ".ST", ".OL", ".CO", ".IC", ".SA", ".MX", ".BA", ".SN", ".JO", ".TA",
    ".IS", ".WA", ".PR", ".BD", ".ME", ".CA", ".SR", ".QA", ".AD", ".DU")


def _flag_quote_ccy(rows):
    """Label rows quoted in a non-USD currency, and warn ONLY about the ones
    actually summed at a raw local price.

    DEFECT FIX 2026-08-28: this flagged by ticker SUFFIX alone and warned that
    "the firm total is overstated" for every foreign listing, including the ones
    the row builder had already translated a few hundred lines below
    (`mk_usd = mk * usd_per_unit; val = u * mk_usd`, with `unconverted` recording
    whether a rate was found). Translation HAS landed, so the board was reporting
    a $5,284 overstatement that did not exist — all three rows carried a live
    rate and a correct USD value. A monitor that cries wolf on healthy data
    trains the reader to ignore it, which is the failure mode that matters here.

    The suffix label is still applied to every foreign listing, because knowing a
    row is quoted in KRW or pence is useful provenance. Only the WARNING is now
    conditional on `unconverted`, i.e. on there being no usable rate.
    """
    n, unconv, exposure = 0, 0, 0.0
    for r in rows:
        s = r.get("symbol") or ""
        if "/" in s or "." not in s:
            continue
        suf = s[s.rfind("."):].upper()
        if not any(suf == x.upper() for x in _NON_USD_SUFFIXES):
            continue
        r["ccy_unverified"] = True
        r["src"] = (r.get("src") or "") + " · QUOTE CCY NOT USD"
        n += 1
        # A row is only mis-summed when NO rate was found for it. Where a rate
        # exists the stored `value` is already USD and belongs in the total.
        if r.get("unconverted") or r.get("fx_usd_per_unit") is None:
            unconv += 1
            exposure += r.get("value") or 0.0
    if not n:
        return None
    out = {"rows": n, "rows_unconverted": unconv,
           "value_usd_labelled": round(exposure, 2)}
    if unconv:
        out["warning"] = ("%d of %d non-USD-quoted position(s), worth $%s, have "
                          "NO usable FX rate and are summed into the USD total "
                          "at their RAW LOCAL price. That amount is not dollars "
                          "and the firm total is overstated by it."
                          % (unconv, n, format(round(exposure, 2), ",")))
    else:
        out["warning"] = None
        out["note"] = ("%d position(s) are quoted in a non-USD currency (KRW / "
                       "GBp pence / EUR ...); every one carries a live rate and "
                       "is summed in USD. Labelled for provenance, not a defect."
                       % n)
    return out


# ---- exchange session table (HT decision 2026-09-26 #4a) ---------------------
# A last-close mark while the listing's exchange is SHUT is not stale -- it is the
# only price that exists. Hours are local regular sessions; holidays come from the
# desk's hand-kept table (tfo_watchlist.HOLIDAYS). Unknown listings fall back to
# weekends only.
_EU = (9, 0), (17, 35)
_SESSIONS = {
    "US": ("America/New_York", (9, 30), (16, 0)), "TO": ("America/Toronto", (9, 30), (16, 0)),
    "V": ("America/Toronto", (9, 30), (16, 0)), "AX": ("Australia/Sydney", (10, 0), (16, 12)),
    "NZ": ("Pacific/Auckland", (10, 0), (16, 45)), "T": ("Asia/Tokyo", (9, 0), (15, 30)),
    "HK": ("Asia/Hong_Kong", (9, 30), (16, 10)), "NS": ("Asia/Kolkata", (9, 15), (15, 30)),
    "BO": ("Asia/Kolkata", (9, 15), (15, 30)), "KS": ("Asia/Seoul", (9, 0), (15, 30)),
    "KQ": ("Asia/Seoul", (9, 0), (15, 30)), "SI": ("Asia/Singapore", (9, 0), (17, 0)),
    "TW": ("Asia/Taipei", (9, 0), (13, 30)), "SS": ("Asia/Shanghai", (9, 30), (15, 0)),
    "SZ": ("Asia/Shanghai", (9, 30), (15, 0)), "L": ("Europe/London", (8, 0), (16, 35)),
    "IR": ("Europe/Dublin", (8, 0), (16, 30)), "SA": ("America/Sao_Paulo", (10, 0), (17, 0)),
    "MX": ("America/Mexico_City", (8, 30), (15, 0)), "JO": ("Africa/Johannesburg", (9, 0), (17, 0)),
}
for _sfx, _tz in (("PA", "Europe/Paris"), ("AS", "Europe/Amsterdam"), ("BR", "Europe/Brussels"),
                  ("DE", "Europe/Berlin"), ("F", "Europe/Berlin"), ("MC", "Europe/Madrid"),
                  ("MI", "Europe/Rome"), ("LS", "Europe/Lisbon"), ("VI", "Europe/Vienna"),
                  ("HE", "Europe/Helsinki"), ("ST", "Europe/Stockholm"), ("OL", "Europe/Oslo"),
                  ("CO", "Europe/Copenhagen"), ("SW", "Europe/Zurich")):
    _SESSIONS[_sfx] = (_tz,) + _EU
_CLOSED_MARK_MAX_AGE_S = 4 * 86400     # a last close older than a long weekend is genuinely stale


def _holidays():
    try:
        if BASE not in sys.path:
            sys.path.insert(0, BASE)
        import tfo_watchlist as _tw
        return _tw.HOLIDAYS
    except Exception:
        return {}


def market_shut(symbol, now=None):
    """True when the listing's exchange is outside its regular session (weekend,
    holiday or after hours). Crypto / perps never shut. Unknown suffix: weekends only."""
    import datetime as _dt
    from zoneinfo import ZoneInfo as _Z
    s = str(symbol or "")
    if not s or "/" in s or s.startswith("PF_") or s.endswith("=X"):
        return False
    sfx = "US" if "." not in s else s.rsplit(".", 1)[1].upper()
    now = now or _dt.datetime.now(_dt.timezone.utc)
    sess = _SESSIONS.get(sfx)
    if not sess:
        return now.astimezone(_Z("America/New_York")).weekday() >= 5
    tz, (oh, om), (ch, cm) = sess
    loc = now.astimezone(_Z(tz))
    if loc.weekday() >= 5 or loc.date().isoformat() in _holidays().get(sfx, ()):
        return True
    hm = loc.hour * 60 + loc.minute
    return not (oh * 60 + om <= hm < ch * 60 + cm)


def marks_health(rows):
    """How many rows are marked LIVE vs served from a frozen/ledger price.

    A process that cannot reach the quote feed still renders every row with a
    price and no indication that the price is days old. This counts the rows it
    is actually guessing on, so the board can say so.
    """
    live = snap = stale = closed = 0
    stale_floors = {}
    for r in rows:
        s = (r.get("src") or "").lower()
        age = r.get("mark_age_sec")
        if s.startswith("live") or s == "engine":
            live += 1
        elif s.startswith("desk snapshot") or s in ("pre-market quote", "after-hours quote"):
            snap += 1
        elif (r.get("mark") is not None and market_shut(r.get("symbol"))
              and (age is None or age <= _CLOSED_MARK_MAX_AGE_S)):
            closed += 1          # exchange shut: the last close IS the current price
        else:
            stale += 1
            f = r.get("floor") or "?"
            stale_floors[f] = stale_floors.get(f, 0) + 1
    n = len(rows) or 1
    pct = round(stale * 100.0 / n, 1)
    feed = quote_feed_status()
    # material = enough frozen rows that the headline is not what it claims
    degraded = (not feed["ok"]) or pct >= 20.0
    if not feed["ok"]:
        warn = ("QUOTE FEED DOWN in this process (%s) — %d of %d rows (%.0f%%) "
                "are FROZEN ledger marks rendered as if current. Totals below "
                "are NOT live." % (feed["error"] or "import failed",
                                   stale, len(rows), pct))
    elif degraded:
        warn = ("%d of %d rows (%.0f%%) are frozen/ledger marks, not live "
                "quotes. Totals are partly stale." % (stale, len(rows), pct))
    else:
        warn = None
    return {"rows": len(rows), "live": live, "snapshot": snap, "stale": stale,
            "closed": closed, "closed_note": "last close while the exchange is shut (session/holiday table) -- not stale",
            "stale_pct": pct, "degraded": degraded, "warning": warn,
            "stale_by_floor": stale_floors, "equity_feed": feed,
            "served_by": served_by()}


# ---- registered capital ----------------------------------------------------
# The denominator for return on capital. NOT deployed position value: that
# excludes idle cash, counts CFDs at notional, and moves when a book rotates to
# cash. Every component names where its number comes from so the total is
# auditable line by line.
FLOOR_DESK_START = 40000.0        # per desk, as registered in tournament.py:562
GROWTH_BOOK_START = 50000.0       # growth_book.py START_CASH
VALUE_BOOK_START = 50000.0        # value_book.py START_CASH
# NAV at the funder's first fill. Promoted from a local inside
# positions_payload() so the capital base can cite the same constant.
class _BookRemoved(Exception):
    """Raised to short-circuit a decommissioned book's reader."""


CLAUDE_BOOK_START_NAV = 52323.30  # AUD — the OANDA account is AUD-denominated
# DECOMMISSIONED 2026-08-28 (funder-directed: "remove both desks entirely").
# The OANDA Book is no longer a book of this firm, so this dashboard neither
# reads nor reports it. The reader code below is gated rather than deleted: it
# is the only description of what the practice account held when the desk was
# removed, and those positions could NOT be closed (sustained 401, and a logged
# close on that desk is a known no-op). See DECOMMISSION_20260828.md.
OANDA_BOOK_REMOVED = True



def _coo_mandate_block():
    """Live COO recovery mandate vs fixed target US$1,694,524.90 (PAPER)."""
    fr = _json_file(os.path.join(BASE, "reports", "firm_risk_state.json"), {}) or {}
    gcl = _json_file(os.path.join(BASE, "reports", "gap_close_levered_book.json"), {}) or {}
    target = 1694524.90
    try:
        eq = float(fr.get("firm_equity_usd") or 0.0)
        peak = float(fr.get("firm_peak_usd") or 0.0)
    except (TypeError, ValueError):
        eq, peak = 0.0, 0.0
    vs = eq - target
    holds = gcl.get("holdings") or {}
    return {
        "schema": "tfo.coo_mandate_status.v1",
        "paper_only": True,
        "target_usd": target,
        "deadline_aest": "2026-09-18T16:30:00+10:00",
        "firm_equity_usd": round(eq, 2),
        "firm_peak_usd": round(peak, 2),
        "vs_target_usd": round(vs, 2),
        "vs_peak_usd": round(eq - peak, 2),
        "status": "GREEN" if vs >= 0 else "SHORT",
        "level": fr.get("level"),
        "capital_accounting_valid": fr.get("capital_accounting_valid"),
        "gap_close_soxl_equity_usd": round(float(gcl.get("equity") or 0), 2),
        "gap_close_soxl_units": holds.get("SOXL"),
        "note": "Mandate target is COO recovery US$1,694,524.90 — distinct from live firm peak.",
    }


def _firm_funding_block(books, firm_risk, registry_error=None):
    """All documented funding, including negative residuals after cash sweeps."""
    errors = [registry_error] if registry_error else []
    total = 0.0
    if not books:
        errors.append("book registry unavailable or empty")
    for book in books:
        key = book.get("key", "unknown")
        if book.get("error"):
            errors.append("%s: %s" % (key, book["error"]))
            continue
        value = book.get("start_usd")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            errors.append("%s: documented funding unavailable" % key)
            continue
        total += value
    try:
        import book_archive
        total += book_archive.retained_basis_usd(exclude={b.get("key") for b in books})
        # Custodian 2026-09-25: same basis as private_wealth_monitor.account_summary --
        # the verified CoinSpot retirement keeps its retained (unrecovered) funding basis.
        from coinspot_retirement import verified_retirement
        _ret = verified_retirement(__import__("pathlib").Path(BASE)) if "coinspot_floor" not in {b.get("key") for b in books} else None
        if _ret:
            total += _ret["retained_funding_basis_usd"]
    except Exception as exc:                              # noqa: BLE001
        errors.append("archived funding basis unreadable (%s)" % exc)
    if not math.isfinite(total):
        errors.append("combined documented funding is nonfinite")
    if firm_risk.get("capital_accounting_valid") is not True:
        errors.append("firm capital accounting is unverified")
    if (firm_risk.get("books_total") is not None
            and firm_risk.get("books_readable") != firm_risk.get("books_total")):
        errors.append("firm risk reports unreadable books")
    if (firm_risk.get("book_keys") is not None
            and set(firm_risk["book_keys"]) != {b.get("key") for b in books}):
        errors.append("firm risk and current registry cover different books")
    return {"complete": not errors, "total_usd": round(total, 2) if not errors else None,
            "books": len(books), "errors": errors,
            "basis": "sum of all documented registered funding bases plus verified-archive and CoinSpot-retirement retained bases, including negative residuals"}


def _sol_short_pilot_block():
    """Sanitized, validated short ledger; no journal or hypothetical live access."""
    key = "crypto_sol_short_pilot"
    path = os.path.join(BASE, "reports", key + ".json")
    block = {"available": os.path.exists(path), "readable": False, "error": None,
             "book_key": key, "symbol": "PF_SOLUSD", "side": "short",
             "paper_only": True, "simulated_only": True, "verified_account_access": False,
             "label": "SOL short pilot · SIMULATED ONLY · access unverified"}
    if not block["available"]:
        return block
    try:
        import book_coverage as bc
        with open(path) as f:
            before = f.read()
        validated = bc._load_short_pilot(key, block["label"], path)
        with open(path) as f:
            after = f.read()
        if before != after:
            raise ValueError("short ledger changed during validation; retry next refresh")
        if not validated or validated.get("error"):
            raise ValueError((validated or {}).get("error") or "short ledger unavailable")
        ledger = json.loads(after)
        for field in ("quantity", "entry_price", "mark_price", "mark_utc", "status",
                      "seed_usd", "deploy_basis_usd", "collateral_usd", "unrealised_usd",
                      "realised_usd", "realised_funding_usd", "funding_accrued_usd",
                      "funding_total_usd", "costs_paid_usd", "estimated_exit_fee_usd",
                      "thesis_stop_price", "opened_at", "funding_through_utc"):
            block[field] = ledger.get(field)
        block.update({"readable": True, "equity_usd": validated["equity_usd"],
                      "cash_usd": validated["cash_usd"], "mark_age_sec": validated["mark_age_sec"],
                      "stale": validated["stale"], "warnings": validated["warnings"],
                      "gross_notional_usd": ledger["quantity"] * ledger["mark_price"],
                      "net_pnl_usd": validated["equity_usd"] - validated["start_usd"],
                      "equity_basis": "net liquidation, including recorded fees and funding"})
        basis = ledger.get("deploy_basis_usd")
        block["pnl_pct_vs_deploy"] = (100 * (validated["equity_usd"] / basis - 1)
            if isinstance(basis, (int, float)) and not isinstance(basis, bool)
            and math.isfinite(basis) and basis > 0 else None)
    except Exception as exc:
        block.update({"readable": False, "error": str(exc), "status": "UNREADABLE",
                      "equity_usd": None, "net_pnl_usd": None, "gross_notional_usd": None})
    return block


def _sol_short_position_row(block):
    if not block.get("readable") or block.get("quantity", 0) <= 0:
        return None
    entry, mark = block["entry_price"], block["mark_price"]
    return {"floor": "SOL short pilot · SIMULATED", "book": "SOL short pilot · SIMULATED",
            "book_key": block["book_key"], "symbol": "PF_SOLUSD", "side": "short",
            "units": block["quantity"], "entry": entry, "mark": mark,
            "value": block["gross_notional_usd"], "upnl": block["unrealised_usd"],
            "upnl_pct": round(100 * (entry - mark) / entry, 2),
            "net_pnl_usd": block["net_pnl_usd"],
            "pnl_note": "row P&L is price-only; net sleeve P&L includes fees and funding",
            "ccy": "USD", "quote_ccy": "USD", "fx_usd_per_unit": 1.0,
            "mark_local": mark, "mark_usd": mark, "unconverted": False,
            "src": "ledger · simulated liquidation quote", "mark_utc": block["mark_utc"],
            "mark_age_sec": block["mark_age_sec"], "stale": block["stale"],
            "simulated_only": True, "verified_account_access": False}


def _salary_block():
    """Owner-withdrawal ledger, read only; never initiates a payment."""
    value = _json_file(os.path.join(BASE, "reports", "salary_payments.json"), {})
    return value if isinstance(value, dict) else {}


def _daily_pnl_block():
    """Record reporting-only daily P&L from the same risk ledger as main equity."""
    try:
        import tfo_daily_pnl
        return tfo_daily_pnl.update(os.path.join(BASE, 'reports'))
    except Exception as exc:
        return {'status':'UNVERIFIED','pnl_usd':None,'pnl_pct':None,'history':[],
                'note':'Daily P&L unavailable: '+str(exc),'paper_only':True}


def _tfo_hero_block():
    """Top-of-board firm equity / P&L / top performers for War Room readability."""
    import book_coverage as _bc
    import importlib
    importlib.reload(_bc)
    fr = _json_file(os.path.join(BASE, "reports", "firm_risk_state.json"), {}) or {}
    gcl = _json_file(os.path.join(BASE, "reports", "gap_close_levered_book.json"), {}) or {}
    mandate = _coo_mandate_block()
    target = float(mandate.get("target_usd") or 1694524.90)
    try:
        eq = float(fr.get("firm_equity_usd") or 0.0)
        peak = float(fr.get("firm_peak_usd") or 0.0)
    except (TypeError, ValueError):
        eq, peak = 0.0, 0.0
    # Sep 10 baseline from funder recovery frame (for "recovered from" context)
    baseline = 1676552.10
    books = []
    _registered, _registry_error = [], None
    try:
        _registered = _bc.registry()
        for b in _registered:
            if b.get("error"):
                continue
            key = b.get("key")
            start = b.get("start_usd")
            equity = b.get("equity_usd")
            if start is None or equity is None:
                continue
            try:
                start_f = float(start); eq_f = float(equity)
            except (TypeError, ValueError):
                continue
            # Skip emptied / accounting-stub books (negative or tiny funding basis
            # inflates fake "P&L" after capital was moved to Gap-Close).
            if start_f < 500 or eq_f < 100:
                continue
            pnl = eq_f - start_f
            pct = (eq_f / start_f - 1.0) * 100.0 if start_f > 1e-9 else None
            books.append({
                "key": key,
                "label": b.get("label") or key,
                "equity_usd": round(eq_f, 2),
                "start_usd": round(start_f, 2),
                "pnl_usd": round(pnl, 2),
                "pnl_pct": None if pct is None else round(pct, 2),
            })
    except Exception as e:  # noqa: BLE001
        _registry_error = "%s: %s" % (type(e).__name__, str(e))
        books = [{"key": "_error", "label": type(e).__name__, "pnl_usd": 0, "equity_usd": 0}]
    books_sorted = sorted(books, key=lambda x: -(x.get("pnl_usd") or 0))
    top = [x for x in books_sorted if (x.get("pnl_usd") or 0) > 0][:8]
    bottom = [x for x in sorted(books, key=lambda x: (x.get("pnl_usd") or 0)) if (x.get("pnl_usd") or 0) < 0][:5]
    gcl_eq = float(gcl.get("equity") or 0)
    gcl_seed = float(gcl.get("seed_usd") or gcl.get("start") or 0)
    gcl_pnl = gcl_eq - gcl_seed if gcl_seed else None
    funding = _firm_funding_block(_registered, fr, _registry_error)
    taylor_seed = funding["total_usd"]
    gcl_status = gcl.get("status") or ("OPEN_SOXL" if (gcl.get("holdings") or {}).get("SOXL") else "CASH")
    locked = gcl.get("locked_pnl_vs_seed_usd")
    gcl_units = (gcl.get("holdings") or {}).get("SOXL")
    gcl_closed = ("CLOSED" in str(gcl_status)) or (gcl_units is None and float(gcl.get("cash") or 0) > 0)
    # P&L since THIS open deploy (entry_px + cash). Cumulative vs funding-basis
    # seed can still show prior locked gains and mislead the 5% daily take rail.
    since_deploy_pnl = None
    since_deploy_pct = None
    try:
        ep = gcl.get("entry_px")
        if isinstance(ep, dict):
            ep = ep.get("SOXL") or ep.get(str(gcl.get("symbol") or "SOXL"))
        if gcl_units and ep is not None and not gcl_closed:
            ep_f = float(ep)
            cash_f = float(gcl.get("cash") or 0)
            deploy_eq = float(gcl_units) * ep_f + cash_f
            if deploy_eq > 1e-9:
                since_deploy_pnl = gcl_eq - deploy_eq
                since_deploy_pct = (gcl_eq / deploy_eq - 1.0) * 100.0
    except Exception:
        since_deploy_pnl = None
        since_deploy_pct = None
    if gcl_closed and locked is not None:
        display_pnl = float(locked)
        display_pct = None if not gcl_seed else round((gcl_eq / gcl_seed - 1) * 100, 2)
        display_basis = "locked_vs_seed"
    elif since_deploy_pnl is not None:
        display_pnl = since_deploy_pnl
        display_pct = None if since_deploy_pct is None else round(since_deploy_pct, 2)
        display_basis = "since_deploy"
    else:
        display_pnl = gcl_pnl
        display_pct = None if not gcl_seed else round((gcl_eq / gcl_seed - 1) * 100, 2)
        display_basis = "vs_seed"

    crypto_clips = None
    try:
        ccb = _json_file(os.path.join(BASE, "reports", "crypto_clip_book.json"), {}) or {}
        if ccb.get("seed_usd") or ccb.get("holdings"):
            eq_c = float(ccb.get("equity") or 0)
            seed_c = float(ccb.get("seed_usd") or ccb.get("start") or 0)
            # Rail P&L = vs deploy_basis (never Taylor/seed carry — funder 2026-09-19)
            basis_c = float(ccb.get("deploy_basis_usd") or 0) or seed_c
            basis_label = "since deploy" if float(ccb.get("deploy_basis_usd") or 0) > 1 else "vs seed"
            pnl_c = eq_c - basis_c if basis_c else None
            pct_c = ((eq_c / basis_c) - 1.0) * 100.0 if basis_c > 1e-9 else None
            holds = ccb.get("holdings") or {}
            hold_lbl = "/".join(s.replace("/USD","") for s in holds) if holds else "clips"
            crypto_clips = {
                "label": "Crypto clips · %s" % hold_lbl,
                "equity_usd": round(eq_c, 2),
                "seed_usd": round(seed_c, 2),
                "deploy_basis_usd": round(float(ccb.get("deploy_basis_usd") or 0), 2) or None,
                "basis_label": basis_label,
                "pnl_usd": None if pnl_c is None else round(pnl_c, 2),
                "pnl_pct": None if pct_c is None else round(pct_c, 2),
                "status": ccb.get("status") or "OPEN",
                "holdings": ccb.get("holdings") or {},
                "clip_rules": ccb.get("clip_rules") or {},
                "label_alpha_beta": "opportunistic beta",
            }
    except Exception:
        crypto_clips = None

    # Fundamental recovery SOXL sleeve (separate book; dual SOXL exposure)
    fund_recovery = None
    try:
        fund = _json_file(os.path.join(BASE, "reports", "fundamental_book.json"), {}) or {}
        rec = fund.get("fundamental_recovery") or {}
        soxl_pos = (fund.get("positions") or {}).get("SOXL") or {}
        if rec.get("deployed_usd") and soxl_pos.get("recovery_sleeve"):
            units_f = float(soxl_pos.get("units") or rec.get("units") or 0)
            mark_f = float(soxl_pos.get("mark") or rec.get("entry_px") or 0)
            cost_f = float(rec.get("deployed_usd") or 0)
            mv_f = units_f * mark_f
            pnl_f = mv_f - cost_f if cost_f else None
            pct_f = ((mv_f / cost_f) - 1.0) * 100.0 if cost_f > 1e-9 else None
            fund_recovery = {
                "label": "Fundamental recovery · SOXL",
                "symbol": "SOXL",
                "equity_usd": round(mv_f, 2),
                "deployed_usd": round(cost_f, 2),
                "pnl_usd": None if pnl_f is None else round(pnl_f, 2),
                "pnl_pct": None if pct_f is None else round(pct_f, 2),
                "units": round(units_f, 2),
                "entry_px": round(float(rec.get("entry_px") or 0), 4),
                "mark_px": round(mark_f, 4),
                "hard_profit_take_pct": rec.get("hard_profit_take_pct"),
                "hard_stop_loss_pct": rec.get("hard_stop_loss_pct"),
                "target_gap_usd": None if rec.get("target_gap_usd") is None else round(float(rec["target_gap_usd"]), 2),
                "status": "OPEN",
            }
    except Exception:
        fund_recovery = None

    # Crypto stack for War Room top strip — every live crypto sleeve, clearly labeled
    crypto_sleeves = []
    try:
        _CRYPTO_SLEEVE_SPECS = [
            ("crypto_liq_beta_book", "Liq beta", "BTC/ETH/XRP/SOL"),
            ("crypto_clip_book", "Clip", "DOGE/ADA/NEAR"),
            ("crypto_uni_sui_rs", "UNI stack", "UNI/AAVE/SUI"),
            ("crypto_mid_clip_book", "Mid clip", "LINK/TAO/HYPE"),
            ("crypto_weekend_rs_probe", "Weekend RS", "XMR/VVV"),
            ("crypto_avax_ondo_ltc", "Clip B", "AVAX/ONDO/LTC"),
            ("tour_capacity_probe", "Capacity", "PF alts"),
        ]
        _reg = {b.get("key"): b for b in _registered if not b.get("error")}
        for _key, _short, _default_names in _CRYPTO_SLEEVE_SPECS:
            _b = _reg.get(_key)
            if not _b:
                continue
            _path = _b.get("path") or _b.get("file")
            if not _path:
                continue
            _led = _json_file(_path, {}) or {}
            _holds = _led.get("holdings") or {}
            _names = []
            for _s, _u in _holds.items():
                try:
                    if abs(float(_u or 0)) > 1e-12:
                        _names.append(_s.replace("/USD", "").replace("PF_", ""))
                except (TypeError, ValueError):
                    continue
            # Canonical scanner ledger is the fallback, not older nested mark fields.
            _eqf = float(_led.get("equity") if _led.get("equity") is not None else (_b.get("equity_usd") or 0))
            _mark_utc = _led.get("last_mark_utc")
            _mark_source = "Desk check"
            _mark_age = None
            try:
                from datetime import datetime, timezone
                _mark_age = max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(
                    _mark_utc.replace("Z", "+00:00"))).total_seconds()))
            except (ValueError, TypeError, AttributeError):
                pass
            # Live Kraken MTM so crypto strip moves — ONLY if every holding is marked.
            # Partial marks understate equity vs full deploy_basis and fake huge losses
            # (funder 2026-09-19: Liq beta −35% / Clip −33% mirage).
            if _holds:
                try:
                    _syms = [s for s, u in _holds.items() if abs(float(u or 0)) > 1e-12]
                    _live = crypto_marks(_syms) if _syms else {}
                    if _syms and all(s in _live for s in _syms):
                        _eqf = float(_led.get("cash") or 0) + sum(
                            float(_holds[s]) * float(_live[s]) for s in _syms
                        )
                        _mark_epoch = min(_PX_CACHE["crypto_stamps"][s] for s in _syms)
                        _mark_utc = datetime.fromtimestamp(_mark_epoch, timezone.utc).isoformat()
                        _mark_age = max(0, int(__import__("time").time() - _mark_epoch))
                        _mark_source = "Public quote"
                    # else keep ledger/registry equity
                except Exception:
                    pass
            _stf = float(_b.get("start_usd") if _b.get("start_usd") is not None else (_led.get("seed_usd") or _led.get("start") or 0))
            try:
                _basis = float(_led.get("deploy_basis_usd") or 0) or None
            except (TypeError, ValueError):
                _basis = None
            if _basis and _basis > 1:
                _pnl = _eqf - _basis
                _pct = (_eqf / _basis - 1.0) * 100.0
                _basis_label = "since deploy"
            elif _stf > 1:
                _pnl = _eqf - _stf
                _pct = (_eqf / _stf - 1.0) * 100.0
                _basis_label = "vs seed"
            else:
                _pnl, _pct, _basis_label = None, None, None
            _name_lbl = "/".join(_names) if _names else _default_names
            _status = str(_led.get("status") or "")
            _frozen = bool(_led.get("frozen"))
            if (not _names and _eqf < 100) or "RECYCLED" in _status:
                continue
            if _status.startswith("BANKED") or (_frozen and not _names):
                _short_disp = "%s · BANKED" % _short
                if _led.get("locked_pnl_pct") is not None:
                    _name_lbl = "locked %+.2f%% · cash" % float(_led.get("locked_pnl_pct"))
            else:
                _short_disp = _short
            if not _names and not (_status.startswith("BANKED") or _frozen):
                if _eqf < 100:
                    continue
            crypto_sleeves.append({
                "key": _key,
                "short": _short_disp,
                "label": "%s · %s" % (_short, _name_lbl),
                "names": _names,
                "names_label": _name_lbl,
                "equity_usd": round(_eqf, 2),
                "seed_usd": round(_stf, 2) if _stf else None,
                "deploy_basis_usd": None if not _basis else round(_basis, 2),
                "pnl_usd": None if _pnl is None else round(_pnl, 2),
                "pnl_pct": None if _pct is None else round(_pct, 2),
                "basis_label": _basis_label,
                "status": _led.get("status") or "OPEN",
                "label_alpha_beta": _led.get("label_alpha_beta") or "opportunistic beta",
                "mark_utc": _mark_utc, "mark_age_sec": _mark_age,
                "mark_source": _mark_source, "stale": _mark_age is None or _mark_age > 900,
                "cash_usd": round(float(_led.get("cash") or 0), 2),
            })
        _short = _sol_short_pilot_block()
        if _short.get("readable") and (_short["quantity"] > 0 or _short["equity_usd"] >= 100):
            crypto_sleeves.append({
                "key": _short["book_key"], "short": "SOL SHORT · simulated",
                "label": _short["label"], "names": ["SOL SHORT"], "names_label": "PF_SOLUSD · SHORT",
                "equity_usd": round(_short["equity_usd"], 2), "seed_usd": _short["seed_usd"],
                "deploy_basis_usd": _short["deploy_basis_usd"],
                "pnl_usd": round(_short["equity_usd"] - _short["deploy_basis_usd"], 2)
                    if _short["pnl_pct_vs_deploy"] is not None else None,
                "pnl_pct": _short["pnl_pct_vs_deploy"], "basis_label": "net since deploy",
                "status": _short["status"], "label_alpha_beta": "negative crypto beta · simulated only",
                "cash_usd": _short["cash_usd"], "mark_utc": _short["mark_utc"],
                "mark_age_sec": _short["mark_age_sec"], "stale": _short["stale"],
                "simulated_only": True, "verified_account_access": False,
            })
        crypto_sleeves.sort(key=lambda x: -(x.get("equity_usd") or 0))
    except Exception:
        crypto_sleeves = []

    return {
        "firm_equity_usd": round(eq, 2),
        "firm_peak_usd": round(peak, 2),
        "mandate_target_usd": target,
        "taylor_seed_usd": taylor_seed,
        "taylor_seed_label": "Recorded funding (all book funding bases)",
        "funding_complete": funding["complete"],
        "funding_errors": funding["errors"],
        "funding_basis": funding["basis"],
        "pnl_vs_taylor_seed_usd": round(eq - taylor_seed, 2) if funding["complete"] else None,
        "baseline_usd": baseline,
        "pnl_vs_mandate_usd": round(eq - target, 2),
        "pnl_vs_peak_usd": round(eq - peak, 2),
        "pnl_vs_baseline_usd": round(eq - baseline, 2),
        "status": "GREEN" if eq >= target else "SHORT",
        "level": fr.get("level"),
        "gap_close": {
            "equity_usd": round(gcl_eq, 2),
            "seed_usd": round(gcl_seed, 2),
            "pnl_usd": None if gcl_pnl is None else round(gcl_pnl, 2),
            "pnl_pct": None if not gcl_seed else round((gcl_eq / gcl_seed - 1) * 100, 2),
            "display_pnl_usd": None if display_pnl is None else round(float(display_pnl), 2),
            "display_pnl_pct": display_pct,
            "display_basis": display_basis,
            "since_deploy_pnl_usd": None if since_deploy_pnl is None else round(float(since_deploy_pnl), 2),
            "since_deploy_pct": None if since_deploy_pct is None else round(float(since_deploy_pct), 2),
            "units": gcl_units,
            "symbol": "SOXL",
            "status": gcl_status,
            "cash_usd": round(float(gcl.get("cash") or 0), 2),
            # Only meaningful when CLOSED; clients should prefer display_pnl_usd
            "locked_pnl_vs_seed_usd": locked if gcl_closed else None,
            "closed": gcl_closed,
        },
        "fundamental_recovery": fund_recovery,
        "crypto_clips": crypto_clips,
        "crypto_sleeves": crypto_sleeves,
        "crypto_sleeves_total_usd": round(sum((x.get("equity_usd") or 0) for x in crypto_sleeves), 2),
        "top_performers": top,
        "laggards": bottom,
    }

def capital_base(fx):
    """Sum of registered book STARTING EQUITY, from the books' own state files
    where they record one, and from the registering script's constant where
    they do not. Every row is labelled with its source."""
    comps, unregistered = [], []

    def comp(floor, book, start, ccy, source):
        if start is None:
            unregistered.append({"floor": floor, "book": book,
                                 "reason": "no registered start found"})
            return
        comps.append({"floor": floor, "book": book, "start": round(float(start), 2),
                      "ccy": ccy, "source": source})

    comp("Growth Book", "dual momentum", GROWTH_BOOK_START, "USD",
         "growth_book.py START_CASH")
    comp("Value Book", "global value", VALUE_BOOK_START, "USD",
         "value_book.py START_CASH")

    # Funder 2026-09-16 gap sleeves — seed from sealed books (weights shape)
    _wg = _json_file(os.path.join(BASE, "reports", "worldwide_gap_book.json"), {})
    if _wg:
        comp("Worldwide Gap Sleeve", "EWJ/IWM/QQQ/VXUS",
             _wg.get("seed_usd") or _wg.get("start"), "USD",
             "worldwide_gap_book.json seed_usd")
    _gcl = _json_file(os.path.join(BASE, "reports", "gap_close_levered_book.json"), {})
    if _gcl:
        comp("Gap-Close Levered Sleeve", "SOXL 100%",
             _gcl.get("seed_usd") or _gcl.get("start"), "USD",
             "gap_close_levered_book.json seed_usd")

    for ledger, floor in ((FLOOR_LEDGER, "Kraken Floor"),
                          (REPLICA_LEDGER, "Venue Replica")):
        d = _json_file(ledger, {"desks": {}})
        for dk_name in (d.get("desks") or {}):
            comp(floor, dk_name, FLOOR_DESK_START, "USD",
                 "registered floor allocation (tournament.py)")

    cs = _json_file(COINSPOT_LEDGER, {})
    if cs.get("desks"):
        comp("CoinSpot Floor", "AU retail", cs.get("start_cash"), "AUD",
             "coinspot_floor.json start_cash")

    st_e = engine_state()
    if not st_e.get("_unreachable"):
        # USD book: start_cash 40,000 with an all-XXX/USD universe and USD marks
        comp("Kraken Engine", "Chief-PM", st_e.get("start_cash"), "USD",
             "engine /state start_cash")

    if not OANDA_BOOK_REMOVED and os.path.exists(
            os.path.join(OANDA_DIR, "oanda_status.txt")):
        comp("OANDA", "Claude Book", CLAUDE_BOOK_START_NAV, "AUD",
             "funder's first-fill NAV (AUD account)")

    cp = _json_file(CARRY_PROBE_LEDGER, {})
    if cp:
        comp("Carry Probe", "XMR/HYPE", cp.get("seed"), "USD",
             "carry_newnames_probe.json seed")

    cb = _json_file(CARRY_BOOK, {})
    if cb:
        # opens against notional, no registered bank of its own
        unregistered.append({"floor": "Carry Executor", "book": "basis carry",
                             "reason": "no registered starting capital "
                                       "(sized off notional)"})

    import glob as _g2
    for fn in sorted(_g2.glob(os.path.join(BASE, "reports", "tournament", "*.json"))):
        sv = _json_file(fn, {})
        name = sv.get("name", os.path.basename(fn)[:-5])
        comp("Tournament", name, sv.get("start", _SLEEVE_START.get(name, 10000.0)),
             "USD", "sleeve state 'start'" if sv.get("start") is not None
             else "tournament.py SLEEVE_START default")

    usd = sum(c["start"] for c in comps if c["ccy"] == "USD")
    aud = sum(c["start"] for c in comps if c["ccy"] == "AUD")
    total, complete, reason = None, False, None
    if aud and not fx.get("rate"):
        reason = ("AUD capital (A$%s) cannot be converted — %s"
                  % (format(round(aud, 2), ","), fx.get("note") or "no FX rate"))
    else:
        total = round(usd + aud * (fx.get("rate") or 0.0), 2)
        complete = True
    return {"components": comps, "n_books": len(comps),
            "usd_component": round(usd, 2), "aud_component": round(aud, 2),
            "total_usd": total, "complete": complete, "unavailable_reason": reason,
            "unregistered": unregistered,
            "fx_status": fx.get("status"),
            "label": "registered starting capital of %d books" % len(comps)}


# Mirrors tournament.py's own start resolution so the capital base and the
# tournament board agree by construction.
_SLEEVE_START = {"covered_call_etf": 25000.0, "vix_regime_switch": 25000.0,
                 "cef_discount": 10000.0, "claude_discretionary": 10000.0}

# Accounting basis fingerprint. Stamped into the daily anchor so a day whose
# anchor was written under the OLD basis (0.70 FX fallback, engine booked as
# AUD, tournament missing from pnl_by_desk) is reported as not-comparable
# instead of producing a spurious "today" move.
PNL_BASIS = "v2-fx-explicit-engine-usd-tournament-included"


# ---------------------------------------------------------------- rally_rider
# READ-ONLY mirror of the rally_rider sleeve's scale-out ladder. The constants
# below MIRROR tournament.py's frozen RR_* values and tick_rally_rider()'s
# exact semantics — the panel must show the rule as it actually runs:
#   * exits are only checked once bars_held >= 1 (never on the entry bar), and
#     the take-profit factor is sqrt(bars_held) AT THE DECIDING SETTLED BAR —
#     so the levels served use sqrt(max(bars_held, 1)): on the entry bar these
#     are the levels that will govern the FIRST possible check;
#   * tp1 = entry*(1 + 2.0*sigma*sqrt(t)), tp2 = entry*(1 + 4.0*sigma*sqrt(t));
#     take-profits are checked BEFORE the stop on the same settled close;
#   * trail = peak_close*(1 - 2.5*sigma) — peak of settled CLOSES, no sqrt;
#   * time stop at 40 settled bars; every fill books at the triggering close;
#   * sigma is frozen at entry and may be None (<26 settled closes at entry) —
#     the leg is then EXIT-EXEMPT from the sigma legs; only the time stop
#     governs (tournament.py records that in sigma_note; passed through here).
RR_TP1_SIGMA = 2.0
RR_TP2_SIGMA = 4.0
RR_TRAIL_SIGMA = 2.5
RR_TIME_STOP_BARS = 40
RR_UNIVERSE_N = 17            # ETF15 + XBT/USD + ETH/USD, scanned daily
RR_FILE = os.path.join(BASE, "reports", "tournament", "rally_rider.json")
# rally_rider_x — the $100k / 10-slot / ~34-name breadth sibling (registered
# 2026-08-21). Identical frozen ladder constants; only file, state key and
# universe size differ, so the same block builder serves both riders.
RRX_FILE = os.path.join(BASE, "reports", "tournament", "rally_rider_x.json")
RRX_UNIVERSE_N = 34
# 2026-08-22 addendum sleeves (2x2 experiment; same frozen trail/time-stop
# constants). rally_rider_hold is the LET-IT-RIDE twin: its registered rule has
# NO profit-taking legs, so its block is served ladder="trail_only" and must
# NEVER fabricate TP levels. dip_rider uses the FULL rally ladder on inverted
# entries; identical block semantics, same 17-name universe.
RRH_FILE = os.path.join(BASE, "reports", "tournament", "rally_rider_hold.json")
DIP_FILE = os.path.join(BASE, "reports", "tournament", "dip_rider.json")


def _rally_rider_block(path=RR_FILE, state_key="rr", universe_n=None,
                       ladder="full"):
    """Rally Rider ladder block for /api/positions (built inside the cached
    payload). Marks come from the same equity_marks()/crypto_marks() caches
    the tournament sleeve rows just warmed — a cache read, never a new
    network fetch. Any failure is returned IN the block, loudly, so the rest
    of the payload survives and the board can say what broke.

    ladder="trail_only" (rally_rider_hold): the registered rule has no
    profit-taking whatsoever, so tp1/tp2/next_tp are served as None and
    scaled_thirds as None — the trail + time stop are the only exits and the
    panel must say so rather than inventing a ladder."""
    import math
    import time as _t
    gen = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())
    trail_only = (ladder == "trail_only")
    if universe_n is None:
        universe_n = RR_UNIVERSE_N
    base_name = os.path.basename(path)
    try:
        with open(path) as f:
            sv = json.load(f)
    except FileNotFoundError:
        return {"empty": True, "empty_reason": base_name + " missing",
                "universe_n": universe_n, "frozen": False, "legs": [],
                "events": [], "n_events": 0, "generated_utc": gen}
    except Exception as e:                              # noqa: BLE001 - reported
        return {"error": base_name + " unreadable: %s: %s"
                         % (type(e).__name__, str(e)[:160]),
                "legs": [], "events": [], "generated_utc": gen}
    try:
        rr = sv.get(state_key) or {}
        positions = rr.get("positions") or {}
        holds = sv.get("holdings") or {}
        events = rr.get("events") or []
        ev5 = [{"side": e.get("side"), "sym": e.get("sym"),
                "reason": e.get("reason"), "fill_close": e.get("fill_close"),
                "bar_date": e.get("bar_date")} for e in events[-5:]]

        eq_syms = [t for t in positions if "/" not in t]
        kr_syms = [t for t in positions if "/" in t]
        eq_mk = equity_marks(eq_syms) if eq_syms else {}
        kr_mk = crypto_marks(kr_syms) if kr_syms else {}

        legs = []
        for sym, st in positions.items():
            entry = st.get("entry_px")
            sigma = st.get("entry_sigma")
            bars = int(st.get("bars_held") or 0)
            peak = st.get("peak_close")
            scaled = int(st.get("scaled") or 0)
            live = (kr_mk if "/" in sym else eq_mk).get(sym)
            mk = _held_mark(sym, live, entry)
            # sqrt(max(bars,1)): tick_rally_rider never checks an exit on the
            # entry bar (bars_held 0) and its first possible check uses
            # sqrt(1) — these are the code's own semantics, not a smoothing.
            t_eff = max(bars, 1)
            tp1 = tp2 = trail = None
            if sigma and entry and peak:
                if not trail_only:
                    tp1 = entry * (1.0 + RR_TP1_SIGMA * sigma * math.sqrt(t_eff))
                    tp2 = entry * (1.0 + RR_TP2_SIGMA * sigma * math.sqrt(t_eff))
                trail = float(peak) * (1.0 - RR_TRAIL_SIGMA * sigma)
            if trail_only or sigma is None or tp1 is None:
                nxt, nxt_label = None, None
            elif scaled < 1:
                nxt, nxt_label = tp1, "TP1 +2σ√t"
            elif scaled < 2:
                nxt, nxt_label = tp2, "TP2 +4σ√t"
            else:
                nxt, nxt_label = None, None   # 2/3 taken — trail/time only
            legs.append({
                "symbol": sym,
                "units": holds.get(sym),
                "initial_units": st.get("initial_units"),
                "scaled_thirds": None if trail_only else scaled,
                "entry_px": entry,
                "entry_date": st.get("entry_date"),
                "entry_sigma": sigma,
                "sigma_note": st.get("sigma_note"),
                "mark": mk,
                "mark_src": (_equity_mark_details(sym)[0] if "/" not in sym and live else "live" if live else "ledger"),
                "mark_age_sec": (_equity_mark_details(sym)[1] if "/" not in sym and live else None if live else _mark_age(sym)),
                "upnl_pct": round((mk / entry - 1) * 100, 2)
                            if (mk and entry) else None,
                "bars_held": bars,
                "bars_left": max(0, RR_TIME_STOP_BARS - bars),
                "time_stop_bars": RR_TIME_STOP_BARS,
                # bars_held == 0: no exit can trigger until the next settled
                # bar — the levels below govern the first check.
                "entry_bar": bars == 0,
                "peak_close": peak,
                "tp1": round(tp1, 6) if tp1 is not None else None,
                "tp2": round(tp2, 6) if tp2 is not None else None,
                "trail": round(trail, 6) if trail is not None else None,
                "next_tp": round(nxt, 6) if nxt is not None else None,
                "next_tp_label": nxt_label,
                "pct_to_tp_next": round((nxt / mk - 1) * 100, 2)
                                  if (nxt is not None and mk) else None,
                "trail_gap_pct": round((mk - trail) / mk * 100, 2)
                                 if (trail is not None and mk) else None,
            })

        # a held symbol with no exit state is a defect tick_rally_rider
        # rebuilds loudly — carry it so the board can say it too
        gap = [t for t in holds if t not in positions]
        return {
            "empty": not legs,
            "empty_reason": None if legs else "no open positions",
            "universe_n": universe_n,
            "equity": sv.get("equity"),
            "cash": sv.get("cash"),
            "costs": sv.get("costs"),
            "started": sv.get("started"),
            "dd_pct": sv.get("dd_pct"),
            "frozen": bool(sv.get("frozen")),
            "frozen_reason": sv.get("frozen_reason"),
            "updated": sv.get("updated"),
            "last_eval_utc": rr.get("last_eval_utc"),
            "data_error": rr.get("data_error"),
            "state_error": rr.get("state_error"),
            "state_gap": gap,
            "n_events": len(events),
            "events": ev5,
            "legs": legs,
            "ladder": "trail_only" if trail_only else "full",
            "rule": (("no profit-taking by registered rule — trail close ≤ "
                      "peak·(1−2.5σ) and 40-bar time stop only; settled "
                      "closes only") if trail_only else
                     ("scale 1/3 at entry·(1+2σ√t), 1/3 at entry·(1+4σ√t); "
                      "remainder: close ≤ peak·(1−2.5σ) trail or 40-bar time "
                      "stop; settled closes only, TPs checked before the stop")),
            "generated_utc": gen,
        }
    except Exception as e:                              # noqa: BLE001 - reported
        return {"error": base_name + " block failed: %s: %s"
                         % (type(e).__name__, str(e)[:200]),
                "legs": [], "events": [], "generated_utc": gen}


# ---------------------------------------------------------------- capacity_probe
# READ-ONLY mirror of the capacity-discovery sleeve (funder-directed 2026-08-24,
# $100k paper). Its PRIMARY output is not P&L — it is the measured answer to
# "how much can this thin corner actually absorb, at what real cost". So the
# panel leads with deployment, the per-symbol capacity scan and the REFUSALS
# (each carrying its measured shortfall), not with returns. Any failure is
# returned IN the block, loudly, so the rest of the payload survives.
CAPPROBE_FILE = os.path.join(BASE, "reports", "tournament", "capacity_probe.json")


def _capacity_probe_block():
    import time as _t
    gen = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())
    try:
        with open(CAPPROBE_FILE) as f:
            sv = json.load(f)
    except FileNotFoundError:
        return {"empty": True, "empty_reason": "capacity_probe.json missing",
                "legs": [], "refusals": [], "scan": [], "generated_utc": gen}
    except Exception as e:                              # noqa: BLE001 - reported
        return {"error": "capacity_probe.json unreadable: %s: %s"
                         % (type(e).__name__, str(e)[:160]),
                "legs": [], "refusals": [], "scan": [], "generated_utc": gen}
    try:
        cp = sv.get("cap_probe") or {}
        positions = cp.get("positions") or {}
        holds = sv.get("holdings") or {}
        summ = cp.get("capacity_summary") or {}
        scan_raw = cp.get("capacity_scan") or {}
        refus_raw = cp.get("refusals") or {}

        legs = []
        for sym, st in positions.items():
            entry = st.get("entry_px")
            u = holds.get(sym)
            legs.append({
                "symbol": sym, "units": u, "entry_px": entry,
                "entry_date": st.get("entry_date"),
                "entry_sigma": st.get("entry_sigma"),
                "entry_cvd_slope": st.get("entry_cvd_slope"),
                "peak_close": st.get("peak_close"),
                "bars_held": st.get("bars_held"),
                "value_usd": (u * entry) if (u and entry) else None,
            })

        # capacity scan, sorted thinnest-depth first (where the limit bites)
        scan = []
        for sym, r in scan_raw.items():
            mx = r.get("max_fillable_usd")
            tgt = r.get("target_clip_usd")
            scan.append({
                "symbol": sym, "status": r.get("status"),
                "spread_bps": r.get("spread_bps"),
                "depth10_usd": r.get("depth10_usd"),
                "top_of_book_usd": r.get("top_of_book_usd"),
                "max_fillable_usd": mx,
                "target_clip_usd": tgt,
                "clip_fits": bool(mx is not None and tgt and mx >= tgt),
                "snapshot_age_s": r.get("snapshot_age_s"),
            })
        scan.sort(key=lambda x: (x["max_fillable_usd"] is None,
                                 x["max_fillable_usd"] or 0))

        refusals = []
        for sym, r in refus_raw.items():
            refusals.append({
                "symbol": sym, "reason": r.get("reason"),
                "intended_usd": r.get("intended_usd"),
                "fillable_usd": r.get("fillable_usd"),
                "shortfall_usd": r.get("shortfall_usd"),
                "depth10_usd": r.get("depth10_usd"),
                "spread_bps": r.get("spread_bps"),
                "snapshot_age_s": r.get("snapshot_age_s"),
            })
        refusals.sort(key=lambda x: -(x["shortfall_usd"] or 0))

        fills = [{"sym": f.get("sym"), "side": f.get("side"),
                  "fill_close": f.get("fill_close"),
                  "filled_usd": f.get("filled_usd"),
                  "intended_usd": f.get("intended_usd"),
                  "realized_cost_bps": f.get("realized_cost_bps"),
                  "bar_date": f.get("bar_date")}
                 for f in (cp.get("fills") or [])[-6:]]

        return {
            "empty": not (legs or scan),
            "equity": sv.get("equity"), "cash": sv.get("cash"),
            "costs": sv.get("costs"), "started": sv.get("started"),
            "dd_pct": sv.get("dd_pct"), "frozen": bool(sv.get("frozen")),
            "frozen_reason": sv.get("frozen_reason"),
            "start": sv.get("start"),
            "deployed_usd": cp.get("deployed_usd"),
            "deployed_pct": cp.get("deployed_pct_of_equity"),
            "purpose": cp.get("purpose"),
            "capacity_verdict_due": cp.get("capacity_verdict_due"),
            "edge_verdict_min_days": cp.get("edge_verdict_min_days"),
            "signal_breadth": cp.get("signal_breadth"),
            "breadth_error": (cp.get("breadth_error") or {}).get("msg")
                             if isinstance(cp.get("breadth_error"), dict) else None,
            "top_quintile": cp.get("top_quintile") or [],
            "roster_n": cp.get("roster_n"),
            "summary": summ, "scan": scan, "refusals": refusals,
            "legs": legs, "fills": fills,
            "last_eval_utc": cp.get("last_eval_utc"),
            "refusals_at_utc": cp.get("refusals_at_utc"),
            "generated_utc": gen,
        }
    except Exception as e:                              # noqa: BLE001 - reported
        return {"error": "capacity_probe block failed: %s: %s"
                         % (type(e).__name__, str(e)[:200]),
                "legs": [], "refusals": [], "scan": [], "generated_utc": gen}


# ---------------------------------------------------------------- the 24h punt
# READ-ONLY mirror of the funder-directed 24-hour punt (2026-08-26, $10k paper,
# PUMP/USD at 20:1). QUARANTINED from the tournament board by design: its sealed
# pre-registration declares IN ADVANCE that the result proves nothing either way.
# The panel therefore leads with DISTANCE TO LIQUIDATION, not with P&L.
PUNT_FILE = os.path.join(BASE, "reports", "punt_24h.json")


FUNDAMENTAL_FILE = os.path.join(BASE, "reports", "fundamental_book.json")


def _fund_identity(b):
    """CM-052: does the book's equity reconcile? Published, never assumed."""
    try:
        seed = float(b.get("seed_usd") or 0.0)
        idn = (seed + float(b.get("realised_usd") or 0.0)
               - float(b.get("costs_paid_usd") or 0.0)
               + float(b.get("unrealised_usd") or 0.0))
        eq = float(b.get("equity") or 0.0)
        gap = eq - idn
        return {"identity_usd": round(idn, 2), "reported_usd": round(eq, 2),
                "gap_usd": round(gap, 2),
                "reconciles": abs(gap) <= max(500.0, 0.005 * seed) if seed else None,
                "note": ("UNEXPLAINED $%s — equity does not reconcile to "
                         "seed + realised - costs + unrealised. The P&L on this "
                         "card is UNTRUSTWORTHY until resolved."
                         % "{:,.2f}".format(gap))
                        if seed and abs(gap) > max(500.0, 0.005 * seed) else "reconciles"}
    except Exception as e:                              # noqa: BLE001
        return {"error": "%s" % type(e).__name__}


def _fund_auto_deploy():
    """Fundamental auto-deploy rules + last dry/live plan (static/fundamental_autodeploy.json). Info only."""
    try:
        import value_deploy as _vd
        st = {}
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "fundamental_autodeploy.json")) as f: st = json.load(f)
        except Exception: pass
        return {"on": True, "slot_pct": _vd.SLOT_PCT * 100, "slot_max_pct": _vd.SLOT_MAX_PCT * 100, "reserve_pct": _vd.CASH_RESERVE_PCT * 100,
                "max_open": _vd.MAX_OPEN, "max_per_sector": _vd.MAX_PER_SECTOR, "max_pct_adv": _vd.MAX_PCT_ADV * 100, "stop_pct": _vd.STOP_PCT * 100,
                "schedule": "launchd com.tfo.fundamental-autodeploy every 15 min; buys only in US/ASX/LSE/JP regular session (+/-15 min buffers) at executable quotes",
                "last": st}
    except Exception as e:
        return {"on": False, "error": str(e)[:120]}


def _fundamental_block():
    """TFO Fundamental Desk — the $600k funder-directed global long/short book
    ($500k initial + $100k top-up, funder-confirmed 2026-08-29; see CM-054).

    Surfaces the three things a reader must not have to dig for: the standing of
    the evidence (NO validated edge), the modelled borrow on the short book, and
    any name whose borrow is DOUBTFUL — which is an existence question, not a
    cost one.
    """
    import time as _t
    gen = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())
    try:
        with open(FUNDAMENTAL_FILE) as f:
            b = json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:                              # noqa: BLE001 - reported
        return {"error": "fundamental_book.json unreadable: %s: %s"
                         % (type(e).__name__, str(e)[:160]), "generated_utc": gen}
    pos = b.get("positions") or {}
    longs = {k: v for k, v in pos.items() if v.get("side") == "long"}
    shorts = {k: v for k, v in pos.items() if v.get("side") == "short"}
    seed = float(b.get("seed_usd") or 0.0)
    eq = b.get("equity")
    rule = b.get("trade_rule") or {}

    def _top(d, n=5):
        rows = []
        for sym, v in d.items():
            e, m = v.get("entry_px_usd"), v.get("mark_usd")
            if not e or not m:
                continue
            g = ((m - e) / e) if v.get("side") == "long" else ((e - m) / e)
            rows.append({"symbol": sym, "side": v.get("side"),
                         "gross_pct": round(g * 100, 2),
                         "notional": round(v.get("notional_usd") or 0.0, 2),
                         "borrow_tier": v.get("borrow_tier"),
                         "borrow_accrued": round(
                             float(v.get("borrow_accrued_usd") or 0.0), 2),
                         "stale": bool(v.get("mark_stale"))})
        rows.sort(key=lambda r: -r["gross_pct"])
        return rows[:n], rows[-n:]

    lb, lw = _top(longs)
    sb, sw = _top(shorts)
    try:   # 2026-10-28 Brett "fix it": honest since-inception figure alongside the rebased one
        if BASE not in sys.path:
            sys.path.insert(0, BASE)
        import fundamental_true_return as _ftr
        _tr = _ftr.compute(b, _ftr.load_curve())
    except Exception as _e:                              # noqa: BLE001
        _tr = {"error": "%s: %s" % (type(_e).__name__, str(_e)[:120])}
    return {
        "generated_utc": gen,
        "true_return": _tr,
        "auto_deploy": _fund_auto_deploy(),   # 2026-10-28 14:10 Brett fundamental auto-trade
        "label": "TFO Fundamental Desk",
        "equity": eq, "seed": seed,
        "pnl": (round(eq - seed, 2) if isinstance(eq, (int, float)) and seed else None),
        "pnl_pct": (round((eq / seed - 1) * 100, 3)
                    if isinstance(eq, (int, float)) and seed else None),
        "cash": b.get("cash"),
        "n_long": len(longs), "n_short": len(shorts),
        "long_mv": b.get("long_mv_usd"), "short_notional": b.get("short_notional_usd"),
        "unrealised": b.get("unrealised_usd"), "realised": b.get("realised_usd"),
        "costs_paid": b.get("costs_paid_usd"),
        "n_unmarked": b.get("n_unmarked"),
        "updated": b.get("updated"),
        "drawdown_pct": (b.get("book_risk") or {}).get("drawdown_pct"),
        "frozen": (b.get("book_risk") or {}).get("frozen"),
        # the trading rule, so the dashboard states the gate rather than implying one
        "rule": {
            "gate_pct": rule.get("min_net_gain_pct"),
            "round_trip_bps": rule.get("round_trip_bps"),
            "borrow_modelled": rule.get("borrow_cost_modelled"),
            "ladder": "25% at +5% / +10% / +15% net, new highs only; last 25% "
                      "trails 5pp off peak",
        },
        # CM-051 risk rule — the funder-delegated short decision, on the board
        "short_risk": {
            "stop_pct": 25.0,
            "stop_basis": "GROSS adverse price move (mark >= 1.25 x entry); "
                          "borrow can never fire a price stop",
            "applies_to": "SHORTS ONLY — longs are deliberately not stopped; a "
                          "long's loss is bounded and the undervaluation thesis "
                          "needs room to be early",
            "closed_hard_tier": ["MARA", "CLSK", "KEEL", "BKKT", "CLF", "STLA"],
            "closed_reason": "borrow 15%/yr = 7.5% over 182d, so the name must "
                             "fall 12.78% just to clear the 5% gate",
            "evidence": "46 names x 10y x 182d windows: a 25% stop cuts the worst "
                        "case from -5608.87% to -32.78%. A stop does NOT create "
                        "edge — the mean is negative at every level; it converts "
                        "an unbounded loss into a bounded one.",
            "unconditional_short_ev": "median -8.17% / mean -21.57% per name",
            "near_stop": sorted(
                [{"symbol": k,
                  "adverse_pct": round(((v.get("mark_usd") or 0) /
                                        (v.get("entry_px_usd") or 1) - 1) * 100, 2)}
                 for k, v in shorts.items()
                 if v.get("mark_usd") and v.get("entry_px_usd")],
                key=lambda r: -r["adverse_pct"])[:5],
        },
        "borrow": {
            "accrued_total": b.get("borrow_accrued_total_usd"),
            "last_accrual_utc": b.get("borrow_last_accrual_utc"),
            "tiers": (b.get("borrow_model") or {}).get("tier_rates_annual"),
            "doubtful": b.get("borrow_doubtful") or {},
        },
        "held_not_opened": b.get("unopened") or {},
        "best_longs": lb, "worst_longs": lw,
        "best_shorts": sb, "worst_shorts": sw,
        "recent_fills": (b.get("fills") or [])[-5:],
        "equity_identity": _fund_identity(b),
        "standing": ("NO VALIDATED EDGE. Cross-sectional fundamental screen with "
                     "a dated catalyst overlay — no backtest, no search-deflation, "
                     "no CV, no kill-tests. Funder-directed documented exception "
                     "on the rally_rider precedent. Search count travels with it: "
                     "326 screened / 59 directional / 8 undervalued."),
        "caveat": ("Borrow is MODELLED, never quoted — no securities-lending desk "
                   "was asked. Names flagged borrow_doubtful may not be borrowable "
                   "at all and are never presented as executable. PAPER ONLY."),
    }


DATED_EVENT_FILE = os.path.join(BASE, "reports", "dated_event_book.json")


def _dated_event_block():
    """Dated-Event Long Book — $75k from the sleeve, funder-directed 2026-09-06.

    The board carries the honest expectation next to the number: cash + 0 to +3%/yr,
    P(beats cash) ~55%. This book exists because the desk's own tape said held longs
    through dated events were the only thing that ever made money here — not because
    anyone found an edge. Say so on the board or the board lies by omission.
    """
    import time as _t
    gen = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())
    try:
        with open(DATED_EVENT_FILE) as f:
            b = json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:                              # noqa: BLE001
        return {"error": "dated_event_book.json unreadable: %s: %s" % (type(e).__name__, str(e)[:140]),
                "generated_utc": gen}
    from capital_accounting import net_allocated_capital
    seed = net_allocated_capital(b); cash = float(b.get("cash") or 0.0)
    pos = b.get("positions") or {}; pend = b.get("pending") or {}
    mv = sum(float(p.get("mark_usd") or 0) * float(p.get("units") or 0) for p in pos.values())
    eq = cash + mv
    rows = []
    for sym, p in sorted(pos.items()):
        e = float(p.get("entry_px_usd") or 0); m = float(p.get("mark_usd") or 0)
        rows.append({"symbol": sym, "units": p.get("units"), "entry": p.get("entry_px"), "ccy": p.get("entry_ccy"),
                     "mark_usd": m, "ret_pct": round(100 * (m / e - 1), 2) if e else None,
                     "event_date": p.get("event_date"), "exit_date": p.get("planned_exit_date"),
                     "stale": bool(p.get("mark_stale")), "reread": bool(p.get("reread_triggered"))})
    prows = [{"symbol": s_, "ticket": p.get("ticket_usd"), "event_date": p.get("event_date"),
              "fill_session": p.get("fill_session")} for s_, p in sorted(pend.items())]
    return {"generated_utc": gen, "updated_utc": b.get("updated_utc"),
            "seed": seed, "cash": cash, "equity": eq, "pnl": eq - seed,
            "pnl_pct": (100 * (eq / seed - 1)) if seed else None,
            "realised": b.get("realised_usd"), "costs": b.get("costs_paid_usd"),
            "n_open": len(pos), "n_pending": len(pend), "events_completed": b.get("events_completed", 0),
            "events_for_verdict": 20, "positions": rows, "pending": prows,
            "standing": {"headline": "DESK-DERIVED: held longs through dated events — the only thing that ever made money here",
                         "expectation": "cash 3.77% + 0 to +3%/yr excess; central +1%/yr; P(beats cash) ~55%; P(9.49% target) ~15%",
                         "rules": "$7,500 flat · fill at the OPEN, never previousClose · hold >=30 td · no price stop · -25% re-read · no +5% rung",
                         "kill": "strategy dead at 20 events / 120 td if mean net <= 0 or it trails buy-and-hold of its own names"},
            "paper_only": True}

CONVEXITY_FILE = os.path.join(BASE, "reports", "convexity_sleeve.json")


def _convexity_block():
    """Convexity Sleeve — $150k defined-risk, funder-directed 21x target.

    The board publishes the honest standing WITH the number. The funder asked to
    triple the firm in two months; the measured probability of that was 0.000% at
    the firm's own vol and ~5% at 13x leverage with a 41% chance of losing half.
    This sleeve is the bounded alternative, and a board that showed a progress bar
    toward 21x without saying it is a lottery ticket would be lying by omission.
    """
    import time as _t
    gen = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())
    try:
        with open(CONVEXITY_FILE) as f:
            b = json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:                              # noqa: BLE001 - reported
        return {"error": "convexity_sleeve.json unreadable: %s: %s"
                         % (type(e).__name__, str(e)[:140]), "generated_utc": gen}
    seed = float(b.get("seed_usd") or 0.0)
    cash = float(b.get("cash") or 0.0)
    pos = b.get("positions") or {}
    mv = sum(float(p.get("mark_usd") or 0.0) for p in pos.values())
    eq = cash + mv
    committed = sum(float(p.get("max_loss_usd") or 0.0) for p in pos.values())
    target = float(b.get("target_usd") or 0.0)
    span = target - seed
    rows = []
    for sym, p in sorted(pos.items()):
        prem = float(p.get("premium_usd") or 0.0)
        mk = float(p.get("mark_usd") or 0.0)
        rows.append({
            "symbol": sym,
            "instrument": p.get("instrument"),
            "venue": p.get("venue"),
            "max_loss_usd": float(p.get("max_loss_usd") or 0.0),
            "premium_usd": prem,
            "mark_usd": mk,
            "ret_pct": round(100.0 * (mk / prem - 1.0), 2) if prem else None,
            "upside_multiple": p.get("upside_multiple"),
            "catalyst_date": p.get("catalyst_date"),
            "honest_probability": p.get("honest_probability"),
        })
    st = b.get("standing") or {}
    return {
        "generated_utc": gen,
        "updated_utc": b.get("updated_utc"),
        "seed": seed, "cash": cash, "equity": eq,
        "pnl": eq - seed,
        "pnl_pct": (100.0 * (eq / seed - 1.0)) if seed else None,
        "target_usd": target,
        "target_multiple": b.get("target_multiple"),
        "target_date": b.get("target_date"),
        "progress_pct": (100.0 * (eq - seed) / span) if span > 0 else 0.0,
        "committed_risk": committed,
        "risk_cap": seed,
        "max_loss_pct_of_firm": round(100.0 * committed / 1526988.0, 2),
        "n_open": len(pos),
        "n_closed": len(b.get("closed") or []),
        "positions": rows,
        "standing": st,
        "measured": b.get("measured") or {},
        "paper_only": True,
    }

CRYPTO_RUMOUR_FILE = os.path.join(BASE, "reports", "crypto_rumour_book.json")


def _crypto_rumour_block():
    """Crypto Rumour-Rider — $20k declared forward test, two bands.

    The standing is published with the numbers, not buried: this book's own
    backtest FAILED out of sample, and the low-vol band is a CONTROL the
    backtest says loses. A board that shows the P&L without that is lying by
    omission.
    """
    import time as _t
    gen = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())
    try:
        with open(CRYPTO_RUMOUR_FILE) as f:
            b = json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:                              # noqa: BLE001 - reported
        return {"error": "crypto_rumour_book.json unreadable: %s: %s"
                         % (type(e).__name__, str(e)[:140]), "generated_utc": gen}
    seed = float(b.get("seed_usd") or 0.0)
    eq = b.get("equity")
    bands = []
    for k, bk in (b.get("bands") or {}).items():
        cap = float(bk.get("capital") or 0.0)
        beq = float(bk.get("equity") or cap)
        opens = []
        for sym, p in (bk.get("positions") or {}).items():
            e_, m_ = p.get("entry"), p.get("mark") or p.get("entry")
            opens.append({"symbol": sym, "entry": e_, "mark": m_,
                          "ret_pct": round(100 * (m_ / e_ - 1), 2) if e_ and m_ else None,
                          "vol_ratio": (p.get("signal") or {}).get("vol_ratio"),
                          "drift_pct": (p.get("signal") or {}).get("drift_pct"),
                          "stale": bool(p.get("mark_stale"))})
        opens.sort(key=lambda r: -(r["ret_pct"] or -99))
        closed = bk.get("closed") or []
        wins = sum(1 for c in closed if (c.get("pnl_usd") or 0) > 0)
        bands.append({
            "key": k, "label": bk.get("label"), "capital": cap,
            "equity": round(beq, 2), "cash": bk.get("cash"),
            "pnl": round(beq - cap, 2),
            "pnl_pct": round((beq / cap - 1) * 100, 3) if cap else None,
            "n_open": len(opens), "n_closed": len(closed),
            "win_rate": round(100 * wins / len(closed), 1) if closed else None,
            "realised": bk.get("realised_usd"), "costs": bk.get("costs_usd"),
            "drawdown_pct": bk.get("drawdown_pct"), "frozen": bk.get("frozen"),
            "frozen_reason": bk.get("frozen_reason"),
            "open_positions": opens, "recent_closed": closed[-5:],
            "is_control": k == "lowvol",
        })
    bands.sort(key=lambda r: -r["capital"])
    return {
        "generated_utc": gen, "label": "Crypto Rumour-Rider",
        "equity": eq, "seed": seed,
        "pnl": round((eq or 0) - seed, 2) if isinstance(eq, (int, float)) else None,
        "pnl_pct": round(((eq or 0) / seed - 1) * 100, 3)
                   if isinstance(eq, (int, float)) and seed else None,
        "updated": b.get("updated"),
        "closed_trades": b.get("closed_trades"), "max_closed": 100,
        "days_live": b.get("days_live"), "max_days": 182,
        "experiment_over": b.get("experiment_over"),
        "firm_halt": b.get("firm_halt"),
        "cost_bps_per_side": b.get("cost_bps_per_side"),
        "bands": bands,
        "rule": {
            "entry": "volume >= 3.0x its own 20d median AND +5% over 5 days, "
                     "REFUSED above 6.0x (that is the news, not the rumour)",
            "exit": "volume CLIMAX >= 6.0x (sell INTO it) | stop -12% | max hold 5d",
            "venue": "modelled at Kraken 40 bps/side (MEASURED). Binance 10 bps is "
                     "cheaper but is not the venue this desk trades — assuming a "
                     "fee tier we do not have is the CM-042 error.",
        },
        "verdict": {
            "headline": "NO VALIDATED EDGE — the backtest FAILED out of sample",
            "oos": "recent half: mean -0.055%, t -0.06 at 40bps (older half was "
                   "+2.601%, t 2.44 — the edge decayed)",
            "concentration": "155% of all backtest profit came from the best 5% "
                             "of trades; remove them and it loses money",
            "mechanism": "the climax exit fired on only 19% of trades — the rest "
                         "is a 5-day momentum hold with a stop",
            "bands": "top-50 +1.292% (t 1.96) vs low-vol -0.345% (t -0.79) at "
                     "40bps — the signal is in the LIQUID band, the opposite of "
                     "the retail thin-market thesis. Low-vol runs as a CONTROL.",
            "search_count": "16 parameter sets x 2 fee levels = 32 tests; best t "
                            "1.98 vs 1.92 expected max under the null",
        },
        "kill": {"halt_dd_pct": -20.0, "max_closed_trades": 100, "max_days": 182,
                 "note": "halts NEW entries only; exits are never blocked"},
    }


def _punt_block():
    import time as _t
    gen = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())
    try:
        with open(PUNT_FILE) as f:
            b = json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:                              # noqa: BLE001 - reported
        return {"error": "punt_24h.json unreadable: %s: %s"
                         % (type(e).__name__, str(e)[:160]), "generated_utc": gen}
    state = ("LIQUIDATED" if b.get("liquidated_utc") else
             "CLOSED" if b.get("closed_utc") else
             "OPEN" if b.get("units") else "unopened")
    return {
        "state": state, "equity": b.get("equity"), "start": b.get("start"),
        "pnl": b.get("pnl"), "pnl_pct": b.get("pnl_pct"),
        "leverage": b.get("leverage"), "mark": b.get("mark"),
        "entry": b.get("entry"), "symbol": "PUMP/USD",
        "notional": (b.get("units") or 0) * (b.get("mark") or 0),
        "borrowed": b.get("borrowed"), "borrow_accrued": b.get("borrow_accrued"),
        "liq_price": b.get("liq_price"),
        "pct_to_liquidation": b.get("pct_to_liquidation"),
        "hours_left": b.get("hours_left"), "costs": b.get("costs"),
        "liquidated_utc": b.get("liquidated_utc"),
        "liquidated_at_price": b.get("liquidated_at_price"),
        "closed_utc": b.get("closed_utc"),
        "intraday_low_seen": b.get("intraday_low_seen"),
        "intraday_bars_checked": b.get("intraday_bars_checked"),
        "events": (b.get("events") or [])[-4:],
        "error": b.get("error"),
        "caveat": ("Sealed pre-registration: this result proves NOTHING either "
                   "way and may never be cited as evidence. Quarantined from the "
                   "tournament board."),
        "generated_utc": gen,
    }


def positions_payload():
    """Every open position on every floor, marked live where possible."""
    rows = []
    short_pilot = _sol_short_pilot_block()
    short_row = _sol_short_position_row(short_pilot)
    if short_row:
        rows.append(short_row)
    coverage_errors = (["SOL short pilot: " + short_pilot["error"]]
                       if short_pilot.get("error") else [])

    # registered books (reported in USD -- see the FX note below)
    #
    # THE FIRM TOTAL MUST USE THE SAME FX AS THE BOOK ITSELF.
    # value_book.py was corrected to translate quote currencies to USD, but this
    # function valued the same holdings independently at the RAW LOCAL mark, so
    # the published firm total stayed wrong: 005930.KS (KRW) booked at $5,954
    # against a true $4.22, BARC.L (GBp = PENCE) at $4,937 against $66.82.
    # Measured at one instant, combined_usd published $319,185.56 against an
    # FX-correct $309,144.03 -- overstated by $10,041.53.
    #
    # Two code paths computing one number is how that survived the first fix.
    # This path now uses the same fx helper the book uses. Where a rate is
    # unavailable the row is marked UNCONVERTED and its value/upnl are None, so
    # it is visibly missing rather than silently counted at the wrong scale.
    for book_file, floor in (("growth_book.json", "Growth Book"),
                             ("value_book.json", "Value Book"),
                             ("worldwide_gap_book.json", "Worldwide Gap Sleeve"),
                             ("gap_close_levered_book.json", "Gap-Close Levered Sleeve"),
                             ("crypto_clip_book.json", "Crypto Clip Book"),
                             ("crypto_mid_clip_book.json", "Mid Clip Book"),
                             ("soxl_weekend_probe.json", "SOXL Probe"),
                             ("crypto_weekend_rs_probe.json", "Weekend RS Probe"),
                             ("crypto_liq_beta_book.json", "Liq beta — BTC/ETH/XRP"),
                             ("crypto_uni_sui_rs.json", "UNI/SUI RS clip"),
                             ("crypto_avax_ondo_ltc.json", "Clip B — AVAX/ONDO/LTC"),
                             # HT decision 2026-09-26 #4d: Head Trader crypto clips (rails mark it every 60s).
                             # Capital arrived by paired transfer from the Gap-Close powder, so it adds no
                             # capital-base component (that would double count the powder seed).
                             ("tfo_ht_crypto.json", "TFO Head Trader Crypto")):
        b = _json_file(os.path.join(BASE, "reports", book_file), {})
        holds = b.get("holdings", {})
        entry = b.get("entry_px", {})
        # Kraken USD crypto sleeves — never Yahoo equity marks (stale XRP/ARB/NEAR).
        if book_file in ("crypto_clip_book.json", "weekend_liq_crypto_hunt.json", "crypto_small_clip_book.json", "crypto_mid_clip_book.json", "crypto_weekend_rs_probe.json", "crypto_liq_beta_book.json", "crypto_uni_sui_rs.json", "crypto_avax_ondo_ltc.json", "tfo_ht_crypto.json"):
            marks = crypto_marks(list(holds)) if holds else {}
        else:
            marks = equity_marks(list(holds)) if holds else {}

        # Resolve one rate per currency for this book, once.
        # NOTE: fetch_usd_rates returns a TUPLE (rates, asof) -- passing the tuple
        # straight to usd_per_quote_unit silently yields no rate for every
        # non-USD name, which reads as "rate unavailable" rather than as the
        # caller error it is. Unpack it.
        _fx_rates, _fx_asof, _fx_err = {"USD": 1.0}, None, None
        try:
            import fx as _fx
            _ccys = {t: _fx.quote_currency(t) for t in holds}
            _needed = {c for c in _ccys.values() if c}
            if _needed:
                _fx_rates, _fx_asof = _fx.fetch_usd_rates(_needed)
        except Exception as _e:                     # noqa: BLE001 - reported, not swallowed
            _ccys, _fx_err = {t: None for t in holds}, "%s: %s" % (type(_e).__name__, _e)

        for t, u in holds.items():
            e = entry.get(t)
            mk = marks.get(t) or (b.get("mark_px_usd") or {}).get(t) or _held_mark(t, None, e)
            ccy = _ccys.get(t)
            # USD per one quoted unit (handles GBp=pence via the minor-unit scale).
            usd_per_unit, conv_note = None, None
            if ccy:
                try:
                    import fx as _fx
                    usd_per_unit = _fx.usd_per_quote_unit(ccy, _fx_rates)
                except Exception as _e:             # noqa: BLE001
                    conv_note = "no USD rate for %s (%s)" % (ccy, type(_e).__name__)
            elif _fx_err:
                conv_note = "fx helper unavailable (%s)" % _fx_err

            if usd_per_unit is None:
                val = upnl = mk_usd = e_usd = None
                conv_note = conv_note or "quote currency unknown"
            else:
                mk_usd = mk * usd_per_unit if mk else None
                e_usd = e * usd_per_unit if e else None
                val = u * mk_usd if mk_usd else None
                upnl = (u * (mk_usd - e_usd)) if (mk_usd and e_usd) else None

            rows.append({"floor": floor, "book": floor, "symbol": t, "side": "long",
                         "units": round(u, 4), "entry": e, "mark": mk,
                         "value": val, "upnl": upnl,
                         # percentage is currency-neutral, so it survives a missing rate
                         "upnl_pct": round((mk / e - 1) * 100, 2) if (mk and e) else None,
                         "ccy": "USD",
                         "quote_ccy": ccy, "fx_usd_per_unit": usd_per_unit,
                         "mark_local": mk, "mark_usd": mk_usd,
                         "unconverted": usd_per_unit is None,
                         "fx_note": conv_note,
                         "src": (_equity_mark_details(t)[0] if "/" not in t and t in marks else "live" if t in marks else "ledger"),
                         "mark_age_sec": (_equity_mark_details(t)[1] if "/" not in t and t in marks else None if t in marks else _mark_age(t))})

    # TFO Fundamental Desk — positions dict (not holdings). Was missing from
    # Deployed / open-position table entirely (~$500k+), so portfolio all-time
    # fought the firm hero. Use the book's own USD marks (already FX-translated).
    _fund = _json_file(FUNDAMENTAL_FILE, {}) or {}
    for sym, pos in (_fund.get("positions") or {}).items():
        try:
            u = float(pos.get("units") or 0)
        except (TypeError, ValueError):
            continue
        if abs(u) < 1e-12:
            continue
        e = pos.get("entry_px_usd")
        if e is None:
            e = pos.get("entry_px")
        mk = pos.get("mark_usd")
        if mk is None:
            mk = pos.get("mark")
        try:
            e = float(e) if e is not None else None
            mk = float(mk) if mk is not None else None
        except (TypeError, ValueError):
            e, mk = None, None
        upnl = pos.get("unrealised_usd")
        try:
            upnl = float(upnl) if upnl is not None else (
                (u * (mk - e)) if (mk is not None and e is not None) else None)
        except (TypeError, ValueError):
            upnl = None
        val = (u * mk) if mk is not None else pos.get("notional_usd")
        try:
            val = float(val) if val is not None else None
        except (TypeError, ValueError):
            val = None
        rows.append({
            "floor": "TFO Fundamental Desk",
            "book": "Fundamental Desk",
            "symbol": sym,
            "side": pos.get("side") or "long",
            "units": round(u, 4),
            "entry": e,
            "mark": mk,
            "value": val,
            "upnl": upnl,
            "upnl_pct": (round((mk / e - 1) * 100, 2) if (mk and e) else None),
            "ccy": "USD",
            "quote_ccy": pos.get("entry_ccy") or "USD",
            "fx_usd_per_unit": pos.get("mark_fx_usd") or pos.get("entry_fx_usd") or 1.0,
            "mark_local": pos.get("mark"),
            "mark_usd": mk,
            "unconverted": False,
            "fx_note": None,
            "src": _position_mark_details(pos)[0],
            "mark_age_sec": _position_mark_details(pos)[1],
            "mark_utc": pos.get("mark_quote_utc") or pos.get("mark_utc"),
            "recovery_sleeve": bool(pos.get("recovery_sleeve")),
        })

    # 2026-10-30 feedfix2: Veteran Desk (VD, 11 seats incl. Victor Crane / banks) open positions were
    # missing from this payload (only the Fundamental Desk was listed). Read-only from the book's own
    # file; marks are the book's last tick marks; USD via the book's mark_notional_aud x audusd.
    try:
        _vd = _json_file(os.path.join(BASE, "reports", "veteran_desk_book.json"), {}) or {}
        _vd_audusd = float(_vd.get("audusd") or 0) or None
        for _seat, _sd in (_vd.get("seats") or {}).items():
            for _pid, pos in ((_sd or {}).get("positions") or {}).items():
                if (pos or {}).get("status") != "open":
                    continue
                try:
                    u = float(pos.get("shares") or 0)
                    fill = float(pos.get("fill") if pos.get("fill") is not None else pos.get("entry"))
                    mk = float(pos["mark"]) if pos.get("mark") is not None else None
                except (TypeError, ValueError):
                    continue
                if abs(u) < 1e-12:
                    continue
                short = str(pos.get("side") or "").upper() == "SHORT"
                notional_aud = pos.get("mark_notional_aud")
                aud_per_local = (abs(float(notional_aud)) / abs(u * mk)) if (notional_aud and mk) else None
                usd_per_local = (aud_per_local * _vd_audusd) if (aud_per_local and _vd_audusd) else None
                upnl_local = (u * (mk - fill) * (-1 if short else 1)) if mk is not None else None
                rows.append({
                    "floor": "TFO Veteran Desk",
                    "book": "Veteran Desk · " + str(_seat),
                    "symbol": pos.get("ticker"),
                    "side": "short" if short else "long",
                    "units": round(u, 4),
                    "entry": (fill * usd_per_local) if usd_per_local else fill,
                    "mark": (mk * usd_per_local) if (usd_per_local and mk is not None) else mk,
                    "value": (abs(float(notional_aud)) * _vd_audusd) if (notional_aud and _vd_audusd) else None,
                    "upnl": round(upnl_local * usd_per_local, 2) if (upnl_local is not None and usd_per_local) else None,
                    "upnl_pct": (round((mk / fill - 1) * 100 * (-1 if short else 1), 2) if (mk and fill) else None),
                    "ccy": "USD",
                    "quote_ccy": pos.get("venue") or "",
                    "fx_usd_per_unit": usd_per_local,
                    "mark_local": mk,
                    "mark_usd": (mk * usd_per_local) if (usd_per_local and mk is not None) else None,
                    "unconverted": usd_per_local is None,
                    "fx_note": None if usd_per_local else "no book notional/audusd - local ccy",
                    "src": "veteran_desk_book (last tick mark)",
                    "mark_age_sec": (round(float(pos["px_age_min"]) * 60) if pos.get("px_age_min") is not None else None),
                    "mark_utc": pos.get("mark_utc"),
                    "stop_local": pos.get("stop"),
                    "position_id": pos.get("id") or _pid,
                    "recovery_sleeve": False,
                })
    except Exception as _vd_e:
        coverage_errors.append("Veteran Desk: %s" % _vd_e)

    # forward-test floors (crypto)
    for ledger, floor in ((FLOOR_LEDGER, "Kraken Floor"),
                          (REPLICA_LEDGER, "Venue Replica")):
        d = _json_file(ledger, {"desks": {}})
        syms = [s2 for dk in d.get("desks", {}).values()
                for s2 in dk.get("positions", {})]
        marks = crypto_marks(syms) if syms else {}
        for desk_name, dk in d.get("desks", {}).items():
            for s2, pos in dk.get("positions", {}).items():
                e = pos.get("entry")
                mk = _held_mark(s2, marks.get(s2), pos.get("mark") or e)
                cost_basis = pos.get("cost", 0.0)
                if e and mk:
                    move = (mk - e) / e if pos.get("side") == "long" else (e - mk) / e
                    upnl = move * cost_basis
                else:
                    upnl = pos.get("upnl")
                rows.append({"floor": floor, "book": desk_name, "symbol": s2,
                             "side": pos.get("side"), "units": round(pos.get("units", 0), 4),
                             "entry": e, "mark": mk, "value": cost_basis,
                             "upnl": round(upnl, 2) if upnl is not None else None,
                             "upnl_pct": round(((mk - e) / e if pos.get("side") == "long"
                                                else (e - mk) / e) * 100, 2)
                             if (e and mk) else None,
                             "ccy": "USD", "src": "live" if s2 in marks else "ledger",
                             "mark_age_sec": None if s2 in marks else _mark_age(s2)})

    # CoinSpot (AU) paper floor — AUD retail venue, same algo, public data only.
    # The floor marks unrealized from live CoinSpot bids internally but does not
    # persist a per-position mark, so open rows show at entry (pending) until a
    # mark is stored; realized P&L still surfaces on the desk card below.
    cs = _json_file(COINSPOT_LEDGER, {"desks": {}})
    _cs_now = __import__("time").time()
    _cs_saved = cs.get("updated")            # epoch of the book's last save
    for desk_name, dk in cs.get("desks", {}).items():
        for s2, pos in dk.get("positions", {}).items():
            e = pos.get("entry")
            mk = pos.get("mark") or e
            cost_basis = pos.get("cost", 0.0)
            upnl = ((mk - e) / e * cost_basis) if (e and mk and mk != e) else None
            # mark age (display honesty): a stored mark is as old as the
            # ledger's last save; the entry-price fallback is as old as the
            # entry fill itself (entry_epoch). Unknown stays None, never 0.
            if pos.get("mark") and _cs_saved is not None:
                try:
                    _saved_f = float(_cs_saved)
                except (TypeError, ValueError):
                    # ISO timestamps (e.g. after manual ledger edits) — parse, never crash the feed
                    try:
                        _iso = str(_cs_saved).replace("Z", "+00:00")
                        _saved_f = __import__("datetime").datetime.fromisoformat(_iso).timestamp()
                    except Exception:
                        _saved_f = None
                cs_age = max(0, int(_cs_now - _saved_f)) if _saved_f is not None else None
            elif not pos.get("mark") and pos.get("entry_epoch"):
                try:
                    cs_age = max(0, int(_cs_now - float(pos["entry_epoch"])))
                except (TypeError, ValueError):
                    cs_age = None
            else:
                cs_age = None
            rows.append({"floor": "CoinSpot Floor", "book": desk_name,
                         "symbol": s2.upper(), "side": pos.get("side", "long"),
                         "units": round(pos.get("units", 0), 6),
                         "entry": e, "mark": mk, "value": cost_basis,
                         "upnl": round(upnl, 2) if upnl is not None else None,
                         "upnl_pct": round((mk / e - 1) * 100, 2)
                         if (e and mk and mk != e) else None,
                         "ccy": "AUD", "src": ("live CoinSpot quote" if pos.get("mark") and cs_age is not None and cs_age <= 120 else "ledger CoinSpot quote"),
                         "mark_age_sec": cs_age})

    # Carry New-Names Probe (USD) — delta-neutral basis carry, XMR/HYPE, $10k
    # paper. Open legs harvest funding only; the two legs are delta-neutral so the
    # net directional value is ~0 and funding_accrued is the live P&L. Illiquid
    # marks are flagged in src, never headlined. Read-only.
    cp = _json_file(CARRY_PROBE_LEDGER, {})
    for name, pos in (cp.get("positions") or {}).items():
        illiq = " · ILLIQUID" if pos.get("illiquid") else ""
        rows.append({"floor": "Carry Probe", "book": "XMR/HYPE",
                     "symbol": name, "side": pos.get("side", "long spot / short perp"),
                     "units": None, "entry": pos.get("entry"),
                     "mark": pos.get("spot_mark") or pos.get("entry"),
                     "value": pos.get("notional"),
                     "upnl": round(pos.get("funding_accrued") or 0.0, 2),
                     "upnl_pct": None, "ccy": "USD",
                     "src": (pos.get("mark_src") or "ledger") + illiq})

    # tournament sleeves — real deployed positions, one row each
    import glob as _g
    for fn in sorted(_g.glob(os.path.join(BASE, "reports", "tournament", "*.json"))):
        sv = _json_file(fn, {})
        name = sv.get("name", os.path.basename(fn)[:-5])
        entry = sv.get("entry_px", {})
        holds = sv.get("holdings", {})
        # PF_* are perps: priced from the thin order-book stream, not from the
        # spot ticker or yfinance (neither lists them) — see perp_marks().
        eq_marks = equity_marks([t for t in holds
                                 if "/" not in t and not t.startswith("PF_")]) \
            if holds else {}
        kr_marks = crypto_marks([t for t in holds if "/" in t]) if any(
            "/" in t for t in holds) else {}
        pf_marks = perp_marks([t for t in holds if t.startswith("PF_")]) if any(
            t.startswith("PF_") for t in holds) else {}
        for t, u in holds.items():
            e = entry.get(t)
            pf = pf_marks.get(t)
            if pf:
                # a perp we could actually price
                mk = pf["mid"]
                fresh = pf["age_s"] <= PERP_FRESH_S
                src = ("live (perp book)" if fresh
                       else "perp book STALE %ds" % pf["age_s"])
                age = None if fresh else pf["age_s"]
            else:
                mk = _held_mark(t, (kr_marks if "/" in t else eq_marks).get(t), e)
                live = t in eq_marks or t in kr_marks
                src = "live" if live else "ledger"
                age = None if live else _mark_age(t)
                if "/" not in t and t in eq_marks:
                    src, age = _equity_mark_details(t)
            rows.append({"floor": "Tournament", "book": name, "symbol": t,
                         "side": "long", "units": round(u, 4), "entry": e,
                         "mark": mk, "value": (u * mk) if mk else None,
                         "upnl": round(u * (mk - e), 2) if (mk and e) else None,
                         "upnl_pct": round((mk / e - 1) * 100, 2) if (mk and e) else None,
                         "ccy": "USD",
                         "src": src,
                         "mark_age_sec": age})
        call = sv.get("short_call")
        if call:
            n = call.get("contracts", 1)
            rows.append({"floor": "Tournament", "book": name,
                         "symbol": "%s C%.0f %s" % (sv.get("underlying", "?"),
                                                    call["strike"], call["expiry"]),
                         "side": "short", "units": n, "entry": call.get("premium"),
                         "mark": None, "value": -call.get("premium", 0) * 100 * n,
                         "upnl": None, "upnl_pct": None, "ccy": "USD",
                         "src": "ledger (marked on board)"})

    # OANDA Claude Book — parsed from the railed wrapper's status snapshot
    oanda_upnl, oanda_nav = 0.0, None
    try:
        if OANDA_BOOK_REMOVED:
            # Decommissioned 2026-08-28. Leave with nav=None and no rows, which
            # is exactly what this block already produced once the status file
            # went empty — so every downstream consumer is on a path it has
            # already been exercising, not a new one.
            raise _BookRemoved()
        import re as _re
        # quote->AUD conversion factors from the ledger's last target set
        conv = {}
        try:
            last_led = None
            with open("/Users/test/Downloads/oanda-desk/ledger.jsonl") as lf:
                for line in lf:
                    if line.strip():
                        last_led = line
            if last_led:
                for t in json.loads(last_led).get("targets", []):
                    conv[t["instrument"]] = float(t.get("quote_to_aud") or 1.0)
        except Exception:
            pass
        snap = open("/Users/test/Downloads/oanda-desk/oanda_status.txt").read()
        mn = _re.search(r"NAV ([\d,]+\.\d+)", snap)
        if mn:
            oanda_nav = float(mn.group(1).replace(",", ""))
        for m in _re.finditer(
                r"^\s+(\S+)\s+([+-][\d.]+)u @ ([\d.]+)\s+PL\s+([+-][\d.]+)\s+stop ([\d.]+)",
                snap, _re.M):
            ins, units, entry, pl, stop = m.groups()
            u, e = float(units), float(entry)
            val = abs(u) * e * conv.get(ins, 1.0)      # AUD notional at entry
            rows.append({"floor": "OANDA", "book": "Claude Book",
                         "symbol": ins, "side": "long" if u > 0 else "short",
                         "units": u, "entry": e,
                         "mark": None, "value": round(val, 2),
                         "upnl": float(pl),
                         "upnl_pct": round(float(pl) / val * 100, 2) if val else None,
                         "ccy": "AUD", "src": "desk snapshot"})
            oanda_upnl += float(pl)
    except Exception:
        pass

    # live engine (USD)
    #
    # CURRENCY CORRECTION: these rows were stamped "AUD". The engine is a USD
    # book — /state reports start_cash 40000 against an all-XXX/USD universe
    # with USD marks — so every engine row was being folded into the firm total
    # at the AUDUSD rate and understated by ~29%.
    #
    # The engine publishes no per-position mark, so `upnl` is genuinely absent.
    # That is now said in `src` rather than left to look like a live-marked row
    # that happens to have no P&L.
    st = engine_state()
    if not st.get("_unreachable"):
        for s2, pos in (st.get("positions") or {}).items():
            rows.append({"floor": "Kraken Engine", "book": "Chief-PM",
                         "symbol": s2, "side": pos.get("side", "long"),
                         "units": pos.get("units"), "entry": pos.get("entry"),
                         "mark": pos.get("mark"), "value": pos.get("cost"),
                         "upnl": pos.get("upnl"), "upnl_pct": None,
                         "ccy": "USD",
                         "src": "engine" if pos.get("mark")
                                else "engine (no per-position mark)"})

    # carry executor (A$)
    cb = _json_file(CARRY_BOOK, {})
    for base_a, pos in (cb.get("positions") or {}).items():
        rows.append({"floor": "Carry Executor", "book": "basis carry",
                     "symbol": base_a, "side": "long spot / short perp",
                     "units": None, "entry": pos.get("entry_mark"),
                     "mark": None, "value": pos.get("notional_aud"),
                     "upnl": pos.get("funding_accrued_aud"), "upnl_pct": None,
                     "ccy": "AUD", "src": "ledger"})

    # flag (do not convert) rows whose quote currency is not USD
    quote_ccy_warning = _flag_quote_ccy(rows)

    totals = {}
    for r in rows:
        t = totals.setdefault(r["ccy"], {"value": 0.0, "upnl": 0.0, "n": 0})
        t["n"] += 1
        t["value"] += r.get("value") or 0.0
        t["upnl"] += r.get("upnl") or 0.0
    for t in totals.values():
        t["value"] = round(t["value"], 2)
        t["upnl"] = round(t["upnl"], 2)

    # ---- P&L split by desk: realized (from ledgers) + live unrealized (from rows) ----
    live_upnl, live_n = {}, {}
    for r in rows:
        key = (r["floor"], r.get("book") or r["floor"])
        live_upnl[key] = live_upnl.get(key, 0.0) + (r.get("upnl") or 0.0)
        live_n[key] = live_n.get(key, 0) + 1

    desks = []

    def add(floor, book, realized, unreal, ccy="USD", extra="", note=None):
        # realized/unrealized may legitimately be unknown. They are carried as
        # null and reported as unavailable — never coerced to 0.0, which would
        # silently move the missing amount into the other line.
        tot = (None if (realized is None or unreal is None)
               else round(realized + unreal, 2))
        desks.append({"floor": floor, "book": book,
                      "realized": None if realized is None else round(realized, 2),
                      "unrealized": None if unreal is None else round(unreal, 2),
                      "total": tot, "ccy": ccy,
                      "open": live_n.get((floor, book if book else floor),
                                         live_n.get((floor, floor), 0)),
                      "extra": extra, "note": note})

    gb = _json_file(GROWTH_BOOK, {})
    add("Growth Book", "dual momentum", 0.0 - gb.get("costs_paid", 0.0),
        live_upnl.get(("Growth Book", "Growth Book"), 0.0), extra="vs $50k start")
    vb = _json_file(os.path.join(BASE, "reports", "value_book.json"), {})
    add("Value Book", "global value", 0.0 - vb.get("costs_paid", 0.0),
        live_upnl.get(("Value Book", "Value Book"), 0.0), extra="vs $50k start")

    # GAPCLOSE_DESK_TILE_V1 — largest risk sleeves must appear on the desk strip
    gclb = _json_file(os.path.join(BASE, "reports", "gap_close_levered_book.json"), {}) or {}
    try:
        gcl_eq = float(gclb.get("equity") or 0)
        gcl_seed = float(gclb.get("seed_usd") or gclb.get("start") or 0)
    except (TypeError, ValueError):
        gcl_eq, gcl_seed = 0.0, 0.0
    gcl_upnl = live_upnl.get(("Gap-Close Levered Sleeve", "Gap-Close Levered Sleeve"), 0.0)
    gcl_realized = (gcl_eq - gcl_seed - gcl_upnl) if gcl_seed else None
    add("Gap-Close Levered Sleeve", "Gap-Close Levered Sleeve", gcl_realized, gcl_upnl,
        extra=(str(gclb.get("status") or "OPEN") + " · vs sleeve seed"))

    fdb = _json_file(FUNDAMENTAL_FILE, {}) or {}
    try:
        fd_eq = float(fdb.get("equity") or 0)
        fd_seed = float(fdb.get("seed_usd") or fdb.get("start") or 0)
    except (TypeError, ValueError):
        fd_eq, fd_seed = 0.0, 0.0
    fd_upnl = live_upnl.get(("TFO Fundamental Desk", "Fundamental Desk"), 0.0)
    fd_realized = (fd_eq - fd_seed - fd_upnl) if fd_seed else None
    add("TFO Fundamental Desk", "Fundamental Desk", fd_realized, fd_upnl,
        extra=("vs seed · recovery SOXL inside"
               if fdb.get("fundamental_recovery") else "vs seed"))

    # CRYPTO_CLIP_DESK_TILE_V1
    ccb = _json_file(os.path.join(BASE, "reports", "crypto_clip_book.json"), {}) or {}
    try:
        cc_eq = float(ccb.get("equity") or 0)
        cc_seed = float(ccb.get("seed_usd") or ccb.get("start") or 0)
    except (TypeError, ValueError):
        cc_eq, cc_seed = 0.0, 0.0
    cc_upnl = live_upnl.get(("Crypto Clip Book", "Crypto Clip Book"), 0.0)
    cc_realized = (cc_eq - cc_seed - cc_upnl) if cc_seed else None
    add("Crypto Clip Book", "Crypto Clip Book", cc_realized, cc_upnl,
        extra=(str(ccb.get("status") or "OPEN") + " · BTC/ETH/SOL clips · opportunistic beta"))

    if short_pilot.get("available"):
        if short_pilot.get("readable"):
            short_realized = (short_pilot["realised_usd"] + short_pilot["realised_funding_usd"]
                              - short_pilot["costs_paid_usd"])
            short_unrealized = (short_pilot["unrealised_usd"] + short_pilot["funding_accrued_usd"]
                                - short_pilot["estimated_exit_fee_usd"])
            add("SOL short pilot · SIMULATED", "SOL short pilot · SIMULATED",
                short_realized, short_unrealized,
                extra="SHORT · net of recorded fees, funding and estimated exit fee",
                note="Simulated only; account access unverified. Capital transfers excluded from profit.")
        else:
            add("SOL short pilot · SIMULATED", "SOL short pilot · SIMULATED", None, None,
                extra="UNREADABLE", note=short_pilot.get("error"))

    for ledger, floor in ((FLOOR_LEDGER, "Kraken Floor"),
                          (REPLICA_LEDGER, "Venue Replica")):
        d = _json_file(ledger, {"desks": {}})
        for dk_name, dk in d.get("desks", {}).items():
            add(floor, dk_name, dk.get("realized_pnl", 0.0),
                live_upnl.get((floor, dk_name), 0.0),
                extra="%d closed" % len(dk.get("closed_trades", [])))

    # CoinSpot (AU) paper floor — AUD, same algo on Australian retail costs
    cs2 = _json_file(COINSPOT_LEDGER, {"desks": {}})
    for dk_name, dk in cs2.get("desks", {}).items():
        add("CoinSpot Floor", dk_name, dk.get("realized_pnl", 0.0),
            live_upnl.get(("CoinSpot Floor", dk_name), 0.0), ccy="AUD",
            extra="%d closed · AU retail" % len(dk.get("closed_trades", [])))

    # Kraken Engine — USD book (see the row block above for the ccy correction).
    #
    # The realized line used to be back-derived as
    #     realized = equity - start_cash - sum(row upnl)
    # and every engine row carries upnl=None, so the subtracted term was always
    # 0.0 and the whole open P&L was swept into "realized". The engine publishes
    # both halves itself; read them instead of re-deriving one from the other.
    st_e = engine_state()
    if not st_e.get("_unreachable"):
        ep = st_e.get("pnl") or {}
        e_real = ep.get("realized")
        if e_real is None:
            e_real = st_e.get("realized_pnl")
        e_unreal = ep.get("unrealized")
        note = None
        if e_real is None or e_unreal is None:
            note = ("engine did not publish %s — shown as unavailable rather "
                    "than back-derived"
                    % (" and ".join([n for n, v in (("realized", e_real),
                                                    ("unrealized", e_unreal))
                                     if v is None])))
        add("Kraken Engine", "Chief-PM",
            None if e_real is None else float(e_real),
            None if e_unreal is None else float(e_unreal), ccy="USD",
            extra="daily %+.2f · engine /state" % (st_e.get("daily_pnl") or 0.0),
            note=note)

    if oanda_nav:
        add("OANDA", "Claude Book",
            oanda_nav - CLAUDE_BOOK_START_NAV - oanda_upnl, oanda_upnl,
            ccy="AUD", extra="4 idx longs · 6-railed")

    cb2 = _json_file(CARRY_BOOK, {})
    add("Carry Executor", "basis carry",
        (cb2.get("realized_aud") or 0.0) - (cb2.get("costs_paid_aud") or 0.0),
        (cb2.get("funding_accrued_aud") or 0.0)
        if cb2.get("positions") else 0.0, ccy="AUD",
        extra="armed" if not cb2.get("positions") else "OPEN")

    # Carry New-Names Probe (USD) — XMR/HYPE $10k paper. Realized = funding
    # harvested on closed legs; unrealized = funding accrued on open legs.
    cp2 = _json_file(CARRY_PROBE_LEDGER, {})
    cp_realized = sum(float(c.get("funding_harvested") or 0.0)
                      for c in cp2.get("closed", []))
    if cp2.get("frozen"):
        cp_extra = ("FROZEN %.0f%% kill"
                    % (cp2.get("book_risk", {}).get("kill_drawdown_pct")
                       or cp2.get("kill_drawdown_pct") or -18.0))
    elif cp2.get("positions"):
        cp_extra = "OPEN %d/2 · dd %.1f%%" % (len(cp2["positions"]),
                                              cp2.get("drawdown_pct", 0.0))
    else:
        cp_extra = "armed · no entry"
    add("Carry Probe", "XMR/HYPE", cp_realized,
        live_upnl.get(("Carry Probe", "XMR/HYPE"), 0.0), ccy="USD",
        extra=cp_extra)

    # Tournament sleeves (USD) — MISSING ENTIRELY until now.
    #
    # positions_payload() has always built the 51 sleeve rows, but never called
    # add() for them, and all_time_usd sums pnl_by_desk only. Twelve sleeves and
    # $150,000 of registered starts contributed nothing to the firm headline.
    #
    # realized is taken from the sleeve's own books: cash + cost basis of what
    # it still holds, less its start. unrealized is the live open P&L on the
    # rows above. The two reconcile to (equity - start) when the sleeve's stored
    # marks and the board's live marks agree.
    for fn in sorted(_g.glob(os.path.join(BASE, "reports", "tournament", "*.json"))):
        sv = _json_file(fn, {})
        name = sv.get("name", os.path.basename(fn)[:-5])
        start = sv.get("start", _SLEEVE_START.get(name, 10000.0))
        entry = sv.get("entry_px", {})
        basis = 0.0
        for t, u in (sv.get("holdings") or {}).items():
            e = entry.get(t)
            if e:
                basis += u * e
        call = sv.get("short_call")
        if call:
            basis -= (call.get("premium") or 0.0) * 100 * call.get("contracts", 1)
        cash = sv.get("cash")
        realized = None if cash is None else (cash + basis - start)
        add("Tournament", name, realized,
            live_upnl.get(("Tournament", name), 0.0), ccy="USD",
            extra="vs $%s start%s" % (format(int(start), ","),
                                      " · FROZEN" if sv.get("frozen") else ""),
            note=None if realized is not None
            else "sleeve state has no cash figure — realized unavailable")

    # one firm-wide number: fold A$ floors into USD at a rate whose provenance
    # travels with it. If there is no rate at all, `combined_usd` is null and
    # carries the reason — the client must NOT fall back to the USD subtotal.
    fx = fx_audusd()
    usd_t = totals.get("USD", {"value": 0.0, "upnl": 0.0, "n": 0})
    aud_t = totals.get("AUD", {"value": 0.0, "upnl": 0.0, "n": 0})
    combined, combined_unavailable = None, None
    if fx["rate"]:
        combined = {
            "value": round(usd_t["value"] + aud_t["value"] * fx["rate"], 2),
            "upnl": round(usd_t["upnl"] + aud_t["upnl"] * fx["rate"], 2),
            "n": usd_t["n"] + aud_t["n"],
            "audusd": round(float(fx["rate"]), 4),
            "fx_status": fx["status"],          # "live" | "cached"
            "fx_source": fx["source"],
            "fx_age_human": fx["age_human"],
            "fx_note": fx["note"],
        }
    else:
        combined_unavailable = (
            "firm total unavailable: %d AUD positions (A$%s) cannot be "
            "converted — %s"
            % (aud_t["n"], format(round(aud_t["value"], 2), ","), fx["note"]))

    # ---- firm-wide P&L: all-time (per-desk totals) + today (midnight anchor) ----
    import time as _t
    pnl = None
    cap = {"components": [], "n_books": 0, "total_usd": None, "complete": False,
           "unavailable_reason": "complete registry funding unavailable", "unregistered": [],
           "label": "all documented registered funding", "basis": "whole firm registry"}
    try:
        # NO HARDCODED FX. The old line was
        #     rate2 = (combined or {}).get("audusd") or 0.70
        # which silently priced the entire AUD book at 0.70 whenever the fetch
        # failed — and it did fail, on the loopback process, every single time.
        rate2 = fx["rate"]
        incomplete = [k for k in desks if k["total"] is None]
        aud_desks = [k for k in desks if k["ccy"] == "AUD"]
        cum, cum_reason = None, None
        if incomplete:
            cum_reason = ("%d desk(s) could not report a total: %s"
                          % (len(incomplete),
                             ", ".join("%s/%s" % (k["floor"], k["book"])
                                       for k in incomplete)))
        elif aud_desks and not rate2:
            cum_reason = ("%d AUD desk(s) cannot be converted — %s"
                          % (len(aud_desks), fx["note"]))
        else:
            cum = sum(k["total"] * ((rate2 or 1.0) if k["ccy"] == "AUD" else 1.0)
                      for k in desks)

        today = _t.strftime("%Y-%m-%d")
        anchor_f = os.path.join(BASE, "reports", "daily_anchor.json")
        anchor = _json_file(anchor_f, {})
        # Only ever anchor on a number we actually have. Anchoring on a figure
        # derived from a guessed FX rate poisons every subsequent "today".
        if cum is not None and anchor.get("date") != today:
            hist = anchor.get("history", [])
            if anchor.get("date"):
                hist.append({"date": anchor["date"],
                             "close_cum_usd": anchor.get("open_cum_usd"),
                             "eod_cum_usd": round(cum, 2)})
            anchor = {"date": today, "open_cum_usd": round(cum, 2),
                      "basis": PNL_BASIS, "history": hist[-370:]}
            tmpa = anchor_f + ".tmp"
            with open(tmpa, "w") as fa:
                json.dump(anchor, fa, indent=1)
            os.replace(tmpa, anchor_f)
        # days since firm inception (earliest book/engine start: 2026-08-05)
        try:
            incep = _t.mktime(_t.strptime("2026-08-05", "%Y-%m-%d"))
            days_live = max(1, int((_t.time() - incep) // 86400))
        except Exception:
            days_live = None
        # best-performing OPEN asset by unrealized %, split by LIQUIDITY so a thin
        # micro-cap spike can't masquerade as the headline. Liquidity = the engine's
        # own 24h $volume universe (vol_musd); non-crypto rows (ETFs, large-caps,
        # index CFDs, G10 FX) are liquid by definition.
        volmap = _liquidity_volmap()
        LIQ_MIN = 5.0   # musd/24h — separates majors from micro-caps
        LIQ_WHITELIST = {"XBT/USD", "ETH/USD", "SOL/USD", "XRP/USD", "ADA/USD",
                         "DOT/USD", "LINK/USD", "LTC/USD", "AVAX/USD", "BCH/USD",
                         "DOGE/USD", "XDG/USD", "BNB/USD", "XLM/USD", "TRX/USD",
                         "ATOM/USD"}

        def _is_liquid(sym):
            if not sym:
                return True
            if "/" in sym:                       # crypto pair
                v = volmap.get(sym)
                # A ZERO volume reading means NO DATA, not "no liquidity". The
                # engine's vol_musd field went all-zero (found 2026-08-25; it
                # read 63.3 for XBT on 2026-08-08), which made the old
                # `v >= LIQ_MIN` branch file every major as illiquid and
                # inflated the dashboard's thin-air figure by ~$7k. Falsy
                # volume now falls through to the majors whitelist — the
                # fail-safe direction for a data outage.
                if v:
                    return v >= LIQ_MIN
                return sym in LIQ_WHITELIST      # fallback: engine down OR zeroed
            return True                          # equities / indices / FX

        best_liq = best_illiq = None
        for r in rows:
            p = r.get("upnl_pct")
            if p is None:
                continue
            entry = {"symbol": r.get("symbol"), "upnl_pct": p,
                     "book": r.get("book") or r.get("floor")}
            if _is_liquid(r.get("symbol")):
                if best_liq is None or p > best_liq["upnl_pct"]:
                    best_liq = entry
            else:
                if best_illiq is None or p > best_illiq["upnl_pct"]:
                    best_illiq = entry
        _cands = [x for x in (best_liq, best_illiq) if x]
        best = max(_cands, key=lambda x: x["upnl_pct"]) if _cands else None
        # split OPEN unrealized P&L by liquidity (USD; AUD folded at live rate).
        # Only unrealized splits honestly — realized P&L is banked cash, already
        # liquidity-agnostic. This shows how much of the open gain is CAPTURABLE
        # (liquid) vs a thin-market mark (illiquid).
        open_liq = open_illiq = 0.0
        unconverted_aud = 0
        for r in rows:
            u = r.get("upnl")
            if u is None:
                continue
            if r.get("ccy") == "AUD":
                if not rate2:
                    unconverted_aud += 1      # excluded, and said so below
                    continue
                usd = u * rate2
            else:
                usd = u
            if _is_liquid(r.get("symbol")):
                open_liq += usd
            else:
                open_illiq += usd

        # ---- today: only meaningful against an anchor on the SAME basis ----
        today_usd, today_reason = None, None
        if cum is None:
            today_reason = cum_reason
        elif anchor.get("date") != today:
            today_reason = "no anchor for today yet"
        elif anchor.get("basis") != PNL_BASIS:
            today_reason = ("today's anchor was written on the previous "
                            "accounting basis (%s) and is not comparable — "
                            "re-anchors at the next date rollover"
                            % (anchor.get("basis") or "pre-fix"))
        else:
            today_usd = round(cum - anchor.get("open_cum_usd", cum), 2)

        fr_hero = _json_file(os.path.join(BASE, "reports", "firm_risk_state.json"), {}) or {}
        firm_eq = fr_hero.get("firm_equity_usd")
        if isinstance(firm_eq, bool) or not isinstance(firm_eq, (int, float)) or not math.isfinite(firm_eq):
            firm_eq = None
        # Same complete, flow-preserving funding basis as the firm hero.
        firm_seed = None
        firm_books = []
        firm_funding = {"complete": False, "errors": ["funding registry unavailable"]}
        try:
            import importlib
            import book_coverage as _bc
            importlib.reload(_bc)
            firm_books = _bc.registry()
            firm_funding = _firm_funding_block(firm_books, fr_hero)
            firm_seed = firm_funding["total_usd"]
        except Exception as exc:
            firm_funding["errors"] = ["%s: %s" % (type(exc).__name__, str(exc))]
        mandate_target = 1694524.90
        firm_vs_seed = (round(firm_eq - firm_seed, 2)
                        if firm_eq is not None and firm_seed is not None else None)
        firm_vs_mandate = round(firm_eq - mandate_target, 2) if firm_eq is not None else None
        cap.update({"n_books": len(firm_books), "complete": firm_funding["complete"],
                    "total_usd": firm_seed,
                    "unavailable_reason": "; ".join(firm_funding["errors"]) or None,
                    "label": "all documented funding of %d registered books" % len(firm_books),
                    "basis": "whole firm; includes zero and negative funding residuals",
                    "unregistered": [{"floor": "Firm registry", "book": "funding", "reason": e}
                                     for e in firm_funding["errors"]]})
        if firm_funding["complete"]:
            cap["components"] = [{"floor": b.get("label") or b["key"], "book": b["key"],
                                  "start": round(b["start_usd"], 2), "ccy": "USD",
                                  "source": "documented registry funding, translated by the book adapter"}
                                 for b in firm_books]
        # The legacy all_time_usd numerator is a different, incomplete desk-sum
        # scope. Never divide it by whole-firm funding or silently relabel it.
        ret_pct = None
        ret_reason = (cap["unavailable_reason"] if not cap["complete"] else
                      "legacy desk-sum P&L does not cover the complete firm; use explicit firm return")
        firm_return_pct, firm_return_reason = None, None
        if not cap["complete"]:
            firm_return_reason = cap["unavailable_reason"]
        elif firm_vs_seed is None:
            firm_return_reason = "firm equity unavailable"
        elif firm_seed <= 0:
            firm_return_reason = "positive complete firm funding required for a return percentage"
        else:
            firm_return_pct = round(firm_vs_seed / firm_seed * 100, 3)

        pnl = {
            "open_upnl_usd": (combined or {}).get("upnl"),
            "open_upnl_unavailable": combined_unavailable,
            "today_usd": today_usd,
            "today_unavailable": today_reason,
            "all_time_usd": None if cum is None else round(cum, 2),
            "all_time_unavailable": cum_reason,
            # Firm hero numbers — different basis than desk-sum all-time; surface
            # both so Deployed strip does not fight GREEN vs-mandate.
            "firm_equity_usd": round(firm_eq, 2) if firm_eq is not None else None,
            "firm_vs_seed_usd": firm_vs_seed,
            "firm_funding_complete": firm_funding["complete"],
            "firm_funding_errors": firm_funding["errors"],
            "firm_vs_mandate_usd": firm_vs_mandate,
            "all_time_note": "desk-sum trading P&L (not firm equity vs Taylor seed)",
            "return_on_capital_pct": ret_pct,
            "return_unavailable": ret_reason,
            "firm_return_on_capital_pct": firm_return_pct,
            "firm_return_unavailable": firm_return_reason,
            "firm_return_basis": "(complete firm equity - all documented firm funding) / that same funding",
            "capital_usd": cap["total_usd"],
            "capital_label": cap["label"],
            "capital_n_books": cap["n_books"],
            "anchor_date": anchor.get("date"),
            "anchor_basis": anchor.get("basis"),
            "basis": PNL_BASIS,
            "history_days": len(anchor.get("history", [])),
            "days_since_inception": days_live,
            "best_asset": best,
            "best_asset_liquid": best_liq,
            "best_asset_illiquid": best_illiq,
            "open_upnl_liquid_usd": round(open_liq, 2),
            "open_upnl_illiquid_usd": round(open_illiq, 2),
            "open_split_excluded_aud_rows": unconverted_aud,
        }
    except Exception as _e:
        # Never swallow this silently: an empty pnl block used to render as a
        # blank strip with no explanation.
        pnl = {"error": "%s: %s" % (type(_e).__name__, str(_e)[:200])}

    return {"generated": _t.strftime("%H:%M:%S"), "rows": rows, "totals": totals,
            "coverage_errors": coverage_errors, "sol_short_pilot": short_pilot,
            "combined_usd": combined,
            "combined_unavailable": combined_unavailable,
            "fx": fx,
            "marks": marks_health(rows),
            "quote_ccy": quote_ccy_warning,
            "capital": cap,
            "oanda_nav": oanda_nav, "oanda_nav_ccy": "AUD",
            "served_by": served_by(),
            # rally_rider scale-out ladder — built here, inside the cached
            # payload, AFTER the tournament loop warmed the mark caches.
            "rally_rider": _rally_rider_block(),
            "rally_rider_x": _rally_rider_block(RRX_FILE, "rr_x",
                                                RRX_UNIVERSE_N),
            "capacity_probe": _capacity_probe_block(),
            "punt_24h": _punt_block(),
            "fundamental_desk": _fundamental_block(),
            # 2026-08-22 addendum sleeves. rally_rider_hold serves
            # ladder="trail_only" — its registered rule has NO profit-taking,
            # so TP levels are None by construction, never fabricated.
            "rally_rider_hold": _rally_rider_block(RRH_FILE, "rr_hold",
                                                   RR_UNIVERSE_N,
                                                   ladder="trail_only"),
            "dip_rider": _rally_rider_block(DIP_FILE, "dip", RR_UNIVERSE_N),
            "pnl": pnl, "pnl_by_desk": desks}


OANDA_DIR = "/Users/test/Downloads/oanda-desk"


def oanda_status():
    """READ-ONLY view of the external OANDA practice desk's sleeve state.

    That desk is its own pre-registered experiment (running since 2026-08-05 —
    resume, never restart). Its NAV lives on the OANDA practice server behind
    the operator's credentials, which this firm never touches; what it publishes
    locally is per-sleeve regime state and paper tickets. We mirror, never manage.
    """
    out = {"sleeves": [], "tickets": []}
    if OANDA_BOOK_REMOVED:
        # Decommissioned 2026-08-28 (funder-directed). This mirror is off; the
        # empty shape is what every caller already handles.
        out["removed"] = {
            "utc": "2026-08-28",
            "basis": 'funder direction: "remove both desks entirely"',
            "note": ("the OANDA practice account still held positions this desk "
                     "could not close (sustained 401 since 2026-08-27 18:12); "
                     "removing the book stopped us measuring that exposure, it "
                     "did not flatten it")}
        return out
    for name in ("trend", "meanrev", "carry"):
        d = _json_file(os.path.join(OANDA_DIR, "book_%s.json" % name), None)
        if d:
            out["sleeves"].append({
                "sleeve": name, "as_of": d.get("as_of"),
                "in_season": d.get("in_season"),
                "opinion": (d.get("regime_opinion") or "")[:180],
                "n_ideas": len(d.get("ideas") or [])})
    try:
        with open(os.path.join(OANDA_DIR, "ledger.jsonl")) as f:
            last = None
            for line in f:
                if line.strip():
                    last = line
        if last:
            led = json.loads(last)
            out["nav_aud"] = led.get("nav")
            out["last_reconcile"] = led.get("ts")
            out["registry_hash"] = led.get("registry_hash")
    except Exception:
        pass
    try:
        with open(os.path.join(OANDA_DIR, "ledger.jsonl")) as f:
            last_full = None
            for line in f:
                if line.strip():
                    last_full = json.loads(line)
        if last_full:
            out["last_targets"] = len(last_full.get("targets", []))
    except Exception:
        last_full = None
    cb = _json_file(os.path.join(OANDA_DIR, "CLAUDE_BOOK_ACTIVE.json"), None)
    if cb:
        live = bool(last_full and last_full.get("registry_hash") == cb.get("hash")
                    and len(last_full.get("targets", [])) > 0)
        out["claude_book"] = {"name": cb.get("book"), "hash": cb.get("hash"),
                              "registered": cb.get("registered"),
                              "live": live,
                              "status": "LIVE" if live else
                              "REGISTERED — awaiting funder-executed first fill"}
    out["sealed_concluded"] = bool(last_full and len(last_full.get("targets", [])) == 0
                                   and last_full.get("registry_hash") != (cb or {}).get("hash"))
    t = _json_file(os.path.join(OANDA_DIR, "tickets_live.json"), {})
    for tk in (t.get("tickets") or []):
        out["tickets"].append({
            "instrument": tk.get("instrument"), "direction": tk.get("direction"),
            "status": tk.get("status"), "risk_aud": tk.get("risk_aud"),
            "purpose": (tk.get("purpose") or "")[:120]})
    return out


def carry_slim():
    """Executor book + the monitor verdict it acts on, in one payload."""
    mon = _json_file(CARRY_MONITOR, {})
    return {
        "book": _json_file(CARRY_BOOK, {"positions": {}, "realized_aud": 0.0,
                                        "funding_accrued_aud": 0.0,
                                        "costs_paid_aud": 0.0}),
        "verdict": mon.get("preferred_verdict"),
        "preferred_mean_pct": mon.get("preferred_mean_annualised_pct"),
        "btc_pct": mon.get("btc_annualised_pct"),
        "threshold_pct": mon.get("harvest_threshold_pct", 4.0),
        "checked_at": mon.get("checked_at"),
    }


def _json_file(path, fallback):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return fallback


def equity_series(limit=MAX_CHART_POINTS):
    """Downsampled Kraken *engine* equity curve (legacy; ~$40k book)."""
    try:
        rows = []
        with open(EQUITY_CSV) as f:
            for rec in csv.DictReader(f):
                try:
                    rows.append([rec["timestamp"], float(rec["equity"])])
                except (KeyError, ValueError):
                    continue
    except OSError:
        return {"points": [], "start": None, "last": None, "label": "Kraken engine"}
    if not rows:
        return {"points": [], "start": None, "last": None, "label": "Kraken engine"}
    step = max(1, len(rows) // limit)
    pts = rows[::step]
    if pts[-1] != rows[-1]:
        pts.append(rows[-1])
    vals = [p[1] for p in pts]
    return {
        "points": pts,
        "start": rows[0][1],
        "last": rows[-1][1],
        "min": min(vals),
        "max": max(vals),
        "n_raw": len(rows),
        "label": "Kraken engine",
        "scope": "engine",
    }


def firm_equity_series(limit=MAX_CHART_POINTS):
    """Firm-wide equity curve from reports/firm_equity.csv — what CONSOLIDATED should show."""
    try:
        rows = []
        with open(FIRM_EQUITY_CSV) as f:
            for rec in csv.DictReader(f):
                try:
                    ts = rec.get("utc") or rec.get("timestamp")
                    eq = float(rec.get("firm_equity_usd") or rec.get("equity") or 0)
                    if ts and eq > 0:
                        rows.append([ts, eq])
                except (KeyError, ValueError, TypeError):
                    continue
    except OSError:
        return {"points": [], "start": None, "last": None, "label": "Taylor firm", "scope": "firm"}
    if not rows:
        return {"points": [], "start": None, "last": None, "label": "Taylor firm", "scope": "firm"}
    step = max(1, len(rows) // limit)
    pts = rows[::step]
    if pts[-1] != rows[-1]:
        pts.append(rows[-1])
    vals = [p[1] for p in pts]
    return {
        "points": pts,
        "start": rows[0][1],
        "last": rows[-1][1],
        "min": min(vals),
        "max": max(vals),
        "n_raw": len(rows),
        "label": "Taylor firm",
        "scope": "firm",
    }


def trades_list(limit=200):
    try:
        with open(TRADES_CSV) as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return []
    for r in rows:
        for k in ("units", "price", "usd", "fee", "realized_pnl", "equity_after"):
            try:
                r[k] = float(r[k])
            except (KeyError, ValueError, TypeError):
                r[k] = None
    return rows[-limit:][::-1]

def blotter_meta(rows=None):
    """Honest label for the Kraken engine trade blotter (not firm-wide fills)."""
    if rows is None:
        rows = trades_list(200)
    if not rows:
        return {
            "source": "Kraken engine CSV",
            "n": 0,
            "label": "no engine fills yet",
            "last_timestamp": None,
            "age_days": None,
            "idle": True,
        }
    last_ts = rows[0].get("timestamp")  # newest first
    age_days = None
    try:
        from datetime import datetime, timezone
        dt = datetime.strptime(str(last_ts), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
    except Exception:
        pass
    idle = age_days is not None and age_days >= 1.0
    if idle and age_days is not None:
        label = "%d engine fills · last %dd ago · paper idle" % (len(rows), int(age_days))
    else:
        label = "%d most recent engine fills" % len(rows)
    return {
        "source": "Kraken engine CSV (kraken_trades.csv)",
        "n": len(rows),
        "label": label,
        "last_timestamp": last_ts,
        "age_days": None if age_days is None else round(age_days, 1),
        "idle": idle,
    }



def engine_state():
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/state" % ENGINE_PORT,
                                    timeout=8) as r:
            return json.load(r)
    except Exception as e:
        return {"_unreachable": True, "_error": str(e)[:200]}


def slim_engine(st):
    """Only what the dashboard renders — /state is large and mostly irrelevant here."""
    if st.get("_unreachable"):
        return st
    keys = ("equity", "cash", "daily_pnl", "realized_pnl", "fees_paid", "positions",
            "open_positions", "error_count", "error_streak", "kill_switch",
            "trading_paused", "data_ok", "last_error", "max_dd_seen", "peak_equity",
            "gross_exposure", "net_exposure", "leverage", "margin_level",
            "start_cash", "day_start_equity", "daily_target", "trades_total",
            "closed_trades", "universe", "mode", "trade_mode", "engine_version",
            "started_at", "updated_at", "ticks", "regime", "risk", "health")
    out = {k: st.get(k) for k in keys if k in st}
    # 2026-10-28 STALE flag: a hung engine still answers /state but stops updating.
    try:
        import time as _ts
        ue = st.get("updated_epoch")
        if ue is None:
            # never produced a tick (e.g. stuck loading universe) -> stale, not live
            out["_stale"] = True
            out["_age_s"] = None
            out["_stale_reason"] = "no update yet: " + str(st.get("last_error") or "no ticks")[:120]
        else:
            lim = max(4 * float(st.get("loop_seconds") or 60), 300.0)
            age = _ts.time() - float(ue)
            out["_age_s"] = round(age)
            out["_stale_after_s"] = lim
            out["_stale"] = age > lim
    except Exception:
        pass
    log = st.get("log") or []
    out["log"] = log[-60:] if isinstance(log, list) else []
    return out


_PAYLOAD_CACHE = {"pos": [0.0, None], "all": [0.0, None]}
_PAYLOAD_LOCKS = {"pos": __import__("threading").Lock(),
                  "all": __import__("threading").Lock()}


def _cached(kind, ttl, builder):
    """Serve the cached payload while fresh; otherwise rebuild SINGLE-FLIGHT.

    Two defects fixed 2026-08-22 (they compounded into a mobile app that could
    never render while the engine answered slowly):
      * the stamp was taken BEFORE the build, so a build slower than the TTL
        produced a cache that was already expired the moment it was stored —
        every request rebuilt, forever;
      * there was no lock, so each 5s poller stacked its own concurrent
        multi-second rebuild, hammering the engine and inflating build times
        further.
    Now: one builder at a time; while a rebuild runs, other requests are
    served the last-good payload (it carries its own `generated` stamp, and
    every non-live mark carries its age — nothing is presented as fresher
    than it is); the stamp is taken at build COMPLETION."""
    _time = __import__("time")
    lock = _PAYLOAD_LOCKS[kind]
    ts, data = _PAYLOAD_CACHE[kind]
    if data is not None and _time.time() - ts < ttl:
        return data
    if not lock.acquire(blocking=False):
        # someone else is already rebuilding
        if data is not None:
            return data              # last-good, honestly stamped
        lock.acquire()               # first-ever build: wait for it
        try:
            ts2, data2 = _PAYLOAD_CACHE[kind]
            if data2 is not None:
                return data2
            data2 = builder()
            _PAYLOAD_CACHE[kind] = [_time.time(), data2]
            return data2
        finally:
            lock.release()
    try:
        data = builder()
        _PAYLOAD_CACHE[kind] = [_time.time(), data]
        return data
    finally:
        lock.release()


# HT decision 2026-09-26 #4f: /api/all is built by ONE background thread every
# _BG_PERIOD_S and requests are served the last-good copy (it carries `generated`
# and `payload_age_s`), so the browser never waits on a 7-20 s cold build. Only the
# very first request after a restart builds synchronously (if the warm-up has not
# finished yet).
_BG_PERIOD_S = 40.0
_BG_STARTED = {}
_BG_LAST_REQ = {}


def _bg_loop(kind, builder, period, idle=None):
    _time = __import__("time")
    while True:
        if idle and _time.time() - _BG_LAST_REQ.get(kind, 0) > idle:
            _time.sleep(1.0)          # nobody watching: no quote traffic until the next request
            continue
        try:
            data = builder()
            _PAYLOAD_CACHE[kind] = [_time.time(), data]
        except Exception as exc:  # keep serving last-good; never kill the thread
            print("background %s rebuild failed: %s" % (kind, exc), file=sys.stderr)
        _time.sleep(period)


def _bg_cached(kind, builder, period=_BG_PERIOD_S, idle=None):
    _time = __import__("time")
    _BG_LAST_REQ[kind] = _time.time()
    th = _BG_STARTED.get(kind)
    if th is None or not th.is_alive():
        import threading as _th
        with _PAYLOAD_LOCKS[kind]:
            th = _BG_STARTED.get(kind)
            if th is None or not th.is_alive():
                th = _th.Thread(target=_bg_loop, args=(kind, builder, period, idle), daemon=True, name="bg-" + kind)
                _BG_STARTED[kind] = th
                th.start()
    ts, data = _PAYLOAD_CACHE[kind]
    if data is None:
        for _ in range(600):                 # first build after restart: wait for the warm-up
            _time.sleep(0.1)
            ts, data = _PAYLOAD_CACHE[kind]
            if data is not None:
                break
        if data is None:
            return {"error": "War Room payload still building", "generated": None}
    out = dict(data)
    out["payload_age_s"] = round(_time.time() - ts, 1)
    return out


def _build_all():
    return {
        "engine": slim_engine(engine_state()),
        "floor": _json_file(FLOOR_LEDGER, {"desks": {}}),
        "replica": _json_file(REPLICA_LEDGER, {"desks": {}}),
        "equity": firm_equity_series(),
        "engine_equity": equity_series(),
        "trades": trades_list(),
        "blotter": blotter_meta(),
        "health": _json_file(HEALTH_JSON, {}),
        "boot_report": _json_file(BOOT_REPORT, {}),
        "cost_wall": _json_file(COST_WALL, {}),
        "hold_lab": _json_file(HOLD_LAB, {}),
        "growth": _json_file(GROWTH_BOOK, {}),
        "growth_equity": growth_equity_series(),
        "carry": carry_slim(),
        "warroom": warroom(),
        "tournament": _json_file(os.path.join(BASE, "reports", "tournament.json"), {}),
        "oanda": oanda_status(),
        "value": _json_file(os.path.join(BASE, "reports", "value_book.json"), {}),
        "risk": _json_file(os.path.join(BASE, "reports", "risk_report.json"), {}),
        # firm equity / peak / drawdown + the two firm triggers
        # (FIRM_RISK_AMENDMENT.md, funder-directed 2026-08-27)
        "firm_risk": _json_file(
            os.path.join(BASE, "reports", "firm_risk_state.json"), {}),
        "fundamental_desk": _fundamental_block(),
        "coo_mandate": _coo_mandate_block(),
        "salary": _salary_block(),
        "daily_pnl": _daily_pnl_block(),
        "tfo_hero": _tfo_hero_block(),
        "crypto_recovery": _json_file(os.path.join(BASE, "reports", "crypto_recovery_20260920_latest.json"), {}),
        "sol_short_pilot": _sol_short_pilot_block(),
        "crypto_rumour": _crypto_rumour_block(),
        "convexity": _convexity_block(),
        "dated_event": _dated_event_block(),
        "generated": __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def _pw_compute():
    from private_wealth_monitor import build
    obj = build()
    if True:
        if True:
            pass
            try:  # 2026-09-28 blocked-rail watchdog: small neutral header indicator
                import rail_watchdog
                obj["rail_watchdog"] = rail_watchdog.summary()
            except Exception as exc2:
                obj["rail_watchdog"] = {"status": "UNKNOWN", "alert_count": 0, "alerts": [], "note": str(exc2)[:120]}
            try:  # 2026-10-02 PAPER funder-fee ledger (funder_fee.py): Funder panel on the Overview
                import funder_fee
                obj["funder"] = funder_fee.summary()
            except Exception as exc3:
                obj["funder"] = {"status": "UNAVAILABLE", "error": str(exc3)[:160]}
            try:  # 2026-10-03 DEEP18 trend sleeve (trend18.py): PAPER sleeve inside firm equity; read-only summary
                import trend18
                obj["trend18"] = trend18.summary()
            except Exception as exc5:
                obj["trend18"] = {"status": "UNAVAILABLE", "error": str(exc5)[:160]}
            try:  # 2026-10-28 TREND-CORE A$1m AUD paper (path B, not firm equity): read-only status
                import trend_core
                obj["trend_core"] = trend_core.write_status()
                obj["trend_core"]["status"] = "OK"
            except Exception as exc_tc:
                obj["trend_core"] = {"status": "UNAVAILABLE", "error": str(exc_tc)[:160], "paper_only": True, "in_firm_equity": False}
            try:  # 2026-10-03 DEEP18 round-3 forward tests (deep18_fwd.py): PAPER SHADOW, read-only, NOT firm equity
                import deep18_fwd
                obj["deep18_fwd"] = deep18_fwd.summary_all(120)
            except Exception as exc6:
                obj["deep18_fwd"] = {"status": "UNAVAILABLE", "error": str(exc6)[:160]}
            try:  # 2026-10-03 DEEP19 near-miss shadows (deep19_fwd.py): did NOT pass, $0 desk capital, read-only
                import deep19_fwd
                obj["deep19_fwd"] = deep19_fwd.summary_all(120)
            except Exception as exc8:
                obj["deep19_fwd"] = {"status": "UNAVAILABLE", "error": str(exc8)[:160], "paper_shadow_only": True}
            try:  # 2026-10-04 OANDA PRACTICE sleeve (oanda_sleeve.py -> state/oanda/snapshot.json): PAPER, practice account, NOT desk equity
                import importlib, oanda_panel
                obj["oanda_paper"] = importlib.reload(oanda_panel).summary()
            except Exception as exc9:
                obj["oanda_paper"] = {"status": "UNAVAILABLE", "error": str(exc9)[:160], "paper_only": True}
            try:  # 2026-10-03 CRYPTO20 T05: PAPER SHADOW, read-only, NOT firm equity
                import crypto20_fwd
                obj["crypto20_fwd"] = crypto20_fwd.summary()
            except Exception as exc7:
                obj["crypto20_fwd"] = {"status": "UNAVAILABLE", "error": str(exc7)[:160], "paper_shadow_only": True}
            try:  # 2026-10-02 SHADOW 2x Nasdaq combo (shadow_nasdaq2x.py): read-only, NOT firm equity
                import shadow_nasdaq2x
                obj["shadow"] = shadow_nasdaq2x.summary()
            except Exception as exc4:
                obj["shadow"] = {"status": "UNAVAILABLE", "error": str(exc4)[:160]}
            return obj

# 2026-10-12 stale-while-revalidate cache for /api/private-wealth (build() parses ~1400 JSON files, ~6s).
import threading as _pw_threading, time as _pw_time
from datetime import datetime as _pw_dt, timezone as _pw_tz
_PW_CACHE = {"obj": None, "at": 0.0, "err": None}
_PW_LOCK = _pw_threading.Lock()
_PW_REFRESH_S = 15

def _pw_refresh_once():
    t0 = _pw_time.time()
    try:
        obj = _pw_compute()
        with _PW_LOCK:
            _PW_CACHE.update(obj=obj, at=_pw_time.time(), err=None, build_s=round(_pw_time.time() - t0, 2))
    except Exception as exc:
        with _PW_LOCK:
            _PW_CACHE["err"] = str(exc)[:200]

def _pw_loop():
    while True:
        _pw_refresh_once()
        _pw_time.sleep(_PW_REFRESH_S)

def _pw_start():
    _pw_threading.Thread(target=_pw_loop, name="pw-cache", daemon=True).start()

def _pw_cached():
    with _PW_LOCK:
        obj, at, err, bs = _PW_CACHE["obj"], _PW_CACHE["at"], _PW_CACHE["err"], _PW_CACHE.get("build_s")
    if obj is None:
        return None, err
    out = dict(obj)
    out["cache"] = {"generated_utc": _pw_dt.fromtimestamp(at, _pw_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "age_s": round(_pw_time.time() - at, 1), "refresh_s": _PW_REFRESH_S,
                    "build_s": bs, "last_refresh_error": err}
    return out, err


class Handler(BaseHTTPRequestHandler):
    server_version = "DeskCommand/1.0"

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if path == "/api/private-wealth":
            obj, err = _pw_cached()
            if obj is None:
                self._json({"error": "Private Wealth report warming up" if not err else "Private Wealth report unavailable",
                            "detail": (err or "first build in progress, retry in a few seconds")[:200]}, 503)
            else:
                self._json(obj)
            return

        if path in ("/parallel", "/parallel/", "/api/parallel"):  # 2026-10-04 PARALLEL DESK (PAPER; separate ledger state/parallel_desk; read-only)
            try:
                _pd_dir = os.path.join(BASE, "research", "parallel_desk")
                if _pd_dir not in sys.path:
                    sys.path.insert(0, _pd_dir)
                import importlib, parallel_desk, parallel_desk_web
                importlib.reload(parallel_desk); parallel_desk_web = importlib.reload(parallel_desk_web)
                if path == "/api/parallel":
                    self._json(parallel_desk_web.summary())
                else:
                    self._send(200, parallel_desk_web.page(), "text/html; charset=utf-8")
            except Exception as exc:
                self._json({"status": "UNAVAILABLE", "error": str(exc)[:200], "paper_only": True}, 503)
            return

        if path == "/api/all":
            self._json(_bg_cached("all", _build_all))
            return

        if path == "/api/markets":  # 2026-10-27 War Room ALL MARKETS v1 (read-only aggregator; PAPER)
            try:
                import warroom_markets
                self._json(warroom_markets.cached())
            except Exception as exc:
                self._json({"status": "UNAVAILABLE", "error": str(exc)[:200], "paper_only": True}, 503)
            return
        if path == "/api/trend18":  # 2026-10-03 DEEP18 trend sleeve summary (read-only; PAPER)
            try:
                import trend18
                self._json(trend18.summary())
            except Exception as exc:
                self._json({"status": "UNAVAILABLE", "error": str(exc)[:200]}, 503)
            return

        if path in ("/all", "/all/", "/api/all_desks"):  # 2026-10-28 Brett 21:11 WITA: one phone page, all TFO desks excl. Veteran Desk (read-only; PAPER)
            try:
                if path == "/api/all_desks":
                    sys.path.insert(0, os.path.join(BASE, "tools")); import importlib, all_desks; importlib.reload(all_desks); self._json(all_desks.build())
                else:
                    with open(os.path.join(BASE, "static", "all_desks.html"), "rb") as f: self._send(200, f.read(), "text/html; charset=utf-8")
            except Exception as exc:
                self._json({"status": "UNAVAILABLE", "error": str(exc)[:160], "paper_only": True})
            return
        if path == "/api/strategy_scoreboard":  # 2026-10-28 Brett-approved: per-strategy scoreboard (read-only; PAPER; books never merged)
            sb = os.path.join(BASE, "reports", "strategy_scoreboard.json")
            try:
                age = _pw_time.time() - os.path.getmtime(sb) if os.path.exists(sb) else 1e9
                if age > 600 and not getattr(Handler, "_sb_busy", False):   # refresh in background, serve last good copy now
                    Handler._sb_busy = True
                    def _sb_refresh():
                        try: subprocess.run([sys.executable, os.path.join(BASE, "tools", "strategy_scoreboard.py")], cwd=BASE, timeout=90, capture_output=True)
                        finally: Handler._sb_busy = False
                    _pw_threading.Thread(target=_sb_refresh, daemon=True).start()
                with open(sb) as f: out = json.load(f)
                out["age_s"] = round(age); self._json(out)
            except Exception as exc:
                self._json({"status": "UNAVAILABLE", "error": str(exc)[:160], "paper_only": True})
            return
        if path == "/api/trend_core":  # 2026-10-28 TREND-CORE A$1m AUD paper (read-only; PAPER; not firm equity)
            try:
                import trend_core
                out = trend_core.write_status()
                out["status"] = "OK"
                self._json(out)
            except Exception as exc:
                self._json({"status": "UNAVAILABLE", "error": str(exc)[:160], "paper_only": True, "in_firm_equity": False})
        if path == "/api/deep18_fwd" or path.startswith("/api/deep18_fwd/"):  # 2026-10-03 DEEP18 R3 forward tests (read-only; PAPER SHADOW)
            try:
                import deep18_fwd
                tid = path[len("/api/deep18_fwd/"):].strip("/") if path.startswith("/api/deep18_fwd/") else ""
                if not tid:
                    self._json(deep18_fwd.summary_all(400))
                else:
                    out = deep18_fwd.summary(tid)
                    self._json(out, 404 if out.get("status") == "UNKNOWN_TEST" else 200)
            except Exception as exc:
                self._json({"status": "UNAVAILABLE", "error": str(exc)[:200]}, 503)
            return

        if path == "/api/deep19_fwd" or path.startswith("/api/deep19_fwd/"):  # 2026-10-03 DEEP19 near-miss shadows (read-only; did NOT pass; $0 desk capital)
            try:
                import deep19_fwd
                tid = path[len("/api/deep19_fwd/"):].strip("/") if path.startswith("/api/deep19_fwd/") else ""
                if not tid:
                    self._json(deep19_fwd.summary_all(400))
                else:
                    out = deep19_fwd.summary(tid)
                    self._json(out, 404 if out.get("status") == "UNKNOWN_TEST" else 200)
            except Exception as exc:
                self._json({"status": "UNAVAILABLE", "error": str(exc)[:200], "paper_shadow_only": True}, 503)
            return

        if path == "/api/crypto20_fwd":  # 2026-10-03 CRYPTO20 T05 paper shadow (read-only; not desk equity)
            try:
                import crypto20_fwd
                self._json(crypto20_fwd.summary())
            except Exception as exc:
                self._json({"status": "UNAVAILABLE", "error": str(exc)[:200], "paper_shadow_only": True}, 503)
            return

        if path == "/api/shadow":  # 2026-10-02 SHADOW 2x Nasdaq combo (read-only; paper, not in firm equity)
            try:
                import shadow_nasdaq2x
                self._json(shadow_nasdaq2x.summary())
            except Exception as exc:
                self._json({"status": "UNAVAILABLE", "error": str(exc)[:200]}, 503)
            return

        if path == "/api/audit":  # hash-chained paper audit: read-only tip + last nightly verify
            try:
                import audit
                self._json(audit.card())
            except Exception as exc:
                self._json({"status": "UNAVAILABLE", "ok": None, "error": str(exc)[:200], "paper_only": True}, 503)
            return

        if path == "/api/dashboard":  # 2026-10-03 interactive dashboard aggregate (dashboard_api.py): read-only, PAPER, no broker contact
            try:
                import dashboard_api
                self._json(dashboard_api.cached(lambda: _bg_cached("pos", positions_payload, period=8.0, idle=180)))
            except Exception as exc:
                self._json({"status": "UNAVAILABLE", "error": str(exc)[:200]}, 503)
            return

        if path in ("/glossary", "/glossary/", "/api/glossary") or path.startswith("/static/"):  # 2026-10-03 glossary + static assets for the interactive layer
            name = "glossary.html" if path.startswith("/glossary") else ("glossary.json" if path == "/api/glossary" else path[len("/static/"):])
            ctypes = {".json": "application/json; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                      ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8", ".svg": "image/svg+xml"}
            ext = os.path.splitext(name)[1].lower()
            full = os.path.join(BASE, "static", name)
            if (not name or name != os.path.basename(name) or name.startswith(".") or ext not in ctypes
                    or not os.path.isfile(full)):
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            with open(full, "rb") as f:
                self._send(200, f.read(), ctypes[ext])
            return

        if path == "/api/m":  # 2026-10-09 phone page /m + War Room desk-status panel (mobile_api.py): read-only, PAPER; live TCP probe of the Mac IBKR gateway :4002
            try:
                import importlib, mobile_api
                _mt = os.path.getmtime(mobile_api.__file__)
                if getattr(mobile_api, "_loaded_mtime", None) not in (None, _mt):
                    mobile_api = importlib.reload(mobile_api)
                mobile_api._loaded_mtime = _mt
                self._json(mobile_api.cached())
            except Exception as exc:
                self._json({"status": "UNAVAILABLE", "error": str(exc)[:200]}, 503)
            return

        if path == "/api/funder":  # 2026-10-02 PAPER funder-fee ledger summary (read-only)
            try:
                import funder_fee
                self._json(funder_fee.summary())
            except Exception as exc:
                self._json({"status": "UNAVAILABLE", "error": str(exc)[:200]}, 503)
            return

        if path in ("/", "/index.html"):
            try:
                with open(PRIVATE_WEALTH_PAGE, "rb") as f:
                    body = f.read()
            except OSError:
                self._send(404, b"private_wealth_dashboard.html not found",
                           "text/plain; charset=utf-8")
                return
            self._send(200, body, "text/html; charset=utf-8")

        elif path in ("/warroom", "/warroom/"):
            try:
                with open(PAGE, "rb") as f:
                    body = f.read()
            except OSError:
                self._send(404, b"command_dashboard.html not found",
                           "text/plain; charset=utf-8")
                return
            self._send(200, body, "text/html; charset=utf-8")

        elif path == "/api/all":
            # one round trip for the whole page
            self._json({
                "engine": slim_engine(engine_state()),
                "floor": _json_file(FLOOR_LEDGER, {"desks": {}}),
                "replica": _json_file(REPLICA_LEDGER, {"desks": {}}),
                "equity": firm_equity_series(),
                    "engine_equity": equity_series(),
                "trades": trades_list(),
                    "blotter": blotter_meta(),
                "health": _json_file(HEALTH_JSON, {}),
                "boot_report": _json_file(BOOT_REPORT, {}),
                "cost_wall": _json_file(COST_WALL, {}),
                "hold_lab": _json_file(HOLD_LAB, {}),
                "growth": _json_file(GROWTH_BOOK, {}),
                "growth_equity": growth_equity_series(),
                "carry": carry_slim(),
                "warroom": warroom(),
                "tournament": _json_file(os.path.join(BASE, "reports", "tournament.json"), {}),
                "oanda": oanda_status(),
                "risk": _json_file(os.path.join(BASE, "reports", "risk_report.json"), {}),
                "firm_risk": _json_file(
                    os.path.join(BASE, "reports", "firm_risk_state.json"), {}),
                "fundamental_desk": _fundamental_block(),
                "coo_mandate": _coo_mandate_block(),
                "salary": _salary_block(),
                "daily_pnl": _daily_pnl_block(),
                    "tfo_hero": _tfo_hero_block(),
                "crypto_recovery": _json_file(os.path.join(BASE, "reports", "crypto_recovery_20260920_latest.json"), {}),
                "sol_short_pilot": _sol_short_pilot_block(),
                "crypto_rumour": _crypto_rumour_block(),
                    "convexity": _convexity_block(),
                    "dated_event": _dated_event_block(),
                "value": _json_file(os.path.join(BASE, "reports", "value_book.json"), {}),
            })

        elif path == "/api/engine":
            self._json(slim_engine(engine_state()))
        elif path == "/api/floor":
            self._json(_json_file(FLOOR_LEDGER, {"desks": {}}))
        elif path == "/api/replica":
            self._json(_json_file(REPLICA_LEDGER, {"desks": {}}))
        elif path == "/api/equity":
            self._json(equity_series())
        elif path == "/api/trades":
            self._json(trades_list())
        elif path == "/api/health":
            self._json(_json_file(HEALTH_JSON, {}))
        elif path == "/api/research":
            self._json({"cost_wall": _json_file(COST_WALL, {}),
                        "hold_lab": _json_file(HOLD_LAB, {})})
        elif path == "/api/heal":
            # Runs desk_healer in DRY-RUN only. A read-only viewer must not be able
            # to restart processes on an unauthenticated GET.
            try:
                out = subprocess.run(
                    [sys.executable, os.path.join(BASE, "desk_healer.py"),
                     "--dry-run", "--json"],
                    capture_output=True, text=True, timeout=90, cwd=BASE)
                self._json(json.loads(out.stdout or "{}"))
            except Exception as e:
                self._json({"error": str(e)[:200]}, 503)
        elif path in ("/m", "/m/"):
            try:
                with open(os.path.join(BASE, "mobile_dashboard.html"), "rb") as f:
                    body = f.read()
                self._send(200, body, "text/html; charset=utf-8")
            except OSError:
                self._send(404, b"mobile page missing", "text/plain; charset=utf-8")
        elif path == "/api/positions":
            # background-built while the board is open (8 s cadence; idles after 3 min without a viewer)
            self._json(_bg_cached("pos", positions_payload, period=8.0, idle=180))
        elif path == "/report":
            # LIVE report: rebuilt from every book + the engine's /state on each
            # request, so the page is always a current representation of the floor.
            try:
                import make_desk_report
                out = make_desk_report.build()
                with open(out, "rb") as f:
                    body = f.read()
                self._send(200, body, "text/html; charset=utf-8")
            except Exception as e:
                self._send(503, json.dumps({"error": str(e)[:200]}).encode(),
                           "application/json; charset=utf-8")
        elif path == "/healthz":
            self._json({"ok": True})
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        self._send(405, b"read-only viewer", "text/plain; charset=utf-8")

    def log_message(self, *args):
        return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18811)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    # Sign every payload with the process that produced it. Two servers share
    # port 18812 on different interfaces; without this the board cannot tell
    # which one answered, and their totals legitimately differ.
    _SERVED_BY["host"], _SERVED_BY["port"] = args.host, args.port
    _pw_start()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    __import__("threading").Thread(target=lambda: _bg_cached("all", _build_all), daemon=True, name="warm-all").start()
    # warm the positions cache too, so the first browser poll after a restart is not a cold 10-15 s build
    __import__("threading").Thread(target=lambda: _bg_cached("pos", positions_payload, period=8.0, idle=180), daemon=True, name="warm-pos").start()
    print("Desk command dashboard (read-only) on http://%s:%d" % (args.host, args.port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
