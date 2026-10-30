# Growth Book — Pre-registered Forward Test

**Registered:** 2026-08-06 · **Status:** LIVE (paper) · **Resume this — never restart it.**

## What this is

The deployment of everything this desk's research established, as one running paper
book targeting the **largest return the evidence will defend**, compounding without a
daily target. Registered before the first tick so results cannot be curated after
the fact.

## The strategy (exact, frozen)

**Dual momentum, top-2, monthly** over 15 asset-class ETF proxies
(SPY QQQ IWM EFA EEM TLT IEF LQD HYG GLD SLV DBC USO IYR AGG):

1. Every 21 trading days, rank all assets by trailing 252-day total return.
2. Hold the **top 2, equal weight** — but only those with trailing return **> 0**.
3. If fewer than 2 qualify, the remainder goes to **IEF** (intermediate treasuries).
4. Costs: 10 bps per side on traded notional. No leverage. Long only.
5. Start: **$50,000** (USD-denominated paper; FX unmodelled and noted).

**Plus the carry overlay** (separate book, already armed): if delta-neutral basis
carry ≥ 4%/yr, `carry_paper_desk.py` opens its position independently. The two books
are additive and independently accounted.

## Why this and not something bigger

- 36-year test (3 crashes): dual momentum matched buy-and-hold's return within
  0.15%/yr at **60% of the drawdown**; +30.2% through dot-com, +14.5% through COVID.
- 2010-26 test: it **trails** buy-and-hold by ~4%/yr in an uninterrupted bull. That
  is the known price of the crash protection. Registered here so underperformance
  in a bull is expected, not a surprise.
- Kelly logic: with no validated alpha (this desk's central finding), the
  growth-optimal portfolio is risk-managed beta at 1x — not leverage, not
  concentration. Leverage without edge lowers the geometric growth rate.

## What this claims — and does not

**Claims:** over a multi-year horizon, equity-like returns with materially smaller
maximum drawdown than buy-and-hold; survivable compounding.
**Does NOT claim:** alpha, edge, outperformance of the index in a bull market, or
any daily/monthly income. Expected long-run range **7–12%/yr**; on $50k that is
~$10–16/day *on average, arriving in lumps, with losing years*.

## Evaluation & kill criteria (frozen now)

- **Quarterly reports** (first: 2026-11-06); **primary evaluation 2028-08-06** (2yr) —
  allocation strategies cannot be judged in months; noise dominates below ~1yr.
- **Kill/review triggers** (any → flag REVIEW at top of scheduled report):
  - Max drawdown exceeds **35%** (worse than the mechanism's historical behaviour)
  - Trails a 60/40 benchmark by >10%/yr annualized at any quarterly report ≥ 1yr in
  - Any rule above gets edited (edits invalidate the registration — log and restart
    the clock instead)

## Operations

`growth_book.py` ticks inside the 2-hourly scheduled task; it only **rebalances**
when 21 trading days have elapsed. Ledger: `reports/growth_book.json` +
`reports/growth_book_ledger.jsonl` + equity curve `reports/growth_book_equity.csv`.
PAPER ONLY — no broker, no credentials, no real orders.

---

## Amendment — uniform cash yield on idle capital (2026-08-27)

**Full text: `CASH_YIELD_AMENDMENT.md`.** Funder-directed 2026-08-27; that
document, not this one, is the governing record.

From each book's stamped `cash_yield_since` instant, **idle cash earns the live
13-week US T-bill rate (`^IRX`, quoted in percent)** rather than 0%. Interest
accrues as `cash * (rate/100) * (elapsed_seconds / 365d)`, prorated by actual
elapsed time. Short-term **bills only — not bonds**: a bond yield would bolt
unintended duration risk onto every book. If the rate cannot be fetched, is
non-positive, is absurd (>25), or is more than 24h stale, the accrual is **zero**
and an error marker is written; the rate is never guessed or carried forward.

**It is forward-only.** No interest is credited for any period before that
instant, and nothing published above is retro-edited or restated.

**It is separable.** Each book carries its own cumulative
**`cash_interest_accrued`** field (plus `cash_yield_since` and
`last_rate_used`), so at every review **strategy return can be split from
interest return**: `strategy return = total return − cash_interest_accrued`.

It applies **uniformly** to every book holding cash, so no strategy is
advantaged. It changes **no** entry, exit, sizing, cost or kill rule; **no
holding, unit count or entry price was touched**. Its purpose is to stop
modelling cash at 0%, which structurally penalised books that correctly hold
cash against books that are always deployed.
