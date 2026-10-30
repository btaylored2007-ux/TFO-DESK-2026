# Strategy Tournament — Pre-registered Live Competition

**Registered:** 2026-08-06 · **Status:** LIVE (paper) · **Resume — never restart.**

## Philosophy

Paper capital is an experiment budget: money we can afford to lose *finding the
strategy*. Backtests on this desk have manufactured four beautiful fakes; live
forward performance is the one validation channel that cannot be curve-fit. So
every strategy the firm runs competes on ONE leaderboard under identical
accounting, and standing is earned only by live evidence.

## Competitors

**Existing books** (their own registrations govern them): Growth Book ($50k),
Value Book ($50k), the three Kraken Floor desks ($40k each), Venue Replica desks,
the Kraken Engine (A$40k, shown separately), the carry executor.

**New experimental sleeves — $10,000 paper each, rules frozen here:**

| Sleeve | Rule (exact) | Why it's in |
|---|---|---|
| `lowvol_crypto` | Weekly: hold the 5 lowest-30d-vol of the ~20 most liquid Kraken USD pairs, equal weight, long only. 30 bps/side. | **Rejected in backtest** (looked like short-beta in a bear). Forward test is its honest day in court. |
| `fng_momentum` | Long BTC while Fear&Greed ≥ 55, flat below. Daily check. 30 bps/side. | The *inverted* folk signal — best-sampled cell of the sentiment lab (raw t=2.6, died under deflation). Live control on the deflation itself. |
| `equal_weight_all` | 15 asset-class ETFs equal weight, quarterly. 10 bps/side. | Pure-diversification control. If clever loses to this, clever is fired. |
| `spy_trend_cash` | SPY when above its 200-dma, cash below. Monthly. 10 bps/side. | The right-expression defense (shorting the same signal lost 15pp/yr). |

## Evidence rules (frozen)

- **No verdict before 182 days.** Early leaderboards are noise and are labelled so.
- Ranking metric at evaluation: return / max-drawdown since inception, ties to
  lower turnover. Raw return alone never ranks.
- **Sleeve kill:** −30% drawdown freezes a sleeve (state preserved, reported).
- **Promotion means nothing but attention.** Paper stays paper. A sleeve that
  leads for 2+ quarters earns a deeper study, not money.
- Rule edits invalidate a sleeve's entry; log a new sleeve instead.
- First leaderboard review: **2026-11-06**, then quarterly with the books.

## Operations

`tournament.py` ticks inside the 2-hourly schedule (each sleeve rebalances on its
own cadence); `tournament.py --board` writes `reports/tournament.json` for the
war room. PAPER ONLY — no broker, no credentials, no real orders.

---

## Addendum — 2026-08-07: the volatility sleeves (funder increased pool +$50,000)

| Sleeve | Capital | Rule (exact, frozen) | Why it's in |
|---|---|---|---|
| `covered_call_etf` | $25,000 | Deterministic underlying: the most expensive ETF from the frozen shortlist [DIA, IWM, GLD, EFA, TLT, XLF, XLE, EEM] whose 100-share round lot fits the sleeve's cash; hold up to 2 round lots; sell 1 call per lot ~3% OTM at the nearest expiry 25–40 days out (premium credited at BID); buy back at ASK within 2 days of expiry, re-sell next month. If the chain has no bid, fall through the shortlist. 10 bps/side on shares, $1/contract. Marked at live chain mids. *(Amended 2026-08-07 PRE-FIRST-TRADE and logged: the original IWM round lot ($30,083) exceeded sleeve capital — the sleeve correctly refused to trade rather than fudge. Underlying selection made deterministic; no discretion.)* | The first LIVE options strategy on the board. Cboe's 30-year BuyWrite record (vol_lab) shows income + smaller drawdowns at a return cost; live chains are the only honest way to test it forward. |
| `vix_regime_switch` | $25,000 | Hold SPY while VIX < VIX3M (contango/calm); switch to IEF when inverted. Daily check, trades on flips only. 10 bps/side. | 20-year backtest: Sharpe 0.69 vs SPY 0.66 with maxDD −35% vs −55% — crash protection at a return cost, now sent to live court. |

Same evidence rules, same −30% freeze, same first review 2026-11-06. **Short-vol
ETPs (SVXY-style) are PERMANENTLY BARRED from this desk**: 36.7%/yr with a −95%
drawdown and −83% in one session (2018-02-06) is ruin dressed as income.

---

## Addendum — 2026-08-07: the GLOBAL PROBES (funder-directed worldwide expansion; head-of-desk selection)

The funder granted open authority to hunt any market. The risk engine shows the
firm's true concentration is US equity beta (+0.30) — so these four probes were
chosen for DISTINCT MECHANISMS, not more exposure to the same bet. Each is a
$10,000 buy-and-hold sleeve of one liquid ETF: the cleanest possible measurement
of whether that market's premium is worth owning (stock-picking and timing
inside markets were already tested and failed — the probe question is the
MARKET itself). 10 bps entry, no leverage, kill at −30% DD, no verdict <182d.

| Sleeve | ETF | Distinct mechanism being probed |
|---|---|---|
| `probe_japan` | DXJ | Japanese equities with the yen hedged out — the one major market in a different monetary regime; hedging removes the FX offset that usually masks it |
| `probe_india` | INDA | Structural domestic-growth premium; lowest US-cycle linkage of the majors |
| `probe_agri` | DBA | Agriculture — the commodity family the firm doesn't hold, weather/food cycle, historically the lowest equity beta on the board |
| `probe_em_debt` | EMLC | EM local-currency government debt — FX carry in bond form; kin to the firm's one validated mechanism (carry), in a wholly different wrapper |

Expectations, pre-registered: each probe earns roughly its market's beta —
these are premium measurements, NOT alpha claims. Success = a probe whose
return/maxDD earns a deeper look at review; failure = information at $10k/lesson.

---

## Addendum — 2026-08-20: `rally_rider` (funder-directed live counter-test: rally-riding WITH scale-out profit-taking)

**Deployed on funder instruction of 2026-08-20 — an explicit exception to the
survivors-only deployment rule in the operating skill.** Implemented exactly as
directed, then FROZEN. Judged only by its 182-day live record.

