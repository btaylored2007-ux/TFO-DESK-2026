"""leaderboard_prize is external funding: explains a start/seed rise with no receipt error (custodian 2026-10-30)."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import firm_capital_flows as F, book_kill, fundamental_true_return as FT

def test_prize():
    d = tempfile.mkdtemp(); p = os.path.join(d, "vd.json")
    book = {"start": 1000.0, "capital_events": []}
    json.dump(book, open(p, "w")); b = [{"key": "vd", "path": p, "ccy": "USD", "start_usd": 1000.0}]
    prev, e1 = F.snapshot(b)
    book["start"] = 1351.49; book["capital_events"].append({"type": "leaderboard_prize", "amount_aud": 500.0,
        "amount_usd": 351.49, "utc": "2026-10-29T22:00:09+00:00"})
    json.dump(book, open(p, "w")); b[0]["start_usd"] = 1351.49
    cur, e2 = F.snapshot(b, prev)
    r = F.reconcile(prev, cur)
    net, details, errs = r; assert abs(net - 351.49) < 0.005
    assert not e1 and not e2 and not errs, (e1, e2, r)
    assert book_kill.net_capital_flow(book) == 0.0  # usd-only by design (16:40 revert); VD is not a book_kill flow caller
    assert abs(sum(u for _, u, _, _ in FT.flows(book)) - 351.49) < 1e-6
    print("OK", r)

if __name__ == "__main__":
    test_prize()
