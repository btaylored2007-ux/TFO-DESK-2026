# Fundamental research schema v2 — preregistered 2026-09-02

PAPER ONLY. Written before the strict-parser change and before any v2 batch is
used for allocation.

## Scope

Historical blocks without v2 fields retain their legacy parsing so their record
is not rewritten. Once a block declares any v2 execution-route field, it is a v2
block and must be complete.

## V2 allocation contract

A v2 block must have non-empty exact fields for execution symbol, exchange,
currency and route; verdict; confidence; canonical sector; trade direction;
catalyst date and direction; own-history comparison; volume trend; KILLS IT;
sources; and liquidity. Verdict is parsed from its exact leading token, never a
substring.

`CATALYST_DATE` must be an ISO date in the future for a v2 long allocation.
`none`, malformed, missing or already-passed dates remain research records but
receive $0. The entry tool still independently checks the date and every live
gate.

Incomplete v2 blocks are named parse failures and are excluded from allocation;
they are never silently dropped. No historical output is relabelled and no
existing position changes.

## Acceptance tests

- `VERDICT: not undervalued` does not parse as `undervalued`;
- a truncated v2 block is a named parse failure;
- a complete v2 block parses;
- a v2 long with no valid future catalyst date receives $0; and
- legacy blocks continue to parse under their previous schema.

## Implementation receipt — 2026-09-02

Implemented. Eight focused tests pass, including exact verdict parsing,
truncation failure, complete local/ADR routes, and future-date allocation.
Implementation hashes are recorded in the execution-route receipt.
