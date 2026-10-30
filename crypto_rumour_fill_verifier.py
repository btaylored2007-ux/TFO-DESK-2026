#!/usr/bin/env python3
"""Timestamp-aware, read-only receipts for Crypto Rumour-Rider maker fills.

The frozen book and engine are inputs only.  For each MAKER_POST -> MAKER_FILL
pair, exact Binance aggregate trades are the primary evidence.  A receipt is
accepted only when an aggregate trade printed strictly after the order post,
no later than the recorded fill, and strictly through the resting limit using
the engine's 1 bp strict-through buffer.

If exact tape cannot be retrieved, a complete one-minute candle whose open is
strictly after posting and whose close is no later than the fill may provide a
conservative bounded proof.  That fallback is explicitly labelled and never
presented as an exact print.  Missing or ambiguous evidence fails closed into
an evidence/performance quarantine.  This script never changes the book.
"""
from __future__ import annotations
# TFO DoH hook 2026-10-28 (Brett item B4: desk_doh process-local DoH for IBKR + Binance/Coinbase/OKX/Alpaca/Polymarket; no system DNS; kill switch TFO_DESK_DOH=0)
try:
    import desk_doh as _doh; _doh.install(extra=True)
except Exception as _e:
    print("desk_doh install skipped:", _e, file=__import__("sys").stderr)

import argparse
import collections
import datetime as dt
import hashlib
import json
import pathlib
import time
import urllib.parse
import urllib.request

BASE = pathlib.Path(__file__).resolve().parent
BOOK = BASE / "reports/crypto_rumour_book.json"
OUT = BASE / "reports/crypto_rumour_fill_verification.json"
AGG_TRADES_API = "https://api.binance.com/api/v3/aggTrades"
KLINES_API = "https://api.binance.com/api/v3/klines"
ONE_MINUTE_MS = 60_000
STRICT_THROUGH_BUFFER = 1e-4  # mirrors crypto_rumour_desk.py


