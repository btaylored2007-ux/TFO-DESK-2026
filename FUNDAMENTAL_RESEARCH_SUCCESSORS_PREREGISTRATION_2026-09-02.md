# Fundamental research successors — registered 2026-09-02

PAPER RESEARCH ONLY. This registration creates no order, position, allocation or
change to either frozen fundamental book. It is sealed before any forward
observation from the new adapters or filing-diff engine is interpreted.

## Purpose

The generic ROE/margin method is invalid in sectors where reported profit is a
fair-value movement, regulatory accounting series, reserve release, concession
asset accretion or cycle-peak commodity price. The desk also lacks a canonical
point-in-time filing-diff queue outside the United States daily numerical funnel.

## Six separately identified adapter families

The following adapter IDs are separate research families and separate entries in
the house multiple-testing ledger if they are ever tested for alpha:

1. `insurer_v1`: P/TBV history plus underwriting ex prior-year reserve
   development and catastrophe normalisation.
2. `bank_v1`: P/TCE plus through-cycle credit costs and a local sovereign hurdle.
3. `investment_entity_v1`: cash distributions, disposal realisations,
   look-through debt and independently tested NAV; IFRS FVTPL profit/ROE is
   forbidden as a primary signal.
4. `regulated_utility_v1`: regulatory earnings/cash realisation and tax/inflation
   normalised allowed returns; raw cross-border headline returns and IFRIC 12
   contract-asset revenue are forbidden as primary signals.
5. `holdco_dualclass_v1`: taxed SOTP, exact economic entitlement, control rights
   and own-history discount.
6. `resources_v1`: reserve life, realised price, hedge book and AISC/C1 under a
   mandatory mid-cycle price and cash-flow stress; spot-only cash flow is
   forbidden.

Every observation requires primary-source dates, a future sourced catalyst that
has been walked forward and remains alive, an explicit per-share direction, and
own-history plus current-regime percentiles where the sector is cycle-sensitive.
`RESEARCH_READY` means only that specialist work is sufficiently evidenced. It
is never an undervaluation verdict or an entry ticket.

## Filing-diff successor

`fundamental_filing_diff.py` may surface material changes in point-in-time SEC
facts and exact normalised event states. It filters every fact by `filed` date,
refuses look-ahead, and labels duration periods of incompatible length
`NOT_COMPARABLE`. A diff is a research lead only. Numeric direction remains
unclassified until the relevant sector adapter and primary disclosure are read.

The normalized non-US schema may be populated only from an issuer, exchange or
regulator primary source with a public/filed date. A secondary story cannot enter
the point-in-time record as a primary event.

## Forward evidence bar

- Minimum 182 elapsed days and 100 fully researched forward observations per
  adapter family before any profitability conclusion.
- Search count includes every tried adapter/name/rule combination.
- Any capital proposal must independently pass the complete
  `quant_validation.py` harness, search-deflated Sharpe, purged/embargoed CV,
  effective-N, cost, liquidity, decay and execution-delay tests.
- A survivor may receive only one separately hash-sealed US$10,000 PAPER probe,
  with the firm's 182-day and -30% drawdown rules.
- A killed or unavailable catalyst, missing primary source, short regime window,
  invalid sector accounting series or incompatible period comparison is
  `INSUFFICIENT_EVIDENCE`, never a neutral/pass value.

## Operational controls, not alpha hypotheses

- `fundamental_source_budget.py` separates event-discovery, catalyst-verification
  and adjudication search quotas from direct primary-source retrieval. Exhaustion
  records `UNRESEARCHED`, never `nothing_found`.
- `fundamental_session_queue.py` holds a completed candidate until the exact
  selected line can be re-measured in a regular session. It can advance only to
  `READY_FOR_DRY_RUN`; the existing dry-run and portfolio gate remain mandatory.

These two controls do not create search trials because they make no alpha claim.

## Acceptance tests

- Later restatements cannot enter an earlier point-in-time snapshot.
- Incompatible duration facts cannot create a directional diff.
- FVTPL ROE cannot drive an investment-entity research record.
- A killed or stale catalyst fails the adapter.
- Search exhaustion creates a named unresearched queue.
- A closed venue remains pending and a fresh gate pass advances only to dry-run.
- Repeated adapter observations are idempotent by content hash.

