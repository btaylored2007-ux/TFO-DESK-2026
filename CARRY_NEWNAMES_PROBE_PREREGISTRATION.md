# Carry New-Names Probe — Pre-Registration ($10k paper)

**Registered:** 2026-08-10 · **PAPER ONLY** · **Benchmark:** 4%/yr cash
**Basis:** `reports/research_carry_deepening.md` — the ONE survivor of the
2026-08-10 eight-hunt sweep. Distinct mechanism: delta-neutral basis carry on
names the BTC/ETH executor does not hold. This is the highest-lever *and*
most-caveated finding, so it deploys as the smallest, hardest-capped vehicle.

## The rule (frozen — editing invalidates this registration)
- **Universe:** XMR and HYPE only (perps). XMR +12.1%/yr (t 5.7, both halves
  ~+12%), HYPE +8.6%/yr (t 5.6) — the only two names that cleared a Bonferroni
  screen; the broad set was proven to *hurt* (SUI/LINK/XRP/SOL/PEPE flip
  negative out-of-sample), so widening is barred.
- **Mechanism:** delta-neutral — long spot / short perp, same asset — harvest
  funding only. No directional exposure.
- **Entry:** enter a name when its annualized funding clears **+2%** AND it
  passes the liquidity screen below. **Exit** when funding < 0 (unchanged from
  the sealed carry desk — this preserves the ~8× cost margin).
- **HARD LIQUIDITY CAPS (the load-bearing safety, because the tail is
  unmeasured):** per-name position notional ≤ **0.5% of that name's trailing
  24h $volume** AND ≤ **$5,000** (half the book). Skip any name whose 24h
  volume is below floor or whose round-trip spread exceeds the desk ceiling.
  Illiquid marks are never counted as harvestable at size.
- **Sizing:** $10,000 paper total, split across the two names subject to the
  caps above; uninvested cash sits flat.
- **Kill:** freeze the probe at **−30% drawdown** (same as all sleeves).

## Honest caveat (recorded up front, not discovered later)
The average carry on these names is real and statistically strong, but the
**basis-cascade tail during a delta-neutral unwind is UNMEASURED** — thin books
can gap both legs against you faster than the hedge rebalances. The caps and the
−30% kill bound the damage; they do not eliminate the risk. This probe's JOB is
to measure that tail live, at $10k paper, so we learn it cheaply. Success = the
carry is capturable at capped size without a ruinous unwind; failure =
information at $10k, logged with receipts.

## Evidence rule
No verdict before **182 days**. Reviewed with the tournament (first board
2026-11-06). Ranked, like the rest, by return/maxDD — never raw return.

## Marking (dashboard honesty)
Funding accrual is real (paid/received on the position). Spot and perp marked at
live quotes. The probe surfaces as its own competitor; any illiquid mark is
flagged, never headlined — consistent with the liquid/illiquid split already on
the dashboards.

**Spec hash (sealed 2026-08-10):** 6196101b61833248
