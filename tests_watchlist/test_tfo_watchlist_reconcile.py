#!/usr/bin/env python3
"""audit 2026-09-26: tfo_watchlist.reconcile_tags tags HT BUY fills that bypassed `fire`. Offline; temp reports dir only."""
import datetime as dt, importlib, json, os, sys, tempfile, unittest
from zoneinfo import ZoneInfo
DESK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DESK)
SYD, UTC = ZoneInfo("Australia/Sydney"), dt.timezone.utc

class Reconcile(unittest.TestCase):
    def setUp(self):
        self.wl = importlib.import_module("tfo_watchlist")
        self.old = (self.wl.R, self.wl.TAGS)
        self.tmp = tempfile.TemporaryDirectory(); r = self.tmp.name
        self.wl.R = r; self.wl.TAGS = os.path.join(r, "ht_fills_tagged.jsonl")
        self.fire_line = "LOG 2026-09-25 20:05 BUY SOL/USD 1203.2879 118.01 LIVE tfo_ht_crypto 142000.00 0.00 0.00"
        self.direct_eq = "LOG 2026-09-25 23:55 BUY USO 1218.58 150.14 LIVE fundamental_book 182958.00 0.00 0.00"
        self.direct_cr = "LOG 2026-09-26 16:05 BUY AVAX/USD 100 20 LIVE tfo_ht_crypto 2000.00 0.00 0.00 5.20"
        self.exit_line = "LOG 2026-09-26 01:13 STOP HUM 5.04701 409.275 LIVE fundamental_book 1973.68 1881.75 -91.93"
        self.old_line = "LOG 2026-09-25 17:51 BUY OLD 1 1 LIVE fundamental_book 1.00 0.00 0.00"
        with open(os.path.join(r, "tfo_head_trader_log.txt"), "w") as f:
            f.write("\n".join([self.old_line, self.fire_line, self.direct_eq, self.exit_line, self.direct_cr]) + "\n")
        with open(os.path.join(r, "tfo_head_trader_facts.jsonl"), "w") as f:
            f.write(json.dumps({"syd": "2026-09-25 23:55", "fact": "BUY USO $182958 LIVE (beta) theme oil: x", "ticket": "HT-uso"}) + "\n")
            f.write(json.dumps({"syd": "2026-09-26 16:05", "fact": "BUY AVAX/USD $2000 LIVE (beta) theme alt: x", "ticket": "HT-avax"}) + "\n")
        json.dump({"head_trader_tickets": [{"ticket_id": "HT-uso", "utc": "2026-09-25T13:55:41Z", "action": "BUY", "sym": "USO", "theme": "oil_momentum", "label": "beta"}]},
                  open(os.path.join(r, "fundamental_book.json"), "w"))
        json.dump({"head_trader_tickets": [{"ticket_id": "HT-avax", "utc": "2026-09-26T06:05:03Z", "action": "BUY", "pair": "AVAX/USD", "theme": "alt", "label": "edge"}]},
                  open(os.path.join(r, "tfo_ht_crypto.json"), "w"))
        with open(self.wl.TAGS, "w") as f:
            f.write(json.dumps({"symbol": "SOL/USD", "setup": "watchlist_momentum_crypto", "log_line": self.fire_line}) + "\n")

    def tearDown(self):
        self.wl.R, self.wl.TAGS = self.old; self.tmp.cleanup()

    def tags(self):
        return [json.loads(l) for l in open(self.wl.TAGS) if l.strip()]

    def test_tags_direct_fills_once_and_skips_fire_exits_old_and_young(self):
        now = dt.datetime(2026, 9, 26, 16, 10, tzinfo=SYD).astimezone(UTC)   # AVAX line only 5 min old -> waits
        recs = self.wl.reconcile_tags(now)
        self.assertEqual([r["symbol"] for r in recs], ["USO"])
        r = recs[0]
        self.assertEqual((r["setup"], r["market"], r["ticket_id"], r["theme"], r["label"], r["ts"]), ("ht_direct_equity", "US", "HT-uso", "oil_momentum", "beta", "2026-09-25T13:55:41Z"))
        self.assertEqual((r["notional_usd"], r["risk_usd"], r["fees_usd"], r["log_line"]), (182958.0, 9147.9, None, self.direct_eq))
        self.assertEqual(self.wl.reconcile_tags(now), [])                   # idempotent
        later = dt.datetime(2026, 9, 26, 16, 30, tzinfo=SYD).astimezone(UTC)
        recs = self.wl.reconcile_tags(later)
        self.assertEqual([(x["symbol"], x["setup"], x["market"], x["fees_usd"], x["label"]) for x in recs], [("AVAX/USD", "ht_direct_crypto", "CRYPTO", 5.2, "edge")])
        self.assertEqual(len(self.tags()), 3)
        self.assertEqual(sum(1 for t in self.tags() if t["log_line"] == self.fire_line), 1)
        self.assertFalse(any("OLD" == t["symbol"] or "HUM" == t["symbol"] for t in self.tags()))

    def test_missing_log_is_noop(self):
        os.unlink(os.path.join(self.wl.R, "tfo_head_trader_log.txt"))
        self.assertEqual(self.wl.reconcile_tags(), [])

if __name__ == "__main__":
    unittest.main()
