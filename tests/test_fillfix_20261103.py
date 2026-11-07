"""fillfix_20261103 (PAPER; fixture data only - temp books, injected prices, no network, no live state).
F5a  US/FUT open-avoid window = 09:30-09:45 America/New_York after DST ended (2 Nov: DB/KO filled 09:30:04 ET, IMO/CVS/VEON 09:32:03 ET).
F5b  pre-market 5m bars never trigger or price an RTH entry (2 Nov: KO limit 94.08 filled while the RTH low was 94.28; DB filled at
     33.83 = the open of the 08:30 ET pre-market bar).
F5c  only a DATED, fresh catalyst waives the grade-B 1.5x volume check ("No fresh headline" GOOGL); stale intraday volume bars = unknown.
F5d  BRK-B is asked from Massive as BRK.B.
F5e  cancel_if acts only on a fresh regular-session print (no pre-market Massive bar, no stale Yahoo RTH close).
F6   a closed equity venue marks at its official close (pit daily bar) once that session ended after the last intraday print;
     a daily bar whose stamp precedes the close (fetched mid-session) is never used as the day's final close."""
import datetime as dt, json, os, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from veteran_desk import book as BK, b_tight as BT, intraday_store as IS, massive as MS, seat_cli as SC
U = dt.timezone.utc


def _book(pos, seat):
    tmp = tempfile.mkdtemp()
    D = dict(seats={s: BK._seat_ledger() for s in BK.TRADERS}, capital_events=[], hedge={"disabled": True},
             kill_switch={"state": "OK"}, peak_equity_aud=None, seed_aud=BK.SEED_AUD, paper_only=True)
    D["seats"][seat]["positions"][pos["id"]] = pos
    p = os.path.join(tmp, "b.json"); json.dump(D, open(p, "w")); q = os.path.join(tmp, "q.json"); json.dump({"entries": {}}, open(q, "w"))
    return p, q


def _tick(path, q, T, mark=None, live=None, bar=None):
    """One book tick on a temp book at time T. mark: injected price dict (price_fn). live/bar: patch BK.live_px + OFFICIAL_CLOSE_FN
    and tick with the default feed (price_fn=None), which is the only path that applies the official close."""
    names = ("BOOK", "now", "fallback_quote", "ATR_FN", "first_print_after_open", "firm_ok", "posture", "NEWS_FN", "BARS_FN", "live_px", "OFFICIAL_CLOSE_FN")
    old = {n: getattr(BK, n) for n in names}
    try:
        BK.BOOK = path; BK.now = lambda: T; BK.fallback_quote = lambda s: None; BK.ATR_FN = lambda s: 0.1
        BK.first_print_after_open = lambda s, t: None; BK.firm_ok = lambda: (True, ""); BK.posture = lambda: (False, "risk-off")
        BK.NEWS_FN = lambda p: None; BK.BARS_FN = lambda s, c: None
        if live is not None:
            BK.live_px = live; BK.OFFICIAL_CLOSE_FN = bar
            return BK.tick(price_fn=None, au=0.70, queue_path=q)
        return BK.tick(price_fn=lambda s: dict(mark), au=0.70, queue_path=q)
    finally:
        for n, v in old.items(): setattr(BK, n, v)


def _armed_us(tk, entry, stop, target):
    return dict(id="VET-%s-1" % tk, seat="essentials_utilities", ticker=tk, side="LONG", entry=entry, stop=stop, target=target, shares=100,
                risk_aud=round((entry - stop) * 100 / .7, 2), status="armed", order_type="limit", armed_utc="2026-11-01T22:00:00+00:00",
                venue="US", sleeve="core", scale_t1=True)


