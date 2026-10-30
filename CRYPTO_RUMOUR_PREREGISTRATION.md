# Crypto Rumour-Rider — PRE-REGISTRATION (funder-directed, 2026-08-27)

**Status:** SPECULATIVE FORWARD TEST. $20,000 paper. PAPER ONLY.
**Standing:** documented exception on the `rally_rider` precedent. **The backtest
does NOT support this strategy.** It is deployed as a declared trial to generate
out-of-sample evidence, never as a validated edge. Sealed before it runs.

## 1. What the funder asked, and what is actually implementable

Asked: *"rally ride the crypto universe, fundamental NEWS / Rumour — buying on the
rumour selling before the news… from low vol, to top 50… lowest fee platform."*

**There is no rumour feed.** DefiLlama's unlock/emissions endpoint returns
**HTTP 402 Payment Required**; CoinGecko's events endpoint **404s**. Buying an
actual rumour is neither implementable nor testable here.

What is both: rumours leave a **footprint in the tape** before the news prints —
abnormal volume with price drift while the information is still private, then a
volume **climax** as it becomes public.

    "buy the rumour"       -> enter on abnormal volume + early drift
    "sell before the news"  -> exit INTO the volume climax, not after it

## 2. Fees — measured, not assumed

| venue | taker | maker | source |
|---|---|---|---|
| **Binance** | **10 bps** | 10 bps | documented VIP0 |
| Kraken | **40 bps** | 25 bps | **measured live** from `AssetPairs`, 2026-08-27 |
| Coinbase | 60 bps | 40 bps | documented |

Binance is the lowest-fee venue. **Every result below is reported at BOTH 10 and
40 bps**, because the desk's actual venue is Kraken and assuming a fee tier we do
not have is precisely the CM-042 error. This sleeve is modelled at **40 bps**.

## 3. THE BACKTEST VERDICT — this is why it is a trial, not an edge

106 Binance USDT spot series, daily bars, next-bar-open entry (no lookahead).
**Search count: 16 parameter sets x 2 fee levels = 32 tests.** Best set:
`vol_mult 3.0, drift_days 5, drift_min 5%, climax 6.0x, max_hold 5d, stop 12%`.

**(a) The band split REVERSES the funder's thesis.**

| band | fee | n | mean | median | win | t |
|---|---|---|---|---|---|---|
| **top-50** | 10 bps | 455 | **+1.892%** | −0.233% | 49.0% | **2.86** |
| **top-50** | 40 bps | 455 | **+1.292%** | −0.833% | 47.5% | 1.96 |
| low-vol | 10 bps | 1141 | +0.255% | −2.583% | 42.2% | 0.59 |
| **low-vol** | 40 bps | 1141 | **−0.345%** | −3.183% | 40.5% | −0.79 |

The signal lives in the **top-50**, not in low-vol. The retail "we can trade thin
markets" thesis does **not** hold for this strategy: in liquid names a 3x volume
spike is information; in thin names it is noise, and the spread eats it.

**(b) It FAILS out of sample.** Same parameters, sample split in time:

| period | fee | n | mean | t |
|---|---|---|---|---|
| 1st half (older) | 40 bps | 216 | **+2.601%** | 2.44 |
| **2nd half (recent)** | 40 bps | 217 | **−0.055%** | **−0.06** |

**The edge exists only in the older half and is gone in the recent half.** That is
the signature of a decayed or overfit signal.

**(c) The profit is a lottery ticket.** **155% of all profit comes from the best
5% of trades** — remove them and the strategy loses money. Median trade is
negative (−0.833%). Worst −12.8%, best +68.3%.

**(d) The defining mechanism barely fires.** Of 455 exits: **87 climax** (19%),
129 stop, 239 max-hold. "Sell before the news" operates on one trade in five; the
rest is a 5-day momentum hold with a stop.

**(e) Deflation is marginal even before the above.** Best t across all 32 tests
was 1.98 against an expected max of **1.92** under the null. That is not an edge
clearing a bar; that is a coin landing on its edge.

**Control (identical random entries, both fee levels):** 10 bps mean +0.281%
(t 0.69); 40 bps mean −0.319% (t −0.79). The top-50 result does beat random — which
is the only reason this is worth a forward test at all.

