"""Brett 28 Oct 13:40 approve-all #3: hunt books stage outside own market; caps/MAX_OPEN/A$2,500/funder tick kept. PAPER."""
import datetime as dt, json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hunt_cross_stage as X
NOW = dt.datetime(2026, 10, 28, 6, 0, tzinfo=dt.timezone.utc)

def _base(trades_by_mkt, enabled=True):
    b = tempfile.mkdtemp()
    for m in X.BOOKS:
        os.makedirs(os.path.join(b, "state", "hunt48_%s" % m))
        json.dump({"trades": trades_by_mkt.get(m, [])}, open(os.path.join(b, "state", "hunt48_%s" % m, "ledger.json"), "w"))
    json.dump({"enabled": enabled}, open(os.path.join(b, "state", "hunt_cross_market.json"), "w"))
    return b

def _t(sym, st="BENCH", risk=2500.0, score=4.0):
    return dict(id=hash(sym) % 999, sym=sym, status=st, direction="long", risk_aud=risk, risk_aud_actual=risk - 10,
                armed_utc=(NOW + dt.timedelta(hours=3)).isoformat(), score=score)

def test_stage_only_never_in_ledger_and_funder_tick():
    b = _base({"jp": [_t("7203.T")]}); before = open(os.path.join(b, "state", "hunt48_asx", "ledger.json")).read()
    o = X.run(b, NOW)
    r = o["asx"]["rows"]
    assert [x["sym"] for x in r] == ["7203.T"] and r[0]["needs_funder_tick"] and not r[0]["auto_arm"] and r[0]["status"] == "XSTAGED_WATCH"
    assert open(os.path.join(b, "state", "hunt48_asx", "ledger.json")).read() == before
    assert o["jp"]["rows"] == []   # never stages its own market

def test_caps_respected():
    full = [_t("A%d.T" % i, st="FILLED") for i in range(3)] + [_t("Z.T")]
    many = [_t("H%d.HK" % i, score=i) for i in range(6)] + [_t("BIG.HK", risk=4000.0, score=99)]
    o = X.build(_base({"jp": full, "hk": many}), NOW)
    syms = [x["sym"] for x in o["asx"]["rows"]]
    assert "Z.T" not in syms                                   # JP at MAX_OPEN -> stages nothing elsewhere
    assert "BIG.HK" not in syms                                # > A$2,500 risk skipped
    assert sum(s.endswith(".HK") for s in syms) == X.MAX_OPEN  # per-source cap 3
    assert X.MAX_OPEN == 3 and X.RISK_AUD == 2500.0

def test_disabled_writes_nothing():
    b = _base({"jp": [_t("7203.T")]}, enabled=False)
    assert X.run(b, NOW) == {} and not os.path.exists(os.path.join(b, "state", "hunt48_asx", "xstaged.json"))
