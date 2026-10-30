# Fundamental execution-manifest enrichment — preregistered 2026-09-02

PAPER ONLY. Written before the allocator-enrichment implementation and before
any enriched manifest is admitted to the existing session queue.

## Purpose

Research must never select its own notional. The strict discovery manifest and
the deterministic allocator therefore remain separate receipts. A new additive
enrichment stage may combine them only after re-verifying every hash-sealed
source file and re-parsing every candidate from source.

## Eligibility contract

An execution candidate requires all of the following:

- exact leading `VERDICT: undervalued`;
- exact economic `TRADE DIRECTION: LONG` (no expansion of short expression);
- exact positive catalyst direction;
- exact future catalyst and review dates as of the enrichment run;
- stable issuer ID;
- verified execution route with the exact symbol, venue and quote currency;
- execution status `pending_session` or `ready`;
- substantive `KILLS IT`; and
- a positive notional supplied only by a separate deterministic allocator
  receipt bound to the same batch ID and source-file digest.

`RESEARCH_READY`, `MATERIAL_LEAD`, negative/two-sided catalysts, fairly priced
names, value traps, shorts, conflicts, past dates, incomplete routes and absent
notionals remain evidence or $0 outcomes. They are never translated into a
tradable verdict.

## Integrity contract

Serialized `candidates` inside the research manifest are not authoritative and
must be empty. Aggregation and enrichment re-read the verified source blocks;
candidate injection, source mutation, batch/hash mismatch, file-set changes and
duplicate allocator keys fail closed. Conflicting duplicate research symbols
are held out of execution.

The enrichment output may be passed to `fundamental_session_queue.build_queue`.
It does not verify a live venue, open a position, size capital, mutate a ledger,
or bypass the existing dry-run, liquidity, FX, concentration or portfolio gates.

## Acceptance tests

- undervalued plus `NEITHER` emits no candidate;
- fairly priced plus `LONG` emits no candidate;
- negative or two-sided catalyst emits no candidate;
- missing allocator notional emits no candidate;
- catalyst or review date on/before the explicit as-of date emits no candidate;
- source hash mutation and serialized-candidate injection are refused;
- conflicting duplicate symbols are held out; and
- a fully eligible record plus a bound positive allocator notional builds a
  session-queue-compatible `PENDING_SESSION` row with no symbol substitution.