## 4. What is deployed

**$20,000, split so the forward test answers the question the backtest could not:**
- **$14,000 top-50 band** — where the evidence says any signal lives.
- **$6,000 low-vol band** — a declared CONTROL. The backtest says it loses; the
  funder asked for it; so it is MEASURED rather than assumed in either direction.

Both bands are booked and reported **separately**. Costs charged at **40 bps per
side**. Entries depth-gated. Longs only. No leverage.

## 5. Frozen rules

- Entry: `vol/median20 >= 3.0` AND 5-day drift `>= +5%` AND `vol/median20 < 6.0`
  (already-climaxing names are refused — that is the news, not the rumour).
- Exit, first to trigger: volume climax `>= 6.0x median` (sell INTO it) |
  hard stop −12% | max hold 5 days.
- Max 8 concurrent positions per band; one position per symbol.
- Universe refreshed daily from Binance 24h quote volume; stablecoins and
  leveraged tokens excluded.

## 6. Kill rules — pre-committed, not discretionary

- **Sleeve halts NEW entries at −20% from its own peak equity.** Exits always run.
- **Hard stop of the experiment at 100 closed trades OR 182 days**, whichever
  first, then a single verdict is written. No extension, no re-parameterisation.
- Changing ANY parameter above invalidates this registration and starts a new one.

## 7. What would make this a real edge

Out-of-sample mean materially above the random control at **40 bps**, with the
climax exit firing on a meaningful share of trades, and profit NOT concentrated in
the top 5%. Absent that, the honest conclusion is that crypto volume-spike
anticipation decayed as the market got more efficient — which is what the
second-half result already says.

**Sealed 2026-08-27 before any capital was committed.**

---

## Defect disclosure — CM-053 staleness gate (2026-08-29, append-only)

**Not a rule change; a gate that was mis-specified against the data's own cadence.**
Entry/exit rules, sizing, bands, costs and kills are byte-unchanged.

The sleeve applied a single 6-hour freshness rule to the SETTLED DAILY BAR used
for the signal. A settled daily bar is up to ~24 hours old by construction the
moment it settles, so from ~06:00 UTC onward every day:

- every open position was flagged **STALE** on the dashboard, and
- the ENTRY loop refused **every** candidate.

**Eighteen of every twenty-four hours the sleeve could not trade at all, and the
board cried wolf on data that was fine.** An alarm that fires 75% of the time is
not an alarm.

The error was conflating two different questions. Non-negotiable #7 — *"all books
need live prices, no stale prices to trade against"* — governs **the price we
trade at**, which here is the live Binance 24h ticker and is real-time. The daily
candle is a **signal input**, not the execution price.

**Fixed:** `MAX_SIGNAL_BAR_AGE_SEC = 30h` (one daily bar plus slack — still
catches a genuine feed outage) and a separate `MAX_LIVE_PX_AGE_SEC`; an entry is
now refused if there is **no live ticker price**, which is the real Non-negotiable
#7 test. `mark_stale` now means "we could not get a live price, or the signal feed
has stalled" — not "a daily bar is a day old".

Verified after the fix: **0 of 7 positions flagged stale** (was 7 of 7), and the
entry path is open around the clock rather than for a six-hour window after UTC
midnight.

---

## Exit study — "is the best exit to lock profit?" NO. (2026-08-29, append-only)

Funder asked whether the exit design locks profit and, if not, to build one and
make it permanent. **Nine exit variants were tested on the same entry signal,
same universe, same 40bps. The pre-registered design WON. No rule is changed.**

