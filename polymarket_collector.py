#!/usr/bin/env python3
"""Taylor Family Office — Polymarket READ-ONLY snapshot leg (PAPER research, no venue).

Snapshots the top ~100 Polymarket markets by liquidity once per desk tick (hourly) from
the PUBLIC gamma API — no account, no keys, no orders, GET only. Data accrues toward the
pre-registered 21-day evaluation in reports/POLYMARKET_LEG_PREREGISTRATION.md. This is a
DATA leg only: no probe deploys from it without harness validation + funder sign-off.

Output: reports/polymarket_snapshots.sqlite
  snapshots — one row per market per run
  scans     — one row per RUN, including failures (SBO lesson: a run that fails must
              leave a loud, queryable trace — never empty success)
  reports/polymarket_last_error.json — error marker, cleared on the next clean run

SCHEMA-DRIFT contract (learned twice now — the SBO supplier-key rename and the Kalshi
yes_bid -> yes_bid_dollars rename, each of which read as "no data" and went dark):
  * A hard-required field missing from any market object  -> RAISE SchemaDriftError.
  * bestBid / bestAsk are legitimately ABSENT on thin one-sided books (measured
    2026-08-20: bestBid absent on 16 of the top 100) -> absent on SOME markets is a
    NULL quote, but absent on EVERY market in the batch is the rename signature -> RAISE.
  * Any raise prints a loud ERROR line, persists the error marker, records a failed
    scans row, and exits non-zero. Silence is never success.

Endpoint (verified live 2026-08-20):
  https://gamma-api.polymarket.com/markets?limit=100&active=true&closed=false
      &order=liquidityNum&ascending=false
Fields: id, question, bestBid, bestAsk, spread, liquidityNum, volumeNum, endDate,
        outcomes (JSON string, e.g. '["Yes","No"]'), outcomePrices (JSON string).
"""
import datetime, json, os, sqlite3, sys, traceback, urllib.request
try:
    import desk_doh; desk_doh.install(ibkr=False, oanda=False, extra=True)  # 2026-10-27 audit
except Exception as _doh_e:
    print("desk_doh unavailable: %r" % _doh_e, file=sys.stderr)

BASE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(BASE, "reports")
DB = os.path.join(REPORTS, "polymarket_snapshots.sqlite")
ERR_MARKER = os.path.join(REPORTS, "polymarket_last_error.json")

TOP_N = 100
URL = ("https://gamma-api.polymarket.com/markets?limit=%d&active=true&closed=false"
       "&order=liquidityNum&ascending=false" % TOP_N)

# --- CM-061 (2026-08-30): the crypto slice this collector was blind to --------
# The top-100-by-liquidity slice is dominated by ~$1-3M 2028 US-election books.
# Crypto price markets sit FAR below that liquidity cut, so in 24 days of polling
# the store captured effectively ZERO of them -- measured live: 2 of 100. A
# prediction-market study on this desk returned UNTESTABLE for exactly that
# reason: the collector was sampling the wrong end of the venue.
#
# Polymarket tags crypto as tag_id=21. Paginated, that yields ~300 distinct
# crypto-tagged markets, ~100 of them asset/price questions ("Will Bitcoin hit
# $150k by December 31, 2026?"). Those are the ones that could conceivably lead
# a Kraken spot price -- and they were never being recorded.
#
# The liquidity slice is KEPT so the existing 24-day series stays continuous;
# the crypto slice is ADDED alongside it, and every row carries `slice` so the
# two can never be silently conflated.
CRYPTO_TAG_ID = 21
CRYPTO_PAGES = 3                      # 3 x 100 covers the tag today
CRYPTO_URL = ("https://gamma-api.polymarket.com/markets?limit=100&active=true"
              "&closed=false&tag_id=%d&offset=%%d" % CRYPTO_TAG_ID)

# Missing from ANY market object -> schema drift, no exceptions.
# 2026-10-29: endDate removed — Polymarket's /markets no longer returns it; stored as NULL.
HARD_REQUIRED = ("id", "question", "spread", "liquidityNum", "volumeNum",
                 "outcomes", "outcomePrices")
# Legitimately absent on one-sided books; absent from the WHOLE batch -> drift.
QUOTE_FIELDS = ("bestBid", "bestAsk")


class SchemaDriftError(RuntimeError):
    """A live response is missing an expected field — rename/drift, never 'no data'."""


def utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def db_conn():
    os.makedirs(REPORTS, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=15)
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("""CREATE TABLE IF NOT EXISTS snapshots (
        utc_ts TEXT NOT NULL,
        market_id TEXT NOT NULL,
        question TEXT,
        outcome_yes TEXT, outcome_no TEXT,
        yes_mid REAL, no_mid REAL,
        best_bid REAL, best_ask REAL, spread REAL,
        liquidity REAL, volume REAL,
        end_date TEXT,
        PRIMARY KEY (utc_ts, market_id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS scans (
        utc_ts TEXT NOT NULL,
        ok INTEGER NOT NULL,
        markets_received INTEGER,
        rows_written INTEGER,
        skipped_nonbinary INTEGER,
        error TEXT)""")
    return conn


def record_scan(ok, received, written, skipped, error=None):
    conn = db_conn()
    try:
        conn.execute("INSERT INTO scans (utc_ts, ok, markets_received, rows_written, "
                     "skipped_nonbinary, error) VALUES (?,?,?,?,?,?)",
                     (utc_now_iso(), 1 if ok else 0, received, written, skipped,
                      None if error is None else str(error)[:2000]))
        conn.commit()
    finally:
        conn.close()


def persist_error_marker(err, tb):
    os.makedirs(REPORTS, exist_ok=True)
    with open(ERR_MARKER, "w") as f:
        json.dump({"utc_ts": utc_now_iso(), "error": str(err)[:2000],
                   "traceback": tb[-4000:]}, f, indent=1)


def clear_error_marker():
    try:
        if os.path.exists(ERR_MARKER):
            os.remove(ERR_MARKER)
    except OSError:
        pass


def fetch_markets():
    req = urllib.request.Request(URL, headers={"User-Agent": "tfo-paper-research/1.0"})
    ms = json.load(urllib.request.urlopen(req, timeout=30))
    if not isinstance(ms, list):
        raise SchemaDriftError("SCHEMA-DRIFT: /markets no longer returns a list; got %s "
                               "with keys %s" % (type(ms).__name__,
                                                 sorted(ms.keys()) if isinstance(ms, dict) else "?"))
    if not ms:
        raise SchemaDriftError("SCHEMA-DRIFT/OUTAGE: /markets returned an EMPTY list for the "
                               "top-liquidity query — never treat as a quiet no-data day")
    for m in ms:
        missing = [f for f in HARD_REQUIRED if f not in m]
        if missing:
            raise SchemaDriftError("SCHEMA-DRIFT: market object missing required field(s) %s; "
                                   "id=%r; available keys: %s"
                                   % (missing, m.get("id"), sorted(m.keys())))
    for f in QUOTE_FIELDS:
        if not any(f in m for m in ms):
            raise SchemaDriftError("SCHEMA-DRIFT: quote field %r absent from EVERY market in "
                                   "the batch — rename signature, not thin books" % f)
    return ms


