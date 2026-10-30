"""OPEN-PNL FIX 2026-10-27 (pt1534u): War Room book tiles must headline P&L since entry, not the H4 day-change field."""
import json, os, re, subprocess, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "warroom_clean.js")
HTML = os.path.join(ROOT, "command_dashboard.html")
STATUS = ["hunt48_short_status.json", "hunt48_short_claude_status.json", "hunt48_short_gpt_status.json"]


class WarroomOpenPnl(unittest.TestCase):
    def test_tile_headline_uses_since_entry(self):
        s = open(JS).read()
        self.assertIn("j.pnl_since_entry_aud", s)
        self.assertIn('<div class="v ${C(opn)}">${SA(opn)}</div>', s)
        self.assertNotIn('<div class="v ${C(j.pnl_today_aud)}">', s)
        self.assertIn("Total (3 books)", s)

    def test_js_parses(self):
        r = subprocess.run(["node", "-e", "new Function(require('fs').readFileSync(process.argv[1],'utf8'))", JS],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_banner_uses_since_entry(self):
        s = open(HTML).read()
        self.assertIn("j.pnl_since_entry_aud!=null?j.pnl_since_entry_aud:j.pnl_today_aud", s)

    def test_status_files_carry_since_entry_equal_to_trade_sum(self):
        for f in STATUS:
            p = os.path.join(ROOT, "static", f)
            if not os.path.exists(p):
                continue
            d = json.load(open(p))
            self.assertIn("pnl_since_entry_aud", d, f)
            tot = sum((t.get("pnl_aud") or 0) for t in d.get("trades") or [])
            self.assertAlmostEqual(d["pnl_since_entry_aud"], round(tot, 2), delta=0.05, msg=f)


if __name__ == "__main__":
    unittest.main()
