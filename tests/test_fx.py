#!/usr/bin/env python3
"""FX translation for the value book — the pence trap, the won trap, and the
refusal that must happen when a rate is missing.

All synthetic, no network. These exist because the book spent ten days summing
KRW, GBp and EUR as though they were dollars: 005930.KS booked at $5,000 was
worth about $3.60, and BARC.L booked at $5,000 was worth about $67. The single
most dangerous line here is the London one — .L quotes in PENCE, not pounds, and
getting that wrong replaces one currency bug with a hundred-times-worse one.
"""
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fx  # noqa: E402
import value_book as vb  # noqa: E402

# representative rates: USD per 1 MAJOR unit
RATES = {"USD": 1.0, "GBP": 1.35329, "KRW": 0.000708, "EUR": 1.157274,
         "JPY": 0.006277, "HKD": 0.127437}


# ------------------------------------------------------------- currency detect
class TestCurrencyDetection(unittest.TestCase):
    def test_london_is_pence_not_pounds(self):
        self.assertEqual(fx.quote_currency("BARC.L"), "GBp")
        self.assertEqual(fx.major_and_scale("GBp"), ("GBP", 0.01))

    def test_known_suffixes(self):
        for ticker, ccy in (("005930.KS", "KRW"), ("BNP.PA", "EUR"),
                            ("TTE.PA", "EUR"), ("7203.T", "JPY"),
                            ("BHP.AX", "AUD"), ("SHOP.TO", "CAD"),
                            ("0700.HK", "HKD"), ("600519.SS", "CNY")):
            self.assertEqual(fx.quote_currency(ticker), ccy, ticker)

    def test_us_listings_default_usd(self):
        for t in ("PDD", "MU", "ADBE", "ITUB", "TCEHY", "SHEL"):
            self.assertEqual(fx.quote_currency(t), "USD", t)

    def test_provider_cannot_override_the_pence_table(self):
        # yfinance frequently reports 'GBP' for a line that prints in pence.
        # The table wins on the minor unit; agreement on the major is enough.
        self.assertEqual(fx.quote_currency("BARC.L", info_ccy="GBP"), "GBp")

    def test_provider_conflict_refuses(self):
        # feed says USD for a Paris line: unresolvable, so we resolve nothing
        self.assertIsNone(fx.quote_currency("BNP.PA", info_ccy="USD"))

    def test_unknown_suffix_without_provider_is_unknown(self):
        self.assertIsNone(fx.quote_currency("FOO.ZZZ"))
        self.assertEqual(fx.quote_currency("FOO.ZZZ", info_ccy="EUR"), "EUR")


# ------------------------------------------------------------- translation
class TestTranslation(unittest.TestCase):
    def test_pence_case_barc_l(self):
        """5,000 GBp is ~US$67, not ~US$6,766 and certainly not $5,000."""
        usd = fx.to_usd(5000.0, "GBp", RATES)
        self.assertAlmostEqual(usd, 67.6645, places=3)
        self.assertLess(usd, 100.0)
        # the pounds mistake would be exactly 100x this
        self.assertAlmostEqual(fx.to_usd(5000.0, "GBP", RATES) / usd, 100.0,
                               places=6)

    def test_pence_price_of_a_real_holding(self):
        # BARC.L entry 527.10 pence -> ~$7.13 a share, not $527.10
        self.assertAlmostEqual(fx.to_usd(527.0999755859375, "GBp", RATES),
                               7.1331, places=3)

    def test_krw_case_samsung(self):
        """230,500 KRW a share is ~US$163, and 5,000 KRW is ~US$3.54."""
        self.assertAlmostEqual(fx.to_usd(230500.0, "KRW", RATES), 163.194,
                               places=2)
        self.assertAlmostEqual(fx.to_usd(5000.0, "KRW", RATES), 3.54, places=2)

    def test_usd_is_identity(self):
        self.assertEqual(fx.to_usd(123.45, "USD", RATES), 123.45)

    def test_eur_case(self):
        self.assertAlmostEqual(fx.to_usd(113.12, "EUR", RATES), 130.9108, places=3)

    def test_cents_and_agorot_are_minor_units(self):
        self.assertEqual(fx.major_and_scale("ZAc"), ("ZAR", 0.01))
        self.assertEqual(fx.major_and_scale("ILA"), ("ILS", 0.01))


# ------------------------------------------------------------- refusals
class TestRefusesRatherThanGuessing(unittest.TestCase):
    def test_missing_rate_raises(self):
        with self.assertRaises(fx.FXUnavailable):
            fx.usd_per_quote_unit("KRW", {"USD": 1.0})

    def test_missing_rate_never_becomes_one(self):
        """The whole bug in one assertion: no rate must not mean rate = 1."""
        try:
            got = fx.usd_per_quote_unit("KRW", {"USD": 1.0})
        except fx.FXUnavailable:
            got = None
        self.assertIsNone(got)

    def test_degenerate_rates_raise(self):
        for bad in (0.0, -1.0, float("nan"), float("inf"), None, "x"):
            with self.assertRaises(fx.FXUnavailable):
                fx.usd_per_quote_unit("EUR", {"EUR": bad})

    def test_unknown_currency_raises(self):
        with self.assertRaises(fx.FXUnavailable):
            fx.usd_per_quote_unit(None, RATES)

    def test_fetch_omits_what_it_cannot_price(self):
        def download(symbols):
            return {"EURUSD=X": 1.157274}, "2026-08-16"
        rates, asof = fx.fetch_usd_rates({"EUR", "KRW", "GBp"}, download=download)
        self.assertAlmostEqual(rates["EUR"], 1.157274)
        self.assertNotIn("KRW", rates)     # absent, never a placeholder
        self.assertNotIn("GBP", rates)
        self.assertEqual(asof, "2026-08-16")

    def test_fetch_inverts_when_only_the_reciprocal_quotes(self):
        def download(symbols):
            return {"USDKRW=X": 1412.0}, "2026-08-16"
        rates, _ = fx.fetch_usd_rates({"KRW"}, download=download)
        self.assertAlmostEqual(rates["KRW"], 1.0 / 1412.0, places=12)


