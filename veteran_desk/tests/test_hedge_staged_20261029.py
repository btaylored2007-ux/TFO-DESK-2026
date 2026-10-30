import datetime as dt, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
U = dt.timezone.utc; OPEN = dt.datetime(2026, 10, 28, 18, 0, tzinfo=U); CLOSED = dt.datetime(2026, 10, 28, 22, 0, tzinfo=U)
CO = lambda: dict(pct=0.01, contract="X", bid=1, ask=1.02, expiry="2026-11-18")
def _D(gross):   # one open position worth `gross` AUD (au=1, ASX-free US ticker -> aud = x/au)
    D = dict(seats={"energy": BK._seat_ledger()}); D["seats"]["energy"]["positions"]["p"] = dict(status="open", shares=1, ticker="XLE", fill=gross, mark=gross); return D
def _arch(tmp): BK.HEDGE_ARCHIVE = os.path.join(tmp, "a.json")
def test_bands_and_hysteresis():
    S = BK.hedge_stage
    assert S(.10, 2) == 0 and S(.15, 0) == 0 and S(.15, 2) == 2 and S(.2999, 1) == 1 and S(.2999, 0) == 0
    assert S(.30, 0) == 1 and S(.4999, 3) == 1 and S(.50, 0) == 2 and S(.6999, 3) == 2 and S(.70, 0) == 3 and S(.1499, 3) == 0
def test_legacy_close_then_stage1_and_offhours_hold():
    tmp = tempfile.mkdtemp(); _arch(tmp); eq = 1_000_000.0
    D = _D(400_000); H = dict(premium_aud=50000.0, value_aud=47000.0, vix_ref=17.55, opened_utc="2026-10-28T09:09:38+00:00")
    H1 = BK.hedge_step(D, dict(H), {}, 1.0, eq, CLOSED, 17.2, {}, cost_fn=CO)   # US closed: queued, nothing realised
    assert H1.get("close_pending") and "hedge_realised_aud" not in D and H1["premium_aud"] == 50000.0
    H2 = BK.hedge_step(D, H1, {}, 1.0, eq, OPEN, 17.2, {}, cost_fn=CO)
    v = round(50000 * (17.2 / 17.55) ** 3 * (1 - 0 / 30), 2); real = round(v - round(v * .01, 2) - 50000, 2)
    assert H2["stage"] == 1 and H2["premium_aud"] == 2500.0, H2
    assert abs(D["hedge_realised_aud"] - (real - 25.0)) < .02, (D["hedge_realised_aud"], real)   # legacy realised + stage-1 entry cost
    import json; A = json.load(open(BK.HEDGE_ARCHIVE)); assert A["trades"][0]["realised_pnl_aud"] == real
def test_step_up_down_off_and_cap():
    tmp = tempfile.mkdtemp(); _arch(tmp); eq = 1_000_000.0; H = {}
    H = BK.hedge_step(_D(800_000), H, {}, 1.0, eq, OPEN, 17.0, {}, cost_fn=CO); assert H["stage"] == 3 and H["premium_aud"] == 7500.0   # max 0.75%
    H = BK.hedge_step(_D(600_000), H, {}, 1.0, eq, OPEN, 17.0, {}, cost_fn=CO); assert H["stage"] == 2
    H = BK.hedge_step(_D(200_000), H, {}, 1.0, eq, OPEN, 17.0, {}, cost_fn=CO); assert H["stage"] == 2   # 15-30% hold
    H = BK.hedge_step(_D(100_000), H, {}, 1.0, eq, CLOSED, 17.0, {}, cost_fn=CO); assert H["stage"] == 2 and "queued" in H["exec_note"]   # off-hours: no fill
    H = BK.hedge_step(_D(100_000), H, {}, 1.0, eq, OPEN, 17.0, {}, cost_fn=CO); assert H["stage"] == 0 and H["premium_aud"] == 0
def test_no_quote_no_fill():
    tmp = tempfile.mkdtemp(); _arch(tmp)
    H = BK.hedge_step(_D(400_000), {}, {}, 1.0, 1e6, OPEN, 17.0, {}, cost_fn=lambda: None); assert H["stage"] == 0
