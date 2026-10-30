#!/bin/bash
# HT 22:45 ruling: FMP parallel ranking deploy, SEPARATE run, only at/after 05:50 WITA 31 Oct.
# Deploys EXACTLY staged value_hunt.py + paid_feeds.py via safe_deploy; appends top-30 Yahoo-vs-FMP diff to DEPLOY_LOG;
# snapshots both rankings to reports/rank_compare/ (2-week side-by-side). PAPER only.
set -u; cd "$(dirname "$0")/.." || exit 1
S=staged_fixes/paid_feeds_stage_20261031; TS=$(date +%Y%m%d_%H%M%S); RC=reports/rank_compare; mkdir -p $RC
now=$(date +%Y%m%d%H%M); [ "$now" -ge 202610310550 ] || { echo "REFUSED: before 05:50 WITA 31 Oct"; exit 2; }
q=$(( 10#$(date +%M) % 15 )); [ $q -ge 4 ] && [ $q -le 8 ] || { echo "REFUSED: minute Q=$q not in 4-8 (between ticks)"; exit 3; }
pgrep -f value_deploy >/dev/null && { echo "REFUSED: value_deploy running"; exit 3; }
pgrep -f value_hunt >/dev/null && { echo "REFUSED: value_hunt running"; exit 3; }
shasum -a 256 -c $S/BASE_SHA256_v2 || { echo "REFUSED: live value_hunt.py/paid_feeds.py changed since staging (BASE_SHA256_v2)"; exit 4; }
python3 -m pytest -q veteran_desk/tests/test_no_direct_yf_20261030.py || { echo "REFUSED: no-direct-Yahoo test failed pre-deploy"; exit 5; }
cp -p reports/value_rank.json $RC/value_rank_yahoo_$TS.json
python3 - <<'P' || { echo "REFUSED: desk_lock"; exit 6; }
import subprocess, sys; sys.path.insert(0, "."); import desk_lock
with desk_lock.ledger_lock("fundamental", timeout=120):
    r = subprocess.run(["bash", "tools/safe_deploy.sh", "--tag", "fmp-ranking", "--note",
        "HT 22:45: value_hunt FMP-first parallel (10 workers, 1500/min token bucket, breaker, shared P/B gate, Yahoo fallback per name) + paid_feeds token bucket. Rehearsed /tmp/pfd2 442s.",
        "staged_fixes/paid_feeds_stage_20261031/value_hunt.py:value_hunt.py", "staged_fixes/paid_feeds_stage_20261031/paid_feeds.py:paid_feeds.py"])
    sys.exit(r.returncode)
P
python3 -m pytest -q veteran_desk/tests/test_no_direct_yf_20261030.py || echo "WARN: no-direct-Yahoo test failed POST-deploy"
F=$(ls -t $RC/value_rank_fmp_rehearsal_*.json | head -1)
{ echo; echo "## $(date '+%Y-%m-%d %H:%M WITA') fmp-ranking deploy - top-30 Yahoo vs FMP (rehearsal output; rerun tools/rank_compare.py after first live FMP value_hunt)"; python3 tools/rank_compare.py $RC/value_rank_yahoo_$TS.json "$F" 30; } >> DEPLOY_LOG.md
echo "done; snapshot $RC/value_rank_yahoo_$TS.json. After the first live FMP run: cp reports/value_rank.json $RC/value_rank_fmp_\$(date +%Y%m%d_%H%M%S).json"