| variant | mean | median | win | t | top-5% share | worst |
|---|---|---|---|---|---|---|
| **A — current: climax / −12% / 5d** | **+1.292%** | −0.833% | 47.5% | **1.96** | 155% | −12.8% |
| B — + trail 8% off peak | +1.023% | −1.384% | 45.2% | 1.71 | 183% | −12.8% |
| C — + trail 12% off peak | +1.182% | −1.242% | 45.6% | 1.86 | 163% | −12.8% |
| D — hard take-profit +10% | **+0.001%** | +1.742% | **53.5%** | 0.00 | — | −12.8% |
| E — hard take-profit +15% | +0.267% | −0.542% | 48.3% | 0.53 | 256% | −12.8% |
| F — bank 50% @+10%, rest BE+trail | **−1.281%** | +0.732% | 50.9% | **−3.62** | — | −12.8% |
| G — bank 50% @+15%, rest BE+trail | −0.115% | −1.001% | 47.2% | −0.26 | — | −12.8% |
| H — breakeven stop after +5% | **−3.210%** | −0.800% | **6.8%** | **−12.86** | — | −12.8% |
| I — F with 10-day hold | −1.006% | +0.607% | 50.9% | −2.52 | — | −12.8% |

**Every profit-locking variant made it worse, several significantly so.**

**Variant D is the trap worth naming.** A hard +10% take-profit raises the win
rate to **53.5%** and turns the median trade **positive (+1.742%)** — it looks and
feels far better on a dashboard — while the mean collapses from +1.292% to
**+0.001%**. It converts a thin edge into exactly nothing, and it does so while
appearing to improve. Variant H is worse still: a breakeven ratchet after +5%
gets shaken out constantly, leaving a **6.8% win rate and t = −12.86**.

**Why: this is a fat-tailed momentum strategy.** Its entire expectancy lives in
the right tail — the same trait recorded at registration (155% of profit from the
best 5% of trades). Every profit-lock amputates that tail. You cannot bank the
middle of a distribution whose mean lives in its edge.

**A defect in this study, caught and corrected.** The first run checked the trail
BEFORE the stop, so a gap-down exited at the close (−25.8%) instead of the stop
(−12.8%). That flattered variant C into looking like the winner (+1.346%, t 2.13).
With the protective stop evaluated first — which is the correct order — C falls to
+1.182%, t 1.86, **below** the current design. **The apparent improvement was my
own bug, not an edge.**

**Decision: NO CHANGE. The frozen rules stand and the seal is intact.** Nothing was
invented to satisfy the request — inventing an exit the evidence contradicts would
be the fabrication this desk exists to refuse. What is locked in permanently is
the FINDING: *on this signal, locking profit destroys it.* Any future proposal to
add a take-profit, a scale-out or a breakeven ratchet to this sleeve must first
beat +1.292% mean / t 1.96 on this same test.

---

## AMENDMENT v1.1 — maker execution (funder-directed 2026-08-30, registered BEFORE it runs)

**Timing matters and is stated first: this sleeve has ZERO closed trades.** The
amendment lands before a single trade has completed, so the forward test is
uncontaminated by it. Had trades already settled, this would have required a new
registration rather than an amendment.

### What changes

1. **`COST_BPS_SIDE` 40.0 → 25.0.** Kraken's MAKER fee at the BASE tier —
   measured live from `AssetPairs`, requiring **no volume qualification at all**.
   The desk has been modelling taker (crossing the spread) throughout.
2. **Entries become POST-ONLY.** A limit is rested **5 bps below** the reference
   price and fills only if the market trades **through** it. Unfilled orders
   expire after `MAKER_ORDER_TTL_TICKS` and are recorded as MISSES.
3. **Exits remain TAKER at 40 bps.** An exit that does not fill is not an exit.
   The stop, the climax exit and the time stop must be able to cross the spread —
   charging the cheaper fee on an exit we might not get would be exactly the kind
   of flattery this desk refuses.

### Why — measured, not assumed

`reports/research_maker_fill_2026-08-30.md`, 151,201 one-minute bars over 15 pairs
plus a direct test on this sleeve's own signal:

| execution | mean | fill | t |
|---|---|---|---|
| taker 40 bps (before) | +1.292% | 100% | 1.96 |
| **maker 10 bps, 5 bp under** | **+1.907%** | **99.1%** | **2.88** |

**The adverse-selection risk is real and was measured, not waved away.** The
trades a passive order misses went on to make **+4.35%**, well above the +1.9% we
capture — the concern was correct in direction. It is negligible in size only
because the fill rate is 99.1%: missing 0.9% of trades worth +4.35% costs about
**2 bps** against a **61 bp** fee saving.

