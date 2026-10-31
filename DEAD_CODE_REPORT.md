# Dead code report

Paper-only desk. Phase 1 is this report. Later commits on this branch apply only the high-confidence rows. Anything on a risk, stop, exit, ledger, audit, backup, or watchdog path was left for a person unless the dead branch was provably unreachable, in which case only that unreachable branch was deleted and the live check stayed.

## How a candidate was judged

Three checks, all required before anything was called high confidence:

1. **Vulture 2.16** on the live tree (staged snapshots excluded so duplicate copies are not treated as callers). Unreachable code and unused imports are reported at 90–100%. Functions top out at 60% in vulture, so a function was not removed on that score alone.
2. **Import graph.** Every `import` / `from` was parsed with `ast`. A module imported by a test, a desk module, or another live module is live.
3. **Outside references.** Launchd `ProgramArguments` (including `python -m`), shell scripts (`desk_tick.sh` `run` lines and the other `*.sh` files), cron comments, dashboards (`*.html`), and the CLIs. A name that appears only as a string, a plist target, or a dynamic import is **not** dead.

`staged_fixes/`, `staging/`, and `work/stage/` are in-flight copies of live files. They are not on the import path. They were not archived: a later `safe_deploy.sh SRC:DEST` still points at them by path.

## Removed — high confidence

### Stale scripts (moved to `archive/dead_code/`, not deleted)

| File | Evidence | Confidence |
|---|---|---|
| `tools/asia/patch_asx.py` | One-shot source rewriter from 2026-10-27. Nothing imports it, no shell or plist names it. The strings it requires (`bench_note="IBKR cost gate: "` without `demoted_utc`) are already gone from `asx_hunt48.py` (the sticky-demotion lines are in place). Running it would fail its own `assert count == 1`. | High |
| `tools/asia/patch_clearance.py` | Same pattern for `hunt_clearance.py`. `ASIA_MKTS` is already in the live file. Zero references outside the script. | High |
| `veteran_desk/merge_pit_20261028.py` | Docstring: one-shot PIT merge dated 2026-10-28. Not imported, not named by a shell, plist, test, or dashboard. No `--daily` mode. | High |

Restore: `git mv` the file back to the path recorded below, or `cp archive/dead_code/<same relative path> <original path>`.

### Unreachable code deleted in place (behaviour unchanged)

Vulture marked these at 100%. Each one is an unconditional `return` / `raise` / `if False` / `if True` whose other branch cannot run. The live branch was left as it is.

| Location | What was unreachable | Why it is safe |
|---|---|---|
| `hunt_clearance.claude_probe` | Statements after `return "RETIRED", ...` | Claude was retired 2026-10-29. Callers already receive `RETIRED`. Tests that still expect `PENDING` from `claude_review` were already failing on that early return; deleting the tail does not change it. |
| `hunt_clearance.claude_review` | Statements after `CLAUDE_STATE.update(status="RETIRED"); return {}` | Same. |
| `veteran_desk.llm_backend.backend` | Codex/`revert_at` block after `return "grok_queue"` | Brett 22:29 WITA 30 Oct: every desk is Grok. The function already always returns `grok_queue`. |
| `trend18.build_funding_plan`, `sell_staged`, `cmd_sell_staged_only` | Bodies after `raise SystemExit(NO_OTHER_BOOK_SALES)` | The raise is the live rule (TFO Risk 29 Oct: Trend18 never sells another book). `tests/test_trend18_funding_no_other_books_20261029.py` asserts that `SystemExit`. The dead sale loop cannot run. |
| `trend18.fit_orders`, `trend18.funding_candidates` | Whole functions | No caller. `resolve_funding` replaced `fit_orders`. `funding_candidates()` is the old other-book sale ranker; the funding test asserts `cmd_stage` does not call it. Not a string dispatch. |
| `desk_costs.round_trip_bps` | Whole function | No caller. `schedule_table` inlines the same arithmetic. `fill_fees` is untouched. |
| `command_server._pw_compute` | Nested `if True` / `if True` / `pass` | The watchdog, funder, and sleeve reads inside stay. Only the always-true wrappers go. |
| `veteran_desk.desk._mark_one` | `if True:` around the marker | The stop/target marker itself is unchanged; the wrapper was a no-op. |
| `oanda_aggro` entry duplicate check | `if True else None` | Always takes the `S.api(...)` side. |
| `ws_tape_collector.run_session` | `return connected_at` after `while True` | The loop only leaves by exception, and an exception skips the return after `finally`. `main_loop` already handles that exception. |
| `veteran_desk.vd_engine.oanda_yahoo` | `(b + "=X") if a == "USD" and False else ...` | `and False` makes the condition never true. Result is always `a + b + "=X"`, which is what the line already returned. |
| `tfo_crypto.cmd_scan` | `ws.replace(...) if False else ws` | Always `ws`. |
| `tfo_bank_rail_scanner` | First `nearest = sorted(...)` | Overwritten before any read. The second assignment (the live nearest-rail list) stays. This is not the stop execution path. |
| `macro_event_data_probe.parse_calendar` | `"unscheduled": ... and False` | Always `False`. Stored as `False`. |
| `overlay_lab.scheme_metrics` | Unused parameter `gross_mult` | No caller passes it. |
| `tools/health_overlord.py` | Unused import `py_compile` | The compile check shells out to `python3 -c "import py_compile..."` and does not use the import. |

