#!/bin/bash
# TFO deploy gate (PAPER ONLY). Owner: TFO. See ../OWNERSHIP.md
# Usage: tools/safe_deploy.sh [--dry-run] [--tag TAG] [--note TEXT] ITEM...
#   ITEM = DEST            gate a file already edited in place (backup = its current state)
#   ITEM = SRC:DEST        back up DEST, copy staged SRC over DEST, then gate
# Steps: tick-window refuse -> lock -> .bak -> py_compile -> HARD exec-speed suite
#        (any failure refuses; no baseline exemption) -> veteran_desk tests vs
#        tools/test_baseline.json -> restore all + exit 1 on any new failure, else DEPLOY_LOG.md entry.
# --dry-run: runs every check, then restores originals and prints the log entry instead of writing it.
# Env (testing only, and ONLY with --dry-run): SAFE_DEPLOY_BASELINE, SAFE_DEPLOY_MINUTE,
#   SAFE_DEPLOY_SECOND, SAFE_DEPLOY_SKIP_VD_TESTS=1, SAFE_DEPLOY_HARD_SUITE=path.
#
# Deploy window on the Mac (already the rule; do not "fix" this into a wider slot):
#   Tick windows are the first 4 minutes of every quarter hour:
#   :00-:03, :15-:18, :30-:33, :45-:48. A deploy must start at :04:00 or later
#   AND finish before the next quarter, using SAFE_DEPLOY_RUNTIME_S (default 450).
#   With that default the legal start is :04:00 through :07:29 of each quarter.
#   :08 and later is refused even though the tick window is over, because a 450s
#   run would still be going at :15/:30/:45/:00.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT" || exit 2
PY=python3; LOCK="$ROOT/tools/deploy.lock"; BASE="${SAFE_DEPLOY_BASELINE:-$ROOT/tools/test_baseline.json}"
DRY=0; TAG=deploy; NOTE=""; ITEMS=()
while [ $# -gt 0 ]; do case "$1" in
  --dry-run) DRY=1;; --tag) TAG="$2"; shift;; --note) NOTE="$2"; shift;;
  -h|--help) sed -n 2,10p "$0"; exit 0;; *) ITEMS+=("$1");; esac; shift; done
