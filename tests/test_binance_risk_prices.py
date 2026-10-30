"""Synthetic exact-source, currency and fail-closed risk regression tests."""
import copy
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import binance_risk_prices as bp
import book_coverage
import exposure_dial
import risk_analytics
import risk_engine

NOW = datetime(2026, 9, 10, 9, tzinfo=timezone.utc)


def fixture(start="2026-06-01"):
    days = pd.date_range(start, "2026-09-10", tz="UTC")
    rows = [[int(d.timestamp() * 1000), "10", "11", "9", "10", "100",
             int(d.timestamp() * 1000) + bp.DAY_MS - 1, "1000", 20, "50", "500", "0"] for d in days]
    fx = [[int(d.timestamp()), ".9", "1.1", ".7", ".8", ".9", "1000", 100] for d in days]
    info = {"symbols": [{"symbol": s, "baseAsset": b, "quoteAsset": "USDT", "isSpotTradingAllowed": True}
                         for s, b in bp.VERIFIED_PAIRS.items()]}
    fx_info = {"error": [], "result": {"USDTZUSD": {"base": "USDT", "quote": "ZUSD",
                              "altname": "USDTUSD", "wsname": "USDT/USD"}}}
    return info, rows, fx_info, {"error": [], "result": {"USDTZUSD": fx, "last": 123}}


def coverage(ticker):
    p = {"book": "test", "label": ticker, "ticker": ticker, "fallback_usd": 500,
         "units": 50, "fallback_basis": "mark"}
    b = {"key": "test", "label": "test", "equity_usd": 1000, "positions": [p], "error": None, "stale": False}
    return {"books": [b], "positions": [p], "n_books": 1, "n_books_ok": 1,
            "n_positions": 1, "firm_equity_usd": 1000, "errors": []}


