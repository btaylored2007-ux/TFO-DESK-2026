"""Guardian checks, heal, feeds, brains, caps, signing, takeover. PAPER. No live paths."""
import datetime as dt, json, os, tempfile, unittest
from thf_guardian import ai_mode, caps, fill_paper, gradewake, signing, takeover
from thf_guardian.actions import parse
from thf_guardian.autoclear import judge
from thf_guardian.config import APPROVED_GATE, LONG_UNITS, TIMERS, config_for
from thf_guardian.gaps import flag_stale, gap_on_first_fresh
from thf_guardian.guardian import run_once
from thf_guardian.heal import decide
from thf_guardian.inbox import handle_message
from thf_guardian.lag import compare_gate, diagnose as diagnose_lag, rerank
from thf_guardian.feeds import diagnose as diagnose_feeds
from thf_guardian.push import dispatch_ai, plan
from thf_guardian.responder import answer
from thf_guardian.runtime import diagnose_drift, diagnose_locks
from thf_guardian.seats import resolve_seats
from thf_guardian.stuck import assess
from thf_guardian.tickwatch import book_integrity, prepare_tick_restart, watch_guardian
from thf_guardian.util import StateError, atomic_write, read_state, sha256_file

U = dt.timezone.utc
NOW = dt.datetime(2026, 11, 2, 7, 0, tzinfo=U)  # 15:00 WITA Monday


def _book():
    return {"paper_only": True, "seats": {"energy": {"cash_aud": 500000, "positions": {}, "closed": []}}}


def _snap(cfg):
    units = {u: "active" for u in list(LONG_UNITS.values()) + ["tfo-thf-queue-answerer.service"]}
    timers = {u: "active" for u in list(TIMERS.values()) + ["tfo-thf-guardian.timer", "tfo-thf-guardian-check.timer"]}
    pages = {p: 200 for p in ("/", "/desk/phone", "/healthz", "/api/state", "/api/ticker")}
    feeds = {fid: {"age_sec": 5, "present": True, "backup_age_sec": 5, "backup_present": True}
             for fid in ("ws_tape", "news", "borrow", "kraken", "yahoo_fallback", "announcements")}
    return dict(now=NOW, units=units, timers=timers, book=_book(), book_age_sec=30, book_fresh_sec=420,
                feed_ok_age_sec=20, feed_ok_path="/tmp/feed", feed_ok_sec=300, sgp_primary=True,
                engine_kill=False, mac_lease=False, journal_errors=[], pages=pages, kill_readable=True,
                feeds=feeds, locks=[], approved={}, queue={"entries": {}}, gate=None, stuck=[],
                guardian_status_age_sec=10, brains={}, mark_rows=[], fresh_after_stale=[], clock_stamps=[])


def _io():
    calls = []
    return dict(systemctl=lambda how, unit: calls.append((how, unit)), clear_lock=lambda p: calls.append(("lock", p)),
                write_queue=lambda q: calls.append(("queue", q)), fill=lambda a: calls.append(("fill", a.get("id"))),
                calls=calls, run=None, min_free=0)


class Heal(unittest.TestCase):
    def test_backoff_then_escalate(self):
        self.assertEqual(decide([], 1000)[0], "restart")
        self.assertEqual(decide([1000], 1030)[0], "wait")
        self.assertEqual(decide([1000], 1070)[0], "restart")
        self.assertEqual(decide([1000, 1200, 1500], 2000)[0], "escalate")

    def test_run_does_not_edit_the_book(self):
        root = tempfile.mkdtemp()
        cfg = config_for(root)
        book = _book()
        book["seats"]["energy"]["positions"]["A"] = dict(status="armed", grade="A", entry=1, stop=0.9, shares=3, ticker="XOM")
        snap = _snap(cfg)
        snap["book"] = book
        raw = json.dumps(book, sort_keys=True)
        io = _io()
        # make the engine look dead so a restart is planned, and prove the book bytes we passed are not rewritten
        snap["units"]["tfo-thf-engine.service"] = "failed"
        run_once(cfg, snap, io)
        self.assertIn(("restart", "tfo-thf-engine.service"), io["calls"])
        self.assertEqual(json.dumps(book, sort_keys=True), raw)
        self.assertFalse(any(c[0] == "fill" for c in io["calls"]))


