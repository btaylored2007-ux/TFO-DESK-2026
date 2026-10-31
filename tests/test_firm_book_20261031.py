"""Offline firm-book tests. Brokers are mocks. No ledger is written. No network."""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import firm_book as F

U = dt.timezone.utc
WEEKEND = dt.datetime(2026, 10, 30, 21, 30, tzinfo=U)   # Friday 17:30 America/New_York
MIDWEEK = dt.datetime(2026, 10, 28, 15, 0, tzinfo=U)    # Wednesday
AUD = 0.70


class _Book:
    def __init__(self, rate=AUD, boom=None):
        self.rate, self.boom = rate, boom

    def audusd(self, px_fn=None, t=None):
        if self.boom:
            raise RuntimeError(self.boom)
        return self.rate


def _dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f)


def _tree(base):
    _dump(os.path.join(base, "reports", "value_book.json"),
          {"cash": 100, "equity": 700, "holdings": {"SPY": 1}, "mark_px_usd": {"SPY": 500}})
    _dump(os.path.join(base, "reports", "veteran_desk_book.json"),
          {"seats": {"energy": {"cash_aud": 400000, "equity_aud": 510000, "positions": {
              "1": {"ticker": "AAPL", "shares": 10, "side": "LONG", "status": "open", "mark": 100, "fill": 90, "venue": "US"}}}}})
    _dump(os.path.join(base, "state", "thf", "thf_book.json"),
          {"seats": {"macro": {"cash_aud": 450000, "equity_aud": 500000, "positions": {
              "2": {"ticker": "BHP.AX", "shares": 100, "side": "SHORT", "status": "open", "mark": 40, "fill": 42, "venue": "ASX"}}}}})
    _dump(os.path.join(base, "state", "trend18.json"),
          {"ccy": "USD", "cash": 1000, "equity": 5000, "holdings": {"UPRO": 10}, "mark_px_usd": {"UPRO": 80}})
    _dump(os.path.join(base, "state", "oanda", "snapshot.json"),
          {"nav": 100000, "balance": 90000, "currency": "AUD", "trades": [
              {"instrument": "EUR_USD", "units": 1000, "price": 1.1, "tfo": True, "upl_aud": 12}]})
    _dump(os.path.join(base, "reports", "crypto_clip_book.json"),
          {"ccy": "USD", "cash": 50, "equity": 350, "holdings": {"BTC-USD": 2}, "mark_px_usd": {"BTC-USD": 100}})
    _dump(os.path.join(base, "reports", "fundamental_book.json"),
          {"cash": 100, "equity": 1000, "positions": {"ABC": {"units": 5, "side": "short", "mark_usd": 20}}})
    _dump(os.path.join(base, "reports", "v2", "tfo_v2_book.json"),
          {"cash": 10, "equity": 999, "positions": {"ZZZ": {"qty": 1, "side": "long", "mark_usd": 9}}})
    _dump(os.path.join(base, "state", "hunt48_short", "ledger.json"),
          {"cash_aud": 2000, "equity_aud": 2000, "trades": [
              {"status": "FILLED", "ticker": "GLD", "direction": "SHORT", "shares": 3, "last_px": 180}]})
    _dump(os.path.join(base, "reports", "firm_risk.json"), {"firm_equity_usd": 123, "level": "OK"})


def _aud(now=MIDWEEK, caches=None, book=None):
    return F.resolve_audusd(now, book_mod=book or _Book(), caches=caches or [])


