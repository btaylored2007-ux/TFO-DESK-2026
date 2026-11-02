"""Feed doctor. Staleness, failover, restart, backfill. No prices are invented and no LLM is called.

Primary files are the ones the desk already writes. A stale primary fails over
to the named backup when that backup is fresh, then the existing collector unit
is restarted (with backoff) so it backfills its own file. ASX announcements are
not wired in news_desk.NOT_WIRED; that feed is reported, not scraped.
"""
from thf_guardian.config import VENUE_FLOOR_MIN

# stale_sec None -> per-venue quote ages, else the sentinel age.
# backup is a status label, not a second writer.
FEEDS = (
    dict(id="ws_tape", title="price / WS tape", unit="tfo-thf-wstape.service", how="restart",
         backup="yahoo_fallback", stale_sec=300, per_venue=True),
    dict(id="news", title="news", unit="tfo-thf-news.service", how="restart",
         backup="news_last_good", stale_sec=1200, per_venue=False),
    dict(id="borrow", title="borrow", unit="tfo-thf-borrow.service", how="start",
         backup="borrow_last_good", stale_sec=86400, per_venue=False),
    dict(id="kraken", title="Kraken", unit="tfo-thf-kraken-daily.service", how="start",
         backup="kraken_last_good", stale_sec=26 * 3600, per_venue=False),
    dict(id="yahoo_fallback", title="Yahoo fallback", unit="tfo-thf-feed.service", how="start",
         backup=None, stale_sec=1800, per_venue=False),
    dict(id="announcements", title="announcements", unit=None, how=None,
         backup=None, stale_sec=6 * 3600, per_venue=False, unwired=True),
)


def venue_limit_min(venue):
    """floor + 5 minutes, matching book.stale_limit_min's shape, including ASX."""
    floor = VENUE_FLOOR_MIN.get(venue, 15)
    return floor + 5


def diagnose(snap, now_ts):
    """snap['feeds'][id] = {age_sec, present, backup_age_sec, backup_present, unwired}.
    snap['venue_quotes'][venue] = age_min of the newest quote the tape claims.
    """
    feeds = snap.get("feeds") or {}
    quotes = snap.get("venue_quotes") or {}
    rows = []
    actions = []
    for spec in FEEDS:
        fid = spec["id"]
        obs = feeds.get(fid) or {}
        if spec.get("unwired") and not obs.get("present"):
            rows.append(dict(id=fid, title=spec["title"], ok=True, state="not_wired",
                             detail="ASX/LSE/HKEX announcement wires are not wired (news_desk.NOT_WIRED); no restart"))
            continue
        age = obs.get("age_sec")
        stale = age is None or (spec["stale_sec"] is not None and age > spec["stale_sec"])
        venue_bad = []
        if spec.get("per_venue"):
            for venue, age_min in quotes.items():
                limit = venue_limit_min(venue)
                if age_min is None or age_min > limit:
                    venue_bad.append("%s %smin>%s" % (venue, age_min, limit))
            if venue_bad:
                stale = True
        backup_ok = bool(obs.get("backup_present")) and obs.get("backup_age_sec") is not None and (
            spec["stale_sec"] is None or obs.get("backup_age_sec") <= spec["stale_sec"])
        if not stale:
            rows.append(dict(id=fid, title=spec["title"], ok=True, state="fresh",
                             detail="age %ss" % age, active_source="primary"))
            continue
        state = "failover" if backup_ok else "stale"
        detail = "age %s sec" % age
        if venue_bad:
            detail += "; venues " + ", ".join(venue_bad)
        if backup_ok:
            detail += "; backup %s is fresh" % (spec["backup"] or "backup")
        rows.append(dict(id=fid, title=spec["title"], ok=False, state=state, detail=detail,
                         active_source=(spec["backup"] if backup_ok else "primary"),
                         severity="amber" if backup_ok else "red"))
        if spec.get("unit"):
            actions.append(dict(kind="restart_feed", feed=fid, unit=spec["unit"], how=spec["how"],
                                backfill=True, why=detail))
    return dict(feeds=rows, actions=actions, paper_only=True,
                rule="restart the existing collector; do not invent prices; do not loosen borrow 24h or venue floors")
