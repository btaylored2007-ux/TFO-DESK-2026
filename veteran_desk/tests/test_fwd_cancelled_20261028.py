import os, json, tempfile
import veteran_desk.desk as D
def test_cancelled_never_marked():
    tmp = tempfile.mkdtemp(); old = D.SD; D.SD = tmp
    try:
        T = {"trades": {"X": dict(seat="banks", ticker="BAC", side="SHORT", entry=49.15, stop=51.1, target=45.25, shares=10, cost_aud=1.0,
             registered_utc="2026-01-01T00:00:00+00:00", horizon_days=20, status="cancelled", cluster="banks:US")}}
        json.dump(T, open(os.path.join(tmp, "forward_trades.json"), "w"))
        out = D.mark()["trades"]["X"]
        assert out["status"] == "cancelled" and "pnl_aud" not in out and "outcome" not in out
    finally: D.SD = old
