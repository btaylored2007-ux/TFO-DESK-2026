"""2026-10-27 audit: Codex fills external GPT when OPENAI_API_KEY is absent; fail closed to PENDING."""
import os, sys, tempfile, unittest, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.argv = [sys.argv[0]]  # wipe --date etc before import
import hunt_clearance as HC

class Parse(unittest.TestCase):
    def test_parses_pass_fail_lines(self):
        t = "main FLG PASS thesis ok\nclaude T FAIL earnings inside hold\n# noise\ngpt HD PASS\nbad line\nMAIN eCH pass lower\n"
        p = HC.parse_gpt_verdicts(t)
        self.assertEqual(p["main"]["FLG"]["GPT"], "PASS")
        self.assertEqual(p["claude"]["T"]["GPT"], "FAIL")
        self.assertIn("earnings", p["claude"]["T"]["reason"])
        self.assertEqual(p["gpt"]["HD"]["GPT"], "PASS")
        self.assertEqual(p["main"]["ECH"]["GPT"], "PASS")  # case fold
    def test_ignores_unknown_book(self):
        self.assertEqual(HC.parse_gpt_verdicts("other XYZ PASS x"), {})
    def test_apply_only_pending(self):
        ext = {"main": {"FLG": {"GPT": "PENDING", "CLAUDE": "PENDING"}, "ECH": {"GPT": "PASS", "CLAUDE": "PENDING"}}}
        n = HC.apply_gpt_verdicts(ext, {"main": {"FLG": {"GPT": "FAIL", "reason": "thin"}, "ECH": {"GPT": "FAIL", "reason": "x"}}})
        self.assertEqual(n, 1)
        self.assertEqual(ext["main"]["FLG"]["GPT"], "FAIL")
        self.assertEqual(ext["main"]["ECH"]["GPT"], "PASS")  # already set, not overwritten
    def test_codex_fail_closed_on_missing_bin(self):
        old = HC.CODEX_BIN; HC.CODEX_BIN = "/no/such/codex"
        try:
            d = tempfile.mkdtemp(); self.assertEqual(HC.gpt_via_codex("main FLG PASS", d), {})
            self.assertEqual(HC.GPT_STATE["status"], "PENDING")
        finally: HC.CODEX_BIN = old
    def test_codex_fail_closed_on_bad_rc(self):
        import subprocess
        class Fake:
            returncode = 1; stdout = ""; stderr = "boom"
        real = subprocess.run
        def fake(*a, **k): return Fake()
        subprocess.run = fake; old = HC.CODEX_BIN; HC.CODEX_BIN = sys.executable
        try:
            d = tempfile.mkdtemp(); open(os.path.join(d, "gpt_codex_last.txt"), "w").write("")
            # gpt_via_codex will still call subprocess; our Fake returns empty text + rc 1
            self.assertEqual(HC.gpt_via_codex("x", d), {})
            self.assertEqual(HC.GPT_STATE["status"], "PENDING")
        finally:
            subprocess.run = real; HC.CODEX_BIN = old
    def test_codex_parses_stubbed_output(self):
        # Codex CLI was removed 2026-10-30. The same BOOK TICKER PASS|FAIL lines now come from the Grok queue.
        from veteran_desk import llm_backend as LB
        text = "main FLG PASS ok thesis\nclaude T FAIL event\n"
        real = LB.grok_call
        def fake(engine, prompt, timeout=None, effort="high", seat=None, desk=None):
            return text, "rid-test", "ok"
        LB.grok_call = fake
        try:
            d = tempfile.mkdtemp()
            p = HC.gpt_via_codex("prompt", d)
            self.assertEqual(p["main"]["FLG"]["GPT"], "PASS")
            self.assertEqual(p["claude"]["T"]["GPT"], "FAIL")
            self.assertEqual(HC.GPT_STATE["status"], "ok (grok)")
            self.assertEqual(HC.GPT_STATE["source"], "grok_queue")
            self.assertTrue(os.path.exists(os.path.join(d, "gpt_grok_raw.md")))
        finally:
            LB.grok_call = real

if __name__ == "__main__": unittest.main()
