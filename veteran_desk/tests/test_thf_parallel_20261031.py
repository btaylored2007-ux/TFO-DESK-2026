# tfo_thfpar_20261031: THF reviews all eligible seats per round, bounded parallel, per-seat timeout, private tmp, gates kept.
import datetime as dt, json, os, tempfile, unittest
from unittest import mock
from veteran_desk import vd_engine as E
def _top(sym, g="A"): return dict(sym=sym, side="LONG", grade=g, score=90, wake_ok=True)
class T(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(); st = os.path.join(self.td.name, "vd_engine"); os.makedirs(os.path.join(st, "top10"))
        self.p = [mock.patch.object(E, "ST", st), mock.patch.object(E, "STATE_P", os.path.join(st, "b.json")), mock.patch.object(E, "COUNT_P", os.path.join(st, "c.json")),
                  mock.patch.object(E, "QUEUE_P", os.path.join(st, "q.jsonl")), mock.patch.object(E, "BASE", self.td.name), mock.patch.object(E, "second_looks", lambda W: None),
                  mock.patch.object(E, "wita", lambda t=None: dt.datetime(2026, 10, 31, 1, 12, tzinfo=E.WITA))]
        for x in self.p: x.start()
        os.makedirs(os.path.join(self.td.name, "state", "veteran_desk"), exist_ok=True)
        self.st = st; E.RUNNER.update(proc=None, wid=None, t0=None)
    def tearDown(self):
        for x in self.p: x.stop()
        self.td.cleanup()
    def _seed(self, seats):
        pend = {}
        for i, s in enumerate(seats):
            E.wj(os.path.join(self.st, "top10", s + ".json"), dict(top10=[_top("S%d" % i)]))
            pend[s] = {"S%d" % i: dict(_top("S%d" % i), queued_utc="2026-10-30T17:0%d:00+00:00" % (i % 10))}
        E.wj(E.STATE_P, dict(pending=pend))
    def test_all_eleven_seats_one_round(self):
        seats = ["s%02d" % i for i in range(11)]; self._seed(seats)
        W = E.release_slot(); self.assertIsNotNone(W); self.assertEqual(sorted(W["seats"]), seats)
    def test_hold_while_round_busy(self):
        self._seed(["a", "b"]); E.jl(E.QUEUE_P, dict(wake_id="W0", status="queued", seats=["x"]))
        self.assertIsNone(E.release_slot()); self.assertTrue(E._round_busy())
        E.jl(E.QUEUE_P, dict(wake_id="W0", status="running")); self.assertFalse(E._round_busy())
    def test_caps_still_enforced(self):
        seats = ["s%02d" % i for i in range(11)]; self._seed(seats)
        E.wj(E.COUNT_P, dict(day="2026-10-31", total=38, seat={"s00": 6}))
        W = E.release_slot(); self.assertEqual(len(W["seats"]), 2); self.assertNotIn("s00", W["seats"])
    def test_ttl_gate_kept(self):
        self._seed(["a"]); E.wj(os.path.join(self.st, "top10", "a.json"), dict(top10=[dict(_top("S0"), wake_ok=False)]))
        self.assertIsNone(E.release_slot())
    def test_runner_env_bounded_and_private_tmp(self):
        E.jl(E.QUEUE_P, dict(wake_id="THF-x/1", status="queued", seats=["a", "b"])); os.makedirs(os.path.join(self.td.name, "logs"))
        with mock.patch.object(E.subprocess, "Popen") as P:
            P.return_value.pid = 1; E.runner_tick()
        env = P.call_args.kwargs["env"]; self.assertEqual(env["VET_LLM_CONCURRENCY"], E.THF_LLM_CONCURRENCY); self.assertEqual(env["VET_LLM_TIMEOUT"], E.THF_SEAT_TIMEOUT_S)
        self.assertTrue(1 <= int(E.THF_LLM_CONCURRENCY) <= 6); self.assertLessEqual(int(E.THF_SEAT_TIMEOUT_S), 180)
        self.assertTrue(env["TMPDIR"].startswith(self.st) and "/" not in os.path.basename(env["TMPDIR"]) and os.path.isdir(env["TMPDIR"]))
        self.assertIn("--seats=a,b", P.call_args.args[0])
if __name__ == "__main__": unittest.main()