def fetch_crypto_markets():
    """CM-061: the crypto-tagged slice. Failure here must NOT break the main
    liquidity scan -- a partial capture is better than none, and it is reported."""
    out, errs = [], []
    for page in range(CRYPTO_PAGES):
        try:
            req = urllib.request.Request(CRYPTO_URL % (page * 100),
                                         headers={"User-Agent": "tfo-paper-research/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                ms = json.loads(r.read().decode("utf-8"))
            if not isinstance(ms, list):
                errs.append("page %d: not a list" % page)
                break
            out.extend(ms)
            if len(ms) < 100:
                break
        except Exception as e:                       # noqa: BLE001 - reported
            errs.append("page %d: %s" % (page, type(e).__name__))
            break
    return out, errs


def to_row(m, utc):
    """Parse one market. Returns row tuple, or None for non-binary (caller counts it)."""
    try:
        outcomes = json.loads(m["outcomes"])
        prices = json.loads(m["outcomePrices"])
    except (ValueError, TypeError) as e:
        raise SchemaDriftError("SCHEMA-DRIFT: outcomes/outcomePrices no longer JSON-decodable "
                               "(id=%r): %s" % (m.get("id"), e))
    if not (isinstance(outcomes, list) and isinstance(prices, list)
            and len(outcomes) == 2 and len(prices) == 2):
        return None  # non-binary market — counted loudly by the caller, never silent
    yes_mid, no_mid = float(prices[0]), float(prices[1])
    bb = m.get("bestBid"); ba = m.get("bestAsk")
    return (utc, str(m["id"]), m["question"], str(outcomes[0]), str(outcomes[1]),
            yes_mid, no_mid,
            None if bb is None else float(bb),
            None if ba is None else float(ba),
            float(m["spread"]), float(m["liquidityNum"]), float(m["volumeNum"]),
            # CM-061: endDate was accessed HARD but never declared in
            # HARD_REQUIRED — an inconsistency that only surfaced when the crypto
            # slice arrived, because every top-100 liquidity market happens to
            # carry one. It is legitimately absent on perpetual crypto questions,
            # so it is treated exactly like bestBid: absent on SOME markets is a
            # NULL, absent on EVERY market is the rename signature and raises.
            None if m.get("endDate") is None else str(m["endDate"]))


def run_once():
    utc = utc_now_iso()
    ms = fetch_markets()
    # CM-061: add the crypto-tagged slice. Deduplicated by id so a market in BOTH
    # slices is stored once. Crypto failure never breaks the liquidity scan.
    n_liq = len(ms)
    cms, cerrs = fetch_crypto_markets()
    seen = {str(m.get("id")) for m in ms}
    n_crypto_new = 0
    for m in cms:
        if str(m.get("id")) not in seen:
            seen.add(str(m.get("id")))
            ms.append(m)
            n_crypto_new += 1
    if cerrs:
        print("[polymarket] CRYPTO SLICE PARTIAL: %s — the liquidity scan is "
              "unaffected, but crypto coverage this tick is incomplete"
              % "; ".join(cerrs), flush=True)
    if ms and all("endDate" not in m for m in ms):
        # 2026-10-29: Polymarket dropped endDate from the API; record NULL, warn, don't fail.
        print("[polymarket] NOTE: endDate absent on every market (%d) — stored as NULL"
              % len(ms), flush=True)
    # 2026-10-17 (Brett item 11): one unpriced market (no outcomePrices) raised KeyError and failed
    # EVERY hourly desk_tick (com.taylor.familyoffice exit 1). Same rule as endDate: absent on SOME
    # markets = skipped loudly; absent on EVERY market = rename signature and raises.
    if ms and all("outcomePrices" not in m for m in ms):
        raise SchemaDriftError("SCHEMA-DRIFT: outcomePrices absent on EVERY market (%d)" % len(ms))
    unpriced = [m for m in ms if "outcomePrices" not in m]
    if unpriced:
        print("[polymarket] skipped %d unpriced market(s) (no outcomePrices): ids=%s"
              % (len(unpriced), [m.get("id") for m in unpriced][:10]), flush=True)
        ms = [m for m in ms if "outcomePrices" in m]
    rows, skipped = [], 0
    for m in ms:
        r = to_row(m, utc)
        if r is None:
            skipped += 1
            print("[polymarket] skipped non-binary market id=%s outcomes=%s"
                  % (m.get("id"), m.get("outcomes")), flush=True)
        else:
            rows.append(r)
    conn = db_conn()
    try:
        conn.executemany("INSERT OR REPLACE INTO snapshots (utc_ts, market_id, question, "
                         "outcome_yes, outcome_no, yes_mid, no_mid, best_bid, best_ask, "
                         "spread, liquidity, volume, end_date) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
    finally:
        conn.close()
    record_scan(True, len(ms), len(rows), skipped)
    clear_error_marker()
    print("[polymarket] %s snapshot: %d markets received (%d liquidity slice + %d "
          "crypto-tagged new), %d rows written, %d non-binary skipped"
          % (utc, len(ms), n_liq, n_crypto_new, len(rows), skipped), flush=True)


def main():
    try:
        run_once()
    except Exception as e:
        tb = traceback.format_exc()
        tag = "SCHEMA-DRIFT " if isinstance(e, SchemaDriftError) else ""
        print("[polymarket] ERROR %s%s :: %s\n%s" % (tag, utc_now_iso(), e, tb), flush=True)
        persist_error_marker(e, tb)
        try:
            record_scan(False, None, 0, None, error="%s%s" % (tag, e))
        except Exception as db_e:
            print("[polymarket] ERROR could not record failed scan row: %s" % db_e, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