class F5aOpenWindow(unittest.TestCase):
    def test_us_open_avoid_follows_new_york_after_dst(self):
        self.assertTrue(BK.in_open_window("US", dt.datetime(2026, 11, 2, 14, 30, 4, tzinfo=U)))    # 09:30:04 EST: DB/KO fill time -> now blocked
        self.assertTrue(BK.in_open_window("US", dt.datetime(2026, 11, 2, 14, 32, 3, tzinfo=U)))    # 09:32:03 EST: IMO/CVS/VEON fill time -> blocked
        self.assertFalse(BK.in_open_window("US", dt.datetime(2026, 11, 2, 13, 35, tzinfo=U)))      # 08:35 EST (the old fixed 13:30 UTC window)
        self.assertFalse(BK.in_open_window("US", dt.datetime(2026, 11, 2, 14, 45, tzinfo=U)))      # 09:45 EST: window over
        self.assertTrue(BK.in_open_window("US", dt.datetime(2026, 10, 30, 13, 31, tzinfo=U)))      # 09:31 EDT: summer unchanged
        self.assertTrue(BK.in_open_window("FUT", dt.datetime(2026, 11, 2, 14, 31, tzinfo=U)))
        self.assertTrue(BK.in_open_window("ASX", dt.datetime(2026, 11, 2, 23, 5, tzinfo=U)))       # 10:05 AEDT

    def test_ko_not_filled_at_0930_04(self):
        path, q = _book(_armed_us("KO", 94.08, 92.0, 98.5), "essentials_utilities")
        m = dict(last=94.0, hi=94.6, lo=93.9, open=94.5, asof="2026-11-02T14:25:00+00:00", window_start="2026-11-02T14:00:00+00:00", src="massive_aggs_5m")
        D = _tick(path, q, dt.datetime(2026, 11, 2, 14, 30, 4, tzinfo=U), mark=m)
        p = D["seats"]["essentials_utilities"]["positions"]["VET-KO-1"]
        self.assertEqual(p["status"], "armed"); self.assertIn("waiting out the first", p["exec_note"])


class F5bPreMarketBars(unittest.TestCase):
    T = dt.datetime(2026, 11, 2, 14, 50, 4, tzinfo=U)   # 09:50:04 EST, past the open-avoid window

    def test_ko_premarket_low_does_not_fill(self):
        # 6-bar window starts 09:20 ET (pre-market) and holds the pre-market low 93.90; RTH prints stay above the 94.08 limit
        m = dict(last=94.35, hi=94.6, lo=93.9, open=94.5, asof="2026-11-02T14:45:00+00:00", window_start="2026-11-02T14:20:00+00:00", src="massive_aggs_5m")
        p0 = _armed_us("KO", 94.08, 92.0, 98.5)
        self.assertEqual(BK.post_extremes(m, p0["armed_utc"])[1], 93.9)          # old rule (bars since armed_utc) would have triggered
        self.assertEqual(BK.post_extremes(m, BK.entry_since(p0, self.T)), (94.35, 94.35, None))
        path, q = _book(p0, "essentials_utilities")
        p = _tick(path, q, self.T, mark=m)["seats"]["essentials_utilities"]["positions"]["VET-KO-1"]
        self.assertEqual(p["status"], "armed"); self.assertIsNone(p.get("fill"))

    def test_db_not_priced_off_premarket_open(self):
        m = dict(last=34.05, hi=34.3, lo=33.8, open=33.83, asof="2026-11-02T14:45:00+00:00", window_start="2026-11-02T14:20:00+00:00", src="massive_aggs_5m")
        path, q = _book(_armed_us("DB", 34.10, 33.2, 36.0), "essentials_utilities")
        p = _tick(path, q, self.T, mark=m)["seats"]["essentials_utilities"]["positions"]["VET-DB-1"]
        self.assertEqual(p["status"], "open")
        self.assertEqual(p["fill"], 34.10)   # at the limit, never 33.83 (the pre-market bar open)

    def test_fresh_print_after_window_is_always_rth(self):
        # massive.quote asof = bar START; a print fresh enough to fill (<= stale limit) at the end of the avoid window starts >= 09:30 ET
        self.assertGreaterEqual(BK.OPEN_AVOID_MIN, BK.stale_limit_min("US"))


