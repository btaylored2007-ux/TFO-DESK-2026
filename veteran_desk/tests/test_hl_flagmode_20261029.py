"""2026-10-29 flagmode PAPER: pit ingest filter flags instead of clipping; hl_review_ibkr clips only IBKR-confirmed bad bars.
Temp sqlite + fixture IBKR JSON only - never data/pit/pit.sqlite, never the IB gateway."""
import json, os, sys, tempfile, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import pit as P, hl_review_ibkr as R
SPIKE = ("2026-06-25", 100.0, 140.0, 70.0, 100.5, 1e6)
def _db():
    c = P.con(os.path.join(tempfile.mkdtemp(), "pit_test.sqlite")); d0 = dt.date(2026, 6, 1)
    for s in ("BAD.L", "REAL", "KRAKEN:XBTUSD", "NOIB.L"):
        c.executemany("INSERT INTO bars VALUES(?,?,?,?,?,?,?,?,?)", [(s, (d0 + dt.timedelta(days=i)).isoformat(), 100.0, 101.0, 99.0, 100.0, 1e6, "2026-06-01T00:00:00+00:00", "fixture") for i in range(20)])
    return c
def _bar(c, s, d="2026-06-25"): return c.execute("SELECT h,l,src FROM bars WHERE sym=? AND date=?", (s, d)).fetchone()
def _st(c, s, d="2026-06-25"): return (c.execute("SELECT status FROM bar_review WHERE sym=? AND date=?", (s, d)).fetchone() or [None])[0]
IB = {"BAD.L": {"bars": {"2026-06-25": [100.0, 101.0, 99.5, 100.5, 5e5]}}, "REAL": {"bars": {"2026-06-25": [100.0, 140.0, 70.0, 100.5, 5e5]}}, "NOIB.L": {"err": "unqualified"}}
def test_ingest_stores_raw_and_flags_pending():
    c = _db()
    for s in ("BAD.L", "REAL", "NOIB.L"): P.put_bars(c, s, [SPIKE], "yahoo")
    for s in ("BAD.L", "REAL", "NOIB.L"): assert _bar(c, s)[:2] == (140.0, 70.0) and _st(c, s) == "pending"
def test_crypto_exempt_no_flag():
    c = _db(); P.put_bars(c, "KRAKEN:XBTUSD", [SPIKE], "kraken")
    assert _bar(c, "KRAKEN:XBTUSD")[:2] == (140.0, 70.0) and _st(c, "KRAKEN:XBTUSD") is None
def test_normal_bar_not_flagged():
    c = _db(); P.put_bars(c, "REAL", [("2026-06-26", 100.0, 102.0, 98.5, 101.0, 1e6)], "yahoo"); assert _st(c, "REAL", "2026-06-26") is None
def test_dry_run_changes_nothing():
    c = _db(); P.put_bars(c, "BAD.L", [SPIKE], "yahoo"); r = R.check(c, IB, apply_=False)
    assert r["counts"]["clipped"] == 1 and _bar(c, "BAD.L")[:2] == (140.0, 70.0) and _st(c, "BAD.L") == "pending"
def test_batch_clips_only_ibkr_confirmed_bad():
    c = _db()
    for s in ("BAD.L", "REAL", "NOIB.L"): P.put_bars(c, s, [SPIKE], "yahoo")
    r = R.check(c, IB, apply_=True)
    assert r["counts"]["clipped"] == 1 and r["counts"]["kept"] == 1 and r["counts"]["no_ibkr"] == 1
    h, l, src = _bar(c, "BAD.L"); assert (h, l) == (101.0, 99.5) and "hlreview_ibkr" in src and _st(c, "BAD.L") == "ibkr_bad"
    assert _bar(c, "REAL")[:2] == (140.0, 70.0) and _st(c, "REAL") == "ibkr_ok"
    assert _bar(c, "NOIB.L")[:2] == (140.0, 70.0) and _st(c, "NOIB.L") == "no_ibkr"
