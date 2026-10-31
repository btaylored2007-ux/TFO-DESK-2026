"""Pytest default: a fresh firm-risk snapshot so existing entry tests still open.

Production has no such stand-in. A missing service refuses new risk. Tests that
cover the refuse path replace QUERY_FN or pass a snapshot.
"""
import datetime as dt

import pytest


def _allow(path=None, url=None, now=None, timeout=1.5, opener=None, stale_sec=None):
    from veteran_desk.exec_speed.common import iso
    now = now or dt.datetime.now(dt.timezone.utc)
    book = {"ok": True, "us_beta_usd": 0.0, "us_beta_cap_usd": 1e15,
            "equity_aud": 1e15, "open_risk_aud": 0.0}
    return {
        "ok": True, "paper_only": True, "reachable": True, "utc": iso(now),
        "new_entries_allowed": True, "new_entries_halted": False, "exits_allowed": True,
        "level": "OK", "firm_equity_aud": 1e15, "firm_open_risk_aud": 0.0,
        "firm_heat": 0.0, "heat_cap": 0.15, "reason": "pass",
        "books": {"veteran": dict(book), "thf": dict(book)},
        "oanda": {"ok": True, "open_n": 0, "open_risk": 0.0, "risk_cap": 0.02, "max_open": 4},
    }


@pytest.fixture(autouse=True)
def _firm_risk_test_default(monkeypatch, tmp_path):
    from veteran_desk.exec_speed import firm_risk_service as R
    monkeypatch.setattr(R, "QUERY_FN", _allow)
    monkeypatch.setattr(R, "DECISION_LOG", str(tmp_path / "firm_risk_decisions.jsonl"))