def parse_utc(value: str) -> int:
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def iso_ms(value: int) -> str:
    return dt.datetime.fromtimestamp(value / 1000, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def full_minute_after(post_ms: int) -> int:
    """Return the first minute open strictly later than ``post_ms``."""
    return ((post_ms // ONE_MINUTE_MS) + 1) * ONE_MINUTE_MS


def reconstruct_pairs(book: dict) -> tuple[list[dict], list[dict]]:
    pending: dict[tuple[str, str], collections.deque] = collections.defaultdict(collections.deque)
    pairs: list[dict] = []
    orphan_fills: list[dict] = []
    for event in book.get("events", []):
        key = (str(event.get("band")), str(event.get("sym")))
        if event.get("event") == "MAKER_POST":
            pending[key].append(event)
        elif event.get("event") == "MAKER_FILL":
            if not pending[key]:
                orphan_fills.append(event)
                continue
            post = pending[key].popleft()
            pairs.append({
                "band": key[0],
                "symbol": key[1],
                "limit": float(post["limit"]),
                "posted_utc": post["utc"],
                "fill_utc": event["utc"],
                "ticks_to_fill": event.get("ticks_to_fill"),
            })
    return pairs, orphan_fills


def _request_json(endpoint: str, params: dict) -> list:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        endpoint + "?" + query,
        headers={"User-Agent": "tfo-readonly-verifier/2.0"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError("unexpected Binance market-data payload")
    return payload


def fetch_aggregate_trades(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    """Fetch exact aggregate trades without silently truncating a busy interval."""
    if end_ms < start_ms:
        return []
    params = {
        "symbol": symbol,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": 1000,
    }
    rows: dict[int, dict] = {}
    previous_last_id: int | None = None
    while True:
        page = _request_json(AGG_TRADES_API, params)
        for row in page:
            trade_ms = int(row["T"])
            if start_ms <= trade_ms <= end_ms:
                rows[int(row["a"])] = row
        if len(page) < 1000:
            break
        last_id = int(page[-1]["a"])
        last_ms = int(page[-1]["T"])
        if previous_last_id is not None and last_id <= previous_last_id:
            raise RuntimeError("aggregate-trade pagination did not advance")
        previous_last_id = last_id
        if last_ms > end_ms:
            break
        params = {"symbol": symbol, "fromId": last_id + 1, "limit": 1000}
        time.sleep(0.03)
    return [rows[key] for key in sorted(rows)]


def fetch_klines(symbol: str, start_ms: int, end_ms: int) -> list[list]:
    """Fetch one-minute bars used only when exact aggregate tape is unavailable."""
    rows: list[list] = []
    cursor = (start_ms // ONE_MINUTE_MS) * ONE_MINUTE_MS
    while cursor <= end_ms:
        page_end = min(end_ms, cursor + 999 * ONE_MINUTE_MS + ONE_MINUTE_MS - 1)
        page = _request_json(KLINES_API, {
            "symbol": symbol,
            "interval": "1m",
            "startTime": cursor,
            "endTime": page_end,
            "limit": 1000,
        })
        rows.extend(page)
        cursor = page_end + 1
        time.sleep(0.03)
    return [value for _, value in sorted({int(row[0]): row for row in rows}.items())]


def evaluate_aggregate_trades(pair: dict, trades: list[dict]) -> dict:
    post_ms = parse_utc(pair["posted_utc"])
    fill_ms = parse_utc(pair["fill_utc"])
    threshold = pair["limit"] * (1.0 - STRICT_THROUGH_BUFFER)
    eligible = sorted(
        (
            (int(row["T"]), int(row["a"]), float(row["p"]))
            for row in trades
            if post_ms < int(row["T"]) <= fill_ms
        ),
        key=lambda item: (item[0], item[1]),
    )
    proofs = [item for item in eligible if item[2] < threshold]
    result = dict(pair)
    result.update({
        "strict_through_threshold": threshold,
        "verification_method": "EXACT_BINANCE_AGGREGATE_TRADES",
        "evidence_source": AGG_TRADES_API,
        "exact_trade_window_start_exclusive_utc": pair["posted_utc"],
        "exact_trade_window_end_inclusive_utc": pair["fill_utc"],
        "exact_trades_examined": len(eligible),
        "minimum_exact_trade_price": min((item[2] for item in eligible), default=None),
    })
    if proofs:
        first = proofs[0]
        result.update({
            "status": "VERIFIED_EXACT_AGGTRADE_STRICT_THROUGH",
            "quarantined": False,
            "first_qualifying_trade_utc": iso_ms(first[0]),
            "first_qualifying_trade_price": first[2],
            "first_qualifying_aggregate_trade_id": first[1],
        })
    else:
        result.update({
            "status": "UNVERIFIED_QUARANTINE",
            "quarantined": True,
            "reason": "no exact post-order aggregate trade printed strictly through before recorded fill",
        })
    return result


def evaluate_candles(pair: dict, candles: list[list], exact_error: str | None = None) -> dict:
    """Conservative bounded fallback; it does not claim an exact trade time."""
    post_ms = parse_utc(pair["posted_utc"])
    fill_ms = parse_utc(pair["fill_utc"])
    threshold = pair["limit"] * (1.0 - STRICT_THROUGH_BUFFER)
    safe_start = full_minute_after(post_ms)
    complete: list[tuple[int, int, float]] = []
    posting_minute: list[tuple[int, int, float]] = []
    for row in candles:
        open_ms, close_ms, low = int(row[0]), int(row[6]), float(row[3])
        if (post_ms // ONE_MINUTE_MS) * ONE_MINUTE_MS == open_ms:
            posting_minute.append((open_ms, close_ms, low))
        if open_ms >= safe_start and close_ms <= fill_ms:
            complete.append((open_ms, close_ms, low))
    proofs = [row for row in complete if row[2] < threshold]
    ambiguous = [row for row in posting_minute if row[2] < threshold]
    result = dict(pair)
    result.update({
        "strict_through_threshold": threshold,
        "verification_method": "BOUNDED_1M_CANDLE_FALLBACK",
        "evidence_source": KLINES_API,
        "exact_tape_unavailable": True,
        "exact_tape_error": exact_error,
        "fallback_is_exact_print": False,
        "proof_window_start_utc": iso_ms(safe_start),
        "proof_window_end_utc": pair["fill_utc"],
        "complete_post_order_minutes": len(complete),
        "min_complete_post_order_low": min((row[2] for row in complete), default=None),
        "posting_minute_low": min((row[2] for row in posting_minute), default=None),
        "posting_minute_cross_is_ambiguous": bool(ambiguous),
    })
    if proofs:
        first = min(proofs, key=lambda row: row[0])
        result.update({
            "status": "VERIFIED_BOUNDED_1M_CANDLE_STRICT_THROUGH",
            "quarantined": False,
            "proof_candle_open_utc": iso_ms(first[0]),
            "proof_candle_close_utc": iso_ms(first[1]),
            "proof_low": first[2],
            "qualification": "bounded interval proof only; no exact qualifying print timestamp available",
        })
    else:
        result.update({
            "status": "UNVERIFIED_QUARANTINE",
            "quarantined": True,
            "reason": (
                "exact tape unavailable; only the posting-minute candle crossed and its low may predate the order"
                if ambiguous else
                "exact tape unavailable; no complete post-order minute traded strictly through before recorded fill"
            ),
        })
    return result


def verify_pair(pair: dict) -> dict:
    start_ms = parse_utc(pair["posted_utc"]) + 1
    end_ms = parse_utc(pair["fill_utc"])
    try:
        trades = fetch_aggregate_trades(pair["symbol"], start_ms, end_ms)
    except Exception as exact_exc:
        exact_error = "%s: %s" % (type(exact_exc).__name__, exact_exc)
        try:
            candles = fetch_klines(pair["symbol"], start_ms - 1, end_ms)
            return evaluate_candles(pair, candles, exact_error)
        except Exception as candle_exc:
            result = dict(pair)
            result.update({
                "status": "UNVERIFIED_QUARANTINE",
                "quarantined": True,
                "verification_method": "NO_MARKET_EVIDENCE_AVAILABLE",
                "exact_tape_error": exact_error,
                "candle_fallback_error": "%s: %s" % (type(candle_exc).__name__, candle_exc),
                "reason": "exact tape and bounded candle fallback both unavailable",
            })
            return result
    return evaluate_aggregate_trades(pair, trades)


def run() -> dict:
    book = json.loads(BOOK.read_text())
    pairs, orphan_fills = reconstruct_pairs(book)
    results = [verify_pair(pair) for pair in pairs]
    ledger_fill_rows = sum(len(band.get("fills", [])) for band in book.get("bands", {}).values())
    verified = sum(row["status"].startswith("VERIFIED") for row in results)
    quarantined = sum(row.get("quarantined", False) for row in results) + len(orphan_fills)
    methods = collections.Counter(row.get("verification_method", "NONE") for row in results)
    report = {
        "control": "crypto_rumour_timestamp_aware_maker_fill_receipt_verifier",
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "paper_only": True,
        "read_only": True,
        "source_book": str(BOOK),
        "source_book_sha256": hashlib.sha256(BOOK.read_bytes()).hexdigest(),
        "source_book_updated": book.get("updated"),
        "acceptance_rule": "exact aggregate trade T > post and T <= recorded fill with price < limit*(1-1e-4)",
        "fallback_rule": "only if exact tape retrieval fails: complete 1m candle opened strictly after post and closed by fill with low < threshold",
        "ledger_fill_rows": ledger_fill_rows,
        "paired_post_fill_events": len(pairs),
        "orphan_fill_events": orphan_fills,
        "verification_method_counts": dict(methods),
        "verified": verified,
        "quarantined": quarantined,
        "all_fills_verified": bool(ledger_fill_rows == verified and not orphan_fills),
        "book_action": "NONE — quarantine applies to evidence/performance attribution only",
        "fills": results,
    }
    OUT.write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    del args
    report = run()
    print(
        "maker-fill receipts: %d verified, %d quarantined, %d ledger rows"
        % (report["verified"], report["quarantined"], report["ledger_fill_rows"])
    )


if __name__ == "__main__":
    main()