class F5cCatalyst(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 3, 14, 30, tzinfo=U)

    def test_googl_no_fresh_headline_is_not_a_catalyst(self):
        for c in ("No fresh headline; breakdown below 276.4", "Q3 beat; guidance raised", "Northrop contract award", "FDA approval today (Benzinga 09:23Z)"):
            self.assertFalse(BT.has_catalyst(dict(catalyst=c), now=self.NOW), c)

    def test_dated_fresh_catalyst_counts(self):
        for c in ("'Oil prices climb after U.S. reportedly strikes Iran's Qeshm Island' (CNBC, 01:41 UTC 3 Nov)", "EW earnings AMC Thu 05 Nov 05:05 WITA",
                  "FDA approval 2026-11-02 (Benzinga)", "Nov 3: guidance cut"):
            self.assertTrue(BT.has_catalyst(dict(catalyst=c), now=self.NOW), c)
        self.assertTrue(BT.has_catalyst(dict(catalyst_date="2026-11-05"), now=self.NOW))
        self.assertTrue(BT.has_catalyst(dict(tags=dict(news_risk=[dict(title="x", published_utc="2026-11-03T01:00:00Z")])), now=self.NOW))

    def test_stale_or_far_dates_do_not_count(self):
        for c in ("contract award 10 Oct", "headline 25 Oct 03:00Z", "earnings December 1st", "No dated EURCAD catalyst; supporting headline 02 Nov 22:44Z"):
            self.assertFalse(BT.has_catalyst(dict(catalyst=c), now=self.NOW), c)
        self.assertEqual(BT.catalyst_dates("FXEmpire 02 Nov 22:44Z", self.NOW), [dt.date(2026, 11, 2)])   # 22:44 is a time, not 22 Nov

    def test_stale_volume_bars_are_unknown(self):
        now_s = self.NOW.timestamp()
        self.assertIn("min old", BT._stale_vols(now_s - 17 * 3600, now_s))   # yesterday's closing bars (the GOOGL 7.8x pre-open rvol)
        self.assertIsNone(BT._stale_vols(now_s - 25 * 60, now_s))             # 20-min delayed venue + bar
        rows = [(int(now_s) - 17 * 3600 - 300 * (30 - i), 1, 1, 1, 1, 100.0 if i < 29 else 780.0) for i in range(30)]
        from veteran_desk import data_layer as DL
        env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
        BT._VCACHE.pop("ZZFILL", None)
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(DL, "intraday", lambda *a, **k: rows):
            self.assertIsNone(BT._intraday_vols("ZZFILL"))
            r = BT.fill_confirm(dict(grade="B", ticker="ZZFILL", catalyst="No fresh headline"))
        BT._VCACHE.pop("ZZFILL", None)
        self.assertIn("b_confirm_wait", r); self.assertIn("min old", r)


class F5dBrkB(unittest.TestCase):
    def test_store_and_quote_ask_massive_for_brk_dot_b(self):
        seen = []
        with mock.patch.object(MS, "_call", lambda kind, path, q, *a, **k: seen.append(path) or {"results": []}):
            IS._massive_rows("BRK-B", 5, "minute", None)
        self.assertIn("/v2/aggs/ticker/BRK.B/range/5/minute/", seen[0])
        got = []
        MS.quote("BRK-B", get=lambda path, q: (got.append(path), (200, {"results": []}))[1], now_utc=dt.datetime(2026, 11, 3, 15, 0, tzinfo=U))
        self.assertIn("/v2/aggs/ticker/BRK.B/", got[0])