class StuckAndFeeds(unittest.TestCase):
    def test_asx_stale_block_is_stuck_and_young_order_is_not(self):
        pos = dict(status="armed", side="LONG", entry=10, stop=9, shares=5, grade="A", venue="ASX", order_type="limit",
                   armed_utc=(NOW - dt.timedelta(minutes=10)).isoformat(),
                   exec_note="price 22 min old (> 15 = venue delay 0 + 5) - no fill on a stale price, order still working")
        hit = assess(pos, {"last": 9.5, "hi": 10.2, "lo": 9.4, "session_open": True}, NOW, 300)
        self.assertTrue(hit["stuck"])
        self.assertEqual(hit["reason"], "stale_price_delayed_venue")
        young = dict(pos, armed_utc=(NOW - dt.timedelta(minutes=2)).isoformat())
        self.assertFalse(assess(young, {"last": 9.5, "lo": 9.4, "hi": 10}, NOW)["stuck"])
        closed = dict(pos, venue="US", exec_note="venue session closed - no fill")
        self.assertFalse(assess(closed, {"last": 9, "lo": 9, "hi": 11, "session_open": False}, NOW)["stuck"])

    def test_rerank_keeps_risk_fields_and_gate_mismatch_is_not_rewritten(self):
        today = "2026-11-02T00:30:00+00:00"  # 08:30 WITA same calendar day as NOW+8? NOW is 07:00 UTC = 15:00 WITA 2 Nov. 00:30 UTC is 08:30 WITA same day.
        stale = "2026-10-30T00:00:00+00:00"
        entries = {
            "old": {"id": "old", "state": "cleared", "queued_utc": stale, "risk_aud": 100, "stop": 9, "grade": "A", "ticker": "OLD"},
            "new": {"id": "new", "state": "cleared", "queued_utc": today, "risk_aud": 50, "stop": 8, "grade": "A", "ticker": "NEW"},
        }
        ordered, moved = rerank(entries, NOW)
        self.assertEqual(list(ordered)[0], "new")
        self.assertEqual(ordered["new"]["risk_aud"], 50)
        self.assertEqual(ordered["old"]["stop"], 9)
        self.assertIn("new", moved)
        bad = compare_gate(dict(APPROVED_GATE, rr_min=1.0))
        self.assertTrue(any(m["key"] == "rr_min" for m in bad))
        # diagnose must not include a rewrite action
        out = diagnose_lag(dict(queue={"entries": entries}, gate=dict(APPROVED_GATE, rr_min=1.0), stuck=[], book_age_sec=10, tick_paused=True), NOW)
        self.assertTrue(any(i["kind"] == "wrong_gate_thresholds" for i in out["incidents"]))
        self.assertFalse(any(a["kind"] == "rewrite_gate" for a in out["actions"]))

    def test_auto_clear_halts(self):
        allow, until, _n = judge([1, 2, 3], 10, window=100, max_n=3, halted_until=None)
        self.assertFalse(allow)
        self.assertIsNotNone(until)
        allow2, until2, _ = judge([1, 2, 3], 10, window=100, max_n=3, halted_until=until)
        self.assertFalse(allow2)


