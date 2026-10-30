"""tfo_corporate_actions: splits, dividends, idempotency, scheduling, logging (PAPER, no network)."""
import copy, datetime as dt, json, os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tfo_corporate_actions as ca

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 29, 1, 15, tzinfo=UTC)              # 10:15 JST 29 Sep
EX = int(dt.datetime(2026, 9, 29, 0, 0, tzinfo=UTC).timestamp())  # 09:00 JST 29 Sep (Yahoo stamps the session open)
FX = 0.0064


def ev(sym="8766.T", splits=(), divs=(), closes=None, tz="Asia/Tokyo", ccy="JPY", start=EX):
    return {"sym": sym, "tz": tz, "ccy": ccy, "url": "https://query1.finance.yahoo.com/v8/finance/chart/%s?events=div,split" % sym,
            "splits": [dict(s) for s in splits], "divs": [dict(d) for d in divs],
            "closes": closes if closes is not None else {"2026-09-25": 532.8, "2026-09-28": 538.333, "2026-09-29": 528.4},
            "last_px": 528.4, "regular_start": start, "regular_end": start + 6 * 3600}


SPLIT15 = {"date": "2026-09-29", "ts": EX, "numerator": 15.0, "denominator": 1.0, "ratio": 15.0, "split_ratio": "15:1"}
DIV8766 = {"date": "2026-09-29", "ts": EX, "amount": 122.5}


def pos8766(**kw):
    p = {"side": "long", "units": 4995.310654197686, "notional_usd": 256000.0, "entry_px": 8081.0, "entry_ccy": "JPY",
         "entry_px_usd": 51.24806397873908, "rail_orig_entry_usd": 51.24806397873908, "entry_utc": "2026-09-28T04:36:51Z",
         "mark": 8075.0, "mark_usd": 51.2, "mark_quote_utc": "2026-09-28T06:30:00Z",
         "rail_trail": {"half_banked": True, "hwm_usd": 52.0, "level_usd": 51.248, "atr14_pct": 0.02}}
    p.update(kw)
    return p


def ledger(**positions):
    return {"cash": 1000.0, "realised_usd": 0.0, "seed_usd": 1000.0, "equity": 1000.0, "positions": positions, "closed": {}}


class Fetch:
    def __init__(self, table):
        self.table, self.calls = table, []

    def __call__(self, sym, p1, p2):
        self.calls.append(sym)
        v = self.table[sym]
        if isinstance(v, Exception):
            raise v
        return copy.deepcopy(v)


fx = lambda ccy, d: (FX if ccy == "JPY" else 1.0, "test fx")


