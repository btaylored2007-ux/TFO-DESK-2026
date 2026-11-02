"""One guardian pass. Deterministic. Calls no model.

Also importable as `python -m thf_guardian.guardian` for the oneshot timer.
The long-running inbox is `python -m thf_guardian.daemon` (Restart=always).
A oneshot cannot use Restart=always: systemd would restart it the moment it
exits and the timer would never get a gap. The daemon carries Restart=always.
"""
import os, sys

from thf_guardian.autoclear import judge
from thf_guardian.checks import evaluate
from thf_guardian.feeds import diagnose as diagnose_feeds
from thf_guardian.gaps import flag_stale, gap_on_first_fresh
from thf_guardian.heal import plan as heal_plan, record as heal_record
from thf_guardian.lag import diagnose as diagnose_lag
from thf_guardian.notify import emit
from thf_guardian.runtime import diagnose_drift, diagnose_locks, diagnose_units
from thf_guardian.tickwatch import book_integrity, prepare_tick_restart, watch_guardian
from thf_guardian.util import atomic_write, clock_drift, read_json, read_state, wita_str


def run_once(cfg, snap, io):
    now = snap["now"]
    now_ts = now.timestamp()
    status_prev, status_err = read_state(cfg.status_path)
    if status_err == "corrupt":
        emit(cfg, dict(level="alert", kind="corrupt_state", detail="guardian_status.json was corrupt and was moved aside"), io.get("run"))
    heal = read_json(cfg.heal_state, {}) or {}
    screen_rev = int((status_prev or {}).get("screen_rev") or 0) + 1
    drift_hits = clock_drift(now, snap.get("clock_stamps") or [])
    checks = evaluate(snap)
    feeds = diagnose_feeds(snap, now_ts)
    lag = diagnose_lag(dict(snap, book_age_sec=snap.get("book_age_sec"), queue=snap.get("queue"), gate=snap.get("gate"),
                            stuck=snap.get("stuck"), tick_paused=snap.get("engine_kill") or snap.get("mac_lease") or not snap.get("sgp_primary", True)), now)
    units = diagnose_units(snap)
    locks = diagnose_locks(snap.get("locks") or [], now_ts, cfg.lock_stale_sec)
    drift = diagnose_drift(cfg.live, snap.get("approved") if snap.get("approved") is not None else (read_json(cfg.approved_code, {}) or {}))
    integrity = book_integrity(snap.get("book") or {})
    tick_prep = prepare_tick_restart(integrity, snap.get("locks") or [], cfg.lock_stale_sec)
    gwatch = watch_guardian(snap.get("guardian_status_age_sec"), cfg.interval_sec, snap.get("timers") or {})
    stale_flags = flag_stale(snap.get("mark_rows") or [])
    gaps = []
    for item in snap.get("fresh_after_stale") or []:
        flag = gap_on_first_fresh(item.get("pos") or {}, item.get("was_stale"), item.get("fresh"))
        if flag:
            gaps.append(flag)
    heals = []
    restarts = []
    wanted = []
    for chk in checks:
        if chk.get("heal"):
            wanted.append(chk["heal"])
    for act in feeds["actions"] + units["actions"]:
        if act.get("unit"):
            wanted.append(act["unit"])
    if any(i["kind"] == "tick_lag" for i in lag["incidents"]):
        if tick_prep["restart"]:
            wanted.append("tfo-thf-tick.service")
            for lk in tick_prep["clear"]:
                io["clear_lock"](lk["path"])
                heals.append(dict(action="clear_lock", path=lk["path"]))
        else:
            heals.append(dict(action="skip_tick", why=tick_prep["why"], problems=tick_prep.get("problems")))
    for lk in locks["actions"]:
        if lk["kind"] == "clear_lock":
            io["clear_lock"](lk["path"])
            heals.append(lk)
        else:
            heals.append(lk)
    deadman_recent = bool(snap.get("deadman_restart_age_sec") is not None and snap["deadman_restart_age_sec"] < 300)
    seen = set()
    for unit in wanted:
        if unit in seen:
            continue
        seen.add(unit)
        decision = heal_plan(heal, unit, now_ts, cfg.heal_backoff_sec, cfg.heal_max, cfg.heal_window_sec, deadman_recent)
        if decision["action"] == "restart":
            how = "start" if unit.endswith(".timer") or unit in (
                "tfo-thf-tick.service", "tfo-thf-feed.service", "tfo-thf-borrow.service", "tfo-thf-kraken-daily.service") else "restart"
            io["systemctl"](how, unit)
            heal_record(heal, unit, now_ts, cfg.heal_window_sec)
            restarts.append(dict(unit=unit, how=how))
        heals.append(decision)
    for actn in gwatch["actions"]:
        if actn["kind"] == "start":
            decision = heal_plan(heal, actn["unit"], now_ts, cfg.heal_backoff_sec, cfg.heal_max, cfg.heal_window_sec, False)
            if decision["action"] == "restart":
                io["systemctl"]("start", actn["unit"])
                heal_record(heal, actn["unit"], now_ts, cfg.heal_window_sec)
                restarts.append(dict(unit=actn["unit"], how="start"))
            heals.append(decision)
        else:
            heals.append(actn)
    history = list(heal.get("auto_fills") or [])
    halted_until = heal.get("auto_clear_halted_until")
    reranked = None
    fills = []
    for actn in lag["actions"]:
        if actn["kind"] == "rerank_queue":
            io["write_queue"](actn["entries"])
            reranked = actn["moved"]
            emit(cfg, dict(level="alert", kind="rerank", moved=actn["moved"], wita=wita_str(now)), io.get("run"))
        elif actn["kind"] == "fill_at_entry":
            allow, halted_until, _n = judge(history, now_ts, cfg.heal_window_sec, cfg.auto_fill_max, halted_until)
            if not allow:
                heal["auto_clear_halted_until"] = halted_until
                emit(cfg, dict(level="halt", kind="auto_clear_halt", detail="fill-at-entry halted; too many clears", wita=wita_str(now)), io.get("run"))
                heals.append(dict(action="halt_auto_clear", until=halted_until))
                break
            if drift_hits:
                heals.append(dict(action="skip_fill", why="clock drift"))
                continue
            try:
                io["fill"](actn)
                history.append(now_ts)
                fills.append(actn["id"])
                emit(cfg, dict(level="alert", kind="fill_at_entry", id=actn["id"], seat=actn["seat"], wita=wita_str(now)), io.get("run"))
            except Exception as exc:
                heals.append(dict(action="fill_refused", id=actn.get("id"), why=str(exc)[:200]))
    heal["auto_fills"] = history
    if halted_until:
        heal["auto_clear_halted_until"] = halted_until
    min_free = io.get("min_free", 50_000_000)
    atomic_write(cfg.heal_state, heal, min_free=min_free)
    status = dict(
        paper_only=True, at_utc=now.isoformat(), at_wita=wita_str(now), screen_rev=screen_rev,
        ok=not any((not c["ok"]) and c.get("severity") == "red" for c in checks),
        checks=checks, feeds=feeds, lag_incidents=lag["incidents"], heals=heals, restarts=restarts,
        locks=locks, drift=drift, brains=snap.get("brains") or {}, firm_paused=snap.get("firm_paused"),
        degraded_n=snap.get("degraded_n"), takeover=snap.get("takeover"), stuck=snap.get("stuck") or [],
        stale_prices=stale_flags, gaps=gaps, tick=tick_prep, guardian_watch=gwatch,
        clock_drift=drift_hits, auto_clear_halted_until=halted_until, fills=fills, reranked=reranked,
        book_integrity=integrity,
        active_feeds={r["id"]: r.get("active_source", "primary") for r in feeds["feeds"]},
    )
    atomic_write(cfg.status_path, status, min_free=min_free)
    atomic_write(cfg.feed_active, status["active_feeds"], min_free=min_free)
    if (not status["ok"]) or drift_hits or not drift.get("ok", True):
        emit(cfg, dict(level="alert", kind="guardian", ok=status["ok"], drift=drift.get("state"), wita=wita_str(now)), io.get("run"))
    return status


def main():
    print("thf_guardian oneshot needs a collected snap; the systemd unit runs python -m thf_guardian.daemon")
    return 0


if __name__ == "__main__":
    sys.exit(main())