## Flagged for a person — not removed

These are unused by the import graph, or vulture called them unused, and they sit on a protected path or are an operator CLI with no retirement note. They were not deleted.

| Candidate | Why it looks dead | Why it stayed |
|---|---|---|
| `book_coverage._load_oanda` | No caller. Vulture 60%. | The function comment and `DECOMMISSION_20260828.md` say the reader is the only remaining description of OANDA positions that could not be closed. Ledger path. |
| `book_coverage._load_punt`, `_load_dormant_desk`, `_load_exit_shadow` | No caller. | Ledger / dormant-book / exit-shadow readers. Exit and audit. |
| `risk_engine.remark` | No caller. | Risk engine. |
| `venue_costs.max_leverage` | The function is uncalled. The `max_leverage` **config keys** are used by `replica_floor`. | Leverage is a risk limit. Only the unused function was in question; the keys stay, and the function was left too. |
| `veteran_desk.book.open_net_aud`, `payouts._read_cut_seats` | Vulture, no other reference. | Book and payout ledger. |
| `veteran_desk.ibkr_hist.daily_bars(..., end_date=None)` | Parameter never read. | Public read-only history API. Dropping the argument would break a caller that passes it by keyword. |
| `veteran_desk.pos_live.seat_view(..., seat_eq=None)` | Parameter never read. Callers pass only the book. | Dashboard position view. Signature left intact. |
| `fundamental_refresh.py` | No import, plist, or shell reference. | Docstring: 5-minute mark of both fundamental books. Writes ledgers. Not provably retired — the job may be fired by a box routine that is not in this export. |
| `book_spec_sleeve.py` | No reference. | Approved 28 Oct sleeve writer (`init`/`status`). Writes `state/spec_sleeves`. Ledger. |
| `fundamental_catalyst_hygiene.py` | No reference. | Writes catalyst dates onto the fundamental book. Can move an exit. |
| `fundamental_frontier_reconciliation.py` | No reference. | Read-only audit over the hunt ledger. |
| `fundamental_post_exit.py` | Only named by `fundamental_refresh.py`, which itself has no caller. | Exit path. |
| `tfo_crypto_recovery_status.py` | No reference. | Read-only ledger recovery measurement. |
| `killer_forward_test.py`, `futures_panel_coverage.py` | No reference. | Measurement / collection-design audit. Operator CLIs. |
| `veteran_desk/gate_calibrate.py` | No reference. CLI: `python3 -m veteran_desk.gate_calibrate`. | Training calibration of the live gate. Not the gate itself, but adjacent. |
| `veteran_desk/backfill_kraken_ohlc.py`, `tools/eodhd_backfill_20261030.py` | No reference. | Both are data backfills with a repeatable mode (`--daily`, `desk\|full`). Not one-shots that have clearly finished. |
| `tools/health_overlord.py` line `if False and m and ...` | The stale-log check is hard-disabled. | Watchdog-style health check. Deleting the line removes the disabled check; turning `False` into `True` would change behaviour. Left for a person. |
| Research labs with a `__main__` and no caller (`crypto_basis_lab.py`, `carry_deepening_lab.py`, `gold_basis_lab.py`, and the other `*_lab.py` files whose only mention is `yf_callsite_baseline.json`) | Not on the tick. | They are the research CLIs. A baseline json mention is a string reference, so they are not dead under the rule above. The two labs with **zero** mentions are listed here rather than archived, because nothing says they were retired. |
| Retired books that still have readers | `desk_tick.sh` comments say do not run `growth_book.py`, `crypto_rumour_desk.py`, `dated_event_book.py`, `punt_24h.py`, `carry_paper_desk.py`, `carry_newnames_probe.py`. Each CLI raises or returns immediately. | `command_server`, `firm_risk`, `exit_axis`, and `book_coverage` still name the ledgers or the modules. String and dashboard references. Not removed. |
| `paid_feeds.fmp_fundamentals` | No live caller. | The paid-feed function. `staged_fixes/paid_feeds_stage_20261031/value_hunt.py` calls the same name on the staged copy. Not a retired feed. |
| CoinGlass | No live client. | See below. |