class F5eCancelIf(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); bk = os.path.join(self.tmp, "book.json")
        json.dump(dict(seats=dict(tech_ai_infra=dict(positions=dict(G1=dict(id="G1", ticker="GOOGL", side="SHORT", status="armed", grade="B", entry=276.4, stop=287.5, venue="US")), closed=[]))), open(bk, "w"))
        self.calls = []
        self.p = [mock.patch.object(BK, "BOOK", bk), mock.patch.object(BK, "load", lambda *a, **k: json.load(open(bk))),
                  mock.patch.object(SC, "withdraw", lambda seat, pid, reason: (self.calls.append((seat, pid, reason)), dict(ok=True, id=pid))[1])]
        for x in self.p: x.start()
        self.assertTrue(SC.head_cancel_if("head_of_desk", "tech_ai_infra", "G1", "below", 275.0, "pre-market test")["ok"])

    def tearDown(self):
        for x in self.p: x.stop()

    def test_premarket_print_does_not_cancel(self):
        w = SC.head_watch(price_fn=lambda s: dict(last=274.75, asof="2026-11-03 13:25:00+00:00"), now=dt.datetime(2026, 11, 3, 13, 31, tzinfo=U))   # 08:25 EST print
        self.assertIn("session closed", w["checked"]["G1"]); self.assertEqual(self.calls, [])

    def test_premarket_bar_after_open_and_stale_close_do_not_cancel(self):
        t = dt.datetime(2026, 11, 3, 14, 31, tzinfo=U)   # 09:31 EST
        w = SC.head_watch(price_fn=lambda s: dict(last=274.75, asof="2026-11-03 14:25:00+00:00"), now=t)   # 09:25 pre-market bar
        self.assertIn("outside the US regular session", w["checked"]["G1"])
        w = SC.head_watch(price_fn=lambda s: dict(last=274.0, asof="2026-11-02 15:55:00-05:00"), now=dt.datetime(2026, 11, 3, 15, 0, tzinfo=U))   # stale Yahoo RTH close
        self.assertIn("min old", w["checked"]["G1"]); self.assertEqual(self.calls, [])

    def test_fresh_rth_print_cancels(self):
        w = SC.head_watch(price_fn=lambda s: dict(last=274.9, asof="2026-11-03 14:50:00+00:00"), now=dt.datetime(2026, 11, 3, 14, 56, tzinfo=U))
        self.assertTrue(w["checked"]["G1"]["ok"]); self.assertEqual(self.calls[0][1], "G1")


KO_OPEN = dict(id="VET-KO-OPEN-1", seat="essentials_utilities", ticker="KO", side="LONG", venue="US", status="open", entry=94.08, fill=94.08,
               filled_utc="2026-11-02T14:30:04+00:00", armed_utc="2026-10-31T11:16:51+00:00", stop=92.0, initial_stop=92.0, target=94.80, shares=948,
               risk_aud=2816.0, r0=2.08, cost_aud=60.0, fill_notional_aud=127408.0, fill_audusd=0.70, order_type="limit", order="limit", off_hours_checked=True,
               rail_state="initial", rail_hwp=94.5, peak_fav_R=0.2, scale_t1=True, sleeve="core", grade="A", cost_rt_rate=0.001, adv_aud=2.0e9, adv_day="2026-11-03",
               atr14=1.2, atr14_day="2026-11-03", time_stop_deadline_utc="2026-11-11T23:59:59+00:00", cm_rebaseline=dict(utc="2026-11-02T15:00:00+00:00", at_px=94.08, delta_R=0.0))


