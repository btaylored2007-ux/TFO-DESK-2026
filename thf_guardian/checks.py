"""Pure health checks. snapshot in, findings out. No LLM, no systemd."""
from thf_guardian.config import DASH_PAGES, LONG_UNITS
from thf_guardian.util import age_sec, parse_dt


def _check(name, ok, detail, heal=None, severity="red"):
    return dict(name=name, ok=bool(ok), detail=detail, heal=heal, severity=("ok" if ok else severity))


def evaluate(snap):
    """snap keys: now, units, timers, feed_ok_age_sec, book_age_sec, book_error,
    sgp_primary, engine_kill, mac_lease, journal_errors, pages, kill_readable,
    kill_error, engine_hb_age_min, deadman_age_min.
    """
    out = []
    units = snap.get("units") or {}
    for name, unit in LONG_UNITS.items():
        st = units.get(unit)
        ok = st == "active"
        out.append(_check("unit_" + name, ok, "%s is %s" % (unit, st or "unknown"),
                          heal=unit if not ok else None))
    timers = snap.get("timers") or {}
    feed_timer = timers.get("tfo-thf-feed.timer")
    out.append(_check("unit_feed", feed_timer == "active",
                      "tfo-thf-feed.timer is %s (feed itself is a oneshot)" % (feed_timer or "unknown"),
                      heal="tfo-thf-feed.timer" if feed_timer != "active" else None))
    # book freshness. A paused or non-primary droplet is not a stuck book.
    if snap.get("engine_kill"):
        out.append(_check("book_fresh", True, "ENGINE_KILL is on; the tick is paused so the book is not expected to move", severity="info"))
    elif snap.get("mac_lease"):
        out.append(_check("book_fresh", True, "Mac holds the takeover lease; droplet book is standby", severity="info"))
    elif not snap.get("sgp_primary", True):
        out.append(_check("book_fresh", True, "no SGP_PRIMARY; droplet tick is idle", severity="info"))
    elif snap.get("book_error"):
        out.append(_check("book_fresh", False, "book unreadable: %s" % snap["book_error"], heal="tfo-thf-tick.service"))
    else:
        age = snap.get("book_age_sec")
        limit = snap.get("book_fresh_sec", 420)
        ok = age is not None and age <= limit
        out.append(_check("book_fresh", ok, "book age %s sec (limit %s)" % (age, limit),
                          heal="tfo-thf-tick.service" if not ok else None))
    feed_age = snap.get("feed_ok_age_sec")
    feed_limit = snap.get("feed_ok_sec", 300)
    if feed_age is None:
        out.append(_check("feed_ok", False, "no .feed_ok sentinel (set THF_GUARDIAN_FEED_OK if the feed writes it elsewhere)",
                          heal="tfo-thf-feed.service"))
    else:
        ok = feed_age <= feed_limit
        out.append(_check("feed_ok", ok, ".feed_ok age %s sec (limit %s) path %s" % (round(feed_age, 1), feed_limit, snap.get("feed_ok_path")),
                          heal="tfo-thf-feed.service" if not ok else None))
    errs = snap.get("journal_errors") or []
    out.append(_check("engine_journal", not errs, "engine journal errors: %s" % ("; ".join(errs[:5]) or "none"),
                      severity="amber"))
    pages = snap.get("pages") or {}
    bad = ["%s=%s" % (p, pages.get(p)) for p in (snap.get("dash_pages") or DASH_PAGES) if pages.get(p) != 200]
    out.append(_check("dash_pages", not bad, "dashboard pages " + (", ".join(bad) if bad else "all 200"),
                      heal="tfo-dash-thf.service" if bad else None))
    if snap.get("kill_readable"):
        out.append(_check("kill_events", True, "kill_events.jsonl readable"))
    else:
        out.append(_check("kill_events", False, "kill_events.jsonl not readable: %s" % (snap.get("kill_error") or "missing")))
    hb = snap.get("engine_hb_age_min")
    if hb is not None and hb > 5 and units.get("tfo-thf-engine.service") == "active":
        # Deadman owns a fresh heartbeat restart. If deadman itself is stale, the guardian takes it.
        dm = snap.get("deadman_age_min")
        if dm is None or dm > 3:
            out.append(_check("engine_heartbeat", False, "engine unit is active but heartbeat is %.1f min and deadman is stale" % hb,
                              heal="tfo-thf-engine.service"))
        else:
            out.append(_check("engine_heartbeat", True, "heartbeat %.1f min; deadman is fresh and owns the restart" % hb, severity="info"))
    else:
        out.append(_check("engine_heartbeat", True, "engine heartbeat %s min" % hb, severity="info"))
    return out
