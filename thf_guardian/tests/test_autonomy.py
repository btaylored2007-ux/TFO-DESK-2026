"""Block every LLM endpoint. The book tick still fills and stops out, and the guardian still heals.

PAPER. Uses a temp book. Does not import a broker.
"""
import datetime as dt, json, os, tempfile, unittest
import urllib.request

U = dt.timezone.utc
# Monday 2 Nov 2026 15:00 UTC = 10:00 America/New_York (EST), inside the cash session, past the open window.
T = dt.datetime(2026, 11, 2, 15, 0, tzinfo=U)


class Block:
    def __init__(self):
        self.urls = []
        self.cmds = []

    def urlopen(self, req, *a, **k):
        url = getattr(req, "full_url", None) or str(req)
        self.urls.append(url)
        raise OSError("LLM and network blocked: " + url)

    def run(self, cmd, **k):
        self.cmds.append(list(cmd))
        text = " ".join(str(c) for c in cmd).lower()
        if any(s in text for s in ("claude", "grok", "codex", "openai", "anthropic", "cursor-agent")):
            raise OSError("LLM command blocked")
        class R:
            returncode = 0
            stdout = "active\n"
            stderr = ""
        return R()


def _arm():
    return dict(id="A1", seat="energy", ticker="XOM", side="LONG", entry=100, stop=90, target=130, shares=10,
                risk_aud=100.0, status="armed", venue="US", order_type="limit", grade="A", sleeve="core",
                armed_utc=(T - dt.timedelta(hours=1)).isoformat(), scale_t1=True)


def _open():
    return dict(id="P1", seat="energy", ticker="CVX", side="LONG", entry=100, fill=100, stop=98, target=120, shares=10,
                risk_aud=20.0, status="open", venue="US", grade="A", sleeve="core", cost_aud=1.0,
                filled_utc=(T - dt.timedelta(hours=2)).isoformat(), off_hours_checked=True, scale_t1=True)