class SplitTests(unittest.TestCase):
    def test_split_rebases_qty_basis_rails_and_pre_ex_mark_keeping_money(self):
        led = ledger(**{"8766.T": pos8766()})
        acts = ca.process_book("fundamental_book", led, NOW, Fetch({"8766.T": ev(splits=[SPLIT15])}), fx, splits_only=True)
        p = led["positions"]["8766.T"]
        self.assertEqual([a["type"] for a in acts], ["split"])
        self.assertAlmostEqual(p["units"], 74929.65981296528, 6)
        self.assertAlmostEqual(p["entry_px"], 8081.0 / 15)
        self.assertAlmostEqual(p["entry_px_usd"], 51.24806397873908 / 15)
        self.assertAlmostEqual(p["rail_orig_entry_usd"], 51.24806397873908 / 15)
        self.assertAlmostEqual(p["rail_trail"]["hwm_usd"], 52.0 / 15)
        self.assertAlmostEqual(p["rail_trail"]["level_usd"], 51.248 / 15)
        self.assertEqual(p["rail_trail"]["atr14_pct"], 0.02)               # a percentage: unchanged
        self.assertAlmostEqual(p["mark_usd"], 51.2 / 15)                   # pre-ex-date mark re-based
        self.assertEqual(p["notional_usd"], 256000.0)                      # money unchanged
        rec = p["corporate_actions"][0]
        self.assertEqual(rec["pre_split"]["units"], 4995.310654197686)
        self.assertEqual(rec["pre_split"]["entry_px_usd"], 51.24806397873908)
        self.assertTrue(rec["mark_rebased"])
        self.assertEqual(led["cash"], 1000.0)

    def test_split_is_idempotent(self):
        led = ledger(**{"8766.T": pos8766()})
        f = Fetch({"8766.T": ev(splits=[SPLIT15])})
        ca.process_book("fundamental_book", led, NOW, f, fx, splits_only=True)
        snap = copy.deepcopy(led)
        self.assertEqual(ca.process_book("fundamental_book", led, NOW, f, fx, splits_only=True, events_cache={}), [])
        self.assertEqual(led, snap)

    def test_existing_manual_corporate_action_block_counts_as_applied(self):
        p = pos8766(units=74929.65981296528, entry_px_usd=3.4165, rail_orig_entry_usd=3.4165, mark_usd=3.35,
                    corporate_action={"type": "stock_split", "ratio": "15-for-1", "ex_date": "2026-09-29"})
        led = ledger(**{"8766.T": p})
        acts = ca.process_book("fundamental_book", led, NOW, Fetch({"8766.T": ev(splits=[SPLIT15])}), fx, splits_only=True)
        self.assertEqual(acts, [])
        self.assertEqual(led["positions"]["8766.T"]["units"], 74929.65981296528)

    def test_post_ex_mark_is_not_rebased(self):
        led = ledger(**{"8766.T": pos8766(mark=528.4, mark_usd=3.38, mark_quote_utc="2026-09-29T00:50:00Z")})
        ca.process_book("fundamental_book", led, NOW, Fetch({"8766.T": ev(splits=[SPLIT15])}), fx, splits_only=True)
        self.assertEqual(led["positions"]["8766.T"]["mark_usd"], 3.38)

    def test_split_before_fill_ignored(self):
        led = ledger(**{"8766.T": pos8766(entry_utc="2026-09-29T00:30:00Z")})   # bought on the ex-date: already post-split
        self.assertEqual(ca.process_book("fundamental_book", led, NOW, Fetch({"8766.T": ev(splits=[SPLIT15])}), fx, splits_only=True), [])

    def test_convexity_split_keeps_premium_and_total_mark(self):
        p = {"units": 100.0, "premium_usd": 3750.0, "mark_usd": 3770.0, "rail_orig_entry_usd": 37.5, "entry_px": 37.5,
             "entry_utc": "2026-09-20T00:00:00Z", "mark_quote_utc": "2026-09-28T06:00:00Z"}
        led = {"cash": 0.0, "positions": {"7004.T": p}}
        ca.process_book("convexity_sleeve", led, NOW, Fetch({"7004.T": ev("7004.T", splits=[dict(SPLIT15, ratio=5.0, split_ratio="5:1")])}), fx)
        self.assertEqual(p["units"], 500.0)
        self.assertEqual(p["premium_usd"], 3750.0)
        self.assertEqual(p["mark_usd"], 3770.0)
        self.assertAlmostEqual(p["rail_orig_entry_usd"], 7.5)

    def test_v2_books_refused(self):
        with self.assertRaises(ValueError):
            ca.process_book("tfo_v2_book", {"positions": {}}, NOW, Fetch({}), fx)


