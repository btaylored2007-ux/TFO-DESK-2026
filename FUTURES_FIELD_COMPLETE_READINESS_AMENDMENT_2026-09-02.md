# Futures-panel readiness gate — field-complete defect amendment, 2026-09-02

PAPER RESEARCH INFRASTRUCTURE ONLY. No strategy rule, signal definition, engine,
position or capital allocation is changed.

## Defect

The existing 27-day and 36-day clocks counted any archived panel row, even if a
partial metric outage left one or more fields needed by the predeclared 4-hour
family null. That could declare readiness early and allow the surviving feature
set to be selected after seeing which collector stayed alive.

## Fixed measurement

The readiness clock now advances only on rows containing the exact archived
fields consumed by the original family: `oi`, `lsr`, `basis`, `cvd`, `buy_vol`,
`sell_vol`, `liq`, `aggr`, and `funding`. It still measures per-symbol contiguous
30-minute rows and uses the liquid-seven median; it never uses whole-table span.

The precommitted bars do not change: 27 field-complete contiguous days for the
single 4-hour time-series rerun and 36 field-complete days with at least 30-symbol
breadth for the later 12-hour cross-sectional rerun. Horizons at or below two
hours remain closed.

## Acceptance tests

- a row with any required field missing cannot advance the clock;
- 48 contiguous complete half-hour rows measure one day; and
- zero field-complete rows in a non-empty archive is reported as a partial-feed
  outage, not as readiness or a zero-result study.