class Brains(unittest.TestCase):
    def test_timeout_ping_and_two_real_calls(self):
        modes = ai_mode.blank_modes(NOW)
        modes = ai_mode.note_event(modes, "crypto", "call", False, NOW, "timeout")
        self.assertEqual(modes["crypto"]["mode"], "degraded")
        modes = ai_mode.note_event(modes, "crypto", "ping", True, NOW, "pong")
        self.assertEqual(modes["crypto"]["mode"], "degraded")
        modes = ai_mode.note_event(modes, "crypto", "call", True, NOW, "ok")
        self.assertEqual(modes["crypto"]["mode"], "degraded")
        self.assertEqual(modes["crypto"]["real_successes"], 1)
        modes = ai_mode.note_event(modes, "crypto", "call", True, NOW, "ok")
        self.assertEqual(modes["crypto"]["mode"], "ai")
        # a later timeout drops only that seat
        modes = ai_mode.note_event(modes, "energy", "call", True, NOW, "ok")
        modes = ai_mode.note_event(modes, "crypto", "call", False, NOW, "timeout")
        self.assertEqual(modes["crypto"]["mode"], "degraded")
        self.assertEqual(modes["energy"]["mode"], "ai")

    def test_code_grade_first_and_late_brain_is_dropped(self):
        rows = [dict(sym="BHP.AX", score=90, nz=3, r_fit=0.9, floors_failed=[], sector="materials", market="ASX", side="LONG")]
        late = gradewake.merge_wake(rows, [dict(ticker="BHP.AX", side="LONG", grade="A+", entry=1, stop=0.9, target=2)], False)
        self.assertEqual(late["brain"], "late")
        self.assertEqual(late["rows"][0]["grade"], "A")
        fast = gradewake.merge_wake(rows, [dict(ticker="BHP.AX", side="LONG", grade="A+", entry=1, stop=0.9, target=2, shares=1)],
                                    True, universe=["BHP.AX"], marks={"BHP.AX": 1.0}, max_notional=100)
        self.assertEqual(fast["rows"][0]["grade"], "A")  # brain cannot lift A to A+
        bad = gradewake.merge_wake(rows, [dict(ticker="NOPE", side="LONG", grade="A", entry=1, stop=0.9, target=2)],
                                   True, universe=["BHP.AX"])
        self.assertTrue(bad["rejected"])
        self.assertEqual(bad["rows"][0]["source"], "code")

    def test_degraded_half_size_and_firm_pause(self):
        modes = ai_mode.blank_modes(NOW)
        for seat in ("crypto", "energy", "trend", "macro", "banks"):
            modes = ai_mode.note_event(modes, seat, "call", False, NOW, "timeout")
        paused, n = caps.firm_new_arms_paused(modes)
        self.assertTrue(paused)
        self.assertGreaterEqual(n, 5)
        book = _book()
        # one degraded seat, A+ at 4% -> half 2% is above the floor
        modes = ai_mode.note_event(ai_mode.blank_modes(NOW), "energy", "call", False, NOW, "timeout")
        b = caps.screen_arm(dict(seat="energy", ticker="XOM", side="LONG", grade="B", venue="US", sector="energy"), book, modes)
        self.assertFalse(b["ok"])
        a = caps.screen_arm(dict(seat="energy", ticker="XOM", side="LONG", grade="A+", venue="US", sector="energy"), book, modes, base_pct=0.04)
        self.assertTrue(a["ok"])
        self.assertEqual(a["size_mult"], 0.5)
        self.assertAlmostEqual(a["risk_pct"], 0.02)
        tiny = caps.screen_arm(dict(seat="energy", ticker="XOM", side="LONG", grade="A", venue="US", sector="energy"), book, modes, base_pct=0.01)
        self.assertFalse(tiny["ok"])
        self.assertTrue(any("not lifted" in r for r in tiny["reasons"]))

    def test_feed_failover_restarts_primary(self):
        snap = {"feeds": {"ws_tape": {"age_sec": 900, "present": True, "backup_present": True, "backup_age_sec": 10},
                          "news": {"age_sec": 10, "present": True}, "borrow": {"age_sec": 10, "present": True},
                          "kraken": {"age_sec": 10, "present": True}, "yahoo_fallback": {"age_sec": 10, "present": True},
                          "announcements": {"present": False}}}
        out = diagnose_feeds(snap, NOW.timestamp())
        tape = next(r for r in out["feeds"] if r["id"] == "ws_tape")
        self.assertEqual(tape["state"], "failover")
        self.assertEqual(tape["active_source"], "yahoo_fallback")
        self.assertTrue(any(a["unit"] == "tfo-thf-wstape.service" and a["backfill"] for a in out["actions"]))
        ann = next(r for r in out["feeds"] if r["id"] == "announcements")
        self.assertEqual(ann["state"], "not_wired")

    def test_degraded_push_does_not_dispatch_the_brain(self):
        modes = ai_mode.note_event(ai_mode.blank_modes(NOW), "energy", "call", False, NOW, "timeout")
        modes = ai_mode.note_event(modes, "fx_commodities", "call", True, NOW, "ok")
        packets = {
            "energy": {"rows": [dict(sym="XOM", score=95, nz=3, r_fit=0.95, floors_failed=[], sector="energy", market="US", side="LONG")],
                       "brain": [dict(ticker="XOM", side="LONG", grade="A+", entry=10, stop=9, target=14)],
                       "brain_sec": 40, "universe": ["XOM"], "base_pct": 0.04},
            "fx_commodities": {"rows": [], "brain": None, "brain_sec": None},
        }
        result = plan(["energy", "fx_commodities"], modes, packets, _book(), NOW)
        self.assertNotIn("energy", result["ai_dispatch"])
        self.assertIn("fx_commodities", result["ai_dispatch"])
        energy = next(r for r in result["reports"] if r["seat"] == "energy")
        self.assertEqual(energy["brain"], "late")
        self.assertEqual(energy["graded"][0]["grade"], "A+")
        spawned = []
        cmd = dispatch_ai(result["ai_dispatch"], config_for(tempfile.mkdtemp()), popen=lambda *a, **k: spawned.append(a[0]))
        self.assertIn("veteran_desk.desk", cmd)
        self.assertNotIn("energy", cmd[-1])
        self.assertIn("fx_commodities", cmd[-1])

    def test_push_names(self):
        seats, _ = resolve_seats("push Lena and Dex on gold")
        self.assertIn("fx_commodities", seats)
        self.assertIn("trend", seats)
        self.assertIn("price_action", seats)


