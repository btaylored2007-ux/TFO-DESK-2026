#!/bin/bash
# Singapore live THF code is authoritative; no legacy snapshot or runtime patch scripts.
set -u
LT=/opt/tfo/live/thf
RELEASE_ROOT="$LT"; RELEASE_BUSY_RC=0; PY=python3
# Share one portable advisory lock with the deploy gate. The inherited descriptor
# stays locked in the supervising shell; verify it before trusting env.
release_lock_owned() {
  [ -n "${THF_CODE_RELEASE_FD:-}" ] || return 1
  "$PY" - "$RELEASE_ROOT/tools/.code_release.lock" "$THF_CODE_RELEASE_FD" <<'PYVERIFY'
import fcntl, os, sys
try:
    fd = int(sys.argv[2]); held = os.fstat(fd); path = os.stat(sys.argv[1])
    if (held.st_dev, held.st_ino) != (path.st_dev, path.st_ino): sys.exit(1)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except (OSError, ValueError): sys.exit(1)
PYVERIFY
}
if ! release_lock_owned || [ "${THF_CODE_RELEASE_FD:-}" != "9" ]; then
  exec "$PY" - "$RELEASE_ROOT/tools/.code_release.lock" "$RELEASE_BUSY_RC" "$0" "$@" <<'PYLOCK'
import fcntl, os, sys
path, busy, script, *args = sys.argv[1:]
# Accept a verified intentional inherited owner, including older FD numbers.
fd = None
try:
    inherited = int(os.environ.get("THF_CODE_RELEASE_FD", "-1"))
    held = os.fstat(inherited); expected = os.stat(path)
    if (held.st_dev, held.st_ino) == (expected.st_dev, expected.st_ino):
        fd = inherited
except (OSError, ValueError):
    pass
# Refresh's sandbox can read an existing lock but cannot write live tools.
if fd is None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print("code release lock busy; deferring", file=sys.stderr)
    sys.exit(int(busy))
if fd != 9:
    os.dup2(fd, 9); os.close(fd)
fd = 9
os.set_inheritable(fd, True)
env = os.environ.copy(); env["THF_CODE_RELEASE_FD"] = str(fd)
os.execvpe("bash", ["bash", script] + args, env)
PYLOCK
fi
# Keep the lock in this supervising shell; test/AI/audit descendants must not
# extend its lifetime. FD 9 is reserved and normalized by the guard above.
release_child(){ "$@" 9>&-; }

# Honour pre-upgrade deployers that only hold the established PID lock.
if [ -r "$LT/tools/deploy.lock" ]; then
  owner=""
  read -r owner _ < "$LT/tools/deploy.lock" || true
  case "$owner" in
    ""|*[!0-9]*) ;;
    *) if [ "$owner" -gt 1 ] 2>/dev/null && kill -0 "$owner" 2>/dev/null; then
         echo "deploy gate active; deferring dashboard refresh"
         exit 0
       fi ;;
  esac
fi
B=/opt/tfo/dash/base; D=/opt/tfo/data
release_child mkdir -p $B/state $B/reports $B/data/intraday
R="release_child rsync -rlt --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r"
X=(--exclude='autonomy/learning.sqlite*' --exclude='dash_cache*.json' --exclude='*.tmp.*' --exclude='__pycache__/')
M=/opt/tfo/dash/codemerge; release_child mkdir -p $M
$R --checksum --delete --delete-excluded --exclude='__pycache__/' --exclude='state/' --exclude='reports/' --exclude='data/' --exclude='logs/' --exclude='backups/' --exclude='/research/' --exclude='/handover/' --exclude='/staged_fixes/' --exclude='/staging/' --exclude='/work/' --exclude='/idea_feeds/' --exclude='/tests/' --exclude='/tests_watchlist/' --exclude='veteran_desk/tests/' --exclude='thf_guardian/tests/' --exclude='/.claude/' --exclude='/.scratch/' --exclude='/.pytest_cache/' --exclude='/.staging_gates/' --exclude='/plan48/' --exclude='secrets*' --exclude='*.env' --exclude='*.bak*' --exclude='*.sqlite*' --exclude='.*.??????' --exclude='*.tmp' --exclude='*.tmp.*' --exclude='*.after_*' $LT/ $M/ || exit 1
$R --checksum --delete --exclude='__pycache__/' --exclude='state/' --exclude='reports/' --exclude='data/' --exclude='.refreshed' $M/ $B/ || exit 1
for d in vd_engine thf veteran_desk llm_queue eodhd_eu data_hub calendar catalyst_stream clearance config learning intraday_rescan; do
  src=$D/state/$d; [ -f /opt/tfo/live/thf/SGP_PRIMARY ] && [ -d /opt/tfo/live/thf/state/$d ] && src=/opt/tfo/live/thf/state/$d   # macdata_20261106 (F31): the dash reads the LIVE TREE for every state dir (it already holds the Mac copies synced by run_thf_feed_sgp.sh plus the droplet-owned dirs), so MAC_SYNC_PAUSE covers the dash; the Mac mirror is only the fallback for a dir the live tree lacks. Was: Mac mirror except thf/vd_engine/eodhd_eu/catalyst_stream/calendar (tfo_sgplease_20261031, tfo_eusgpdash_20261102, sgpfeeds_20261103)yst_stream + calendar
  [ -d $src ] && $R --delete "${X[@]}" --exclude='archive/' $src/ $B/state/$d/
