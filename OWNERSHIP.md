# Code ownership & deploy authority — TFO paper office (PAPER ONLY)
Approved by Brett 2026-10-30 19:44 WITA.

## Sole deployer: TFO
TFO is the ONLY agent that deploys changes to:
- veteran_desk/gate_v2.py
- veteran_desk/book.py
- veteran_desk/desk.py
- THF engine files (veteran_desk/run_thf_tick.sh, thf_dash.py, thf_dash_overlord.py, thf_review.py, and THF code paths in vd_engine.py / payouts_bridge.py)
- funder_gate.py

Seat agents and Marcus agents MUST NOT edit these files. Send TFO a change request instead
(file, exact change or diff, reason, test evidence). TFO stages it and deploys via the gate.

## How to deploy
`tools/safe_deploy.sh [--dry-run] [--tag TAG] [--note TEXT] FILE | STAGED_SRC:FILE ...`
- Deploy window (Mac, already the rule): tick windows are the first 4 minutes of every quarter hour (:00–:03, :15–:18, :30–:33, :45–:48). A deploy may start only at :04:00 or later, and the whole run must finish before the next quarter. Expected runtime is `SAFE_DEPLOY_RUNTIME_S` (default 450s), so the legal start with that default is **:04:00 through :07:29** of each quarter. :08 and later is refused because a 450s run would still be going when the next tick window opens.
- One deploy at a time: `tools/deploy.lock` (pid-owned; stale only if the pid is dead).
- Timestamped `.bak-YYYYMMDD-HHMMSS-TAG` per file, `py_compile`, then a hard gate on `veteran_desk/tests/test_exec_speed_20261031.py` (any failure refuses; the file must exist), then veteran_desk tests vs `tools/test_baseline.json`.
- Any hard-gate failure or new baseline failure -> automatic restore of all files, exit 1. Pass -> entry in DEPLOY_LOG.md.
- Change the baseline only when a known failure is fixed or deliberately accepted; log why in DEPLOY_LOG.md.
