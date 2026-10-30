import datetime as dt, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import book as BK
def test_vix_disabled_close_and_reversal():
    assert BK.HEDGE_ENABLED is False
    tmp = tempfile.mkdtemp(); BK.HEDGE_ARCHIVE = os.path.join(tmp, "a.json")
    try:
        D = dict(hedge_realised_aud=-5441.33); H = dict(stage=1, tranches=[dict(stage=1, premium_aud=13749.83, value_aud=13556.51, entry_cost_basis=dict(pct=0.013636363636363648), opened_utc="2026-10-28T19:25:02+00:00")])
        H2 = BK.hedge_close_all(D, H, dt.datetime(2026, 10, 28, 22, 47, tzinfo=dt.timezone.utc), "test")
        assert H2["disabled"] and H2["stage"] == 0 and H2["pnl_aud"] == 0.0 and H2["closed_realised_aud"] == -378.18 and D["hedge_realised_aud"] == -5819.51
        src = open(BK.__file__).read(); assert 'hedge_error_reversal_aud' in src
    finally: BK.HEDGE_ARCHIVE = None
