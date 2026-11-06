"""Breadth expand (Brett 2026-10-29): Dex full-DB path wired into desk.pack. PAPER only.

The list tests read the 6 Nov historical universe fixture. They keep the original
thresholds. They do not claim the book is covered today."""
import datetime as dt, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import desk as D, pit as P, scan_universe as S
from veteran_desk.tests.isolated_pit import open_pit, seed_bars
FIX = os.path.join(os.path.dirname(__file__), "fixtures", "scan_universe_20261106.json")
BANKS = ("AAMI", "AB", "ABCB", "ABR", "ABTC", "ABX", "ACGL", "ACGLN", "ACGLO", "ACIC", "ACNB", "ACP", "ACT", "ACV", "ADX", "AEF", "AEG", "AEXA", "AFB", "AFG", "AFL", "AFRM", "AGD", "AGM")
CRYPTO = ("AAVEUSD", "ALGOUSD", "ATOMUSD", "AVAXUSD", "BCHUSD", "COMPUSD", "CRVUSD", "DASHUSD", "DOGEUSD", "DOTUSD", "EGLDUSD", "ENJUSD", "EOSUSD", "ETCUSD", "FILUSD", "FLOWUSD", "GRTUSD", "ICPUSD", "INJUSD", "KSMUSD", "LDOUSD", "MANAUSD", "NEARUSD", "SANDUSD", "SNXUSD")

def _use_historical_universe(monkeypatch):
    monkeypatch.setattr(S, "OUT", FIX)

def test_scan_file_exists_and_cleared(monkeypatch):
    _use_historical_universe(monkeypatch)
    J = S.load(); assert J and J.get("cleared", 0) >= 5000, J.get("cleared") if J else None
    assert J.get("scanned", 0) >= 10000

def test_seat_list_broad_ge_500(monkeypatch):
    _use_historical_universe(monkeypatch)
    for seat in ("price_action", "trend", "pit:all"):
        L, Y, Z = S.seat_list(seat); assert len(L) >= 500, (seat, len(L)); assert Z >= 500

def test_seat_list_sector_or_full(monkeypatch):
    _use_historical_universe(monkeypatch)
    L, Y, Z = S.seat_list("banks"); assert len(L) >= 50, len(L)  # full liquid Financials set

def test_pack_scan_line_and_topn(tmp_path, monkeypatch):
    _use_historical_universe(monkeypatch)
    c = open_pit(tmp_path, monkeypatch)
    for sym in BANKS: seed_bars(c, sym, n=80, close=40.0, spread=0.4)
    asof = dt.datetime.now(dt.timezone.utc)
    pk = D.pack(c, json.load(open(os.path.join(P.BASE, "veteran_desk/seats/banks.json"))), asof)
    assert pk.get("scan") and "cleared A$500k" in pk["scan"], pk.get("scan")
    assert 20 <= len(pk["instruments"]) <= 160, len(pk["instruments"])   # tfo_seatpipe_3_20261030: top 40 -> ~120

def test_pack_trend_not_swamped_by_crypto(tmp_path, monkeypatch):
    _use_historical_universe(monkeypatch)
    c = open_pit(tmp_path, monkeypatch); asof = dt.datetime.now(dt.timezone.utc)
    pk = D.pack(c, json.load(open(os.path.join(P.BASE, "veteran_desk/seats/trend.json"))), asof)
    assert "scanned" in (pk.get("scan") or "")
    assert len(pk["instruments"]) < 200, len(pk["instruments"])

def test_crypto_seat_uses_kraken_24h(tmp_path, monkeypatch):
    _use_historical_universe(monkeypatch)
    sweep = tmp_path / "kraken_24h_all.json"
    sweep.write_text(json.dumps(dict(utc="2026-11-06T08:00:00+00:00", pairs=[dict(alt=a, liquid=True) for a in CRYPTO])))
    monkeypatch.setattr(S, "KRAKEN_24H", str(sweep))
    c = open_pit(tmp_path, monkeypatch)
    for alt in CRYPTO: seed_bars(c, "KRAKEN:" + alt, n=80, close=20.0, spread=0.2)
    asof = dt.datetime.now(dt.timezone.utc)
    pk = D.pack(c, json.load(open(os.path.join(P.BASE, "veteran_desk/seats/crypto.json"))), asof)
    assert 20 < len(pk["instruments"]) < 200 and pk["scan"].startswith("scanned ") and "cleared A$500k" in pk["scan"]   # tfo_seatpipe_2_20261030 unified format
    assert not any(f["sym"].startswith(("KRAKEN:USDT", "KRAKEN:USDC")) for f in pk["instruments"])

def test_dead_names_dropped(monkeypatch):
    _use_historical_universe(monkeypatch)
    from veteran_desk import scan_universe as SU
    names = {x["sym"] for x in SU.load()["names"]}
    assert not (names & SU._dead())

def test_news_has_asia(monkeypatch):
    _use_historical_universe(monkeypatch)
    from veteran_desk import scan_universe as SU
    L, _, _ = SU.seat_list("news_filings")
    assert sum(1 for s in L if s.endswith((".T", ".HK", ".KS", ".TW", ".SI"))) > 300
