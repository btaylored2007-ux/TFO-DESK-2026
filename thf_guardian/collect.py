"""Build a snap from the droplet. Missing files become explicit failures, not guesses.
systemctl and the bridge are injected so tests do not touch the host.
"""
import os, subprocess, urllib.request

from thf_guardian.config import DASH_PAGES, LONG_UNITS, TIMERS
from thf_guardian.util import parse_dt, read_json, utcnow


def _active(unit, runner):
    try:
        r = runner(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=8)
        return (r.stdout or "").strip() or "unknown"
    except Exception:
        return "unknown"


def _age_file(path, now):
    try:
        return now.timestamp() - os.path.getmtime(path), path
    except OSError:
        return None, path


def _json_age(path, now, *keys):
    data = read_json(path, None)
    if not isinstance(data, dict):
        age, _ = _age_file(path, now)
        return age, data
    for key in keys:
        t = parse_dt(data.get(key))
        if t:
            return (now - t).total_seconds(), data
    age, _ = _age_file(path, now)
    return age, data


def collect(cfg, now=None, runner=None):
    now = now or utcnow()
    runner = runner or subprocess.run
    units = {unit: _active(unit, runner) for unit in LONG_UNITS.values()}
    units["tfo-thf-queue-answerer.service"] = _active("tfo-thf-queue-answerer.service", runner)
    timers = {unit: _active(unit, runner) for unit in list(TIMERS.values()) + [
        "tfo-thf-guardian.timer", "tfo-thf-guardian-check.timer"]}
    book = read_json(cfg.book, None) or {}
    book_age = None
    if isinstance(book, dict) and book.get("updated"):
        t = parse_dt(book.get("updated"))
        if t:
            book_age = (now - t).total_seconds()
    if book_age is None:
        book_age, _ = _age_file(cfg.book, now)
    feed_age = feed_path = None
    for cand in cfg.feed_ok_candidates():
        age, _ = _age_file(cand, now)
        if age is not None:
            feed_age, feed_path = age, cand
            break
    pages = {}
    for page in DASH_PAGES:
        try:
            with urllib.request.urlopen(cfg.dash_base + page, timeout=5) as resp:
                pages[page] = getattr(resp, "status", 200)
        except Exception:
            pages[page] = 0
    kill_ok = False
    kill_err = None
    try:
        with open(cfg.kill_events, "rb") as fh:
            fh.read(1)
        kill_ok = True
    except Exception as exc:
        kill_err = type(exc).__name__
    news_age, _ = _json_age(cfg.p("state", "veteran_desk", "news.json"), now, "updated_utc", "built_utc")
    borrow_age, _ = _json_age(cfg.p("state", "ibkr_borrow.json"), now, "updated_utc")
    kraken_age, _ = _json_age(cfg.p("state", "veteran_desk", "kraken_24h_all.json"), now, "utc", "updated_utc")
    yahoo_age, _ = _json_age(cfg.p("state", "feed_delay.json"), now, "built_utc")
    ann_path = cfg.p("state", "thf", "announcements.json")
    ann_age, _ = _age_file(ann_path, now)
    tape_age, _ = _age_file(cfg.p("reports", "ws_tape_heartbeat.json"), now)
    if tape_age is None:
        tape_age = feed_age
    feeds = {
        "ws_tape": dict(age_sec=tape_age, present=tape_age is not None, backup_present=yahoo_age is not None, backup_age_sec=yahoo_age),
        "news": dict(age_sec=news_age, present=news_age is not None),
        "borrow": dict(age_sec=borrow_age, present=borrow_age is not None),
        "kraken": dict(age_sec=kraken_age, present=kraken_age is not None),
        "yahoo_fallback": dict(age_sec=yahoo_age, present=yahoo_age is not None),
        "announcements": dict(age_sec=ann_age, present=os.path.exists(ann_path)),
    }
    locks = []
    for rel, kind in (("state/thf/.tick.lock", "mkdir"), ("state/veteran_desk/.tick.lock", "mkdir"), (".tick.flock", "flock")):
        path = cfg.p(*rel.split("/")) if "/" in rel or rel.startswith(".") else cfg.p(rel)
        if rel.startswith("."):
            path = cfg.p(rel)
        if os.path.exists(path):
            age = now.timestamp() - os.path.getmtime(path)
            locks.append(dict(path=path, kind=kind, age_sec=age, held=True))
    approved = read_json(cfg.approved_code, {}) or {}
    if isinstance(approved, dict) and "files" in approved:
        approved = approved["files"]
    status_age, _ = _age_file(cfg.status_path, now)
    return dict(
        now=now, units=units, timers=timers, book=book if isinstance(book, dict) else {},
        book_age_sec=book_age, book_fresh_sec=cfg.book_fresh_sec, feed_ok_age_sec=feed_age, feed_ok_path=feed_path,
        feed_ok_sec=cfg.feed_ok_sec, sgp_primary=os.path.exists(cfg.p("SGP_PRIMARY")),
        engine_kill=os.path.exists(cfg.engine_kill), mac_lease=os.path.exists("/opt/tfo/data/state/thf_lease_mac.json"),
        journal_errors=[], pages=pages, kill_readable=kill_ok, kill_error=kill_err,
        feeds=feeds, locks=locks, approved=approved, queue=read_json(cfg.funder_queue, {}) or {},
        gate=read_json(cfg.p("state", "thf", "gate_thresholds.json"), None),
        stuck=[], guardian_status_age_sec=status_age, dash_pages=DASH_PAGES,
        clock_stamps=[("book", book.get("updated") if isinstance(book, dict) else None)],
    )