class Actions(unittest.TestCase):
    def test_whitelist_and_bridge_down(self):
        self.assertEqual(parse("restart the engine")["unit"], "tfo-thf-engine.service")
        self.assertEqual(parse("restart tfo-evil")["kind"], "reject")
        self.assertEqual(parse("please wire five million")["kind"], "queue")
        self.assertTrue(parse("flatten the book")["kind"] == "flatten")
        snap = dict(book=_book(), checks=[dict(name="book_fresh", ok=True, detail="fresh")], brains={})
        ans = answer("is everything ok?", snap, bridge=lambda *a: (_ for _ in ()).throw(ConnectionError("down")))
        self.assertEqual(ans["bridge"], "down")
        self.assertIn("PAPER", ans["answer"])
        self.assertNotIn("All quiet from Grok", ans["answer"])

    def test_confirm_restart_and_stale_screen(self):
        root = tempfile.mkdtemp()
        cfg = config_for(root)
        secret = "test-secret"
        used = set()
        snap = dict(book=_book(), checks=[], brains={}, screen_rev=3)
        calls = []
        io = dict(systemctl=lambda how, unit: calls.append((how, unit)), head=lambda t: calls.append(("head", t)),
                  health=lambda: {"ok": True}, fill=lambda i: None, push=lambda s: {}, write_book=lambda b: None)
        first = handle_message(cfg, snap, "restart the engine", NOW, secret, used, io)
        self.assertTrue(first["needs_confirm"])
        token = first["token"]
        token["body"]["screen_rev"] = 1
        # re-sign stale body
        token = signing.sign_action(secret, token["body"])
        stale = handle_message(cfg, snap, "restart the engine", NOW, secret, used, io, confirm=token)
        self.assertEqual(stale["error"], "stale screen")
        self.assertEqual(calls, [])
        fresh = handle_message(cfg, snap, "restart the engine", NOW, secret, used, io)
        done = handle_message(cfg, snap, "restart the engine", NOW, secret, used, io, confirm=fresh["token"])
        self.assertEqual(done["executed"]["unit"], "tfo-thf-engine.service")
        replay = handle_message(cfg, snap, "restart the engine", NOW, secret, used, io, confirm=fresh["token"])
        self.assertEqual(replay["error"], "replay")

    def test_flatten_is_immediate_and_stop_cannot_widen(self):
        root = tempfile.mkdtemp()
        cfg = config_for(root)
        takeover.takeover_on(cfg, NOW)
        book = _book()
        book["kill_switch"] = {"state": "OK"}
        book["seats"]["energy"]["positions"]["P"] = dict(
            id="P", status="open", side="LONG", ticker="XOM", venue="US", entry=10, fill=10, stop=9,
            shares=10, grade="A", sector="energy", fill_notional_aud=100)
        secret = "k"
        snap_rev = 4
        token = signing.issue(secret, "flatten", {}, NOW, snap_rev)
        used = set()
        out = takeover.act(cfg, book, ai_mode.blank_modes(NOW), token, NOW, snap_rev, secret, used,
                           marks={"XOM": {"last": 11}}, notional_fn=lambda p, px: float(p["shares"]) * px, cost_fn=lambda p, px: 1)
        self.assertTrue(out["ok"])
        self.assertEqual(out["book"]["kill_switch"]["state"], "FLAT")
        self.assertEqual(out["book"]["seats"]["energy"]["positions"], {})
        self.assertTrue(any(r["what"] == "flattened" for r in out["rows"]))
        # widen
        book2 = _book()
        book2["seats"]["energy"]["positions"]["P"] = dict(status="open", side="LONG", ticker="XOM", entry=10, fill=10, stop=9, shares=10, mark=11)
        with self.assertRaises(ValueError):
            takeover.move_stop(book2, "energy", "P", 8)
        tightened = takeover.move_stop(book2, "energy", "P", 9.4)
        self.assertEqual(tightened["seats"]["energy"]["positions"]["P"]["stop"], 9.4)
        self.assertEqual(tightened["seats"]["energy"]["positions"]["P"]["shares"], 10)


