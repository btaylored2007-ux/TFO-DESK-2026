#!/bin/zsh
# On-demand research through ChatGPT (Codex CLI) or Claude Code at MAX model/effort.
# Usage: ask.sh <claude|chatgpt> "<question>" [outname]
# Output: idea_feeds/adhoc/<tool>_<YYYYMMDD_HHMMSS>_<outname>.md  (path printed on stdout)
# Sandboxed: read-only except idea_feeds/.scratch; web allowed. Research only - never orders,
# never V1 ledgers, never tfo_v2*. Timeout: ASK_TIMEOUT_S env (default 1920s).
if [[ $# -lt 2 || ( "$1" != claude && "$1" != chatgpt ) ]]; then
  echo 'Usage: ask.sh <claude|chatgpt> "<question>" [outname]' >&2; exit 2
fi
export PATH=/Library/Frameworks/Python.framework/Versions/3.14/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
cd /Users/test/Applications/MultiVenueDesk/idea_feeds || exit 1
exec python3 idea_feed_runner.py --tool "$1" --ask "$2" --outname "${3:-ask}"
