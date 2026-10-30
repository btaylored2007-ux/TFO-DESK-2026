#!/bin/bash
# Run ON THE MAC via Shell(machineId=61edf223-7c1d-4f19-92a9-d34c021c8074).
# Read-only: tar selected Veteran Desk state to stdout (base64). Writes nothing on the Mac
# (the agent harness may save large stdout to ~/agent-tools/<id>.txt).
# Refuses during tick minutes :00-02,:15-17,:30-32,:45-47 and while a desk tick is running.
# Durable path: /Users/test/.tfo_swing/mac_pull_cmd.sh
set -euo pipefail
cd "$HOME/Applications/MultiVenueDesk"
M=$(date +%M)
case $M in
  00|01|02|15|16|17|30|31|32|45|46|47) echo "SKIP tick-window $M"; exit 3;;
esac
# Only real tick runners (module form or named scripts) — not greps / pytest / dash_server paths
if pgrep -f '/[Pp]ython([0-9.]*)? -m veteran_desk\.(book|desk)( |$)' >/dev/null \
  || pgrep -f '/[Pp]ython([0-9.]*)? .*/veteran_desk/(book|desk)\.py( |$)' >/dev/null \
  || pgrep -f 'run_tick\.sh|run_pipeline\.sh' >/dev/null; then
  echo "SKIP desk-busy"; exit 4
fi
echo "BEGIN $(date -u +%Y-%m-%dT%H:%M:%SZ)"
tar czf - \
  --exclude='*.bak*' \
  --exclude='archive' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  veteran_desk/*.py \
  veteran_desk/seats/*.json \
  state/veteran_desk \
  state/learning \
  reports/learning \
  state/config/claude_retired.json \
  reports/veteran_desk_book.json \
  reports/firm_risk.json \
  static/veteran_desk.json \
  static/veteran_desk_health.json \
  state/veteran_desk_scorecard.json \
  state/veteran_desk_payouts.json \
  state/funder_queue.json \
  state/funder_gate.json \
  2>/dev/null | base64
echo END
