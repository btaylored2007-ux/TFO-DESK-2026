import os, sys, time, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hunt_clearance as HC
class C(unittest.TestCase):
    def fake(self, body):
        d = tempfile.mkdtemp(); p = os.path.join(d, "claude"); open(p, "w").write("#!/bin/sh\n" + body + "\n"); os.chmod(p, 0o755); return d, p
    def test_hang_times_out_fast(self):
        d, p = self.fake("sleep 30"); old = (HC.CLAUDE_BIN, HC.CLAUDE_TIMEOUT_S); HC.CLAUDE_BIN, HC.CLAUDE_TIMEOUT_S = p, 1
        try: t0 = time.time(); self.assertEqual(HC.claude_review("x", d), {}); self.assertLess(time.time() - t0, 10); self.assertEqual(HC.CLAUDE_STATE["status"], "PENDING")
        finally: HC.CLAUDE_BIN, HC.CLAUDE_TIMEOUT_S = old
    def test_login_expired(self):
        d, p = self.fake('echo "Invalid API key - Please run /login"; exit 1'); old = HC.CLAUDE_BIN; HC.CLAUDE_BIN = p
        try: self.assertEqual(HC.claude_review("x", d), {})
        finally: HC.CLAUDE_BIN = old
    def test_gpt_alone_clears(self):
        self.assertTrue(HC.ai_ok("PASS", "PASS", "PENDING")); self.assertTrue(HC.ai_ok("PASS", "PENDING", "PENDING_LOGIN"))
    def test_timeout_default(self): self.assertLessEqual(HC.CLAUDE_TIMEOUT_S, 300)
if __name__ == "__main__": unittest.main()