class F5bExitsOnPreOpenPrint(unittest.TestCase):
    """3 Nov 09:30:04 ET the book half-banked KO 474 sh at 94.91 = close of the 09:25 ET PRE-MARKET bar (first RTH bar opened 94.88)."""
    T1 = dt.datetime(2026, 11, 3, 14, 30, 4, tzinfo=U)   # 09:30:04 EST, first RTH tick
    PRE = dict(last=94.91, hi=95.05, lo=94.75, open=94.91, asof="2026-11-03 14:25:00+00:00", window_start="2026-11-03T14:00:00+00:00", src="massive_aggs_5m")

    def test_preopen_print_detection(self):
        self.assertTrue(BK._us_preopen_print(self.PRE, self.T1))
        self.assertFalse(BK._us_preopen_print(dict(self.PRE, asof="2026-11-03 14:30:00+00:00"), dt.datetime(2026, 11, 3, 14, 36, tzinfo=U)))
        self.assertFalse(BK._us_preopen_print(self.PRE, dt.datetime(2026, 11, 3, 14, 26, tzinfo=U)))   # 09:26 ET: session closed, normal closed rules
        self.assertFalse(BK._us_preopen_print(dict(last=1.0), self.T1))                                # no asof (legacy fake) -> unchanged
        self.assertEqual(BK.trigger_hold(dict(venue="US", mark_preopen="x", px_age_min=5.1), self.T1), "premarket_print")
        self.assertTrue(BK._session_closed_for_exit(dict(venue="US", ticker="KO", mark_preopen="x"), self.T1))

    def test_target_hit_on_premarket_print_is_queued_not_booked(self):
        path, q = _book(dict(KO_OPEN), "essentials_utilities")
        D = _tick(path, q, self.T1, mark=self.PRE)   # pre-market hi 95.05 >= target 94.80
        p = D["seats"]["essentials_utilities"]["positions"]["VET-KO-OPEN-1"]
        self.assertEqual(p["shares"], 948); self.assertEqual(D["seats"]["essentials_utilities"]["closed"], [])
        self.assertTrue(p.get("exit_queued")); self.assertEqual(p.get("mark_preopen"), "2026-11-03 14:25:00+00:00")
        rth = dict(last=94.75, hi=95.07, lo=94.655, open=94.88, asof="2026-11-03 14:30:00+00:00", window_start="2026-11-03T14:05:00+00:00", src="massive_aggs_5m")
        D = _tick(path, q, dt.datetime(2026, 11, 3, 14, 36, 4, tzinfo=U), mark=rth)
        S = D["seats"]["essentials_utilities"]; p = S["positions"]["VET-KO-OPEN-1"]
        self.assertNotIn("mark_preopen", p); self.assertEqual(p["shares"], 474)
        self.assertEqual([c.get("exit") for c in S["closed"]], [94.75])   # booked at the first RTH print, never at the 94.91 pre-market close


BGA = dict(id="VET-BGAAX-20261031-111312-essentials_utilities", seat="essentials_utilities", ticker="BGA.AX", side="LONG", venue="ASX", status="open",
           entry=6.29, fill=6.29, filled_utc="2026-11-02T00:26:01+00:00", armed_utc="2026-10-31T11:16:53+00:00", stop=6.117679, initial_stop=6.117679,
           target=6.634642, shares=29015, risk_aud=4999.89, r0=0.172321, cost_aud=219.01, fill_notional_aud=182504.35, fill_audusd=0.7033, order_type="stop_limit",
           order="stop_limit", off_hours_checked=True, rail_state="initial", rail_hwp=6.31, peak_fav_R=0.1161, scale_t1=True, sleeve="core", grade="A",
           cost_rt_rate=0.004, adv_aud=3649119.0, adv_day="2026-11-03", atr14=0.1257, atr14_day="2026-11-03", time_stop_deadline_utc="2026-11-11T23:59:59+00:00",
           cm_rebaseline=dict(utc="2026-11-02T00:28:05+00:00", at_px=6.29, delta_R=-0.0365), mark=6.28)
BGA_BAR = ("2026-11-03", 6.28, "2026-11-03T06:40:00+00:00", "eodhd_bulk_sgp")   # EODHD EOD 3 Nov close (auction 16:10 AEDT, 238,540 sh)


def _yahoo_preauction(s):   # last continuous 5m bar 16:00 AEDT (05:00 UTC), before the closing single price auction
    return dict(last=6.27, hi=6.28, lo=6.26, open=6.27, asof="2026-11-03 16:00:00+11:00", window_start="2026-11-03T04:35:00+00:00")