class AudUsd(unittest.TestCase):
    def test_live_rate_from_the_book_helper(self):
        info = _aud()
        self.assertTrue(info["ok"])
        self.assertEqual(info["rate"], 0.70)
        self.assertFalse(info["frozen"])
        self.assertEqual(info["source"], "veteran_desk.book.audusd")

    def test_weekend_freeze_keeps_last_good_and_open_session_does_not(self):
        cache = [{"v": 0.71, "utc": "2026-10-30T10:00:00+00:00"}]
        frozen = F.resolve_audusd(WEEKEND, book_mod=_Book(boom="no live"), caches=cache)
        self.assertTrue(frozen["ok"])
        self.assertTrue(frozen["frozen"])
        self.assertEqual(frozen["rate"], 0.71)
        self.assertTrue(frozen["source"].startswith("weekend_freeze:"))
        refused = F.resolve_audusd(MIDWEEK, book_mod=_Book(boom="no live"), caches=[
            {"v": 0.71, "utc": "2026-10-28T12:00:00+00:00"}])
        self.assertFalse(refused["ok"])
        self.assertIsNone(refused["rate"])
        self.assertNotIn("0.65", json.dumps(refused))

    def test_stale_weekend_rate_and_insane_rate_are_not_used(self):
        old = F.resolve_audusd(WEEKEND, book_mod=_Book(boom="no live"), caches=[
            {"v": 0.71, "utc": "2026-10-26T00:00:00+00:00"}])
        self.assertFalse(old["ok"])
        self.assertIsNone(old["rate"])
        insane = F.resolve_audusd(MIDWEEK, book_mod=_Book(rate=0.30))
        self.assertFalse(insane["ok"])
        self.assertIsNone(insane["rate"])

    def test_fx_last_good_shape_is_accepted_on_the_freeze(self):
        blob = {"rates": {"AUD": {"rate": 0.69, "quote_utc": "2026-10-30T12:00:00+00:00"}}}
        info = F.resolve_audusd(WEEKEND, book_mod=_Book(boom="down"), caches=[blob])
        self.assertEqual(info["rate"], 0.69)
        self.assertTrue(info["frozen"])