### What this amendment does NOT claim

- **It does not claim the 10 bps tier.** This sleeve moves to **25 bps**, the base
  maker rate, which needs no volume. The 10 bps figures above are the ceiling the
  route reaches, not what is charged here.
- **It does not make this an edge.** The registered verdict stands: the backtest
  FAILED out of sample (recent half −0.055%, t −0.06) and 155% of backtest profit
  came from the best 5% of trades. Cheaper execution improves a strategy that is
  still not validated.
- **No entry rule, exit rule, band, size or kill is touched.** Vol multiple 3.0,
  climax 6.0x, drift +5%, stop −12%, hold 5d, 8 positions/band, −20% halt, 100
  trades / 182 days — all byte-unchanged.

### The sleeve now measures its own fill rate

Every posted order records fill or miss, and every miss records what the trade
would have done. **The sleeve becomes its own live fill-rate probe**, which is the
number the whole fee route depends on and which `maker_calibration_probe` could
only sample six times in a week.

**Amendment sealed 2026-08-30 before any capital moved under it.**

### Defect CM-056 — duplicate maker fill leaked $750 (2026-08-30, append-only)

**A code defect in amendment v1.1, not a market loss, and not a rule change.**

The candidate list `cands` is built BEFORE the pending-order loop runs. So a
symbol that filled during THIS tick was still sitting in the stale list, got
re-posted, filled a second time, **overwrote the position dict** and **debited
cash again**. Observed live on `BEAMXUSDT`: two `MAKER_FILL` events, one
position, **$750 gone from the low-vol band**.

It surfaced only because the sleeve's own `-5%` watcher fired at −6.22% and the
band accounting was checked rather than believed: low-vol cash was $2,250, which
is exactly `$6,000 − 5 × $750` against **four** positions.

**Fixed at both sites.** The posting loop now re-checks `sym in bk["positions"]`
at post time (not at list-build time), and the fill site refuses outright to
overwrite a live position or debit cash for one already held, logging
`DUPLICATE_FILL_REFUSED`. Both guards proven on a scratch copy.

**Repaired:** $750.00 credited back to the low-vol band and the duplicate
$1.88 entry cost reversed, recorded in `bands.lowvol.corrections`. The position
held is the SECOND fill; the first fill's debit was orphaned. Both bands now
reconcile to the cent: cash equals `capital − positions × unit`.

**The reported drawdown was wrong while this was live.** The sleeve showed −6.22%
when the true figure was about −2.4%; $750 of the "loss" was a bookkeeping leak.
After repair: equity $19,521.34, **−2.39%**. Stated plainly because a drawdown
that is really a defect must never be left standing as performance.

---

## v1.1 VERDICT — maker execution CONFIRMED, on the pre-committed threshold (2026-08-30)

The threshold was set BEFORE the data existed: **30 fills and 30 misses with
4-hour forward outcomes, at the 5 bp offset the sleeve actually posts at.**
That bar is now met and the answer is recorded exactly as committed.

### The reading

    5 bp offset, n=329 resolved orders
      filled   n=163   mean fwd -0.5603%   sd 2.384
      missed   n= 34   mean fwd -0.4917%   sd 1.282
      fill rate 84.8%

    ADVERSE (missed - filled) = +0.0686%
    standard error             =  0.2885%
    95% CI                     = [-0.497%, +0.634%]
    t = 0.24  ->  INDISTINGUISHABLE FROM ZERO

    adverse cost = (1 - 0.848) x 0.0686% = 0.0104 pp
    fee saving   (40 -> 25 bps on entry) = 0.1500 pp
    NET                                  = +0.1396 pp

**VERDICT: NET POSITIVE. v1.1 maker execution STAYS.**

### Why this is a decision and not a coin-flip

Adverse selection at the sleeve's offset is **statistically indistinguishable from
zero** (t = 0.24). That is not a weak result — it is the *answer to the question
that was asked*. The fear was that passive orders systematically miss the winners.
At 5 bp they do not: the orders we miss perform the same as the ones we fill,
within noise.

**The decision is robust across the entire confidence interval:**

