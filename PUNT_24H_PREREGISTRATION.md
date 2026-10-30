# THE 24-HOUR PUNT — pre-registration ($10,000 paper)

**Declared 2026-08-26, BEFORE any position is opened. PAPER ONLY.**
Funder-directed: *"lets play with $10k for next 24hrs and see how much money we
can turn that into, free reign, no holds barred, you can risk it all."*

## WHAT THIS IS — read this before you read the result

**This is a punt, not a strategy.** It is one draw from a distribution. The
interpretation is fixed HERE, in advance, so that neither outcome can be
misread afterwards:

- **If it wins, it proves nothing.** A single 24-hour outcome on an 8%-daily-vol
  asset is a coin flip with a wide face. A green number will NOT be evidence
  that concentration, aggression, or stock-picking works.
- **If it loses, it proves nothing either.** Same reason.
- **No conclusion, rule, sleeve or allocation may ever cite this result as
  evidence.** It is quarantined from the tournament board and excluded from the
  firm's hypothesis count, because it tests no hypothesis.

The desk's standing evidence is unchanged by it: ~2,500 hypotheses tested, ONE
mechanism validated (delta-neutral basis carry), and rally-riding measured
losing to buy-and-hold on this desk's own live tape six days ago.

## The rule (frozen)

- **Capital:** $10,000 paper. **Duration:** 24 hours from entry, hard close.
- **Position:** 100% of the book in **PUMP/USD** — selected for ONE declared
  reason: it is the **highest-variance name on the liquid roster** (8.10%
  daily vol, ~2x the next). It is NOT a forecast. I have no view on direction.
- **Liquidity check (done before entry, 2026-08-26):** $5.55M/24h volume,
  4.3 bps spread; a $10k clip is 0.18% of daily volume — a real fill, not a
  thin-air mark.
- **NO stop loss.** Deliberate: a stop would truncate variance, which is the
  entire point of the exercise as directed ("risk it all").
- **Costs:** 30 bps in, 30 bps out — charged, as on every other book.
- **Marking:** live Kraken price, honest, same rules as the rest of the desk.

## What is NOT compromised

Risk is unconstrained by instruction. **Measurement is not.** Fills are real,
costs are charged, marks are live, and the result will be reported exactly as it
lands — win or lose, with no story attached.

**Expected distribution, stated up front:** at 8.1% daily vol, ~68% of outcomes
land between **$9,190 and $10,810**; ~95% between **$8,380 and $11,620**. Turning
$10k into materially more than ~$12k inside 24 hours would require leverage this
desk does not have. Anyone expecting a moonshot should read that range twice.

**Spec hash (sealed 2026-08-26):** 7cdee16eebb663aa

---

## AMENDMENT — 20:1 LEVERAGE (funder-directed 2026-08-26, append-only)

Funder: *"LEVER IT"*. The unlevered punt (opened 05:06Z, PUMP/USD @ 0.004699)
is **levered up in place to 20:1** rather than closed and reopened — that avoids
a pointless round-trip cost. Same asset, same deadline, same quarantine.

**The interpretation declared above still stands and now matters MORE:** this
result proves nothing either way and may never be cited as evidence.

### What leverage actually changes (computed before the amendment, not after)
- Notional goes to **~20x equity**; position volatility becomes **~162%/day**.
- **Liquidation is modelled at Kraken's margin-level rule (40% of used margin),
  which triggers EARLIER than the naive 1/20 = −5%**: liquidation lands near a
  **−3% adverse move**, not −5%.
- **P(touching the liquidation barrier inside 24h): >50%.** The dominant single
  outcome of this amendment is a **total loss before the deadline**.
- The barrier is **absorbing**: a wipeout at hour 3 cannot recover if the asset
  rallies afterwards. That makes it strictly worse than a fair coin flip.

### Honesty requirements this build MUST meet (non-negotiable, funder-agreed)
1. **Real liquidation.** A levered book may never display a loss larger than the
   equity — that is fiction. On breach, equity goes to $0 and stays there.
2. **Real borrow cost** on the borrowed notional, accrued per elapsed time
   (modelled at Kraken's published margin rollover order of 0.02%/4h — the one
   assumption here not independently verified; it works AGAINST the position).
3. **Intraday liquidation checking.** The desk heartbeat is hourly; a wipeout at
   minute 20 must not be missed. Every tick checks the **intraday LOW** of each
   bar since entry, not just the close — reporting a survival that never
   happened would be the worst defect this desk could produce.

Risk is unconstrained by instruction. Measurement is not.
**Amendment spec hash:** f0a76749ea8aac08
