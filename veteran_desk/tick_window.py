"""US-session tick gate (TFO HT follow-up 2, 2026-10-28): exit 0 if t is inside NYSE regular session 09:30-16:00 America/New_York
+/- 5 min on a trading day (DST-aware via zoneinfo, holidays from book.US_HOLIDAYS), else exit 1. Usage: python3 tick_window.py [ISO-UTC]"""
import datetime as dt, sys
from zoneinfo import ZoneInfo
HOL = {"2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"}
def in_window(t, pad=5):
    ny = t.astimezone(ZoneInfo("America/New_York"))
    if ny.weekday() >= 5 or ny.date().isoformat() in HOL: return False
    m = ny.hour * 60 + ny.minute; return 570 - pad <= m <= 960 + pad
if __name__ == "__main__":
    t = dt.datetime.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else dt.datetime.now(dt.timezone.utc)
    ok = in_window(t); print("IN" if ok else "OUT", t.astimezone(ZoneInfo("Asia/Makassar")).strftime("%F %H:%M WITA")); sys.exit(0 if ok else 1)
