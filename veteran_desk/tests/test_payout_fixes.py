import json, os, sys, tempfile, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import payouts as PY, payouts_bridge as PB, book as BK
def kw():
    t = tempfile.mkdtemp(); return dict(out_path=os.path.join(t, "p.json"), fwd_path=os.path.join(t, "f.json"), score_path=os.path.join(t, "s.json"))
def B(rows): return dict(cash_aud=1e6, commission_expense_aud=0.0, commission_forfeit_credit_aud=0.0, closed=rows)
def test_out_of_order_hwm_matches_in_order():
    a = dict(id="a", seat="energy", net_aud=1000.0, closed_utc="2026-10-01T00:00:00Z")
    b = dict(id="b", seat="energy", net_aud=-800.0, closed_utc="2026-10-02T00:00:00Z")
    c = dict(id="c", seat="energy", net_aud=900.0, closed_utc="2026-10-03T00:00:00Z")
    k1 = kw(); x = B([a, b, c]); PY.accrue(x, now="2026-10-28T00:00:00Z", **k1)
    k2 = kw(); y = B([a, c]); PY.accrue(y, now="2026-10-28T00:00:00Z", **k2); y["closed"].append(b); out = PY.accrue(y, now="2026-10-28T00:00:00Z", **k2)
    assert out["seats"]["energy"]["accrued_commission_aud"] == 165.0 == x["commission_expense_aud"], out["seats"]["energy"]   # peak 1100 -> 15%
    assert y["commission_expense_aud"] == 165.0 and y["cash_aud"] == x["cash_aud"]
def test_rounding_no_cent_loss():
    k = kw(); rows = [dict(id="t%d" % i, seat="banks", net_aud=0.33, closed_utc="2026-10-%02dT00:00:00Z" % (i + 1)) for i in range(20)]
    out = PY.accrue(B(rows), now="2026-10-28T00:00:00Z", **k); e = out["seats"]["banks"]
    assert e["cumulative_net_aud"] == 6.6 and e["accrued_commission_aud"] == 0.99, e   # 15% of 6.60, not 20 x round(0.0495)
def test_forfeit_keyed_to_cut_date():
    k = kw(); rows = [dict(id="m", seat="macro", net_aud=1000.0, closed_utc="2026-05-10T00:00:00Z")]
    json.dump(dict(seats=dict(macro=dict(verdict="CUT", cut_utc="2026-08-15T00:00:00Z"))), open(k["score_path"], "w"))
    out = PY.accrue(B(rows), now="2026-10-28T00:00:00Z", **k); m = out["seats"]["macro"]
    assert m["forfeited_aud"] == 75.0 and m["vested_aud"] == 0.0 and m["cut_utc"].startswith("2026-08-15"), m   # May vests 09-01, after the 08-15 cut
    json.dump(dict(seats=dict(macro=dict(verdict="CUT", cut_utc="2026-09-15T00:00:00Z"))), open(k["score_path"], "w"))
    k2 = dict(k, out_path=k["out_path"] + "2"); out = PY.accrue(B(rows), now="2026-12-28T00:00:00Z", **k2)
    assert out["seats"]["macro"]["forfeited_aud"] == 0.0 and out["seats"]["macro"]["vested_aud"] == 75.0
def test_timezones():
    for s in ("2026-10-28T08:00:00+08:00", "2026-10-28T00:00:00.1Z", "2026-10-28T08:00:00+0800", "2026-10-28 00:00:00 UTC", "1793145600"):
        d = PY._parse_dt(s); assert d is not None and d.utcoffset().total_seconds() == 0, s
    assert PY._parse_dt("2026-11-01T07:00:00+08:00").month == 10   # month bucket is UTC
def test_atomic_concurrent():
    k = kw(); rows = [dict(id="z%d" % i, seat="crypto", net_aud=100.0, closed_utc="2026-10-%02dT00:00:00Z" % (i + 1)) for i in range(10)]
    books = [B(list(rows)) for _ in range(6)]; th = [threading.Thread(target=PY.accrue, args=(b,), kwargs=dict(now="2026-10-28T00:00:00Z", **k)) for b in books]
    [t.start() for t in th]; [t.join() for t in th]
    out = json.load(open(k["out_path"])); assert out["seats"]["crypto"]["accrued_commission_aud"] == 150.0
    assert sum(b["commission_expense_aud"] for b in books) == 150.0   # only one run books the expense
    assert not [f for f in os.listdir(os.path.dirname(k["out_path"])) if f.endswith(".tmp")]
def test_bridge_split_sums_exactly():
    k = kw(); D = dict(seats={s: BK._seat_ledger() for s in BK.TRADERS})
    json.dump(dict(trades=dict(v=dict(status="closed", vetoed=True, vetoed_by=["blind_challenger"], pnl_aud=-1000.0, closed="2026-10-05"))), open(k["fwd_path"], "w"))
    PB.book_commissions(D, **k); tot = sum(PY._d(L["commission_expense_aud"]) for L in D["seats"].values())
    assert tot == PY._d("20.00"), tot
if __name__ == "__main__":
    for k_, v in dict(globals()).items():
        if k_.startswith("test_"): v(); print("PASS", k_)
