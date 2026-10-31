"""The value hunt book can paper-trade delayed prints only when the config flag names it, and every fill keeps its stop."""
import value_deploy as vd


def _q(**k):
    return dict({"px_usd": 1.0, "px": 10.0, "fx_usd": 1.0, "executable": False, "paper_executable": True,
                 "paper_price_class": "DELAYED PAPER", "age_sec": 1200}, **k)


def test_flag_off_us_delayed_still_refused(monkeypatch):
    monkeypatch.delenv("TFO_HUNT_DELAYED_PAPER", raising=False)
    assert vd.fillable("HCI", _q())[0] is False
    assert vd.fillable("BPT.AX", _q())[0] is True


def test_flag_on_one_book_delayed_with_stop(monkeypatch):
    monkeypatch.setenv("TFO_HUNT_DELAYED_PAPER", "value")
    ok, delay, klass = vd.fillable("HCI", _q())
    assert ok and delay == 1200 and klass == "DELAYED PAPER"
    assert vd.STOP_PCT > 0
    px = 10.0
    assert px * (1 - vd.STOP_PCT) < px
    # closed session still does not fill
    assert vd.fillable("HCI", _q(paper_executable=False, executable=False))[0] is False
