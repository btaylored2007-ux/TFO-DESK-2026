# TFO FUNDAMENTAL DESK — SPECULATIVE SLEEVE PRE-REGISTRATION

**Sealed 2026-08-28. FROZEN. Never edit this document to fit a result.**

Funder-directed 2026-08-28 (Brett): *"commit 10% to penny stock exploration and
loosen the edge rather then $, needs to be tech and news."*

PAPER ONLY — no broker is connected anywhere and none may be added.

---

## 1. WHAT IS BEING REGISTERED

A ring-fenced **$50,000 speculative sleeve** — 10% of the TFO Fundamental Desk's
$500,000 — for penny stocks and exploration companies where the edge is
**technology and news** rather than intrinsic value.

**This is NOT new capital.** It is drawn from the book's existing
**$77,894.65 cash**, which was never deployed because the sizing rule funds only
`undervalued` verdicts. The book's total stays $500,000; the sleeve changes what
some of the idle cash is permitted to do. Firm equity is unchanged.

**Long only. 20 positions maximum. $2,500 maximum per name.**

---

## 2. WHY THIS IS A SEPARATE SLEEVE AND NOT AN AMENDMENT TO THE MAIN BOOK

The funder asked on 2026-08-28 whether the main book's entry and exit rules
should be loosened too. **They are not, and the reasons are recorded here before
any sleeve result exists.**

1. **The main book is sealed and its results are now known.** Its §3 states that
   changing any constant "must be registered before it runs, never after a result
   is known." HRL scaled out at +6.17% net on 2026-08-27. Loosening the gate now
   would be fitting the rule to a result that already exists and would void the
   182-day forward test, because the thing measured would no longer be the thing
   registered.
2. **The main book's rules are not tight, they are correctly calibrated.** A
   30bps round trip is honest for liquid large caps; a 5% net gate against a 0.3%
   cost is a 16× margin. That gate is not what limits the book.
3. **This sleeve is loose because its ARITHMETIC differs, not because loose is
   better.** Measured spreads in this tier ran 4.20% (SUP.L), 4.17% (SOM.L) and
   53.08% (CLSK). At 350bps the round trip is 12× the main book's. The same
   discipline produces different numbers.

**Applying the main book's 5% gate here would refuse every exit forever** — a 4%
spread is an 8% handicap — and the sleeve would never trade at all.

---

## 3. THE FROZEN CONSTANTS — `fundamental_spec.py`

**Changing any number below is a RULE CHANGE and must be registered before it
runs, never after a result is known.**

### 3.1 Size
| | |
|---|---|
| `SLEEVE_USD` | 50,000 |
| `MAX_POSITION_USD` | 2,500 (5% of sleeve, 0.5% of book) |
| `MAX_POSITIONS` | 20 |
| `MIN_POSITIONS_FOR_FULL_DEPLOY` | 12 — below this the remainder stays CASH |

### 3.2 Cost — penny-tier and deliberately expensive
| | |
|---|---|
| `ENTRY_COST_BPS` / `EXIT_COST_BPS` | 25 each |
| `SLIPPAGE_BPS` | 150 **per side** |
| `ROUND_TRIP_BPS` | **350** |

A cost model that flatters the gate is the failure mode that matters. This one is
set to hurt.

### 3.3 THE EXIT RULE — set in stone
| | |
|---|---|
| `MIN_NET_GAIN` | **0.15 — 15% NET** |
| `LADDER` | (+15% net → release 33%), (+30% net → release 33%) |
| `RUNNER_FRACTION` | 0.34 |
| `RUNNER_GIVEBACK` | 0.15 |

**Clause 1 — the 15% net gate.** No exit unless the fill clears entry cost, exit
cost and slippage and leaves 15% on top. **15% is not greed: it is 3.5% of cost
plus a margin that makes the trade worth doing.** A penny position up 5% gross
LOSES money here. This is the funder's original 2026-08-27 rule — *"min 5% over
fill and exit cost, then scaled if it is rallying"* — with the same logic applied
to a cost base 12× larger.

**Clause 2 — scale while it rallies.** Clearing the gate does not close the
position. A rung fires **only on a NEW net-gain high**, never on a retrace
through a level already taken. The final 34% runs until net gain falls **15
percentage points off its own peak**, then comes off. The giveback is wider than
the main book's 5% because these names are volatile and a 5% trail would be
stopped out by noise on the way up.

**No short side.** Nobody lends micro caps, and a short you cannot borrow is not
a position. Borrow tiers do not apply.

### 3.4 Kill
| | |
|---|---|
| `SLEEVE_KILL_DD_PCT` | −40% — freezes NEW sleeve entries |
| `POSITION_KILL_DD_PCT` | −50% — a single name comes off |

**Consistent with the firm-wide invariant: neither kill force-liquidates the
book, and a frozen sleeve can always still EXIT.**

---

## 4. WHAT "LOOSENED EDGE" MEANS — the entry rule, also in stone