class BinanceRiskSource(unittest.TestCase):
    def setUp(self):
        self.info, self.rows, self.fx_info, self.fx = fixture()
        self.cache = {}
        self.fetch = mock.Mock(side_effect=self.response)

    def response(self, url):
        value = (self.fx_info if url == bp.FX_INFO else self.fx if url == bp.FX_PRICES
                 else self.info if "exchangeInfo?" in url else self.rows)
        return copy.deepcopy(value), "a" * 64

    def load(self, symbols=("CATIUSDT",)):
        return bp.load(symbols, self.cache, now=NOW.timestamp(), fetch=self.fetch)

    def test_usdt_is_converted_not_assumed_one_dollar(self):
        series = self.load()["CATIUSDT"]
        self.assertEqual(series["2026-09-09"], 8.)
        self.assertEqual(min(series), "2026-06-02")
        self.assertNotIn("2026-09-10", series)
        record = self.cache[bp.CACHE_KEY]["CATIUSDT"]
        self.assertEqual(record["output_currency"], "USD")
        self.assertEqual(record["fx_last_close"], .8)
        self.assertEqual(record["fx_sha256"], "a" * 64)

    def test_shared_currency_fetch_is_bounded_and_cache_reused(self):
        expected = self.load(tuple(bp.VERIFIED_PAIRS))
        self.assertEqual(self.fetch.call_count, 3 + len(bp.VERIFIED_PAIRS))
        self.fetch.reset_mock()
        self.assertEqual(self.load(tuple(bp.VERIFIED_PAIRS)), expected)
        self.fetch.assert_not_called()

    def test_old_kraken_spot_cache_cannot_substitute(self):
        self.cache["series"] = {"CATIUSD": {"2026-09-09": 999}}
        self.cache["fetched"] = {"CATIUSD": NOW.timestamp()}
        self.assertEqual(self.load()["CATIUSDT"]["2026-09-09"], 8.)

    def test_wrong_symbol_base_quote_or_spot_permission_refuses(self):
        for k, v in (("symbol", "MANTAUSDT"), ("baseAsset", "OTHER"),
                     ("quoteAsset", "USD"), ("isSpotTradingAllowed", False)):
            self.info, self.rows, self.fx_info, self.fx = fixture()
            self.info["symbols"][0][k] = v
            self.cache = {}
            self.assertEqual(self.load(), {})

    def test_wrong_fx_pair_currency_response_and_missing_rate_refuse(self):
        for k, v in (("base", "USDC"), ("quote", "ZEUR"), ("wsname", "USD/USDT")):
            self.info, self.rows, self.fx_info, self.fx = fixture()
            self.fx_info["result"]["USDTZUSD"][k] = v
            self.cache = {}
            self.assertEqual(self.load(), {})
        self.fx_info = {"error": ["EGeneral:No data"]}
        self.assertEqual(self.load(), {})

    def test_empty_errored_or_malformed_klines_refuse(self):
        for rows in ([], {"code": -1}, [[1, 2]], None):
            self.rows, self.cache = rows, {}
            self.assertEqual(self.load(), {})

    def test_duplicate_gap_bad_duration_seconds_future_and_nonfinite_refuse(self):
        changes = [lambda r: r.append(copy.deepcopy(r[10])), lambda r: r.pop(10),
                   lambda r: r[10].__setitem__(6, r[10][6] - 1),
                   lambda r: r[10].__setitem__(0, r[10][0] // 1000),
                   lambda r: r[-1].__setitem__(0, r[-1][0] + bp.DAY_MS),
                   lambda r: r[10].__setitem__(4, "nan"),
                   lambda r: r[10].__setitem__(4, True),
                   lambda r: r[10].__setitem__(4, "0")]
        for change in changes:
            self.info, self.rows, self.fx_info, self.fx = fixture()
            change(self.rows)
            self.cache = {}
            self.assertEqual(self.load(), {})

    def test_fx_gap_or_stale_endpoint_never_forward_filled(self):
        self.fx["result"]["USDTZUSD"].pop(10)
        self.assertEqual(self.load(), {})
        self.info, self.rows, self.fx_info, self.fx = fixture()
        self.fx["result"]["USDTZUSD"] = self.fx["result"]["USDTZUSD"][:-2]
        self.assertEqual(self.load(), {})

    def test_shorter_fx_window_is_explicit_intersection_not_padded(self):
        self.fx["result"]["USDTZUSD"] = self.fx["result"]["USDTZUSD"][-10:]
        self.assertEqual(len(self.load()["CATIUSDT"]), 9)

    def test_expired_cache_failed_refresh_returns_unknown(self):
        self.load()
        self.fetch.side_effect = TimeoutError("offline")
        self.assertEqual(bp.load(["CATIUSDT"], self.cache, now=NOW.timestamp() + bp.CACHE_SECONDS, fetch=self.fetch), {})

    def test_modified_series_or_source_url_refetches(self):
        for field, value in (("source_url", "https://example.invalid/"), ("series", {"2026-09-09": 999})):
            self.load()
            self.cache[bp.CACHE_KEY]["CATIUSDT"][field] = value
            self.fetch.reset_mock()
            self.assertEqual(self.load()["CATIUSDT"]["2026-09-09"], 8.)
            self.assertEqual(self.fetch.call_count, 4)

    def test_unknown_symbol_never_fetches(self):
        self.assertEqual(self.load(["WRONGUSDT"]), {})
        self.fetch.assert_not_called()

    def test_malformed_cache_namespace_or_record_is_a_cache_miss(self):
        for value in (None, [], "broken", 123):
            for namespace in (True, False):
                self.cache = {bp.CACHE_KEY: value if namespace else {"CATIUSDT": value}}
                self.fetch.reset_mock()
                self.assertEqual(self.load()["CATIUSDT"]["2026-09-09"], 8.)
                self.assertEqual(self.fetch.call_count, 4)

    def test_canonical_mapping_keeps_exact_binance_pair_and_other_names_unchanged(self):
        ledger = {"equity": 1000, "bands": {"test": {"cash": 0, "positions": {
            s: {"units": 1, "mark": 10} for s in (*bp.VERIFIED_PAIRS, "BTCUSDT")}}}}
        with mock.patch.object(book_coverage.os.path, "exists", return_value=True), \
                mock.patch.object(book_coverage, "jf", return_value=ledger):
            book = book_coverage._load_crypto_rumour("test", "test", "/synthetic/ledger")
        mapping = {p["sym"]: p["ticker"] for p in book["positions"]}
        self.assertEqual(mapping["BTCUSDT"], "KRK:BTC")
        for s in bp.VERIFIED_PAIRS:
            self.assertEqual(mapping[s], bp.PREFIX + s)

    def test_short_crwvb_history_cannot_open_gate_or_be_renormalized(self):
        self.info, self.rows, self.fx_info, self.fx = fixture("2026-07-22")
        series = self.load(["CRWVBUSDT"])["CRWVBUSDT"]
        ticker = bp.PREFIX + "CRWVBUSDT"
        raw = pd.DataFrame({ticker: pd.Series(series)})
        dial = exposure_dial.compute(coverage(ticker), raw, now=NOW, unclaimed=[], check_files=False)
        self.assertEqual(dial["price_freshness"][ticker]["return_observations"], 34)
        self.assertEqual(dial["scale"], 0)
        self.assertFalse(dial["gate_open_for_new_probes"])
        self.assertEqual(dial["unpriced_gross_usd"], 500)

    def test_new_exact_pairs_are_explicit_not_a_wildcard(self):
        for symbol, base in {"IQUSDT": "IQ", "GMEBUSDT": "GMEB", "LITEBUSDT": "LITEB"}.items():
            self.assertEqual(bp.VERIFIED_PAIRS[symbol], base)
        self.assertNotIn("GMEUSDT", bp.VERIFIED_PAIRS)
        self.assertNotIn("LITEUSDT", bp.VERIFIED_PAIRS)
        self.assertEqual(self.load(["GMEUSDT", "LITEUSDT"]), {})
        self.fetch.assert_not_called()

    def test_correct_identity_does_not_admit_short_new_histories(self):
        for symbol, start in (("GMEBUSDT", "2026-08-12"), ("LITEBUSDT", "2026-06-30")):
            with self.subTest(symbol=symbol):
                self.info, self.rows, self.fx_info, self.fx = fixture(start)
                self.cache = {}
                series = self.load([symbol])[symbol]
                ticker = bp.PREFIX + symbol
                raw = pd.DataFrame({ticker: pd.Series(series)})
                dial = exposure_dial.compute(coverage(ticker), raw, now=NOW, unclaimed=[], check_files=False)
                self.assertLess(dial["price_freshness"][ticker]["return_observations"], 63)
                self.assertEqual(dial["scale"], 0)
                self.assertFalse(dial["gate_open_for_new_probes"])
                self.assertEqual(dial["unpriced_gross_usd"], 500)

    def test_qkc_steem_are_exact_pairs_not_usd_or_name_aliases(self):
        for symbol, base in {"QKCUSDT": "QKC", "STEEMUSDT": "STEEM"}.items():
            self.assertEqual(bp.VERIFIED_PAIRS[symbol], base)
        unknown = ["QKC", "STEEM", "QKCUSD", "STEEMUSD", "QKC2USDT", "STEEM2USDT"]
        self.assertTrue(all(symbol not in bp.VERIFIED_PAIRS for symbol in unknown))
        self.assertEqual(self.load(unknown), {})
        self.fetch.assert_not_called()

    def test_qkc_steem_require_correct_identity_and_actual_fx_conversion(self):
        for symbol in ("QKCUSDT", "STEEMUSDT"):
            with self.subTest(symbol=symbol):
                self.info, self.rows, self.fx_info, self.fx = fixture()
                self.cache = {}
                series = self.load([symbol])[symbol]
                self.assertEqual(series["2026-09-09"], 8.)
                self.assertNotIn("2026-09-10", series)
                self.assertEqual(self.cache[bp.CACHE_KEY][symbol]["output_currency"], "USD")
                self.assertEqual(self.cache[bp.CACHE_KEY][symbol]["fx_last_close"], .8)
                row = next(row for row in self.info["symbols"] if row["symbol"] == symbol)
                row["baseAsset"] = "OTHER"
                self.cache = {}
                self.assertEqual(self.load([symbol]), {})

    def test_qkc_steem_mapping_cannot_admit_short_histories(self):
        for symbol in ("QKCUSDT", "STEEMUSDT"):
            with self.subTest(symbol=symbol):
                self.info, self.rows, self.fx_info, self.fx = fixture("2026-09-01")
                self.cache = {}
                ticker = bp.PREFIX + symbol
                raw = pd.DataFrame({ticker: pd.Series(self.load([symbol])[symbol])})
                dial = exposure_dial.compute(coverage(ticker), raw, now=NOW, unclaimed=[], check_files=False)
                self.assertLess(dial["price_freshness"][ticker]["return_observations"], 63)
                self.assertEqual(dial["scale"], 0)
                self.assertFalse(dial["gate_open_for_new_probes"])
                self.assertEqual(dial["unpriced_gross_usd"], 500)

    def test_ckb_tfuel_are_exact_pairs_not_usd_or_name_aliases(self):
        for symbol, base in {"CKBUSDT": "CKB", "TFUELUSDT": "TFUEL"}.items():
            self.assertEqual(bp.VERIFIED_PAIRS[symbol], base)
        unknown = ["CKB", "TFUEL", "CKBUSD", "TFUELUSD", "CKB2USDT", "TFUEL2USDT"]
        self.assertTrue(all(symbol not in bp.VERIFIED_PAIRS for symbol in unknown))
        self.assertEqual(self.load(unknown), {})
        self.fetch.assert_not_called()

    def test_ckb_tfuel_require_correct_identity_and_actual_fx_conversion(self):
        for symbol in ("CKBUSDT", "TFUELUSDT"):
            with self.subTest(symbol=symbol):
                self.info, self.rows, self.fx_info, self.fx = fixture()
                self.cache = {}
                series = self.load([symbol])[symbol]
                self.assertEqual(series["2026-09-09"], 8.)
                self.assertNotIn("2026-09-10", series)
                self.assertEqual(self.cache[bp.CACHE_KEY][symbol]["output_currency"], "USD")
                self.assertEqual(self.cache[bp.CACHE_KEY][symbol]["fx_last_close"], .8)
                row = next(row for row in self.info["symbols"] if row["symbol"] == symbol)
                row["baseAsset"] = "OTHER"
                self.cache = {}
                self.assertEqual(self.load([symbol]), {})

    def test_ckb_tfuel_mapping_cannot_admit_short_histories(self):
        for symbol in ("CKBUSDT", "TFUELUSDT"):
            with self.subTest(symbol=symbol):
                self.info, self.rows, self.fx_info, self.fx = fixture("2026-09-01")
                self.cache = {}
                ticker = bp.PREFIX + symbol
                raw = pd.DataFrame({ticker: pd.Series(self.load([symbol])[symbol])})
                dial = exposure_dial.compute(coverage(ticker), raw, now=NOW, unclaimed=[], check_files=False)
                self.assertLess(dial["price_freshness"][ticker]["return_observations"], 63)
                self.assertEqual(dial["scale"], 0)
                self.assertFalse(dial["gate_open_for_new_probes"])
                self.assertEqual(dial["unpriced_gross_usd"], 500)

    def test_binance_uses_same_two_calendar_day_freshness_as_crypto(self):
        ticker = bp.PREFIX + "CATIUSDT"
        series = self.load()["CATIUSDT"]
        raw = pd.DataFrame({ticker: pd.Series(series)}).iloc[:-2]
        result = risk_analytics.analyze(coverage(ticker), raw, now=NOW, check_files=False)
        self.assertEqual(result["price_freshness"][ticker]["max_age_calendar_days"], 2)
        self.assertIn("last settled quote is stale", result["price_freshness"][ticker]["reasons"])

    def test_shared_panel_routes_binance_without_yahoo_or_kraken_substitution(self):
        raw = pd.DataFrame({("Close", "SPY"): [100.]}, index=pd.to_datetime(["2026-09-09"]))
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(risk_engine, "PRICE_CACHE", os.path.join(tmp, "cache.json")), \
                mock.patch("yfinance.download", return_value=raw) as yahoo, \
                mock.patch.object(bp, "load", return_value={"CATIUSDT": {"2026-09-09": 8.}}) as source, \
                mock.patch.object(risk_engine, "kraken_closes") as spot:
            panel, counts = risk_engine.fetch_price_panel([{"ticker": bp.PREFIX + "CATIUSDT"}])
        self.assertEqual(panel[bp.PREFIX + "CATIUSDT"].iloc[-1], 8.)
        self.assertEqual(counts["binance_spot_usd_loaded"], 1)
        self.assertEqual(source.call_args.args[0], ["CATIUSDT"])
        self.assertNotIn(bp.PREFIX + "CATIUSDT", yahoo.call_args.args[0])
        spot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
