"""tfo_datalayer_20261030 (Brett HARD RULE 20:59 WITA): fail if a NEW direct Yahoo/yfinance/quoteSummary call appears outside the
fallback layer. Legacy call sites are frozen in yf_callsite_baseline.json (count per file) and are being migrated; a file may only go
DOWN. New production .py files must use veteran_desk/data_layer.py (paid first) instead. To retire a file: lower/remove its entry."""
import json, os, re
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PAT = re.compile(r"import yfinance|from yfinance|\byf\.(download|Ticker|Tickers|Search)\b|quoteSummary|finance\.yahoo\.com")
FALLBACK_LAYER = {"veteran_desk/data_layer.py", "veteran_desk/paid_first.py", "veteran_desk/yf_guard.py"}
# Byte-identical copy of veteran_desk/book.py kept for the bank hash. Not a new caller.
PINNED_BOOK_COPIES = {
    "docs/live_bank07/book.py",
    "deploy/paper_rules_20261107/base/veteran_desk/book.py",
    "deploy/paper_rules_20261107/payload/veteran_desk/book.py",
}
SKIP_DIRS = ("research", "work", "backups", "reports", "state", "staged_fixes", "tests", "tests_watchlist", "data", ".claude", ".scratch",
             ".staging_gates", "handover", "static", "logs", "__pycache__", "idea_feeds/.scratch", "ops")
def scan():
    out = {}
    for d, ds, fs in os.walk(ROOT):
        rel = os.path.relpath(d, ROOT)
        if rel != "." and (any(x in SKIP_DIRS or x.startswith(".") for x in rel.split(os.sep)) or rel in SKIP_DIRS): ds[:] = []; continue
        for f in fs:
            if not f.endswith(".py") or f.startswith("test_") or ".bak" in f: continue
            p = os.path.normpath(os.path.join(rel, f))
            if p in FALLBACK_LAYER or p in PINNED_BOOK_COPIES: continue
            try: n = len(PAT.findall(open(os.path.join(d, f), errors="ignore").read()))
            except Exception: continue
            if n: out[p] = n
    return out
def test_policy_book_copies_do_not_add_yahoo_calls():
    pin = len(PAT.findall(open(os.path.join(ROOT, "docs/live_bank07/book.py"), errors="ignore").read()))
    for rel in PINNED_BOOK_COPIES:
        count = len(PAT.findall(open(os.path.join(ROOT, rel), errors="ignore").read()))
        assert count == pin


def test_no_new_direct_yahoo_calls():
    base = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "yf_callsite_baseline.json")))
    now = scan(); bad = {p: (n, base.get(p, 0)) for p, n in now.items() if n > base.get(p, 0)}
    assert not bad, "NEW direct Yahoo/yfinance calls outside veteran_desk/data_layer.py (file: (now, allowed)): %s" % bad
