"""Tests for idea_feed_runner: Claude switched off, GPT critic role. No network, no CLI calls."""
import datetime as dt, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import idea_feed_runner as r

SCAN = {"generated_at": "x", "source": "chatgpt-codex-cli", "market": "US", "note": "",
        "ideas": [{"ticker": "AAA", "thesis": "t", "catalyst": "c", "adv_usd_10d": 3e7,
                   "spread_bp_est": 5, "stop": "-5%", "label": "edge"}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name).resolve()
        self.p = [mock.patch.object(r, "FEED_DIR", d), mock.patch.object(r, "RAW_DIR", d / "raw"),
                  mock.patch.object(r, "SCRATCH", d / ".scratch")]
        for p in self.p: p.start()
        (d / "raw").mkdir()
        self.d = d

    def tearDown(self):
        for p in self.p: p.stop()
        self.tmp.cleanup()


class TestClaudeOff(Base):
    def test_claude_scan_never_calls_cli(self):
        with mock.patch.object(r, "CLAUDE_ENABLED", False), \
             mock.patch.object(r.subprocess, "run", side_effect=AssertionError("CLI called")), \
             mock.patch.object(sys, "argv", ["x", "--tool", "claude", "--market", "US"]):
            self.assertEqual(r.main(), 0)
        st = json.loads((self.d / "latest_claude_US.json").read_text())
        self.assertTrue(st["disabled"])
        self.assertFalse(list(self.d.glob("*.FAILED.txt")))

    def test_claude_ask_reroutes_to_chatgpt(self):
        calls = []
        def fake(prompt, schema, last, timeout=None):
            calls.append(prompt); return 0, "answer", ""
        with mock.patch.object(r, "CLAUDE_ENABLED", False), mock.patch.object(r, "run_chatgpt", fake), \
             mock.patch.object(r, "run_claude", side_effect=AssertionError("claude called")):
            self.assertEqual(r.ask("claude", "q?", "t"), 0)
        self.assertEqual(len(calls), 1)
        self.assertTrue(list((self.d / "adhoc").glob("chatgpt_*_t.md")))


class TestCritic(Base):
    def test_critic_writes_separate_files(self):
        out = {"generated_at": "y", "source": "s", "market": "US", "scan_stem": "chatgpt_US_20261009_2135",
               "note": "n", "reviews": [{"ticker": "AAA", "verdict": "reject", "objections": "o",
                                         "checks": "c", "label_ok": False}]}
        fake = mock.Mock(return_value=(0, json.dumps(out), ""))
        st = r.run_critic(SCAN, "US", "chatgpt_US_20261009_2135", dt.datetime.now(r.SYD), runner=fake)
        self.assertTrue(st["ok"], st)
        self.assertEqual(st["verdicts"], {"AAA": "reject"})
        self.assertTrue((self.d / "critic_chatgpt_US_20261009_2135.json").exists())
        self.assertTrue((self.d / "latest_critic_US.json").exists())
        # critic files must not match the idea-file globs used by parallel desk consumers
        self.assertFalse(list(self.d.glob("chatgpt_*_*.json")))
        self.assertIn("AAA", fake.call_args[0][0])

    def test_critic_failure_is_contained(self):
        fake = mock.Mock(side_effect=subprocess.TimeoutExpired("codex", 1))
        st = r.run_critic(SCAN, "US", "chatgpt_US_20261009_2135", dt.datetime.now(r.SYD), runner=fake)
        self.assertFalse(st["ok"])
        self.assertTrue((self.d / "critic_chatgpt_US_20261009_2135.FAILED.txt").exists())

    def test_critic_skipped_for_empty_scan(self):
        fake = mock.Mock()
        st = r.run_critic(dict(SCAN, ideas=[]), "ASX", "chatgpt_ASX_20261009_0855", dt.datetime.now(r.SYD), runner=fake)
        self.assertTrue(st["ok"]); fake.assert_not_called()

    def test_chatgpt_scan_then_critic(self):
        crit = {"generated_at": "y", "source": "s", "market": "US", "scan_stem": "z", "note": "n",
                "reviews": [{"ticker": "AAA", "verdict": "keep", "objections": "", "checks": "c", "label_ok": True}]}
        outs = iter([(0, json.dumps(SCAN), ""), (0, json.dumps(crit), "")])
        fake = mock.Mock(side_effect=lambda *a, **k: next(outs))
        with mock.patch.object(r, "run_chatgpt", fake), mock.patch.object(r, "CRITIC_ENABLED", True), \
             mock.patch.object(sys, "argv", ["x", "--tool", "chatgpt", "--market", "US"]):
            self.assertEqual(r.main(), 0)
        self.assertEqual(fake.call_count, 2)
        self.assertTrue(json.loads((self.d / "latest_critic_US.json").read_text())["ok"])
        self.assertTrue(json.loads((self.d / "latest_chatgpt_US.json").read_text())["ok"])


if __name__ == "__main__":
    unittest.main()
