# Build acceptance checklist — this workspace only

This is the checklist for the code in this branch. It is not the parent’s master tracker. A ticked row means the evidence named in the row exists. It does not mean the Singapore runtime was changed, restarted, or accepted.

Nothing here was deployed. No service was restarted. Old fixer ticks are not acceptance of this build. Production verification is still required before any shipping claim.

Hashes for the files this run actually executed or stored are in `docs/ACCEPTANCE_HASHES_20261106.txt`.

| ID | Requirement | State | Evidence | Not claimed |
|---|---|---|---|---|
| B1 | Keep the live net +0.7R half-bank. Do not install a second policy. | In the package, not redeployed | `veteran_desk/book.py` sha256 `b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891`, same as `docs/live_bank07/book.py`. `test_live_bank_snapshot_20261106.py` requires that match. `deploy/incremental_20261106/install.py` prints `ALREADY_PROTECTED` for that hash and `BLOCKED` for any other. `apply.sh` still exits 1 and is not the installer. | Droplet restart. A destination whose book hash differs was not overwritten. |
| B2 | Boundary, partial, idempotency on the running tick | Rerun here, not on the droplet | `tests/test_bank07_20261106.py` passed. The four `F5cCatalyst` tests passed after `filing_freshness.py` arrived. Integer half 29015 → 14507. `mark_half_banked` still labels a second partial `quarter_bank`. | A replay of the BGA fill. |
| B3 | Seat docs match +0.7R net | Edited in `veteran_desk/SEAT_CLI.md` | The doc no longer tells seats the old +1R / +1% half-bank. | Not loaded by a running process. |
| F1 | Asia status must not be fresh when `last_bar` is null during a session | Tested, not deployed | `veteran_desk/feed_health.py`, `asia_intraday_sgp.py`, `thf_feedkeeper.py`. `test_feed_health_20261106.py`. | No live Asia collector run. No synthetic bars written. |
| F2 | EU names stay inside a cap; do not turn quotes on; do not treat 422 as success | Tested, not deployed | `allocate_rest` and `tools/intraday_bars_pull.py`. Same test module. `eu-quote` stays off. | No EODHD call. Entitlement file on the droplet was not read. |
| F3 | US clock, holidays, DST | Tested as a clock only | `us_rth_open` / `asia_session_open`. 07:43 New York on 6 Nov 2026 is before the open. | Not a judgment that the live feed is wrong. |
| F4 | Kraken secondary refusal, DCD 403, borrow, spread, corporate actions | Gap, left in place | Primary tape was not changed. Spread/NBBO writer was not found in the supplied pricing modules, so it is unknown, not “absent.” | No new vendor. |
| L1 | Lesson inclusion, citation, and validated impact are different records | Tested, not deployed | `veteran_desk/lesson_trace.py`, `test_lesson_trace_20261106.py`. “I learned” does not validate. | No live lesson row. Not proof anyone traded better. |
| L2 | All 18 seats have an explicit coverage state | Tested on an empty/partial log | `coverage()` returns `no_evidence` for seats with no row, including admins and seats with no closes. | Does not fill those seats. |
| L3 | `seat_brain.prompt_block` records refs it actually inserted | Tested through the brain functions | `test_integrated_paths_20261106.py` calls `lesson`, `prompt_block`, `after_chat`, then `record_validated`. A second process reads the same trace file. Outside pytest, inclusion and citation append to `state/thf/lesson_trace.jsonl` with no extra flag. Reviews stay hypotheses. | Not observed in droplet logs. A citation is not a change in P&amp;L. |
| L4 | Durable retry test and autonomy runtime | Integration mostly run | Durable retry, autonomy runtime, and the autonomy integration file passed after the closure pack. The dashboard refresh test ran the real script. | `test_intent_routing_20261104.py::test_advisor_uses_original_intent_and_respects_ask` still needs `queue_answerer_sgp.py`, which was not in the pack. |
| R1 | Bare stand-aside is not finished research; empty output is not a forced trade | On the round path, floor unchanged | `thf_round.admit` calls `research_mandate.record_round`. Eleven empty outputs stay `hunt_satisfied` false and `forced_trade` false. `test_half_risk_a_allowed_at_1pct` passed with `small_coin.py` present. `risk_pct` stayed 0.01. | The floor was not lowered. This is not a droplet replay. |
| A1 | Matched four-provider shadow trial on the existing clients | Wired, not executed against live credentials | `thf_shadow_sampler.py` calls `claude_brain.ask`, `chatgpt_bridge.ask`, `thf_grok.ask`, and `deepseek_bridge.ask`. `tfo-thf-shadow.timer` runs `python -m veteran_desk.thf_shadow_sampler once`. Rounds only queue the prompt. `test_shadow_sampler_20261106.py`: four calls, one prompt, unavailable is not scored, window stays off until a paired answer, fixture rows do not open it, restart does not repeat a finished task. | This machine did not use live credentials. No winner. Seven-day window not started. |
| U1 | Transparent Trading chrome on `/desk/chat` | Fixture only | Node fixture: 6 mocked scenarios passed. Fixture screenshots: `docs/brand/screenshots/tt-home-360.png`, `tt-thread-390.png`, `tt-upload-390.png`, `tt-error-390.png`, `tt-desktop-1280.png`. Banner text is “FIXTURE — mocked APIs, not the live desk.” | Tailnet was not bypassed. Production UI not verified. |
| U2 | Name and routes | Held | Display name only. Routes, seat ids, and service names stay THF. | No trademark search. No marketing site. |
| C1 | Subscription product | Gap, separate file | `docs/COMMERCIAL_READINESS_GAPS_20261106.md`. | Not built. Not a launch. |
| P1 | Speed targets | Not measured | — | No p50/p95 claim. |
| D1 | Production deploy | Bundle ready, not applied | `deploy/incremental_20261106/install.py` dry-run, `--apply`, and `--rollback`. Book hash match is `ALREADY_PROTECTED`. A recorded base hash is `REPLACE`. Any other hash is `BLOCKED` and apply copies nothing. Timer units and `APPLY.md` are in that directory. The downloadable archive is `deploy/incremental_20261106/thf-increment-20261106.tar.gz`. | Not executed on the droplet. `tfo-thf-tick` must not be restarted for this increment. Parent verification is still required. |

## Latest suite commands in this workspace

`python3 -m pytest tests/test_bank07_20261106.py veteran_desk/tests/test_learning_retry_durable.py veteran_desk/tests/test_autonomy_runtime_20261106.py veteran_desk/tests/test_integrated_paths_20261106.py veteran_desk/tests/test_incremental_deploy_20261106.py veteran_desk/tests/test_live_bank_snapshot_20261106.py`

`tests/test_fillfix_20261103.py` catalyst cases passed with `filing_freshness.py`. `test_half_risk_a_allowed_at_1pct` passed with `small_coin.py`. Chat node fixture: 6 passed, requests mocked.

## Gap G1 — do not hide

`veteran_desk/desk.py` is the live file `c5d24291…` plus the research-mandate calls. `THF_DESK=1` still delegates `desk.run` to `thf_round.run`. The 1% floor was not changed. `queue_answerer_sgp.py` is still absent, so one intent-routing test is red. All checks have not passed, and nothing has been applied on the droplet.

The matched trial is `thf_shadow_sampler`, not a production-answer row with `shadow_calls` 0. That older helper remains in `thf_shadow_bench.note_production` and does not start the window.

## Files still required

See `docs/LIVE_SOURCE_REQUIREMENTS.md`. Production completion is not claimed.