def test_redownload_reapplies_ibkr_fix_not_blind_clip():
    c = _db(); P.put_bars(c, "BAD.L", [SPIKE], "yahoo"); R.check(c, IB, apply_=True)
    P.put_bars(c, "BAD.L", [SPIKE], "yahoo")   # Yahoo re-download of the same bad print
    h, l, src = _bar(c, "BAD.L"); assert (h, l) == (101.0, 99.5) and src.endswith("|hlreview_fix") and _st(c, "BAD.L") == "ibkr_bad"
def test_redownload_keeps_ibkr_ok_without_requeue():
    c = _db(); P.put_bars(c, "REAL", [SPIKE], "yahoo"); R.check(c, IB, apply_=True)
    P.put_bars(c, "REAL", [SPIKE], "yahoo"); assert _bar(c, "REAL")[:2] == (140.0, 70.0) and _st(c, "REAL") == "ibkr_ok"
def test_crypto_never_checked_even_if_queued():
    c = _db(); c.execute("INSERT INTO bar_review(sym,date,o,h,l,c,flags,status) VALUES('KRAKEN:XBTUSD','2026-06-25',100,140,70,100.5,'[\"high x\"]','pending')")
    assert R.pending(c) == []
def test_legacy_clip_mode_switch(monkeypatch):
    c = _db(); monkeypatch.setattr(P, "HL_MODE", "clip"); P.put_bars(c, "BAD.L", [SPIKE], "yahoo")
    assert _bar(c, "BAD.L")[:2] == (100.5, 100.0)
def _applied(tmp):
    J = dict(clipped=[dict(sym="BAD.L", date="2026-06-25", hand=False, old=dict(o=100.0, h=140.0, l=70.0, c=100.5, src="yahoo"), new=dict(h=101.0, l=99.5), why={"high": "ibkr_confirms_bad", "low": "ibkr_confirms_bad"}, ibkr_bar=[100, 101, 99.5, 100.5, 1])],
             restored=[dict(sym="REAL", date="2026-06-25", old=dict(o=100.0, h=100.5, l=70.0, c=100.5, src="yahoo"), new=dict(h=140.0, l=70.0), why={"high": "ibkr_shows_traded"})],
             kept=[dict(sym="NOIB.L", date="2026-06-25", old=dict(o=100.0, h=140.0, l=70.0, c=100.5, src="yahoo"), why={"high": "no_ibkr - kept"})])
    p = os.path.join(tmp, "applied.json"); json.dump(J, open(p, "w")); return p
def test_seed_then_redownload_respects_verdicts(tmp_path):
    c = _db(); p = _applied(str(tmp_path)); n = R.seed(c, p, apply_=True)
    assert n == dict(ibkr_bad=1, hand_bad=0, ibkr_ok=1, no_ibkr=1)
    for s in ("BAD.L", "REAL", "NOIB.L"): P.put_bars(c, s, [SPIKE], "yahoo")
    assert _bar(c, "BAD.L")[:2] == (101.0, 99.5) and _bar(c, "REAL")[:2] == (140.0, 70.0)
    assert _st(c, "REAL") == "ibkr_ok" and _st(c, "NOIB.L") == "no_ibkr" and R.pending(c) == []
def test_recheck_restored_undoes_reclip(tmp_path):
    c = _db(); p = _applied(str(tmp_path))
    c.execute("INSERT OR REPLACE INTO bars VALUES('REAL','2026-06-25',100,100.5,70,100.5,1,'x','yahoo')")   # blind clamp put high back at the body
    r = R.recheck_restored(c, p, apply_=False); assert r["counts"]["reclipped"] == 1 and _bar(c, "REAL")[0] == 100.5
    r = R.recheck_restored(c, p, apply_=True); assert r["reclipped"][0]["applied"] and _bar(c, "REAL")[:2] == (140.0, 70.0)
    assert R.recheck_restored(c, p)["counts"] == dict(ok=1, reclipped=0, missing=0)
def test_sources_parse_on_py39_grammar():
    import ast
    for f in ("pit.py", "hl_review_ibkr.py"):
        ast.parse(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), f)).read(), feature_version=(3, 9))