| Item | Frozen rule |
|---|---|
| Capital | $10,000 paper (tournament START) |
| Universe | the ETF15 list in tournament.py plus XBT/USD and ETH/USD (Kraken daily bars) |
| Entry | Daily, SETTLED closes only (in-progress bars dropped — yfinance rows dated the current UTC day, and Kraken's always-in-progress last 1440-min bar: the exit_axis settled-bar discipline). Enter when the settled close equals the 20-trading-day high of SETTLED CLOSES **and** that bar's volume >= 1.5x the 20-day median volume; both windows use bars strictly up to and including the deciding settled close. Enter at that settled close, 25% of sleeve equity per position. Max 4 concurrent positions; signals are skipped when full; an open name is never pyramided. Signals are processed in fixed universe order (ETF15 order, then XBT/USD, ETH/USD). Exits book before entries on the same settled bar. |
| Volume | yfinance daily Volume for ETFs; Kraken 1440-min bar volume for crypto. A name whose volume is missing anywhere in its 20-bar window is INELIGIBLE that bar — recorded in sleeve state (`rr.ineligible`), never guessed. |
| Profit-taking | sigma FROZEN at entry from the same settled daily series via `exit_axis.compute_entry_sigma` (61 closes -> 60 daily simple returns, min 25; not reimplemented). Scale out 1/3 of the initial units when close >= entry\*(1 + 2\*sigma\*sqrt(bars_held)); another 1/3 when close >= entry\*(1 + 4\*sigma\*sqrt(bars_held)); the remainder exits on trailing stop close <= peak_close\*(1 − 2.5\*sigma) — peak of settled CLOSES since entry, never intraday highs — or a 40-trading-day time stop. Take-profit legs are checked before the stop on the same close (exit_axis ordering); no exit on the entry bar (bars_held >= 1). ALL fills book at the TRIGGERING settled close — never the threshold price, never the peak. If sigma is unavailable at entry (<25 settled returns) the position is loudly exit-exempt from the sigma legs and only the 40-bar time stop governs (recorded in state). |
| Costs | 10 bps/side ETFs, 30 bps/side crypto, charged on EVERY entry and EVERY partial exit through the sleeve cost accounting (identical arithmetic to `set_holdings`: cost on the notional traded). |
| Kill | freeze at −30% drawdown (standard tournament kill via `mark()`). |
| Evidence | no verdict before 182 days; first review 2026-11-06 with the tournament; ranked by return/maxDD. |

**Pre-registered EXPECTATION, stated plainly: the desk prior is NEGATIVE.**
582 momentum/trend trials on this desk produced zero after-cost survivors, and
the exit-axis backtest found profit-taking ties the null at best
(reports/research_exit_axis.md). This sleeve is the funder-directed LIVE
counter-test of rally-riding WITH scale-out profit-taking, deployed on funder
instruction of 2026-08-20. If it clears the controls after costs over 182+
days it earns a deeper study; if it loses, that is the prior confirmed at
$10k/lesson. Either way the verdict comes from the live record, not opinion.
Rule edits invalidate the entry — log a new sleeve instead.

### rally_rider — verification disclosures (appended 2026-08-20, post-hostile-review; append-only, no prior bytes changed)

Both hostile verifications PASSED (no-lookahead/honest-fills: 41/41 scratch
assertions + live replay of all four day-one entries at 0 relative error;
costs/isolation: every fill hand-reproduced to the penny, 0 deletions to any
existing code, prior file bytes verified as an exact prefix). Disclosures:

1. **Sourcing of "582 momentum/trend trials"**: that figure comes from the
   funder's 2026-08-20 brief citing sister-desk measurements; THIS desk's own
   receipts document 360 equity value/momentum hypotheses, 1,124 trials at the
   trend kill, and 1,345 cumulative (HUNT_BACKLOG, 2026-08-17). The negative
   prior stands on the desk-documented numbers regardless.
2. **Outage catch-up semantics**: if ticks miss a day, only the latest settled
   close is evaluated — a stop breached on an intermediate settled close that
   recovers by the latest close will not exit, and a still-breached stop fills
   at the latest close. This matches the exit-axis convention.
3. **Cash convention**: entries size 25% of equity with costs charged after,
   so cash can go negative by exactly the costs paid (day one: -$14.99 on
   $19.99 of entry costs). This is the same arithmetic the house set_holdings
   uses for every existing sleeve (weights summing to 1.0 leave cash = -cost);
   equity is exact. Left as-is because amending the frozen rule would reset
   this registration for zero economic effect (~0.15% of the book).
4. Cosmetic, house-wide notes: last_rebalance stamps host-local time (display
   only; the daily decision gate is UTC); distributing-ETF adjusted-price drift
   shifts stops ~0.2-0.5% vs a ~4% stop width (current holdings unaffected).

---

## Addendum — 2026-08-21: `rally_rider_x` (funder-directed BREADTH counter-test; "seal it and launch it")

**Deployed on funder instruction of 2026-08-21 — an explicit exception to the
survivors-only deployment rule, exactly like its sibling `rally_rider`.**
Implemented exactly as directed, then FROZEN. Judged only by its 182-day live
record, reviewed alongside `rally_rider` with the tournament.

| Item | Frozen rule |
|---|---|
| Capital | $100,000 paper (SLEEVE_START entry). Sleeve name: `rally_rider_x`. |
| Universe (fixed, verbatim — 34 names) | ETF15 (already in tournament.py): SPY, QQQ, IWM, EFA, EEM, TLT, IEF, LQD, HYG, GLD, SLV, DBC, USO, IYR, AGG. Deep-liquid additions: XLK, XLF, XLE, XLV, XLI, XLY, XLP, XLU, XLB, DIA, MDY, VNQ, EWJ, FXI, INDA. Crypto majors: XBT/USD, ETH/USD, SOL/USD, XRP/USD (all clear the >=$5M/24h liquidity screen on the engine universe). |
| Entry | Daily, SETTLED closes only, identical settled-bar discipline to rally_rider (yfinance rows dated the current UTC day dropped; Kraken's always-in-progress last 1440-min bar always dropped). Enter when the settled close equals the 20-trading-day high of SETTLED CLOSES **and** that bar's volume >= 1.5x the 20-day median volume; both windows use bars strictly up to and including the deciding settled close. Enter at that settled close. Max 10 concurrent positions; an open name is never pyramided. When qualifying signals exceed free slots, priority = highest volume-ratio (deciding-bar volume / 20-day median volume — declared here, FROZEN); ties break alphabetically. Exits book before entries on the same settled bar. |
| Sizing — NET OF COSTS | Slice = 10% of sleeve equity at entry, sized NET OF COSTS: the notional is chosen so that notional + cost equals the 10% allocation exactly (notional = alloc / (1 + fee)), and the allocation is additionally capped at free cash, so **cash can never go negative**. This is the v2 accounting improvement over the convention disclosed in the rally_rider addendum (item 3 there: rally_rider's cash may go negative by exactly the costs paid). |
| Volume | yfinance daily Volume for ETFs; Kraken 1440-min bar volume for crypto. A name whose volume is missing anywhere in its 20-bar window is INELIGIBLE that bar — recorded in sleeve state (`rr_x.ineligible`), never guessed. |
| Profit-taking | Identical sealed ladder to rally_rider; sigma FROZEN at entry from the same settled daily series via `exit_axis.compute_entry_sigma` (imported, never copied; sigma*sqrt(bars_held) convention from exit_axis). Scale out 1/3 of the initial units when close >= entry\*(1 + 2\*sigma\*sqrt(bars_held)); another 1/3 when close >= entry\*(1 + 4\*sigma\*sqrt(bars_held)); the remainder exits on trailing stop close <= peak_close\*(1 − 2.5\*sigma) — peak of settled CLOSES since entry, never intraday highs — or a 40-trading-day time stop. Take-profit legs are checked before the stop on the same close (exit_axis ordering); no exit on the entry bar (bars_held >= 1). ALL fills book at the TRIGGERING settled close — never the threshold price, never the peak. If sigma is unavailable at entry (<25 settled returns) the position is loudly exit-exempt from the sigma legs and only the 40-bar time stop governs (recorded in state). |
| Costs | 10 bps/side ETFs, 30 bps/side crypto, charged on EVERY entry and EVERY partial exit through the sleeve cost accounting. |
| Kill | freeze at −30% drawdown (standard tournament kill via `mark()`). |
| Evidence | no verdict before 182 days; reviewed with the tournament; ranked by return/maxDD. |

**Pre-registered EXPECTATION, stated plainly: the momentum-family prior is
NEGATIVE.** The house receipts document zero after-cost survivors across the
momentum/trend programme, and the spec_rally lab found that even liquid
rally-riding does not beat simply holding its own names. This sleeve tests ONE
new axis its $10k sibling cannot: BREADTH — 10 concurrent rallies vs 4, a wide
~34-name universe vs a narrow 17. Deployed as a funder-directed exception to
survivors-only deployment ("seal it and launch it", 2026-08-21); judged only by
its 182-day live record alongside rally_rider. Rule edits invalidate the entry —
log a new sleeve instead.

### rally_rider_x — post-hoc hostile verification (appended 2026-08-22; append-only)

All four checks PASSED (fills replayed at zero error from fresh public data
across the full 34-name universe — exactly the 6 recorded names fired, XLE/DBC
correctly volume-gated out; no-lookahead confirmed line-by-line with the
once-per-UTC-day gate proven on a live tick; the entry cash cascade reproduced
BIT-FOR-BIT to 40042.85787160982; registration append-only and zero deleted
lines vs the 2026-08-20 backup, sibling sleeve byte-identical). One disclosed
data-source property (not a defect): Yahoo revises consolidated ETF volume
after the fact (~0.3% observed), so ETF entries near the 1.5x volume gate
(GLD entered at ~1.52x) sit within revision noise by construction; crypto
volume ratios replay exactly. The gate itself is unchanged and frozen.

---

## Addendum — 2026-08-22: `rally_rider_hold` + `dip_rider` (funder-directed "do it" — completing the 2x2 rally experiment)

**Deployed on funder instruction of 2026-08-22 — explicit exceptions to the
survivors-only deployment rule, exactly like their siblings.** Implemented
exactly as directed, then FROZEN. Judged only by their 182-day live records,
reviewed with the tournament.

### The 2x2 experimental design (stated at registration)

|  | Exit: BANK thirds (rally_rider ladder) | Exit: LET IT RIDE (trail/time only) |
|---|---|---|
| **Entry: 20d-high STRENGTH + 1.5x volume** | `rally_rider` (live 2026-08-20) | `rally_rider_hold` (this addendum) |
| **Entry: 20d-low WEAKNESS + 1.5x volume** | `dip_rider` (this addendum) | — (null cell) |

The null cell and the overall bar are the buy-and-hold CONTROLS at review.
`rally_rider_hold` isolates the **EXIT AXIS** against `rally_rider`: same
universe, same entries — every performance difference between the pair is
attributable to the exit rule alone. `dip_rider` isolates the **ENTRY AXIS**:
buying 20d strength vs buying 20d weakness under identical management.

### Sleeve A — `rally_rider_hold` ($10,000 paper)

| Item | Frozen rule |
|---|---|
| Capital | $10,000 paper (explicit SLEEVE_START; the module default START is also 10000.0). |
| Universe | Identical to rally_rider: ETF15 (SPY, QQQ, IWM, EFA, EEM, TLT, IEF, LQD, HYG, GLD, SLV, DBC, USO, IYR, AGG) + XBT/USD + ETH/USD. |
| Entry | Identical trigger to rally_rider, identical settled-bar discipline (yfinance rows dated the current UTC day dropped; Kraken's always-in-progress last 1440-min bar always dropped). Enter when the settled close equals the 20-trading-day high of SETTLED CLOSES **and** that bar's volume >= 1.5x the 20-day median volume; both windows end at the deciding bar. Enter at that settled close. Max 4 concurrent; an open name is never pyramided; when qualifying signals exceed free slots, priority = highest volume-ratio (deciding-bar volume / 20-day median; ties break alphabetically). Exits book before entries on the same settled bar. |
| Sizing — NET OF COSTS | Slice = 25% of sleeve equity at entry, sized NET OF COSTS (v2 accounting: notional = alloc / (1 + fee); allocation capped at free cash; **cash can never go negative**). DISCLOSED at registration: live rally_rider sizes gross with cost charged after (its addendum, item 3) and fills in fixed universe order rather than volume-ratio priority; the funder-directed rule for this twin is the v2 convention. Economic effect is bounded by the costs themselves (~0.15% of book at entry) and slot-priority differences only bind when >4 signals fire on one bar; both differences are disclosed here so the exit-axis attribution is read with them in mind. |
| Volume | yfinance daily Volume for ETFs; Kraken 1440-min bar volume for crypto. Missing volume anywhere in the 20-bar window makes the name INELIGIBLE that bar — recorded in sleeve state (`rr_hold.ineligible`), never guessed. |
| Exits — LET IT RIDE | **NO profit-taking whatsoever.** ONLY: trailing stop close <= peak_close*(1 − 2.5\*sigma) — peak of SETTLED closes since entry, never intraday — or the 40-trading-day time stop. Sigma FROZEN at entry via `exit_axis.compute_entry_sigma` (imported, never copied). No exit on the entry bar (bars_held >= 1). ALL fills book at the TRIGGERING settled close. If sigma is unavailable at entry (<25 settled returns) the position is loudly exit-exempt from the sigma trail and only the time stop governs (recorded in state). |
| Costs | 10 bps/side ETFs, 30 bps/side crypto, on EVERY fill. |
| Kill | freeze at −30% drawdown (standard tournament kill via `mark()`). |
| Evidence | no verdict before 182 days; reviewed with the tournament; ranked by return/maxDD. |

**PURPOSE + PRIOR, stated plainly:** this sleeve isolates the EXIT AXIS
against `rally_rider` — same entries, bank-thirds vs let-ride — so every
performance difference between the pair is attributable to the exit rule
alone. Prior: the spec-rally and exit-axis labs found letting winners run >=
scale-outs net of costs; this is the LIVE test of that finding.

### Sleeve B — `dip_rider` ($10,000 paper)

| Item | Frozen rule |
|---|---|
| Capital | $10,000 paper (explicit SLEEVE_START; the module default START is also 10000.0). |
| Universe | Identical to rally_rider: ETF15 + XBT/USD + ETH/USD. |
| Entry — INVERTED | Same settled-bar discipline. Enter when the settled close equals the **LOWEST** settled close of the trailing 20 trading days **and** that bar's volume >= 1.5x the 20-day median volume — a volume flush on the dip; both windows end at the deciding bar. Enter at that settled close. Max 4 concurrent; an open name is never pyramided; when qualifying signals exceed free slots, priority = highest volume-ratio (ties break alphabetically). Exits book before entries on the same settled bar. |
| Sizing — NET OF COSTS | Slice = 25% of sleeve equity at entry, sized NET OF COSTS (v2 accounting: notional = alloc / (1 + fee); allocation capped at free cash; **cash can never go negative**). |
| Volume | yfinance daily Volume for ETFs; Kraken 1440-min bar volume for crypto. Missing volume anywhere in the 20-bar window makes the name INELIGIBLE that bar — recorded in sleeve state (`dip.ineligible`), never guessed. |
| Exits — FULL rally_rider ladder | Sigma FROZEN at entry via `exit_axis.compute_entry_sigma` (imported, never copied; sigma\*sqrt(bars_held) convention from exit_axis). Scale out 1/3 of the initial units when close >= entry\*(1 + 2\*sigma\*sqrt(bars_held)); another 1/3 when close >= entry\*(1 + 4\*sigma\*sqrt(bars_held)); the remainder exits on trailing stop close <= peak_close\*(1 − 2.5\*sigma) — peak of SETTLED closes — or the 40-trading-day time stop. Take-profit legs checked before the stop on the same close (exit_axis ordering); no exit on the entry bar. ALL fills book at the TRIGGERING settled close. Sigma unavailable at entry -> loudly exit-exempt from the sigma legs, time stop only (recorded). |
| Costs | 10 bps/side ETFs, 30 bps/side crypto, on EVERY entry and EVERY partial exit. |
| Kill | freeze at −30% drawdown (standard tournament kill via `mark()`). |
| Evidence | no verdict before 182 days; reviewed with the tournament; ranked by return/maxDD. |

**PURPOSE + PRIOR, stated plainly:** this sleeve isolates the ENTRY AXIS —
buying 20d strength vs buying 20d weakness with identical management. It is
the EXACT INVERSION of `rally_rider`'s entry, and the honest prior is
**NEGATIVE**: mean-reversion entries have a negative desk prior (the mean-rev
family died in backtests), and a 20-day closing low on heavy volume is often
a falling knife. It is deployed anyway as a funder-directed live counter-test
("do it", 2026-08-22), judged only by its 182-day record. If it loses, that
is the prior confirmed at $10k/lesson; if it wins, the desk learns something
its backtests could not teach.

Both sleeves: rule edits invalidate the entry — log a new sleeve instead.
Implementation: `tick_rally_rider_hold` and `tick_dip_rider` in
tournament.py, appended 2026-08-22 with zero deletions; the live siblings
`tick_rally_rider` and `tick_rally_rider_x` are byte-identical to their
frozen registrations.

---

## Disclosure — covered_call_etf stale-fill fixes (appended 2026-08-22T13:40Z, append-only)

**Basis:** funder standing directive 2026-08-22 — "all books need live prices, no
stale prices to trade against" (Non-negotiable #7). That directive authorizes
stale-refusal gates and defect fixes that make the code match this sealed
registration; nothing else was changed. Audit trail: reports/STALE_PRICE_AUDIT.md §2.1.

**Defect (period 2026-08-07 inception → 2026-08-22 fix):** the registered rule is
"buy back at ASK". The close path called `_iwm_chain(...)` — a function that does
not exist anywhere in the codebase — and the resulting NameError was swallowed by a
bare `except`, so every call buy-back would have booked at the INTRINSIC fallback
`max(0, px - strike)` (no time value — systematically below the real ask, i.e.
flattering the sleeve).

**Contamination quantified (counted, not guessed):** **zero closes booked at
intrinsic; estimated flattering $0.00.** The sleeve's one and only short call
(EFA C112, expiry 2026-09-04, 2 contracts) was opened on the inception date
2026-08-07 and is still open; the close path (days_left ≤ 2) has never executed —
confirmed from the sleeve state (`reports/tournament/covered_call_etf.json`:
`short_call.opened == started == 2026-08-07`) and from `reports/desk_tick.log`
(no "closed … call @ ask" line exists). The defect would first have fired around
2026-09-02. Booked history is uncontaminated.

**Fix (2026-08-22, tournament.py `tick_covered_call_etf`):**
1. The close path now calls the real chain helper `_chain(und, call["expiry"])`
   (the rename the audit prescribed, plus the helper's required underlying
   argument, matching the existing marking call) — closes pay the real ASK. This
   RESTORES the registered rule; it does not change it.
2. Stale-refusal gate on BOTH option fills (premium sell at bid, buy-back at ask):
   the fill is REFUSED loudly and retried next tick unless the chain row's
   `lastTradeDate` is ≤ 1 trading day old AND the US regular session is open
   (13:30–21:00 UTC Mon–Fri; holidays caught by the age gate). The swallowed-error
   intrinsic fallback on the close is gone — chain failure now refuses loudly.
3. One bounded exception, disclosed: once `days_left ≤ 0` (contract expired), the
   position settles at intrinsic against the LIVE underlying price — that is the
   contract's settlement value computed from a live price, not a stale quote, and
   it prevents a permanently wedged sleeve when the chain drops the expiry.
   Marking (chain mids) is unchanged — marks are display, not fills.

Entries, exits, sizing, strikes, expiries, costs and the kill rule are otherwise
byte-preserved. Zero deletions outside the fixed close/sell blocks.

---

# ADDENDUM 2026-08-24 — `capacity_probe` ($100,000 paper), FROZEN

**Funder direction, verbatim, 2026-08-24: "commit $100k paper and run it for 8
weeks."** Registered here before the first tick. Append-only: nothing above this
line was edited, and every previously registered sleeve rule is unchanged.

**PAPER ONLY.** No broker, no credentials, no real orders. The $100,000 is
experiment budget.

## 0. PURPOSE — read this before any number below

**This sleeve is PRIMARILY A CAPACITY AND COST MEASUREMENT. The edge test is
secondary and its verdict is explicitly NOT claimed at 8 weeks.**

The funder's thesis is that retail's structural edge lives in the sub-$1M/day
markets institutions are excluded from. The question this sleeve answers in eight
weeks is narrow and answerable:

> Committing $100,000 to that corner, **HOW MUCH could actually be deployed**, at
> **WHAT realized cost**, and **what is the capacity ceiling in $/day**?

It does **not** answer "does `cvd_slope` make money." That needs the full 182
days and is registered as such below. A sleeve that ends eight weeks holding
mostly cash and a thick book of refused fills has **succeeded**, because the
refusals are the measurement. **Refusals are the primary dataset, never a
failure.**

## 1. THE HONEST PRIOR — two corrections, both stated up front

### 1.1 The signal's provenance is weak and is not being oversold

`cvd_slope`, 24h horizon, illiquid cohort ($100k–500k/day) earns **+141.5 bps
gross** (`reports/research_futures_panel.md`, `reports/research_liquidity_floor.md`
§4). It is genuinely **not drift**: always-long *loses* **−37.7 bps** in the same
cohort, and a 2,000-draw random-sign permutation gives **p = 0.025**.

**That p = 0.025 is not evidence, and this registration does not treat it as
evidence.** It is **the best of 288 cohort-band cells**, where **7.2 cells with
p < 0.025 are expected under the pure null**. One cell at p = 0.025 out of 288 is
exactly the expected yield of the search. The house search count behind it is
**N = 2,542**; the cell **does not survive deflation** (DSR 0.0000 against fitted
cost; DSR 0.0316 even against a physically impossible zero-impact cost). Yesterday's
verdict on this corner was **NO EDGE**, and this sleeve does not overturn it.

### 1.2 The cost figures that produced that NO were EXTRAPOLATED, and first measurements contradict them

`reports/research_liquidity_floor.md` said so itself, in its own opening caveat:
**no order-book data existed anywhere below $5M/day.** Every sub-$5M cost number
in that report is a 6-point log-log fit, and the depth leg of that fit has
**R² = 0.256** — the report labelled it "the least reliable line in this report."

A thin-name order-book collector was started **2026-08-24** (`desk_healer.py`
`COLLECTORS["orderbook_thin"]`, 20 PF symbols log-spaced **$81k–$1.4M/day**,
output `reports/microstructure/ob_perp_thin_*.jsonl` under `KrakenQuantDesk`).
Its **first measurements contradict the extrapolation dramatically**:

| Quantity | Extrapolated (R²=0.26 fit) | **First measurement** |
|---|---|---|
| sub-$250k cohort spread | ~119 bps/side | **17.4 bps** (median) |
| book depth at that volume | ~$1.8k, "$2,500 UNFILLABLE" | **~$13.8k** (median) |
| PF_ORDIUSD @ $81k/day | 83 bps spread, $7 top-of-book | **17.2 bps spread, $13,124 bid depth** |
| implied round trip | **238 bps** | **~27 bps** (spread + fees) |

**This is only minutes of data. It is PROMISING, NOT SETTLED, and this
registration treats it as a hypothesis to be measured over eight weeks — not as a
refutation already achieved.** The sleeve exists precisely because the honest
answer to "what does this corner really cost" is currently *unknown*, and the
only way to know is to measure it forward with real committed capital and a hard
fill gate.

## 2. PREREQUISITE FINDING — the signal does not cover the roster (measured, not assumed)

The **signal** comes from the `futures_analytics` panel
(`KrakenQuantDesk/kraken_bot/market_intelligence.sqlite`). The **depth** comes
from the new thin order-book roster. **They cover different symbols.** Measured
2026-08-24:

| | count |
|---|---|
| Thin roster symbols | **20** |
| …with **any** `cvd` history in the panel | **9** |
| …with `cvd` **fresh enough to compute a 24h-horizon signal** (< 24h old) | **1** (PF_PAXGUSD) |

The other eight are stale by **77–174 hours**; eleven have **no `cvd` row at
all**. The panel collector's newest `cvd` bucket overall is 0.1h old, so the
panel is healthy — it is simply **rotating over a different, more liquid symbol
set** than the thin roster.

**Consequence, stated plainly: the sleeve cannot presently generate entries.** A
top-quintile cross-sectional rule with one live name is not a cross-section. The
sleeve is built, registered and live anyway so it is ready the moment coverage
exists, and **it delivers its primary purpose regardless** via the capacity scan
(§4.6), which measures every roster symbol every tick whether or not it trades.

**RECOMMENDATION (NOT IMPLEMENTED — no collector was repointed, no config was
touched):** repoint the `futures_analytics` panel collector at the thin roster,
or add the thin roster to its rotation, so signal and depth describe the same 20
instruments. Until a funder decision, the sleeve reports `signal_breadth` and a
loud `breadth_error` every tick rather than inventing coverage.

## 3. UNIVERSE — frozen, and read live so it cannot drift

The universe is the thin PF roster. The code does **not** hold its own copy: it
parses the roster out of `desk_healer.COLLECTORS["orderbook_thin"]`'s `--symbols`
argv at every tick, so **the sleeve and the collector can never disagree**. If
that read fails the sleeve **refuses to trade** rather than guess a universe.

Listed verbatim as of registration (20 symbols, with the 24h perp dollar volume
measured 2026-08-24):

| # | symbol | $ vol / 24h | | # | symbol | $ vol / 24h |
|---|---|---:|---|---|---|---:|
| 1 | `PF_ORDIUSD` | 82,017 | | 11 | `PF_ETCUSD` | 368,985 |
| 2 | `PF_SPCXXUSD` | 99,406 | | 12 | `PF_TRXUSD` | 424,683 |
| 3 | `PF_XTZUSD` | 108,603 | | 13 | `PF_MORPHOUSD` | 504,376 |
| 4 | `PF_STRKUSD` | 128,177 | | 14 | `PF_ETHFIUSD` | 568,773 |
| 5 | `PF_SPXUSD` | 148,746 | | 15 | `PF_WIFUSD` | 676,406 |
| 6 | `PF_EIGENUSD` | 167,871 | | 16 | `PF_BOMEUSD` | 746,887 |
| 7 | `PF_GLDXUSD` | 203,405 | | 17 | `PF_PAXGUSD` | 930,791 |
| 8 | `PF_KAITOUSD` | 237,005 | | 18 | `PF_POLUSD` | 1,061,930 |
| 9 | `PF_DYDXUSD` | 269,969 | | 19 | `PF_BNBUSD` | 1,110,584 |
| 10 | `PF_APTUSD` | 326,578 | | 20 | `PF_CRVUSD` | 1,430,649 |

Twelve of the twenty sit at or below the $500k top of the cohort band the signal
was found in; the rest extend the measurement up to $1.4M.

## 4. THE FROZEN RULE

### 4.1 Signal
`cvd_slope`, **exactly as `futures_panel_lab.py` defines it**, cited to the line:

* `build_signals()` line 206: `sig["cvd_slope"] = zroll(diff(cvd, 2))`
* `diff(x,k)` line 183: `out[k:] = x[k:] - x[:-k]`
* `zroll(x, w=48)` lines 167–175: causal rolling z-score, window 48, **min 12
  finite obs**

The panel's `cvd` is a **30-minute bucket** series, so `diff(cvd,2)` is the
**1-hour change in cumulative volume delta** and the z-score window is **24
hours**. The lab uses numpy; `tournament.py` deliberately does not, so `zroll`
is **replicated faithfully in pure Python** (`_cp_zroll_last`) rather than
imported — the arithmetic is identical, including `ddof=1` and the 12-obs floor.
Buckets are reconstructed with the same payload handling as the lab's
`build_panel` stage (millisecond/second timestamp normalisation, 1.7e9–1.8e9
sanity window). **The database is opened `mode=ro`; the sleeve never writes to
it.**

**Clarification, disclosed:** in the source research "24h" is the **holding
horizon**, not a signal window. Both are honoured here — the signal's own
z-window is 24h, and the sleeve rebalances **once per UTC day**, i.e. a 24h
horizon.

### 4.2 Direction — LONG / FLAT only
Long the **top quintile** by `cvd_slope` (`ceil(0.20 × n_live)`, minimum 1),
**flat otherwise**. **Shorts are excluded by rule**: the desk's accumulated
shorts prior is negative, and this sleeve is not the place to re-litigate it.

### 4.3 Signal liveness gates (loud, recorded, never guessed)
* A symbol whose newest `cvd` bucket is **older than 24 hours** is DEAD this bar —
  a signal older than its own horizon predicts nothing. Recorded per symbol with
  its measured age.
* **Fewer than 6 live signals ⇒ no ranking at all and no entries**, recorded as a
  loud `breadth_error`. (6 matches `min_breadth` in the lab's own `xs_test`.)

### 4.4 DEPTH-GATED FILLS — the load-bearing mechanism
Before any entry fill, the sleeve reads **the most recent order-book snapshot for
that symbol** from `ob_perp_thin_*.jsonl`. Required clip = **target notional**.

**The fill is ALLOWED only if BOTH hold:**
1. `clip ≤ 0.25 × measured same-side book depth` (`CP_DEPTH_FRACTION = 0.25`).
   Buying crosses the **ask**, so the ask side is the binding one. Snapshot depth
   is a 10-level **base-unit** sum and is converted to dollars at the snapshot
   mid.
2. The snapshot is **less than 15 minutes old** (`CP_MAX_SNAP_AGE_S = 900`) —
   **Non-negotiable #7: no stale fills.**

**Otherwise the fill is REFUSED**, and logged with the **reason**, the
**shortfall**, and **the size that WOULD have been fillable**. Refusal reasons are
enumerated and distinct: `depth_gate`, `stale_snapshot`, `no_orderbook_snapshot`,
`unusable_snapshot_fields`, `max_positions`, `no_free_cash`,
`already_held_never_pyramid`. A per-day `refusal_history` accumulates counts by
reason. **This log is the deliverable.**

### 4.5 REALIZED COST — measured, never assumed
Every fill pays, **per side**:

| Component | Source |
|---|---|
| **spread** = `spread_bps / 2` | **measured** at that snapshot (crossing the touch; the round trip therefore pays one full measured spread — the convention of `research_liquidity_floor.md`) |
| **impact** | depth-walk term, **declared approximation — see below** |
| **fee** = **5.0 bps** | Kraken PF bottom-tier taker, 0.05%/side |

Recorded **per fill**: intended size, filled size, measured spread bps, impact
bps, total realized cost bps, the 10-level depth, the max fillable size, and the
snapshot age.

**DECLARED DEVIATION — the impact model is an approximation, and is flagged as
one.** The collector's snapshot records only top-of-book size, the 10-level
aggregate depth and the spread; it carries **no per-level prices**, so a true
level-by-level walk is **not computable from this feed**. The frozen model is
therefore: **zero impact while the clip stays inside top-of-book, then LINEAR in
the notional consumed beyond it, reaching one full measured spread of extra
slippage when the clip would consume the entire visible 10-level book.** It uses
only measured quantities. Because the depth gate caps the clip at 25% of the
book, **impact is bounded at ≈ 0.25 × spread by construction**. This is a
weaker model than the source report's tick-spaced walk and it is stated here
rather than buried; if per-level data is later collected, replacing it is a
**rule change requiring a funder-signed amendment**.

### 4.6 CAPACITY SCAN — recorded every tick, trading or not
Every tick records, for **every roster symbol**: snapshot age, mid, measured
spread bps, top-of-book $, 10-level depth $, **max fillable $ at the gate**,
whether the target clip fits, the shortfall, and the round-trip cost at the max
fillable size — plus a roster-level summary (`with_fresh_book`, `clip_fits_n`,
`sum_max_fillable_usd`, median spread, median depth). Nothing in it is modelled.
**This accrues whether or not the signal ever fires, and it is what the 8-week
clock judges.**

### 4.7 Sizing
Target **10 concurrent positions max**, each **10% of sleeve equity**, **always
capped by the depth gate** — so **actual deployment is discovered, not assumed**.
Sizing is **net of costs**: notional + cost equals the allocation exactly, and
the allocation is capped at free cash, so **cash can never go negative** (the
invariant is asserted and prints a loud error if violated). **Uninvested capital
sits in cash, and most of it is expected to.**

### 4.8 Exits
* **2.5σ trailing stop** off the **peak settled close**.
* **40-bar time stop** (settled daily bars).
* **σ is FROZEN at entry** via `exit_axis.compute_entry_sigma` (imported, never
  copied). A symbol with insufficient history at entry is **exit-exempt from the
  σ stop** — the time stop alone governs — and that is printed and recorded.
* **NO scale-outs.** Deliberate: partial exits would blur the capacity question.
* Never an exit on the entry bar.

**Exits are NEVER depth-gated.** A risk control must not be blocked by a data
gap. If the snapshot is stale or missing, the exit is **taken anyway** and the
cost is **flagged**: priced from the stale snapshot with its age recorded, or —
if no snapshot exists at all — at a **declared fallback of 30 bps/side**, flagged
`no_snapshot_declared_fallback`. The fallback applies to **exits only, never to
entries**.

### 4.9 Prices
Settled **daily closes of the PF instrument itself** (Kraken public futures
charts, `/api/charts/v1/trade/{symbol}/1d`), so price and order book can never
describe different instruments. **The final candle is ALWAYS the in-progress UTC
day and is ALWAYS dropped** — settled-bar discipline, identical to
`_rrx_settled_kraken`. All 20 roster symbols are covered by this endpoint
(verified at registration). A non-positive or malformed close drops the symbol
for that bar, loudly.

### 4.10 Cadence, kill, and BOTH clocks
* **Cadence:** one evaluation per UTC day; marked to market on every other tick.
* **Kill:** freeze at **−30% drawdown**, via the standard `mark()` rule.
* **CAPACITY / COST VERDICT: 2026-10-19** (8 weeks). This clock judges *only*
  deployment, realized cost and the capacity ceiling in $/day.
* **EDGE VERDICT: NOT BEFORE 182 DAYS.** No P&L claim, positive or negative, is
  admissible before then, and the 8-week report must say so.

### 4.11 Failure discipline
Every failure is **LOUD**: an `ERROR:` line on stdout **and** a state flag on the
sleeve (`roster_error`, `book_error`, `data_error`, `state_error`,
`signal_error`, `breadth_error`, `cash_error`). Nothing fails silently, and no
missing number is ever substituted with a guess. All timestamps in the new code
are **UTC**.

## 5. Registration

* Sleeve name: **`capacity_probe`** · capital **$100,000.00** paper
* Dispatch: `SLEEVES["capacity_probe"]`, `SLEEVE_START["capacity_probe"]` — both
  **append-only** registrations; the `SLEEVES` and `SLEEVE_START` literals higher
  in `tournament.py` are untouched, per the frozen-files rule.
* Implementation: `tournament.py` → `tick_capacity_probe` and helpers `_cp_*`.
  **Zero deleted lines**; `tick_rally_rider`, `tick_rally_rider_x`,
  `tick_rally_rider_hold` and `tick_dip_rider` are **byte-identical** to their
  pre-change forms (verified by AST-level hashing).
* Role: **competitor** (a signal mapping observation to position), though its
  primary output is a measurement.

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


---

## POINTER — Firm risk amendment, 2026-08-27 (append-only, nothing above edited)

Funder-directed 2026-08-27. **No sleeve rule changed.** The sleeves' own
−30%-drawdown kill (Evidence rules, frozen 2026-08-06, `tournament.py` `mark()`)
is untouched and remains the authority for this tournament.

Two things now sit *around* it:

1. The four paper FLOORS (`kraken_floor`, `replica_floor`, `coinspot_floor`,
   `cross_asset_floor`) — which had **no book-level kill at all** — now run the
   **same −30% rule**, imported from `book_kill.py` rather than copied, so the
   floors and these sleeves can never drift on what "killed" means.
2. A **firm-wide** drawdown monitor (`firm_risk.py`) covering every book:
   **WARN at −10%**, **HALT NEW ENTRIES at −20%**, and **NEVER force-liquidate at
   any level** — auto-closing pre-registered sleeves would destroy exactly the
   track records this registration exists to protect. Exits are never blocked.

Related defect fix, same date: `risk_engine.py` called itself FIRM-WIDE while
reading four ledgers and omitting every sleeve here. Sleeves are now covered
(and globbed, so a new one is covered the moment it writes a ledger).

**Full registration:** `FIRM_RISK_AMENDMENT.md`
**Implementation:** `book_coverage.py`, `book_kill.py`, `firm_risk.py`

---

# ADDENDUM 2026-08-27 — `shareholder_yield` ($40,000 paper), FROZEN

Appended 2026-08-27. **Append-only: nothing above this line was edited**, and no
existing sleeve rule changed. `tournament.py` gained one new self-contained tick
function and two registration lines; every live sleeve body
(`tick_rally_rider`, `tick_rally_rider_x`, `tick_rally_rider_hold`,
`tick_dip_rider`, `tick_capacity_probe`) is byte-identical.

## 0. FUNDER DIRECTION

> **"TRADE $40K ON THE FUNDAMENTALS BOOK, FREE REIGN TO DEPLOY"** — funder,
> 2026-08-27.

$40,000 paper. Deployed the same day, on the first tick, at settled closes.

## 1. THE HONEST PRIOR — stated before the first fill, not after the first month

The value / quality / factor family is **the most heavily falsified family on
this desk**. The receipts:

- **360 cross-sectional hypotheses** tested, no after-cost survivor.
- The **equity-factor lab** verdict: *"beta not alpha."*
- **Defensive factors**: the apparent survivor turned out to be de-levered beta
  with **NEGATIVE excess return versus the index**, and its alpha *t* fell to
  **0.20** once the effective-bets correction was applied.
- **Global value long/short**: **0 of 15 variants** survived.

Shareholder yield is therefore deployed as a **funder-directed EXCEPTION to the
survivors-only rule**. The case for the exception — stated as a claim to be
falsified, not as a belief:

> It tests a **DISTINCT axis** — *cash actually returned to owners* rather than
> an accounting ratio — and is the closest fundamentals cousin to **carry**, the
> only family that has ever shown anything real on this desk.

That is an argument for *running the test*, not evidence of an edge. The prior
remains **negative**. This book is judged **only by its 182-day live record**,
reviewed with the tournament. Nothing before day 182 is a verdict.

## 2. DATA-AVAILABILITY FINDING — measured 2026-08-27, before the book traded

The registered signal needs **share-count history**. A book that cannot source
it and quietly fills zeros is not measuring shareholder yield at all — it is
measuring dividend yield under another name. So coverage was measured first.

**RESULT: 75 of 76 names (98.7%) had honestly sourced net-buyback data.**
Coverage is **well above half**; the registered signal **is** honestly
computable on this universe, and the first rebalance is **NOT**
dividend-dominated.

Decomposition of the eight names actually bought (2026-08-26 settled closes):

| | average |
|---|---|
| dividend yield | **2.245 %** |
| net buyback yield | **6.203 %** |
| **buyback share of the signal** | **73.4 %** |

Nearly three-quarters of the signal is buyback, not dividend. Had that number
come back near zero, the honest report would have been "this is a dividend book"
— it did not, and the same decomposition is recomputed and stored every
rebalance (`shareholder_yield.basket_decomposition`) so the claim stays testable
rather than being a one-off boast.

### 2a. The one exclusion, and the trap it exposed

**`BRK-B` — EXCLUDED.** yfinance returns the **Class A** share count (~1.4
million) against a **Class B** price. Left alone it produces a perfectly
plausible-looking, quietly meaningless ~0 % buyback yield — the exact failure
mode this book exists to avoid.

It is caught by a **market-cap cross-check that is part of the frozen rule**:
`share_count × settled_close` must land within a factor of **3** of the reported
market cap. BRK-B lands at **×0.0007** and is excluded and recorded. No name is
ever imputed, and no name is ever assumed to have zero buybacks.

## 3. UNIVERSE — fixed, verbatim, and written into the code

**76 liquid US-listed large/mid caps**, chosen once by hand across every GICS
sector, frozen in `tournament.py` as `SY_UNIVERSE` and reproduced here verbatim
so it can never drift:

```
Information technology / communication services (17)
  AAPL  MSFT  GOOGL  META  NVDA  AVGO  ORCL  CSCO  ADBE  CRM
  INTC  IBM   QCOM   TXN   AMAT  ACN   INTU
Consumer discretionary (8)
  AMZN  TSLA  HD    MCD   NKE   SBUX  LOW   TGT
Consumer staples (9)
  COST  WMT   PG    KO    PEP   PM    MO    MDLZ  CL
Financials (12)
  BRK-B JPM   BAC   WFC   C     GS    MS    AXP   BLK   SPGI  USB   PNC
Health care (11)
  UNH   JNJ   PFE   MRK   ABBV  LLY   TMO   ABT   AMGN  GILD  CVS
Industrials (8)
  CAT   DE    HON   UNP   UPS   RTX   LMT   GD
Energy (4)
  XOM   CVX   COP   SLB
Materials / utilities / real estate (7)
  LIN   FCX   NEE   DUK   SO    AMT   PLD
```

**DISCLOSED BIAS.** These are all names that are large and listed **today**
(2026-08-27); the list was **not** reconstructed as of a past date. This is
**not** a live index-membership pull — membership is fixed at t0 and is never
re-pulled, so no future information can enter the forward test. For a forward
live test the survivorship effect is therefore small, but it is **real for
anyone who later backtests this list**, and it is stated here rather than
buried.

## 4. THE FROZEN RULE

| | |
|---|---|
| **Capital** | $40,000 paper, funder-directed 2026-08-27 |
| **Universe** | the 76 names above, fixed and verbatim |
| **Signal** | **shareholder yield = trailing-12m dividend yield + net buyback yield** |
| **Net buyback yield** | net **share-count** reduction over the trailing 12 months, from the quarterly balance sheet (`Ordinary Shares Number`, falling back to `Share Issued`): latest non-null observation vs. the earlier observation whose span is closest to 365 days and **≥ 300 days**, annualised as `(prior − latest) / prior × 365/span`. A share **count** is used precisely because it already nets off issuance and stock comp — a cash-flow "repurchase" line does not. |
| **Dividend yield** | trailing-12-month sum of **actually paid** dividends ÷ settled close. An empty dividend series on a name with sound price history is a **genuine** non-payer (e.g. ADBE) and books a true 0.0, recorded as such. A dividend **fetch failure** excludes the name. |
| **Exclusions** | a name whose buyback data cannot be honestly sourced is **EXCLUDED and recorded** — never imputed, never assumed zero. Exclusion triggers: no share-count row; fewer than 2 non-null observations; span < 300 days; no market cap for the cross-check; `share_count × price` outside **⅓–3×** market cap (the BRK-B share-class trap); \|net buyback yield\| > **50 %/yr** (data error); any fundamentals or dividend fetch error. |
| **Book** | **top decile by shareholder yield, or top 8, whichever is LARGER**, **EQUAL WEIGHT**. With 75 usable names the decile is 7.5 → top **8** governs. |
| **Cadence** | **quarterly — 63 trading days**, matching the house cadence. Evaluated at most **once per UTC day**. |
| **Costs** | **10 bps PER SIDE, charged on every fill.** |
| **Sizing** | **NET OF COSTS BY CONSTRUCTION.** `_sy_solve_notional` bisects for the largest equal per-name notional `N` satisfying `avail − k·N − fee·Σ\|N − haveᵢ\| ≥ 0`, so residual **cash can never go negative** — it is a construction, not a check afterwards. |
| **Fills** | **SETTLED daily closes only.** The final bar is dropped whenever its date equals the current UTC date, because during a live session that bar is still in progress. |
| **Kill** | **freeze at −30 % drawdown**, the standard tournament `mark()` rule. |
| **Refusal** | fewer than **20** usable names ⇒ **REFUSE to rebalance**, loudly; no fills, book unchanged. Coverage below **50 %** ⇒ loud `ERROR` line and a `coverage_warning` state field warning the rebalance may be dividend-dominated. |
| **Evidence** | **no verdict before 182 days**; reviewed with the tournament. |

### 4a. Benchmarks — SPY **and** the equal-weight universe

Both benchmarks run on the **same $40,000 start, the same 10 bps per side and
the same settled closes** as the sleeve (`bench_spy`, `bench_ew_universe`).

The universe benchmark is the one that matters most: it **separates the SIGNAL
from the BASKET**. If the top-decile book cannot beat its own equal-weight
universe, then the shareholder-yield **ranking** added nothing and the book has
failed on its own terms — whatever it did against SPY.

### 4b. Loud-failure contract

Every failure path prints an `ERROR:` line **and** sets a state field on the
sleeve: `data_error`, `state_error`, `signal_error`, `coverage_error`,
`coverage_warning`, `bench_error`, `cash_error`. This book never fails silently,
because a silent failure here degrades it into dividend yield wearing another
name.

## 5. FIRST TICK — 2026-08-27, reported as it happened

Settled bar **2026-08-26**. Buyback data usable for **75 of 76** names
(**98.7 %**); **1 excluded** (BRK-B, share-class trap). Top **8** by shareholder
yield, equal weight:

| rank | name | shareholder yield | dividend | net buyback |
|---|---|---|---|---|
| 1 | CRM | +15.342 % | +0.833 % | +14.509 % |
| 2 | C | +10.729 % | +1.849 % | +8.879 % |
| 3 | WFC | +8.128 % | +2.171 % | +5.958 % |
| 4 | BAC | +7.490 % | +1.800 % | +5.690 % |
| 5 | MO | +6.801 % | +6.134 % | +0.666 % |
| 6 | ADBE | +6.557 % | 0.000 % | +6.557 % |
| 7 | COP | +6.384 % | +2.573 % | +3.812 % |
| 8 | XOM | +6.154 % | +2.605 % | +3.550 % |

**8 fills, all buys.** Total cost **$39.96** (10 bps on $39,960 deployed).
Deployed **$39,960.04**; residual cash **$0.00** (7×10⁻¹² — non-negative by
construction). Equity **$39,960.04**, i.e. **−0.10 %**, which is the entry cost
and nothing else. Both benchmarks initialised the same day at the same
$39,960.04 after the same $39.96 of cost.

**Note on CRM.** Its +14.5 % net buyback yield comes from a reported share count
falling 929 M → 819 M in a single quarter. That is the sourced figure, inside
the ±50 % plausibility band and consistent with market cap (×1.00), so it is
booked as reported rather than quietly adjusted — but it is flagged here so the
182-day review knows the top pick rests on one large single-quarter move.

## 6. Registration

- **Implementation:** `tournament.py` → `SY_UNIVERSE`, `_sy_settled_closes`,
  `_sy_name_yield`, `_sy_solve_notional`, `_sy_rebalance_to`, `_sy_bench_step`,
  `tick_shareholder_yield`.
- **Dispatch (append-only):** `SLEEVES["shareholder_yield"]`,
  `SLEEVE_START["shareholder_yield"] = 40000.0`.
- **Ledger:** `reports/tournament/shareholder_yield.json`.
- **First review:** 2026-11-06 with the tournament. **No verdict before 182
  days.**
- **PAPER ONLY.** No broker, no credentials, no real orders.

### shareholder_yield — hostile-review defect D1 fixed (2026-08-27, append-only)

Review verdict PARTIAL. Signal honesty PASSED decisively — buyback yield IS
computed from real share-count history (all 8 holdings reproduced to 4dp from an
independent raw pull; 26 of 75 names carry NEGATIVE buyback yield, which a
zero-filling book cannot produce), and the "it's really just dividend yield"
hypothesis was REFUTED (top-8 by shareholder yield overlaps dividend-only 1 of 8,
buyback-only 7 of 8; Spearman 0.416 vs 0.733). BRK-B was correctly excluded — the
provider returns the Class A share count against a Class B price, which without
the market-cap cross-check would have produced a plausible-looking, meaningless
~0% buyback yield.

**DEFECT D1 (fixed, CM-043):** `_sy_bench_step` computed benchmark equity as
`sum(u*px[t] ... if t in px)` with NO fallback, silently DROPPING any unpriced
holding. `yf_last()` returns an empty dict whenever its download raises, so a cold
cache collapsed a benchmark to **$0 / −100% with no error and no state flag**, and
one missing name in 75 shaved 1.4% off invisibly. This biased the equal-weight
universe benchmark **DOWNWARD — in the sleeve's own favour** — on every data gap,
and §4a of this registration calls that benchmark "the one that matters most".
**Fixed:** holdings now mark at last-known price (the discipline `mark()` already
uses), never dropped; any gap sets `bench_error` and prints an ERROR line, with a
second louder line if a holding has no last-known price at all. Verified against
the reviewer's own probes: `px={}` no longer collapses, and a 1-of-75 gap no
longer moves the benchmark.

**Disclosed, not fixed (they change no rule; stated so the 182-day review reads
the numbers correctly):** (D2) on the daily mark path the SPY benchmark is priced
with `yf_last()` (which does not drop an in-progress bar) while the sleeve marks
on settled closes, so an intraday same-instant sleeve-vs-benchmark read is not
strictly like-for-like — the REBALANCE path, which decides the record, uses
settled closes for both. (D3) there is no reporting-lag or recency guard on the
balance-sheet observation: the eight picks' period-ends span 2026-03-31 to
2026-06-30, up to 91 days apart, so the cross-sectional ranking mixes
non-contemporaneous windows. (D4) the "buyback is 73.4% of the signal" figure is a
ratio of averages over the SELECTED names and is therefore selection-biased upward
by construction; the universe-wide mean buyback yield is only +0.049%.
