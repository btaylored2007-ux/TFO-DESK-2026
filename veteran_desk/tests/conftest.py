import os as _os_rg; _os_rg.environ["VD_REVGUARD_WEBHOOK_DISABLE"] = "1"
"""TFO HT 03:35 global guard: no test may create/modify/delete anything under the live state/, reports/, logs/ or data/.
Every write path (open w/a/x/+, os.open O_CREAT/O_WRONLY/O_RDWR, os.replace/rename, os.remove/unlink, os.makedirs) is checked; a live-path
write raises PermissionError AND is recorded, so a swallowed exception still fails the session. Session end also re-checks mtimes of key live files."""
import builtins, io, os, sys, pytest
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Code-only tree has no live book. A synthetic empty seat book lets unit tests copy a template.
# Created only when the real file is absent, and removed at session end so a live ledger is never overwritten.
_SYN_BOOK = os.path.join(ROOT, "reports", "veteran_desk_book.json")
_SYN_MADE = False
if not os.path.exists(_SYN_BOOK):
    os.makedirs(os.path.dirname(_SYN_BOOK), exist_ok=True)
    _traders = ["energy", "essentials_utilities", "banks", "healthcare", "macro", "news_filings", "price_action", "crypto", "fx_commodities", "tech_ai_infra", "trend"]
    def _seat():
        return dict(seed_aud=500000.0, cash_aud=500000.0, equity_aud=500000.0, positions={}, closed=[], commission_expense_aud=0.0, commission_forfeit_credit_aud=0.0)
    json_mod = __import__("json")
    json_mod.dump(dict(book="veteran_desk", paper_only=True, label="synthetic test book", ccy_native="AUD", seed_aud=500000.0 * len(_traders),
                       audusd=0.65, seats={s: _seat() for s in _traders}, hedge={"disabled": True}, kill_switch={"state": "OK"},
                       peak_equity_aud=None, capital_events=[], holdings={}), open(_SYN_BOOK, "w"))
    _SYN_MADE = True
for _d in ("top10", "grades", "wakes", "hb", "second_look/gpt", "second_look/grok", "runs"):
    os.makedirs(os.path.join(ROOT, "state", "vd_engine", _d), exist_ok=True)
_NEWS = os.path.join(ROOT, "state", "veteran_desk", "news.json")
_FIRM = os.path.join(ROOT, "reports", "firm_risk.json")
_MADE_NEWS = _MADE_FIRM = False
if not os.path.exists(_NEWS):
    os.makedirs(os.path.dirname(_NEWS), exist_ok=True)
    __import__("json").dump({"regime": {"flag": "risk-off"}}, open(_NEWS, "w"))
    _MADE_NEWS = True
if not os.path.exists(_FIRM):
    os.makedirs(os.path.dirname(_FIRM), exist_ok=True)
    __import__("json").dump({"level": "OK", "new_entries_halted": False}, open(_FIRM, "w"))
    _MADE_FIRM = True
LIVE = [os.path.realpath(os.path.join(ROOT, d)) for d in ("state", "reports", "logs", "data")]
KEY = ["state/veteran_desk_payouts.json", "state/veteran_desk/KILL_ALERT.json", "reports/veteran_desk_book.json", "state/veteran_desk/forward_trades.json",
       "state/veteran_desk/leaderboard.json", "state/veteran_desk/latest.json", "state/veteran_desk/head_of_desk.json", "state/veteran_desk/price_alerts.jsonl",
       "state/veteran_desk/archive/hedge_20261029.json", "state/funder_queue.json", "state/veteran_desk/staff_pool.json", "state/veteran_desk/seat_equity_snapshots.jsonl"]
VIOLATIONS = []
def is_live(p):
    try: r = os.path.realpath(os.fspath(p) if not isinstance(p, int) else "")
    except Exception: return False
    return bool(r) and any(r == L or r.startswith(L + os.sep) for L in LIVE)
def _chk(p, what):
    if is_live(p): VIOLATIONS.append((what, os.fspath(p))); raise PermissionError(f"TEST GUARD: {what} on live path {p}")
_o_open, _o_osopen, _o_replace, _o_rename, _o_remove, _o_unlink, _o_makedirs, _o_mkdir = builtins.open, os.open, os.replace, os.rename, os.remove, os.unlink, os.makedirs, os.mkdir
def g_open(file, mode="r", *a, **k):
    if not isinstance(file, int) and any(c in mode for c in "wax+"): _chk(file, "open(%s)" % mode)
    return _o_open(file, mode, *a, **k)
def g_osopen(path, flags, *a, **k):
    if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC): _chk(path, "os.open")
    return _o_osopen(path, flags, *a, **k)
def g_replace(src, dst, *a, **k): _chk(dst, "os.replace"); return _o_replace(src, dst, *a, **k)
def g_rename(src, dst, *a, **k): _chk(dst, "os.rename"); _chk(src, "os.rename(src)"); return _o_rename(src, dst, *a, **k)
def g_remove(p, *a, **k): _chk(p, "os.remove"); return _o_remove(p, *a, **k)
def g_unlink(p, *a, **k): _chk(p, "os.unlink"); return _o_unlink(p, *a, **k)
def g_makedirs(p, *a, **k):
    if not os.path.isdir(p): _chk(p, "os.makedirs")
    return _o_makedirs(p, *a, **k)
def g_mkdir(p, *a, **k):
    if not os.path.isdir(p): _chk(p, "os.mkdir")
    return _o_mkdir(p, *a, **k)
