"""Exact futures risk-source contract: synthetic only, no trading or network."""
import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import book_coverage
import kraken_futures_risk_prices as prices
import risk_engine

SYMBOL = "PF_SPCXXUSD"
NOW = datetime(2026, 9, 10, 8, tzinfo=timezone.utc).timestamp()


def fixture():
    instrument = {"symbol": SYMBOL, "base": "SPCXx", "quote": "USD",
                  "type": "flexible_futures", "contractSize": 1,
                  "openingDate": "2026-06-06T22:46:51Z"}
    candles = [{"time": int(day.timestamp() * 1000), "open": "100",
                "high": "102", "low": "99", "close": "101", "volume": "0"}
               for day in pd.date_range("2026-06-06", "2026-09-10", tz="UTC")]
    return {"result": "success", "instruments": [instrument]}, {"candles": candles, "more_candles": False}


class ExactFuturesPrices(unittest.TestCase):
    def setUp(self):
        self.catalogue, self.payload = fixture()
        self.cache = {}
        self.fetch = mock.Mock(side_effect=lambda url: (copy.deepcopy(
            self.catalogue if url == prices.INSTRUMENTS_URL else self.payload), "a" * 64))

    def load(self):
        return prices.closes(SYMBOL, self.cache, now=NOW, fetch=self.fetch)

    def test_exact_identity_preserved_only_for_verified_contracts(self):
        self.assertEqual(book_coverage.yf_ticker(SYMBOL), "KRK:" + SYMBOL)
        self.assertEqual(book_coverage.yf_ticker("PF_GLDXUSD"), "KRK:PF_GLDXUSD")
        self.assertEqual(book_coverage.yf_ticker("PF_XBTUSD"), "KRK:XBT")
        self.assertEqual(book_coverage.yf_ticker("SPCXX/USD"), "KRK:SPCXX")

    def test_completed_exact_marks_and_provenance(self):
        series = self.load()
        self.assertEqual((min(series), max(series), len(series)), ("2026-06-07", "2026-09-09", 95))
        record = self.cache[prices.CACHE_KEY][SYMBOL]
        self.assertEqual(record["tick_type"], "mark")
        self.assertEqual(record["instrument"]["quote"], "USD")
        self.assertEqual(record["candles_sha256"], "a" * 64)
        self.assertEqual(self.fetch.call_args_list[-1].args[0], prices.CHARTS_URL + SYMBOL + "/1d?count=1000")

    def test_validated_cache_reused_but_spot_cache_not_reused(self):
        self.cache.update({"series": {"SPCXXUSD": {"2026-09-09": 9999}}, "fetched": {"SPCXXUSD": NOW}})
        first = self.load()
        self.assertEqual(first["2026-09-09"], 101)
        self.fetch.reset_mock()
        self.assertEqual(self.load(), first)
        self.fetch.assert_not_called()

    def test_expired_cache_failure_returns_unknown(self):
        self.load()
        self.fetch.side_effect = TimeoutError("offline")
        value = prices.closes(SYMBOL, self.cache, now=NOW + prices.CACHE_SECONDS, fetch=self.fetch)
        self.assertIsNone(value)
        self.assertEqual(self.cache[prices.CACHE_KEY][SYMBOL]["status"], "UNAVAILABLE")

    def test_tampered_cache_refetched(self):
        self.load()
        self.cache[prices.CACHE_KEY][SYMBOL]["series"]["2026-09-09"] = 9999
        self.fetch.reset_mock()
        self.assertEqual(self.load()["2026-09-09"], 101)
        self.assertEqual(self.fetch.call_count, 2)

    def test_wrong_identity_currency_size_or_missing_contract_refuses(self):
        for field, bad in (("symbol", "PF_GLDXUSD"), ("base", "SPCX"), ("quote", "USDT"),
                           ("type", "spot"), ("contractSize", 10)):
            with self.subTest(field=field):
                self.catalogue, self.payload = fixture()
                self.catalogue["instruments"][0][field] = bad
                self.cache = {}
                self.assertIsNone(self.load())
                self.assertIn("UNAVAILABLE", self.cache[prices.CACHE_KEY][SYMBOL]["status"])

    def test_unknown_contract_never_fetches(self):
        self.assertIsNone(prices.closes("PF_MADEUPUSD", {}, now=NOW, fetch=self.fetch))
        self.fetch.assert_not_called()

    def test_duplicate_gap_seconds_future_and_non_daily_refuse(self):
        changes = [lambda rows: rows.append(copy.deepcopy(rows[5])),
                   lambda rows: rows.pop(5),
                   lambda rows: rows[5].update(time=rows[5]["time"] // 1000),
                   lambda rows: rows[-1].update(time=rows[-1]["time"] + prices.DAY_MS),
                   lambda rows: rows[5].update(time=rows[5]["time"] + 1000)]
        for change in changes:
            self.catalogue, self.payload = fixture()
            change(self.payload["candles"])
            self.cache = {}
            self.assertIsNone(self.load())

    def test_invalid_prices_refuse(self):
        for bad in ("nan", "inf", "0", "-1", "103"):
            self.catalogue, self.payload = fixture()
            self.payload["candles"][5]["close"] = bad
            self.cache = {}
            self.assertIsNone(self.load())

    def test_partial_or_missing_response_refuses(self):
        for payload in ({}, {"candles": [], "more_candles": False},
                        dict(self.payload, more_candles=True)):
            self.payload, self.cache = payload, {}
            self.assertIsNone(self.load())

    def test_short_history_not_padded(self):
        self.payload["candles"] = self.payload["candles"][-5:]
        self.assertEqual(len(self.load()), 4)

    def test_shared_panel_routes_exact_and_spot_separately(self):
        raw = pd.DataFrame({("Close", "SPY"): [100.]}, index=pd.to_datetime(["2026-09-09"]))
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(risk_engine, "PRICE_CACHE", os.path.join(tmp, "cache.json")), \
                mock.patch("yfinance.download", return_value=raw), \
                mock.patch.object(prices, "closes", return_value={"2026-09-09": 147.0}) as exact, \
                mock.patch.object(risk_engine, "kraken_closes", return_value=None) as spot:
            panel, sources = risk_engine.fetch_price_panel([{"ticker": "KRK:" + SYMBOL}, {"ticker": "KRK:CATI"}])
        self.assertEqual(exact.call_args.args[0], SYMBOL)
        self.assertEqual(spot.call_args.args[0], "CATI")
        self.assertEqual(panel["KRK:" + SYMBOL].iloc[-1], 147)
        self.assertEqual(sources["kraken_exact_futures_mark"], 1)


if __name__ == "__main__":
    unittest.main()
