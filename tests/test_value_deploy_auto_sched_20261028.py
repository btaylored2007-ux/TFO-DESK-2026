import os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import value_deploy_auto as A
U = dt.timezone.utc
def test_us_open_edt(): assert "US" in A.open_markets(dt.datetime(2026, 10, 28, 14, 0, tzinfo=U))
def test_us_closed_pre_buffer(): assert "US" not in A.open_markets(dt.datetime(2026, 10, 28, 13, 40, tzinfo=U))
def test_asx_aedt(): assert A.open_markets(dt.datetime(2026, 10, 28, 1, 0, tzinfo=U)) == ["ASX", "JP"]
def test_weekend(): assert A.open_markets(dt.datetime(2026, 10, 31, 14, 0, tzinfo=U)) == []
def test_lse(): assert "LSE" in A.open_markets(dt.datetime(2026, 10, 28, 10, 0, tzinfo=U))
