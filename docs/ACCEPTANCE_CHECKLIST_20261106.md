# Build acceptance checklist — this workspace only

This is the checklist for the code in this branch. It is not the parent’s master tracker. A ticked row means the evidence named in the row exists. It does not mean the Singapore runtime was changed, restarted, or accepted.

Nothing here was deployed. No service was restarted. Old fixer ticks are not acceptance of this build. Production verification is still required before any shipping claim.

Hashes for the files this run actually executed or stored are in `docs/ACCEPTANCE_HASHES_20261106.txt`.

| ID | Requirement | State | Evidence | Not claimed |
|---|---|---|---|---|
| B1 | Keep the live net +0.7R half-bank. Do not install a second policy. | Preserved, not redeployed | Live `book.py` sha256 `b4453b50…` parked at `docs/live_bank07/book.py`. `RAIL_BANK_R = 0.7` asserted by `test_live_bank_snapshot_20261106.py`. `deploy/thf_half_bank_20261106/apply.sh` exits 1. Package `veteran_desk/book.py` is still the older GitHub file (`RAIL_BANK_R = 1.0`) and must not be copied onto the droplet. | Droplet restart. The archive’s 7 tick tests were not rerun (`test_fillfix_20261103.py` and `session_expiry.py` are absent). |
| B2 | Boundary, partial, idempotency on the running tick | Not run here | `mark_half_banked` text in the parked file sets `t2_done` / `quarter_bank` on a second partial. Integer half 29015 → 14507 is checked as arithmetic only. | A replay of the BGA fill or a second tick. |
| B3 | Seat docs match +0.7R net | Edited in `veteran_desk/SEAT_CLI.md` | The doc no longer tells seats the old +1R / +1% half-bank. | Not loaded by a running process. |
| F1 | Asia status must not be fresh when `last_bar` is null during a session | Tested, not deployed | `veteran_desk/feed_health.py`, `asia_intraday_sgp.py`, `thf_feedkeeper.py`. `test_feed_health_20261106.py`. | No live Asia collector run. No synthetic bars written. |
| F2 | EU names stay inside a cap; do not turn quotes on; do not treat 422 as success | Tested, not deployed | `allocate_rest` and `tools/intraday_bars_pull.py`. Same test module. `eu-quote` stays off. | No EODHD call. Entitlement file on the droplet was not read. |
| F3 | US clock, holidays, DST | Tested as a clock only | `us_rth_open` / `asia_session_open`. 07:43 New York on 6 Nov 2026 is before the open. | Not a judgment that the live feed is wrong. |
| F4 | Kraken secondary refusal, DCD 403, borrow, spread, corporate actions | Gap, left in place | Primary tape was not changed. Spread/NBBO writer was not found in the supplied pricing modules, so it is unknown, not “absent.” | No new vendor. |
| L1 | Lesson inclusion, citation, and validated impact are different records | Tested, not deployed | `veteran_desk/lesson_trace.py`, `test_lesson_trace_20261106.py`. “I learned” does not validate. | No live lesson row. Not proof anyone traded better. |
| L2 | All 18 seats have an explicit coverage state | Tested on an empty/partial log | `coverage()` returns `no_evidence` for seats with no row, including admins and seats with no closes. | Does not fill those seats. |
| L3 | `seat_brain.prompt_block` records refs it actually inserted | Code present | Outside pytest it appends an inclusion row. The reader still labels reviews as hypotheses. | Not observed in `logs` on the droplet. |
| L4 | Durable retry test and autonomy runtime | Not accepted | `test_learning_retry_durable.py` needs `bridge_maintenance.py`. Autonomy tests need `thf_http.py` (26 failed, 9 passed in this workspace). | Do not treat the 9 passes as the suite. |
| R1 | Bare stand-aside is not finished research; 0.19R is not grade A; module does not arm | Tested | `veteran_desk/research_mandate.py`, `test_research_and_shadow_20261106.py`. | Not wired into the committed `desk.py` (see gap G1). Grade floors were not lowered. FUNDAMENTAL vs VD comparison was not touched. |
| A1 | Matched four-provider shadow log, blind score, unknown cost, no winner from n&lt;20 | Tested with a fixture transport | `veteran_desk/thf_shadow_bench.py` in the same test module. | No live Claude/ChatGPT/Grok/DeepSeek call. Window not started. Not a 7-day result. |
| U1 | Transparent Trading chrome on `/desk/chat` | Fixture only | `assets/brand/*`, `tools/thf_chat.js`, `tools/thf_chat_page.html`. Node fixture: 6 mocked scenarios passed. Screenshot `docs/brand/screenshots/tt-home-360.png` (360px, local mock). | 390 and 1280 shots were not saved. Tailnet returned 403 earlier and was not bypassed. Production UI not verified. |
| U2 | Name and routes | Held | Display name only. Routes, seat ids, and service names stay THF. | No trademark search. No marketing site. |
| C1 | Subscription product | Gap, separate file | `docs/COMMERCIAL_READINESS_GAPS_20261106.md`. | Not built. Not a launch. |
| P1 | Speed targets | Not measured | — | No p50/p95 claim. |
| D1 | Production deploy | Not done | Rollback for the removed parallel bank module is “do not install it.” Feed and learning files have no droplet apply in this commit. | Hash-precondition apply on the server still required. |

## Final test command in this workspace

`python3 -m pytest veteran_desk/tests/test_research_and_shadow_20261106.py veteran_desk/tests/test_feed_health_20261106.py veteran_desk/tests/test_lesson_trace_20261106.py veteran_desk/tests/test_live_bank_snapshot_20261106.py`

Result: 18 passed. Chat fixture: 6 passed, requests mocked.

## Gap G1 — do not hide

The droplet `desk.py` in the worktree calls `research_mandate`, but that file is the live desk, not the older GitHub `desk.py`. Committing it fails `test_review_20261030.py::test_half_risk_a_allowed_at_1pct` because `passed` becomes false while `risk_pct` stays 0.01. The extra fail-closed path is from missing `b_tight.py`, which this archive did not include. The threshold was not changed to make the test pass, and the live desk file was not committed.

## Files still required before a droplet patch

`veteran_desk/session_expiry.py`, `b_tight.py`, `small_coin.py`, `seat_risk.py`, `eodhd_eu_ws` dependencies already in tree where noted, `pit_fx_sgp.py`, `bridge_maintenance.py`, `thf_http.py`, `tests/test_fillfix_20261103.py`.
