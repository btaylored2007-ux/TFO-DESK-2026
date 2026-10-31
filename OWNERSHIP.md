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
- Deploy window (already the only window check in `tools/safe_deploy.sh`; the exec-speed gate uses this same rule, it is not a second clock): tick windows are the first 4 minutes of every quarter hour. A deploy may start only at :04:00 or later, and the whole run must finish before the next quarter. Expected runtime is `SAFE_DEPLOY_RUNTIME_S` (default 450s), so the legal start with that default is **:04:00 through :07:29** of each quarter.
- One deploy at a time: `tools/deploy.lock` (pid-owned; stale only if the pid is dead).
- Timestamped `.bak-YYYYMMDD-HHMMSS-TAG` per file, `py_compile`, then `tests/` and `veteran_desk/tests` vs `tools/test_baseline.json`.
- The baseline `known_failures` list is empty. Any failure rolls every file back and exits 1. Pass -> entry in DEPLOY_LOG.md.
- Do not put a test back on the baseline to make a deploy green. Fix the test or the code and record it in TEST_TRIAGE.md.
