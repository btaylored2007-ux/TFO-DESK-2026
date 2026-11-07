"""Feed closure stays on the captured production bytes and refuses every other hash."""
import datetime as dt
import hashlib
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "deploy" / "lesson_feedclose_20261107"
BOOK = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"
REPLACED = (
    "veteran_desk/lesson_trace.py",
    "veteran_desk/seat_brain.py",
    "veteran_desk/thf_round.py",
    "veteran_desk/thf_msgdesk.py",
    "thf_feedkeeper.py",
    "veteran_desk/thf_home.py",
)
WEEKDAY = dt.datetime(2026, 11, 4, 19, 0, tzinfo=dt.timezone.utc)  # 14:00 America/New_York
THANKSGIVING = dt.datetime(2026, 11, 26, 19, 0, tzinfo=dt.timezone.utc)
JP_HOLIDAY = dt.datetime(2026, 12, 31, 1, 0, tzinfo=dt.timezone.utc)  # 10:00 Asia/Tokyo


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stage(tmp_path):
    root = tmp_path / "stage"
    for rel in REPLACED + ("veteran_desk/feed_health.py",):
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(PKG / "base" / rel, dest)
    shutil.copy(REPO / "docs" / "live_bank07" / "book.py", root / "veteran_desk" / "book.py")
    assert _sha(root / "veteran_desk" / "book.py") == BOOK
    assert not (root / "veteran_desk" / "feed_status_facts.py").exists()
    return root


def _run(root, *args):
    return subprocess.run([sys.executable, str(PKG / "install.py"), "--root", str(root), *args],
                          capture_output=True, text=True)


def test_dry_run_names_each_captured_file_and_the_new_file_guard(tmp_path):
    root = _stage(tmp_path)
    dry = _run(root)
    assert dry.returncode == 0, dry.stdout
    for rel in REPLACED:
        assert "REPLACE %s" % rel in dry.stdout
    assert "UNCHANGED veteran_desk/feed_health.py" in dry.stdout
    assert "INSTALL_NEW veteran_desk/feed_status_facts.py" in dry.stdout
    assert "NEW_FILE_GUARDED" in dry.stdout
    assert not (root / "veteran_desk" / "feed_status_facts.py").exists()


def test_apply_uses_captured_feed_health_and_rollback_removes_only_the_new_file(tmp_path):
    root = _stage(tmp_path)
    health_before = (root / "veteran_desk" / "feed_health.py").read_bytes()
    staged = _run(root, "--staging", "--apply")
    assert staged.returncode == 0, staged.stdout
    for rel in REPLACED:
        assert _sha(root / rel) == _sha(PKG / "payload" / rel)
    assert (root / "veteran_desk" / "feed_health.py").read_bytes() == health_before
    assert _sha(root / "veteran_desk" / "feed_status_facts.py") == _sha(PKG / "payload" / "veteran_desk" / "feed_status_facts.py")
    assert "feed_status_facts" not in (PKG / "base" / "thf_feedkeeper.py").read_text()
    assert "from veteran_desk.feed_status_facts import annotate, summarize" in (root / "thf_feedkeeper.py").read_text()
    health = _load(root / "veteran_desk" / "feed_health.py", "captured_feed_health_close")
    facts = _load(root / "veteran_desk" / "feed_status_facts.py", "staged_feed_status_facts")
    home = _load(root / "veteran_desk" / "thf_home.py", "staged_thf_home_close")
    saved = sys.modules.get("veteran_desk.feed_health")
    sys.modules["veteran_desk.feed_health"] = health
    try:
        assert health.us_rth_open(WEEKDAY) is True
        assert health.us_rth_open(THANKSGIVING) is False
        assert health.asia_session_open("JP", JP_HOLIDAY, ("Asia/Tokyo", "09:00", "15:00")) is False
        open_row = facts.classify("yahoo_quotes", dict(status="ok", age_s=5, max_age_s=30, file_mtime="2026-11-04T19:00:00+00:00"), WEEKDAY)
        assert open_row["facts"]["heuristic_session_expected"] is True
        assert open_row["facts"]["session_expected"] is None
        assert open_row["facts"]["fresh_usable"] is None
        assert open_row["facts"]["freshness_source"] == "unknown_mtime_ignored"
        assert open_row["facts"]["entitlement"] == "unknown"
        assert open_row["facts"]["calendar"] == "heuristic_not_venue_calendar"
        holiday = facts.classify("yahoo_quotes", dict(status="ok", authorized=False, last_status=dict(code=422, message="refused")), THANKSGIVING)
        assert holiday["facts"]["heuristic_session_expected"] is False
        assert holiday["facts"]["session_expected"] is None
        assert holiday["facts"]["entitlement"] == "denied"
        assert holiday["facts"]["last_failure"]["code"] == 422
        assert holiday["facts"]["current_health"] == "ok"
        jp = facts.classify("paid_bars", dict(status="ok"), JP_HOLIDAY)
        assert jp["facts"]["heuristic_session_expected"] is False and jp["facts"]["session_expected"] is None
    finally:
        if saved is None:
            sys.modules.pop("veteran_desk.feed_health", None)
        else:
            sys.modules["veteran_desk.feed_health"] = saved
    plain = home.feed_rows(dict(feeds={"yahoo_quotes": dict(status="ok", age_s=5)}))
    assert plain[0]["facts"] == "" and plain[0]["usable"] is None
    shown = home.feed_rows(dict(feeds={"yahoo_quotes": open_row}))
    assert "session unknown" in shown[0]["facts"]
    assert "heuristic session expected" in shown[0]["facts"]
    assert "entitlement unknown" in shown[0]["facts"]
    assert shown[0]["usable"] is None
    back = _run(root, "--staging", "--rollback")
    assert back.returncode == 0 and "ROLLED_BACK" in back.stdout
    for rel in REPLACED:
        assert _sha(root / rel) == _sha(PKG / "base" / rel)
    assert (root / "veteran_desk" / "feed_health.py").read_bytes() == health_before
    assert not (root / "veteran_desk" / "feed_status_facts.py").exists()
    assert _sha(root / "veteran_desk" / "book.py") == BOOK