class DividendTests(unittest.TestCase):
    def test_8766_dividend_published_per_pre_split_share_is_rebased(self):
        led = ledger(**{"8766.T": pos8766()})
        reg = {"events": []}
        acts = ca.process_book("fundamental_book", led, NOW, Fetch({"8766.T": ev(splits=[SPLIT15], divs=[DIV8766])}), fx, registry=reg)
        div = [a for a in acts if a["type"] == "dividend"][0]
        self.assertAlmostEqual(div["amount_per_share"], 122.5 / 15)
        expect = round(4995.310654197686 * 122.5 * FX, 2)                   # = pre-split shares x JPY 122.5
        self.assertAlmostEqual(div["usd"], expect, 2)
        self.assertAlmostEqual(led["cash"], 1000.0 + expect, 2)
        self.assertAlmostEqual(led["realised_usd"], expect, 2)               # identity stays reconciled
        self.assertAlmostEqual(led["equity"], 1000.0 + expect, 2)
        self.assertEqual(reg["events"][0]["applied_utc"], ca._utc(NOW))
        self.assertIn("http", reg["events"][0]["source_url"])

    def test_dividend_idempotent_and_registry_prevents_double_count(self):
        led = ledger(**{"6501.T": pos8766(units=1949.0, entry_utc="2026-09-28T04:38:06Z")})
        f = Fetch({"6501.T": ev("6501.T", divs=[{"date": "2026-09-29", "ts": EX, "amount": 28.0}], closes={"2026-09-28": 5518.0})})
        reg = {"events": []}
        ca.process_book("fundamental_book", led, NOW, f, fx, registry=reg)
        cash = led["cash"]
        self.assertAlmostEqual(cash - 1000.0, round(1949.0 * 28 * FX, 2), 2)
        self.assertEqual([a["type"] for a in ca.process_book("fundamental_book", led, NOW, f, fx, registry=reg, events_cache={})], [])
        self.assertEqual(led["cash"], cash)
        # an event already in the sourced registry (fundamental_distributions path) is never booked here too
        led2 = ledger(**{"6501.T": pos8766(units=1949.0, entry_utc="2026-09-28T04:38:06Z")})
        acts = ca.process_book("fundamental_book", led2, NOW, f, fx, registry={"events": [{"symbol": "6501.T", "ex_date": "2026-09-29"}]}, events_cache={})
        self.assertEqual([a["type"] for a in acts], ["dividend_skipped"])
        self.assertEqual(led2["cash"], 1000.0)

    def test_short_owes_dividend(self):
        led = ledger(X=pos8766(side="short", units=100.0, entry_utc="2026-09-20T00:00:00Z"))
        ca.process_book("fundamental_book", led, NOW, Fetch({"X": ev("X", divs=[{"date": "2026-09-29", "ts": EX, "amount": 10.0}], closes={"2026-09-28": 1000.0})}), fx)
        self.assertAlmostEqual(led["cash"], 1000.0 - 100 * 10 * FX, 2)
        self.assertAlmostEqual(led["realised_usd"], -100 * 10 * FX, 2)

    def test_units_sold_on_or_after_ex_date_are_entitled(self):
        p = pos8766(units=20.0, entry_utc="2026-09-10T00:00:00Z", exit_fills=[
            {"utc": "2026-09-21T19:15:47Z", "units": 5.0},        # sold before the (US) ex-date
            {"utc": "2026-09-25T13:31:43Z", "qty": 20.0}])        # sold after it
        led = ledger(LRCX=p)
        acts = ca.process_book("fundamental_book", led, NOW, Fetch({"LRCX": ev("LRCX", tz="America/New_York", ccy="USD",
                               divs=[{"date": "2026-09-23", "ts": 1790170200, "amount": 0.33}], closes={"2026-09-22": 311.0})}), fx)
        self.assertEqual(acts[0]["units"], 40.0)
        self.assertAlmostEqual(led["cash"], 1000.0 + 13.2, 2)

    def test_implausible_dividend_refused_not_guessed(self):
        led = ledger(X=pos8766(units=100.0, entry_utc="2026-09-20T00:00:00Z"))
        acts = ca.process_book("fundamental_book", led, NOW, Fetch({"X": ev("X", divs=[{"date": "2026-09-29", "ts": EX, "amount": 300.0}], closes={"2026-09-28": 1000.0})}), fx)
        self.assertEqual(acts[0]["type"], "dividend_refused")
        self.assertEqual(led["cash"], 1000.0)

    def test_recently_closed_position_held_into_ex_date_gets_dividend(self):
        c = pos8766(units=0.0, entry_utc="2026-08-27T12:14:49Z", exit_fills=[
            {"utc": "2026-09-28T00:19:52Z", "qty": 1674.88}, {"utc": "2026-09-29T00:33:25Z", "qty": 1674.88}])
        led = ledger()
        led["closed"] = {"6902.T": c, "OLD": pos8766(units=0.0, entry_utc="2026-08-01T00:00:00Z",
                                                        exit_fills=[{"utc": "2026-09-28T00:00:00Z", "qty": 5.0}])}
        f = Fetch({"6902.T": ev("6902.T", divs=[{"date": "2026-09-29", "ts": EX, "amount": 37.0}], closes={"2026-09-28": 1919.5})})
        acts = ca.process_book("fundamental_book", led, NOW, f, fx)
        self.assertEqual([(a["type"], a["closed_key"]) for a in acts], [("dividend", "6902.T")])
        self.assertAlmostEqual(acts[0]["units"], 1674.88)
        self.assertEqual(f.calls, ["6902.T"])                  # OLD closed before go-live: not re-opened for restatement

    def test_units_at_ex_scales_fills_before_a_split(self):
        p = {"units": 150.0, "corporate_actions": [{"type": "split", "ex_date": "2026-09-29", "ratio": 15.0}],
             "exit_fills": [{"utc": "2026-09-30T01:00:00Z", "qty": 30.0}]}
        self.assertEqual(ca.units_at_ex(p, "2026-09-29", "Asia/Tokyo"), 180.0)
        p["exit_fills"] = [{"utc": "2026-09-28T01:00:00Z", "qty": 2.0}]      # before both
        self.assertEqual(ca.units_at_ex(p, "2026-09-25", "Asia/Tokyo"), 180.0)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.led = ledger(**{"8766.T": pos8766()})
        with open(os.path.join(self.tmp, "fundamental_book.json"), "w") as f:
            json.dump(self.led, f)
        self.f = Fetch({"8766.T": ev(splits=[SPLIT15], divs=[DIV8766], start=EX)})

    def read(self, name):
        with open(os.path.join(self.tmp, name)) as f:
            return f.read()

    def test_run_applies_logs_and_schedules_next_session(self):
        s = ca.run(now=NOW, fetch=self.f, fx=fx, reports=self.tmp, lock=False, verbose=False)
        self.assertEqual(sorted(a["type"] for a in s["actions"]), ["dividend", "split"])
        led = json.loads(self.read("fundamental_book.json"))
        p = led["positions"]["8766.T"]
        self.assertAlmostEqual(p["units"], 74929.65981296528, 6)
        self.assertEqual(len(p["distributions_received"]), 1)
        log = self.read("tfo_head_trader_log.txt").splitlines()
        self.assertTrue(log[0].startswith("LOG 2026-09-29 11:15 SPLIT 8766.T ") and " CORP fundamental_book " in log[0])
        self.assertTrue(log[1].startswith("LOG 2026-09-29 11:15 DIV 8766.T ") and log[1].split()[12] == "0.00")
        self.assertEqual(len(self.read("tfo_head_trader_facts.jsonl").splitlines()), 2)
        self.assertEqual(len(self.read("corporate_actions_log.jsonl").splitlines()), 2)
        reg = json.loads(self.read("distributions.json"))
        self.assertEqual(reg["events"][0]["symbol"], "8766.T")
        st = json.loads(self.read("corporate_actions_state.json"))
        self.assertEqual(st["symbols"]["fundamental_book|8766.T"]["next_check_utc"], "2026-09-30T00:00:00Z")  # next Tokyo open
        # same session: nothing due, no refetch, no double booking
        s2 = ca.run(now=NOW + dt.timedelta(minutes=5), fetch=self.f, fx=fx, reports=self.tmp, lock=False, verbose=False)
        self.assertEqual(s2["due"], {})
        self.assertEqual(self.f.calls, ["8766.T"])

    def test_marker_request_forces_a_check_and_is_cleared(self):
        ca.run(now=NOW, fetch=self.f, fx=fx, reports=self.tmp, lock=False, verbose=False)
        import bad_tick_guard as btg
        btg.request_ca_check("fundamental_book", "8766.T", "bad tick", 15, path=os.path.join(self.tmp, "corporate_actions_requests.json"))
        s = ca.run(now=NOW + dt.timedelta(minutes=5), fetch=self.f, fx=fx, reports=self.tmp, lock=False, verbose=False)
        self.assertEqual(s["due"], {"fundamental_book": ["8766.T"]})
        self.assertEqual([a for a in s["actions"] if a["type"] in ("split", "dividend")], [])   # already applied
        self.assertEqual(json.loads(self.read("corporate_actions_requests.json")), {})

    def test_before_open_schedules_for_todays_open(self):
        early = dt.datetime(2026, 9, 28, 23, 0, tzinfo=UTC)          # 08:00 JST, before the 09:00 open
        self.assertEqual(ca._next_check({"regular_start": EX}, early), dt.datetime.fromtimestamp(EX, UTC))

    def test_dry_run_writes_nothing(self):
        before = self.read("fundamental_book.json")
        ca.run(now=NOW, fetch=self.f, fx=fx, reports=self.tmp, lock=False, dry=True, verbose=False)
        self.assertEqual(self.read("fundamental_book.json"), before)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "tfo_head_trader_log.txt")))

    def test_precheck_never_raises(self):
        self.assertIsNone(ca.precheck(now=NOW, fetch=self.f, fx=fx, reports="/nonexistent/dir", lock=False, verbose=False) and None)


