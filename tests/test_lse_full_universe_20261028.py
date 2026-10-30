"""Brett 2026-10-28 20:31 WITA: LSE full main+AIM listing, FTSE REIT regex repair, point-in-time open-bar guard. Offline."""
import os, sys, json, tempfile, datetime as dt
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import universe_asia as U, asia_markets as AM

def test_lse_all_listed_paging_and_fund_filter(monkeypatch):
    td = tempfile.mkdtemp(); monkeypatch.setattr(U, "CD", td)
    pages = {0: [dict(symbol="AAA.L", quoteType="EQUITY", longName="Alpha plc"), dict(symbol="BBB.L", quoteType="EQUITY", longName="Beta Investment Trust plc")],
             2: [dict(symbol="CCC.L", quoteType="EQUITY", longName="Gamma VCT"), dict(symbol="DDD.IL", quoteType="EQUITY", longName="Delta"), dict(symbol="EEE.L", quoteType="ETF", longName="E ETF")]}
    calls = []
    def fetch(off): calls.append(off); return dict(total=5, quotes=pages.get(off, []))
    d = U.lse_all_listed(fetch=fetch)
    assert calls == [0, 2] and list(d.sym) == ["AAA.L"] and set(d.tier) == {"lse_other"}
    assert json.load(open(os.path.join(td, "lse", "all_listed.json")))["total"] == 5

def test_reit_not_excluded_trust_excluded():
    rx = r"(?<!Real Estate )Investment Trusts?\b|Closed End"
    s = pd.Series(["Real Estate Investment Trusts", "Investment Trusts", "Closed End Investments", "Banks"])
    assert list(s.str.contains(rx, case=False, regex=True)) == [False, True, True, False]

def test_drop_open_bar_pit():
    M = AM.MARKETS["lse"]; idx = pd.to_datetime(["2026-10-26", "2026-10-27", "2026-10-28"])
    d = pd.DataFrame({"x": [1, 2, 3]}, index=idx)
    mid = dt.datetime(2026, 10, 28, 12, 40, tzinfo=dt.timezone.utc)      # 12:40 London, session open -> drop 28 Oct
    assert list(U.drop_open_bar(d, M, now=mid).x) == [1, 2]
    after = dt.datetime(2026, 10, 28, 17, 0, tzinfo=dt.timezone.utc)     # after 16:35 London -> keep
    assert list(U.drop_open_bar(d, M, now=after).x) == [1, 2, 3]
    pre = dt.datetime(2026, 10, 28, 5, 15, tzinfo=dt.timezone.utc)       # 13:15 WITA pre-open
    assert list(U.drop_open_bar(d, M, now=pre).x) == [1, 2]
