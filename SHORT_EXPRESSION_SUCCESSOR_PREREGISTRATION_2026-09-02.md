# Cash-vs-short expression successor shadow — preregistered 2026-09-02

PAPER OBSERVATION ONLY. No frozen floor rule, position, signal, order or capital
is changed.

## Motivation

Across the Kraken and replica floors, 66 historical closed shorts lost
US$19,431.77 while closed longs made US$1,655.79. This is motivation, not a
forward result and not an annualised forecast.

## Fixed shadow

At creation, every existing closed trade in both source ledgers is fingerprinted
as `pre_start` context. On later runs, each newly observed closed trade is added
once to an append-preserving shadow record. For every future short, the two arms
are:

- frozen source strategy: its actual recorded after-cost `pnl`;
- cash expression: exactly US$0.

Longs are recorded as context but are not part of the short-expression test.
The shadow does not suppress or alter any source trade.

## Evidence bar

No promotion or economic conclusion before both 182 elapsed days and at least
100 forward closed/effective short bets. Any successor must beat cash after all
costs with a positive lower confidence bound, purged/embargoed validation and
deflated Sharpe using the canonical house trial ledger. It must not merely be a
Bitcoin-beta switch.

## Acceptance tests

- first run quarantines all history as pre-start;
- one later short appears exactly once in the forward sample;
- repeated runs are idempotent;
- actual and cash arms reconcile arithmetically; and
- unreadable or structurally invalid source ledgers fail without rewriting the
  last good shadow.

## Implementation receipt — 2026-09-02

Implemented and initialized at 2026-09-02T06:27:40Z. Historical context
reconciled exactly to 103 closes: 66 shorts at -US$19,431.77 and 37 longs at
+US$1,655.79. All are quarantined as pre-start; the forward sample is zero.
Two focused tests pass, including idempotency and last-good-state preservation.

```text
short_expression_shadow.py             c4b6c9c01a55eac005b697d1d39010a5852977b917e6dc492c46759f839a0c13
tests/test_short_expression_shadow.py  e695475b4ceacd1c749038af53da756ddb97a794ba11b7a7f5627c4d11ba632a
short_expression_shadow.json           35542dcc9192ea52fc662f2b98baeee937a221de1ff9dd76a06acb7ec4825425
```