| | adverse | net |
|---|---|---|
| best case (CI low) | −0.497% | **+0.2255 pp** |
| point estimate | +0.069% | **+0.1396 pp** |
| **worst case (CI high)** | **+0.634%** | **+0.0536 pp** |

Even at the pessimistic end the fee saving wins. **Adverse selection would have to
exceed 0.987% — 1.6x the upper bound of the interval — to flip it.**

### What is NOT claimed

- **The 99.1% fill assumption in the original amendment was WRONG.** Measured:
  **84.8%**. The amendment's arithmetic was right for the wrong input; it survives
  because the adverse term collapsed, not because the fill rate held.
- **This does not make the sleeve an edge.** The registered verdict stands: the
  backtest FAILED out of sample (recent half −0.055%, t −0.06) and 155% of
  backtest profit came from the best 5% of trades. Cheaper execution improves a
  strategy that is still not validated.
- **This is 5 bp only.** The same probe measures adverse selection turning clearly
  POSITIVE at deeper offsets (+0.303% at 25 bp, +0.460% at 50 bp on larger missed
  samples). **Posting deeper to chase a better price would be a mistake**, and the
  frozen 5 bp offset is in the favourable half of that curve by measurement rather
  than by luck.

### Method notes, for the record

An earlier POOLED reading across all six offsets returned "NET POSITIVE" and was
**overruled and disarmed**: adverse selection reverses sign across offsets, and
58% of that pooled sample came from 25–50 bp offsets the sleeve never posts at.
Averaging a sign flip manufactures a confident verdict. Only the per-offset
reading was allowed to decide.

The probe itself was corrected twice before it could be trusted — once for
counting a price TOUCH as a fill (98.6% vs a real 50%), and once for sampling the
ticker one per tick instead of using true 1-minute intraday lows (57.0%, biased the
other way). Both are recorded in `reports/research_maker_fill_2026-08-30.md`.

---

## DISCRETIONARY OVERRIDE — HEMIUSDT closed by funder instruction (2026-08-31)

**Funder-directed, in-session: "SELL HEMI NOW."** Executed immediately. Recorded
here because a forward test whose interventions are not on the record is not a
forward test.

    HEMIUSDT  entry 0.01165 -> exit 0.0153 (live ticker)  = +31.33%
    proceeds $2,289.09  - exit fee $9.16 (40 bps taker)   = +$536.93
    held 2.10 of 5 days

**None of the three registered exits had fired.** Volume on the settled bar was
1.46x its 20-day median against a 6.0x climax threshold; the stop was 31.6% away;
2.9 days remained on the clock. This was a hand close, not a rule exit, and it is
labelled `discretionary: true` in the ledger with a `discretionary_events` entry.

### What it does to the record — stated plainly, not buried

The sleeve's realised P&L flips **-$456.08 -> +$80.85** and its win rate becomes
**1 of 3**. That improvement is **not** attributable to the strategy. It is
attributable to a discretionary decision to close the single largest winner while
it was open, which is the textbook contaminant of a forward test: the rule set
never got to say what it would have done.

**Any future evaluation of this sleeve must treat trade 3 as an intervention, not
an observation.** Two honest ways to read the record from here:

1. **Rules-only:** exclude HEMIUSDT entirely. Realised becomes -$456.08 over two
   trades, both stops. That is what the registered rules have actually produced.
2. **As-traded:** include it at +$536.93 and carry the note that a human hand
   produced the only winner.

Neither is wrong; presenting only #2 without the label would be.

### The counterfactual is now unknowable, and that is the cost

Had the rules run, HEMIUSDT exits on the 5-day clock (the likely case, since no
climax was developing), on a climax if one arrived, or on the stop after a -31.6%
reversal. **Which of those would have happened can never be measured**, and the
sleeve's central open question -- does the volume-climax exit ever actually fire?
-- lost its most informative trade. Three trades in, the climax exit has still
fired ZERO times.

**This is the funder's capital and the funder's call. The record simply has to say
what happened.** The frozen rules are byte-unchanged; no parameter was altered.
Sleeve after the close: $19,393.74, -3.03%, 14 open, day 2.1 of 182.

