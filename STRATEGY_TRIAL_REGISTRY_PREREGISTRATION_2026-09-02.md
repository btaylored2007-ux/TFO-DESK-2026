# Strategy trial and closed-hypothesis registry — preregistered 2026-09-02

PAPER RESEARCH ONLY. Written before the registry implementation. This creates no
strategy, signal, order or capital allocation.

## Problem

Recent research receipts disagree on the cumulative house search count. The
latest sequential harness reports 2,775 trials; later documented fan-out and
sweep work establishes a conservative floor of 3,612, and the separately
documented 32-configuration crypto-rumour grid raises the operational floor to
3,644 if it was not already included. Using an older number understates the
multiple-testing cost.

The desk also has no machine-readable refusal against rerunning economically
identical strategy families that already failed.

## Predeclared implementation

1. Bootstrap an append-only strategy-trial ledger at the conservative
   `n_trials >= 3,644` floor. The uncertainty is preserved; 3,644 is a minimum,
   not a claim of an exact historical count.
2. Every future research run reserves a named mechanism ID and exact variant
   count before testing. The resulting range is the deflation floor for that run.
3. A closed-hypothesis registry names economically answered families. Exact
   duplicates are refused unless the operator explicitly documents a material
   change in instrument, data or mechanism under a new ID.
4. Ledger rows are append-only and carry UTC, mechanism, before/after count,
   variant count, status and rationale.

## Initially closed families

- liquid-ETF time-series trend as standalone alpha;
- G10 FX carry as a distinct standalone alpha;
- clean oil/gas ETF roll-yield proxies;
- futures-flow horizons at or below two hours.

The predeclared future 4-hour futures-flow study is not closed, but it remains
ineligible until its existing 27-day, field-complete data gate is met.

## Acceptance tests

- status reports a floor of at least 3,644;
- reserving variants increments the count exactly once and appends a receipt;
- invalid or duplicate reservations fail closed;
- a closed mechanism is machine-detectable; and
- the registry never edits a historical row.

## Implementation receipt — 2026-09-02

Implemented with an exclusive append lock so parallel research labs cannot
reserve overlapping ranges. Three focused tests pass. Live status reports the
conservative floor of 3,644 and all four initially closed mechanisms.

```text
strategy_trial_registry.py             033798db4244785038f6b616ecc1b7e42301fdcdea92d3a0daadf96a43ca9cc5
tests/test_strategy_trial_registry.py  75dbf35320a8bcfd536cb16a522deab1dd3f2fde8a2449cfe045386ebaf7e602
strategy_trial_ledger.jsonl            2b6d5b693e91ace4d9cab2182a0d9113958cb8aa87e6a6e65267d10a8bf6f4c7
closed_hypotheses.json                 3fc3fb895851521af3acf04814d7ac6f715b617c45832f8a8dda04e22978bd9c
```
