#!/usr/bin/env python3
"""Fundamental auto-deploy scheduler wrapper (Brett 28 Oct 2026 14:10 WITA "Let it trade indefinitely"). PAPER ONLY.
launchd runs this every 15 min; it calls value_deploy --live ONLY while a relevant market is in its regular session
(15 min after the open to 15 min before the close, local exchange time, DST-aware via zoneinfo, Mon-Fri).
value_deploy requires a current regular-session quote per name; delayed PAPER quotes (current session, <=45m) are fillable only for value_deploy.DELAYED_OK_SUFFIXES, with the delay logged + charged as slippage. A closed market never fills.
Idempotent: value_deploy skips held names and is a no-op when cash <= 5% reserve + 1% clip."""
import datetime as dt, json, os, sys
from zoneinfo import ZoneInfo
BASE = os.path.dirname(os.path.abspath(__file__))
SESSIONS = {"US": ("America/New_York", (9, 30), (16, 0)), "ASX": ("Australia/Sydney", (10, 0), (16, 0)),
            "LSE": ("Europe/London", (8, 0), (16, 30)), "JP": ("Asia/Tokyo", (9, 0), (15, 30))}
BUFFER_MIN = 15
def open_markets(now=None):
    now = now or dt.datetime.now(dt.timezone.utc); out = []
    for k, (tz, o, c) in SESSIONS.items():
        t = now.astimezone(ZoneInfo(tz))
        if t.weekday() >= 5: continue
        m = t.hour * 60 + t.minute
        if o[0] * 60 + o[1] + BUFFER_MIN <= m < c[0] * 60 + c[1] - BUFFER_MIN: out.append(k)
    return out
def main():
    mk = open_markets(); stamp = dt.datetime.now(ZoneInfo("Asia/Makassar")).strftime("%Y-%m-%d %H:%M WITA")
    if not mk:
        print(stamp, "no relevant market in session — idle"); return 0
    print(stamp, "in session:", ",".join(mk), "-> value_deploy --live (PAPER)"); sys.stdout.flush()
    sys.path.insert(0, BASE); import value_deploy
    value_deploy.main(dry="--dry" in sys.argv)
    return 0
if __name__ == "__main__":
    sys.exit(main())
