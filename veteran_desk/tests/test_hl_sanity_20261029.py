"""2026-10-29 PAPER (updated 2026-10-29 flagmode: put_bars/sane_rows store raw + flag; hl_check still returns the clamp): pit.py bar sanity filter (hl_check / sane_rows / put_bars, bak .bak-20261029-1349-hlsanity2).
A high/low beyond the open-close body by more than max(3 x ATR14, 5% of the body edge) is clipped to the body edge; inside either bound it is kept.
Crypto (KRAKEN:/BINANCE: ':' symbols, venue CRYPTO) is exempt. All DB work is on a temp sqlite file - never data/pit/pit.sqlite."""
import os, sys, tempfile, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import pit as P
# ---------- hl_check: clip vs keep ----------
def test_high_beyond_3atr_and_5pct_is_clipped_to_body():
    h, l, fl = P.hl_check(100.0, 130.0, 99.0, 101.0, 2.0)
    assert h == 101.0 and l == 99.0 and len(fl) == 1 and fl[0].startswith("high")
def test_low_beyond_3atr_and_5pct_is_clipped_to_body():
    h, l, fl = P.hl_check(492.9, 505.72, 414.0, 503.5, 16.25)   # BARC.L 17 Jun shape
    assert l == 492.9 and h == 505.72 and len(fl) == 1 and fl[0].startswith("low")
def test_both_sides_clipped():
    h, l, fl = P.hl_check(100.0, 120.0, 80.0, 100.0, 1.0)
    assert (h, l) == (100.0, 100.0) and len(fl) == 2
def test_inside_3atr_is_kept_even_if_over_5pct():
    h, l, fl = P.hl_check(100.0, 110.0, 99.0, 101.0, 4.0)   # excess 9 < 3 x 4 = 12
    assert (h, l, fl) == (110.0, 99.0, [])
def test_over_3atr_but_under_5pct_is_kept():
    h, l, fl = P.hl_check(100.0, 104.5, 99.5, 101.0, 1.0)   # excess 3.5 > 3xATR 3, but < 5% of 101 = 5.05 (tick-noise guard)
    assert (h, l, fl) == (104.5, 99.5, [])
def test_no_atr_means_no_clip():
    assert P.hl_check(100.0, 200.0, 50.0, 101.0, None) == (200.0, 50.0, [])
    assert P.hl_check(100.0, 200.0, 50.0, 101.0, 0) == (200.0, 50.0, [])
def test_custom_bounds():
    assert P.hl_check(100.0, 108.0, 99.0, 101.0, 1.0, k=3.0, rel=0.05)[0] == 101.0
    assert P.hl_check(100.0, 108.0, 99.0, 101.0, 1.0, k=3.0, rel=0.10)[0] == 108.0
# ---------- sane_rows / put_bars on a temp DB ----------
def _db():
    c = P.con(os.path.join(tempfile.mkdtemp(), "pit_test.sqlite"))
    d0 = dt.date(2026, 6, 1); rows = []
    for i in range(20):
        d = (d0 + dt.timedelta(days=i)).isoformat(); rows.append((d, 100.0, 101.0, 99.0, 100.0, 1e6))
    for s in ("TEST.L", "TESTUS", "KRAKEN:XBTUSD", "BINANCE:FOOUSDT"):
        c.executemany("INSERT INTO bars VALUES(?,?,?,?,?,?,?,?,?)", [(s, *r, "2026-06-01T00:00:00+00:00", "fixture") for r in rows])
    return c
SPIKE = ("2026-06-25", 100.0, 140.0, 70.0, 100.5, 1e6)   # ATR ~2: both extremes far beyond max(6, 5%)
NORMAL = ("2026-06-26", 100.0, 102.5, 98.0, 101.0, 1e6)
def test_sane_rows_flags_equity_keeps_raw_and_logs_reject():   # 2026-10-29 flagmode: raw kept, flag logged + queued in bar_review
    c = _db(); n = len(P.HL_REJECTS)
    out = P.sane_rows(c, "TEST.L", [SPIKE, NORMAL])
    assert out[0] == SPIKE
    assert c.execute("SELECT status FROM bar_review WHERE sym='TEST.L' AND date='2026-06-25'").fetchone() == ("pending",)
    assert out[1] == NORMAL
    assert P.HL_REJECTS[n][0] == "TEST.L" and P.HL_REJECTS[n][1] == "2026-06-25" and len(P.HL_REJECTS) == n + 1
def test_sane_rows_crypto_exempt():
    c = _db()
    for s in ("KRAKEN:XBTUSD", "BINANCE:FOOUSDT"):
        assert P.sane_rows(c, s, [SPIKE]) == [SPIKE], s
def test_sane_rows_short_history_kept():
    c = P.con(os.path.join(tempfile.mkdtemp(), "pit_test.sqlite"))   # < 6 prior bars -> no ATR -> keep (new listings)
    assert P.sane_rows(c, "NEWCO", [SPIKE]) == [SPIKE]
def test_put_bars_stores_raw_equity_and_raw_crypto():   # 2026-10-29 flagmode
    c = _db()
    P.put_bars(c, "TESTUS", [SPIKE], "test"); P.put_bars(c, "KRAKEN:XBTUSD", [SPIKE], "test")
    assert c.execute("SELECT h,l,c FROM bars WHERE sym='TESTUS' AND date='2026-06-25'").fetchone() == (140.0, 70.0, 100.5)
    assert c.execute("SELECT h,l,c FROM bars WHERE sym='KRAKEN:XBTUSD' AND date='2026-06-25'").fetchone() == (140.0, 70.0, 100.5)
def test_venue_of_crypto():
    assert P.venue_of("KRAKEN:XBTUSD") == "CRYPTO" and P.venue_of("BARC.L") == "LSE" and P.venue_of("AEHL") == "US"
