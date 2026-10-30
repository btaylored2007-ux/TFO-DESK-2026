"""vetofix 2026-10-29: vetoed shadow rows are written status=vetoed (shadow lifecycle in shadow_status), still marked and payout-scored."""
import os, json, tempfile
import veteran_desk.desk as D
import veteran_desk.payouts as PO
class _R:
    def __init__(s): s.gate = {"passed": False, "reasons": ["below risk floor - rejected, not lifted"]}
def _rec():
    return dict(id="VET-KO-T-price_action", seat="price_action", proposal=dict(ticker="KO", side="LONG", entry=91.73, stop=90.1, target=94.9, horizon_days=10),
                gate={"passed": False, "reasons": ["below risk floor - rejected, not lifted"]}, features={"venue": "US"}, created_utc="2026-10-29T04:55:52+00:00")
def test_track_writes_vetoed_and_mark_keeps_label(monkeypatch):
    tmp = tempfile.mkdtemp(); monkeypatch.setattr(D, "SD", tmp)
    D.track([_rec()])
    row = json.load(open(os.path.join(tmp, "forward_trades.json")))["trades"]["VET-KO-T-price_action"]
    assert row["status"] == "vetoed" and row["shadow_status"] == "pending_trigger" and row["vetoed"] is True
    bars = [("2026-10-30", 91, 92, 89.0, 89.5, 1, "2026-10-30T20:00:00+00:00")]   # triggers then hits stop
    monkeypatch.setattr(D.P, "con", lambda: None); monkeypatch.setattr(D.P, "bars_asof", lambda c, t, a, n: bars)
    monkeypatch.setattr(D.P, "venue_of", lambda t: "US"); monkeypatch.setattr(D, "audusd", lambda c, a: 0.7)
    out = D.mark()["trades"]["VET-KO-T-price_action"]
    assert out["status"] == "vetoed" and out["shadow_status"] == "closed" and out["outcome"] == "stop" and out["pnl_aud"] < 0
def test_payouts_reads_shadow_status(tmp_path):
    p = tmp_path / "f.json"
    p.write_text(json.dumps({"trades": {"a": dict(seat="banks", status="vetoed", shadow_status="closed", vetoed=True, vetoed_by=["blind_challenger"], pnl_aud=-10.0, closed="2026-10-30"),
                                        "b": dict(seat="banks", status="vetoed", shadow_status="pending_trigger", vetoed=True, vetoed_by=["blind_challenger"], pnl_aud=-10.0)}}))
    rows = PO._read_correct_vetoes(str(p))
    blob = repr(rows)
    assert "'a'" in blob and "'b'" not in blob