class StateFiles(unittest.TestCase):
    def test_corrupt_disk_and_drift(self):
        root = tempfile.mkdtemp()
        path = os.path.join(root, "s.json")
        atomic_write(path, {"a": 1}, min_free=0)
        open(path, "w").write("{not json")
        data, err = read_state(path)
        self.assertIsNone(data)
        self.assertEqual(err, "corrupt")
        self.assertTrue(os.path.exists(path + ".corrupt"))
        with self.assertRaises(StateError):
            atomic_write(os.path.join(root, "b.json"), {"a": 1}, min_free=10 ** 18)
        live = os.path.join(root, "veteran_desk")
        os.makedirs(live)
        f = os.path.join(live, "book.py")
        open(f, "w").write("print(1)\n")
        approved = {"veteran_desk/book.py": "deadbeef"}
        # diagnose_drift joins root + rel, so root is the parent of veteran_desk
        report = diagnose_drift(root, approved, files=("veteran_desk/book.py",))
        self.assertEqual(report["state"], "drift")
        self.assertEqual(open(f).read(), "print(1)\n")
        self.assertNotEqual(sha256_file(f), "deadbeef")

    def test_locks_and_tick_integrity(self):
        rows = diagnose_locks([
            dict(path="/tmp/a/.tick.lock", kind="mkdir", age_sec=30, held=True),
            dict(path="/tmp/a/.tick.lock", kind="mkdir", age_sec=900, held=True),
            dict(path="/tmp/a/.tick.flock", kind="flock", age_sec=900, held=True),
        ], 0, 720)
        kinds = [a["kind"] for a in rows["actions"]]
        self.assertIn("clear_lock", kinds)
        self.assertIn("escalate_lock", kinds)
        self.assertFalse(any(a.get("lock_kind") == "flock" and a["kind"] == "clear_lock" for a in rows["actions"]))
        bad = book_integrity({"paper_only": True, "seats": {"energy": {"positions": {"P": {"status": "open", "shares": 1}}}}})
        self.assertFalse(bad["ok"])
        prep = prepare_tick_restart(bad, [], 720)
        self.assertFalse(prep["restart"])
        good = book_integrity(_book())
        prep = prepare_tick_restart(good, [dict(path="L", kind="mkdir", held=True, age_sec=900)], 720)
        self.assertTrue(prep["restart"])
        self.assertEqual(len(prep["clear"]), 1)
        watch = watch_guardian(1000, 90, {"tfo-thf-guardian.timer": "inactive", "tfo-thf-guardian-check.timer": "active"})
        self.assertFalse(watch["ok"])
        self.assertTrue(any(a["unit"] == "tfo-thf-guardian.timer" for a in watch["actions"] if a["kind"] == "start"))

    def test_gap_flag_does_not_move_the_stop(self):
        flags = flag_stale([dict(id="P", seat="energy", ticker="BHP.AX", venue="ASX", status="open", mark_age_min=40, stop=10, side="LONG")])
        self.assertEqual(flags[0]["flag"], "stale_price")
        pos = dict(id="P", status="open", side="LONG", stop=10, ticker="BHP.AX")
        gap = gap_on_first_fresh(pos, True, 8)
        self.assertTrue(gap["through_stop"])
        self.assertEqual(pos["stop"], 10)

    def test_fill_at_entry_keeps_size_and_stop(self):
        book = _book()
        book["seats"]["energy"]["positions"]["A"] = dict(
            status="armed", grade="A", side="LONG", entry=10, stop=9, shares=4, ticker="XOM", venue="US")
        out = fill_paper.paper_fill_at_entry(book, "energy", "A", NOW, cost_aud=1, notional_aud=40)
        pos = out["seats"]["energy"]["positions"]["A"]
        self.assertEqual(pos["status"], "open")
        self.assertEqual(pos["fill"], 10)
        self.assertEqual(pos["stop"], 9)
        self.assertEqual(pos["shares"], 4)
        self.assertEqual(pos["grade"], "A")
        book["seats"]["energy"]["positions"]["B"] = dict(
            status="armed", grade="B", side="LONG", entry=10, stop=9, shares=4, ticker="CVX", venue="US")
        with self.assertRaises(fill_paper.FillRefused):
            fill_paper.paper_fill_at_entry(book, "energy", "B", NOW, 1, 40)


class Panel(unittest.TestCase):
    def test_phone_strip_has_no_chart(self):
        from thf_guardian.dash_panel import panel_html
        html = panel_html()
        self.assertIn("/desk/phone", html)
        self.assertIn("gd-phone", html)
        self.assertIn("gd-brains", html)
        self.assertNotIn("lightweight-charts", html)
        self.assertNotIn("<canvas", html)


if __name__ == "__main__":
    unittest.main()
