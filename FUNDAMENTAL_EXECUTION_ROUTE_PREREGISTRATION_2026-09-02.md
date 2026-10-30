# Fundamental execution-route allocation — preregistered 2026-09-02

PAPER ONLY. Written before the allocator change and before any capital is
allocated through the new fields.

## Problem

The discovery schema currently says `US_LISTED` alone determines actionability.
The PAPER book and price engine already support verified local listings, but the
allocator gives every local-only candidate $0. This is a routing mismatch, not a
valuation or risk rule, and it can delete the very international opportunities
the standing hunt is intended to discover.

## Predeclared change

New research blocks may supply an exact `EXEC_SYMBOL`, `EXEC_EXCHANGE`,
`EXEC_CCY`, and `EXEC_ROUTE` (`direct_local`, `adr:<ratio>`, or `unverified`).
The allocator will use a verified direct local or ADR execution symbol for PAPER
pricing and capital allocation. `unverified`, incomplete, unpriceable, or
currency-inconsistent routes receive $0.

Legacy blocks without the four new fields retain their existing `US_LISTED`
behaviour so historical results are not reinterpreted. A new block explicitly
marked `unverified` may not fall back to the legacy field.

## Unchanged controls

All valuation verdict, trade-direction, conflict, dual-listing, capital,
concentration, quote-currency, catalyst, entry-time liquidity and session rules
remain unchanged. This adds no broker and opens no live trade. The entry tool is
the final fail-closed authority and independently remeasures the route in session.

## Acceptance tests

- a complete `direct_local` block uses its exact local symbol;
- a complete ADR block uses its exact ADR symbol;
- `unverified` or incomplete new routes receive $0 even if `US_LISTED=yes`;
- legacy blocks retain their old behaviour; and
- provider/declared quote-currency conflicts remain ineligible.

## Implementation receipt — 2026-09-02

Implemented additively in the research allocator. Eight focused route/schema
tests pass; the allocator also compiles cleanly. Historical blocks with no v2
fields keep their legacy actionability test.

```text
aggregate.py               85aede472d081a8b44f96a26265adab8eb92aecde1626d6101ea980fa95c153c
test_aggregate_routes.py  b4ba01805f8d9244db0bdf9edab482538613e27ed06ea4dd69efb45899d7fa60
```