# ------------------------------------------------------------- the book itself
class TestValueBookCurrency(unittest.TestCase):
    def setUp(self):
        self._real = vb._provider_ccy
        vb._provider_ccy = lambda t: None      # no network in tests

    def tearDown(self):
        vb._provider_ccy = self._real

    def _pb(self, closes, rates=RATES):
        return vb.price_book(list(closes), closes=closes, rates=rates)

    def test_prices_translated_for_valuation(self):
        pb = self._pb({"005930.KS": 230500.0, "BARC.L": 527.1, "BNP.PA": 113.12,
                       "MU": 893.19})
        self.assertAlmostEqual(pb["usd_px"]["005930.KS"], 163.194, places=2)
        self.assertAlmostEqual(pb["usd_px"]["BARC.L"], 7.1331, places=3)
        self.assertAlmostEqual(pb["usd_px"]["BNP.PA"], 130.9108, places=3)
        self.assertEqual(pb["usd_px"]["MU"], 893.19)
        self.assertEqual(pb["refused"], {})
        self.assertEqual(pb["ccy"]["BARC.L"], "GBp")
        self.assertAlmostEqual(pb["rate"]["BARC.L"], 0.0135329, places=9)

    def test_sizing_is_equal_weight_in_dollars_not_in_local_units(self):
        """$5,000 into BARC.L must buy ~701 shares, not 9.49."""
        closes = {"BARC.L": 527.1, "MU": 893.19}
        pb = self._pb(closes)
        eq, n = 10000.0, 2
        per = eq / n
        units = {t: per / pb["usd_px"][t] for t in closes}
        self.assertAlmostEqual(units["BARC.L"],
                               5000.0 / (527.1 * RATES["GBP"] / 100.0), places=6)
        self.assertGreater(units["BARC.L"], 690)
        # and each leg is genuinely worth the same number of dollars
        vals = [units[t] * pb["usd_px"][t] for t in closes]
        self.assertAlmostEqual(vals[0], vals[1], places=6)

    def test_equity_is_a_usd_sum(self):
        b = {"cash": 100.0, "holdings": {"BARC.L": 100.0, "005930.KS": 2.0}}
        pb = self._pb({"BARC.L": 527.1, "005930.KS": 230500.0})
        eq = vb.equity_of(b, pb["usd_px"])
        want = (100.0 + 100 * 527.1 * RATES["GBP"] / 100.0
                + 2 * 230500.0 * RATES["KRW"])
        self.assertAlmostEqual(eq, want, places=6)

    def test_legacy_units_restate_downward(self):
        """The live book's 0.0217 units of Samsung are worth ~$3.54, not $5,000."""
        b = {"cash": 0.0, "holdings": {"005930.KS": 0.021691973969631236}}
        pb = self._pb({"005930.KS": 230500.0})
        self.assertAlmostEqual(vb.equity_of(b, pb["usd_px"]), 3.54, places=2)

    def test_book_refuses_to_value_without_a_rate(self):
        rates = {"USD": 1.0, "GBP": 1.35329}          # no KRW
        pb = self._pb({"BARC.L": 527.1, "005930.KS": 230500.0}, rates=rates)
        self.assertIn("005930.KS", pb["refused"])
        self.assertNotIn("005930.KS", pb["usd_px"])
        b = {"cash": 0.0, "holdings": {"BARC.L": 10.0, "005930.KS": 2.0}}
        with self.assertRaises(fx.FXUnavailable):
            vb.equity_of(b, pb["usd_px"])

    def test_unpriceable_name_is_never_counted_as_zero(self):
        """Old code summed px.get(t, 0.0) — a missing name quietly valued at $0."""
        b = {"cash": 1000.0, "holdings": {"MU": 1.0, "005930.KS": 2.0}}
        pb = self._pb({"MU": 893.19, "005930.KS": 230500.0},
                      rates={"USD": 1.0})
        with self.assertRaises(fx.FXUnavailable):
            vb.equity_of(b, pb["usd_px"])

    def test_unknown_currency_is_refused_not_assumed_usd(self):
        pb = self._pb({"FOO.ZZZ": 100.0, "MU": 893.19})
        self.assertIn("FOO.ZZZ", pb["refused"])
        self.assertNotIn("FOO.ZZZ", pb["usd_px"])


# ------------------------------------------------------------- tournament label
class TestTournamentCurrencyLabel(unittest.TestCase):
    def test_kraken_engine_is_labelled_usd(self):
        """start_cash 40,000 USD, XXX/USD pairs, USD marks — never A$."""
        with open(os.path.join(BASE, "tournament.py")) as f:
            src = f.read()
        i = src.index('add("kraken_engine"')
        call = src[i:i + 220]
        self.assertIn('ccy="USD"', call)
        self.assertNotIn('ccy="AUD"', call)


if __name__ == "__main__":
    unittest.main(verbosity=2)