## CoinGlass

Retired 2026-10-30 22:10 WITA. Coinalyze is the crypto derivatives source.

- Live `veteran_desk/thf_paid_pack.py` has no `coinglass_oi` entry and no `open-api-v4.coinglass` URL. `veteran_desk/tests/test_coinalyze_newspaid_20261030.py` locks that in.
- The old client is only in `staged_fixes/paidpush_20261030/thf_paid_pack.py` (`coinglass_funding`) and in prompt strings in two staged `desk.py` copies. Those directories are deploy snapshots, not the running pack. They were not edited, so a historical snapshot stays restorable.
- `staged_fixes/datahub/data_hub.py` lists `coinglass` in `BLOCKED` and refuses the call. That is a block, not a client. Left in place.

## Launchd targets already missing from this export

Not caused by this cleanup. `tests/test_launchd_plist_targets.py` (also run from `veteran_desk/tests/test_plist_targets_guard.py`) fails if any **other** plist target disappears.

| Plist | Target | Note |
|---|---|---|
| `launchd/com.tfo.entry-engine.plist` | `research/entry_engine/entry_engine.py` | `research/` is not in this code-only export |
| `launchd/com.tfo.entry-engine-equity.plist` | `research/entry_engine/equity_engine.py` | same |
| `launchd/com.tfo.oanda-lab.plist` | `research/oanda_deep_20261027/code/lab_job.py` | same |
| `launchd/com.tfo.hunt48.asx.plist` | `state/hunt48_asx/run_asx.sh` | runtime copy; no `tools/asia/deploy/run_asx.sh` in the repo. The other Asia runners do have repo sources and the test checks those. |
| `staged_fixes/datahub/com.tfo.massive-ws.plist` | `massive_ws.py` at the desk root | The file that exists is `staged_fixes/datahub/massive_ws.py`. The test requires that copy to stay. |

## What was not treated as dead

- Anything a plist, `desk_tick.sh`, or another shell script actually runs.
- `yf_callsite_baseline.json` mentions (string references).
- Side-effect imports. The only unused import removed is `py_compile` in the health overlord, which is not a registration import.
- Pytest hook arguments (`pytest_sessionfinish(..., exitstatus)`). Vulture calls `exitstatus` unused. It is part of the hook signature.
- Unittest methods that do not start with `test_` in `tests/test_regression_audit_20261027.py`. Vulture does not know unittest. They were not deleted.
