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
- Droplet test named in the snapshot: `tests/test_bank07_20261106.py`, 7 passed, 1.51s. This workspace did **not** rerun it. The helper `test_fillfix_20261103.py` was not in the archive.

## What this checkout can and cannot import

The parked live file is `docs/live_bank07/book.py` (same hash). It is not installed as `veteran_desk/book.py` here because that module imports `session_expiry`, `b_tight`, `small_coin`, `seat_risk`, `eodhd_eu_ws`, and `pit_fx_sgp`, which are not in this tree. Copying it into the package breaks import. The GitHub `veteran_desk/book.py` still has the older `RAIL_BANK_R = 1.0`. **Do not deploy that older file onto the droplet.**

Exact files needed before this repo can load the live tick: `veteran_desk/session_expiry.py`, `veteran_desk/b_tight.py`, `veteran_desk/small_coin.py`, `veteran_desk/seat_risk.py`, `veteran_desk/eodhd_eu_ws.py`, `veteran_desk/pit_fx_sgp.py`, and `tests/test_fillfix_20261103.py`.

## Parallel module

`veteran_desk/thf_half_bank.py` was drafted before this snapshot arrived. It is removed. Do not install it beside the live tick.
