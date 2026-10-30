"""2026-10-28: kraken_paper_bot.py must install kraken_dns (process-local, try/except) before network use."""
import ast, os, unittest
BOT = os.path.expanduser("~/Applications/KrakenQuantDesk/kraken_paper_bot.py")

class T(unittest.TestCase):
    def test_hook_present_guarded_and_early(self):
        src = open(BOT).read(); tree = ast.parse(src)
        tries = [n for n in tree.body if isinstance(n, ast.Try) and "_kraken_dns.install()" in ast.get_source_segment(src, n)]
        self.assertEqual(len(tries), 1)
        self.assertTrue(tries[0].handlers, "must be inside try/except")
        first_urlopen = src.find("urlopen(")
        self.assertTrue(first_urlopen == -1 or src.find("_kraken_dns.install()") < first_urlopen)
    def test_paper_mode_text_unchanged(self):
        self.assertIn("PAPER", open(BOT).read())

if __name__ == "__main__": unittest.main()
