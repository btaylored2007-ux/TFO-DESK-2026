# Fundamental allocator direction alignment — preregistered 2026-09-02

PAPER ONLY. Written before the allocation-filter change.

## Defect

The allocator currently builds its long pool from `VERDICT == undervalued`
without also requiring the research block's explicit `TRADE DIRECTION == LONG`.
It can therefore assign PAPER capital to a company the research desk labelled
`NEITHER`, including a name whose execution evidence is unresolved.

## Predeclared correction

New long allocation requires both `VERDICT == undervalued` and the exact leading
trade-direction token `LONG`. `NEITHER`, `SHORT`, missing or unparseable direction
receives $0 on the long side. The existing short requirements are unchanged.

This is a fail-closed alignment of two existing fields, not a new strategy. It
does not reinterpret valuation, change sizing or caps, or alter any existing
position.

## Acceptance tests

- undervalued + LONG remains eligible subject to every other gate;
- undervalued + NEITHER receives $0;
- undervalued + missing direction receives $0; and
- fairly priced + LONG remains $0.

## Implementation receipt — 2026-09-02

Implemented in `aggregate.py` and covered by the focused route/schema tests.
The implementation hash is recorded in the execution-route receipt.
