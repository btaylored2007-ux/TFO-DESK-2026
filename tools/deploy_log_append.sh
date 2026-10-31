#!/bin/zsh
# Append one deploy line to the desk's canonical log (~/.tfo_swing/DEPLOY_LOG.txt).
# Optional: also append a short markdown bullet to MultiVenueDesk/DEPLOY_LOG.md when -m is set.
# Usage: tools/deploy_log_append.sh [-m "md title"] "one-line summary"
# Convention (Brett health-fix 2026-10-29): every meaningful PAPER deploy appends HERE, not only to chat.
set -euo pipefail
MD=""
while getopts "m:" opt; do case $opt in m) MD="$OPTARG";; esac; done
shift $((OPTIND-1))
[[ $# -ge 1 ]] || { echo "usage: $0 [-m title] summary" >&2; exit 2; }
SUMMARY="$1"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TXT="${DEPLOY_LOG_TXT:-$HOME/.tfo_swing/DEPLOY_LOG.txt}"
NOW="$(date '+%Y-%m-%d %H:%M WITA')"
mkdir -p "$(dirname "$TXT")"
echo "$NOW $SUMMARY" >> "$TXT"
echo "appended -> $TXT"
(cd "$ROOT" && AUDIT_SOURCE=deploy_log_append AUDIT_DESK=tfo AUDIT_SUMMARY="$SUMMARY" AUDIT_MD="${MD:-}" python3 -m audit emit) || true
if [[ -n "$MD" ]]; then
  {
    echo ""
    echo "## $NOW — $MD"
    echo "- $SUMMARY"
  } >> "$ROOT/DEPLOY_LOG.md"
  echo "appended -> $ROOT/DEPLOY_LOG.md"
fi
