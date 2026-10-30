# Value Book — Pre-registered Forward Test

**Registered:** 2026-08-06 · **Status:** LIVE (paper) · **Resume this — never restart it.**

## What this is

A second $50,000 paper book deploying the global value/quality screen as a live
forward test. Fundamental strategies CANNOT be backtested honestly on free data
(point-in-time fundamentals are paid; ranking today's ratios against past returns
is look-ahead bias). So the forward test IS the experiment: this registration
freezes the rules before the first result exists.

## The strategy (exact, frozen)

1. Universe: the ~134 global large-caps in `global_value_screen.py` (US, Europe,
   UK, Japan, Australia, Asia, EM — any market, no company out of reach).
2. Every **63 trading days** (quarterly), re-run the screen: composite =
   **50% value** (earnings/forward-earnings/book/FCF yields, EV/EBITDA) +
   **30% quality** (ROE, margins, low leverage) + **20% GARP** (revenue growth,
   low PEG). Percentile ranks, NaN-safe, ≥4 metrics required per name.
3. Hold the **top 10 by composite, equal weight, excluding value-trap flags**
   (cheap + revenue shrinking >5% + debt/equity >150).
4. Costs 10 bps per side on traded notional. Long only, no leverage. Start $50,000
   (USD paper; FX unmodelled and noted).

## What this claims — and does not

**Claims:** a disciplined tilt toward the published value/quality premia, applied
globally, with a trap filter — expected to behave like world equities with a value
lean. **Does NOT claim:** backtested edge (impossible here), or immunity from the
documented value droughts (the 2010s were brutal). Published post-2005 anomaly
decay (median 7 bps/mo, Chen & Welch) is the sober prior. This book exists to
generate the out-of-sample evidence, not to presume it.

## Benchmark, evaluation, kill criteria (frozen now)

- **Benchmark: VT** (Vanguard Total World) — the no-skill global alternative.
- Quarterly reports (first: 2026-11-06); **primary evaluation 2028-08-06**.
- **Kill/review triggers:** max drawdown > 40%; or trailing VT by >12%/yr at any
  quarterly report ≥1yr in; or any rule edit (edits invalidate the registration).

## Operations

`value_book.py` ticks inside the 2-hourly scheduled task; rebalances only when 63
trading days have elapsed (the screen re-fetches fundamentals then, ~5 min).
Ledger: `reports/value_book.json`, `reports/value_book_ledger.jsonl`, equity in
`reports/value_book_equity.csv`. PAPER ONLY — no broker, no credentials.

---

## Disclosure — stale-close refusal on fills (appended 2026-08-22T13:42Z, append-only; registered rules above unchanged)

**Basis:** funder standing directive 2026-08-22 — "all books need live prices, no
stale prices to trade against" (Non-negotiable #7). Audit: reports/STALE_PRICE_AUDIT.md §2.4.

**Defect:** `fetch_closes` served `raw.ffill().iloc[-1]` over a 10-day window, so
a halted/suspended/feed-dead name could silently price a rebalance fill at a close
up to ~9 days old — fabricated liquidity at a stale price.

**Fix (2026-08-22, value_book.py `fetch_closes` / `price_book`):** the EXISTING
refusal machinery is extended — a name whose latest own bar is older than
2 trading days (MAX_FILL_AGE_TDAYS, Mon–Fri UTC count) is added to `refused` with
a stated reason and printed loudly, exactly like the FX refusals. Consequences
flow through machinery that already existed: a refused candidate is excluded from
the rebalance; a refused HELD name blocks the whole rebalance loudly
(held-name-unpriceable rule). Selection, top-10, equal weight, the 63-trading-day
cadence, costs, benchmark and kill rules are byte-preserved. Weekend/holiday
closes for a market that is simply closed remain valid (≤ 2 trading days old by
construction).

### Disclosure — cash restatement, 2026-08-25 (append-only, funder-directed)

The 2026-08-06 rebalance sized every position as `$5,000 / LOCAL price`, so
foreign-quoted names were drastically under-deployed: 005930.KS received **$3.61**
of its intended $5,000 (0.0217 shares, because 230,500 *won* was treated as
230,500 dollars) and BARC.L received **$68.18** (pence read as dollars). Actual USD
deployed was **$40,904.06**, not $50,000. The **$9,095.94** difference was neither
invested nor held as cash — an accounting phantom that made the tournament score
this book **≈ −18%** against capital it never put to work.

**Correction:** cash restored to `50,000 − 40,904.06 deployed − 50.00 costs =
$9,045.94`. **No holding was created, closed or resized; no history was
retro-edited.** The registered rule (top-10 global value/quality, equal weight,
quarterly, $50k, benchmark VT) is UNCHANGED — this restores the ledger to the
$50,000 the book was actually funded with.

The sizing defect is already fixed in `value_book.py` (it now sizes on
USD-translated prices via `fx.py`). The mis-weighted legacy holdings persist until
the **registered quarterly rebalance (~Nov 2026)**, which is the first time this
book will execute its equal-weight rule as written. Until then its weights are NOT
equal and its return is not a clean test of the registered strategy — stated here
so the first review reads it correctly. Deployed-USD uses `fx_asof` rates rather
than 2026-08-06 entry rates; FX drift since entry is disclosed as ±$150 on ~$5.9k
of foreign exposure.

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
