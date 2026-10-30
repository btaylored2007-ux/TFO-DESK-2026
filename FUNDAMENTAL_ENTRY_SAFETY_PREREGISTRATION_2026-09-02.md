# Fundamental entry-path safety alignment — preregistered 2026-09-02

PAPER ONLY. This document is written before the implementation change and before
any entry is attempted under it.

## Defect being corrected

`fundamental_open_new.py` currently checks capital, concentration, catalyst,
currency and price conversion, but it does not re-run the already-authoritative
`fundamental_liquidity.py` gate at the point of entry and it does not verify that
the listing is in a live `REGULAR` session. A public quote feed can therefore
fall back to `previousClose`, or carry an out-of-session/stub quote, while the
entry path records the result as if it were executable.

That behaviour conflicts with the standing rule that every new entry is tested
against the live liquidity gate and that an unmeasured spread must be remeasured
in session before sizing.

## Predeclared implementation

For NEW main-book entries only, before either a dry-run success or a ledger
write, the opener will:

1. fetch a fresh provider record for the exact execution symbol;
2. require `marketState == REGULAR`;
3. require a trustworthy quote currency consistent with the symbol route;
4. call `fundamental_liquidity.assess()` using the requested USD notional, side
   and live FX conversion;
5. refuse every reason returned by that gate;
6. additionally refuse an unmeasured spread, because the gate's own documented
   treatment says it must be remeasured before sizing; and
7. persist the measured liquidity, market state and volume-trend receipt with a
   successful PAPER entry.

Provider failure, empty data, a closed venue, an unmeasured spread, or any gate
failure all fail closed. No stored research measurement can substitute for this
entry-time check.

## What does not change

- No existing position is closed, resized or otherwise altered.
- No broker, credential or live order is introduced.
- No threshold in `fundamental_liquidity.py` changes.
- The 8% name cap, 20% sector cap, 20% non-USD currency-bloc cap, 30% short-gross
  cap, catalyst-date requirement, thesis rule, sizing, and exit rules do not
  change.
- This correction creates no claim of validated alpha. It prevents optimistic
  or stale execution from contaminating the forward PAPER record.

## Acceptance tests declared in advance

- a non-`REGULAR` venue is refused with no ledger or log mutation;
- a `REGULAR` venue that fails the authoritative liquidity gate is refused;
- a `REGULAR` venue with an unmeasured spread is refused even if the gate's
  research-stage waiver otherwise returns `ok`;
- a fully measured passing venue preserves the existing dry-run and entry
  behaviour and records the measurement receipt; and
- all pre-existing focused entry tests continue to pass.

## Implementation receipt — 2026-09-02

Implemented as declared. The fill price, FX conversion and liquidity gate now
come from the same in-session provider snapshot; `previousClose` is explicitly
ineligible. Sixteen focused entry tests pass, including the declared closed
venue, gate failure, unmeasured spread, previous-close and successful-receipt
cases.

```text
fundamental_open_new.py             8b728a18a38d07670abb81ecfe92ca95dc595a274b9e26cd8b6696541029f1e3
tests/test_fundamental_open_new.py  d041773a97fc645137ddce08870e8e9d1b611615e4c1f51cce9dc8331a948b56
```