The main book requires intrinsic value. §2 of the governing method is explicit
that this needs earnings, book value or cash flow — **a pre-revenue explorer has
none, so there is nothing for the price to be "under."** Dropping the
requirement here is not a lowering of standards, it is the only honest way to
look at these at all.

**ACCEPTED INSTEAD:** a **DATED, SOURCED** technology or news catalyst — drill
result, assay, JORC/NI 43-101 resource upgrade, permit decision, offtake, named
partnership, patent grant, trial readout, contract award. Still dated. Still
sourced. Still carrying a named disconfirming condition.

**NOT ACCEPTED:** an undated story · "exposure to" a theme · a chart · a
promoter's projection · a resource with no JORC or NI 43-101 basis · a
partnership "in discussion" · a hot sector.

> **What is loosened is the KIND of evidence. Never whether evidence exists.**

### 4.1 The four hard gates — NOT loosened
1. **TRADABILITY** — ADV ≥ $150,000 · position ≤ **5%** of a day's volume
   (STRICTER than the main book's 10%) · spread ≤ 4%. *A position that cannot be
   exited is not a speculation, it is a donation.*
2. **CASH RUNWAY ≥ 12 MONTHS** — *an explorer with six months of cash is a
   countdown, not a bet.* It will raise, and it will raise at a discount.
3. **DILUTION ≤ 25%/YEAR** — juniors fund by placement. XPP.L diluted 37.9% in
   three years and that was a main-board company. *Dilution consumes discovery
   faster than drilling creates it.*
4. **A DATED CATALYST** — no date, no position.

**Missing data is a REFUSAL, not a pass.** On a micro cap an unmeasurable figure
is far more likely to be bad news than good, and "we could not check" must never
read as "fine."

---

## 5. THE HYPOTHESIS, AND THE EXPECTATION STATED FIRST

Over **182 days from first sleeve entry**:

> Positions selected on a dated, sourced technology/news catalyst, gated on
> tradability, runway and dilution, return more than the cash they displaced,
> net of a 350bps round trip.

**This sleeve is EXPECTED TO LOSE MONEY.** The firm's record on narrative-driven
speculation is ~1,040 killed hypotheses across the crypto desks, 6,533 OANDA
trades and 4,145 Betfair bets, with **no validated edge anywhere**. Penny
exploration is the least evidenced thing this firm has ever deployed into.

It is sized at 10% and capped per name **so that being wrong costs 10% and not
the book.** That is the entire design rationale, and it is recorded before a
single position exists so no later result can rewrite it.

**NO backtest · NO search-deflation · NO cross-validation · NONE of the three
kill-tests.** Registers as a funder-directed documented exception on the
`rally_rider` precedent, same as the main book.

---

## 6. SEALED ARTEFACT HASHES (SHA-256)

```
fundamental_spec.py       37470be357a52488609fe70a13ff6b677a10af83ecbe43a833d604e8ae671db6
fundamental_liquidity.py  58dfd4bdcc481bba4018303c0e8d3493134e6b2d0fabb04b3bee77d84a7c15b3
```

**Main book untouched by this document.** Its seal of 2026-08-27, its constants,
and its 2027-02-25 review date are unchanged.

---

*Sealed 2026-08-28. PAPER ONLY. Nothing here is investment advice.*

---

# AMENDMENT 1 — FUNDING SOURCE. Sealed 2026-08-28T02:40Z.

**Funder-directed 2026-08-28 after the original premise was shown to be false.**

## What changed and why

§1 above states the sleeve is "NOT new capital" and draws from the book's
"$77,894.65 cash". **That was true when written and false within hours.** At
2026-08-27T23:44Z the scheduled entry agent — doing exactly what it was built to
do — deployed the book's idle cash into 8 new positions (SOL.JO, HURC, EPAM,
VSNT, MRP, and shorts in ECOPETROL.CL, SQM-B.SN, BSANTANDER.SN). Book cash fell
to **$263.80**.

The sleeve therefore had no funding source. This is a sequencing error by the
operator: the sleeve was registered against cash that another automated process
was already authorised to spend.

## The amendment

**The sleeve is funded with $50,000 of NEW CAPITAL.** Firm equity rises by
$50,000. The main book is **NOT** touched: no position trimmed, no constant
changed, its 2026-08-27 seal and its 2027-02-25 review date stand.

The funder was given four options (new capital / fund from scale-outs as they
land / trim the main book / shrink the sleeve) and directed **new capital**.

## Why this amendment is legitimate, stated explicitly

Amending a sealed registration is normally forbidden, so the test is applied
rather than assumed:

1. **No sleeve result exists.** Zero positions had been opened when this was
   written. Nothing is being fitted to an outcome.
2. **No RULE changed.** Every constant in §3 and §4 — the 15% net gate, the
   350bps round trip, the ladder, the four hard gates, the kills — is untouched.
   Only the funding SOURCE changed.
