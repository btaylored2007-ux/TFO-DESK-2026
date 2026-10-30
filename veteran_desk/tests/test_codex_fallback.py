"""Rewritten 2026-10-29 (fixer): Claude is retired - no codex->claude fallback. desk.llm is GPT/Codex only; a failing Codex returns __ERR__ (never blocks, never calls Claude)."""
import os, sys, unittest, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import desk as D
class T(unittest.TestCase):
    def setUp(self):
        self.sd, self.cx, self.sl = D.SD, D.CODEX, D.time.sleep; D.SD = tempfile.mkdtemp(); D.time.sleep = lambda s: None
    def tearDown(self): D.SD, D.CODEX = self.sd, self.cx; D.time.sleep = self.sl
    def test_stdout_parse(self):
        self.assertEqual(D._codex_stdout("hdr\ncodex\n{\"a\":1}\ntokens used\n9"), "{\"a\":1}")
    def test_claude_engine_remapped_to_codex_no_fallback(self):
        D.CODEX = "/nonexistent/codex"
        out = D.llm("claude", "hi", 5)
        self.assertTrue(out.startswith("__ERR__"), out); self.assertNotIn("FALLBACK_CLAUDE", out)
    def test_codex_error_returns_err(self):
        D.CODEX = "/nonexistent/codex"
        out = D.llm("codex", "hi", 5); self.assertTrue(out.startswith("__ERR__"), out); self.assertNotIn("FALLBACK_CLAUDE", out)
    def test_claude_retired_constant(self):
        self.assertIsNone(D.CLAUDE)
if __name__ == "__main__": unittest.main()