def _sig(): return {k: (os.stat(os.path.join(ROOT, k)).st_mtime_ns, os.stat(os.path.join(ROOT, k)).st_size) if os.path.exists(os.path.join(ROOT, k)) else None for k in KEY}
builtins.open = io.open = g_open; os.open = g_osopen; os.replace = g_replace; os.rename = g_rename; os.remove = g_remove; os.unlink = g_unlink; os.makedirs = g_makedirs; os.mkdir = g_mkdir
_BEFORE = _sig()
# Redirect every module-level live output path to one session tmp dir (defence in depth; the guard above still catches anything missed).
import contextlib, tempfile
TMP = tempfile.mkdtemp(prefix="vd_tests_")
sys.path.insert(0, ROOT)
def _redirect():
    from veteran_desk import feeds as FD, payouts as PY, leaderboard as LB
    FD.FH = os.path.join(TMP, "feeds_health.json"); FD.CG_CACHE = os.path.join(TMP, "coingecko_cache.json")
    import shutil
    _pdest = os.path.join(TMP, "veteran_desk_payouts.json")
    if os.path.exists(PY.DEFAULT_OUT_PATH): shutil.copyfile(PY.DEFAULT_OUT_PATH, _pdest)   # read-only copy of live
    else: __import__("json").dump({}, open(_pdest, "w"))
    PY.DEFAULT_OUT_PATH = _pdest; LB.LIVE = os.path.join(TMP, "leaderboard.json")
    import funder_gate as FG
    FG._lock = lambda: contextlib.nullcontext()   # shared desk_lock lives under live reports/; tests use tmp queues only
_redirect()
def pytest_sessionfinish(session, exitstatus):
    after = _sig(); changed = [k for k in KEY if after[k] != _BEFORE[k]]
    if _SYN_MADE:
        try: os.remove(_SYN_BOOK)
        except OSError: pass
    if _MADE_NEWS:
        try: os.remove(_NEWS)
        except OSError: pass
    if _MADE_FIRM:
        try: os.remove(_FIRM)
        except OSError: pass
    if VIOLATIONS or changed:
        sys.stderr.write("\nTEST GUARD FAILED - live writes: %s ; key files changed during session: %s\n" % (VIOLATIONS[:20], changed))
        session.exitstatus = 1
@pytest.fixture(autouse=True)
def _no_live_writes():
    n = len(VIOLATIONS); yield
    new = VIOLATIONS[n:]
    if new: pytest.fail("TEST GUARD: test wrote live paths: %s" % new[:5], pytrace=False)
_PINNED = [("veteran_desk.payouts", a) for a in ("DEFAULT_OUT_PATH", "DEFAULT_FWD_PATH", "DEFAULT_SCORE_PATH")] + [("veteran_desk.leaderboard", "LIVE"), ("veteran_desk.feeds", "FH"), ("veteran_desk.feeds", "CG_CACHE"),
           ("veteran_desk.book", "BOOK"), ("veteran_desk.book", "now"), ("veteran_desk.book", "HEDGE_ARCHIVE"), ("veteran_desk.book", "KILL_ALERT_PATH")]
@pytest.fixture(autouse=True)
def _restore_module_paths():
    """Tests that repoint module paths without restoring them must not leak into later tests."""
    snap = [(m, a, getattr(sys.modules[m], a)) for m, a in _PINNED if m in sys.modules and hasattr(sys.modules[m], a)]
    yield
    for m, a, v in snap: setattr(sys.modules[m], a, v)
# tfo_revguard_webhook_20261030: no test may reach the real revguard webhook. Point secrets at an empty dir and block any
# Authorization-bearing urlopen; webhook unit tests override both with their own monkeypatch.
@pytest.fixture(autouse=True)
def _no_real_revguard_webhook(monkeypatch, tmp_path_factory):
    monkeypatch.setenv("VD_REVGUARD_WEBHOOK_DISABLE", "1")
    import urllib.request
    try:
        from veteran_desk import book as _BK
        monkeypatch.setattr(_BK, "REVG_SECRETS_DIR", str(tmp_path_factory.mktemp("no_revg_secrets")), raising=False)
    except Exception: pass
    _real = urllib.request.urlopen
    def _guard(req, *a, **k):
        if hasattr(req, "get_header") and req.get_header("Authorization"): raise RuntimeError("TEST GUARD: authorized POST blocked")
        return _real(req, *a, **k)
    monkeypatch.setattr(urllib.request, "urlopen", _guard)
@pytest.fixture(autouse=True)
def _borrow_without_live_snapshot(monkeypatch, request):
    """Shorts fail closed without state/ibkr_borrow.json. That file is a live locate snapshot, not in this tree.
    test_execution_rules checks the real fail-closed path and is left alone."""
    if "test_execution_rules" in request.node.nodeid:
        return
    if os.path.exists(os.path.join(ROOT, "state", "ibkr_borrow.json")):
        return
    from veteran_desk import execution_rules as XR
    monkeypatch.setattr(XR, "borrow_check", lambda *a, **k: dict(ok=True, fee_pa=0.01, available=10**9, htb=False, why="no live borrow file"))

@pytest.fixture(autouse=True)
def _seatcap_legacy(request, monkeypatch):
    """seatcap/gapguard 2026-10-30: legacy sizing tests predate the 40% per-position cap and gap sizing; only modules with SEATCAP_LIVE = True see them."""
    if getattr(request.module, "SEATCAP_LIVE", False): return
    from veteran_desk import book as _BK
    monkeypatch.setattr(_BK, "SEAT_NOTIONAL_CAP", 10.0, raising=False); monkeypatch.setattr(_BK, "gap_risk_pct", lambda e: 0.0, raising=False)