class ParseTests(unittest.TestCase):
    def test_parse_events_uses_exchange_local_dates(self):
        payload = {"chart": {"result": [{"meta": {"exchangeTimezoneName": "Asia/Tokyo", "currency": "JPY", "regularMarketPrice": 528.4,
                    "currentTradingPeriod": {"regular": {"start": EX, "end": EX + 21600}}},
                    "timestamp": [EX - 86400, EX], "indicators": {"quote": [{"close": [538.33, 528.4]}]},
                    "events": {"splits": {str(EX): {"date": EX, "numerator": 15, "denominator": 1, "splitRatio": "15:1"}},
                               "dividends": {str(EX): {"date": EX, "amount": 122.5}}}}]}}
        e = ca.parse_events("8766.T", payload, "u")
        self.assertEqual(e["splits"][0]["date"], "2026-09-29")
        self.assertEqual(e["splits"][0]["ratio"], 15.0)
        self.assertEqual(e["divs"][0]["date"], "2026-09-29")
        self.assertEqual(e["closes"]["2026-09-28"], 538.33)


class DistributionsRealisedTests(unittest.TestCase):
    def test_apply_pending_books_dividend_into_realised(self):
        import fundamental_distributions as fd
        tmp = tempfile.mkdtemp()
        old = fd.REGISTRY
        fd.REGISTRY = os.path.join(tmp, "distributions.json")
        try:
            fd.save_registry({"events": [{"symbol": "X", "ex_date": "2026-09-29", "amount_per_share": 1.0, "currency": "USD",
                                          "kind": "dividend", "source_url": "https://x", "applied_utc": None}]})
            led = {"cash": 0.0, "realised_usd": 0.0, "positions": {"X": {"side": "long", "units": 10, "entry_ccy": "USD", "mark_fx_usd": 1.0}}}
            fd.apply_pending(led, today="2026-09-29", verbose=False)
            self.assertEqual(led["cash"], 10.0)
            self.assertEqual(led["realised_usd"], 10.0)
            self.assertEqual(fd.pending(led, "2026-09-29"), [])
        finally:
            fd.REGISTRY = old



