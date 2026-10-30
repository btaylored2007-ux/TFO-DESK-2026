#!/bin/bash
# Daily throttled refresh of Veteran Desk liquid scan universe (Brett breadth 2026-10-29).
# Yahoo primary; IBKR fallback inside pit.ingest_yahoo. Never mid-tick.
set -e
cd "$(dirname "$0")/.."
export PYTHONPATH=.
# tfo_seatpipe_4_20261030: Yahoo sector fill for unmapped non-US liquid names (cached; never fatal)
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -c "from veteran_desk import scan_universe as S; print('intl_sectors', S.fill_intl_sectors(limit=1500))" >> logs/scan_refresh.log 2>&1 || true
/usr/bin/python3 -c "from veteran_desk import scan_universe as S; import json; print(json.dumps(S.build(), indent=1)[:500]); print(json.dumps(S.refresh_liquid(pause=0.35), indent=1))" >> logs/scan_refresh.log 2>&1