3. **It corrects a falsified fact, not an inconvenient one.** The original text
   asserted cash existed. It does not. Leaving the document as written would make
   the registration a false record.
4. **Trimming the main book was explicitly REJECTED** as the alternative,
   because selling registered positions to fund an unregistered sleeve is the
   rule-change-after-results failure this whole framework exists to prevent.

**Neither the main book's rules nor the sleeve's rules were relaxed to make this
work. The capital changed; nothing else did.**

## Deployment standing at amendment

Five candidates cleared all four hard gates from 80 screened (`hunt5/`):
**SRB.L · CHN.AX · GL1.AX · TUD.V · POLB.L**.

Five is below `MIN_POSITIONS_FOR_FULL_DEPLOY` (12), so **per the sealed rule the
remainder stays CASH** — $12,500 deployed at most, ~$37,500 idle. The rule is not
waived to put money to work.

**Spreads must be measured IN SESSION before sizing.** Measured live
2026-08-28T02:35Z with ASX open (`marketState=REGULAR`):
- **CHN.AX 0.364%** on US$1,425,554 ADV — PASS
- **GL1.AX 0.733%** on US$233,861 ADV — PASS

**TUD.V (TSXV), SRB.L and POLB.L (AIM) were closed at this time and are NOT
opened.** They are queued for their own sessions. SRB.L (3.23%) and POLB.L
(3.55%) sat near the 4.0% ceiling on out-of-session reads and may yet fail.

*Amendment sealed 2026-08-28. PAPER ONLY.*

---

# AMENDMENT 2 — CATALYST-EXPIRY EXIT. Sealed 2026-08-28T09:00Z.

**Funder-directed 2026-08-28. Sealed BEFORE it runs.** Mirrors Amendment 3 to the
main book's registration; the reasoning there applies in full and is not repeated.

## What is added

**CATALYST EXPIRY: a position whose pre-registered dated catalyst has passed, plus
a 5-TRADING-DAY grace period, is CLOSED IN FULL unless RE-UNDERWRITTEN on a NEW
dated catalyst.**

**It is NOT blocked by the 15% net gate.** §3.3's gate governs the profit ladder;
it must not trap a position whose reason for existing has expired. A sleeve
position may therefore close at a loss under this mechanism, and that is correct.

## WHY IT MATTERS MORE HERE THAN IN THE MAIN BOOK

**This sleeve is 100% catalyst-driven by construction.** Its entry rule accepts a
dated technology or news catalyst **IN PLACE OF** intrinsic value — an explorer or
a clinical-stage company has no earnings for a price to be under. **So when the
catalyst resolves, there is NO residual value thesis underneath it.** A main-book
position at least still owns a business that was cheap; a sleeve position whose
catalyst has passed owns nothing but a story that has finished.

**And the exit-liquidity risk is severe at this cap tier.** Measured spreads here
ran 3.23%-3.64% at entry against a 350bps cost model. Per the funder's lead rule,
**the volume the anticipation created IS the exit liquidity** — when the news
lands and interest disperses, the exit that was possible last week is not possible
next week.

## Volume trend at entry — RECORDED, and the first reading is a warning

Measured 2026-08-28, 10-day vs 3-month average volume: **SIX OF NINE OPEN
POSITIONS ARE FADING** — BRN.AX 0.57, FDEV.L 0.61, SRB.L 0.63, CHN.AX 0.73,
POLB.L 0.81, 2105.HK 0.85; steady GL1.AX 0.87, 6996.HK 1.00, VICO.ST 1.08.

**Three carry catalysts inside six weeks and are fading into them** (CHN.AX and
POLB.L 2026-09-30; FDEV.L 2026-10-13 into a company-COMMITTED launch date).
Recorded before any outcome exists. It is genuinely ambiguous — it may mean the
market has not noticed, or that it does not care — and the forward record will
settle which.

## What does NOT change

Every constant in §3 — the 15% net gate, the 350bps round trip, the ladder, the
runner, the four hard gates, the −40%/−50% kills — is UNCHANGED. Neither kill
force-liquidates; a frozen sleeve can always still exit, and so can a resolved one.

*Sealed 2026-08-28. PAPER ONLY.*

## Amendment 2 — RE-SEALED ARTEFACT HASHES (SHA-256), 2026-08-28T09:20Z

```
fundamental_spec.py       37470be357a52488609fe70a13ff6b677a10af83ecbe43a833d604e8ae671db6
fundamental_spec_exits.py fb947d2531b82b0f077ce122c21a70fd59a3224a7d48ddc0dde19e762a139a5c
fundamental_liquidity.py  07d2748f2a63d335488a39a017830b900f180f4f4d261363f538fda1a958be44
```

**`fundamental_spec.py` is UNCHANGED** (37470be3…, identical to the original
2026-08-28 seal) — **every sleeve constant in §3 is untouched.** Only the exit
module and the shared gate changed.
