# Build acceptance checklist — this workspace only

This is the checklist for the code in this branch. It is not the parent’s master tracker. A ticked row means the evidence named in the row exists. It does not mean the Singapore runtime was changed, restarted, or accepted.

Nothing here was deployed. No service was restarted. Old fixer ticks are not acceptance of this build. Production verification is still required before any shipping claim.

Hashes for the files this run actually executed or stored are in `docs/ACCEPTANCE_HASHES_20261106.txt`.

| ID | Requirement | State | Evidence | Not claimed |
|---|---|---|---|---|
| B1 | Keep the live net +0.7R half-bank. Do not install a second policy. | In the package, not redeployed | `veteran_desk/book.py` sha256 `b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891`, same as `docs/live_bank07/book.py`. `test_live_bank_snapshot_20261106.py` requires that match. `deploy/incremental_20261106/install.py` prints `ALREADY_PROTECTED` for that hash and `BLOCKED` for any other. `apply.sh` still exits 1 and is not the installer. | Droplet restart. A destination whose book hash differs was not overwritten. |
| B2 | Boundary, partial, idempotency on the running tick | Rerun here, not on the droplet | `tests/test_bank07_20261106.py` passed in this workspace (the 0.7 cases). Integer half 29015 → 14507. `mark_half_banked` still labels a second partial `quarter_bank`. | A replay of the BGA fill. Four `F5cCatalyst` tests in the imported fill-fix file fail: missing `filing_freshness.py`. |
| B3 | Seat docs match +0.7R net | Edited in `veteran_desk/SEAT_CLI.md` | The doc no longer tells seats the old +1R / +1% half-bank. | Not loaded by a running process. |
| F1 | Asia status must not be fresh when `last_bar` is null during a session | Tested, not deployed | `veteran_desk/feed_health.py`, `asia_intraday_sgp.py`, `thf_feedkeeper.py`. `test_feed_health_20261106.py`. | No live Asia collector run. No synthetic bars written. |
| F2 | EU names stay inside a cap; do not turn quotes on; do not treat 422 as success | Tested, not deployed | `allocate_rest` and `tools/intraday_bars_pull.py`. Same test module. `eu-quote` stays off. | No EODHD call. Entitlement file on the droplet was not read. |
| F3 | US clock, holidays, DST | Tested as a clock only | `us_rth_open` / `asia_session_open`. 07:43 New York on 6 Nov 2026 is before the open. | Not a judgment that the live feed is wrong. |
| F4 | Kraken secondary refusal, DCD 403, borrow, spread, corporate actions | Gap, left in place | Primary tape was not changed. Spread/NBBO writer was not found in the supplied pricing modules, so it is unknown, not “absent.” | No new vendor. |
| L1 | Lesson inclusion, citation, and validated impact are different records | Tested, not deployed | `veteran_desk/lesson_trace.py`, `test_lesson_trace_20261106.py`. “I learned” does not validate. | No live lesson row. Not proof anyone traded better. |
| L2 | All 18 seats have an explicit coverage state | Tested on an empty/partial log | `coverage()` returns `no_evidence` for seats with no row, including admins and seats with no closes. | Does not fill those seats. |
| L3 | `seat_brain.prompt_block` records refs it actually inserted | Tested through the brain functions | `test_integrated_paths_20261106.py` calls `lesson`, `prompt_block`, `after_chat`, then `record_validated`. Outside pytest, inclusion and citation append to `state/thf/lesson_trace.jsonl` with no extra flag. Reviews stay hypotheses. | Not observed in droplet logs. A citation is not a change in P&amp;L. |
| L4 | Durable retry test and autonomy runtime | Runtime suite passed; integration incomplete | `test_learning_retry_durable.py` and `test_autonomy_runtime_20261106.py` passed after `bridge_maintenance.py` and `thf_http.py` arrived. Three `test_autonomy_integration_20261106.py` tests fail: missing `tools/thf_dash_refresh.sh` and `veteran_desk/tests/test_intent_routing_20261104.py`. | Those three failures were not stubbed green. |
| R1 | Bare stand-aside is not finished research; 0.19R is not grade A; module does not arm | Tested on the round path | `research_mandate.py` plus `test_integrated_paths_20261106.py`: `thf_round.submit_candidate` returns `[]` and does not call `enqueue` for a below-quality candidate. A complete candidate is handed to the desk stub. Eleven trader seats are enumerated. Grade floors were not lowered. | The worktree `desk.py` overlay is not in this commit. `risk_gate` still fail-closes on missing `small_coin.py` (`test_half_risk_a_allowed_at_1pct`). FUNDAMENTAL vs VD comparison was not touched. |
| A1 | Matched four-provider shadow log, blind score, unknown cost, no winner from n&lt;20 | Fixture transport only | `thf_shadow_bench.note_production` writes one row with `shadow_calls=0` and `cost_usd=unknown`. It does not call Claude, ChatGPT, Grok, or DeepSeek. | Window not started. Not a 7-day result. |
| U1 | Transparent Trading chrome on `/desk/chat` | Fixture only | Node fixture: 6 mocked scenarios passed. Fixture screenshots: `docs/brand/screenshots/tt-home-360.png`, `tt-thread-390.png`, `tt-upload-390.png`, `tt-error-390.png`, `tt-desktop-1280.png`. Banner text is “FIXTURE — mocked APIs, not the live desk.” | Tailnet was not bypassed. Production UI not verified. |
| U2 | Name and routes | Held | Display name only. Routes, seat ids, and service names stay THF. | No trademark search. No marketing site. |
| C1 | Subscription product | Gap, separate file | `docs/COMMERCIAL_READINESS_GAPS_20261106.md`. | Not built. Not a launch. |
| P1 | Speed targets | Not measured | — | No p50/p95 claim. |
| D1 | Production deploy | Not done | `deploy/incremental_20261106/install.py` dry-run distinguishes `ALREADY_PROTECTED` (book hash matches) from `INSTALL` (other missing files) and `BLOCKED` (unexpected hash). `--apply` copies nothing when any row is blocked. Tests in `test_incremental_deploy_20261106.py`. | Not executed against `/opt/tfo/live/thf`. No service restart. Parent verification is still required. |

## Latest suite commands in this workspace

`python3 -m pytest tests/test_bank07_20261106.py veteran_desk/tests/test_learning_retry_durable.py veteran_desk/tests/test_autonomy_runtime_20261106.py veteran_desk/tests/test_integrated_paths_20261106.py veteran_desk/tests/test_incremental_deploy_20261106.py veteran_desk/tests/test_live_bank_snapshot_20261106.py`

`tests/test_fillfix_20261103.py` is imported by the bank file. Its four catalyst tests fail on missing `filing_freshness.py`. Three tests in `veteran_desk/tests/test_autonomy_integration_20261106.py` fail on the two paths in `docs/LIVE_SOURCE_REQUIREMENTS.md`. Chat node fixture: 6 passed, requests mocked.

## Gap G1 — do not hide

`veteran_desk/desk.py` in the worktree is the live desk overlay, including research hooks. It is not in this commit. `risk_gate` on an A-grade 1% half-risk case returns `passed: false` with reason `small_coin check error ImportError - fail closed`, while `risk_pct` stays 0.01 and `risk_aud` stays 5000. `b_tight.py` is now present. `small_coin.py` is not. The threshold was not lowered.

The THF round path that this commit does include is `thf_round.submit_candidate`. A bad `research_candidate` is not enqueued. Shadow logging records a production answer and does not call a provider.

## Files still required

See `docs/LIVE_SOURCE_REQUIREMENTS.md`. Production completion is not claimed.
