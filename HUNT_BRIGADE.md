# THE HUNT BRIGADE — how this desk finds, sizes and trades a target

**Standing operating procedure. Funder-directed 2026-09-05:** *"the slice is where our money
is earned — retain all features of finding and sizing out our targets and trading them."*

This is the pipeline that produced the sleeve's first two positions (TAP, Kanadevia). Every
step exists because a previous hunt failed without it. Nothing here is optional.

---

## 0. THE ONE LESSON THAT MAKES IT WORK

Six sweeps (~160 domains, ~500 agents) found zero survivors. Then two positions opened in an
afternoon. The difference was not the market. It was **the order of operations**:

> **Measure the flow FIRST. Write the thesis only if the number survives.**

Every failed candidate did it backwards — asserted a forced seller, wrote a narrative, then got
killed. The dislocation sweep's own post-mortem: *"Nobody measured whether the forced flow is
large enough to SET the price. Every one of these theses ASSERTED it."*

---

## 1. FINDING — hunt SITUATIONS, not cheapness

Cheapness screens find things that are cheap for a reason a careful reader can locate. Thirty
of them produced six candidates and zero survivors (ARI: three foreclosed properties, one a
half-built tower; SITC: adversely selected by construction; Subaru: 33% *above* fair value on
its own framework).

Hunt instead for the four shapes where **someone is selling for a reason unrelated to value**:

| shape | the question | example |
|---|---|---|
| **Forced seller** | who MUST sell, why, and when does it stop? | S&P/Nikkei deletions, fund wind-ups, spin-off orphans |
| **Misread fact** | what did the filing say that the headline got wrong? | one-off charge capitalised as permanent |
| **Orphaned security** | who is structurally unable to own this? | wrong index, wrong size, dual-listing gap |
| **Dated structural event** | where is the value CONTRACTED in a document? | dissolution vote, tender expiry, buy-sell deadline |

Every candidate must name: **WHO** is selling · **WHY** they have no choice · **WHEN** it stops,
from a document with a URL · **WHAT KILLS IT** · **DOWNSIDE FIRST**, upside second.

Workflow: `workflows/scripts/tfo-dislocation-hunt-*.js` (34 situation families).

---

## 2. SIZING — the instruments, run BEFORE the thesis

### 2a. Index events → `index_flow_screen.py`
Forced flow as a **multiple of ADV**. Nine-cell grid across defensible index-cap and AUM
assumptions; **verdict from the WORST cell**, because that is what decides a trade.

    python3 index_flow_screen.py --symbol TAP --index SP500 --cik 0000024545 --price 40.50 --adv 1989456
    python3 index_flow_screen.py --symbol 7004 --index NIKKEI225 --price 1155 --adv 639400 \
        --divisor 29.83110217 --index-level 65020.94 --fx 0.00640118820592761

| verdict | threshold | meaning |
|---|---|---|
| **MATERIAL** | worst cell ≥ 3 days | the seller plausibly sets the price — the flow leg stands alone |
| IMMATERIAL | best cell < 1 day | no flow trade, whatever the narrative |
| INCONCLUSIVE | straddles | must NOT be reported as measured |

**Two rules learned from real bugs:** pass `--fx` for any non-USD name (omitting it on a yen
name INVERTED the verdict); price-weighted indices (Nikkei) need a sourced divisor and index
level from indexes.nikkei.co.jp — cap-weighting 7004 understated its weight 3×.