class Book(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp()
        _tree(self.base)
        self.info = _aud()

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_firm_total_is_aud_and_excludes_oanda_and_v2(self):
        book = F.build_book(self.base, self.info, MIDWEEK, firm_risk_query=lambda path=None: {"ok": True, "utc": "2026-10-28T15:00:00Z"})
        by = {d["key"]: d for d in book["desks"]}
        self.assertTrue(by["veteran"]["in_firm_total"])
        self.assertEqual(by["veteran"]["equity_aud"], 510000)
        self.assertEqual(by["thf"]["equity_aud"], 500000)
        self.assertAlmostEqual(by["trend18"]["equity_aud"], round(5000 / AUD, 2))
        self.assertAlmostEqual(by["fundamental"]["equity_aud"], round(1000 / AUD, 2))
        self.assertEqual(by["main"]["equity_aud"], round(700 / AUD, 2))
        self.assertEqual(by["kraken_crypto"]["equity_aud"], round(350 / AUD, 2))
        self.assertEqual(by["hunts"]["equity_aud"], 2000)
        self.assertFalse(by["oanda"]["in_firm_total"])
        self.assertEqual(by["oanda"]["equity_aud"], 100000)
        self.assertFalse(by["v2"]["in_firm_total"])
        want = 510000 + 500000 + round(5000 / AUD, 2) + round(1000 / AUD, 2) + round(700 / AUD, 2) + round(350 / AUD, 2) + 2000
        self.assertAlmostEqual(book["firm"]["equity_aud"], round(want, 2))
        self.assertTrue(book["firm"]["complete"])
        self.assertGreater(book["firm_including_separate"]["equity_aud"], book["firm"]["equity_aud"])
        self.assertEqual(book["legacy_firm_equity_usd"], 123)
        self.assertTrue(book["firm_risk_service"]["present"])
        self.assertEqual(book["firm_risk_service"]["snapshot"]["ok"], True)
        aapl = [p for p in by["veteran"]["positions"] if p["symbol"] == "AAPL"][0]
        self.assertAlmostEqual(aapl["value_aud"], round(10 * 100 / AUD, 2))
        bhp = [p for p in by["thf"]["positions"] if p["symbol"] == "BHP.AX"][0]
        self.assertEqual(bhp["qty"], -100)
        self.assertEqual(bhp["value_aud"], round(-100 * 40, 2))
        abc = [p for p in by["fundamental"]["positions"] if p["symbol"] == "ABC"][0]
        self.assertLess(abc["qty"], 0)
        self.assertGreater(book["firm"]["exposure"]["gross_aud"], 0)

    def test_missing_audusd_does_not_invent_a_total(self):
        info = {"ok": False, "rate": None, "frozen": False, "source": None, "error": "down"}
        book = F.build_book(self.base, info, MIDWEEK)
        by = {d["key"]: d for d in book["desks"]}
        self.assertFalse(book["firm"]["complete"])
        self.assertIsNone(book["firm"]["equity_aud"])
        self.assertIsNone(by["trend18"]["equity_aud"])
        self.assertEqual(by["veteran"]["equity_aud"], 510000)
        self.assertNotIn("0.65", json.dumps(book["audusd"]))

    def test_unreadable_ledger_is_not_zero(self):
        with open(os.path.join(self.base, "reports", "fundamental_book.json"), "w") as f:
            f.write("{")
        book = F.build_book(self.base, self.info, MIDWEEK)
        fund = [d for d in book["desks"] if d["key"] == "fundamental"][0]
        self.assertTrue(fund["present"])
        self.assertFalse(fund["complete"])
        self.assertIsNone(fund["equity_aud"])
        self.assertFalse(book["firm"]["complete"])
        self.assertTrue(any(w["desk"] == "fundamental" for w in book["firm"]["withheld"]))

    def test_carry_stays_in_aud_and_does_not_invent_cash(self):
        _dump(os.path.join(self.base, "reports", "carry_paper_book.json"),
              {"notional_cap_aud": 10000, "realized_aud": 25, "funding_accrued_aud": 5, "costs_paid_aud": 1,
               "positions": {"BTC": {"notional_aud": 1000, "mark_move_pct": 0}}})
        book = F.build_book(self.base, self.info, MIDWEEK)
        main = [d for d in book["desks"] if d["key"] == "main"][0]
        self.assertEqual(main["equity_aud"], round(700 / AUD, 2) + 10029)
        carry = [s for s in main["subbooks"] if s["name"] == "carry_paper"][0]
        self.assertEqual(carry["equity_aud"], 10029)
        self.assertIsNone(carry["cash_aud"])
        self.assertIsNone(main["cash_aud"])
        legs = [p for p in main["positions"] if "BTC" in p["symbol"]]
        self.assertEqual(sorted(p["qty"] for p in legs), [-1000, 1000])

    def test_service_absent_is_reported(self):
        book = F.build_book(self.base, self.info, MIDWEEK)
        # The pending service is not on this tree. A file-only snapshot is also honest.
        fr = book["firm_risk_service"]
        if fr.get("present"):
            self.assertIn("snapshot", fr)
        else:
            self.assertIn("not on this tree", fr["note"])

    def test_writer_touches_only_firm_book_outputs(self):
        before = {}
        for dirpath, _, names in os.walk(self.base):
            for name in names:
                path = os.path.join(dirpath, name)
                with open(path, "rb") as fh:
                    before[path] = fh.read()
        fetches = _matching_fetches(F.build_book(self.base, self.info, MIDWEEK))
        book, recon = F.run(self.base, now=MIDWEEK, audusd_info=self.info, fetches=fetches, write=True)
        for path, blob in before.items():
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), blob, path)
        self.assertTrue(os.path.isfile(os.path.join(self.base, "state", "firm_book.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.base, "static", "firm_book.json")))
        day = F._wita(MIDWEEK).strftime("%Y%m%d")
        self.assertTrue(os.path.isfile(os.path.join(self.base, "reports", "recon_%s.json" % day)))
        self.assertTrue(recon["matched"])
        self.assertTrue(book["recon"]["matched"])
        with self.assertRaises(RuntimeError):
            F.atomic_json(os.path.join(self.base, "reports", "fundamental_book.json"), {"equity": 0})
        with self.assertRaises(RuntimeError):
            F.atomic_json(os.path.join(self.base, "state", "hunt48_short", "ledger.json"), {})


def _matching_fetches(book):
    def one(name):
        positions, cash, measured, no_book = F._book_side(book, name)
        self_pos = {}
        for p in positions:
            if p.get("status") not in (None, "open"):
                continue
            k = F.norm_symbol(name, p["symbol"])
            self_pos[k] = self_pos.get(k, 0.0) + float(p["qty"])
        rows = [{"symbol": k, "qty": q} for k, q in self_pos.items()]
        return {"ok": True, "status": "OK", "positions": rows, "cash_aud": cash}

    return {name: (lambda name=name: one(name)) for name in ("oanda", "kraken", "ibkr")}


class Recon(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp()
        _tree(self.base)
        self.book = F.build_book(self.base, _aud(), MIDWEEK)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_match_within_tolerance(self):
        recon = F.reconcile(self.book, _matching_fetches(self.book), MIDWEEK)
        self.assertTrue(recon["matched"])
        self.assertEqual(recon["n_breaks"], 0)
        for row in recon["brokers"].values():
            self.assertTrue(row["matched"])

    def test_qty_and_cash_breaks(self):
        base = _matching_fetches(self.book)
        def oanda():
            row = base["oanda"]()
            row["positions"][0]["qty"] = row["positions"][0]["qty"] + 5
            row["cash_aud"] = row["cash_aud"] + 500
            return row
        fetches = dict(base, oanda=oanda)
        recon = F.reconcile(self.book, fetches, MIDWEEK)
        self.assertFalse(recon["matched"])
        kinds = {b["type"] for b in recon["brokers"]["oanda"]["breaks"]}
        self.assertIn("qty", kinds)
        self.assertIn("cash", kinds)
        self.assertTrue(recon["brokers"]["kraken"]["matched"])

    def test_unreachable_is_not_a_match_even_when_flat(self):
        def down():
            return {"ok": False, "status": "UNREACHABLE", "error": "dns timeout", "positions": []}
        recon = F.reconcile(self.book, {n: down for n in ("oanda", "kraken", "ibkr")}, MIDWEEK)
        self.assertFalse(recon["matched"])
        for row in recon["brokers"].values():
            self.assertEqual(row["status"], "UNREACHABLE")
            self.assertFalse(row["matched"])
            self.assertEqual(row["breaks"], [])

    def test_missing_book_is_not_a_flat_match(self):
        empty = tempfile.mkdtemp()
        try:
            book = F.build_book(empty, _aud(), MIDWEEK)
            def flat():
                return {"ok": True, "status": "OK", "positions": [], "cash_aud": 0}
            recon = F.reconcile(book, {n: flat for n in ("oanda", "kraken", "ibkr")}, MIDWEEK)
            self.assertFalse(recon["matched"])
            for row in recon["brokers"].values():
                self.assertEqual(row["status"], "NO_BOOK")
                self.assertFalse(row["matched"])
        finally:
            shutil.rmtree(empty, ignore_errors=True)

    def test_book_only_recon_is_not_a_match(self):
        recon = F.not_run_recon(MIDWEEK)
        self.assertEqual(recon["status"], "NOT_RUN")
        self.assertFalse(recon["matched"])

    def test_fetcher_exception_is_unreachable(self):
        def boom():
            raise ConnectionError("API-Key: supersecretvalue refused")
        recon = F.reconcile(self.book, {"oanda": boom, "kraken": boom, "ibkr": boom}, MIDWEEK)
        blob = json.dumps(recon)
        self.assertNotIn("supersecretvalue", blob)
        self.assertFalse(recon["matched"])
        self.assertIn("API-Key=***", recon["brokers"]["kraken"]["error"])


class Brokers(unittest.TestCase):
    def test_oanda_practice_parse(self):
        seen = []

        def get(path):
            seen.append(path)
            if path.endswith("/summary"):
                return {"account": {"currency": "AUD", "balance": "10", "NAV": "12"}}
            if path.endswith("/openTrades"):
                return {"trades": [{"instrument": "EUR_USD", "currentUnits": "-250"}]}
            raise AssertionError(path)

        out = F.fetch_oanda(get=get, audusd=AUD)
        self.assertTrue(out["ok"])
        self.assertEqual(out["positions"], [{"symbol": "EUR_USD", "qty": -250}])
        self.assertEqual(out["cash_aud"], 10)
        self.assertTrue(any(p.endswith("/summary") for p in seen))

    def test_oanda_down_is_unreachable(self):
        def get(path):
            raise TimeoutError("timed out token=abc123secret")
        out = F.fetch_oanda(get=get)
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "UNREACHABLE")
        self.assertNotIn("abc123secret", out["error"])

    def test_kraken_balance_and_secret_stay_out_of_the_result(self):
        d = tempfile.mkdtemp()
        try:
            keyf = os.path.join(d, "kraken.env")
            with open(keyf, "w") as f:
                f.write("KRAKEN_API_KEY=publickey\nKRAKEN_API_SECRET=%s\n" % __import__("base64").b64encode(b"s" * 64).decode())

            def post(path, body, headers):
                if "publickey" not in headers.get("API-Key", ""):
                    raise AssertionError("key not sent")
                if path.endswith("Balance"):
                    return {"error": [], "result": {"XXBT": "2", "ZUSD": "70"}}
                return {"error": [], "result": {"POS": {"pair": "PF_ETHUSD", "vol": "1", "vol_closed": "0", "type": "sell"}}}

            out = F.fetch_kraken(post=post, keyfile=keyf, audusd=AUD)
            blob = json.dumps(out)
            self.assertNotIn("publickey", blob)
            self.assertTrue(out["ok"])
            self.assertEqual(out["cash_aud"], round(70 / AUD, 2))
            syms = {F.norm_symbol("kraken", p["symbol"]): p["qty"] for p in out["positions"]}
            self.assertEqual(syms["BTC"], 2)
            self.assertEqual(syms["ETH"], -1)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_kraken_error_redacts_and_missing_key_is_not_a_read(self):
        def post(path, body, headers):
            raise RuntimeError("API-Key: supersecretvalue")
        d = tempfile.mkdtemp()
        try:
            keyf = os.path.join(d, "kraken.env")
            with open(keyf, "w") as f:
                f.write("KRAKEN_API_KEY=k\nKRAKEN_API_SECRET=%s\n" % __import__("base64").b64encode(b"s" * 64).decode())
            out = F.fetch_kraken(post=post, keyfile=keyf, audusd=AUD)
            self.assertEqual(out["status"], "UNREACHABLE")
            self.assertNotIn("supersecretvalue", json.dumps(out))
            missing = F.fetch_kraken(post=post, keyfile=os.path.join(d, "nope.env"), audusd=AUD)
            self.assertEqual(missing["status"], "NO_CREDENTIALS")
            self.assertFalse(missing["ok"])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_ibkr_readonly_parse_and_bad_port_and_down(self):
        class Contract:
            symbol, secType, localSymbol = "AAPL", "STK", "AAPL"
        class Pos:
            contract, position = Contract(), 4
        class Cash:
            tag, currency, value = "TotalCashValue", "AUD", "1500.5"
        class IB:
            def positions(self):
                return [Pos()]
            def accountValues(self):
                return [Cash()]
            def disconnect(self):
                self.closed = True
        ib = IB()
        out = F.fetch_ibkr(connect=lambda: ib, audusd=AUD)
        self.assertTrue(out["ok"])
        self.assertEqual(out["positions"][0]["qty"], 4)
        self.assertEqual(out["cash_aud"], 1500.5)
        self.assertTrue(getattr(ib, "closed", False))
        refused = F.fetch_ibkr(connect=lambda: IB(), port=4001)
        self.assertEqual(refused["status"], "BROKER_ERROR")
        self.assertFalse(refused["ok"])
        down = F.fetch_ibkr(connect=lambda: (_ for _ in ()).throw(OSError("connection refused")))
        self.assertEqual(down["status"], "UNREACHABLE")
        self.assertEqual(down["positions"], [])

    def test_sign_does_not_echo_the_secret(self):
        import base64
        secret = base64.b64encode(b"s" * 64).decode()
        sig = F.kraken_sign("/0/private/Balance", {"nonce": "1"}, secret)
        self.assertNotIn(secret, sig)
        other = F.kraken_sign("/0/private/Balance", {"nonce": "1"}, base64.b64encode(b"t" * 64).decode())
        self.assertNotEqual(sig, other)


class Dashboard(unittest.TestCase):
    def test_card_reads_the_firm_book_feed(self):
        with open(os.path.join(ROOT, "command_dashboard.html")) as fh:
            html = fh.read()
        self.assertIn('id="firmbook"', html)
        self.assertIn("/static/firm_book.json", html)
        self.assertIn("fb_recon", html)


if __name__ == "__main__":
    unittest.main()
