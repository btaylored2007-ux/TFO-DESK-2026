"""Breadth expand (Brett 2026-10-29): Dex full-DB path wired into desk.pack. PAPER only."""
import datetime as dt, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import desk as D, pit as P, scan_universe as S
import pytest

def _scan_or_skip():
    J = S.load()
    if not J or not J.get("cleared"):
        pytest.skip("scan universe is not in the code-only tree (live sweep file). See TEST_TRIAGE.md.")
    return J

def test_scan_file_exists_and_cleared():
    J = _scan_or_skip(); assert J and J.get("cleared", 0) >= 5000, J.get("cleared") if J else None
    assert J.get("scanned", 0) >= 10000

def test_seat_list_broad_ge_500():
    _scan_or_skip()
    for seat in ("price_action", "trend", "pit:all"):
        L, Y, Z = S.seat_list(seat); assert len(L) >= 500, (seat, len(L)); assert Z >= 500

def test_seat_list_sector_or_full():
    _scan_or_skip()
    L, Y, Z = S.seat_list("banks"); assert len(L) >= 50, len(L)  # full liquid Financials set

def test_pack_scan_line_and_topn():
    _scan_or_skip()
    c = P.con(); asof = dt.datetime.now(dt.timezone.utc)
    pk = D.pack(c, json.load(open(os.path.join(P.BASE, "veteran_desk/seats/banks.json"))), asof)
    assert pk.get("scan") and "cleared A$500k" in pk["scan"], pk.get("scan")
    assert 20 <= len(pk["instruments"]) <= 160, len(pk["instruments"])   # tfo_seatpipe_3_20261030: top 40 -> ~120

def test_pack_trend_not_swamped_by_crypto():
    c = P.con(); asof = dt.datetime.now(dt.timezone.utc)
    pk = D.pack(c, json.load(open(os.path.join(P.BASE, "veteran_desk/seats/trend.json"))), asof)
    assert "scanned" in (pk.get("scan") or "")
    assert len(pk["instruments"]) < 200, len(pk["instruments"])

def test_crypto_seat_uses_kraken_24h():
    _scan_or_skip()
    c = P.con(); asof = dt.datetime.now(dt.timezone.utc)
    pk = D.pack(c, json.load(open(os.path.join(P.BASE, "veteran_desk/seats/crypto.json"))), asof)
    assert 20 < len(pk["instruments"]) < 200 and pk["scan"].startswith("scanned ") and "cleared A$500k" in pk["scan"]   # tfo_seatpipe_2_20261030 unified format
    assert not any(f["sym"].startswith(("KRAKEN:USDT", "KRAKEN:USDC")) for f in pk["instruments"])

def test_dead_names_dropped():
    _scan_or_skip()
    from veteran_desk import scan_universe as SU
    names = {x["sym"] for x in SU.load()["names"]}
    assert not (names & SU._dead())

def test_news_has_asia():
    _scan_or_skip()
    from veteran_desk import scan_universe as SU
    L, _, _ = SU.seat_list("news_filings")
    assert sum(1 for s in L if s.endswith((".T", ".HK", ".KS", ".TW", ".SI"))) > 300
