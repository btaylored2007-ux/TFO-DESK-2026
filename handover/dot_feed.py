#!/Library/Frameworks/Python.framework/Versions/3.14/bin/python3
"""DOT feed: publish the SHARED entry-engine signal sheet to DOT's read-only outbox.

Reads  : state/entry_engine/latest.md (read-only; if it does not exist yet, exits 0 and waits for the next run)
Writes : ONLY handover/dot_feed/ (entry_engine_signals_latest.md, entry_engine_signals_<stamp>.md, feed_log.jsonl)

Independence rules (DOT is a separate competitor book in the head-to-head):
  * Never writes TFO ledgers or state; refuses any path outside handover/dot_feed/.
  * Strips TFO's live book from the sheet: everything from the "Open positions" section on
    (TFO's open trades, entries, stops) is removed. Only the scan / pick / reject signals go out.
  * Nothing here can receive input from DOT; it is one-way.
"""
import datetime as dt, hashlib, json, os, re, sys
from zoneinfo import ZoneInfo

BASE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SRC = os.path.join(BASE, "state", "entry_engine", "latest.md")
OUT = os.path.join(BASE, "handover", "dot_feed")
SYD = ZoneInfo("Australia/Sydney")
# Section headers that start TFO-private content (live book). Matching line and everything after is dropped.
PRIVATE_HEADERS = re.compile(r"^\s*(open positions|positions|held positions|tfo book|live book)\b", re.I)
# Any stray line that names a TFO book/position id is dropped as well.
PRIVATE_LINE = re.compile(r"\b(PD\d{3,}|parallel desk|trend18|fundamental_book|desk rail|position_id)\b", re.I)


def safe(p):
    p = os.path.realpath(p)
    if not p.startswith(os.path.realpath(OUT) + os.sep):
        raise SystemExit("refusing to write outside handover/dot_feed: %s" % p)
    return p


def sanitize(text):
    keep = []
    for line in text.splitlines():
        if PRIVATE_HEADERS.match(line):
            break
        if PRIVATE_LINE.search(line):
            continue
        keep.append(line)
    while keep and not keep[-1].strip():
        keep.pop()
    return "\n".join(keep) + "\n"


def main():
    if not os.path.exists(SRC):
        return 0  # engine not producing yet; launchd WatchPaths/StartInterval will retry
    os.makedirs(OUT, exist_ok=True)
    with open(SRC, encoding="utf-8") as f:
        raw = f.read()
    body = sanitize(raw)
    digest = hashlib.sha256(body.encode()).hexdigest()[:16]
    state = os.path.join(OUT, ".last_digest")
    if os.path.exists(state) and open(state).read().strip() == digest:
        return 0
    now = dt.datetime.now(SYD)
    header = ("<!-- DOT feed: shared TFO entry-engine signals only (scan, pick, rejects). "
              "TFO open positions/live decisions removed. Paper only; not orders. "
              "Published %s; source mtime %s -->\n" % (now.isoformat(timespec="seconds"),
              dt.datetime.fromtimestamp(os.path.getmtime(SRC), SYD).isoformat(timespec="seconds")))
    doc = header + body
    stamped = safe(os.path.join(OUT, "entry_engine_signals_%s.md" % now.strftime("%Y%m%d_%H%M%S")))
    latest = safe(os.path.join(OUT, "entry_engine_signals_latest.md"))
    for p in (stamped, latest):
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(doc)
        os.replace(tmp, p)
    with open(safe(state), "w") as f:
        f.write(digest)
    with open(safe(os.path.join(OUT, "feed_log.jsonl")), "a") as f:
        f.write(json.dumps({"published_at": now.isoformat(), "file": os.path.basename(stamped), "digest": digest}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
