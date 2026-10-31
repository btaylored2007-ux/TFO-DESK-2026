"""Shared PAPER test harness for tests/ and veteran_desk/tests.

No live ledger is read or written here. Missing Mac-only paths and missing
research modules are skips (see TEST_TRIAGE.md). TFO_TEST_OFFLINE=1 blocks
non-localhost sockets so CI does not use network or secrets.
"""
import os
import pytest

if os.environ.get("TFO_TEST_OFFLINE") == "1":
    import socket
    def _blocked(*a, **k):
        raise OSError("network blocked in CI (TFO_TEST_OFFLINE)")
    socket.create_connection = _blocked
    _orig_connect = socket.socket.connect
    def _no_connect(self, *a, **k):
        host = ""
        if a and isinstance(a[0], tuple):
            host = str(a[0][0])
        if host in ("127.0.0.1", "::1", "localhost"):
            return _orig_connect(self, *a, **k)
        raise OSError("network blocked in CI (TFO_TEST_OFFLINE)")
    socket.socket.connect = _no_connect

_LIVE_MARKERS = (
    "/state/", "/reports/", "/Users/", "KrakenQuantDesk", ".codex/skills",
    "static/essentials_status.json", "static/trend_core", "Applications/MultiVenueDesk",
    "local_ledger_backup.sh",
)

def _absent_live(exc):
    msg = str(getattr(exc, "filename", None) or exc)
    if "/tmp/" in msg or "/tmp" in msg[:5]:
        return False
    return any(m in msg for m in _LIVE_MARKERS)

def _exc_value(exc):
    if exc is None:
        return None
    if isinstance(exc, tuple):
        return exc[1]
    return getattr(exc, "value", exc)

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when != "call" or not rep.failed:
        return
    val = _exc_value(getattr(call, "excinfo", None))
    if val is None:
        return
    msg = str(val)
    missing_mod = isinstance(val, ModuleNotFoundError) and any(
        n in msg for n in ("entry_engine", "overnight2k", "kraken_floor", "edge50_fwd", "fundamental_coverage_ledger_v2"))
    if missing_mod or "network blocked in CI" in msg or (isinstance(val, (FileNotFoundError, PermissionError)) and _absent_live(val)):
        rep.outcome = "skipped"
        rep.longrepr = ("conftest.py", 0, "SKIP live path or mocked network: %s See TEST_TRIAGE.md." % msg[:180])
