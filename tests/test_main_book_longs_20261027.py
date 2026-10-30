import unittest
"""Brett 2026-10-27 20:56 WITA: main US 48-72h hunt book takes longs as well as shorts (PAPER). No network.
Existing rules must still hold: A$2.5k risk, max 3 open, one per sector, direction balance (3rd slot opposite only), room gate."""
import os, sys, json, csv, tempfile, unittest, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hunt_orchestrator as HO, hunt48_short_paper as H
def bars_flat(px, n=260): return [dict(h=px * 1.01, l=px * 0.99, c=px)] * n
COLS = ["sym", "venue", "dir", "px", "atr", "atr_pct", "lo10", "pdl", "hi20", "pdh", "rsi", "roc5", "relvol", "adv_usd", "rank_score", "sector", "excluded", "country", "earn_days"]
def row(sym, d, score, sector):
    return dict(sym=sym, venue="ibkr", dir=d, px="100", atr="2", atr_pct="2", lo10="99", pdl="99.5", hi20="101", pdh="100.5", rsi="50", roc5="0", relvol="1",
                adv_usd="5e8", rank_score=str(score), sector=sector, excluded="", country="United States", earn_days="")
class MainBookLongs(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.saved = {k: getattr(HO, k) for k in ("BASE", "ORCH", "PLAN_DIR", "STATUS", "EVENTS", "TOP_DECILE")}
        HO.TOP_DECILE = 1.0   # 2026-10-28: these tests cover long/short + sector logic on a tiny universe; decile gate has its own test
        HO.BASE = self.d; HO.ORCH = os.path.join(self.d, "state", "hunt_orch"); HO.EVENTS = os.path.join(HO.ORCH, "events.jsonl")
        os.makedirs(os.path.join(self.d, "state", "calendar", "inputs")); os.makedirs(os.path.join(self.d, "state", "hunt48_short"))
    def tearDown(self):
        for k, v in self.saved.items(): setattr(HO, k, v)
    def build(self, rows, trades):
        with open(os.path.join(self.d, "state", "calendar", "inputs", "ranked.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLS); w.writeheader(); [w.writerow(r) for r in rows]
        json.dump(dict(trades=trades), open(os.path.join(self.d, "state", "hunt48_short", "ledger.json"), "w"))
        return HO.us_build_plan("20261027", 0.66, [], bars_fn=lambda s: bars_flat(100), sector_of=lambda tk, t=None: (t or {}).get("sector") or "S_" + tk)
    def held_two_shorts(self):
        return [dict(ticker="FLG", status="FILLED", direction="short", sector="Financial Services"),
                dict(ticker="ECH", status="FILLED", direction="short", sector="ETF:Latin America")]
    @unittest.skip("superseded: Brett 2026-10-28 07:12/07:13 hunt rule = fixed +3%/-8% (old 1.5R/1R-room geometry); see test_hunt_tp3_sl8_20261028")
    def test_long_fills_last_slot_when_two_shorts_open(self):
        P, an = self.build([row("SHRT", "short", 9, "Technology"), row("LONG", "long", 5, "Energy")], self.held_two_shorts())
        prim = [s for s in P["set"] if not s["bench"]]; back = [s for s in P["set"] if s["bench"]]
        self.assertEqual([(s["ticker"], s["direction"]) for s in prim], [("LONG", "long")])     # 3rd short blocked by direction balance
        self.assertEqual([s["ticker"] for s in back], ["SHRT"])
        L = an["LONG"]; R = L["max_fill"] - L["stop"]
        self.assertGreater(R, 0); self.assertGreater(L["t2"], L["max_fill"]); self.assertLessEqual(L["shares"] * R, 2500 * 0.66 + 1)
        self.assertGreaterEqual(L["room_R"], 1.0)
    def test_shorts_still_taken_and_sector_cap(self):
        P, _ = self.build([row("S1", "short", 9, "Technology"), row("L1", "long", 8, "Financial Services"), row("L2", "long", 7, "Energy")], [])
        prim = [(s["ticker"], s["direction"]) for s in P["set"] if not s["bench"]]
        self.assertEqual(prim, [("S1", "short"), ("L1", "long"), ("L2", "long")])
        P, _ = self.build([row("L1", "long", 8, "Financial Services")], self.held_two_shorts())   # FLG holds Financial Services
        self.assertEqual([s for s in P["set"] if not s["bench"]], [])
    def test_unknown_direction_rows_ignored(self):
        P, _ = self.build([row("X", "", 9, "Technology"), row("Y", "flat", 9, "Energy")], [])
        self.assertEqual(P["set"], [])
    def test_engine_arms_long_in_main_book_and_slot_gate_keeps_balance(self):
        saved = H.BOOK; H.BOOK = ""
        try:
            now = dt.datetime(2026, 10, 27, 13, 0, tzinfo=dt.timezone.utc); L = H.new_ledger("Our picks")
            t = H.upsert_trade(L, dict(ticker="LONG", direction="long", trigger=101.2, max_fill=101.35, stop=98.35, t1=104.3, t2=105.8, shares=500, sector="Energy"), now)
            self.assertEqual((t["direction"], t["risk_cap_aud"]), ("long", 5000.0))   # cap5k 2026-10-29 20:17: US main hunt 4 slots / A$5k
            t.update(status="FILLED", entry=101.3, entry_utc=now.isoformat(), fee_in_usd=1.0, fees_usd=1.0)
            self.assertEqual(H.slot_gate(t, 2, 3, self.held_two_shorts()), "filled")
            s = dict(t, ticker="S3", direction="short", sector="Technology", status="FILLED")
            self.assertEqual(H.slot_gate(s, 2, 3, self.held_two_shorts()), "shadow"); self.assertIn("direction balance", s["shadow_reason"])
            f = dict(t, ticker="L4", status="FILLED", fee_in_usd=1.0)
            self.assertEqual(H.slot_gate(f, 3, 3, self.held_two_shorts() + [t]), "shadow"); self.assertEqual(f["shadow_reason"], "max 3 open")
        finally: H.BOOK = saved
if __name__ == "__main__": unittest.main()