class F6ClosedMarketMark(unittest.TestCase):
    T = dt.datetime(2026, 11, 3, 13, 42, 3, tzinfo=U)   # 21:42 WITA, ASX closed

    def test_asx_after_close_marks_at_official_close(self):
        m = BK.official_close_mark("BGA.AX", _yahoo_preauction("BGA.AX"), self.T, bar_fn=lambda s, t: BGA_BAR)
        self.assertEqual(m["last"], 6.28); self.assertEqual((m["hi"], m["lo"]), (6.28, 6.28))
        self.assertTrue(m["src"].startswith("official_close ASX 2026-11-03")); self.assertEqual(m["asof"], "2026-11-03T05:12:00+00:00")
        self.assertEqual(m["intraday_last"], 6.27)

    def test_tick_marks_open_asx_position_at_official_close(self):
        path, q = _book(dict(BGA), "essentials_utilities")
        D = _tick(path, q, self.T, live=_yahoo_preauction, bar=lambda s, t: BGA_BAR)
        p = D["seats"]["essentials_utilities"]["positions"][BGA["id"]]
        self.assertEqual(p["status"], "open"); self.assertEqual(p["mark"], 6.28)
        self.assertTrue(p["mark_src"].startswith("official_close ASX 2026-11-03"))
        self.assertEqual(D["seats"]["essentials_utilities"]["closed"], [])

    def test_injected_feed_is_untouched(self):   # tests/sims that pass price_fn keep their prices
        path, q = _book(dict(BGA), "essentials_utilities")
        old = BK.OFFICIAL_CLOSE_FN
        try:
            BK.OFFICIAL_CLOSE_FN = lambda s, t: BGA_BAR
            D = _tick(path, q, self.T, mark=_yahoo_preauction("BGA.AX"))
        finally: BK.OFFICIAL_CLOSE_FN = old
        self.assertEqual(D["seats"]["essentials_utilities"]["positions"][BGA["id"]]["mark"], 6.27)

    def test_mid_session_daily_bar_is_not_the_final_close(self):
        q = _yahoo_preauction("BGA.AX")
        during = dt.datetime(2026, 11, 3, 3, 0, tzinfo=U)   # 14:00 AEDT, session open: a same-day daily bar is in progress
        self.assertIs(BK.official_close_mark("BGA.AX", q, during, bar_fn=lambda s, t: BGA_BAR), q)
        early = ("2026-11-03", 6.24, "2026-11-03T04:45:00+00:00", "eodhd_rt_midsession")   # stamped 15:45 AEDT, before the 16:12 close
        self.assertIs(BK.official_close_mark("BGA.AX", q, self.T, bar_fn=lambda s, t: early), q)
        veon = dict(last=79.49, asof="2026-11-02 15:55:00-05:00")   # US pit rows are stamped 20:30 UTC = 15:30 EST in winter -> not trusted
        self.assertIs(BK.official_close_mark("VEON", veon, self.T, bar_fn=lambda s, t: ("2026-11-02", 79.46, "2026-11-02T20:30:00+00:00", "eodhd_bulk_sgp")), veon)
        self.assertEqual(BK.official_close_mark("VEON", veon, self.T, bar_fn=lambda s, t: ("2026-11-02", 79.46, "2026-11-02T21:30:00+00:00", "eodhd_bulk_sgp"))["last"], 79.46)

    def test_newer_prints_and_other_markets_unchanged(self):
        post = dict(last=94.9, asof="2026-11-02 21:05:00+00:00", src="massive_aggs_5m")   # post-market bar after the 16:00 EST close
        self.assertIs(BK.official_close_mark("KO", post, self.T, bar_fn=lambda s, t: ("2026-11-02", 95.83, "2026-11-02T21:30:00+00:00", "x")), post)
        fx = dict(last=1.55, asof=self.T.isoformat())
        self.assertIs(BK.official_close_mark("EURCAD=X", fx, self.T, bar_fn=lambda s, t: ("2026-11-02", 1.5, "2026-11-02T21:30:00+00:00", "x")), fx)
        pence = dict(last=1263.0, asof="2026-11-03 16:20:00+00:00")
        self.assertIs(BK.official_close_mark("KNOS.L", pence, self.T, bar_fn=lambda s, t: ("2026-11-03", 12.70, "2026-11-03T17:05:00+00:00", "x")), pence)
        self.assertIsNone(BK.official_close_mark("BGA.AX", None, self.T, bar_fn=lambda s, t: BGA_BAR))


if __name__ == "__main__":
    unittest.main()