[ ${#ITEMS[@]} -gt 0 ] || { echo "safe_deploy: no files given" >&2; exit 2; }
log(){ echo "[safe_deploy $(date +%H:%M:%S)] $*"; }

# 1. tick windows :00-03 :15-18 :30-33 :45-48 (blocked = first 4 min of every quarter hour).
# The whole run [start, start + expected runtime] must stay clear of the next tick.
# Expected runtime default 450s (override SAFE_DEPLOY_RUNTIME_S). Legal start with that
# default: :04:00-:07:29 of each quarter. See the header.
RT=${SAFE_DEPLOY_RUNTIME_S:-450}
if [ $((900 - RT)) -le 240 ]; then log "REFUSED: expected runtime ${RT}s does not fit between :04 and the next tick"; exit 3; fi
MIN=$((10#$(date +%M))); SEC=$((10#$(date +%S)))
[ $DRY = 1 ] && [ -n "${SAFE_DEPLOY_MINUTE:-}" ] && { MIN=$((10#$SAFE_DEPLOY_MINUTE)); SEC=$((10#${SAFE_DEPLOY_SECOND:-0})); }
QS=$(( (MIN % 15) * 60 + SEC ))
LAST=$((900 - RT - 1))
if [ $QS -lt 240 ]; then log "REFUSED: :$(printf %02d $MIN):$(printf %02d $SEC) is inside a tick window (:00-:03 of the quarter). Next legal start is :04:00; with runtime ${RT}s the window closes at second ${LAST} of the quarter (:04-:07 when runtime is 450s)."; exit 3; fi
if [ $((QS + RT)) -ge 900 ]; then log "REFUSED: :$(printf %02d $MIN):$(printf %02d $SEC) + expected ${RT}s run would cross the next tick window. Legal starts are :04:00 through second ${LAST} of each quarter (:07:29 when runtime is 450s)."; exit 3; fi

# lock (atomic noclobber; stale only if owner pid is dead)
if ! ( set -C; echo "$$ $(date '+%F %T') $TAG ${ITEMS[*]}" > "$LOCK" ) 2>/dev/null; then
  OWN=$(awk '{print $1}' "$LOCK" 2>/dev/null)
  if [ -n "$OWN" ] && kill -0 "$OWN" 2>/dev/null; then log "REFUSED: deploy lock held: $(cat "$LOCK")"; exit 4; fi
  log "removing stale lock: $(cat "$LOCK" 2>/dev/null)"; rm -f "$LOCK"
  ( set -C; echo "$$ $(date '+%F %T') $TAG ${ITEMS[*]}" > "$LOCK" ) 2>/dev/null || { log "REFUSED: lock race"; exit 4; }
fi
trap 'rm -f "$LOCK"' EXIT; trap 'exit 130' INT TERM

STAMP=$(date +%Y%m%d-%H%M%S); DESTS=(); BAKS=(); NEW=()
restore(){ local i; for i in "${!DESTS[@]}"; do
    if [ "${BAKS[$i]}" = "-" ]; then rm -f "${DESTS[$i]}"; log "restored: removed new file ${DESTS[$i]}"
    else cp -p "${BAKS[$i]}" "${DESTS[$i]}"; log "restored ${DESTS[$i]} <- ${BAKS[$i]}"; fi; done; }
fail(){ log "FAIL: $*"; restore; log "ROLLED BACK - nothing deployed"; exit 1; }

# 2. backups (+ copy staged sources in)
for it in "${ITEMS[@]}"; do
  SRC=""; DEST="$it"; case "$it" in *:*) SRC="${it%%:*}"; DEST="${it#*:}";; esac
  [ -n "$SRC" ] && [ ! -f "$SRC" ] && fail "staged source missing: $SRC"
  if [ -f "$DEST" ]; then B="$DEST.bak-$STAMP-$TAG"; cp -p "$DEST" "$B" || fail "backup failed: $DEST"; log "backup $B"
  else [ -n "$SRC" ] || fail "file not found: $DEST"; B="-"; fi
  DESTS+=("$DEST"); BAKS+=("$B")
  if [ -n "$SRC" ]; then cp "$SRC" "$DEST" || fail "copy $SRC -> $DEST failed"; log "staged $SRC -> $DEST"; fi
done

# 3. py_compile
for d in "${DESTS[@]}"; do case "$d" in *.py)
  $PY -m py_compile "$d" 2>&1 || fail "py_compile $d"; log "py_compile ok $d";; esac; done

# 4. hard gate: exec-speed suite must pass. No baseline exemption.
OUT=$(mktemp "${TMPDIR:-/tmp}/safe_deploy.XXXXXX")   # Mac and GNU mktemp; -t prefix is BSD-only
HARD_SUITE="veteran_desk/tests/test_exec_speed_20261031.py"
[ $DRY = 1 ] && [ -n "${SAFE_DEPLOY_HARD_SUITE:-}" ] && HARD_SUITE="$SAFE_DEPLOY_HARD_SUITE"
[ -f "$HARD_SUITE" ] || fail "hard test suite missing: $HARD_SUITE (deploys refuse without it)"
log "running hard gate $HARD_SUITE ..."
$PY -m pytest "$HARD_SUITE" -q -p no:cacheprovider --tb=line > "$OUT" 2>&1; HRC=$?
if [ $HRC -ne 0 ]; then tail -30 "$OUT"; fail "hard test gate failed (rc=$HRC); output $OUT"; fi
log "hard gate passed"

# 4b. veteran_desk tests vs baseline (known failures in tools/test_baseline.json are allowed)
if [ $DRY = 1 ] && [ "${SAFE_DEPLOY_SKIP_VD_TESTS:-}" = "1" ]; then
  log "DRY RUN: skipping veteran_desk/tests baseline (SAFE_DEPLOY_SKIP_VD_TESTS)"
  SUMMARY="skipped"; CMP="OK dry-run"
else
log "running veteran_desk/tests ..."
$PY -m pytest veteran_desk/tests -q -p no:cacheprovider -rfE > "$OUT" 2>&1; RC=$?
SUMMARY=$(grep -E '(passed|failed|error)' "$OUT" | tail -1)
CMP=$($PY - "$BASE" "$OUT" <<'PYEOF'
import json, re, sys
known = set(json.load(open(sys.argv[1])).get("known_failures", []))
got = set()
for l in open(sys.argv[2]):
    m = re.match(r"^(FAILED|ERROR) (\S+)", l)
    if m: got.add(m.group(2))
new = sorted(got - known); print("NEW:" + " ".join(new) if new else "OK", "| known still failing:", len(got & known), "| fixed:", len(known - got))
PYEOF
)
log "pytest rc=$RC: $SUMMARY"; log "baseline: $CMP"
if [ $RC -ne 0 ] && [ -z "$SUMMARY" ]; then tail -20 "$OUT"; fail "pytest did not run (rc=$RC); output $OUT"; fi
case "$CMP" in OK*) ;; *) grep -E '^(FAILED|ERROR)' "$OUT"; fail "new test failures vs baseline (full output $OUT)";; esac
fi

# 5/6. success
ENTRY="
## $(date '+%Y-%m-%d %H:%M') WITA — safe_deploy [$TAG] — PAPER
- Files: ${DESTS[*]}
- Backups: ${BAKS[*]}
- py_compile ok; veteran_desk tests: $SUMMARY; baseline: $CMP
${NOTE:+- Note: $NOTE
}- Rollback: cp -p each .bak-$STAMP-$TAG back over its file."
if [ $DRY = 1 ]; then restore; log "DRY RUN passed - originals restored; would append:"; echo "$ENTRY"; exit 0; fi
printf '%s\n' "$ENTRY" >> DEPLOY_LOG.md; log "DEPLOYED; DEPLOY_LOG.md updated"; rm -f "$OUT"; exit 0