def test_unknown_hash_partial_failure_and_existing_new_file_do_not_write(tmp_path):
    root = _stage(tmp_path)
    keeper = root / "thf_feedkeeper.py"
    original = keeper.read_bytes()
    keeper.write_bytes(original + b"\n# unknown\n")
    blocked = _run(root, "--staging", "--apply")
    assert blocked.returncode == 2 and "BLOCKED thf_feedkeeper.py" in blocked.stdout
    assert keeper.read_bytes() == original + b"\n# unknown\n"
    assert _sha(root / "veteran_desk" / "lesson_trace.py") == _sha(PKG / "base" / "veteran_desk" / "lesson_trace.py")
    keeper.write_bytes(original)
    conflict = root / "veteran_desk" / "feed_status_facts.py"
    conflict.write_text("# not the payload\n")
    refused = _run(root, "--staging", "--apply")
    assert refused.returncode == 2 and "NEW_FILE_CONFLICT veteran_desk/feed_status_facts.py" in refused.stdout
    assert conflict.read_text() == "# not the payload\n"
    assert _sha(root / "veteran_desk" / "thf_home.py") == _sha(PKG / "base" / "veteran_desk" / "thf_home.py")
    conflict.unlink()
    (root / "veteran_desk" / "thf_home.py").unlink()
    absent = _run(root, "--staging", "--apply")
    assert absent.returncode == 2 and "ABSENT_REFUSED veteran_desk/thf_home.py" in absent.stdout
    assert not (root / "veteran_desk" / "thf_home.py").exists()
    assert not conflict.exists()
    shutil.copy(PKG / "base" / "veteran_desk" / "thf_home.py", root / "veteran_desk" / "thf_home.py")
    inst = _load(PKG / "install.py", "feedclose_install_partial")
    real = inst.shutil.copy2
    seen = {"n": 0}

    def fail_second_payload(src, dst, *args, **kwargs):
        if "payload" in str(src):
            seen["n"] += 1
            if seen["n"] == 2:
                raise OSError("partial")
        return real(src, dst, *args, **kwargs)

    inst.shutil.copy2 = fail_second_payload
    try:
        assert inst.apply_plan(str(root), inst.plan(str(root))) is False
    finally:
        inst.shutil.copy2 = real
    for rel in REPLACED:
        assert _sha(root / rel) == _sha(PKG / "base" / rel)
    assert not (root / "veteran_desk" / "feed_status_facts.py").exists()
    assert _sha(root / "veteran_desk" / "book.py") == BOOK


def test_production_roots_stay_refused(tmp_path):
    for path in (Path("/opt/tfo/live/thf"), Path("/opt/tfo/dash/base"), REPO):
        refused = _run(path, "--staging", "--apply")
        assert refused.returncode == 2 and "REFUSED_ROOT" in refused.stdout
        assert "STAGED" not in refused.stdout
    assert not Path("/opt/tfo/live/thf/veteran_desk/feed_status_facts.py").exists()
    assert not Path("/opt/tfo/dash/base/veteran_desk/feed_status_facts.py").exists()
