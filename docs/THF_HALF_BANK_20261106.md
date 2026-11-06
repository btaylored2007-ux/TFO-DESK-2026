# Half-bank status — 2026-11-06

The running Singapore tick already half-banks at **net +0.7R**. This repository must not replace that file with an older copy or with a second policy module.

## Verified from the attached live snapshot (not from a restart we ran)

- Execution: `tfo-thf-tick.timer` every 2 minutes → `run_thf_tick_sgp.sh` → `python -m veteran_desk.book tick`.
- `vd_engine` does not write the book rails.
- Live `veteran_desk/book.py` sha256 `b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891`.
- Constants in that file: `RAIL_BANK_R = 0.7`, `RAIL_BANK_ALL_POSITIONS = True`, `RAIL_BANK_R_SEAT = {}`, rule id `bank07_20261106`.
- `RAIL_HALF_R = 2.0` remains a separate gross high-water / target path inside the same once-only half block. After `t1_done`, that block does not sell another half.
- A second call to `mark_half_banked` when `t1_done` is already set labels the event `quarter_bank` and sets `t2_done`. That is the existing manual-partial behavior, not a new remainder rule. The automatic quarter is still `t2_trigger` at net R >= 2.
- Reported BGA fill: 14,507 of 29,015 (integer half; 29,015 // 2 = 14,507, residual 14,508), reason 0.7R, stop tightened. That row was not replayed in this workspace.
- QBE `risk_aud = 0` does not replace `r0`. `bank_trigger` uses `net_R` from the original stop (`risk0_aud`), not the `risk_aud` field. If costs cannot be priced, net R stays unknown; do not invent it.
- Droplet test named in the snapshot: `tests/test_bank07_20261106.py`. This workspace reran that file after the dependency pack arrived. The 0.7 cases passed. Four catalyst cases in the imported `tests/test_fillfix_20261103.py` still fail because `veteran_desk/filing_freshness.py` is absent.

## What this checkout loads

`veteran_desk/book.py` is now the same bytes as `docs/live_bank07/book.py` (sha256 `b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891`). `RAIL_BANK_R = 0.7` and `RAIL_BANK_ALL_POSITIONS = True`. Top-level import succeeds. `small_coin` is still a lazy import and is not in the tree; `desk.risk_gate` fail-closes when that check cannot import it.

Do not copy `staged_fixes/*/book.py`. That copy is `RAIL_BANK_R = 1.0` and is not the live tick.

`deploy/incremental_20261106/install.py` dry-runs a destination tree. A destination book with this hash prints `ALREADY_PROTECTED` and is not rewritten. Any other destination hash prints `BLOCKED`, and `--apply` copies nothing. `deploy/thf_half_bank_20261106/apply.sh` still exits 1. It is not the installer.

Still absent, listed in `docs/LIVE_SOURCE_REQUIREMENTS.md`: `veteran_desk/filing_freshness.py`, `veteran_desk/small_coin.py`, `tools/thf_dash_refresh.sh`, `veteran_desk/tests/test_intent_routing_20261104.py`.

## Parallel module

`veteran_desk/thf_half_bank.py` was drafted before this snapshot arrived. It is removed. Do not install it beside the live tick.