### 2b. Distribution events → `distribution_screen.py`
**Cash coverage** = contracted near-term distribution ÷ price. A hedged date ("within
approximately 30 days following approval") counts as **zero** — it is not a contracted floor.
Same $5.60 distribution: undated = 0% coverage, NO FLOOR; record date fixed by 8-K = 56%,
STRONG FLOOR. That distinction is the whole instrument.

### 2c. Position size
| rule | value |
|---|---|
| First entry | **$7,500** (5% of sleeve) |
| Tranche caps | $30k at 3 positions · $75k at 6 · $150k cap |
| Per position | ≤ $22,500 |
| Sleeve max loss | $150,000 — **9.8% of firm, capped by construction** |
| Weighting | equal — this desk measured it cannot rank its own ideas (0 of 32 books show skill) |

---

## 3. KILLING — two adversarial verifiers, distinct lenses

Every candidate faces two independent killers, each told to **default to refuted if uncertain**:
- **Premise / flow** — is the seller REALLY forced? has the flow ALREADY happened? re-run the screen with your own inputs
- **Date / tradability** — OPEN the URL and confirm it STATES the date (CM-069); real depth for $7,500; exitable in a stress?

### THE REFINEMENT LEARNED FROM TAP AND KANADEVIA
Both were **refuted** — but read the refutations. TAP: *"the forced seller is REAL and correctly
sourced — that single leg survives."* Kanadevia: *"I could not break the forced seller."* Both
died on a **narrative leg** the candidate had bolted on (a "misread fact" that turned out not to
be misread; an index framing whose own kill rule forbade its entry).

**Rule:** when the flow screen says MATERIAL and the killer concedes the seller is real, **the
flow leg alone justifies the position.** A refuted narrative leg is a reason to drop the
narrative, not the trade. Both TAP (10.77–22.28 days) and Kanadevia (18.89–44.09 days) clear
on flow alone.

---

## 4. TRADING — `convexity_autotrade.py`

Consumes **only** the workflow's own `survivor_names` (the journal cannot link killers to
candidates — an earlier version traded refuted names; caught before it ran).

Refuses, in order: no source URL · no dated catalyst · shorts (uncapped loss) · no live price.
Resolves global tickers via venue text (`7004 — Tokyo Stock Exchange` → `7004.T`); a dead
ticker still refuses. Requires a **live** price — never a stale mark.

**Funder-directed override:** when the funder opens a refuted candidate, it is recorded with
`funder_directed: true`, the full killer verdict, and a note that it *"must never be scored as
if it cleared."* The board headline states the count. Precedent: rally_rider.

---

## 5. EXITS — `convexity_sleeve.mark_and_manage`

Stop at **−50% of premium** · thesis death · catalyst passed unresolved · **effective date is
the time stop** — the day the forced selling ends. **Winners are never trimmed** — the desk's
+5% scaling rule is correct on the fundamentals desk and wrong here; a hard take-profit cut the
breakout study from +3.393% to +0.108%. Proceeds recycle to deployable capital.

**Under test** (`floor_exit_shadow.py`, paired, sealed): real stops on bar low/high, 14-day
minimum hold. 60 of 63 live stops realised worse than their level.

---

## 6. WHAT MUST NEVER HAPPEN

- A catalyst date with no URL that states it (CM-069 — a fabricated field once passed a gate)
- A thesis written before the flow is measured
- A Kraken order priced with a Binance fill rate
- A refuted candidate recorded as cleared
- A number quoted without re-reading the ledger (the +$4,335 was stale by $1,785)
- **Fabrication of any result.** Zero survivors is a complete answer.

---

## 7. THE CADENCE

- **Hourly:** `desk_tick.sh` marks the sleeve, runs exits, ticks the shadow
- **Every 4h:** the always-on agent reads `reports/always_on/LEDGER.md`, checks the desk is
  honest, and **runs the flow screen on any newly announced index change**
- **On demand / weekly:** the full dislocation hunt (34 families, ~100 agents)
- **At 40 paired trades:** the exit-shadow verdict

The board: http://100.127.19.45:18812/ (phone `/m`). Trade notices:
`reports/convexity_trade_notices.log`.

## GIVEBACK DNA (hard, 2026-10-29 17:20 WITA)
GIVEBACK DNA (Brett HARD 2026-10-29 17:17 WITA; spec final 17:20; PAPER): every bot and engine. Never sit through a reverse that gives back open profit. Mechanical early-bank trigger on an open winner BEFORE its normal half-bank: (a) peak >= +0.5R then >= 50% of that peak given back; OR (b) a bar closes through the prior bar's low (shorts: high) on >= 2x average volume; OR (c) a logged adverse news item. Action: bank half or more at the live mark, residual stop to BE then normal trail. Noise (no trigger) is not a turn. Never forces an entry; never sells what is not held. Every early bank logs reason + rail counterfactual (what the normal rail would have done). Normal half-bank = net A$ >= 1R of ORIGINAL entry risk after costs (NO +1% leg); Dex trend seat 1.5R.