### Funder intent recorded, and rules-only resumed (2026-08-31)

The funder's stated reason for the HEMIUSDT close, in his words: **"I JUST WANTED
TO BANK SOME PROFIT."** Recorded because intent belongs in the record as much as
the action does — this was a deliberate, disclosed decision to realise a +31.33%
gain, not a change of mind about the rules.

**Direction: "RUN THE REMAINING 14 ON RULES."** No code change was required — the
sleeve has only ever executed the frozen rules; the HEMIUSDT close was a manual
action taken outside it. The 14 open positions and every subsequent entry run on
climax >= 6.0x / stop -12% / max hold 5d, unchanged.

**CM-063: the two P&L numbers are now published automatically every tick** so
neither can be quoted without the other:

    realised: as-traded $+80.85  |  RULES-ONLY $-456.08 (2 trades)
              - 1 discretionary close excluded

`realised_rules_only_usd` **is the only number the 182-day verdict may be based
on.** `realised_as_traded_usd` is what the account actually did. Any tick where
they diverge prints both, and the ledger carries `closed_discretionary` so a
future reader cannot mistake one for the other.

If no further discretionary closes occur, the two converge naturally and the note
switches to "no discretionary closes — as_traded and rules_only are identical".

### Defect CM-064 — the same symbol was held in BOTH bands (2026-08-31, append-only)

**A breach of a frozen rule, found live.** §5 states: *"Max 8 concurrent positions
per band; one position per symbol."* The "per band" qualifier governs the COUNT
(max 8); **symbol uniqueness is sleeve-wide**. CM-056's duplicate guard only
checked within a band, so a coin whose 24h volume drifted from the top-50 band
into the low-vol band could open a SECOND position.

Observed live: **COTIUSDT** entered top-50 at 2026-08-30T00:22:00Z and low-vol at
2026-08-30T22:13:03Z — same coin, two bands, 22 hours apart.

**Two harms, and the second is the worse one:**
1. Double exposure to a single name across a sleeve that caps position count
   precisely to avoid that.
2. **It contaminates the only comparison the two bands exist to make.** top-50 vs
   low-vol is the test of the funder's thin-market thesis; a coin appearing in
   both makes the two legs non-independent for that name.

**Fixed at both sites:** the posting loop and the fill site now check `positions`
AND `pending` across **every band**, not just the current one.

**The offending position was closed** to restore the rule — the low-vol leg, which
is the one that should never have opened. It closed at **+5.18%, +$35.62**, and
that P&L is **EXCLUDED from rules-only** and flagged `discretionary: true`. It was
profitable; that is irrelevant. A position opened in breach of a frozen rule is
not an observation the rules produced, and counting it would flatter the forward
test with a defect.

Post-fix state: **no symbol appears in more than one band.** Realised now reads
**as-traded +$116.47 | RULES-ONLY −$456.08 (2 trades), 2 exclusions.**

**The rules-only number has not moved through any of this** — which is the point of
carrying it separately.

### Live record at 9 closes (2026-09-02, append-only)

    rules-only:  7 trades, 1 win (14%), realised -$1,333.21
    as-traded:   9 trades,             realised   -$760.66  (2 exclusions)

**Backtest expected a 47.5% win rate. Live is 14% on n=7.** Six of seven rule
exits were the -12% stop. At n=7 that is not yet evidence — six-of-seven stops
happens ~7% of the time at a true 47.5% — but it is the wrong direction and it is
recorded rather than waited out.

**The volume-climax exit fired for the first time:** ARBUSDT at 12.2x median
volume, +0.70%, held 0.1 days. The mechanism the strategy is named after works;
on its first firing it captured 70 bps. One climax in seven rule exits (14%) versus
the backtest's 19%.

**A design observation, not a breach:** PROMUSDT was stopped at -12.23%, re-entered
0.8 days later on a fresh signal, and stopped again at -12.39% — the same name
took two full stops in two days. The frozen rules permit re-entry; nothing was
violated. But a momentum signal that re-fires on a name that just stopped out is
buying the same falling knife twice, and if this recurs it belongs in the
182-day verdict as a structural weakness of the entry rule.