done
LT=/opt/tfo/live/thf; DS=$D; [ -f $LT/SGP_PRIMARY ] && DS=$LT   # macdata_20261106 (F31): top-level state/reports files, the reports subdirs and the intraday bars come from the live tree (droplet data; superset of the Mac push)
$R "${X[@]}" --exclude='*/' $DS/state/ $B/state/
$R --exclude='*/' $DS/reports/ $B/reports/
for d in veteran_desk learning; do [ -d $DS/reports/$d ] && $R --delete $DS/reports/$d/ $B/reports/$d/; done
if [ -f $LT/SGP_PRIMARY ] && [ -s $LT/data/intraday/bars_snapshot.sqlite ]; then   # macdata_20261106: the live side writes a consistent rollback-journal snapshot every 5 min (tools/bars_snapshot.py, tfo-thf-bars-snapshot.timer); neither the dash (ProtectSystem=strict) nor this refresh can open the live WAL store, and a 15-s rsync of the live file showed "no bars" mid-copy
  [ -L $B/data/intraday/bars.sqlite ] && release_child rm -f $B/data/intraday/bars.sqlite
  $R $LT/data/intraday/bars_snapshot.sqlite $B/data/intraday/bars.sqlite; release_child rm -f $B/data/intraday/bars.sqlite-wal $B/data/intraday/bars.sqlite-shm
elif [ -f $D/sqlite/intraday_bars.sqlite ]; then [ -L $B/data/intraday/bars.sqlite ] && release_child rm -f $B/data/intraday/bars.sqlite; $R $D/sqlite/intraday_bars.sqlite $B/data/intraday/bars.sqlite; fi
LB=/opt/tfo/live/thf/state/ibkr_borrow.json; [ -s $LB ] && [ $LB -nt $B/state/ibkr_borrow.json ] && release_child cp -p $LB $B/state/ibkr_borrow.json   # tfo_borrowsgp_20261101 newer droplet borrow wins
LN=/opt/tfo/live/thf/state/veteran_desk; for f in news.json edgar_8k_cache.json; do [ -s $LN/$f ] && { [ ! -f $B/state/veteran_desk/$f ] || [ $LN/$f -nt $B/state/veteran_desk/$f ]; } && release_child cp -p $LN/$f $B/state/veteran_desk/$f; done   # sgpfeeds_20261103 droplet news_desk / edgar8k timers own these (newer wins)
# Runtime JSON (including static/hunt48_* status files) changes constantly.
# Reload only when accepted Python or served code/assets change; data stays live.
dashboard_code_digest() {
  release_child "$PY" - "$1" <<'PYCODE'
import hashlib, os, pathlib, re, sys
root = pathlib.Path(sys.argv[1])
skip_dirs = frozenset(("state", "reports", "data", "logs", "backups", "archive", "staging", "staged_fixes", "work", "research", "handover", "idea_feeds", "tests", "tests_watchlist", "__pycache__", ".git", ".claude", ".scratch", ".pytest_cache", ".staging_gates", "plan48"))
code_suffixes = frozenset((".py", ".js", ".html", ".css"))
artifact = re.compile(r"\.(?:bak|tmp)(?:[._-]|$)|\.after_|~$")
paths = []
def walk_error(error):
    raise error
for folder, dirs, files in os.walk(root, topdown=True, onerror=walk_error, followlinks=False):
    dirs[:] = sorted(d for d in dirs if d not in skip_dirs and not os.path.islink(os.path.join(folder, d)))
    for name in files:
        path = pathlib.Path(folder) / name
        if path.suffix not in code_suffixes or artifact.search(name) or path.is_symlink():
            continue
        paths.append(path)
digest = hashlib.sha256()
for path in sorted(paths, key=lambda p: p.relative_to(root).as_posix()):
    relative = path.relative_to(root).as_posix().encode("utf-8")
    digest.update(relative + b"\0")
    with path.open("rb") as stream:
        contents = hashlib.sha256()
        for block in iter(lambda: stream.read(131072), b""):
            contents.update(block)
    digest.update(contents.digest())
print(digest.hexdigest())
PYCODE
}
# Compare only executable/served code; keep the legacy marker path for its watcher.
H=$(dashboard_code_digest "$M") || { echo "dashboard code fingerprint failed; keeping the previous reload marker" >&2; exit 1; }
if [ "$H" != "$(release_child cat "$B/state/.thf_code_md5" 2>/dev/null)" ]; then
  echo "$H" > "$B/state/.thf_code_md5"
fi
release_child date +%s > $B/.refreshed