class RailHookTests(unittest.TestCase):
    def test_precheck_runs_before_scan_on_execute_only(self):
        from unittest import mock
        import tfo_ticket as tt
        order = []
        with mock.patch.object(tt, "CORP_ACTIONS_PRECHECK", True), \
             mock.patch.object(ca, "precheck", lambda **k: order.append(("precheck", k.get("reports")))), \
             mock.patch.object(tt, "scan", lambda only=None: order.append(("scan",)) or []), \
             mock.patch.object(tt, "persist_trails", lambda rows: None), \
             mock.patch.object(sys, "argv", ["tfo_ticket.py", "--execute", "--only", "ZZZ"]), \
             mock.patch("builtins.print"):
            tt.main()
        self.assertEqual(order, [("precheck", tt.R), ("scan",)])
        order.clear()
        with mock.patch.object(tt, "CORP_ACTIONS_PRECHECK", True), \
             mock.patch.object(ca, "precheck", lambda **k: order.append(("precheck",))), \
             mock.patch.object(tt, "scan", lambda only=None: order.append(("scan",)) or []), \
             mock.patch.object(sys, "argv", ["tfo_ticket.py", "--only", "ZZZ"]), mock.patch("builtins.print"):
            tt.main()
        self.assertEqual(order, [("scan",)])                         # preview never writes corporate actions


if __name__ == "__main__":
    unittest.main()