class Autonomy(unittest.TestCase):
    def test_tick_fill_stop_and_heal_with_llm_blocked(self):
        from veteran_desk import book as BK
        tmp = tempfile.mkdtemp()
        D = dict(book="thf", paper_only=True, label="test", seed_aud=BK.SEED_AUD, capital_events=[],
                 hedge={"disabled": True}, kill_switch={"state": "OK"}, peak_equity_aud=BK.SEED_AUD,
                 day_start_equity_aud=BK.SEED_AUD, seats={s: BK._seat_ledger() for s in BK.TRADERS})
        D["seats"]["energy"]["positions"] = {"A1": _arm(), "P1": _open()}
        D["seats"]["energy"]["cash_aud"] = BK.SEAT_SEED
        path = os.path.join(tmp, "book.json")
        json.dump(D, open(path, "w"))
        q = os.path.join(tmp, "q.json")
        json.dump({"entries": {}}, open(q, "w"))
        block = Block()
        old_url = urllib.request.urlopen
        old = (BK.BOOK, BK.now, BK.fallback_quote, BK.firm_ok, BK.posture, BK.in_open_window, BK.KILL_ALERT_PATH, BK.HEDGE_ARCHIVE)
        urllib.request.urlopen = block.urlopen
        try:
            BK.BOOK = path
            BK.now = lambda: T
            BK.fallback_quote = lambda s: None
            BK.firm_ok = lambda: (True, "OK")
            BK.posture = lambda: ("calm", "risk-off")
            BK.in_open_window = lambda v, t: False
            BK.KILL_ALERT_PATH = os.path.join(tmp, "KILL.json")
            BK.HEDGE_ARCHIVE = os.path.join(tmp, "hedge.json")
            if hasattr(BK, "ATR_FN"):
                BK.ATR_FN = lambda s: 1.0
            if hasattr(BK, "BARS_FN"):
                BK.BARS_FN = lambda s, c: None
            if hasattr(BK, "NEWS_FN"):
                BK.NEWS_FN = lambda p: None

            def px(sym):
                if sym == "XOM":
                    return dict(last=100.0, hi=101.0, lo=99.0, open=100.0, asof=T.isoformat())
                if sym == "CVX":
                    return dict(last=97.0, hi=99.0, lo=96.0, open=99.0, asof=T.isoformat())
                return dict(last=1.0, hi=1.0, lo=1.0, asof=T.isoformat())

            out = BK.tick(price_fn=px, au=0.70, queue_path=q)
        finally:
            urllib.request.urlopen = old_url
            BK.BOOK, BK.now, BK.fallback_quote, BK.firm_ok, BK.posture, BK.in_open_window, BK.KILL_ALERT_PATH, BK.HEDGE_ARCHIVE = old
        energy = out["seats"]["energy"]
        filled = energy["positions"].get("A1")
        self.assertIsNotNone(filled, energy.get("closed"))
        self.assertEqual(filled["status"], "open")
        self.assertEqual(filled["fill"], 100)
        self.assertEqual(filled["stop"], 90)
        self.assertEqual(filled["shares"], 10)
        closed = [c for c in energy["closed"] if c.get("id") == "P1"]
        self.assertTrue(closed, energy["positions"].keys())
        self.assertEqual(closed[-1].get("exit_reason"), "stop")
        self.assertEqual(block.urls, [])  # the tick did not call a model or the network

        from thf_guardian.config import LONG_UNITS, TIMERS, config_for
        from thf_guardian.guardian import run_once
        cfg = config_for(os.path.join(tmp, "g"))
        units = {u: "active" for u in list(LONG_UNITS.values())}
        units["tfo-thf-engine.service"] = "failed"
        timers = {u: "active" for u in list(TIMERS.values()) + ["tfo-thf-guardian.timer", "tfo-thf-guardian-check.timer"]}
        pages = {p: 200 for p in ("/", "/desk/phone", "/healthz", "/api/state", "/api/ticker")}
        feeds = {fid: {"age_sec": 5, "present": True, "backup_present": True, "backup_age_sec": 5}
                 for fid in ("ws_tape", "news", "borrow", "kraken", "yahoo_fallback", "announcements")}
        calls = []
        io = dict(systemctl=lambda how, unit: calls.append((how, unit)),
                  clear_lock=lambda p: None, write_queue=lambda q: None, fill=lambda a: None, run=None, min_free=0)
        snap = dict(now=T, units=units, timers=timers, book=out, book_age_sec=10, feed_ok_age_sec=10, feed_ok_sec=300,
                    sgp_primary=True, engine_kill=False, mac_lease=False, journal_errors=[], pages=pages,
                    kill_readable=True, feeds=feeds, locks=[], approved={}, queue={"entries": {}}, gate=None, stuck=[],
                    guardian_status_age_sec=5, brains={}, mark_rows=[], clock_stamps=[])
        status = run_once(cfg, snap, io)
        self.assertTrue(any(c[0] == "restart" and "engine" in c[1] for c in calls))
        self.assertTrue(status["paper_only"])
        self.assertFalse(any("grok" in u or "claude" in u for u in block.urls))
        # a brain probe that uses the blocked urlopen degrades the seat and does not raise into the tick
        from thf_guardian import ai_mode
        modes = ai_mode.blank_modes(T)
        try:
            block.urlopen("http://100.120.8.11:8790/grok")
            ok = True
        except OSError:
            ok = False
        modes = ai_mode.note_event(modes, "crypto", "call", ok, T, "bridge blocked")
        self.assertEqual(modes["crypto"]["mode"], "degraded")
        kept, blocked = ai_mode.filter_ai_arms([dict(seat="crypto", proposal=dict(ticker="BTC", grade="A")),
                                                dict(seat="energy", proposal=dict(ticker="XOM", grade="A"))], modes)
        self.assertEqual([r["seat"] for r in kept], ["energy"])
        self.assertEqual(blocked[0]["seat"], "crypto")


if __name__ == "__main__":
    unittest.main()
